"""SSE EventHub unit tests."""

from __future__ import annotations

import json
import queue

from tesmart.events import EventHub, format_sse


def test_format_sse_named_event() -> None:
    raw = format_sse({"event": "banner", "banner": {"text": "Hi"}, "at": 1})
    text = raw.decode("utf-8")
    assert text.startswith("event: banner\n")
    assert "data: " in text
    data_line = [line for line in text.splitlines() if line.startswith("data: ")][0]
    payload = json.loads(data_line[len("data: ") :])
    assert payload["event"] == "banner"
    assert payload["banner"]["text"] == "Hi"
    assert text.endswith("\n\n")


def test_publish_reaches_subscriber() -> None:
    hub = EventHub()
    q = hub.subscribe()
    assert hub.subscriber_count() == 1
    hub.publish({"event": "input_changed", "active_input": 3})
    payload = q.get(timeout=1.0)
    assert payload["active_input"] == 3
    hub.unsubscribe(q)
    assert hub.subscriber_count() == 0


def test_publish_noop_without_subscribers() -> None:
    hub = EventHub()
    hub.publish({"event": "banner", "banner": None})  # must not raise


def test_close_all_wakes_subscribers() -> None:
    hub = EventHub()
    q = hub.subscribe()
    hub.close_all()
    assert q.get(timeout=1.0) is None
    assert hub.subscriber_count() == 0


def test_full_queue_drops_oldest() -> None:
    hub = EventHub()
    q = hub.subscribe()
    # Fill beyond max; publish must not block or raise.
    for i in range(80):
        hub.publish({"event": "input_changed", "active_input": i})
    last = None
    try:
        while True:
            last = q.get_nowait()
    except queue.Empty:
        pass
    assert last is not None
    assert last["active_input"] == 79
