"""peek_follow SSE when a follow peek starts."""

from __future__ import annotations

import queue
import threading
import time

from tesmart.events import EventHub
from tesmart.peek import PeekFollow, Peeker, apply_follow


class _Switch:
    def __init__(self, active: int = 1) -> None:
        self.active = active
        self.returned_at: float | None = None

    def get_active_input(self) -> int:
        return self.active

    def set_input(self, number: int, verify: bool = True) -> int:
        if self.active != number and number == 1 and self.active > 0:
            self.returned_at = time.monotonic()
        self.active = number
        return number


def _finish(peeker: Peeker) -> None:
    peeker.cancel()
    peeker.wait()


def _assert_quiet(subscribed: queue.Queue) -> None:
    try:
        leaked = subscribed.get_nowait()
    except queue.Empty:
        return
    raise AssertionError(leaked)


def test_once_event_is_published_before_the_switch_returns() -> None:
    hub = EventHub()
    subscribed = hub.subscribe()
    switch = _Switch(1)
    follow = PeekFollow(events=hub)
    follow.set_mode("once", 1)
    peeker = Peeker(switch)

    errors: list[BaseException] = []

    def run() -> None:
        try:
            apply_follow(follow, peeker, 4, 5, block=True)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    payload = subscribed.get(timeout=2)
    seen = time.monotonic()
    worker.join(timeout=3)
    assert errors == []

    assert payload == {
        "event": "peek_follow",
        "mode": "off",
        "peek_follow": "off",
        "followed": "once",
        "previous": 1,
        "active_input": 4,
        "seconds": 1,
    }
    assert switch.returned_at is not None
    assert seen < switch.returned_at
    assert switch.active == 1


def test_always_event_uses_real_inputs_and_stays_armed() -> None:
    hub = EventHub()
    subscribed = hub.subscribe()
    switch = _Switch(1)
    follow = PeekFollow(events=hub)
    follow.set_mode("always", 8)
    peeker = Peeker(switch)

    result = apply_follow(follow, peeker, 4, 5, block=False)
    payload = subscribed.get(timeout=1)
    _finish(peeker)

    assert payload["mode"] == "always"
    assert payload["peek_follow"] == "always"
    assert payload["followed"] == "always"
    assert payload["previous"] == 1
    assert payload["active_input"] == 4
    assert payload["seconds"] == 8
    assert result is not None
    assert result["peek_follow"] == "always"
    assert result["active_input"] == 4


def test_missing_hub_and_off_mode_publish_nothing() -> None:
    switch = _Switch(1)
    follow = PeekFollow()
    follow.set_mode("once", 1)
    peeker = Peeker(switch)
    result = apply_follow(follow, peeker, 4, 5, block=False)
    _finish(peeker)
    assert result is not None
    assert result["followed"] == "once"
    assert result["peek_follow"] == "off"

    hub = EventHub()
    subscribed = hub.subscribe()
    idle = PeekFollow(events=hub)
    assert apply_follow(idle, Peeker(_Switch(1)), 4, 5, block=False) is None
    _assert_quiet(subscribed)


def test_placeholder_input_is_not_published() -> None:
    hub = EventHub()
    subscribed = hub.subscribe()
    switch = _Switch(-1)
    follow = PeekFollow(events=hub)
    follow.set_mode("once", 1)
    peeker = Peeker(switch)
    apply_follow(follow, peeker, 4, 5, block=False)
    _finish(peeker)
    _assert_quiet(subscribed)
