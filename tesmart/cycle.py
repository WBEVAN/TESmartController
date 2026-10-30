"""Listener-owned input cycle: step through saved inputs on a timer.

Not a switch command. One shared runner per listener process, like
:class:`~tesmart.peek.Peeker`. Browsers and ``tesmartctl cycle run --via``
start and stop it; SSE ``cycle`` frames carry the list and current input so
every client sees the same state.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import ContextManager

from . import config
from .events import EventHub
from .switch import SwitchError, TesmartSwitch, next_input

# True while a temporary peek should pause stepping.
Peeking = Callable[[], bool]
NotifyActive = Callable[[int, int | None], None]


class Cycler:
    """Step the saved cycle on a timer. One cycle at a time per switch."""

    def __init__(
        self,
        switch: TesmartSwitch,
        lock: ContextManager | None = None,
        *,
        env_file: str | None = None,
        events: EventHub | None = None,
        notify_active: NotifyActive | None = None,
        peeking: Peeking | None = None,
    ):
        self._switch = switch
        self._device = lock if lock is not None else nullcontext()
        self._env_file = env_file
        self._events = events
        self._notify_active = notify_active
        self._peeking = peeking
        self._guard = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict | None = None
        self._stop = False

    # --- public ------------------------------------------------------------

    def start(self) -> dict:
        """Begin or refresh the timed cycle. Returns :meth:`status`."""
        cycle, seconds = self._load_config()
        with self._device:
            current = self._switch.get_active_input()
        nxt = next_input(current, self._switch.input_count, cycle)
        with self._guard:
            if self._state is not None:
                self._state.update(
                    cycle=list(cycle),
                    seconds=seconds,
                    active_input=current,
                    next_input=nxt,
                    until=time.monotonic() + seconds,
                )
                self._wake.set()
                snap = self._snapshot()
                assert snap is not None
                self._announce_active(snap)
                return snap
            self._state = {
                "cycle": list(cycle),
                "seconds": seconds,
                "active_input": current,
                "next_input": nxt,
                "until": time.monotonic() + seconds,
            }
            self._stop = False
            self._wake.clear()
            self._thread = threading.Thread(target=self._run_loop, name="tesmart-cycle", daemon=True)
            self._thread.start()
            snap = self._snapshot()
            assert snap is not None
            self._announce_active(snap)
            return snap

    def stop(self) -> dict:
        """Stop the runner. Returns an ended snapshot (``active`` is null)."""
        with self._guard:
            running = self._state is not None
            seconds = int(self._state["seconds"]) if self._state else 2
            thread = self._thread
            if running:
                self._stop = True
                self._wake.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(2.0, seconds + 1.0))
        with self._guard:
            # Thread normally clears state and publishes the end frame. If join
            # timed out, force-clear so status does not lie that a cycle is live.
            if self._state is not None:
                self._state = None
                self._stop = False
                current = self._current_input()
                self._announce_ended(current)
            else:
                current = self._current_input()
        return {"active": None, "active_input": current, "stopped": running, "source": "command"}

    def status(self) -> dict | None:
        """``None`` when idle, otherwise cycle list, seconds, current, next, remaining."""
        with self._guard:
            return self._snapshot()

    def wait(self) -> None:
        """Block until the cycle started by :meth:`start` has stopped."""
        thread = self._thread
        if thread is None:
            return
        try:
            thread.join()
        except KeyboardInterrupt:
            self.stop()
            thread.join()

    # --- internals ---------------------------------------------------------

    def _load_config(self) -> tuple[tuple[int, ...], int]:
        info = config.rotate_info(self._env_file, input_count=self._switch.input_count)
        if info["mode"] != "automatic":
            raise SwitchError(
                "cycle mode is manual. Use `cycle mode automatic --every 10`, or start from the kit/panel."
            )
        seconds = info["seconds"] or config.DEFAULT_CYCLE_SECONDS
        if seconds < 1:
            raise SwitchError("cycle interval must be at least 1 second")
        try:
            cycle = config.enabled_cycle(
                self._switch.input_count,
                config.read_rotate(self._env_file),
                self._env_file,
            )
        except config.ConfigError as exc:
            raise SwitchError(str(exc)) from exc
        return cycle, int(seconds)

    def _current_input(self) -> int | None:
        try:
            with self._device:
                return self._switch.get_active_input()
        except Exception:  # noqa: BLE001 - status helpers must not raise
            with self._guard:
                if self._state is not None:
                    return int(self._state["active_input"])
            return None

    def _snapshot(self) -> dict | None:
        if self._state is None:
            return None
        remaining = max(0.0, self._state["until"] - time.monotonic())
        return {
            "cycle": list(self._state["cycle"]),
            "seconds": self._state["seconds"],
            "active_input": self._state["active_input"],
            "next_input": self._state["next_input"],
            "remaining": round(remaining, 1),
        }

    def _announce_active(self, snap: dict) -> None:
        if self._events is None:
            return
        self._events.publish(
            {
                "event": "cycle",
                "active": snap,
                "active_input": snap.get("active_input"),
                "source": "command",
            }
        )

    def _announce_ended(self, current: int | None) -> None:
        if self._events is None:
            return
        self._events.publish(
            {
                "event": "cycle",
                "active": None,
                "active_input": current,
                "stopped": True,
                "source": "command",
            }
        )

    def _run_loop(self) -> None:
        while True:
            with self._guard:
                assert self._state is not None
                remaining = self._state["until"] - time.monotonic()
                stop = self._stop
                seconds = self._state["seconds"]
            if stop:
                break
            if remaining > 0:
                if self._wake.wait(remaining):
                    self._wake.clear()
                continue
            if self._peeking is not None and self._peeking():
                with self._guard:
                    if self._state is not None and not self._stop:
                        self._state["until"] = time.monotonic() + seconds
                continue
            try:
                cycle, seconds = self._load_config()
            except SwitchError:
                break
            try:
                with self._device:
                    current = self._switch.get_active_input()
                    target = next_input(current, self._switch.input_count, cycle)
                    if target != current:
                        reported = self._switch.set_input(target, verify=True)
                        new_active = reported if reported is not None else target
                        previous = current
                    else:
                        new_active = current
                        previous = None
            except Exception:  # noqa: BLE001 - stop the runner; surface via status clearing
                break
            nxt = next_input(new_active, self._switch.input_count, cycle)
            with self._guard:
                if self._stop or self._state is None:
                    break
                self._state.update(
                    cycle=list(cycle),
                    seconds=seconds,
                    active_input=new_active,
                    next_input=nxt,
                    until=time.monotonic() + seconds,
                )
            if previous is not None and self._notify_active is not None:
                self._notify_active(new_active, previous)

        current = None
        with self._guard:
            if self._state is not None:
                current = self._state.get("active_input")
            self._state = None
            self._stop = False
        if current is None:
            current = self._current_input()
        self._announce_ended(current if isinstance(current, int) else None)
