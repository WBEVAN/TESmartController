"""peek-follow fans out to Hubitat-style callbacks on the mesh proxy."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from tesmart.callbacks import CallbackHub, build_peek_follow_event
from tesmart.events import EventHub
from tesmart.panel import _publish_peek_follow
from tesmart.peek import PeekFollow, Peeker, apply_follow


class _Switch:
    endpoint = "tcp://test:5000"
    input_count = 8

    def __init__(self, active: int = 1) -> None:
        self.active = active

    def get_active_input(self) -> int:
        return self.active

    def set_input(self, number: int, verify: bool = True) -> int:
        self.active = number
        return number


def test_build_peek_follow_event_matches_hubitat_apply_peek_follow() -> None:
    payload = build_peek_follow_event("always", switch=_Switch(), seconds=5, source="command")
    assert payload["event"] == "peek_follow"
    assert payload["peek_follow"] == "always"
    assert payload["mode"] == "always"
    assert payload["peek"]["follow"] == "always"
    assert payload["peek"]["seconds"] == 5
    assert payload["seconds"] == 5


def test_publish_peek_follow_posts_when_outbound_enabled(monkeypatch, tmp_path) -> None:
    env = tmp_path / "env"
    env.write_text(
        'TESMART_CALLBACKS=["http://127.0.0.1:9/hook"]\n',
        encoding="utf-8",
    )
    events = EventHub()
    subscribed = events.subscribe()
    posted: list[dict] = []
    lock = threading.Lock()
    hub = CallbackHub(_Switch(), lock, str(env), events=events, outbound_http=True)

    def _capture(urls, payload, *, timeout=None):  # noqa: ANN001
        posted.append({"urls": list(urls), "payload": dict(payload), "timeout": timeout})
        return []

    monkeypatch.setattr("tesmart.callbacks.post_event", _capture)

    _publish_peek_follow(SimpleNamespace(callbacks=hub, events=events), {"mode": "always", "seconds": 7})
    # Give the daemon post thread a moment.
    for _ in range(50):
        if posted:
            break
        threading.Event().wait(0.02)
    assert posted, "expected Hubitat callback POST"
    body = posted[0]["payload"]
    assert body["event"] == "peek_follow"
    assert body["peek_follow"] == "always"
    assert body["peek"]["follow"] == "always"
    assert body["seconds"] == 7
    assert subscribed.get(timeout=1.0)["peek_follow"] == "always"


def test_publish_peek_follow_skips_http_on_mesh_client(monkeypatch, tmp_path) -> None:
    env = tmp_path / "env"
    env.write_text(
        'TESMART_CALLBACKS=["http://127.0.0.1:9/hook"]\n',
        encoding="utf-8",
    )
    events = EventHub()
    subscribed = events.subscribe()
    posted: list[dict] = []
    hub = CallbackHub(
        _Switch(),
        threading.Lock(),
        str(env),
        events=events,
        outbound_http=False,
    )
    monkeypatch.setattr(
        "tesmart.callbacks.post_event",
        lambda *a, **k: posted.append({"a": a, "k": k}) or [],
    )

    _publish_peek_follow(SimpleNamespace(callbacks=hub, events=events), {"mode": "off"})
    assert subscribed.get(timeout=1.0)["mode"] == "off"
    threading.Event().wait(0.05)
    assert posted == []


def test_claimed_follow_peek_uses_notify_peek_follow() -> None:
    seen: list[tuple] = []
    events = EventHub()
    # Quiet SSE path — notify should own delivery.
    follow = PeekFollow(
        events=events,
        notify_peek_follow=lambda mode, seconds=None, source="command", **extra: seen.append(
            (mode, seconds, source, extra)
        ),
    )
    follow.set_mode("once", 3)
    peeker = Peeker(_Switch(2), events=events)
    apply_follow(follow, peeker, 5, 5, block=False)
    peeker.cancel()
    peeker.wait()
    assert seen
    mode, seconds, source, extra = seen[0]
    assert mode == "off"
    assert seconds == 3
    assert source == "command"
    assert extra["followed"] == "once"
    assert extra["previous"] == 2
    assert extra["active_input"] == 5
