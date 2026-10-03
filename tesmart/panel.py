"""Browser control panel served by ``tesmartctl listen --http PORT --panel``.

Off unless ``--panel`` is given. It adds three things to the HTTP listener:

* ``/panel`` – a single-page UI (Bootstrap) that replaces the vendor's
  Windows controller: input buttons, next/previous, buzzer, front-panel
  display timeout, auto input detection, LAN settings, local input names,
  and the local cycle.
* ``/static/...`` – the panel's HTML, CSS and JS, read from this package.
* ``/api/...`` – JSON endpoints the panel calls. They expose every protocol
  feature, including the write-only settings and the LAN address, which the
  plain listener routes deliberately leave out.

The switch cannot read back buzzer, display timeout, or auto-detect. The
panel shows the last value *sent through this server* and says so.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from importlib import resources
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from . import config, notices
from .protocol import LedTimeout
from .switch import SwitchError
from .transport import TransportError

if TYPE_CHECKING:  # pragma: no cover
    from .web import _Handler

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

# ``blocked`` routes are listed but not sent from the panel's API dialog.
# Changing the address stays on the two-warning flow.
API_CATALOG: list[dict[str, Any]] = [
    {"method": "GET", "path": "/api", "summary": "this list"},
    {"method": "GET", "path": "/api/state", "summary": "active input, names, cycle, LAN settings, last-sent values"},
    {"method": "GET", "path": "/api/state?network=0", "summary": "same, without the four LAN queries"},
    {"method": "POST", "path": "/api/input", "summary": "select an input by number or local name", "sample": {"input": 3}},
    {"method": "POST", "path": "/api/next", "summary": "next input in the saved cycle", "sample": {}},
    {"method": "POST", "path": "/api/previous", "summary": "previous input in the saved cycle", "sample": {}},
    {"method": "POST", "path": "/api/peek", "summary": "show an input briefly, then return. {\"cancel\": true} comes back now", "sample": {"input": 3, "seconds": 5}},
    {"method": "POST", "path": "/api/peek-default", "summary": "save the default peek duration", "sample": {"seconds": 8}},
    {"method": "POST", "path": "/api/buzzer", "summary": "buzzer on or off (write-only)", "sample": {"on": True}},
    {"method": "POST", "path": "/api/led", "summary": "front-panel display timeout: never, 10, or 30", "sample": {"timeout": "30"}},
    {"method": "POST", "path": "/api/autodetect", "summary": "auto input detection on or off (write-only)", "sample": {"on": True}},
    {"method": "GET", "path": "/api/network", "summary": "IP, port, gateway, and mask stored in the switch"},
    {"method": "GET", "path": "/api/network/notice", "summary": "the risk and liability wording shown before an address change"},
    {
        "method": "POST",
        "path": "/api/network",
        "summary": "write the address. Not sent from this dialog; use Change address, which warns twice and reads the values back",
        "blocked": True,
    },
    {"method": "POST", "path": "/api/names", "summary": "set local names; an empty string removes one", "sample": {"names": {"1": "Office", "2": ""}}},
    {"method": "POST", "path": "/api/cycle", "summary": "save the cycle list, mode, and interval", "sample": {"inputs": [1, 2, 4], "mode": "manual", "seconds": 15}},
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
    if "/" in name or name.startswith(".") or not name:
        raise PanelError("not found", HTTPStatus.NOT_FOUND)
    try:
        return resources.files("tesmart").joinpath("static", name).read_bytes()
    except (FileNotFoundError, OSError) as exc:
        raise PanelError("not found", HTTPStatus.NOT_FOUND) from exc


def _content_type(name: str) -> str:
    for suffix, content_type in STATIC_TYPES.items():
        if name.endswith(suffix):
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
    status["rotate"] = config.rotate_info(server.env_file, input_count=switch.input_count)
    status["peek"] = config.peek_info(server.env_file)
    status["peek"]["active"] = server.peeker.status()
    status["last_sent"] = dict(server.last_sent)
    status["inputs"] = [
        {"number": number, "name": names.get(number)} for number in range(1, switch.input_count + 1)
    ]
    return status


def _input_payload(handler: "_Handler", payload: dict[str, Any]) -> dict[str, Any]:
    names = config.read_names(handler.server.env_file)
    return config.apply_names(payload, names)


# --- actions -----------------------------------------------------------------


def _action_input(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    server = handler.server
    raw = body.get("input")
    if raw is None:
        raise PanelError('"input" is required')
    names = config.read_names(server.env_file)
    number = config.resolve_named_input(str(raw), input_count=server.switch.input_count, names=names)
    with server.lock:
        reported = server.switch.set_input(number, verify=True)
    return _input_payload(handler, {"requested": number, "active_input": reported})


def _action_step(handler: "_Handler", direction: int) -> dict[str, Any]:
    server = handler.server
    only = config.read_rotate(server.env_file)
    with server.lock:
        previous, reported = server.switch.rotate(only, direction=direction)
    return _input_payload(handler, {"previous": previous, "requested": reported, "active_input": reported})


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
    seconds = body.get("seconds")
    if seconds is None:
        seconds = config.read_peek_seconds(server.env_file)
    elif not isinstance(seconds, int) or seconds < 1:
        raise PanelError('"seconds" must be a whole number of seconds, 1 or more')
    started = server.peeker.start(number, seconds)
    return _input_payload(handler, {"peek": started, "active_input": started["peeked"] if started else number})


def _action_peek_default(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    seconds = body.get("seconds")
    if not isinstance(seconds, int) or seconds < 1:
        raise PanelError('"seconds" must be a whole number of seconds, 1 or more')
    config.set_peek_seconds(seconds, handler.server.env_file)
    return {"peek": config.peek_info(handler.server.env_file)}


def _action_buzzer(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    on = _as_bool(body.get("on"), "on")
    with handler.server.lock:
        handler.server.switch.set_buzzer(on)
    handler.server.last_sent["buzzer"] = on
    return {"buzzer": on, "note": "sent; the switch cannot confirm this value"}


def _action_led(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    try:
        timeout = LedTimeout.from_text(str(body.get("timeout", "")))
    except ValueError as exc:
        raise PanelError(str(exc)) from exc
    with handler.server.lock:
        handler.server.switch.set_led_timeout(timeout)
    handler.server.last_sent["led_timeout"] = timeout.describe()
    return {"led_timeout": timeout.describe(), "note": "sent; the switch cannot confirm this value"}


def _action_autodetect(handler: "_Handler", body: dict[str, Any]) -> dict[str, Any]:
    on = _as_bool(body.get("on"), "on")
    with handler.server.lock:
        handler.server.switch.set_auto_detect(on)
    handler.server.last_sent["auto_detect"] = on
    return {"auto_detect": on, "note": "sent; the switch cannot confirm this value"}


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
    count = handler.server.switch.input_count
    for key, label in table.items():
        if not str(key).isdigit():
            raise PanelError(f"input number expected, got {key!r}")
        number = int(key)
        if label is None or str(label).strip() == "":
            config.unset_input_name(number, handler.server.env_file)
        else:
            config.set_input_name(number, str(label), handler.server.env_file, input_count=count)
    names = config.read_names(handler.server.env_file)
    return {"names": {str(number): label for number, label in names.items()}}


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
    return {"rotate": config.rotate_info(server.env_file, input_count=count)}


# --- dispatcher --------------------------------------------------------------


def handle(handler: "_Handler", method: str, path: str) -> bool:
    """Serve panel, static and API routes. Returns False if ``path`` is not ours."""
    parts = urlsplit(path)
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments or segments[0] not in {"panel", "static", "api"}:
        return False

    if not handler.server.panel:
        _send_json(
            handler,
            HTTPStatus.NOT_FOUND,
            {"error": "the control panel is off; start the listener with --panel"},
        )
        return True

    try:
        if segments[0] == "panel":
            _send_bytes(handler, HTTPStatus.OK, _static_body("panel.html"), STATIC_TYPES[".html"])
            return True
        if segments[0] == "static":
            if len(segments) != 2:
                raise PanelError("not found", HTTPStatus.NOT_FOUND)
            name = segments[1]
            _send_bytes(handler, HTTPStatus.OK, _static_body(name), _content_type(name))
            return True
        _send_json(handler, HTTPStatus.OK, _api(handler, method, segments[1:], parts.query))
    except PanelError as exc:
        _send_json(handler, exc.status, {"error": str(exc)})
    except (config.ConfigError, SwitchError, ValueError) as exc:
        _send_json(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
    except TransportError as exc:
        _send_json(handler, HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
    return True


def _api(handler: "_Handler", method: str, segments: list[str], query_text: str) -> dict[str, Any]:
    if not segments:
        return {"routes": _routes(), "catalog": API_CATALOG}
    name = segments[0].lower()
    query = parse_qs(query_text, keep_blank_values=True)

    if name == "state" and method == "GET":
        include_network = query.get("network", ["1"])[0].lower() not in {"0", "off", "false", "no"}
        return build_state(handler, include_network)
    if name == "network" and method == "GET":
        if len(segments) == 2 and segments[1].lower() == "notice":
            return _network_notice()
        return _action_network_get(handler)

    writable = {
        "input", "next", "previous", "peek", "peek-default",
        "buzzer", "led", "autodetect", "network", "names", "cycle",
    }
    if name not in writable:
        raise PanelError("unknown api route", HTTPStatus.NOT_FOUND)
    if method != "POST":
        raise PanelError("use POST for this route", HTTPStatus.METHOD_NOT_ALLOWED)
    body = _read_json(handler)
    actions = {
        "input": lambda: _action_input(handler, body),
        "next": lambda: _action_step(handler, 1),
        "previous": lambda: _action_step(handler, -1),
        "peek": lambda: _action_peek(handler, body),
        "peek-default": lambda: _action_peek_default(handler, body),
        "buzzer": lambda: _action_buzzer(handler, body),
        "led": lambda: _action_led(handler, body),
        "autodetect": lambda: _action_autodetect(handler, body),
        "network": lambda: _action_network_set(handler, body),
        "names": lambda: _action_names(handler, body),
        "cycle": lambda: _action_cycle(handler, body),
    }
    return actions[name]()


# --- responses ---------------------------------------------------------------


def _send_bytes(handler: "_Handler", status: HTTPStatus, body: bytes, content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    # Static files included: a stale panel.js after an upgrade is worse than a few extra kilobytes.
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_json(handler: "_Handler", status: HTTPStatus, payload: dict[str, Any]) -> None:
    _send_bytes(handler, status, (json.dumps(payload) + "\n").encode("utf-8"), "application/json")
