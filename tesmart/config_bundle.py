"""Export and import a portable listener configuration.

The bundle wraps the env file (names, enablement, cycle, peek, banners and
message history, callbacks, last-sent switch settings, HTTP/mDNS listener and
client connection keys) plus optional kit look/feel prefs from ``kit.json``.

Live switch state (active input, network on the device) is not included.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .kit_prefs import KitPrefs
from .kit_prefs import _clean as clean_kit_prefs
from .version import package_version

FORMAT = "tesmartctl-config"
FORMAT_VERSION = 1


class BundleError(Exception):
    """Raised when a config bundle cannot be read or applied."""


def export_bundle(
    env_file: str | None = None,
    *,
    include_kit: bool = True,
    kit_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build a portable dump of the current listener configuration."""
    path = config.env_file_path(env_file)
    env = config.read_env_file(path)
    bundle: dict[str, Any] = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tesmartctl": package_version(),
        "env": dict(env),
    }
    if include_kit:
        prefs = KitPrefs(kit_path if kit_path is not None else path.parent / "kit.json")
        kit = prefs.snapshot()
        if kit:
            bundle["kit"] = kit
    return bundle


def dump_bundle(bundle: dict[str, Any]) -> str:
    """Serialize a bundle as indented JSON with a trailing newline."""
    return json.dumps(bundle, indent=2, sort_keys=False) + "\n"


def write_bundle(path: Path | str, bundle: dict[str, Any]) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_bundle(bundle), encoding="utf-8")
    return target


def load_bundle(path: Path | str) -> dict[str, Any]:
    """Read and validate a config bundle from disk."""
    target = Path(path).expanduser()
    if not target.is_file():
        raise BundleError(f"config file not found: {target}")
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise BundleError(f"cannot read {target}: {exc}") from exc
    return parse_bundle(raw, source=str(target))


def parse_bundle(raw: str | bytes | dict[str, Any], *, source: str = "config") -> dict[str, Any]:
    """Validate a bundle object or JSON text."""
    if isinstance(raw, dict):
        data = raw
    else:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BundleError(f"{source}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise BundleError(f"{source}: expected a JSON object")
    if data.get("format") != FORMAT:
        raise BundleError(
            f"{source}: unknown format {data.get('format')!r}; expected {FORMAT!r}"
        )
    try:
        version = int(data.get("version"))
    except (TypeError, ValueError) as exc:
        raise BundleError(f"{source}: missing or invalid version") from exc
    if version < 1 or version > FORMAT_VERSION:
        raise BundleError(f"{source}: unsupported version {version}")
    env = data.get("env")
    if not isinstance(env, dict):
        raise BundleError(f"{source}: missing env object")
    cleaned_env: dict[str, str] = {}
    for key, value in env.items():
        name = str(key).strip()
        if not name:
            continue
        if value is None:
            continue
        cleaned_env[name] = value if isinstance(value, str) else str(value)
    kit = data.get("kit")
    cleaned_kit: dict[str, Any] | None = None
    if kit is not None:
        if not isinstance(kit, dict):
            raise BundleError(f"{source}: kit must be an object when present")
        cleaned_kit = clean_kit_prefs(kit)
    return {
        "format": FORMAT,
        "version": version,
        "exported_at": data.get("exported_at"),
        "tesmartctl": data.get("tesmartctl"),
        "env": cleaned_env,
        "kit": cleaned_kit,
    }


def summarize_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Short counts for install confirm / panel import preview."""
    env = bundle.get("env") or {}
    names = sorted(
        int(key[len(config.NAME_PREFIX) :])
        for key in env
        if key.startswith(config.NAME_PREFIX) and key[len(config.NAME_PREFIX) :].isdigit()
    )
    history_raw = (env.get(config.BANNER_HISTORY_KEY) or "").strip()
    history_count = 0
    if history_raw:
        try:
            parsed = json.loads(history_raw)
            if isinstance(parsed, list):
                history_count = len(parsed)
        except json.JSONDecodeError:
            history_count = 0
    callbacks_raw = (env.get("TESMART_CALLBACKS") or "").strip()
    callback_count = 0
    if callbacks_raw:
        try:
            parsed = json.loads(callbacks_raw)
            if isinstance(parsed, list):
                callback_count = len(parsed)
        except json.JSONDecodeError:
            callback_count = 0
    disabled = (env.get(config.DISABLED_KEY) or "").strip()
    disabled_count = len([part for part in disabled.split(",") if part.strip()]) if disabled else 0
    kit = bundle.get("kit")
    return {
        "host": env.get("TESMART_HOST"),
        "port": env.get("TESMART_PORT"),
        "inputs": env.get("TESMART_INPUTS"),
        "serial": env.get("TESMART_SERIAL"),
        "listen": env.get(config.HTTP_KEY),
        "mdns_name": env.get(config.MDNS_NAME_KEY),
        "names": len(names),
        "named_inputs": names,
        "disabled": disabled_count,
        "messages": history_count,
        "callbacks": callback_count,
        "has_cycle": bool((env.get(config.ROTATE_KEY) or "").strip() or env.get(config.CYCLE_MODE_KEY)),
        "has_kit": bool(kit),
        "exported_at": bundle.get("exported_at"),
        "tesmartctl": bundle.get("tesmartctl"),
    }


def apply_bundle(
    bundle: dict[str, Any],
    env_file: str | None = None,
    *,
    include_kit: bool = True,
    kit_path: Path | str | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """Write ``bundle`` into the env file (and optional kit prefs).

    ``replace=True`` (default) replaces the env file contents with the bundle's
    ``env`` map. ``replace=False`` merges bundle keys on top of the existing file.
    """
    parsed = parse_bundle(bundle)
    path = config.env_file_path(env_file)
    incoming = dict(parsed["env"])
    if replace:
        values = incoming
    else:
        values = config.read_env_file(path)
        values.update(incoming)
    config.write_env_file(path, values)

    kit_written = False
    kit_data = parsed.get("kit")
    if include_kit and kit_data is not None:
        target = Path(kit_path) if kit_path is not None else path.parent / "kit.json"
        prefs = KitPrefs(target, fresh=True)
        prefs.update(kit_data)
        kit_written = True
    elif include_kit and replace and kit_data is None:
        # A replace import without kit leaves kit.json alone so a move that only
        # carried listener data does not wipe local window prefs by accident.
        pass

    return {
        "env_file": str(path),
        "keys": len(values),
        "kit_written": kit_written,
        "summary": summarize_bundle(parsed),
    }


def summary_lines(summary: dict[str, Any]) -> list[str]:
    """Human-readable lines for the install wizard / CLI."""
    lines: list[str] = []
    host = summary.get("host") or "-"
    port = summary.get("port") or "-"
    inputs = summary.get("inputs") or "-"
    lines.append(f"  switch:     {host}:{port} ({inputs} inputs)")
    if summary.get("serial"):
        lines.append(f"  serial:     {summary['serial']}")
    listen = summary.get("listen")
    if listen:
        lines.append(f"  listen:     {listen}")
    if summary.get("mdns_name"):
        lines.append(f"  mdns:       {summary['mdns_name']}")
    lines.append(f"  names:      {summary.get('names', 0)}")
    lines.append(f"  disabled:   {summary.get('disabled', 0)}")
    lines.append(f"  messages:   {summary.get('messages', 0)}")
    lines.append(f"  callbacks:  {summary.get('callbacks', 0)}")
    lines.append(f"  kit prefs:  {'yes' if summary.get('has_kit') else 'no'}")
    if summary.get("exported_at"):
        lines.append(f"  exported:   {summary['exported_at']}")
    if summary.get("tesmartctl"):
        lines.append(f"  from:       tesmartctl {summary['tesmartctl']}")
    return lines
