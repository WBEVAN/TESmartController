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
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from typing import ContextManager

from .events import EventHub
from .switch import SwitchError, TesmartSwitch

# Called when a peek moves the switch (start, retarget, or end).
NotifyActive = Callable[[int, int | None], None]


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


class PeekFollow:
    """Decide whether the next plain input change should peek instead of stick.

    ``once`` applies to a single later input change (``/input/3``, ``set 3``,
    next, previous) and then clears. ``always`` keeps applying until set back
    to ``off``. An explicit peek does not consume either mode. The state is
    in this process only; it is not written to the env file.
    """

    def __init__(self, events: EventHub | None = None) -> None:
        self._lock = threading.Lock()
        self._mode = "off"
        self._seconds: int | None = None
        # Set on the HTTP listener so a claimed peek can tell other SSE clients
        # which input is temporary. The line listener leaves this unset.
        self.events = events

    def status(self) -> dict:
        with self._lock:
            return {"mode": self._mode, "seconds": self._seconds}

    def set_mode(self, mode: str, seconds: int | None = None) -> dict:
        if mode not in {"once", "always", "off"}:
            raise ValueError("peek follow must be once, always, or off")
        if mode == "off":
            seconds = None
        elif seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1):
            raise ValueError("peek seconds must be a whole number, 1 or more")
        with self._lock:
            self._mode = mode
            self._seconds = seconds
            return {"mode": self._mode, "seconds": self._seconds}

    def claim(self) -> dict | None:
        """Return the mode to apply to this input change, or ``None`` when off.

        ``once`` clears as it is claimed, so a second change sticks.
        ``always`` stays armed.
        """
        with self._lock:
            if self._mode == "off":
                return None
            claimed = {"mode": self._mode, "seconds": self._seconds}
            if self._mode == "once":
                self._mode = "off"
                self._seconds = None
            return claimed


def apply_follow(
    follow: PeekFollow,
    peeker: Peeker,
    number: int,
    default_seconds: int,
    *,
    block: bool,
) -> dict | None:
    """Peek at ``number`` when follow mode is armed. ``None`` means stick instead.

    ``block`` waits until the switch is back (the plain HTTP and line
    listeners). The panel passes ``False`` so it can show the countdown.

    After ``start`` the return input is known. That is when other clients hear
    ``peek_follow``, before a blocking ``wait`` returns the switch.
    """
    claim = follow.claim()
    if claim is None:
        return None
    seconds = claim["seconds"] if claim["seconds"] is not None else default_seconds
    started = peeker.start(number, seconds)
    if started is not None:
        _publish_claimed_peek(follow, str(claim["mode"]), started, seconds)
    if block:
        result = peeker.wait().to_dict()
    else:
        peeked = started["peeked"] if started is not None else number
        result = {"requested": number, "active_input": peeked, "peek": started}
    result["requested"] = number
    result["followed"] = claim["mode"]
    result["peek_follow"] = follow.status()["mode"]
    return result


def _real_input(value: object) -> int | None:
    """A 1-based port. ``-1`` and other placeholders are not inputs."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _publish_claimed_peek(follow: PeekFollow, followed: str, started: dict, seconds: float) -> None:
    """SSE ``peek_follow`` for a peek that just moved the switch.

    ``mode`` is the mode after the claim (``off`` for Once, ``always`` when
    Always stays armed). ``followed`` is the mode this peek consumed.
    ``previous`` is restored when the timer ends. ``active_input`` is the
    input showing now. Presses of Off use a shorter event and do not come
    through here.
    """
    hub = follow.events
    if hub is None:
        return
    previous = _real_input(started.get("previous"))
    active = _real_input(started.get("peeked"))
    if previous is None or active is None:
        return
    mode = str(follow.status()["mode"])
    hub.publish(
        {
            "event": "peek_follow",
            "mode": mode,
            "peek_follow": mode,
            "followed": followed,
            "previous": previous,
            "active_input": active,
            "seconds": seconds,
            "source": "command",
        }
    )


class Peeker:
    """Peek at an input and come back. One peek at a time per switch."""

    def __init__(
        self,
        switch: TesmartSwitch,
        lock: ContextManager | None = None,
        *,
        events: EventHub | None = None,
        notify_active: NotifyActive | None = None,
    ):
        self._switch = switch
        self._device = lock if lock is not None else nullcontext()
        self._events = events
        self._notify_active = notify_active
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
                snap = self._snapshot()
                assert snap is not None
                self._announce_active(snap)
                return snap
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
            snap = self._snapshot()
            assert snap is not None
            self._announce_active(snap)
            return snap

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

    def _announce_active(self, snap: dict) -> None:
        """Tell SSE clients and Hubitat-style callbacks the temporary input is live."""
        previous = _real_input(snap.get("previous"))
        peeked = _real_input(snap.get("peeked"))
        if peeked is None:
            return
        if self._events is not None:
            self._events.publish(
                {
                    "event": "peek",
                    "active": snap,
                    "active_input": peeked,
                    "previous": previous,
                    "seconds": snap.get("seconds"),
                    "source": "command",
                }
            )
        if self._notify_active is not None:
            self._notify_active(peeked, previous)

    def _announce_ended(self, result: PeekResult) -> None:
        """Clear the temporary peek for browsers and report the restored input."""
        current = _real_input(result.active_input)
        origin = _real_input(result.previous)
        peeked = _real_input(result.peeked)
        if self._events is not None:
            self._events.publish(
                {
                    "event": "peek",
                    "active": None,
                    "active_input": current,
                    "previous": origin,
                    "peeked": peeked,
                    "restored": _real_input(result.restored),
                    "interrupted": result.interrupted,
                    "cancelled": result.cancelled,
                    "source": "command",
                }
            )
        # Note the real on-screen transition (peeked → restored). Passing the
        # peek origin as previous produced input_changed origin→origin and left
        # UIs briefly showing the peeked input as selected (green) before the
        # restore. Interrupted peeks skip this: the foreign change already noted.
        if (
            current is not None
            and self._notify_active is not None
            and result.restored is not None
            and peeked is not None
        ):
            self._notify_active(current, peeked)

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
            # Leave _state set until the restore finishes so /status and polls
            # still report peeking — otherwise UIs can flash the peeked input
            # as selected (green) while the switch is still on that port.

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
        with self._guard:
            self._state = None  # a new start() from here on begins a fresh peek
            self._cancel = False
        self.last = PeekResult(previous, peeked, seconds, restored, interrupted, cancelled, current, error)
        self._announce_ended(self.last)
