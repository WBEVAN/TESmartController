"""Talk to a running ``tesmartctl listen --http`` instead of the switch.

Used by ``tesmartctl --via auto …`` and ``tesmartctl discover``. Resolves the
listener over mDNS when asked, then issues the same HTTP routes Hubitat and
the kit already use.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import quote

from . import config
from .mdns import (
    DEFAULT_BROWSE_TIMEOUT,
    DiscoveredListener,
    MdnsError,
    browse,
    parse_via_target,
    resolve_listener,
)

# Commands that can run entirely through the HTTP listener.
VIA_COMMANDS = frozenset(
    {
        "status",
        "input",
        "rotate",
        "next",
        "previous",
        "peek",
        "banner",
        "kit",
        "discover",
    }
)


def resolve_base_url(
    *,
    via: str,
    mdns_name: str | None = None,
    env_file: str | None = None,
    timeout: float = DEFAULT_BROWSE_TIMEOUT,
    remember: bool = True,
) -> str:
    """Turn ``--via auto`` or an explicit URL into an ``http://host:port`` base."""
    from .debug import log

    target = parse_via_target(via)
    if target != "auto":
        log(f"via {target}")
        if remember:
            config.write_via_url(target, env_file)
        return target

    name = (mdns_name or config.read_mdns_name(env_file) or "").strip() or None
    prefer = config.read_via_url(env_file)
    service = resolve_listener(name=name, prefer_url=prefer, timeout=timeout)
    base = service.base_url
    if remember:
        config.write_via_url(base, env_file)
        if name:
            config.write_mdns_name(service.instance_name, env_file)
    return base


def request_json(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """GET/POST JSON from the listener. ``path`` may include a query string."""
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    from .reach import open_url

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            detail = str(payload.get("error") or payload.get("message") or payload)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, AttributeError):
            detail = str(exc.reason or "")
        raise SystemExit(
            f"listener returned HTTP {exc.code}" + (f": {detail}" if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"cannot reach {url} ({exc.reason}). "
            "Is `tesmartctl listen --http` running on that host?"
        ) from exc
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"listener returned non-JSON from {url}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"listener returned unexpected payload from {url}")
    return payload


def discover_services(
    *,
    name: str | None = None,
    timeout: float = DEFAULT_BROWSE_TIMEOUT,
) -> list[DiscoveredListener]:
    from .mdns import pick_listener

    services = browse(timeout=timeout)
    if name:
        return [pick_listener(services, name=name)]
    return services


def run_via_command(args: Any, base: str) -> int:
    """Dispatch a supported CLI command against ``base``."""
    command = args.command
    if command == "status":
        return _via_status(args, base)
    if command == "input":
        return _via_input(args, base)
    if command in {"rotate", "next", "previous"}:
        return _via_rotate(args, base)
    if command == "peek":
        return _via_peek(args, base)
    raise SystemExit(f"--via does not support `{command}` yet")


def _emit(args: Any, data: dict[str, Any], text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
    else:
        print(text)


def _named(number: object, name: object) -> str:
    if number is None:
        return "unknown (no reply)"
    return f"{number} ({name})" if name else str(number)


def _render_via_status(status: dict[str, Any], base: str) -> str:
    lines = [f"Endpoint:      {status.get('endpoint') or base} (via listener)"]
    lines.append(
        f"Active input:  {_named(status.get('active_input'), status.get('active_name'))}"
        + (f" of {status['input_count']}" if status.get("input_count") is not None else "")
    )
    names = status.get("names") or {}
    if names:
        lines.append("Input names (from listener):")
        for number, label in names.items():
            lines.append(f"  {number:<2} {label}")
    network = status.get("network")
    if network:
        lines.append("Network (persisted; applied after power cycle):")
        lines.append(f"  IP:          {network.get('ip') or 'n/a'}")
        lines.append(f"  Port:        {network.get('port') or 'n/a'}")
        lines.append(f"  Gateway:     {network.get('gateway') or 'n/a'}")
        lines.append(f"  Mask:        {network.get('mask') or 'n/a'}")
    rotate = status.get("rotate") or {}
    if rotate:
        cycle = rotate.get("cycle") or []
        cycle_text = ", ".join(str(number) for number in cycle) if cycle else "none"
        chosen = "chosen inputs" if rotate.get("configured") else "all inputs"
        if rotate.get("mode") == "automatic":
            lines.append(
                f"Cycle:         automatic every {rotate.get('seconds')}s; {cycle_text} ({chosen})"
            )
        else:
            lines.append(f"Cycle:         manual (next/previous); {cycle_text} ({chosen})")
    peek = status.get("peek") or {}
    if peek:
        lines.append(f"Peek:          {peek.get('seconds')}s on another input, then back")
    if status.get("version"):
        lines.append(f"Listener:      tesmartctl {status.get('version')}")
    return "\n".join(lines)


def _via_status(args: Any, base: str) -> int:
    query = "?network=0" if getattr(args, "no_network", False) else ""
    payload = request_json(base, f"/status{query}", timeout=max(float(args.timeout), 10.0))
    if "endpoint" not in payload:
        payload = {**payload, "endpoint": base}
    _emit(args, payload, _render_via_status(payload, base))
    return 0


def _via_input(args: Any, base: str) -> int:
    timeout = max(float(args.timeout), 10.0)
    if args.number is None:
        if args.only is not None:
            print("error: --only applies to 'input next' or 'rotate'", file=sys.stderr)
            return 2
        payload = request_json(base, "/input", timeout=timeout)
        _emit(
            args,
            payload,
            f"Active input: {_named(payload.get('active_input'), payload.get('active_name'))}",
        )
        return 0
    if args.number.lower() == "next":
        return _via_rotate(args, base, verb="rotate")
    if args.only is not None:
        print("error: --only applies to 'input next' or 'rotate'", file=sys.stderr)
        return 2
    if getattr(args, "no_verify", False):
        print(
            "warning: --no-verify is ignored with --via (the listener always reads back)",
            file=sys.stderr,
        )
    path = f"/input/{quote(str(args.number), safe='')}"
    payload = request_json(base, path, timeout=timeout)
    requested = payload.get("requested", args.number)
    reported = payload.get("active_input")
    label_req = _named(requested, payload.get("requested_name"))
    if reported is None:
        _emit(args, payload, f"Sent switch to input {label_req} (not verified)")
        return 0
    if str(reported) == str(requested) or reported == requested:
        _emit(args, payload, f"Active input: {_named(reported, payload.get('active_name'))}")
        return 0
    _emit(
        args,
        payload,
        f"Requested input {label_req} but switch reports {_named(reported, payload.get('active_name'))}",
    )
    return 1


def _via_rotate(args: Any, base: str, *, verb: str | None = None) -> int:
    timeout = max(float(args.timeout), 10.0)
    if getattr(args, "clear", False) or getattr(args, "save", False):
        print(
            "error: --save / --clear change the listener host's cycle list; "
            "run them without --via on that host (or use the panel)",
            file=sys.stderr,
        )
        return 2
    command = verb or args.command
    if command == "next":
        path = "/next"
    elif command == "previous":
        path = "/previous"
    else:
        path = "/rotate"
    only = getattr(args, "only", None)
    if only:
        path = f"{path}?only={urllib.parse.quote(','.join(str(n) for n in only))}"
    payload = request_json(base, path, timeout=timeout)
    previous = payload.get("previous")
    reported = payload.get("active_input")
    current = _named(reported, payload.get("active_name"))
    if reported == previous:
        _emit(args, payload, f"Active input: {current}")
    else:
        _emit(
            args,
            payload,
            f"Active input: {current} (was {_named(previous, payload.get('previous_name'))})",
        )
    return 0 if reported is not None else 1


def _via_peek(args: Any, base: str) -> int:
    if getattr(args, "save", False):
        if args.seconds is None:
            print("error: --save needs --seconds, for example --seconds 8 --save", file=sys.stderr)
            return 2
        config.set_peek_seconds(args.seconds, args.env_file)
    seconds = args.seconds if args.seconds is not None else config.read_peek_seconds(args.env_file)
    path = f"/peek/{quote(str(args.number), safe='')}"
    if args.seconds is not None:
        path = f"{path}?seconds={int(seconds)}"
    # Server holds the request until the peek finishes.
    timeout = max(float(args.timeout), float(seconds) + 10.0)
    if not args.json:
        print(
            f"Peeking via listener for {seconds}s (Ctrl-C will not cancel the remote peek)…",
            flush=True,
        )
    payload = request_json(base, path, timeout=timeout)
    current = _named(payload.get("active_input"), payload.get("active_name"))
    if payload.get("interrupted"):
        _emit(args, payload, f"Input was changed to {current} during the peek; left as is")
        return 1
    _emit(args, payload, f"Active input: {current}")
    return 0
