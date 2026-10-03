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

import os
from dataclasses import dataclass
from pathlib import Path

from .transport import DEFAULT_TCP_PORT

DEFAULT_HOST = "192.168.1.10"
DEFAULT_INPUTS = 8
ENV_FILE_VAR = "TESMART_ENV_FILE"
DEFAULT_ENV_FILE = Path.home() / ".config" / "tesmartctl" / "env"

# Local input labels. The switch has no field for these; they live only in the env file.
NAME_PREFIX = "TESMART_NAME_"
# Local rotate cycle. Empty means "every input". The switch has no rotate command.
ROTATE_KEY = "TESMART_ROTATE"
CYCLE_MODE_KEY = "TESMART_CYCLE_MODE"
CYCLE_SECONDS_KEY = "TESMART_CYCLE_SECONDS"
DEFAULT_CYCLE_SECONDS = 10
# Peek: show another input briefly, then return. Local; the switch has no such command.
PEEK_SECONDS_KEY = "TESMART_PEEK_SECONDS"
DEFAULT_PEEK_SECONDS = 5

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
