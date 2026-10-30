"""LCD banner messages for kit / panel / Hubitat clients.

Sending a message broadcasts it (clients show it) and keeps it as the last
message. The board also retains the last ``HISTORY_MAX`` messages so a client
can reopen an earlier one locally without re-broadcasting. History is restored
from the listener env file on startup (icons come back; the LCD does not
auto-pop the last message until the client shows it or a new one is sent).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

DEFAULT_SECONDS = 8
MIN_SECONDS = 1
MAX_SECONDS = 60
MAX_TEXT = 160
# Status-line label above the LCD text (kit shows this instead of a hard-coded MESSAGE).
DEFAULT_FROM = "MESSAGE"
MAX_FROM = 32
HISTORY_MAX = 10

MODE_TIMED = "timed"
MODE_STICKY = "sticky"

PersistFn = Callable[[list[dict[str, Any]]], None]


def _clamp_seconds(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("seconds must be an integer") from exc
    if seconds < MIN_SECONDS or seconds > MAX_SECONDS:
        raise ValueError(f"seconds must be between {MIN_SECONDS} and {MAX_SECONDS}")
    return seconds


def _clean_text(value: Any) -> str:
    if value is None:
        raise ValueError("text is required")
    text = " ".join(str(value).split())
    if not text:
        raise ValueError("text must not be empty")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text must be at most {MAX_TEXT} characters")
    return text


def _clean_from(value: Any) -> str:
    """Optional sender / origin label for the kit status line. Blank → MESSAGE."""
    if value is None:
        return DEFAULT_FROM
    text = " ".join(str(value).split())
    if not text:
        return DEFAULT_FROM
    if len(text) > MAX_FROM:
        raise ValueError(f"from must be at most {MAX_FROM} characters")
    return text


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "sticky"}
    return False


class BannerBoard:
    """Thread-safe last LCD message, history ring, timed or sticky display."""

    def __init__(
        self,
        *,
        history: list[dict[str, Any]] | None = None,
        persist: PersistFn | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._id = 0
        self._text = ""
        self._from = DEFAULT_FROM
        self._mode = MODE_TIMED
        self._seconds = 0
        self._at = 0.0
        self._until = 0.0
        self._showing = False
        self._history: deque[dict[str, Any]] = deque(maxlen=HISTORY_MAX)
        self._persist = persist
        if history:
            self._restore(history)

    def _restore(self, history: list[dict[str, Any]]) -> None:
        """Load persisted history. Last message is available but not auto-showing."""
        cleaned: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                entry_id = int(item.get("id") or 0)
            except (TypeError, ValueError):
                entry_id = 0
            mode = MODE_STICKY if item.get("mode") == MODE_STICKY else MODE_TIMED
            seconds = item.get("seconds")
            try:
                seconds_i = 0 if seconds is None else int(seconds)
            except (TypeError, ValueError):
                seconds_i = DEFAULT_SECONDS if mode == MODE_TIMED else 0
            try:
                at = float(item.get("at") or 0)
            except (TypeError, ValueError):
                at = 0.0
            at_iso = item.get("at_iso")
            if not at_iso and at:
                at_iso = datetime.fromtimestamp(at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                from_label = _clean_from(item.get("from"))
            except ValueError:
                from_label = DEFAULT_FROM
            cleaned.append(
                {
                    "id": entry_id,
                    "text": text[:MAX_TEXT],
                    "from": from_label,
                    "at": round(at, 3),
                    "at_iso": at_iso,
                    "mode": mode,
                    "seconds": seconds_i if mode == MODE_TIMED else None,
                }
            )
        if not cleaned:
            return
        self._history = deque(cleaned[:HISTORY_MAX], maxlen=HISTORY_MAX)
        newest = cleaned[0]
        self._id = max(int(item["id"]) for item in cleaned)
        self._text = str(newest["text"])
        self._from = str(newest.get("from") or DEFAULT_FROM)
        self._mode = newest["mode"]
        self._seconds = int(newest["seconds"] or 0) if newest["mode"] == MODE_TIMED else 0
        self._at = float(newest["at"] or 0)
        self._until = 0.0
        self._showing = False

    def _save_locked(self) -> None:
        if self._persist is None:
            return
        self._persist([dict(item) for item in self._history])

    def show(
        self,
        text: Any,
        seconds: Any = None,
        *,
        sticky: Any = False,
        from_: Any = None,
    ) -> dict[str, Any]:
        """Store a new last message, push history, and mark it showing.

        ``from_`` is the optional status-line label (JSON/CLI field ``from``).
        When omitted it is ``MESSAGE``.
        """
        cleaned = _clean_text(text)
        from_label = _clean_from(from_)
        is_sticky = _as_bool(sticky)
        if is_sticky:
            duration = 0
            mode = MODE_STICKY
        else:
            duration = _clamp_seconds(seconds if seconds is not None else DEFAULT_SECONDS)
            mode = MODE_TIMED
        now = time.time()
        with self._lock:
            self._id += 1
            self._text = cleaned
            self._from = from_label
            self._mode = mode
            self._seconds = duration
            self._at = now
            self._until = 0.0 if is_sticky else now + duration
            self._showing = True
            # Newest first; deque maxlen drops the oldest when the 11th arrives.
            self._history.appendleft(self._history_entry_locked())
            while len(self._history) > HISTORY_MAX:
                self._history.pop()
            self._save_locked()
            return self._snapshot_locked(now)

    def reshow(self) -> dict[str, Any]:
        """Put the kept last message back on the LCD (restarts a timed window)."""
        now = time.time()
        with self._lock:
            if not self._text:
                raise ValueError("no last message to show")
            if self._mode == MODE_TIMED:
                duration = self._seconds or DEFAULT_SECONDS
                self._until = now + duration
            else:
                self._until = 0.0
            self._showing = True
            return self._snapshot_locked(now)

    def dismiss(self) -> dict[str, Any] | None:
        """Hide from the LCD; keep the last message and history."""
        now = time.time()
        with self._lock:
            if not self._text:
                return None
            self._expire_locked(now)
            previous = self._snapshot_locked(now)
            self._showing = False
            self._until = 0.0
            return previous

    def clear(self) -> dict[str, Any] | None:
        """Erase the last message and history."""
        now = time.time()
        with self._lock:
            self._expire_locked(now)
            previous = self._snapshot_locked(now) if self._text else None
            self._text = ""
            self._from = DEFAULT_FROM
            self._mode = MODE_TIMED
            self._seconds = 0
            self._until = 0.0
            self._at = 0.0
            self._showing = False
            self._history.clear()
            self._save_locked()
            return previous

    def status(self) -> dict[str, Any] | None:
        with self._lock:
            return self._snapshot_locked(time.time())

    def history(self) -> list[dict[str, Any]]:
        """Newest-first list of up to ``HISTORY_MAX`` kept messages."""
        with self._lock:
            return [dict(item) for item in self._history]

    def payload(self) -> dict[str, Any]:
        """Banner envelope plus history for ``/api/state`` and ``/api/banner``."""
        with self._lock:
            return {
                "banner": self._snapshot_locked(time.time()),
                "banner_history": [dict(item) for item in self._history],
            }

    def _history_entry_locked(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "text": self._text,
            "from": self._from or DEFAULT_FROM,
            "at": round(self._at, 3),
            "at_iso": datetime.fromtimestamp(self._at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": self._mode,
            "seconds": self._seconds if self._mode == MODE_TIMED else None,
        }

    def _expire_locked(self, now: float) -> None:
        if self._showing and self._mode == MODE_TIMED and self._until and now >= self._until:
            self._showing = False
            self._until = 0.0

    def _snapshot_locked(self, now: float) -> dict[str, Any] | None:
        self._expire_locked(now)
        if not self._text:
            return None
        remaining = None
        until = None
        if self._showing and self._mode == MODE_TIMED and self._until:
            remaining = round(max(0.0, self._until - now), 2)
            until = round(self._until, 3)
        return {
            "id": self._id,
            "text": self._text,
            "from": self._from or DEFAULT_FROM,
            "at": round(self._at, 3),
            "at_iso": datetime.fromtimestamp(self._at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": self._mode,
            "seconds": self._seconds if self._mode == MODE_TIMED else None,
            "until": until,
            "remaining": remaining,
            "showing": self._showing,
        }
