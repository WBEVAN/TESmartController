"""SSE ``peek`` frames when a temporary input starts and ends."""

from __future__ import annotations

import queue
import time

from tesmart.events import EventHub
from tesmart.peek import Peeker


class _Switch:
    def __init__(self, active: int = 1) -> None:
        self.active = active

    def get_active_input(self) -> int:
        return self.active

    def set_input(self, number: int, verify: bool = True) -> int:
        self.active = number
        return number


def _next_peek(subscribed: queue.Queue, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        wait = max(0.05, deadline - time.monotonic())
        payload = subscribed.get(timeout=wait)
        if payload.get("event") == "peek":
            return payload
    raise AssertionError("no peek event")


def test_start_publishes_active_peek_and_notifies() -> None:
    hub = EventHub()
    subscribed = hub.subscribe()
    noted: list[tuple[int, int | None]] = []
    peeker = Peeker(
        _Switch(1),
        events=hub,
        notify_active=lambda active, previous=None: noted.append((active, previous)),
    )

    snap = peeker.start(4, 5)
    started = _next_peek(subscribed)

    assert snap["previous"] == 1
    assert snap["peeked"] == 4
    assert started["active"]["peeked"] == 4
    assert started["active_input"] == 4
    assert started["previous"] == 1
    assert started["source"] == "command"
    assert noted == [(4, 1)]

    peeker.cancel()
    peeker.wait()
    ended = _next_peek(subscribed)
    assert ended["active"] is None
    assert ended["active_input"] == 1
    assert ended["cancelled"] is True
    assert ended["previous"] == 1
    assert ended["peeked"] == 4
    assert ended["restored"] == 1
    assert ended["source"] == "command"
    # Restored transition is peeked → origin (4 → 1), not origin → origin.
    assert noted[-1] == (1, 4)


def test_retarget_publishes_again() -> None:
    hub = EventHub()
    subscribed = hub.subscribe()
    peeker = Peeker(_Switch(2), events=hub)

    peeker.start(3, 8)
    first = _next_peek(subscribed)
    peeker.start(5, 3)
    second = _next_peek(subscribed)
    peeker.cancel()
    peeker.wait()
    _next_peek(subscribed)

    assert first["active"]["peeked"] == 3
    assert first["previous"] == 2
    assert second["active"]["peeked"] == 5
    assert second["active"]["previous"] == 2
    assert second["active"]["seconds"] == 3


def test_no_hub_still_peeks() -> None:
    peeker = Peeker(_Switch(1))
    snap = peeker.start(2, 2)
    assert snap["peeked"] == 2
    peeker.cancel()
    result = peeker.wait()
    assert result.cancelled is True
    assert result.restored == 1
