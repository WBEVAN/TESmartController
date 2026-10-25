"""Server-Sent Events hub for browser kit / panel clients.

Outbound Hubitat-style callbacks POST to registered URLs. Browsers cannot
receive those; they open ``GET /events`` or ``GET /api/events`` and hold the
connection while this hub fans out the same logical payloads (``input_changed``,
``names_changed``, ``enabled_changed``, ``banner``, ``shutdown``).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

# Drop oldest when a slow client falls behind so publishers never block.
_QUEUE_MAX = 64
_KEEPALIVE_SEC = 20.0


class EventHub:
    """Thread-safe fan-out of JSON event dicts to SSE subscribers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: set[queue.Queue] = set()

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, payload: dict[str, Any]) -> None:
        """Enqueue ``payload`` for every subscriber (non-blocking)."""
        if not payload:
            return
        with self._lock:
            targets = list(self._subs)
        if not targets:
            return
        for q in targets:
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass

    def close_all(self) -> None:
        """Wake every stream so handlers can exit (listener shutdown)."""
        with self._lock:
            targets = list(self._subs)
            self._subs.clear()
        for q in targets:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass


def format_sse(payload: dict[str, Any]) -> bytes:
    """Encode one SSE message (``event`` + ``data`` lines)."""
    name = str(payload.get("event") or "message").strip() or "message"
    # Keep event names token-safe for EventSource.
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    data = json.dumps(payload, separators=(",", ":"))
    return f"event: {safe}\ndata: {data}\n\n".encode("utf-8")


def stream_sse(handler: Any, hub: EventHub) -> None:
    """Hold the HTTP response open and write SSE frames until the client leaves."""
    q = hub.subscribe()
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-store")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        hello = {
            "event": "hello",
            "ok": True,
            "subscribers": hub.subscriber_count(),
            "at": time.time(),
            "mdns_name": getattr(getattr(handler, "server", None), "mdns_name", None),
        }
        handler.wfile.write(format_sse(hello))
        handler.wfile.flush()

        while True:
            try:
                payload = q.get(timeout=_KEEPALIVE_SEC)
            except queue.Empty:
                try:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
                continue
            if payload is None:
                break
            try:
                handler.wfile.write(format_sse(payload))
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                break
    finally:
        hub.unsubscribe(q)
