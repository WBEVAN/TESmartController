"""HTTP callbacks for input-change notifications.

Clients register one or more absolute ``http://`` / ``https://`` URLs. When the
active input changes (front panel, remote, auto-detect, or a command through
this listener), each URL receives ``POST`` with a JSON body. Registering the
same URL twice is a no-op. URLs are stored in the env file and survive restarts.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from . import config

if TYPE_CHECKING:
    from .switch import TesmartSwitch

CALLBACKS_KEY = "TESMART_CALLBACKS"
NOTIFY_TIMEOUT = 5.0


def _normalize_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise config.ConfigError("callback url is required")
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise config.ConfigError("callback url must be an absolute http:// or https:// address")
    # Drop a trailing slash so http://host/app and http://host/app/ match.
    if text.endswith("/") and parts.path not in {"", "/"}:
        text = text.rstrip("/")
    elif text.endswith("/") and parts.path == "/":
        text = text.rstrip("/")
    return text


def read_callback_urls(env_file: str | None = None) -> list[str]:
    raw = config.read_env_file(config.env_file_path(env_file)).get(CALLBACKS_KEY, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Older / hand-edited pipe-separated form.
        parsed = [part.strip() for part in raw.split("|") if part.strip()]
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, str):
            continue
        try:
            url = _normalize_url(item)
        except config.ConfigError:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _write_urls(urls: list[str], env_file: str | None = None) -> list[str]:
    path = config.env_file_path(env_file)
    values = config.read_env_file(path)
    if urls:
        values[CALLBACKS_KEY] = json.dumps(urls)
    else:
        values.pop(CALLBACKS_KEY, None)
    config.write_env_file(path, values)
    return urls


def list_callbacks(env_file: str | None = None) -> dict[str, Any]:
    urls = read_callback_urls(env_file)
    return {"callbacks": urls, "count": len(urls)}


def add_callback(url: str, env_file: str | None = None) -> dict[str, Any]:
    """Register ``url``. Idempotent — duplicates are ignored."""
    normalized = _normalize_url(url)
    urls = read_callback_urls(env_file)
    added = normalized not in urls
    if added:
        urls.append(normalized)
        _write_urls(urls, env_file)
    return {"callbacks": urls, "count": len(urls), "url": normalized, "added": added}


def remove_callback(url: str, env_file: str | None = None) -> dict[str, Any]:
    """Remove ``url`` if present. Idempotent when missing."""
    normalized = _normalize_url(url)
    urls = read_callback_urls(env_file)
    removed = normalized in urls
    if removed:
        urls = [item for item in urls if item != normalized]
        _write_urls(urls, env_file)
    return {"callbacks": urls, "count": len(urls), "url": normalized, "removed": removed}


def clear_callbacks(env_file: str | None = None) -> dict[str, Any]:
    _write_urls([], env_file)
    return {"callbacks": [], "count": 0}


def build_event(
    *,
    previous: int | None,
    active: int,
    source: str,
    switch: TesmartSwitch,
    env_file: str | None = None,
) -> dict[str, Any]:
    names = config.read_names(env_file)
    payload: dict[str, Any] = {
        "event": "input_changed",
        "previous": previous,
        "active_input": active,
        "input_count": switch.input_count,
        "source": source,
        "endpoint": switch.endpoint,
        "names": {str(number): label for number, label in names.items()},
    }
    if previous is not None:
        payload["previous_name"] = names.get(previous)
    payload["active_name"] = names.get(active)
    return payload


def post_event(urls: list[str], payload: dict[str, Any]) -> None:
    if not urls:
        return
    body = (json.dumps(payload) + "\n").encode("utf-8")
    for url in urls:
        try:
            request = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "tesmartctl-callbacks/1",
                },
            )
            with urllib.request.urlopen(request, timeout=NOTIFY_TIMEOUT) as response:
                response.read(256)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"callback failed {url}: {exc}", file=sys.stderr)


class CallbackHub:
    """Watches the switch for input reports and notifies registered URLs."""

    def __init__(self, switch: TesmartSwitch, lock: threading.Lock, env_file: str | None = None):
        self.switch = switch
        self.lock = lock
        self.env_file = env_file
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_active: int | None = None
        self._state_lock = threading.Lock()

    def start(self) -> None:
        try:
            with self.lock:
                self._last_active = self.switch.get_active_input()
        except Exception as exc:  # noqa: BLE001 - startup must not fail the HTTP server
            print(f"callbacks: could not read initial input ({exc})", file=sys.stderr)
            self._last_active = None
        self._thread = threading.Thread(target=self._run, name="tesmartctl-callbacks", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def note_active(self, active: int | None, *, previous: int | None = None, source: str = "command") -> None:
        """Record a known active input (from an HTTP/panel command) and notify if it changed."""
        if active is None:
            return
        self._emit(active, previous=previous, source=source)

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            try:
                with self.lock:
                    frames = self.switch._pump(0.2)  # noqa: SLF001 - shared receive path
                    numbers = [frame.input_number for frame in frames if frame.is_input_report]
                    numbers.extend(self.switch.drain_unsolicited())
            except Exception as exc:  # noqa: BLE001 - keep watching
                print(f"callbacks watcher: {exc}", file=sys.stderr)
                self._stop.wait(1.0)
                continue
            for number in numbers:
                self._emit(number, previous=None, source="unsolicited")

    def _emit(self, active: int, *, previous: int | None, source: str) -> None:
        with self._state_lock:
            if active == self._last_active:
                return
            prev = previous if previous is not None else self._last_active
            self._last_active = active
        urls = read_callback_urls(self.env_file)
        if not urls:
            return
        payload = build_event(
            previous=prev,
            active=active,
            source=source,
            switch=self.switch,
            env_file=self.env_file,
        )
        # Do not hold the switch lock while waiting on remote HTTP clients.
        threading.Thread(
            target=post_event,
            args=(urls, payload),
            name="tesmartctl-callback-post",
            daemon=True,
        ).start()
