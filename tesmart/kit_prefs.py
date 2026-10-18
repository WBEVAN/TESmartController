"""Remembered settings for the floating kit window.

The frameless pywebview window runs in private mode, so browser
``localStorage`` does not survive a restart. The kit keeps its own small JSON
file next to the env file instead (``~/.config/tesmartctl/kit.json``):

* ``position`` — window top-left in native screen coordinates
* ``on_top`` — Always on top
* ``view`` — Compact, Mini, or Full
* ``peek_follow`` — the peek-follow mode last chosen in the kit
* ``body`` — charcoal ``black`` (default) or metallic ``silver`` fascia
* ``lcd`` — ``green`` (default), ``blue``, ``red``, or dark ``amber`` (orange phosphor)
* ``highlight`` — optional ``#RRGGBB`` key/chrome highlight; omit for LCD default
* ``lcd_glass`` — ``matrix`` (default mesh), ``phosphor`` (mesh + bleed), or ``off``

``fresh=True`` ignores whatever is saved for this launch (defaults only); new
changes made in that session are saved as normal, replacing the old file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from .config import DEFAULT_ENV_FILE

DEFAULT_PATH = DEFAULT_ENV_FILE.parent / "kit.json"

VIEWS = ("compact", "mini", "full")
PEEK_MODES = ("always", "off")
BODY_THEMES = ("black", "silver")
LCD_THEMES = ("green", "blue", "red", "amber")
LCD_GLASS = ("matrix", "phosphor", "off")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _clean(data: Any) -> dict[str, Any]:
    """Keep only known keys with sane values (a hand-edited file cannot break the kit)."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    pos = data.get("position")
    if (
        isinstance(pos, dict)
        and isinstance(pos.get("x"), (int, float))
        and isinstance(pos.get("y"), (int, float))
    ):
        out["position"] = {"x": float(pos["x"]), "y": float(pos["y"])}
    if isinstance(data.get("on_top"), bool):
        out["on_top"] = data["on_top"]
    if data.get("view") in VIEWS:
        out["view"] = data["view"]
    if data.get("peek_follow") in PEEK_MODES:
        out["peek_follow"] = data["peek_follow"]
    seconds = data.get("peek_seconds")
    if isinstance(seconds, int) and not isinstance(seconds, bool) and 1 <= seconds <= 60:
        out["peek_seconds"] = seconds
    if data.get("body") in BODY_THEMES:
        out["body"] = data["body"]
    if data.get("lcd") in LCD_THEMES:
        out["lcd"] = data["lcd"]
    if data.get("lcd_glass") in LCD_GLASS:
        out["lcd_glass"] = data["lcd_glass"]
    highlight = data.get("highlight")
    if isinstance(highlight, str) and _HEX_COLOR.fullmatch(highlight.strip()):
        out["highlight"] = highlight.strip().lower()
    return out


class KitPrefs:
    """Thread-safe load/update of the kit preference file."""

    def __init__(self, path: Path | str | None = None, *, fresh: bool = False) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.fresh = fresh
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {} if fresh else self._read()

    def _read(self) -> dict[str, Any]:
        try:
            return _clean(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        """Merge ``values`` (invalid keys dropped; ``None`` removes a key) and save."""
        with self._lock:
            merged = dict(self._data)
            for key, value in (values or {}).items():
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            self._data = _clean(merged)
            self._write(self._data)
            return dict(self._data)

    def _write(self, data: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".kit-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        except OSError:
            pass
