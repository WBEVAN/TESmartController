"""Browser control panel served by ``tesmartctl listen --http PORT --panel``.

Off unless ``--panel`` is given. It adds three things to the HTTP listener:

* ``/panel`` – the browser-only control panel (Bootstrap) that replaces the vendor's
  Windows controller: input buttons, next/previous, buzzer, front-panel
  display timeout, auto input detection, LAN settings, local input names,
  and the local cycle.
* ``/panel/api`` – API explorer in its own page (opened from the panel as a
  browser pop-out so the control surface stays usable underneath).
* ``/kit`` – the floating UI application kit (does not replace ``/panel``).
  **Pop out** opens a chrome-less desktop window on the listener host
  (Chromium ``--app`` or optional ``pywebview``); ``tesmartctl kit`` does the same.
* ``/static/...`` – panel and kit assets (including ``/static/kit/...``).
* ``/api/...`` – JSON endpoints the panel and kit call. They expose every
  protocol feature, including the write-only settings and the LAN address,
  which the plain listener routes deliberately leave out.

The switch cannot read back buzzer, display timeout, or auto-detect. The
panel shows the last value *sent through this server* and says so.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from importlib import resources
from typing import TYPE_CHECKING, Any
import re
import time
from urllib.parse import parse_qs, urlsplit

from . import callbacks, config, kit_window, notices, version as pkg_version
from .events import stream_sse
from .peek import apply_follow
from .protocol import LedTimeout
from .switch import SwitchError, next_input, previous_input
from .transport import TransportError

if TYPE_CHECKING:  # pragma: no cover
    from .web import _Handler

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json",
    ".md": "text/markdown; charset=utf-8",
}

# ``blocked`` routes are listed but not sent from the panel's API dialog.
# Changing the address stays on the two-warning flow.
# ``group`` organizes the panel API explorer. ``stream`` opens as an SSE listener.
API_CATALOG: list[dict[str, Any]] = [
    {"group": "Catalog", "method": "GET", "path": "/api", "summary": "this list"},
    {"group": "Catalog", "method": "GET", "path": "/api/version", "summary": "tesmartctl package version and feature tags"},
    {
        "group": "Catalog",
        "method": "GET",
        "path": "/api/compat",
        "summary": "compatibility check via query (?min_server=&features=)",
    },
    {
        "group": "Catalog",
        "method": "POST",
        "path": "/api/compat",
        "summary": "compatibility check",
        "sample": {"min_server": "0.0.7.9", "client": "hubitat", "features": ["callbacks"]},
    },
    {
        "group": "State",
        "method": "GET",
        "path": "/api/state",
        "summary": "active input, names, cycle, LAN settings, last-sent values, version",
    },
    {
        "group": "State",
        "method": "GET",
        "path": "/api/state?network=0",
        "summary": "same, without the four LAN queries",
    },
    {
        "group": "Live events",
        "method": "GET",
        "path": "/api/events",
        "summary": "Server-Sent Events stream (hello, input_changed, names_changed, enabled_changed, banner, peek, peek_follow, shutdown)",
        "stream": True,
    },
    {
        "group": "Inputs",
        "method": "POST",
        "path": "/api/input",
        "summary": "select an input by number or local name",
        "sample": {"input": 3},
    },
    {"group": "Inputs", "method": "POST", "path": "/api/next", "summary": "next input in the saved cycle", "sample": {}},
    {
        "group": "Inputs",
        "method": "POST",
        "path": "/api/previous",
        "summary": "previous input in the saved cycle",
        "sample": {},
    },
    {
        "group": "Inputs",
        "method": "POST",
        "path": "/api/names",
        "summary": "set local names; an empty string removes one",
        "sample": {"names": {"1": "Office", "2": ""}},
    },
    {
        "group": "Inputs",
        "method": "POST",
        "path": "/api/enabled",
        "summary": "enable or disable an input for selection, peek, and next/previous",
        "sample": {"input": 4, "enabled": False},
    },
    {
        "group": "Peek",
        "method": "POST",
        "path": "/api/peek",
        "summary": "show an input briefly, then return. {\"cancel\": true} comes back now",
        "sample": {"input": 3, "seconds": 5},
    },
    {
        "group": "Peek",
        "method": "POST",
        "path": "/api/peek-default",
        "summary": "save the default peek duration",
        "sample": {"seconds": 8},
    },
    {
        "group": "Peek",
        "method": "POST",
        "path": "/api/peek-follow",
        "summary": "once: the next plain input change peeks. always: every one does, until off",
        "sample": {"mode": "once"},
    },
    {
        "group": "Cycle",
        "method": "POST",
        "path": "/api/cycle",
        "summary": "save the cycle list, mode, and interval",
        "sample": {"inputs": [1, 2, 4], "mode": "automatic", "seconds": 15},
    },
    {
        "group": "Cycle",
        "method": "POST",
        "path": "/api/cycle/start",
        "summary": "start the listener-owned automatic cycle (mode must be automatic)",
        "sample": {},
    },
    {
        "group": "Cycle",
        "method": "POST",
        "path": "/api/cycle/stop",
        "summary": "stop the listener-owned automatic cycle",
        "sample": {},
    },
    {
        "group": "Banner",
        "method": "GET",
        "path": "/api/banner",
        "summary": "last LCD message envelope plus banner_history (up to 10, newest first)",
    },
    {
        "group": "Banner",
        "method": "POST",
        "path": "/api/banner",
        "summary": "send LCD message (timed seconds or sticky); {\"show\":true} reshows last; {\"dismiss\":true} hides; {\"clear\":true} erases history too",
        "sample": {"text": "Door unlocked", "seconds": 8, "from": "Hubitat"},
    },
    {
        "group": "Switch",
        "method": "POST",
        "path": "/api/buzzer",
        "summary": "buzzer on or off (write-only)",
        "sample": {"on": True},
    },
    {
        "group": "Switch",
        "method": "POST",
        "path": "/api/led",
        "summary": "front-panel display timeout: never, 10, or 30",
        "sample": {"timeout": "30"},
    },
    {
        "group": "Switch",
        "method": "POST",
        "path": "/api/autodetect",
        "summary": "auto input detection on or off (write-only)",
        "sample": {"on": True},
    },
    {
        "group": "Network",
        "method": "GET",
        "path": "/api/network",
        "summary": "IP, port, gateway, and mask stored in the switch",
    },
    {
        "group": "Network",
        "method": "GET",
        "path": "/api/network/notice",
        "summary": "the risk and liability wording shown before an address change",
    },
    {
        "group": "Network",
        "method": "POST",
        "path": "/api/network",
        "summary": "write the address. Not sent from this dialog; use Change address, which warns twice and reads the values back",
        "blocked": True,
    },
    {
        "group": "Callbacks",
        "method": "GET",
        "path": "/api/callbacks",
        "summary": "list callbacks (url, optional name, last delivery) and POST timeout",
    },
    {
        "group": "Callbacks",
        "method": "POST",
        "path": "/api/callbacks",
        "summary": "register a callback URL with optional name",
        "sample": {"url": "http://192.168.1.5/hook", "name": "Hubitat"},
    },
    {
        "group": "Callbacks",
        "method": "POST",
        "path": "/api/callbacks",
        "summary": "set callback POST timeout (default 10s; not on the panel UI)",
        "sample": {"timeout": 10},
    },
    {
        "group": "Callbacks",
        "method": "POST",
        "path": "/api/callbacks/timeout",
        "summary": "same as POST /api/callbacks with timeout only",
        "sample": {"timeout": 10},
    },
    {
        "group": "Callbacks",
        "method": "GET",
        "path": "/api/callbacks/health",
        "summary": "probe each callback with a health_check event",
    },
    {
        "group": "Callbacks",
        "method": "POST",
        "path": "/api/callbacks/health",
        "summary": "same as GET /api/callbacks/health",
    },
    {
        "group": "Callbacks",
        "method": "DELETE",
        "path": "/api/callbacks",
        "summary": "remove by url and/or name",
        "sample": {"url": "http://192.168.1.5/hook"},
    },
    {
        "group": "Callbacks",
        "method": "POST",
        "path": "/api/callbacks/remove",
        "summary": "same as DELETE /api/callbacks (for clients that cannot send DELETE)",
        "sample": {"url": "http://192.168.1.5/hook"},
    },
    {
        "group": "Kit",
        "method": "POST",
        "path": "/api/kit-window",
        "summary": "open /kit in a chrome-less desktop window on the listener host (Chrome/Edge --app, or pywebview)",
        "sample": {"prefer": "auto"},
    },
]


def _routes() -> dict[str, str]:
    listed: dict[str, str] = {}
    for item in API_CATALOG:
        detail = item["summary"]
        if "sample" in item:
            detail = f"{detail}. Body: {json.dumps(item['sample'])}"
        listed[f"{item['method']} {item['path']}"] = detail
    return listed


class PanelError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


# --- helpers -----------------------------------------------------------------


def _read_json(handler: "_Handler") -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PanelError(f"request body must be JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PanelError("request body must be a JSON object")
    return data


def _as_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"on", "1", "true", "yes", "unmute", "enable"}:
        return True
    if isinstance(value, str) and value.lower() in {"off", "0", "false", "no", "mute", "disable"}:
        return False
    raise PanelError(f"{field} must be true or false")


def _static_body(name: str) -> bytes:
    """Load a file under ``tesmart/static/``. ``name`` may include subdirs (e.g. ``kit/styles.css``)."""
    if not name or name.startswith("/") or name.startswith(".") or ".." in name.split("/"):
        raise PanelError("not found", HTTPStatus.NOT_FOUND)
    parts = [part for part in name.split("/") if part]
    if not parts or any(part.startswith(".") for part in parts):
        raise PanelError("not found", HTTPStatus.NOT_FOUND)
    try:
        return resources.files("tesmart").joinpath("static", *parts).read_bytes()
    except (FileNotFoundError, OSError, IsADirectoryError, NotADirectoryError) as exc:
        raise PanelError("not found", HTTPStatus.NOT_FOUND) from exc


def _asset_cache_token(query: str) -> str:
    """Version token baked into ``/static/...`` URLs so clients re-fetch after upgrades.

    ``?fresh=1`` adds a one-shot nonce so a reload bypasses a stale disk cache
    without waiting for the package version to change.
    """
    token = pkg_version.package_version()
    qs = parse_qs(query or "", keep_blank_values=True)
    if "fresh" in qs:
        token = f"{token}-f{int(time.time())}"
    return token


def _with_static_cache_bust(html: str, token: str) -> str:
    """Append ``?v=<token>`` (or ``&v=``) to every ``/static/...`` href/src."""

    def _bust(match: re.Match[str]) -> str:
        attr, url = match.group(1), match.group(2)
        sep = "&" if "?" in url else "?"
        return f'{attr}="{url}{sep}v={token}"'

    return re.sub(r'\b(href|src)="(/static/[^"]+)"', _bust, html)


def _kit_html(query: str) -> bytes:
    """Serve ``/kit`` with shell classes already on ``<body>`` for ``?app=1`` / ``?popup=1``.

    ``tesmartctl kit`` must never flash Pop out / Full panel. Relying only on a
    late JS class flip regressed that; stamp the classes into the HTML instead.
    Static asset URLs are version-stamped so the client can cache them until the
    server version changes (or ``?fresh=1``).
    """
    html = _static_body("kit/index.html").decode("utf-8")
    qs = parse_qs(query or "", keep_blank_values=True)
    classes = ["kit-float"]
    shell = None
    if "app" in qs:
        classes.extend(["is-app", "is-popup"])
        shell = "app"
    elif "popup" in qs:
        classes.append("is-popup")
        shell = "popup"
    if shell == "app" and "transparent" in qs:
        # Only the native pywebview window is truly see-through; Chromium
        # --app would paint the cleared page white.
        classes.append("is-transparent")
    # Keep any existing data-* on <body> (themes); only replace the class list.
    html = re.sub(
        r"<body\s+class=\"kit-float\"([^>]*)>",
        f'<body class="{" ".join(classes)}"\\1>',
        html,
        count=1,
    )
    token = _asset_cache_token(query)
    head_bits = [
        f"document.documentElement.dataset.kitVersion={token!r};",
    ]
    if shell:
        head_bits.append(f"document.documentElement.dataset.kitShell={shell!r};")
    html = html.replace(
        "<head>",
        f"<head><script>{''.join(head_bits)}</script>",
        1,
    )
    html = _with_static_cache_bust(html, token)
    return html.encode("utf-8")


def _content_type(name: str) -> str:
    lower = name.lower()
    for suffix, content_type in STATIC_TYPES.items():
        if lower.endswith(suffix):
            return content_type
    return "application/octet-stream"


# --- state -------------------------------------------------------------------


def build_state(handler: "_Handler", include_network: bool) -> dict[str, Any]:
    server = handler.server
    switch = server.switch
    names = config.read_names(server.env_file)
    with server.lock:
        status = switch.read_status(include_network=include_network).to_dict()
    config.apply_names(status, names, include_map=True)
    if getattr(switch, "simulated", False):
        status["simulated"] = True
    status["rotate"] = config.rotate_info(server.env_file, input_count=switch.input_count)
    status["rotate"]["active"] = server.cycler.status()
    status["peek"] = config.peek_info(server.env_file)
    status["peek"]["active"] = server.peeker.status()
    status["peek"]["follow"] = server.peek_follow.status()["mode"]
    # Prefer the live in-memory copy (updated on each write); fall back to env file.
    status["last_sent"] = dict(server.last_sent) if server.last_sent else config.read_last_sent(server.env_file)
    disabled = config.read_disabled(server.env_file)
    status["disabled"] = sorted(number for number in disabled if 1 <= number <= switch.input_count)
    status["inputs"] = [
        {
            "number": number,
            "name": names.get(number),
            "enabled": number not in disabled,
        }
        for number in range(1, switch.input_count + 1)
    ]
    status["callbacks"] = callbacks.list_callbacks(server.env_file).get("callbacks", [])
    banner_payload = server.banner.payload()
    status["banner"] = banner_payload["banner"]
    status["banner_history"] = banner_payload["banner_history"]
    return status


def _input_payload(handler: "_Handler", payload: dict[str, Any]) -> dict[str, Any]:
    names = config.read_names(handler.server.env_file)
    return config.apply_names(payload, names)


def _note_input_change(handler: "_Handler", payload: dict[str, Any]) -> None:
    hub = getattr(handler.server, "callbacks", None)
    if hub is None:
        return
    active = payload.get("active_input")
    previous = payload.get("previous")
    try:
        hub.note_active(
            int(active) if active is not None else None,
            previous=int(previous) if previous is not None else None,
            source="command",
        )
    except (TypeError, ValueError):
        return


# --- actions -----------------------------------------------------------------


def _follow_or_stick(handler: "_Handler", number: int, previous: int | None = None) -> dict[str, Any] | None:
    """Peek when follow mode is armed. ``None`` means the caller should stick."""
    server = handler.server
    followed = apply_follow(
        server.peek_follow,
        server.peeker,
        number,
        config.read_peek_seconds(server.env_file),
        block=False,
    )
    if followed is None:
        return None
    if previous is not None:
        followed["previous"] = previous
    return _input_payload(handler, followed)


def _action_input(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    server = handler.server
    raw = body.get("input")
    if raw is None:
        raise PanelError('"input" is required')
    names = config.read_names(server.env_file)
    number = config.resolve_named_input(str(raw), input_count=server.switch.input_count, names=names)
    config.require_enabled(number, server.env_file)
    followed = _follow_or_stick(handler, number)
    if followed is not None:
        _note_input_change(handler, followed)
        return followed
    with server.lock:
        previous = None
        try:
            previous = server.switch.get_active_input()
        except SwitchError:
            previous = None
        reported = server.switch.set_input(number, verify=True)
    payload = _input_payload(handler, {"previous": previous, "requested": number, "active_input": reported})
    _note_input_change(handler, payload)
    return payload


def _action_step(handler: "_Handler", direction: int) -> dict[str, Any]:
    server = handler.server
    only = config.enabled_cycle(server.switch.input_count, config.read_rotate(server.env_file), server.env_file)
    with server.lock:
        current = server.switch.get_active_input()
        step = previous_input if direction < 0 else next_input
        target = step(current, server.switch.input_count, only)
    followed = _follow_or_stick(handler, target, previous=current)
    if followed is not None:
        _note_input_change(handler, followed)
        return followed
    with server.lock:
        reported = server.switch.set_input(target, verify=True)
    reported = reported if reported is not None else target
    payload = _input_payload(handler, {"previous": current, "requested": reported, "active_input": reported})
    _note_input_change(handler, payload)
    return payload


def _action_peek(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    """Start a peek and return at once; ``/api/state`` reports progress."""
    server = handler.server
    if _as_bool(body.get("cancel", False), "cancel"):
        return {"cancelled": server.peeker.cancel(), "note": "returning to the previous input"}
    raw = body.get("input")
    if raw is None:
        raise PanelError('"input" is required')
    names = config.read_names(server.env_file)
    number = config.resolve_named_input(str(raw), input_count=server.switch.input_count, names=names)
    config.require_enabled(number, server.env_file)
    seconds = body.get("seconds")
    if seconds is None:
        seconds = config.read_peek_seconds(server.env_file)
    elif not isinstance(seconds, int) or seconds < 1:
        raise PanelError('"seconds" must be a whole number of seconds, 1 or more')
    started = server.peeker.start(number, seconds)
    return _input_payload(handler, {"peek": started, "active_input": started["peeked"] if started else number})


def _action_peek_follow(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    mode = body.get("mode")
    if not isinstance(mode, str):
        raise PanelError('"mode" must be once, always, or off')
    seconds = body.get("seconds")
    if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1):
        raise PanelError('"seconds" must be a whole number of seconds, 1 or more')
    state = handler.server.peek_follow.set_mode(mode, seconds)
    # Duration is shared even when follow is off (set_mode drops seconds for off).
    if isinstance(seconds, int) and seconds >= 1:
        config.set_peek_seconds(seconds, handler.server.env_file)
        state = {**state, "seconds": seconds}
    _publish_peek_follow(handler.server, state)
    return {"peek_follow": state["mode"], "seconds": state.get("seconds")}


def _publish_peek_follow(server: Any, state: dict[str, Any]) -> None:
    """Tell open kit/panel pages the peek-follow mode changed (SSE)."""
    hub = getattr(server, "events", None)
    if hub is None:
        return
    mode = str(state.get("mode") or "off")
    payload: dict[str, Any] = {"event": "peek_follow", "mode": mode, "peek_follow": mode}
    if state.get("seconds") is not None:
        payload["seconds"] = state["seconds"]
    hub.publish(payload)


def _action_peek_default(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    seconds = body.get("seconds")
    if not isinstance(seconds, int) or seconds < 1:
        raise PanelError('"seconds" must be a whole number of seconds, 1 or more')
    config.set_peek_seconds(seconds, handler.server.env_file)
    return {"peek": config.peek_info(handler.server.env_file)}


def _remember_last_sent(handler: "_Handler", updates: dict[str, Any]) -> dict[str, Any]:
    """Persist write-only settings and keep the in-memory copy in sync."""
    table = config.update_last_sent(updates, handler.server.env_file)
    handler.server.last_sent = dict(table)
    return table


def _action_buzzer(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    on = _as_bool(body.get("on"), "on")
    with handler.server.lock:
        handler.server.switch.set_buzzer(on)
    _remember_last_sent(handler, {"buzzer": on})
    return {"buzzer": on, "note": "sent; remembered locally (switch cannot confirm)"}


def _action_led(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    try:
        timeout = LedTimeout.from_text(str(body.get("timeout", "")))
    except ValueError as exc:
        raise PanelError(str(exc)) from exc
    with handler.server.lock:
        handler.server.switch.set_led_timeout(timeout)
    described = timeout.describe()
    _remember_last_sent(handler, {"led_timeout": described})
    return {"led_timeout": described, "note": "sent; remembered locally (switch cannot confirm)"}


def _action_autodetect(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    on = _as_bool(body.get("on"), "on")
    with handler.server.lock:
        handler.server.switch.set_auto_detect(on)
    _remember_last_sent(handler, {"auto_detect": on})
    return {"auto_detect": on, "note": "sent; remembered locally (switch cannot confirm)"}


def _action_network_get(handler: "_Handler") -> dict[str, Any]:
    with handler.server.lock:
        return {"network": handler.server.switch.get_network_config().to_dict()}


def _network_notice() -> dict[str, Any]:
    return {
        "risks": list(notices.NETWORK_RISK_LINES),
        "liability": notices.NETWORK_LIABILITY,
        "after_write": notices.NETWORK_AFTER_WRITE,
    }


def _action_network_set(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    """Two separate acknowledgements are required, then the values are read back."""
    if not _as_bool(body.get("confirm", False), "confirm"):
        raise PanelError(
            'set "confirm": true to acknowledge that a wrong address makes the switch unreachable '
            "and that only RS232 can recover it"
        )
    if not _as_bool(body.get("accept_risk", False), "accept_risk"):
        raise PanelError(
            'set "accept_risk": true to accept that you do this at your own risk and that this '
            "software accepts no liability"
        )
    values = {
        key: str(body[key]).strip()
        for key in ("ip", "port", "gateway", "mask")
        if body.get(key) not in (None, "")
    }
    if not values:
        raise PanelError("nothing to set; include ip, port, gateway, or mask")
    with handler.server.lock:
        result = handler.server.switch.write_network_config(**values)
    payload = result.to_dict()
    payload["note"] = (
        notices.NETWORK_AFTER_WRITE
        if result.all_verified
        else "the read-back differs from the request; do not power-cycle until it reports what you expect"
    )
    return payload


def _action_names(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    table = body.get("names")
    if not isinstance(table, dict):
        raise PanelError('"names" must be an object of input number -> label')
    env = handler.server.env_file
    before = config.read_names(env)
    count = handler.server.switch.input_count
    for key, label in table.items():
        if not str(key).isdigit():
            raise PanelError(f"input number expected, got {key!r}")
        number = int(key)
        if label is None or str(label).strip() == "":
            config.unset_input_name(number, env)
        else:
            config.set_input_name(number, str(label), env, input_count=count)
    names = config.read_names(env)
    hub = getattr(handler.server, "callbacks", None)
    if hub is not None:
        hub.note_names(before, names, source="command")
    return {"names": {str(number): label for number, label in names.items()}}


def inputs_payload(server: Any) -> list[dict[str, Any]]:
    env = server.env_file
    names = config.read_names(env)
    disabled = config.read_disabled(env)
    count = server.switch.input_count
    return [
        {"number": number, "name": names.get(number), "enabled": number not in disabled}
        for number in range(1, count + 1)
    ]


def record_input_enabled(server: Any, number: int, enabled: bool) -> dict[str, Any]:
    """Store enablement on this listener and tell open clients."""
    config.set_input_enabled(
        number,
        enabled,
        server.env_file,
        input_count=server.switch.input_count,
    )
    hub = getattr(server, "callbacks", None)
    if hub is not None:
        hub.note_enabled(source="command")
    disabled = sorted(
        item for item in config.read_disabled(server.env_file)
        if 1 <= item <= server.switch.input_count
    )
    return {
        "input": number,
        "enabled": enabled,
        "disabled": disabled,
        "inputs": inputs_payload(server),
    }


def _inputs_payload(handler: "_Handler") -> list[dict[str, Any]]:
    return inputs_payload(handler.server)


def _action_enabled(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("input")
    if isinstance(raw, bool) or not isinstance(raw, int) and not (isinstance(raw, str) and raw.isdigit()):
        raise PanelError('"input" must be an input number')
    if not isinstance(body.get("enabled"), bool):
        raise PanelError('"enabled" must be true or false')
    number = int(raw)
    return record_input_enabled(handler.server, number, body["enabled"])


def _action_cycle(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    server = handler.server
    count = server.switch.input_count
    if "inputs" in body:
        inputs = body["inputs"]
        if not isinstance(inputs, list) or not all(isinstance(n, int) for n in inputs):
            raise PanelError('"inputs" must be a list of input numbers')
        if inputs and len(inputs) < len(range(1, count + 1)):
            config.set_rotate(tuple(inputs), server.env_file, input_count=count)
        else:
            config.clear_rotate(server.env_file)
    if "mode" in body:
        seconds = body.get("seconds")
        if seconds is not None and (not isinstance(seconds, int) or seconds < 1):
            raise PanelError('"seconds" must be a whole number of seconds, 1 or more')
        config.set_cycle_mode(str(body["mode"]), server.env_file, seconds=seconds)
    rotate = config.rotate_info(server.env_file, input_count=count)
    rotate["active"] = server.cycler.status()
    return {"rotate": rotate}


def _action_cycle_start(handler: "_Handler") -> dict[str, Any]:
    server = handler.server
    active = server.cycler.start()
    rotate = config.rotate_info(server.env_file, input_count=server.switch.input_count)
    rotate["active"] = active
    return {"rotate": rotate, "active": active, "active_input": active.get("active_input")}


def _action_cycle_stop(handler: "_Handler") -> dict[str, Any]:
    server = handler.server
    ended = server.cycler.stop()
    rotate = config.rotate_info(server.env_file, input_count=server.switch.input_count)
    rotate["active"] = server.cycler.status()
    ended["rotate"] = rotate
    return ended


# --- dispatcher --------------------------------------------------------------


def handle(handler: "_Handler", method: str, path: str) -> bool:
    """Serve panel, kit, static and API routes. Returns False if ``path`` is not ours."""
    parts = urlsplit(path)
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments or segments[0] not in {"panel", "kit", "static", "api"}:
        return False

    if not handler.server.panel:
        payload = pkg_version.not_supported(
            parts.path or "/panel",
            method=method,
            detail="the control panel is off; start the listener with --panel",
        )
        _send_json(handler, HTTPStatus.NOT_FOUND, payload)
        return True

    try:
        if segments[0] == "panel":
            # /panel — control surface; /panel/api — API explorer pop-out page.
            if len(segments) == 1:
                page = "panel.html"
            elif len(segments) == 2 and segments[1].lower() == "api":
                page = "api-explorer.html"
            else:
                raise PanelError("not supported", HTTPStatus.NOT_FOUND)
            # HTML stays uncached so asset ?v= tokens update after an upgrade.
            # Honor ?fresh=1 the same way /kit does, so a reload bypasses stale
            # immutable panel.js/css after UI changes without a version bump.
            body = _with_static_cache_bust(
                _static_body(page).decode("utf-8"),
                _asset_cache_token(parts.query),
            ).encode("utf-8")
            _send_bytes(handler, HTTPStatus.OK, body, STATIC_TYPES[".html"], cache="html")
            return True
        if segments[0] == "kit":
            if len(segments) != 1:
                raise PanelError("not supported", HTTPStatus.NOT_FOUND)
            _send_bytes(
                handler,
                HTTPStatus.OK,
                _kit_html(parts.query),
                STATIC_TYPES[".html"],
                cache="html",
            )
            return True
        if segments[0] == "static":
            if len(segments) < 2:
                raise PanelError("not supported", HTTPStatus.NOT_FOUND)
            name = "/".join(segments[1:])
            body = _static_body(name)
            _send_bytes(
                handler,
                HTTPStatus.OK,
                body,
                _content_type(name),
                cache="static",
                etag=f'"{pkg_version.package_version()}-{len(body)}"',
            )
            return True
        if segments[0] == "api" and len(segments) >= 2 and segments[1].lower() == "events":
            if method.upper() != "GET":
                raise PanelError("use GET /api/events for the Server-Sent Events stream", HTTPStatus.METHOD_NOT_ALLOWED)
            stream_sse(handler, handler.server.events)
            return True
        _send_json(handler, HTTPStatus.OK, _api(handler, method, segments[1:], parts.query))
    except PanelError as exc:
        if exc.status == HTTPStatus.NOT_FOUND or str(exc).lower().startswith("not supported"):
            payload = pkg_version.not_supported(parts.path or "/api", method=method, detail=str(exc))
            _send_json(handler, HTTPStatus.NOT_FOUND, payload)
        else:
            _send_json(handler, exc.status, {"error": str(exc)})
    except (config.ConfigError, SwitchError, ValueError) as exc:
        _send_json(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
    except TransportError as exc:
        _send_json(handler, HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
    return True


def _api(handler: "_Handler", method: str, segments: list[str], query_text: str) -> dict[str, Any]:
    if not segments:
        return {"routes": _routes(), "catalog": API_CATALOG, "version": pkg_version.package_version()}
    name = segments[0].lower()
    query = parse_qs(query_text, keep_blank_values=True)

    if name == "state" and method == "GET":
        include_network = query.get("network", ["1"])[0].lower() not in {"0", "off", "false", "no"}
        state = build_state(handler, include_network)
        state["version"] = pkg_version.package_version()
        state["mdns_name"] = getattr(handler.server, "mdns_name", None)
        return state
    if name == "version" and method == "GET":
        info = pkg_version.version_info()
        info["mdns_name"] = getattr(handler.server, "mdns_name", None)
        return info
    if name == "compat" and method in {"GET", "POST", "PUT"}:
        return _action_compat(handler, method, query)
    if name == "network" and method == "GET":
        if len(segments) == 2 and segments[1].lower() == "notice":
            return _network_notice()
        return _action_network_get(handler)
    if name == "callbacks":
        return _action_callbacks(handler, method, segments[1:])
    if name == "banner":
        return _action_banner(handler, method)
    if name == "cycle" and len(segments) == 2:
        action = segments[1].lower()
        if method != "POST":
            raise PanelError("use POST for this route", HTTPStatus.METHOD_NOT_ALLOWED)
        if action == "start":
            return _action_cycle_start(handler)
        if action == "stop":
            return _action_cycle_stop(handler)
        raise PanelError("not supported", HTTPStatus.NOT_FOUND)

    writable = {
        "input", "next", "previous", "peek", "peek-default", "peek-follow",
        "buzzer", "led", "autodetect", "network", "names", "enabled", "cycle", "kit-window",
    }
    if name not in writable:
        raise PanelError("not supported", HTTPStatus.NOT_FOUND)
    if method != "POST":
        raise PanelError("use POST for this route", HTTPStatus.METHOD_NOT_ALLOWED)
    body = _read_json(handler)
    actions = {
        "input": lambda: _action_input(handler, body),
        "next": lambda: _action_step(handler, 1),
        "previous": lambda: _action_step(handler, -1),
        "peek": lambda: _action_peek(handler, body),
        "peek-default": lambda: _action_peek_default(handler, body),
        "peek-follow": lambda: _action_peek_follow(handler, body),
        "buzzer": lambda: _action_buzzer(handler, body),
        "led": lambda: _action_led(handler, body),
        "autodetect": lambda: _action_autodetect(handler, body),
        "network": lambda: _action_network_set(handler, body),
        "names": lambda: _action_names(handler, body),
        "enabled": lambda: _action_enabled(handler, body),
        "cycle": lambda: _action_cycle(handler, body),
        "kit-window": lambda: _action_kit_window(handler, body),
    }
    return actions[name]()


def _banner_response(board: Any, **extra: Any) -> dict[str, Any]:
    payload = board.payload()
    payload.update(extra)
    return payload


def _notify_banner(hub: Any, board: Any, *, cleared: bool = False) -> None:
    if hub is None:
        return
    if cleared:
        hub.note_banner(None, source="command", history=[])
        return
    snap = board.payload()
    hub.note_banner(
        snap.get("banner"),
        source="command",
        history=list(snap.get("banner_history") or []),
    )


def _action_banner(handler: "_Handler", method: str) -> dict[str, Any]:
    """Show, dismiss, reshow, or read the LCD banner shared by kit and panel."""
    board = handler.server.banner
    hub = getattr(handler.server, "callbacks", None)
    method = method.upper()
    if method == "GET":
        return _banner_response(board)
    if method == "DELETE":
        previous = board.dismiss()
        _notify_banner(hub, board)
        return _banner_response(board, dismissed=previous)
    if method != "POST":
        raise PanelError("use GET, POST, or DELETE /api/banner", HTTPStatus.METHOD_NOT_ALLOWED)
    body = _read_json(handler)
    if body.get("clear") is True:
        previous = board.clear()
        _notify_banner(hub, board, cleared=True)
        return {"banner": None, "banner_history": [], "cleared": previous}
    if body.get("dismiss") is True:
        previous = board.dismiss()
        _notify_banner(hub, board)
        return _banner_response(board, dismissed=previous)
    if body.get("show") is True:
        try:
            board.reshow()
        except ValueError as exc:
            raise PanelError(str(exc)) from exc
        _notify_banner(hub, board)
        return _banner_response(board)
    try:
        board.show(
            body.get("text"),
            body.get("seconds"),
            sticky=body.get("sticky"),
            from_=body.get("from"),
        )
    except ValueError as exc:
        raise PanelError(str(exc)) from exc
    _notify_banner(hub, board)
    return _banner_response(board)


def _action_kit_window(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    """Open /kit without browser chrome on the machine running tesmartctl."""
    prefer = str(body.get("prefer") or "auto")
    try:
        width = int(body.get("width") or kit_window.DEFAULT_WIDTH)
        height = int(body.get("height") or kit_window.DEFAULT_HEIGHT)
    except (TypeError, ValueError) as exc:
        raise PanelError("width and height must be integers") from exc
    if width < 320 or height < 320:
        raise PanelError("width and height must be at least 320")
    host = handler.headers.get("Host") or "127.0.0.1"
    # Prefer loopback on the listener host so the app window hits this process.
    server = handler.server
    bound_host, bound_port = server.server_address[:2]
    if bound_host in {"0.0.0.0", "::", ""}:
        base = f"http://127.0.0.1:{bound_port}"
    else:
        base = f"http://{bound_host}:{bound_port}"
    raw_url = body.get("url")
    if raw_url:
        url = str(raw_url).strip()
        if not url.startswith(("http://", "https://")):
            raise PanelError("url must be an absolute http(s) address")
    else:
        url = f"{base}/kit?app=1"
    try:
        result = kit_window.open_kit_window(url, width=width, height=height, prefer=prefer)
    except kit_window.KitWindowError as exc:
        raise PanelError(str(exc)) from exc
    result["host_header"] = host
    return result


def _action_compat(handler: "_Handler", method: str, query: dict[str, list[str]]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if method.upper() in {"POST", "PUT"}:
        body = _read_json(handler)

    def _pick(*keys: str) -> object | None:
        for key in keys:
            if key in body and body[key] not in (None, ""):
                return body[key]
            if key in query and query[key]:
                return query[key][0]
        return None

    min_server = _pick("min_server", "min", "requires", "require")
    client = _pick("client")
    client_version = _pick("client_version", "clientVersion")
    features_raw = _pick("features", "feature")
    features: list[str] | None = None
    if isinstance(features_raw, list):
        features = [str(item) for item in features_raw]
    elif isinstance(features_raw, str) and features_raw.strip():
        features = [part.strip() for part in features_raw.split(",") if part.strip()]
    return pkg_version.check_compat(
        min_server=str(min_server) if min_server is not None else None,
        features=features,
        client=str(client) if client is not None else None,
        client_version=str(client_version) if client_version is not None else None,
    )


def _action_callbacks(handler: "_Handler", method: str, rest: list[str]) -> dict[str, Any]:
    env = handler.server.env_file
    if rest and rest[0].lower() == "health":
        if method not in {"GET", "POST"}:
            raise PanelError("use GET or POST /api/callbacks/health")
        return callbacks.health_check(env)
    if rest and rest[0].lower() == "timeout":
        if method == "GET":
            return callbacks.list_callbacks(env)
        if method not in {"POST", "PUT"}:
            raise PanelError('use GET or POST /api/callbacks/timeout with {"timeout": 10}')
        body = _read_json(handler)
        if "timeout" not in body:
            raise PanelError('provide {"timeout": 10} (seconds)')
        return callbacks.set_timeout(body["timeout"], env)
    if not rest and method == "GET":
        return callbacks.list_callbacks(env)
    if not rest and method == "POST":
        body = _read_json(handler)
        url = body.get("url") or body.get("callback")
        name = body.get("name") or body.get("label")
        action = str(body.get("action") or "add").lower()
        if action in {"health", "ping", "check"}:
            return callbacks.health_check(env)
        if "timeout" in body and not url:
            return callbacks.set_timeout(body["timeout"], env)
        if not url:
            raise PanelError('provide {"url": "http://..."} or {"timeout": 10}')
        result = callbacks.add_callback(str(url), env, name=str(name) if name else None)
        if "timeout" in body:
            timed = callbacks.set_timeout(body["timeout"], env)
            result["timeout"] = timed["timeout"]
        return result
    if (not rest and method == "DELETE") or (rest and rest[0].lower() == "remove" and method in {"POST", "DELETE"}):
        body = _read_json(handler)
        url = body.get("url") or body.get("callback")
        name = body.get("name") or body.get("label")
        if not url and not name:
            # Allow ?url= / ?name= when the explorer or client cannot send a DELETE body.
            query = parse_qs(urlsplit(handler.path).query)
            url = (query.get("url") or [None])[0]
            name = (query.get("name") or [None])[0]
        if not url and not name:
            raise PanelError('provide {"url": "http://..."} or {"name": "..."}')
        return callbacks.remove_callback(
            str(url) if url else None,
            env,
            name=str(name) if name else None,
        )
    raise PanelError("unknown callbacks route", HTTPStatus.NOT_FOUND)


# --- responses ---------------------------------------------------------------


def _send_bytes(
    handler: "_Handler",
    status: HTTPStatus,
    body: bytes,
    content_type: str,
    *,
    cache: str = "none",
    etag: str | None = None,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    if cache == "static":
        # Assets are addressed with ?v=<package version> (and a fresh nonce).
        # Safe to keep for a week; a version bump changes the URL.
        handler.send_header("Cache-Control", "public, max-age=604800, immutable")
        if etag:
            handler.send_header("ETag", etag)
    elif cache == "html":
        # Always revalidate the shell so new asset tokens are picked up.
        handler.send_header("Cache-Control", "no-cache")
    else:
        handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_json(handler: "_Handler", status: HTTPStatus, payload: dict[str, Any]) -> None:
    _send_bytes(handler, status, (json.dumps(payload) + "\n").encode("utf-8"), "application/json")
