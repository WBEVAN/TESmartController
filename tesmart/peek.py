"""Peek: show another input for a few seconds, then return to the one that was active.

Not a cycle. One flip there, one flip back. The switch has no such command;
this module times it on the host.

:class:`Peeker` works two ways:

* ``run()`` blocks until the switch is back. The CLI and the line listener use it.
* ``start()`` returns at once and a thread does the waiting and the return.
  The HTTP panel uses it so the page can show a countdown and offer "back now".

A second ``start()`` during a peek retargets: the switch moves to the new
input, the timer restarts, and the return point stays the original input.
If something else changes the input while a peek is in progress (front
panel, remote, another client), the peek is *interrupted* and does not snap
back over that choice.
"""

from __future__ import annotations

import threading
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from typing import ContextManager

from .switch import SwitchError, TesmartSwitch


@dataclass
class PeekResult:
    previous: int
    peeked: int
    seconds: float
    restored: int | None
    interrupted: bool
    cancelled: bool
    active_input: int | None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Peeker:
    """Peek at an input and come back. One peek at a time per switch."""

    def __init__(self, switch: TesmartSwitch, lock: ContextManager | None = None):
        self._switch = switch
        self._device = lock if lock is not None else nullcontext()
        self._guard = threading.Lock()  # protects _state, _cancel, _thread
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict | None = None
        self._cancel = False
        self.last: PeekResult | None = None

    # --- public ------------------------------------------------------------

    def run(self, number: int, seconds: float) -> PeekResult:
        """Peek and block until the switch is back. Ctrl-C returns early."""
        self.start(number, seconds)
        return self.wait()

    def wait(self) -> PeekResult:
        """Block until the peek started by :meth:`start` has returned."""
        thread = self._thread
        if thread is None:
            raise SwitchError("no peek in progress")
        try:
            thread.join()
        except KeyboardInterrupt:
            self.cancel()
            thread.join()
        assert self.last is not None
        if self.last.error:
            raise SwitchError(f"peeked at {self.last.peeked} but could not return: {self.last.error}")
        return self.last

    def start(self, number: int, seconds: float) -> dict:
        """Switch now, return later on a thread. Returns :meth:`status`."""
        if seconds < 1:
            raise SwitchError("peek duration must be at least 1 second")
        with self._guard:
            if self._state is not None:
                with self._device:
                    reported = self._switch.set_input(number, verify=True)
                self._state.update(peeked=reported or number, seconds=seconds, until=time.monotonic() + seconds)
                self._wake.set()
                return self._snapshot()
            with self._device:
                previous = self._switch.get_active_input()
                reported = self._switch.set_input(number, verify=True)
            self._state = {
                "previous": previous,
                "peeked": reported or number,
                "seconds": seconds,
                "until": time.monotonic() + seconds,
            }
            self._cancel = False
            self._wake.clear()
            self._thread = threading.Thread(target=self._wait_then_return, name="tesmart-peek", daemon=True)
            self._thread.start()
            return self._snapshot()

    def cancel(self) -> bool:
        """Return to the previous input now. False if no peek is running."""
        with self._guard:
            if self._state is None:
                return False
            self._cancel = True
            self._wake.set()
            return True

    def status(self) -> dict | None:
        """``None`` when idle, otherwise previous, peeked, seconds, remaining."""
        with self._guard:
            return self._snapshot()

    # --- internals ---------------------------------------------------------

    def _snapshot(self) -> dict | None:
        if self._state is None:
            return None
        remaining = max(0.0, self._state["until"] - time.monotonic())
        return {
            "previous": self._state["previous"],
            "peeked": self._state["peeked"],
            "seconds": self._state["seconds"],
            "remaining": round(remaining, 1),
        }

    def _wait_then_return(self) -> None:
        while True:
            with self._guard:
                assert self._state is not None
                remaining = self._state["until"] - time.monotonic()
                cancelled = self._cancel
            if cancelled or remaining <= 0:
                break
            if self._wake.wait(remaining):
                self._wake.clear()

        with self._guard:
            assert self._state is not None
            previous = self._state["previous"]
            peeked = self._state["peeked"]
            seconds = self._state["seconds"]
            cancelled = self._cancel
            self._state = None  # a new start() from here on begins a fresh peek
            self._cancel = False

        restored: int | None = None
        current: int | None = None
        interrupted = False
        error: str | None = None
        try:
            with self._device:
                current = self._switch.get_active_input()
                if current == peeked:
                    restored = self._switch.set_input(previous, verify=True)
                    current = restored
                else:
                    interrupted = True
        except Exception as exc:  # noqa: BLE001 - reported through PeekResult.error
            error = str(exc)
        self.last = PeekResult(previous, peeked, seconds, restored, interrupted, cancelled, current, error)
