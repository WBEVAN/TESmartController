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


def test_notify_shutdown_publishes_then_posts(monkeypatch) -> None:
    from tesmart.callbacks import notify_shutdown

    events = EventHub()
    queue_ = events.subscribe()
    posted: dict = {}

    class Hub:
        env_file = None

    hub = Hub()
    hub.events = events  # type: ignore[attr-defined]

    monkeypatch.setattr("tesmart.callbacks.read_callback_urls", lambda _env: ["http://hub/tesmart-event"])
    monkeypatch.setattr("tesmart.callbacks.read_timeout", lambda _env: 10.0)

    def fake_post(urls, payload, *, timeout):
        posted["urls"] = urls
        posted["payload"] = payload
        posted["timeout"] = timeout
        return []

    monkeypatch.setattr("tesmart.callbacks.post_event", fake_post)
    notify_shutdown(hub, reason="signal")  # type: ignore[arg-type]
    payload = queue_.get(timeout=1.0)
    assert payload["event"] == "shutdown"
    assert payload["reason"] == "signal"
    assert payload["source"] == "listener"
    assert posted["urls"] == ["http://hub/tesmart-event"]
    assert posted["payload"]["event"] == "shutdown"
    assert posted["timeout"] == 2.0


def test_note_online_reports_the_current_input(monkeypatch) -> None:
    import threading

    from tesmart.callbacks import CallbackHub

    events = EventHub()

    class Switch:
        input_count = 8
        endpoint = "sim://8"

    hub = CallbackHub(Switch(), threading.Lock(), None, events=events)  # type: ignore[arg-type]
    hub._last_active = 2
    queue_ = events.subscribe()
    monkeypatch.setattr("tesmart.callbacks.config.read_names", lambda _env: {2: "Office"})
    monkeypatch.setattr("tesmart.callbacks.config.read_disabled", lambda _env: frozenset({4}))
    monkeypatch.setattr("tesmart.callbacks.read_callback_urls", lambda _env: [])
    hub.note_online()
    payload = queue_.get(timeout=1.0)
    assert payload["event"] == "online"
    assert payload["source"] == "startup"
    assert payload["active_input"] == 2
    assert payload["active_name"] == "Office"
    assert payload["disabled"] == [4]
    assert payload["input_count"] == 8
