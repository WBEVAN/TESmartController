"""HTTP callbacks for input- and name-change notifications.

Clients register one or more absolute ``http://`` / ``https://`` URLs, optionally
with a display ``name``. When the active input changes, or when local input names
are updated through the HTTP API, each URL receives ``POST`` with a JSON body.
Registering the same URL twice updates its name (if given) and is otherwise a
no-op. Entries and the POST timeout are stored in the env file and survive
restarts.

``GET`` / ``POST /callbacks/health`` probes every registered URL with a
``health_check`` event so operators can see which clients are still reachable.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from . import config

if TYPE_CHECKING:
    from .events import EventHub
    from .switch import TesmartSwitch

CALLBACKS_KEY = "TESMART_CALLBACKS"
TIMEOUT_KEY = "TESMART_CALLBACK_TIMEOUT"
DEFAULT_TIMEOUT = 10.0
MIN_TIMEOUT = 0.5
MAX_TIMEOUT = 120.0

# In-process delivery / health results keyed by normalized URL (not persisted).
_delivery_lock = threading.Lock()
_delivery: dict[str, dict[str, Any]] = {}


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


def _normalize_name(name: str | None) -> str | None:
    if name is None:
        return None
    text = str(name).strip()
    return text or None


def _entry(url: str, name: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"url": url}
    if name:
        item["name"] = name
    return item


def _parse_entry(item: Any) -> dict[str, Any] | None:
    """Accept a plain URL string or ``{\"url\": \"...\", \"name\": \"...\"}``."""
    if isinstance(item, str):
        try:
            return _entry(_normalize_url(item))
        except config.ConfigError:
            return None
    if isinstance(item, dict):
        raw_url = item.get("url") or item.get("callback")
        if not raw_url:
            return None
        try:
            url = _normalize_url(str(raw_url))
        except config.ConfigError:
            return None
        return _entry(url, _normalize_name(item.get("name") or item.get("label")))
    return None


def read_callback_entries(env_file: str | None = None) -> list[dict[str, Any]]:
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
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in parsed:
        entry = _parse_entry(item)
        if entry is None or entry["url"] in seen:
            continue
        seen.add(entry["url"])
        out.append(entry)
    return out


def read_callback_urls(env_file: str | None = None) -> list[str]:
    """URL-only list (compatibility helper for callers that only need addresses)."""
    return [entry["url"] for entry in read_callback_entries(env_file)]


def _write_entries(entries: list[dict[str, Any]], env_file: str | None = None) -> list[dict[str, Any]]:
    path = config.env_file_path(env_file)
    values = config.read_env_file(path)
    if entries:
        # Persist as objects so names survive restarts; drop empty name keys.
        stored = [_entry(e["url"], e.get("name")) for e in entries]
        values[CALLBACKS_KEY] = json.dumps(stored)
    else:
        values.pop(CALLBACKS_KEY, None)
    config.write_env_file(path, values)
    return entries


def read_timeout(env_file: str | None = None) -> float:
    """Seconds to wait for each callback POST. Default 10; clamped to a sane range."""
    raw = config.read_env_file(config.env_file_path(env_file)).get(TIMEOUT_KEY, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    return _clamp_timeout(value)


def _clamp_timeout(seconds: float) -> float:
    if seconds < MIN_TIMEOUT:
        return MIN_TIMEOUT
    if seconds > MAX_TIMEOUT:
        return MAX_TIMEOUT
    return float(seconds)


def set_timeout(seconds: float | int | str, env_file: str | None = None) -> dict[str, Any]:
    """Persist the callback POST timeout (seconds) in the env file."""
    try:
        value = float(seconds)
    except (TypeError, ValueError) as exc:
        raise config.ConfigError("timeout must be a number of seconds") from exc
    if value <= 0:
        raise config.ConfigError("timeout must be greater than zero")
    clamped = _clamp_timeout(value)
    path = config.env_file_path(env_file)
    values = config.read_env_file(path)
    values[TIMEOUT_KEY] = str(clamped)
    config.write_env_file(path, values)
    return list_callbacks(env_file)


def _stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def note_delivery(
    url: str,
    *,
    ok: bool,
    status: int | None = None,
    error: str | None = None,
    event: str | None = None,
    ms: float | None = None,
) -> None:
    record: dict[str, Any] = {
        "ok": bool(ok),
        "at": _stamp(),
        "event": event,
    }
    if status is not None:
        record["status"] = int(status)
    if error:
        record["error"] = str(error)
    if ms is not None:
        record["ms"] = round(float(ms), 1)
    with _delivery_lock:
        _delivery[url] = record


def delivery_for(url: str) -> dict[str, Any] | None:
    with _delivery_lock:
        record = _delivery.get(url)
        return dict(record) if record else None


def _enrich(entry: dict[str, Any]) -> dict[str, Any]:
    item = dict(entry)
    last = delivery_for(entry["url"])
    if last:
        item["last"] = last
    return item


def list_callbacks(env_file: str | None = None) -> dict[str, Any]:
    entries = [_enrich(entry) for entry in read_callback_entries(env_file)]
    return {
        "callbacks": entries,
        "urls": [entry["url"] for entry in entries],
        "count": len(entries),
        "timeout": read_timeout(env_file),
    }


def add_callback(
    url: str,
    env_file: str | None = None,
    *,
    name: str | None = None,
) -> dict[str, Any]:
    """Register ``url`` with optional ``name``. Same URL updates the name when given."""
    normalized = _normalize_url(url)
    label = _normalize_name(name)
    entries = read_callback_entries(env_file)
    added = True
    updated = False
    for entry in entries:
        if entry["url"] == normalized:
            added = False
            if label is not None and entry.get("name") != label:
                entry["name"] = label
                updated = True
            elif label is None:
                pass
            break
    else:
        entries.append(_entry(normalized, label))
        updated = True
    if updated:
        _write_entries(entries, env_file)
    result = list_callbacks(env_file)
    result["url"] = normalized
    if label is not None:
        result["name"] = label
    result["added"] = added
    result["updated"] = updated and not added
    return result


def remove_callback(
    url: str | None = None,
    env_file: str | None = None,
    *,
    name: str | None = None,
) -> dict[str, Any]:
    """Remove by ``url`` and/or ``name``. Idempotent when missing."""
    label = _normalize_name(name)
    target_url = _normalize_url(url) if url else None
    if not target_url and not label:
        raise config.ConfigError('provide {"url": "http://..."} or {"name": "..."} to remove')

    entries = read_callback_entries(env_file)
    kept: list[dict[str, Any]] = []
    removed_entries: list[dict[str, Any]] = []
    for entry in entries:
        match_url = target_url is not None and entry["url"] == target_url
        match_name = label is not None and entry.get("name") == label
        if target_url and label:
            hit = match_url and match_name
        elif target_url:
            hit = match_url
        else:
            hit = match_name
        if hit:
            removed_entries.append(entry)
        else:
            kept.append(entry)

    if removed_entries:
        _write_entries(kept, env_file)
        with _delivery_lock:
            for entry in removed_entries:
                _delivery.pop(entry["url"], None)

    result = list_callbacks(env_file)
    if target_url:
        result["url"] = target_url
    if label:
        result["name"] = label
    result["removed"] = bool(removed_entries)
    result["removed_callbacks"] = removed_entries
    return result


def clear_callbacks(env_file: str | None = None) -> dict[str, Any]:
    _write_entries([], env_file)
    with _delivery_lock:
        _delivery.clear()
    return list_callbacks(env_file)


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


def build_names_event(
    *,
    previous_names: dict[int, str],
    names: dict[int, str],
    source: str,
    switch: TesmartSwitch,
    env_file: str | None = None,
    active: int | None = None,
) -> dict[str, Any]:
    """Payload for a local name rename/clear (not a switch protocol event)."""
    changed: dict[str, dict[str, str | None]] = {}
    all_numbers = set(previous_names) | set(names)
    for number in sorted(all_numbers):
        before = previous_names.get(number)
        after = names.get(number)
        if before == after:
            continue
        changed[str(number)] = {"from": before, "to": after}

    payload: dict[str, Any] = {
        "event": "names_changed",
        "changed": changed,
        "names": {str(number): label for number, label in names.items()},
        "input_count": switch.input_count,
        "source": source,
        "endpoint": switch.endpoint,
    }
    if active is not None:
        payload["active_input"] = active
        payload["active_name"] = names.get(active)
    return payload


def build_health_event() -> dict[str, Any]:
    return {
        "event": "health_check",
        "ok": True,
        "at": _stamp(),
    }


def build_banner_event(
    banner: dict[str, Any] | None,
    *,
    source: str,
    switch: TesmartSwitch,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Payload when an LCD banner is shown or cleared (Hubitat TESmart Controller)."""
    payload: dict[str, Any] = {
        "event": "banner",
        "banner": banner,
        "source": source,
        "endpoint": switch.endpoint,
        "input_count": switch.input_count,
        "at": _stamp(),
    }
    if history is not None:
        payload["banner_history"] = history
    return payload


def _post_one(url: str, body: bytes, *, timeout: float, event: str | None) -> dict[str, Any]:
    started = time.monotonic()
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(256)
            status = getattr(response, "status", None) or response.getcode()
        ms = (time.monotonic() - started) * 1000.0
        note_delivery(url, ok=True, status=int(status), event=event, ms=ms)
        return {"url": url, "ok": True, "status": int(status), "ms": round(ms, 1)}
    except urllib.error.HTTPError as exc:
        ms = (time.monotonic() - started) * 1000.0
        # Any HTTP response means the client is reachable; treat 2xx/3xx as honored.
        ok = 200 <= int(exc.code) < 400
        error = f"HTTP {exc.code}"
        note_delivery(url, ok=ok, status=int(exc.code), error=error, event=event, ms=ms)
        return {"url": url, "ok": ok, "status": int(exc.code), "error": error, "ms": round(ms, 1)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        ms = (time.monotonic() - started) * 1000.0
        error = str(exc)
        note_delivery(url, ok=False, error=error, event=event, ms=ms)
        return {"url": url, "ok": False, "error": error, "ms": round(ms, 1)}


def post_event(
    urls: list[str],
    payload: dict[str, Any],
    *,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    if not urls:
        return []
    wait = DEFAULT_TIMEOUT if timeout is None else float(timeout)
    body = (json.dumps(payload) + "\n").encode("utf-8")
    event = str(payload.get("event") or "")
    results: list[dict[str, Any]] = []
    for url in urls:
        result = _post_one(url, body, timeout=wait, event=event or None)
        results.append(result)
        if not result.get("ok"):
            print(f"callback failed {url}: {result.get('error') or result.get('status')}", file=sys.stderr)
    return results


def health_check(env_file: str | None = None) -> dict[str, Any]:
    """POST a ``health_check`` event to every registered callback and report results."""
    entries = read_callback_entries(env_file)
    timeout = read_timeout(env_file)
    payload = build_health_event()
    body = (json.dumps(payload) + "\n").encode("utf-8")
    results: list[dict[str, Any]] = []
    for entry in entries:
        result = _post_one(entry["url"], body, timeout=timeout, event="health_check")
        if entry.get("name"):
            result["name"] = entry["name"]
        results.append(result)
    healthy = sum(1 for item in results if item.get("ok"))
    return {
        "event": "health_check",
        "timeout": timeout,
        "count": len(results),
        "healthy": healthy,
        "unhealthy": len(results) - healthy,
        "results": results,
        "callbacks": [_enrich(entry) for entry in read_callback_entries(env_file)],
    }


def format_callbacks_text(payload: dict[str, Any]) -> str:
    """Plain-text listing for ``format=text`` responses."""
    entries = payload.get("callbacks") or []
    if not entries:
        lines = ["(none)"]
    else:
        lines = []
        for item in entries:
            if isinstance(item, str):
                lines.append(item)
                continue
            label = item.get("name") or "(unnamed)"
            url = item.get("url") or ""
            last = item.get("last") or {}
            suffix = ""
            if last:
                state = "ok" if last.get("ok") else "fail"
                suffix = f" [{state} {last.get('at', '')}]".rstrip()
            lines.append(f"{label}: {url}{suffix}")
    lines.append(f"timeout: {payload.get('timeout', DEFAULT_TIMEOUT)}")
    if "healthy" in payload:
        lines.append(f"healthy: {payload.get('healthy')}/{payload.get('count', 0)}")
    return "\n".join(lines)


class CallbackHub:
    """Watches the switch for input reports and notifies registered URLs and SSE."""

    def __init__(
        self,
        switch: TesmartSwitch,
        lock: threading.Lock,
        env_file: str | None = None,
        *,
        events: EventHub | None = None,
    ):
        self.switch = switch
        self.lock = lock
        self.env_file = env_file
        self.events = events
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

    def note_names(
        self,
        previous_names: dict[int, str],
        names: dict[int, str],
        *,
        source: str = "command",
    ) -> None:
        """Notify SSE subscribers and registered URLs that local input names changed."""
        if previous_names == names:
            return
        with self._state_lock:
            active = self._last_active
        payload = build_names_event(
            previous_names=previous_names,
            names=names,
            source=source,
            switch=self.switch,
            env_file=self.env_file,
            active=active,
        )
        if not payload.get("changed"):
            return
        self._fanout(payload)

    def note_enabled(self, *, source: str = "command") -> None:
        """Notify browsers that an input was enabled or disabled on this listener."""
        count = self.switch.input_count
        disabled = sorted(number for number in config.read_disabled(self.env_file) if 1 <= number <= count)
        names = config.read_names(self.env_file)
        self._fanout(
            {
                "event": "enabled_changed",
                "disabled": disabled,
                "names": {str(number): label for number, label in names.items()},
                "input_count": count,
                "source": source,
            }
        )

    def note_banner(
        self,
        banner: dict[str, Any] | None,
        *,
        source: str = "command",
        history: list[dict[str, Any]] | None = None,
    ) -> None:
        """Notify SSE subscribers and registered URLs that an LCD banner changed."""
        payload = build_banner_event(
            banner,
            source=source,
            switch=self.switch,
            history=history,
        )
        self._fanout(payload)

    def _fanout(self, payload: dict[str, Any]) -> None:
        """Push to browser SSE first, then Hubitat-style callback URLs if any."""
        if self.events is not None:
            self.events.publish(payload)
        urls = read_callback_urls(self.env_file)
        if not urls:
            return
        timeout = read_timeout(self.env_file)
        # Do not hold the switch lock while waiting on remote HTTP clients.
        threading.Thread(
            target=post_event,
            args=(urls, payload),
            kwargs={"timeout": timeout},
            name="tesmartctl-callback-post",
            daemon=True,
        ).start()

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
        payload = build_event(
            previous=prev,
            active=active,
            source=source,
            switch=self.switch,
            env_file=self.env_file,
        )
        self._fanout(payload)
