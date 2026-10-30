"""SSE ``cycle`` frames when the listener-owned automatic cycle starts and stops."""

from __future__ import annotations

import queue
import time

from tesmart.cycle import Cycler
from tesmart.events import EventHub


class _Switch:
    def __init__(self, active: int = 1, input_count: int = 8) -> None:
        self.active = active
        self.input_count = input_count

    def get_active_input(self) -> int:
        return self.active

    def set_input(self, number: int, verify: bool = True) -> int:
        self.active = number
        return number


def _next_cycle(subscribed: queue.Queue, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        wait = max(0.05, deadline - time.monotonic())
        payload = subscribed.get(timeout=wait)
        if payload.get("event") == "cycle":
            return payload
    raise AssertionError("no cycle event")


def test_start_publishes_list_and_current(tmp_path, monkeypatch) -> None:
    env = tmp_path / "env"
    env.write_text(
        "TESMART_CYCLE_MODE=automatic\nTESMART_CYCLE_SECONDS=2\nTESMART_ROTATE=1,3,5\n",
        encoding="utf-8",
    )
    hub = EventHub()
    subscribed = hub.subscribe()
    noted: list[tuple[int, int | None]] = []
    switch = _Switch(3)
    cycler = Cycler(
        switch,
        env_file=str(env),
        events=hub,
        notify_active=lambda active, previous=None: noted.append((active, previous)),
    )

    snap = cycler.start()
    started = _next_cycle(subscribed)

    assert snap["cycle"] == [1, 3, 5]
    assert snap["active_input"] == 3
    assert snap["next_input"] == 5
    assert snap["seconds"] == 2
    assert started["active"]["cycle"] == [1, 3, 5]
    assert started["active_input"] == 3
    assert started["source"] == "command"
    assert cycler.status() is not None

    ended = cycler.stop()
    stopped = _next_cycle(subscribed)
    assert ended["stopped"] is True
    assert ended["active"] is None
    assert stopped["active"] is None
    assert stopped["stopped"] is True
    assert cycler.status() is None


def test_second_start_refreshes_without_extra_thread(tmp_path) -> None:
    env = tmp_path / "env"
    env.write_text(
        "TESMART_CYCLE_MODE=automatic\nTESMART_CYCLE_SECONDS=5\nTESMART_ROTATE=2,4\n",
        encoding="utf-8",
    )
    hub = EventHub()
    subscribed = hub.subscribe()
    cycler = Cycler(_Switch(2), env_file=str(env), events=hub)

    first = cycler.start()
    _next_cycle(subscribed)
    thread = cycler._thread
    second = cycler.start()
    refreshed = _next_cycle(subscribed)
    assert first["seconds"] == 5
    assert second["cycle"] == [2, 4]
    assert refreshed["active"]["cycle"] == [2, 4]
    assert cycler._thread is thread

    cycler.stop()
    _next_cycle(subscribed)


def test_peek_pauses_stepping(tmp_path) -> None:
    env = tmp_path / "env"
    env.write_text(
        "TESMART_CYCLE_MODE=automatic\nTESMART_CYCLE_SECONDS=1\nTESMART_ROTATE=1,2\n",
        encoding="utf-8",
    )
    switch = _Switch(1)
    peeking = {"on": True}
    cycler = Cycler(
        switch,
        env_file=str(env),
        peeking=lambda: peeking["on"],
    )
    cycler.start()
    time.sleep(1.3)
    assert switch.active == 1
    peeking["on"] = False
    time.sleep(1.3)
    assert switch.active == 2
    cycler.stop()


def test_manual_mode_rejects_start(tmp_path) -> None:
    env = tmp_path / "env"
    env.write_text("TESMART_CYCLE_MODE=manual\n", encoding="utf-8")
    cycler = Cycler(_Switch(1), env_file=str(env))
    try:
        cycler.start()
        raise AssertionError("expected SwitchError")
    except Exception as exc:  # noqa: BLE001
        assert "manual" in str(exc).lower()
