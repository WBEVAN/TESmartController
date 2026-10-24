"""Persistent CLI settings (default host, port, serial device, input count).

Resolution order for every setting, highest priority first:

1. command-line flag (``--host`` etc.)
2. the env file (``~/.config/tesmartctl/env`` by default, ``KEY=VALUE`` lines)
3. the process environment variable (``TESMART_HOST`` etc.), if defined
4. the built-in default

The env file location can be overridden with ``--env-file`` or the
``TESMART_ENV_FILE`` environment variable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .transport import DEFAULT_TCP_PORT

DEFAULT_HOST = "192.168.1.10"
DEFAULT_INPUTS = 8
ENV_FILE_VAR = "TESMART_ENV_FILE"
DEFAULT_ENV_FILE = Path.home() / ".config" / "tesmartctl" / "env"

# Local input labels. The switch has no field for these; they live only in the env file.
NAME_PREFIX = "TESMART_NAME_"
# Inputs this computer skips for selection, peek, and next/previous.
# Missing means every input is enabled. The switch is not told.
DISABLED_KEY = "TESMART_DISABLED"
# Local rotate cycle. Empty means "every input". The switch has no rotate command.
ROTATE_KEY = "TESMART_ROTATE"
CYCLE_MODE_KEY = "TESMART_CYCLE_MODE"
CYCLE_SECONDS_KEY = "TESMART_CYCLE_SECONDS"
DEFAULT_CYCLE_SECONDS = 10
# Peek: show another input briefly, then return. Local; the switch has no such command.
PEEK_SECONDS_KEY = "TESMART_PEEK_SECONDS"
DEFAULT_PEEK_SECONDS = 5
# Last write-only switch settings this host sent (buzzer / LED / auto-detect).
# The KVM cannot report these back; we remember them so other clients can read them.
LAST_SENT_KEY = "TESMART_LAST_SENT"
LAST_SENT_KEYS = ("buzzer", "led_timeout", "auto_detect")
# Last LCD banner messages (newest first). Survives listener restart so kits can
# show the message / history icons again without waiting for a new send.
BANNER_HISTORY_KEY = "TESMART_BANNER_HISTORY"
BANNER_HISTORY_MAX = 10
# Last discovered / preferred HTTP listener (for --via auto). Not a switch address.
VIA_KEY = "TESMART_VIA"
# Preferred mDNS instance name when several listeners are on the LAN.
# On a server this is the name quickstart advertises.
MDNS_NAME_KEY = "TESMART_MDNS_NAME"
# Listener this account's client commands attach to. Same env file as the
# server. Absent means a client still reads VIA_KEY and MDNS_NAME_KEY.
CLIENT_VIA_KEY = "TESMART_CLIENT_VIA"
CLIENT_MDNS_NAME_KEY = "TESMART_CLIENT_MDNS_NAME"
# HTTP listener saved by ``tesmartctl install`` and started by ``tesmartctl quickstart``.
# A ``--http`` argument (``8080``, ``0.0.0.0:8080``, ``172.16.0.141:7272``), or ``off``.
HTTP_KEY = "TESMART_HTTP"
HTTP_OFF = "off"
HTTP_PORT_KEY = "TESMART_HTTP_PORT"
HTTP_PANEL_KEY = "TESMART_HTTP_PANEL"
HTTP_MDNS_KEY = "TESMART_HTTP_MDNS"

# setting name -> (env var, built-in default)
SETTINGS: dict[str, tuple[str, str | None]] = {
    "host": ("TESMART_HOST", DEFAULT_HOST),
    "port": ("TESMART_PORT", str(DEFAULT_TCP_PORT)),
    "serial": ("TESMART_SERIAL", None),
    "inputs": ("TESMART_INPUTS", str(DEFAULT_INPUTS)),
}


class ConfigError(Exception):
    pass


def env_file_path(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    from_env = os.environ.get(ENV_FILE_VAR)
    return Path(from_env).expanduser() if from_env else DEFAULT_ENV_FILE


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines; blank lines and ``#`` comments are ignored."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# tesmartctl defaults (managed by `tesmartctl config`)"]
    lines += [f"{key}={value}" for key, value in sorted(values.items())]
    path.write_text("\n".join(lines) + "\n")


@dataclass
class Settings:
    host: str
    port: int
    serial: str | None
    inputs: int
    sources: dict[str, str]
    env_file: Path

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "serial": self.serial,
            "inputs": self.inputs,
            "sources": self.sources,
            "env_file": str(self.env_file),
        }


def resolve(
    env_file: str | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    serial: str | None = None,
    inputs: int | None = None,
) -> Settings:
    """Combine flags, env file, environment and defaults into a :class:`Settings`."""
    path = env_file_path(env_file)
    file_values = read_env_file(path)
    flag_values = {"host": host, "port": port, "serial": serial, "inputs": inputs}

    resolved: dict[str, str | None] = {}
    sources: dict[str, str] = {}
    for name, (env_var, default) in SETTINGS.items():
        flag = flag_values[name]
        if flag is not None:
            resolved[name], sources[name] = str(flag), "flag"
        elif env_var in file_values and file_values[env_var] != "":
            resolved[name], sources[name] = file_values[env_var], f"file:{path}"
        elif os.environ.get(env_var):
            resolved[name], sources[name] = os.environ[env_var], f"env:{env_var}"
        else:
            resolved[name], sources[name] = default, "default"

    try:
        port_value = int(resolved["port"])  # type: ignore[arg-type]
        inputs_value = int(resolved["inputs"])  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"port and inputs must be integers ({exc})") from exc

    return Settings(
        host=resolved["host"] or DEFAULT_HOST,
        port=port_value,
        serial=resolved["serial"] or None,
        inputs=inputs_value,
        sources=sources,
        env_file=path,
    )


def name_key(number: int) -> str:
    return f"{NAME_PREFIX}{number}"


def read_names(env_file: str | None = None) -> dict[int, str]:
    """Return ``{input_number: label}`` from the env file. Unknown keys are ignored."""
    values = read_env_file(env_file_path(env_file))
    names: dict[int, str] = {}
    for key, value in values.items():
        if not key.startswith(NAME_PREFIX) or not value:
            continue
        suffix = key[len(NAME_PREFIX):]
        if suffix.isdigit():
            names[int(suffix)] = value
    return dict(sorted(names.items()))


def lookup_name(number: int, names: dict[int, str] | None = None, env_file: str | None = None) -> str | None:
    table = read_names(env_file) if names is None else names
    return table.get(number)


def resolve_named_input(token: str, *, input_count: int, names: dict[int, str]) -> int:
    """Turn ``"3"`` or ``"Office"`` into an input number."""
    if token.isdigit():
        return int(token)
    folded = token.casefold()
    matches = [number for number, label in names.items() if label.casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ConfigError(f"no input named {token!r}")
    raise ConfigError(f"more than one input is named {token!r}")


def set_input_name(number: int, label: str, env_file: str | None = None, *, input_count: int = DEFAULT_INPUTS) -> Path:
    """Store a local label for ``number``. The switch is not contacted."""
    if not 1 <= number <= input_count:
        raise ConfigError(f"input must be between 1 and {input_count}, got {number}")
    cleaned = " ".join(label.split())
    if not cleaned:
        raise ConfigError("input name cannot be empty")
    path = env_file_path(env_file)
    values = read_env_file(path)
    folded = cleaned.casefold()
    for key, existing in values.items():
        if not key.startswith(NAME_PREFIX) or key == name_key(number):
            continue
        if existing.casefold() == folded:
            raise ConfigError(f"name {cleaned!r} is already used by input {key[len(NAME_PREFIX):]}")
    values[name_key(number)] = cleaned
    write_env_file(path, values)
    return path


def unset_input_name(number: int, env_file: str | None = None) -> Path:
    path = env_file_path(env_file)
    values = read_env_file(path)
    values.pop(name_key(number), None)
    write_env_file(path, values)
    return path


def read_disabled(env_file: str | None = None) -> frozenset[int]:
    """Input numbers skipped for selection, peek, and next/previous.

    An absent key means every input is enabled.
    """
    raw = read_env_file(env_file_path(env_file)).get(DISABLED_KEY, "").strip()
    if not raw:
        return frozenset()
    numbers: list[int] = []
    for part in raw.split(","):
        piece = part.strip()
        if piece.isdigit():
            numbers.append(int(piece))
    return frozenset(numbers)


def input_is_enabled(number: int, env_file: str | None = None) -> bool:
    return number not in read_disabled(env_file)


def input_enablement(input_count: int, env_file: str | None = None) -> dict[str, list[int]]:
    """Enabled and disabled input numbers from 1 through ``input_count``.

    Missing ``TESMART_DISABLED`` means every input is enabled.
    """
    disabled = sorted(number for number in read_disabled(env_file) if 1 <= number <= input_count)
    skipped = set(disabled)
    enabled = [number for number in range(1, input_count + 1) if number not in skipped]
    return {"enabled": enabled, "disabled": disabled}


def format_input_enablement(
    enabled: list[int] | tuple[int, ...],
    disabled: list[int] | tuple[int, ...],
    names: dict | None = None,
) -> tuple[str, str]:
    """One phrase each for the enabled list and the disabled list."""

    def label(number: int) -> str:
        raw = None
        if names:
            raw = names.get(number)
            if raw is None:
                raw = names.get(str(number))
        return f"{number} ({raw})" if raw else str(number)

    enabled_text = ", ".join(label(int(number)) for number in enabled) or "none"
    disabled_text = ", ".join(label(int(number)) for number in disabled) or "none"
    return enabled_text, disabled_text


def require_enabled(number: int, env_file: str | None = None) -> None:
    """Raise when ``number`` must not be selected or peeked."""
    if not input_is_enabled(number, env_file):
        raise ConfigError(f"input {number} is disabled")


def set_input_enabled(
    number: int,
    enabled: bool,
    env_file: str | None = None,
    *,
    input_count: int = DEFAULT_INPUTS,
) -> Path:
    """Remember whether ``number`` can be selected. At least one input stays enabled."""
    if not 1 <= number <= input_count:
        raise ConfigError(f"input must be between 1 and {input_count}, got {number}")
    disabled = set(read_disabled(env_file))
    if enabled:
        disabled.discard(number)
    else:
        disabled.add(number)
        remaining = [n for n in range(1, input_count + 1) if n not in disabled]
        if not remaining:
            raise ConfigError("at least one input must stay enabled")
    path = env_file_path(env_file)
    values = read_env_file(path)
    kept = sorted(n for n in disabled if n >= 1)
    if kept:
        values[DISABLED_KEY] = ",".join(str(n) for n in kept)
    else:
        values.pop(DISABLED_KEY, None)
    write_env_file(path, values)
    return path


def enabled_cycle(
    input_count: int,
    only: tuple[int, ...] | list[int] | None,
    env_file: str | None = None,
) -> tuple[int, ...]:
    """Inputs next/previous may land on.

    ``only`` is the saved cycle, kept as stored. Disabled members are skipped
    for this step. With no saved cycle, the walk is every enabled input.
    """
    pool = tuple(only) if only else tuple(range(1, input_count + 1))
    disabled = read_disabled(env_file)
    kept = tuple(number for number in pool if 1 <= number <= input_count and number not in disabled)
    if not kept:
        raise ConfigError("every input in this cycle is disabled")
    return kept


def read_rotate(env_file: str | None = None) -> tuple[int, ...] | None:
    """Saved rotate cycle, or ``None`` when rotate should walk every input."""
    raw = read_env_file(env_file_path(env_file)).get(ROTATE_KEY, "").strip()
    if not raw:
        return None
    from .switch import parse_input_list

    try:
        return parse_input_list(raw)
    except ValueError as exc:
        raise ConfigError(f"{ROTATE_KEY} in the env file is invalid: {exc}") from exc


def set_rotate(only: tuple[int, ...] | list[int], env_file: str | None = None, *, input_count: int = DEFAULT_INPUTS) -> Path:
    """Remember which inputs ``rotate`` walks. Not sent to the switch."""
    if not only:
        raise ConfigError("rotate cycle cannot be empty; clear it instead")
    for number in only:
        if not 1 <= number <= input_count:
            raise ConfigError(f"input must be between 1 and {input_count}, got {number}")
    path = env_file_path(env_file)
    values = read_env_file(path)
    values[ROTATE_KEY] = ",".join(str(number) for number in only)
    write_env_file(path, values)
    return path


def clear_rotate(env_file: str | None = None) -> Path:
    path = env_file_path(env_file)
    values = read_env_file(path)
    values.pop(ROTATE_KEY, None)
    write_env_file(path, values)
    return path


def read_cycle_mode(env_file: str | None = None) -> str:
    """``manual`` (next/previous) or ``automatic`` (timed ``cycle run``)."""
    raw = read_env_file(env_file_path(env_file)).get(CYCLE_MODE_KEY, "manual").strip().lower()
    if raw in {"auto", "automatic"}:
        return "automatic"
    if raw in {"", "manual", "next"}:
        return "manual"
    raise ConfigError(f"{CYCLE_MODE_KEY} must be manual or automatic, got {raw!r}")


def read_cycle_seconds(env_file: str | None = None) -> int:
    raw = read_env_file(env_file_path(env_file)).get(CYCLE_SECONDS_KEY, "").strip()
    if not raw:
        return DEFAULT_CYCLE_SECONDS
    if not raw.isdigit() or int(raw) < 1:
        raise ConfigError(f"{CYCLE_SECONDS_KEY} must be a positive number of seconds, got {raw!r}")
    return int(raw)


def set_cycle_mode(mode: str, env_file: str | None = None, *, seconds: int | None = None) -> Path:
    normalized = mode.strip().lower()
    if normalized in {"auto", "automatic"}:
        stored = "automatic"
    elif normalized == "manual":
        stored = "manual"
    else:
        raise ConfigError(f"cycle mode must be manual or automatic, got {mode!r}")
    if seconds is not None and seconds < 1:
        raise ConfigError("cycle interval must be at least 1 second")
    path = env_file_path(env_file)
    values = read_env_file(path)
    values[CYCLE_MODE_KEY] = stored
    if seconds is not None:
        values[CYCLE_SECONDS_KEY] = str(seconds)
    write_env_file(path, values)
    return path


def read_peek_seconds(env_file: str | None = None) -> int:
    """How long ``peek`` shows the other input before returning."""
    raw = read_env_file(env_file_path(env_file)).get(PEEK_SECONDS_KEY, "").strip()
    if not raw:
        return DEFAULT_PEEK_SECONDS
    if not raw.isdigit() or int(raw) < 1:
        raise ConfigError(f"{PEEK_SECONDS_KEY} must be a positive number of seconds, got {raw!r}")
    return int(raw)


def set_peek_seconds(seconds: int, env_file: str | None = None) -> Path:
    if seconds < 1:
        raise ConfigError("peek duration must be at least 1 second")
    path = env_file_path(env_file)
    values = read_env_file(path)
    values[PEEK_SECONDS_KEY] = str(int(seconds))
    write_env_file(path, values)
    return path


def peek_info(env_file: str | None = None) -> dict:
    """What status reports about peek: the saved duration."""
    return {"supported": True, "seconds": read_peek_seconds(env_file)}


def read_last_sent(env_file: str | None = None) -> dict[str, Any]:
    """Last buzzer / LED timeout / auto-detect values written through tesmartctl."""
    out: dict[str, Any] = {key: None for key in LAST_SENT_KEYS}
    raw = read_env_file(env_file_path(env_file)).get(LAST_SENT_KEY, "").strip()
    if not raw:
        return out
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return out
    if not isinstance(parsed, dict):
        return out
    if "buzzer" in parsed and parsed["buzzer"] is not None:
        out["buzzer"] = bool(parsed["buzzer"])
    if "led_timeout" in parsed and parsed["led_timeout"] is not None:
        out["led_timeout"] = str(parsed["led_timeout"])
    if "auto_detect" in parsed and parsed["auto_detect"] is not None:
        out["auto_detect"] = bool(parsed["auto_detect"])
    return out


def update_last_sent(updates: dict[str, Any], env_file: str | None = None) -> dict[str, Any]:
    """Merge ``updates`` into the persisted last-sent table and return the full table."""
    current = read_last_sent(env_file)
    for key in LAST_SENT_KEYS:
        if key not in updates:
            continue
        value = updates[key]
        if key == "led_timeout":
            current[key] = None if value is None else str(value)
        elif key in {"buzzer", "auto_detect"}:
            current[key] = None if value is None else bool(value)
    path = env_file_path(env_file)
    values = read_env_file(path)
    # Only persist keys that have been set at least once.
    stored = {key: current[key] for key in LAST_SENT_KEYS if current[key] is not None}
    if stored:
        values[LAST_SENT_KEY] = json.dumps(stored)
    else:
        values.pop(LAST_SENT_KEY, None)
    write_env_file(path, values)
    return current


def read_banner_history(env_file: str | None = None) -> list[dict[str, Any]]:
    """Persisted LCD banner history (newest first), or an empty list."""
    raw = read_env_file(env_file_path(env_file)).get(BANNER_HISTORY_KEY, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, Any]] = []
    for item in parsed[:BANNER_HISTORY_MAX]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            entry_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            entry_id = 0
        mode = "sticky" if str(item.get("mode") or "") == "sticky" else "timed"
        seconds = item.get("seconds")
        try:
            seconds_i = None if seconds is None else int(seconds)
        except (TypeError, ValueError):
            seconds_i = None
        try:
            at = float(item.get("at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        out.append(
            {
                "id": entry_id,
                "text": text[:160],
                "at": at,
                "at_iso": item.get("at_iso"),
                "mode": mode,
                "seconds": seconds_i if mode == "timed" else None,
            }
        )
    return out


def write_banner_history(history: list[dict[str, Any]], env_file: str | None = None) -> Path:
    """Persist up to ``BANNER_HISTORY_MAX`` banner entries (newest first)."""
    path = env_file_path(env_file)
    values = read_env_file(path)
    cleaned: list[dict[str, Any]] = []
    for item in history[:BANNER_HISTORY_MAX]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        cleaned.append(
            {
                "id": int(item.get("id") or 0),
                "text": text[:160],
                "at": float(item.get("at") or 0),
                "at_iso": item.get("at_iso"),
                "mode": "sticky" if item.get("mode") == "sticky" else "timed",
                "seconds": item.get("seconds"),
            }
        )
    if cleaned:
        values[BANNER_HISTORY_KEY] = json.dumps(cleaned)
    else:
        values.pop(BANNER_HISTORY_KEY, None)
    write_env_file(path, values)
    return path


def rotate_info(env_file: str | None = None, *, input_count: int = DEFAULT_INPUTS) -> dict:
    """What status reports about the local cycle.

    Stepping inputs is always supported here. The switch has no rotate command.
    ``configured`` is true only when specific inputs were saved. ``mode`` is
    ``manual`` (next/previous) or ``automatic`` (``cycle run`` advances on a timer).
    """
    configured = read_rotate(env_file)
    cycle = list(configured) if configured else list(range(1, input_count + 1))
    mode = read_cycle_mode(env_file)
    return {
        "supported": True,
        "configured": configured is not None,
        "mode": mode,
        "seconds": read_cycle_seconds(env_file) if mode == "automatic" else None,
        "cycle": cycle,
    }


def apply_names(payload: dict, names: dict[int, str], *, include_map: bool = False) -> dict:
    """Add local labels to a status or input payload. Does not remove existing keys."""
    for source, dest in (
        ("active_input", "active_name"),
        ("previous", "previous_name"),
        ("requested", "requested_name"),
    ):
        number = payload.get(source)
        if isinstance(number, int):
            payload[dest] = names.get(number)
    if include_map:
        payload["names"] = {str(number): label for number, label in names.items()}
    return payload


def set_default(name: str, value: str, env_file: str | None = None) -> Path:
    """Persist ``name=value`` to the env file and return its path."""
    if name not in SETTINGS:
        raise ConfigError(f"unknown setting {name!r}; choose from {', '.join(SETTINGS)}")
    if name in {"port", "inputs"} and not value.isdigit():
        raise ConfigError(f"{name} must be an integer, got {value!r}")
    path = env_file_path(env_file)
    values = read_env_file(path)
    values[SETTINGS[name][0]] = value
    write_env_file(path, values)
    return path


def unset_default(name: str, env_file: str | None = None) -> Path:
    if name not in SETTINGS:
        raise ConfigError(f"unknown setting {name!r}; choose from {', '.join(SETTINGS)}")
    path = env_file_path(env_file)
    values = read_env_file(path)
    values.pop(SETTINGS[name][0], None)
    write_env_file(path, values)
    return path


def _read_optional(key: str, env_file: str | None = None) -> str | None:
    path = env_file_path(env_file)
    file_values = read_env_file(path)
    if key in file_values and file_values[key].strip():
        return file_values[key].strip()
    from_env = os.environ.get(key)
    return from_env.strip() if from_env and from_env.strip() else None


def _write_optional(key: str, value: str | None, env_file: str | None = None) -> Path:
    path = env_file_path(env_file)
    values = read_env_file(path)
    if value is None or not str(value).strip():
        values.pop(key, None)
    else:
        values[key] = str(value).strip()
    write_env_file(path, values)
    return path


def read_via_url(env_file: str | None = None) -> str | None:
    """Last preferred HTTP listener base URL (``TESMART_VIA``)."""
    return _read_optional(VIA_KEY, env_file)


def write_via_url(url: str, env_file: str | None = None) -> Path:
    return _write_optional(VIA_KEY, url.rstrip("/"), env_file)


def read_mdns_name(env_file: str | None = None) -> str | None:
    """Preferred mDNS instance name (``TESMART_MDNS_NAME``)."""
    return _read_optional(MDNS_NAME_KEY, env_file)


def write_mdns_name(name: str | None, env_file: str | None = None) -> Path:
    return _write_optional(MDNS_NAME_KEY, name, env_file)


def client_connection_active(env_file: str | None = None) -> bool:
    """True when this account has its own client listener connection."""
    return bool(
        _read_optional(CLIENT_VIA_KEY, env_file) or _read_optional(CLIENT_MDNS_NAME_KEY, env_file)
    )


def read_client_via_url(env_file: str | None = None) -> str | None:
    """Listener URL a client should use.

    ``TESMART_CLIENT_VIA`` when a client connection is saved. Otherwise
    ``TESMART_VIA``, which is what a client used before it had its own values.
    """
    if client_connection_active(env_file):
        return _read_optional(CLIENT_VIA_KEY, env_file)
    return read_via_url(env_file)


def read_client_mdns_name(env_file: str | None = None) -> str | None:
    """mDNS name a client should attach to.

    ``TESMART_CLIENT_MDNS_NAME`` when a client connection is saved, including
    when that connection is a URL and has no name. Otherwise
    ``TESMART_MDNS_NAME``.
    """
    if client_connection_active(env_file):
        return _read_optional(CLIENT_MDNS_NAME_KEY, env_file)
    return read_mdns_name(env_file)


def write_client_connection(
    via: str | None,
    mdns_name: str | None,
    env_file: str | None = None,
) -> Path:
    """Store the listener a client uses. Server listener keys stay as they are."""
    path = _write_optional(CLIENT_VIA_KEY, via, env_file)
    return _write_optional(CLIENT_MDNS_NAME_KEY, mdns_name, env_file)


def remember_client_listener(
    url: str,
    env_file: str | None = None,
    *,
    mdns_name: str | None = None,
) -> Path:
    """Remember a listener a client attached to.

    A saved client connection is updated in the ``CLIENT_`` keys. With no
    client connection, ``TESMART_VIA`` and ``TESMART_MDNS_NAME`` are updated.
    """
    if client_connection_active(env_file):
        path = _write_optional(CLIENT_VIA_KEY, url, env_file)
        if mdns_name:
            path = _write_optional(CLIENT_MDNS_NAME_KEY, mdns_name, env_file)
        return path
    path = write_via_url(url, env_file)
    if mdns_name:
        path = write_mdns_name(mdns_name, env_file)
    return path


def read_http_spec(env_file: str | None = None) -> str | None:
    """Saved ``--http`` argument, ``off``, or None when install has not stored one."""
    return _read_optional(HTTP_KEY, env_file)


def read_http_port(env_file: str | None = None) -> int | None:
    """Saved HTTP listen port (``TESMART_HTTP_PORT``), or None when it is not recorded."""
    raw = _read_optional(HTTP_PORT_KEY, env_file)
    if raw and raw.isdigit() and 1 <= int(raw) <= 65535:
        return int(raw)
    return None


def read_http_panel(env_file: str | None = None) -> bool | None:
    """True, False, or None when install has not recorded the browser UI choice."""
    raw = _read_optional(HTTP_PANEL_KEY, env_file)
    if raw == "1":
        return True
    if raw == "0":
        return False
    return None


def _port_from_http_spec(spec: str) -> str | None:
    text = spec.strip()
    if text.isdigit():
        return text
    _host, sep, port_text = text.rpartition(":")
    if sep and port_text.isdigit() and 1 <= int(port_text) <= 65535:
        return port_text
    return None


def read_http_mdns(env_file: str | None = None) -> bool | None:
    """True, False, or None when install did not record an mDNS choice."""
    raw = _read_optional(HTTP_MDNS_KEY, env_file)
    if raw == "1":
        return True
    if raw == "0":
        return False
    return None


def write_http_listener(
    spec: str | None,
    *,
    panel: bool = False,
    mdns: bool | None = None,
    port: int | None = None,
    env_file: str | None = None,
) -> Path:
    """Store the HTTP listener for ``tesmartctl quickstart``.

    ``spec`` is the ``--http`` argument, ``off`` when the listener is disabled,
    or None to remove the listener keys (a client configuration). The listen
    port is stored in ``TESMART_HTTP_PORT`` and the browser UI choice in
    ``TESMART_HTTP_PANEL`` (``1`` or ``0``).
    """
    path = env_file_path(env_file)
    values = read_env_file(path)
    if spec is None:
        values.pop(HTTP_KEY, None)
        values.pop(HTTP_PORT_KEY, None)
        values.pop(HTTP_PANEL_KEY, None)
        values.pop(HTTP_MDNS_KEY, None)
    elif spec == HTTP_OFF:
        values[HTTP_KEY] = HTTP_OFF
        values.pop(HTTP_PANEL_KEY, None)
        values.pop(HTTP_MDNS_KEY, None)
    else:
        values[HTTP_KEY] = spec.strip()
        values[HTTP_PANEL_KEY] = "1" if panel else "0"
        recorded = str(port) if port is not None else _port_from_http_spec(spec)
        if recorded:
            values[HTTP_PORT_KEY] = recorded
        if mdns is True:
            values[HTTP_MDNS_KEY] = "1"
        elif mdns is False:
            values[HTTP_MDNS_KEY] = "0"
        else:
            values.pop(HTTP_MDNS_KEY, None)
    write_env_file(path, values)
    return path
