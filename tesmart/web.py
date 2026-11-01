"""HTTP front end for reading and changing the active input.

Runs on the standard library only. Every response is JSON unless
``format=text`` is given, in which case the body is plain text.

Routes (GET or POST)
--------------------
``/``                        this list
``/input``                   active input
``/input?set=3``             select input 3, reply with what the switch reports
``/input/3``                 same as ``/input?set=3``
``/get``                     same as ``/input``
``/set?input=3``             same as ``/input?set=3``
``/set/3``                   same as ``/input?set=3``
``/status``                  active input plus saved LAN settings
``/status?network=0``        active input only
``/rotate`` or ``/next``      next input in the saved cycle
``/previous``                previous input in the saved cycle
``/rotate?only=1,2,4``       next input within that list, this request only
``/input?rotate=1``          same as ``/rotate``
``/peek/3``                  show input 3 for the saved number of seconds, then return
``/peek?input=3&seconds=8``  same with an explicit duration; replies once back
``/peek/once``               the next plain input change peeks, then later changes stick
``/peek/always``             every plain input change peeks until ``/peek/off``
``/peek/off``                plain input changes stick again
``/enable/4``                allow input 4 again (also ``/enable?input=4`` or a local name)
``/disable/4``               ignore input 4 for selection, peek, and next/previous
``/names``                   local input names (GET list; POST JSON to set)
``/version``                 package name, version, and feature tags
``/compat``                  GET/POST compatibility check (min server version + features)
``/mesh``                    mesh role, switch id, peers (when ``--mesh``)
``/mesh/join`` ``/mesh/leave`` ``/mesh/peers`` ``/mesh/config``
``/events``                  Server-Sent Events stream for browser clients
``/callbacks``               GET list; POST {\"url\": \"...\", \"name\": \"...\"} register; DELETE remove;
                             POST {\"timeout\": 10} set wait; GET|POST /callbacks/health probe clients
``/panel``, ``/kit``, ``/api/...``  browser UIs; only with ``--panel`` (see :mod:`tesmart.panel`)
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple
from urllib.parse import parse_qs, unquote, urlsplit

from . import callbacks, config, panel, version as pkg_version
from .banner import BannerBoard
from .mdns import attach_mdns_identity
from .cycle import Cycler
from .events import EventHub, stream_sse
from .peek import PeekFollow, Peeker, apply_follow
from .switch import SwitchError, TesmartSwitch, next_input, parse_input_list, previous_input
from .transport import TransportError

ROUTES = {
    "/": "this list",
    "/input": "active input",
    "/input?set=N": "select input N, reply with the input the switch reports",
    "/input/N": "same as /input?set=N",
    "/get": "same as /input",
    "/set?input=N": "same as /input?set=N",
    "/set/N": "same as /input?set=N",
    "/status": "active input, enabled and disabled inputs, plus saved IP, port, gateway, and mask",
    "/status?network=0": "active input only (skips the LAN queries)",
    "/version": "tesmartctl package version and feature tags",
    "/compat": "GET/POST {\"min_server\":\"0.0.7.9\",\"features\":[\"callbacks\"]} → compatible?",
    "/mesh": "mesh role, switch id, proxy, peers (only with --mesh)",
    "/mesh/peers": "mesh peer list",
    "/mesh/join": "POST mesh client announce (proxy only)",
    "/mesh/leave": "POST graceful leave",
    "/mesh/proxy-leaving": "POST from resigning proxy → client elects immediately",
    "/mesh/config": "GET portable listener config (same shape as /api/config)",
    "/next": "next input in the saved local cycle",
    "/previous": "previous input in the saved local cycle",
    "/rotate": "same as /next",
    "/rotate?only=1,2,4": "next input within that list for this request only",
    "/input?rotate=1": "same as /rotate",
    "/peek/N": "show input N for the saved number of seconds, then return; replies once back",
    "/peek?input=N&seconds=S": "same with an explicit duration",
    "/peek/once": "the next plain input change peeks, then later changes stick",
    "/peek/always": "every plain input change peeks until /peek/off",
    "/peek/off": "plain input changes stick again",
    "/cycle/start": "start the listener-owned automatic cycle (mode must be automatic)",
    "/cycle/stop": "stop the automatic cycle",
    "/enable/N": "allow input N (number or local name) for selection, peek, and next/previous",
    "/disable/N": "ignore input N (number or local name) for selection, peek, and next/previous",
    "/names": "GET local input names; POST {\"names\": {\"2\": \"Office\", \"3\": \"\"}} to set or clear",
    "/banner": "GET last LCD message; POST {\"text\",\"seconds\"|\"sticky\":true,\"from\":\"Hubitat\"}; POST {\"show\":true}|{\"dismiss\":true}|{\"clear\":true}",
    "/events": "Server-Sent Events stream (input_changed, names_changed, banner, peek, cycle); kit/panel use /api/events",
    "/callbacks": "GET list; POST {\"url\",\"name\"} register; POST {\"timeout\":10}; GET|POST /health probe; DELETE remove",
    "/panel": "browser-only control panel (only when started with --panel)",
    "/kit": "floating UI application kit (only when started with --panel)",
    "?format=text": "add to any route for plain text instead of JSON",
}


class WebRequest(NamedTuple):
    verb: str
    number: int | None
    as_text: bool
    include_network: bool
    only: tuple[int, ...] | None = None
    label: str | None = None
    seconds: int | None = None


def _only_from_query(query: dict[str, list[str]]) -> tuple[int, ...] | None:
    if "only" not in query:
        return None
    return parse_input_list(query["only"][0])


def _peek_seconds(text: str | None) -> int | None:
    if text is None:
        return None
    if not text.isdigit() or int(text) < 1:
        raise ValueError("seconds must be a whole number, 1 or more")
    return int(text)


def _flag_off(query: dict[str, list[str]], name: str) -> bool:
    return query.get(name, ["1"])[0].lower() in {"0", "off", "false", "no"}


def _parse_request(path: str) -> WebRequest:
    """Parse a request path.

    ``verb`` is ``help``, ``get``, ``set``, or ``status``. Raises ``LookupError``
    for an unknown route and ``ValueError`` for a bad number.
    """
    parts = urlsplit(path)
    query = parse_qs(parts.query, keep_blank_values=True)
    as_text = query.get("format", [""])[0].lower() == "text"
    include_network = not _flag_off(query, "network")
    segments = [segment for segment in parts.path.split("/") if segment]

    if not segments:
        return WebRequest("help", None, as_text, include_network)

    head = segments[0].lower()
    if head == "status":
        if len(segments) != 1:
            raise LookupError(parts.path)
        return WebRequest("status", None, as_text, include_network)
    if head in {"version", "compat"}:
        if len(segments) != 1:
            raise LookupError(parts.path)
        return WebRequest(head, None, as_text, include_network)
    if head == "mesh":
        if len(segments) == 1:
            return WebRequest("mesh", None, as_text, include_network)
        if len(segments) == 2 and segments[1].lower() in {
            "peers",
            "join",
            "leave",
            "config",
            "proxy-leaving",
        }:
            return WebRequest(f"mesh-{segments[1].lower()}", None, as_text, include_network)
        raise LookupError(parts.path)
    if head == "names":
        if len(segments) != 1:
            raise LookupError(parts.path)
        return WebRequest("names", None, as_text, include_network)
    if head in {"enable", "disable"}:
        if len(segments) > 2:
            raise LookupError(parts.path)
        token = unquote(segments[1]) if len(segments) == 2 else query.get("input", [None])[0]
        if token is not None:
            token = unquote(str(token)).strip()
        if not token:
            raise ValueError(f"missing input: use /{head}/N or /{head}?input=N")
        if token.isdigit():
            return WebRequest(head, int(token), as_text, include_network)
        return WebRequest(head, None, as_text, include_network, None, token)
    if head == "banner":
        if len(segments) != 1:
            raise LookupError(parts.path)
        return WebRequest("banner", None, as_text, include_network)
    if head == "events":
        if len(segments) != 1:
            raise LookupError(parts.path)
        return WebRequest("events", None, as_text, include_network)
    if head == "callbacks":
        if len(segments) == 1:
            return WebRequest("callbacks", None, as_text, include_network)
        if len(segments) == 2 and segments[1].lower() in {"remove", "clear", "timeout", "health"}:
            return WebRequest(f"callbacks-{segments[1].lower()}", None, as_text, include_network)
        raise LookupError(parts.path)
    if head in {"rotate", "next", "previous"}:
        if len(segments) != 1:
            raise LookupError(parts.path)
        verb = "previous" if head == "previous" else "rotate"
        return WebRequest(verb, None, as_text, include_network, _only_from_query(query))
    if head == "peek":
        if len(segments) > 2:
            raise LookupError(parts.path)
        mode = segments[1].lower() if len(segments) == 2 else None
        if mode in {"once", "always", "off"}:
            seconds_text = query.get("seconds", [None])[0]
            if mode == "off" and seconds_text is not None:
                raise ValueError("/peek/off does not take a duration")
            seconds = _peek_seconds(seconds_text)
            return WebRequest("peek-mode", None, as_text, include_network, None, mode, seconds)
        target = segments[1] if len(segments) == 2 else query.get("input", [None])[0]
        if not target:
            raise ValueError("missing input: use /peek/N or /peek?input=N")
        seconds = _peek_seconds(query.get("seconds", [None])[0])
        if target.isdigit():
            return WebRequest("peek", int(target), as_text, include_network, None, None, seconds)
        return WebRequest("peek", None, as_text, include_network, None, target, seconds)
    if head == "cycle":
        if len(segments) != 2 or segments[1].lower() not in {"start", "stop"}:
            raise LookupError(parts.path)
        return WebRequest(f"cycle-{segments[1].lower()}", None, as_text, include_network)
    if head not in {"input", "get", "set"} or len(segments) > 2:
        raise LookupError(parts.path)

    number_text: str | None = None
    if len(segments) == 2:
        number_text = segments[1]
    elif "set" in query:
        number_text = query["set"][0]
    elif "input" in query:
        number_text = query["input"][0]

    wants_rotate = query.get("rotate", ["0"])[0].lower() in {"1", "true", "yes", "next"}
    if wants_rotate and number_text is not None:
        raise ValueError("pass either an input number or rotate, not both")
    if head == "set" and number_text is None and not wants_rotate:
        raise ValueError("missing input number: use /set?input=N or /set/N")
    if head == "get" and (number_text is not None or wants_rotate):
        raise ValueError("/get does not take an input number")
    if wants_rotate:
        return WebRequest("rotate", None, as_text, include_network, _only_from_query(query))
    if number_text is None:
        return WebRequest("get", None, as_text, include_network)
    if not number_text.isdigit():
        return WebRequest("set", None, as_text, include_network, None, number_text)
    return WebRequest("set", int(number_text), as_text, include_network)


def _status_text(payload: dict) -> str:
    active = payload.get("active_input")
    name = payload.get("active_name")
    lines = [f"active_input: {active} ({name})" if name else f"active_input: {active}"]
    if payload.get("simulated"):
        lines.append("simulated: yes")
    network = payload.get("network") or {}
    for key in ("ip", "port", "gateway", "mask"):
        if key in network:
            lines.append(f"{key}: {network[key]}")
    if "enabled" in payload or "disabled" in payload:
        enabled_text, disabled_text = config.format_input_enablement(
            payload.get("enabled") or [],
            payload.get("disabled") or [],
            payload.get("names"),
        )
        lines.append(f"enabled: {enabled_text}")
        lines.append(f"disabled: {disabled_text}")
    names = payload.get("names") or {}
    if names:
        lines.append("names:")
        for number, label in names.items():
            lines.append(f"  {number}: {label}")
    rotate = payload.get("rotate") or {}
    if rotate:
        cycle = ", ".join(str(number) for number in rotate.get("cycle") or [])
        chosen = "chosen inputs" if rotate.get("configured") else "all inputs"
        if rotate.get("mode") == "automatic":
            lines.append(f"cycle: automatic every {rotate.get('seconds')}s; {cycle} ({chosen})")
        else:
            lines.append(f"cycle: manual (next/previous); {cycle} ({chosen})")
        active = rotate.get("active")
        if active:
            lines.append(
                f"cycle running: input {active.get('active_input')} → next {active.get('next_input')} "
                f"in {active.get('remaining')}s"
            )
    peek = payload.get("peek") or {}
    if peek:
        lines.append(f"peek: {peek.get('seconds')}s then back")
        follow = peek.get("follow") or "off"
        if follow != "off":
            lines.append(f"peek follow: {follow}")
    last_sent = payload.get("last_sent") or {}
    if any(value is not None for value in last_sent.values()):
        lines.append("last_sent (local; switch cannot report these):")
        if last_sent.get("buzzer") is not None:
            lines.append(f"  buzzer: {'on' if last_sent['buzzer'] else 'off'}")
        if last_sent.get("led_timeout") is not None:
            lines.append(f"  led_timeout: {last_sent['led_timeout']}")
        if last_sent.get("auto_detect") is not None:
            lines.append(f"  auto_detect: {'on' if last_sent['auto_detect'] else 'off'}")
    return "\n".join(lines)


class _Handler(BaseHTTPRequestHandler):
    server: "WebServer"

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802
        self._not_supported_method()

    def do_HEAD(self) -> None:  # noqa: N802
        self._not_supported_method()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._not_supported_method()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.client_address[0]} {fmt % args}", file=sys.stderr)

    def _not_supported_method(self) -> None:
        path = urlsplit(self.path).path or "/"
        payload = pkg_version.not_supported(path, method=self.command)
        payload["routes"] = ROUTES
        self._send(HTTPStatus.NOT_FOUND, payload, False)

    def _handle(self) -> None:
        if panel.handle(self, self.command, self.path):
            return
        try:
            request = _parse_request(self.path)
        except LookupError as exc:
            path = str(exc) if str(exc) else (urlsplit(self.path).path or "/")
            payload = pkg_version.not_supported(path, method=self.command)
            payload["routes"] = ROUTES
            self._send(HTTPStatus.NOT_FOUND, payload, False)
            return
        except ValueError as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, False)
            return

        if request.verb == "help":
            self._send(HTTPStatus.OK, {"routes": ROUTES, "version": pkg_version.package_version()}, False)
            return
        if request.verb == "version":
            payload = pkg_version.version_info()
            attach_mdns_identity(payload, self.server)
            mesh = getattr(self.server, "mesh", None)
            if mesh is not None:
                payload["mesh"] = mesh.snapshot()
            text = f"{payload['name']} {payload['version']}" if request.as_text else None
            self._send(HTTPStatus.OK, payload, request.as_text, text)
            return
        if request.verb.startswith("mesh"):
            try:
                payload = self._mesh(request.verb, self.command)
            except ValueError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            except LookupError:
                self._send(HTTPStatus.NOT_FOUND, {"error": "mesh is not enabled on this listener"}, False)
                return
            text = None
            if request.as_text:
                role = (payload.get("role") or payload.get("mesh", {}).get("role") or "-")
                text = f"mesh {role}"
            self._send(HTTPStatus.OK, payload, request.as_text, text)
            return
        if request.verb == "compat":
            try:
                payload = self._compat(self.command)
            except ValueError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            text = payload.get("message") if request.as_text else None
            self._send(HTTPStatus.OK, payload, request.as_text, text)
            return
        if request.verb == "peek-mode":
            from .mesh import MeshError, client_proxy_url, forward_to_proxy

            if client_proxy_url(self.server):
                mode = request.label or "off"
                body: dict = {"mode": mode}
                if request.seconds is not None:
                    body["seconds"] = request.seconds
                try:
                    payload = forward_to_proxy(self.server, "/api/peek-follow", method="POST", body=body)
                except MeshError as exc:
                    self._send(HTTPStatus.BAD_GATEWAY, {"error": str(exc)}, request.as_text)
                    return
                follow = payload.get("peek_follow") or mode
                seconds = payload.get("seconds")
                try:
                    self.server.peek_follow.set_mode(
                        str(follow),
                        int(seconds) if follow != "off" and seconds is not None else None,
                    )
                except Exception:
                    pass
                try:
                    config.set_peek_follow(str(follow), self.server.env_file)
                except Exception:
                    pass
                panel._publish_peek_follow(
                    self.server,
                    {"mode": follow, "seconds": seconds},
                )
                text = str(follow) if request.as_text else None
                self._send(HTTPStatus.OK, payload, request.as_text, text)
                return
            state = self.server.peek_follow.set_mode(request.label or "off", request.seconds)
            try:
                config.set_peek_follow(str(state["mode"]), self.server.env_file)
            except Exception:
                pass
            payload = {"peek_follow": state["mode"]}
            if state["seconds"] is not None:
                payload["seconds"] = state["seconds"]
            panel._publish_peek_follow(self.server, state)
            self._send(HTTPStatus.OK, payload, request.as_text, state["mode"])
            return
        if request.verb in {"cycle-start", "cycle-stop"}:
            try:
                if request.verb == "cycle-start":
                    active = self.server.cycler.start()
                    payload = {"rotate": {"active": active}, "active": active, "active_input": active.get("active_input")}
                    text = f"cycling {', '.join(str(n) for n in active.get('cycle') or [])}" if request.as_text else None
                else:
                    payload = self.server.cycler.stop()
                    text = "cycle stopped" if request.as_text else None
            except SwitchError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            except TransportError as exc:
                self._send(HTTPStatus.BAD_GATEWAY, {"error": str(exc)}, request.as_text)
                return
            self._send(HTTPStatus.OK, payload, request.as_text, text)
            return
        if request.verb in {
            "callbacks",
            "callbacks-remove",
            "callbacks-clear",
            "callbacks-timeout",
            "callbacks-health",
        }:
            try:
                payload = self._callbacks(request.verb, self.command)
            except config.ConfigError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            except ValueError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            text = callbacks.format_callbacks_text(payload) if request.as_text else None
            self._send(HTTPStatus.OK, payload, request.as_text, text)
            return
        if request.verb == "names":
            try:
                payload = self._names(self.command)
            except config.ConfigError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            except ValueError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            text = None
            if request.as_text:
                lines = [f"{number}: {label}" for number, label in sorted(
                    ((int(k), v) for k, v in (payload.get("names") or {}).items()),
                    key=lambda item: item[0],
                )]
                text = "\n".join(lines) if lines else "(none)"
            self._send(HTTPStatus.OK, payload, request.as_text, text)
            return
        if request.verb == "banner":
            try:
                payload = self._banner(self.command)
            except ValueError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            text = None
            if request.as_text:
                banner = payload.get("banner")
                text = "(none)" if not banner else f"{banner.get('text')} ({banner.get('remaining')}s)"
            self._send(HTTPStatus.OK, payload, request.as_text, text)
            return
        if request.verb in {"enable", "disable"}:
            try:
                number = self._resolve(request)
                payload = panel.record_input_enabled(
                    self.server, number, request.verb == "enable"
                )
            except config.ConfigError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            except SwitchError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
                return
            word = "enabled" if payload.get("enabled") else "disabled"
            text = f"input {payload.get('input')} {word}" if request.as_text else None
            self._send(HTTPStatus.OK, payload, request.as_text, text)
            return
        if request.verb == "events":
            if self.command.upper() != "GET":
                self._send(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    {"error": "use GET /events for the Server-Sent Events stream"},
                    False,
                )
                return
            stream_sse(self, self.server.events)
            return

        try:
            if request.verb == "peek":
                payload = self._peek(request)
            else:
                payload = self._with_switch(request)
        except config.ConfigError as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
            return
        except SwitchError as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, request.as_text)
            return
        except TransportError as exc:
            self._send(HTTPStatus.BAD_GATEWAY, {"error": str(exc)}, request.as_text)
            return
        names = config.read_names(self.server.env_file)
        # Always attach the names map so Hubitat (and other clients) can keep
        # child labels in sync after /input, /next, and /previous — not only /status.
        config.apply_names(payload, names, include_map=True)
        if request.verb == "status":
            if getattr(self.server.switch, "simulated", False):
                payload["simulated"] = True
            payload["version"] = pkg_version.package_version()
            attach_mdns_identity(payload, self.server)
            payload.update(config.input_enablement(self.server.switch.input_count, self.server.env_file))
            payload["rotate"] = config.rotate_info(self.server.env_file, input_count=self.server.switch.input_count)
            payload["rotate"]["active"] = self.server.cycler.status()
            payload["peek"] = config.peek_info(self.server.env_file)
            payload["peek"]["active"] = self.server.peeker.status()
            payload["peek"]["follow"] = self.server.peek_follow.status()["mode"]
            # Remembered write-only settings (buzzer / LED / auto-detect) from this host.
            payload["last_sent"] = dict(self.server.last_sent)
            banner_payload = self.server.banner.payload()
            payload["banner"] = banner_payload["banner"]
            payload["banner_history"] = banner_payload["banner_history"]
            mesh = getattr(self.server, "mesh", None)
            if mesh is not None:
                payload["mesh"] = mesh.snapshot()
        if request.verb in {"set", "rotate", "previous", "peek"}:
            self._note_callback_change(payload, request.verb)
        text = _status_text(payload) if request.verb == "status" else None
        self._send(HTTPStatus.OK, payload, request.as_text, text)

    def _note_callback_change(self, payload: dict, verb: str) -> None:
        hub = getattr(self.server, "callbacks", None)
        if hub is None:
            return
        active = payload.get("active_input")
        if active is None and verb == "peek":
            # Blocking peek reply is the restored input after returning.
            active = payload.get("restored") or payload.get("active_input")
        previous = payload.get("previous")
        try:
            hub.note_active(
                int(active) if active is not None else None,
                previous=int(previous) if previous is not None else None,
                source="command",
            )
        except (TypeError, ValueError):
            return

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object")
        return parsed

    def _compat(self, method: str) -> dict:
        """Answer whether this server meets a client's minimum version / features."""
        method = method.upper()
        if method not in {"GET", "POST", "PUT"}:
            raise ValueError("use GET or POST on /compat")
        body: dict = {}
        if method in {"POST", "PUT"}:
            body = self._read_json_body()
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

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

    def _mesh(self, verb: str, method: str) -> dict:
        mesh = getattr(self.server, "mesh", None)
        if mesh is None:
            raise LookupError("mesh disabled")
        method = method.upper()
        if verb == "mesh":
            if method != "GET":
                raise ValueError("use GET /mesh")
            return mesh.snapshot()
        if verb == "mesh-peers":
            if method != "GET":
                raise ValueError("use GET /mesh/peers")
            snap = mesh.snapshot()
            return {"peers": snap.get("peers") or [], "mesh": snap}
        if verb == "mesh-config":
            if method != "GET":
                raise ValueError("use GET /mesh/config")
            from .config_bundle import export_bundle, summarize_bundle

            bundle = export_bundle(self.server.env_file, include_kit=False)
            return {"bundle": bundle, "summary": summarize_bundle(bundle)}
        if verb == "mesh-join":
            if method != "POST":
                raise ValueError("use POST /mesh/join")
            body = self._read_json_body()
            return mesh.handle_join(body)
        if verb == "mesh-leave":
            if method != "POST":
                raise ValueError("use POST /mesh/leave")
            body = self._read_json_body()
            # A proxy records peer leave; a client resigns itself.
            snap = mesh.snapshot()
            if snap.get("role") == "proxy":
                return mesh.handle_leave(body)
            return mesh.resign()
        if verb == "mesh-proxy-leaving":
            if method != "POST":
                raise ValueError("use POST /mesh/proxy-leaving")
            body = self._read_json_body()
            return mesh.handle_proxy_leaving(body)
        raise LookupError(verb)

    def _banner(self, method: str) -> dict:
        """Show, dismiss, reshow, or read the LCD banner (in-memory for this process)."""
        board = self.server.banner
        hub = getattr(self.server, "callbacks", None)
        method = method.upper()

        def _pack(**extra: object) -> dict:
            payload = board.payload()
            payload.update(extra)
            return payload

        def _notify() -> None:
            if hub is None:
                return
            snap = board.payload()
            hub.note_banner(
                snap.get("banner"),
                source="command",
                history=list(snap.get("banner_history") or []),
            )

        if method == "GET":
            return _pack()
        if method == "DELETE":
            previous = board.dismiss()
            _notify()
            return _pack(dismissed=previous)
        if method not in {"POST", "PUT"}:
            raise ValueError("use GET to read the banner or POST to set/dismiss it")
        body = self._read_json_body()
        if body.get("clear") is True:
            previous = board.clear()
            if hub is not None:
                hub.note_banner(None, source="command", history=[])
            return {"banner": None, "banner_history": [], "cleared": previous}
        if body.get("dismiss") is True:
            previous = board.dismiss()
            _notify()
            return _pack(dismissed=previous)
        if body.get("show") is True:
            board.reshow()
            _notify()
            return _pack()
        board.show(
            body.get("text"),
            body.get("seconds"),
            sticky=body.get("sticky"),
            from_=body.get("from"),
        )
        _notify()
        return _pack()

    def _names(self, method: str) -> dict:
        """List or update local input names stored in the listener env file."""
        if method.upper() == "GET":
            names = config.read_names(self.server.env_file)
            return {"names": {str(number): label for number, label in names.items()}}

        if method.upper() not in {"POST", "PUT"}:
            raise ValueError("use GET to list names or POST to set them")

        body = self._read_json_body()
        table = body.get("names", body)
        if not isinstance(table, dict):
            raise ValueError('"names" must be an object of input number -> label')
        env = self.server.env_file
        before = config.read_names(env)
        count = self.server.switch.input_count
        for key, label in table.items():
            if not str(key).isdigit():
                raise ValueError(f"input number expected, got {key!r}")
            number = int(key)
            if label is None or str(label).strip() == "":
                config.unset_input_name(number, env)
            else:
                config.set_input_name(number, str(label), env, input_count=count)
        names = config.read_names(env)
        hub = getattr(self.server, "callbacks", None)
        if hub is not None:
            hub.note_names(before, names, source="command")
        return {"names": {str(number): label for number, label in names.items()}}

    def _callbacks(self, verb: str, method: str) -> dict:
        """List, register, remove, health-check callbacks; set POST timeout."""
        env = self.server.env_file
        method = method.upper()
        if verb == "callbacks-clear":
            if method not in {"POST", "DELETE"}:
                raise ValueError("use POST or DELETE on /callbacks/clear")
            return callbacks.clear_callbacks(env)

        if verb == "callbacks-health":
            if method not in {"GET", "POST"}:
                raise ValueError("use GET or POST on /callbacks/health")
            return callbacks.health_check(env)

        if verb == "callbacks-timeout":
            if method == "GET":
                return callbacks.list_callbacks(env)
            if method not in {"POST", "PUT"}:
                raise ValueError('use GET or POST /callbacks/timeout with {"timeout": 10}')
            body = self._read_json_body()
            if "timeout" not in body:
                raise ValueError('provide {"timeout": 10} (seconds)')
            return callbacks.set_timeout(body["timeout"], env)

        if verb == "callbacks-remove" or (verb == "callbacks" and method == "DELETE"):
            if method not in {"POST", "DELETE"}:
                raise ValueError("use POST /callbacks/remove or DELETE /callbacks")
            body = self._read_json_body() if method != "GET" else {}
            url = body.get("url") or body.get("callback")
            name = body.get("name") or body.get("label")
            if not url and not name:
                # Allow ?url= / ?name= for DELETE without a body.
                query = parse_qs(urlsplit(self.path).query)
                url = (query.get("url") or [None])[0]
                name = (query.get("name") or [None])[0]
            if not url and not name:
                raise ValueError('provide {"url": "http://..."} or {"name": "..."} to remove')
            return callbacks.remove_callback(
                str(url) if url else None,
                env,
                name=str(name) if name else None,
            )

        if verb == "callbacks" and method == "GET":
            return callbacks.list_callbacks(env)

        if verb == "callbacks" and method in {"POST", "PUT"}:
            body = self._read_json_body()
            action = str(body.get("action") or "add").lower()
            url = body.get("url") or body.get("callback")
            name = body.get("name") or body.get("label")
            if action in {"health", "ping", "check"}:
                return callbacks.health_check(env)
            if action in {"remove", "delete", "unregister"}:
                if not url and not name:
                    raise ValueError('provide {"url": "http://..."} or {"name": "..."} with action remove')
                return callbacks.remove_callback(
                    str(url) if url else None,
                    env,
                    name=str(name) if name else None,
                )
            if action in {"clear", "reset"}:
                return callbacks.clear_callbacks(env)
            if action in {"timeout", "set-timeout"} or ("timeout" in body and not url and not name):
                if "timeout" not in body:
                    raise ValueError('provide {"timeout": 10} (seconds)')
                return callbacks.set_timeout(body["timeout"], env)
            if not url:
                raise ValueError('provide {"url": "http://..."} to register, or {"timeout": 10}')
            result = callbacks.add_callback(str(url), env, name=str(name) if name else None)
            if "timeout" in body:
                timed = callbacks.set_timeout(body["timeout"], env)
                result["timeout"] = timed["timeout"]
            return result

        raise ValueError("use GET/POST/DELETE on /callbacks")

    def _resolve(self, request: WebRequest) -> int:
        if request.label:
            names = config.read_names(self.server.env_file)
            return config.resolve_named_input(request.label, input_count=self.server.switch.input_count, names=names)
        if request.number is None:
            raise SwitchError("missing input number")
        return request.number

    def _peek(self, request: WebRequest) -> dict:
        """Blocks until the switch is back. The device lock is held only while talking to it."""
        seconds = request.seconds if request.seconds is not None else config.read_peek_seconds(self.server.env_file)
        number = self._resolve(request)
        config.require_enabled(number, self.server.env_file)
        return self.server.peeker.run(number, seconds).to_dict()

    def _followed(self, number: int) -> dict | None:
        """Peek at ``number`` when follow mode is armed. The request waits until back."""
        return apply_follow(
            self.server.peek_follow,
            self.server.peeker,
            number,
            config.read_peek_seconds(self.server.env_file),
            block=True,
        )

    def _with_switch(self, request: WebRequest) -> dict:
        switch = self.server.switch
        if request.verb in {"rotate", "previous"}:
            only = request.only if request.only is not None else config.read_rotate(self.server.env_file)
            only = config.enabled_cycle(switch.input_count, only, self.server.env_file)
            direction = -1 if request.verb == "previous" else 1
            with self.server.lock:
                current = switch.get_active_input()
                step = previous_input if direction < 0 else next_input
                target = step(current, switch.input_count, only)
            followed = self._followed(target)
            if followed is not None:
                followed["previous"] = current
                return followed
            with self.server.lock:
                reported = switch.set_input(target, verify=True)
            reported = reported if reported is not None else target
            return {"previous": current, "requested": reported, "active_input": reported}
        if request.verb == "set":
            number = self._resolve(request)
            config.require_enabled(number, self.server.env_file)
            followed = self._followed(number)
            if followed is not None:
                return followed
            with self.server.lock:
                previous = None
                try:
                    previous = switch.get_active_input()
                except SwitchError:
                    previous = None
                reported = switch.set_input(number, verify=True)
            return {"previous": previous, "active_input": reported, "requested": number}
        if request.verb in {"status", "get"}:
            # Prefer cached input from the watcher — never block Hubitat/kit-style
            # polls on a wedged HDMI TCP session (same failure mode as /api/state).
            hub = getattr(self.server, "callbacks", None)
            cached = getattr(hub, "_last_active", None) if hub is not None else None
            if request.verb == "get" and cached is not None:
                return {"active_input": cached}
            if not self.server.lock.acquire(timeout=0.5):
                if cached is not None:
                    return {"active_input": cached} if request.verb == "get" else {
                        "endpoint": getattr(switch, "endpoint", ""),
                        "active_input": cached,
                        "input_count": switch.input_count,
                    }
                raise SwitchError("switch busy")
            try:
                if request.verb == "status":
                    return switch.read_status(include_network=request.include_network).to_dict()
                return {"active_input": switch.get_active_input(retries=0)}
            finally:
                self.server.lock.release()
        number = self._resolve(request)
        config.require_enabled(number, self.server.env_file)
        with self.server.lock:
            return {"active_input": switch.set_input(number, verify=True), "requested": number}

    def _send(self, status: HTTPStatus, payload: dict, as_text: bool, text: str | None = None) -> None:
        if as_text:
            text = text if text is not None else payload.get("error") or str(payload.get("active_input"))
            body = (text + "\n").encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        else:
            body = (json.dumps(payload) + "\n").encode("utf-8")
            content_type = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class WebServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    # Client hung up mid-request (kit close, SSE reconnect, flaky Wi-Fi). Not a server bug.
    _CLIENT_GONE = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)

    def __init__(
        self,
        address: tuple[str, int],
        switch: TesmartSwitch,
        env_file: str | None = None,
        *,
        panel_enabled: bool = False,
    ):
        super().__init__(address, _Handler)
        self.switch = switch
        self.env_file = env_file
        self.panel = panel_enabled
        # Last write-only values sent through this host (persisted in the env file).
        self.last_sent: dict[str, object] = config.read_last_sent(env_file)
        self.lock = threading.Lock()
        # Browser kit/panel subscribe via GET /events or /api/events (SSE).
        self.events = EventHub()
        self.callbacks = callbacks.CallbackHub(switch, self.lock, env_file, events=self.events)
        # Shared so a peek started from /peek or the panel is visible to /api/state.
        # Publishes SSE ``peek`` and notes the temporary / restored input for callbacks.
        self.peeker = Peeker(
            switch,
            self.lock,
            events=self.events,
            notify_active=lambda active, previous=None: self.callbacks.note_active(
                active, previous=previous, source="command"
            ),
        )
        # Armed by /peek/once or /peek/always. The next plain input change peeks.
        # Mode is restored from the env file so mesh failover keeps the setting.
        self.peek_follow = PeekFollow(
            events=self.events,
            notify_peek_follow=lambda mode, seconds=None, source="command", **extra: (
                self.callbacks.note_peek_follow(
                    mode, seconds=seconds, source=source, **extra
                )
            ),
        )
        try:
            saved_follow = config.read_peek_follow(env_file)
        except Exception:
            saved_follow = "off"
        if saved_follow != "off":
            try:
                self.peek_follow.set_mode(saved_follow, config.read_peek_seconds(env_file))
            except Exception:
                pass
        # Listener-owned automatic cycle; kit/panel/CLI start and stop it.
        self.cycler = Cycler(
            switch,
            self.lock,
            env_file=env_file,
            events=self.events,
            notify_active=lambda active, previous=None: self.callbacks.note_active(
                active, previous=previous, source="command"
            ),
            peeking=lambda: self.peeker.status() is not None,
        )
        # LCD banner + history (restored from env file so kit icons return on startup).
        self.banner = BannerBoard(
            history=config.read_banner_history(env_file),
            persist=lambda entries: config.write_banner_history(entries, env_file),
        )
        # Instance name actually registered with mDNS, or None when not advertising.
        # Reconnecting clients compare this to the name they attached with.
        self.mdns_name: str | None = None
        # Set by serve_http when ``--mesh`` / TESMART_HTTP_MESH is on.
        self.mesh: object | None = None
        self.mesh_sse_stop: threading.Event | None = None

    def handle_error(self, request: object, client_address: tuple[str, int] | str) -> None:
        """Log client disconnects as one line; keep a traceback only for real bugs."""
        exc = sys.exc_info()[1]
        if isinstance(exc, self._CLIENT_GONE):
            host = client_address[0] if isinstance(client_address, tuple) else client_address
            print(f"{host} connection closed by peer", file=sys.stderr)
            return
        super().handle_error(request, client_address)


def serve_http(
    switch: TesmartSwitch | None,
    host: str,
    port: int,
    env_file: str | None = None,
    *,
    panel_enabled: bool = False,
    mdns: bool | None = None,
    mdns_name: str | None = None,
    mesh: bool | None = None,
    mesh_mdns_name: str | None = None,
    open_switch: object | None = None,
) -> int:
    """Serve the routes above on ``host:port`` until Ctrl-C.

    ``mdns`` is True/False for ``--mdns`` / ``--no-mdns``, or None for the
    default (advertise when the bind is not loopback-only).

    ``mesh`` enables mesh mode when True. Pass False for a standalone listener
    (do not pass None from ``listen`` — that used to fall through to
    ``TESMART_HTTP_MESH`` and join a mesh without ``--mesh``). When mesh is on
    and this node becomes a client, ``open_switch`` is unused and the real
    ``switch`` may be ``None``; pass a factory via ``open_switch`` so failover
    to proxy can open TCP later.

    ``mesh_mdns_name`` sets the shared failover DNS-SD name (default
    ``{instance}-mesh``, persisted as ``TESMART_HTTP_MESH_MDNS_NAME``). Only the
    elected proxy advertises that alias.
    """
    from . import mdns as mdns_mod
    from .mesh import (
        MeshController,
        MeshError,
        ProxySwitch,
        mesh_enabled,
        switch_id_from_env,
        write_mesh_enabled,
        write_mesh_mdns_name,
    )

    mesh_on = mesh_enabled(env_file, flag=mesh)
    mesh_controller: MeshController | None = None
    switch_id: str | None = None
    started_at = 0
    active_switch = switch

    if mesh_on:
        try:
            switch_id = switch_id_from_env(env_file)
        except MeshError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        write_mesh_enabled(True, env_file)
        if mesh_mdns_name:
            write_mesh_mdns_name(mesh_mdns_name, env_file)
        started_at = int(__import__("time").time() * 1000)
        settings = config.resolve(env_file)
        # Elect before binding so a client never opens a second TCP session.
        prelim = MeshController(
            env_file=env_file,
            switch_id=switch_id,
            started_at=started_at,
            mesh_mdns_name=mesh_mdns_name,
        )
        # Alias / live proxy first — never open TCP or claim …-mesh until we
        # know nobody else already owns this switch on the LAN.
        planned_name = (mdns_name or config.read_mdns_name(env_file) or mdns_mod.default_instance_name()).replace(".", "-")
        planned_url = f"http://{host if host not in {'0.0.0.0', '::', ''} else '127.0.0.1'}:{port}"
        prelim.set_identity(mdns_name=planned_name, base_url=planned_url)
        existing_proxy = prelim.find_live_proxy_url()
        if existing_proxy:
            print(f"  mesh: joining proxy {existing_proxy}", file=sys.stderr)
            active_switch = ProxySwitch(
                existing_proxy,
                input_count=settings.inputs,
                endpoint=f"tcp://{settings.host}:{settings.port}",
            )
        else:
            from .mesh import elect_proxy

            peers = prelim.discover()
            winner = elect_proxy(
                peers,
                self_started=started_at,
                self_name=planned_name,
                self_url=planned_url,
            )
            if not winner.get("self"):
                proxy_url = str(winner.get("url") or "").rstrip("/")
                if not proxy_url:
                    print("error: mesh election found a peer without a URL", file=sys.stderr)
                    return 1
                print(f"  mesh: joining proxy {proxy_url}", file=sys.stderr)
                active_switch = ProxySwitch(
                    proxy_url,
                    input_count=settings.inputs,
                    endpoint=f"tcp://{settings.host}:{settings.port}",
                )
            elif active_switch is None and open_switch is not None:
                active_switch = open_switch()  # type: ignore[operator]
            elif active_switch is None:
                print("error: mesh proxy needs an open switch connection", file=sys.stderr)
                return 1

    if active_switch is None:
        print("error: no switch connection", file=sys.stderr)
        return 1

    try:
        server = WebServer((host, port), active_switch, env_file, panel_enabled=panel_enabled)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    bound_host, bound_port = server.server_address[:2]
    base_url = f"http://{bound_host if bound_host not in {'0.0.0.0', '::'} else '127.0.0.1'}:{bound_port}"
    # Prefer a LAN address for mesh peer registration when bound to all interfaces.
    if host in {"0.0.0.0", "::", ""}:
        try:
            lan = mdns_mod.local_lan_ipv4s()
            if lan:
                base_url = f"http://{lan[0]}:{bound_port}"
        except Exception:
            pass

    print(
        f"HTTP listener on http://{bound_host}:{bound_port}/ (Ctrl-C to stop).",
        file=sys.stderr,
    )
    from .debug import log_process

    log_process()
    print(f"  HDMI switch:  {active_switch.endpoint}", file=sys.stderr)
    if getattr(active_switch, "simulated", False):
        print(
            "  Simulated switch: input changes, peek, and cycle stay on this computer "
            "and are not sent to an HDMI switch.",
            file=sys.stderr,
        )
    if isinstance(active_switch, ProxySwitch):
        print(f"  mesh client via {active_switch.proxy_url}", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/input        read", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/status       read input and LAN settings", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/version      {pkg_version.package_version()}", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/compat       client/server compatibility check", file=sys.stderr)
    if mesh_on:
        print(f"  http://{bound_host}:{bound_port}/mesh         mesh role and peers", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/names        local input names (GET / POST)", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/enable/N     allow input N", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/disable/N    ignore input N", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/banner       brief LCD message (GET / POST)", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/events       Server-Sent Events (kit/panel push)", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/callbacks    change notifications (GET / POST / DELETE)", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/rotate       next input", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/input?set=3  switch", file=sys.stderr)
    if panel_enabled:
        print(f"  http://{bound_host}:{bound_port}/panel        browser-only control panel", file=sys.stderr)
        print(f"  http://{bound_host}:{bound_port}/kit          floating UI application kit", file=sys.stderr)
        print(f"  tesmartctl kit --listen {bound_port}          open /kit without browser chrome", file=sys.stderr)
    registered = callbacks.read_callback_urls(env_file)
    timeout = callbacks.read_timeout(env_file)
    if registered:
        print(
            f"  {len(registered)} callback URL(s) loaded from env file (timeout {timeout:g}s)",
            file=sys.stderr,
        )
    else:
        print(f"  callback POST timeout {timeout:g}s", file=sys.stderr)

    advertisement = None
    mesh_role = "proxy"
    if mesh_on and switch_id is not None:
        mesh_controller = MeshController(
            env_file=env_file,
            switch_id=switch_id,
            started_at=started_at,
            mesh_mdns_name=mesh_mdns_name,
            on_event=lambda payload: server.events.publish(payload),
        )

        def _rebind_local_owners(switch_obj: object) -> None:
            """Peeker/cycler keep their own switch ref — update on mesh role change."""
            for owner in (server.peeker, server.cycler):
                rebind = getattr(owner, "rebind_switch", None)
                if callable(rebind):
                    try:
                        rebind(switch_obj)
                    except Exception as exc:  # noqa: BLE001
                        print(f"warning: mesh rebind failed: {exc}", file=sys.stderr)

        def _open_owned_switch() -> bool:
            """Replace ProxySwitch with a real TCP session. False if still remote."""
            nonlocal active_switch
            if not isinstance(server.switch, ProxySwitch):
                return True
            if open_switch is None:
                print(
                    "warning: mesh promoted to proxy but no open_switch factory; "
                    "switch commands and Hubitat events will not work",
                    file=sys.stderr,
                )
                return False
            try:
                real = open_switch()  # type: ignore[operator]
            except Exception as exc:  # noqa: BLE001
                print(
                    f"warning: mesh could not open switch for proxy role: {exc}",
                    file=sys.stderr,
                )
                return False
            server.switch = real
            active_switch = real
            print(f"  mesh: opened switch {real.endpoint}", file=sys.stderr)
            _rebind_local_owners(real)
            try:
                server.callbacks.rebind_switch(real, outbound_http=True)
            except Exception as exc:  # noqa: BLE001
                print(f"warning: mesh callbacks rebind failed: {exc}", file=sys.stderr)
            return True

        def _promote_to_proxy() -> None:
            nonlocal active_switch
            # Stop bridging SSE from the old proxy — we own the switch now.
            if server.mesh_sse_stop is not None:
                try:
                    server.mesh_sse_stop.set()
                except Exception:
                    pass
                server.mesh_sse_stop = None
            # Restore shared banner history / peek-follow from env (synced while
            # we were a client, or from the last config pull).
            from .mesh import hydrate_shared_runtime

            hydrate_shared_runtime(server, env_file=env_file)
            if isinstance(server.switch, ProxySwitch):
                if not _open_owned_switch():
                    # TESmart often keeps the dying proxy's TCP for a few seconds.
                    # Keep retrying so /api/state is not stuck on ProxySwitch until
                    # the operator restarts this listener.
                    def _retry_open() -> None:
                        delays = (1.0, 2.0, 3.0, 5.0, 5.0, 8.0, 8.0, 10.0)
                        for delay in delays:
                            if not isinstance(server.switch, ProxySwitch):
                                return
                            time.sleep(delay)
                            if _open_owned_switch():
                                _refresh_mesh_advertise(
                                    advertisement, mesh_controller, panel_enabled, server
                                )
                                return
                        print(
                            "warning: mesh still has no TCP session after promote retries; "
                            "restart this listener or power-cycle the switch",
                            file=sys.stderr,
                        )

                    threading.Thread(
                        target=_retry_open, name="tesmart-mesh-open", daemon=True
                    ).start()
            _refresh_mesh_advertise(advertisement, mesh_controller, panel_enabled, server)

        def _demote_to_client(proxy_url: str) -> None:
            settings = config.resolve(env_file)
            remote = ProxySwitch(
                proxy_url,
                input_count=settings.inputs,
                endpoint=f"tcp://{settings.host}:{settings.port}",
            )
            # Drop a real TCP session if we previously owned the switch.
            old = server.switch
            if old is not None and not isinstance(old, ProxySwitch):
                try:
                    owned = getattr(old, "_mesh_owned_transport", None)
                    if owned is not None:
                        owned.close()
                    close = getattr(old, "close", None)
                    if callable(close):
                        close()
                except Exception:
                    pass
            server.switch = remote
            _rebind_local_owners(remote)
            try:
                server.callbacks.rebind_switch(remote, outbound_http=False)
            except Exception as exc:  # noqa: BLE001
                print(f"warning: mesh callbacks rebind failed: {exc}", file=sys.stderr)
            _start_mesh_sse_bridge(server, proxy_url)
            _refresh_mesh_advertise(advertisement, mesh_controller, panel_enabled, server)

        mesh_controller._on_become_proxy = _promote_to_proxy
        mesh_controller._on_become_client = _demote_to_client
        mesh_controller.set_identity(mdns_name=None, base_url=base_url)
        if isinstance(active_switch, ProxySwitch):
            try:
                mesh_controller._join(active_switch.proxy_url)
            except MeshError as exc:
                print(f"warning: mesh join failed ({exc})", file=sys.stderr)
                if mesh_controller.try_join_alias_owner():
                    pass
                else:
                    print(
                        "  mesh: no live alias owner; becoming proxy",
                        file=sys.stderr,
                    )
                    if open_switch is not None:
                        try:
                            real = open_switch()  # type: ignore[operator]
                            server.switch = real
                            active_switch = real
                        except Exception as open_exc:  # noqa: BLE001
                            print(f"error: {open_exc}", file=sys.stderr)
                            server.server_close()
                            return 1
                    mesh_controller._become_proxy(reason="join-failed")
            if mesh_controller.state.role == "client" and mesh_controller.state.proxy_url:
                _start_mesh_sse_bridge(server, mesh_controller.state.proxy_url)
                from .mesh import hydrate_shared_runtime

                hydrate_shared_runtime(server, env_file=env_file)
        else:
            # Alias already held → join that proxy instead of opening a second TCP.
            if not mesh_controller.try_join_alias_owner():
                mesh_controller._become_proxy(reason="elected")
            elif mesh_controller.state.proxy_url:
                _start_mesh_sse_bridge(server, mesh_controller.state.proxy_url)
                from .mesh import hydrate_shared_runtime

                hydrate_shared_runtime(server, env_file=env_file)
        mesh_role = mesh_controller.state.role
        server.mesh = mesh_controller

    mesh_alias = None
    if mesh_controller is not None:
        mesh_alias = mesh_controller.state.mesh_mdns_name
    # Advertise switch identity even without mesh so discover can flag conflicts.
    advertise_switch = switch_id
    if advertise_switch is None:
        try:
            advertise_switch = switch_id_from_env(env_file)
        except MeshError:
            advertise_switch = None
    try:
        advertisement = mdns_mod.start_for_listener(
            bind_host=host,
            port=bound_port,
            force=mdns,
            instance_name=mdns_name,
            version=pkg_version.package_version(),
            panel=panel_enabled,
            input_count=active_switch.input_count,
            mesh=mesh_on,
            switch_id=advertise_switch,
            started_at=started_at if mesh_on else None,
            role=mesh_role if mesh_on else None,
            mesh_name=mesh_alias if mesh_on else None,
        )
    except mdns_mod.MdnsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        server.server_close()
        return 1
    server.mdns_name = advertisement.instance_name if advertisement is not None else None
    if mesh_controller is not None:
        mesh_controller.set_identity(mdns_name=server.mdns_name, base_url=base_url)
        # Role was chosen before mDNS registered; push current TXT + mesh alias
        # now that advertisement exists (promote/demote during setup no-oped).
        _refresh_mesh_advertise(advertisement, mesh_controller, panel_enabled, server)
        # Re-publish TXT when HDMI link flips (promote open / demote / failed TCP).
        # Also retry the mesh alias if a promote could not claim it yet (stale cache).
        _last_link = [_switch_link_for_server(server)]
        _last_alias_retry = [0.0]
        _orig_tick = mesh_controller.tick

        def _tick_with_link() -> None:
            _orig_tick()
            link = _switch_link_for_server(server)
            now = time.monotonic()
            snap = mesh_controller.snapshot()
            need_alias = (
                snap.get("role") == "proxy"
                and bool(snap.get("mesh_mdns_name"))
                and getattr(advertisement, "alias_info", None) is None
                and now - _last_alias_retry[0] >= 15.0
            )
            if link != _last_link[0] or need_alias:
                _last_link[0] = link
                if need_alias:
                    _last_alias_retry[0] = now
                _refresh_mesh_advertise(
                    advertisement, mesh_controller, panel_enabled, server
                )

        mesh_controller.tick = _tick_with_link  # type: ignore[method-assign]
        mesh_controller.start_watcher()

    def _callback_listener_identity() -> dict | None:
        """LAN HTTP + mDNS / mesh fields for Hubitat callback follow-up."""
        mesh_snap: dict = {}
        mesh = getattr(server, "mesh", None)
        if mesh is not None:
            try:
                mesh_snap = mesh.snapshot() or {}
            except Exception:
                mesh_snap = {}
        return callbacks.build_listener_identity(
            base_url=base_url,
            mdns_name=getattr(server, "mdns_name", None) or mesh_snap.get("mdns_name"),
            mesh_mdns_name=mesh_snap.get("mesh_mdns_name"),
            role=mesh_snap.get("role"),
            switch_id=mesh_snap.get("switch_id") or advertise_switch,
        )

    server.callbacks.identity_fn = _callback_listener_identity
    server.callbacks.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping.", file=sys.stderr)
        try:
            callbacks.notify_shutdown(server.callbacks)
        except Exception as exc:  # noqa: BLE001 - shutdown must still close the socket
            print(f"shutdown notify failed: {exc}", file=sys.stderr)
    finally:
        _stop_http_listener(server, advertisement)
    return 0


def _switch_link_for_server(server: "WebServer") -> str:
    from .mesh import switch_link_state

    return switch_link_state(getattr(server, "switch", None))


def _refresh_mesh_advertise(
    advertisement: object | None,
    mesh_controller: object | None,
    panel_enabled: bool,
    server: "WebServer",
) -> None:
    """Update instance TXT and (when proxy) the shared mesh failover alias."""
    if advertisement is None or mesh_controller is None:
        return
    from . import mdns as mdns_mod
    from .mesh import ROLE_PROXY

    ensure = getattr(mesh_controller, "ensure_mesh_mdns_name", None)
    if callable(ensure):
        ensure()
    snap = mesh_controller.snapshot()  # type: ignore[attr-defined]
    mesh_name = snap.get("mesh_mdns_name")
    link = _switch_link_for_server(server)
    set_link = getattr(mesh_controller, "set_switch_link", None)
    if callable(set_link):
        set_link(link)
    props = mdns_mod.build_properties(
        version=pkg_version.package_version(),
        panel=panel_enabled,
        input_count=server.switch.input_count,
        mesh=True,
        switch_id=snap.get("switch_id"),
        started_at=snap.get("started_at"),
        role=snap.get("role"),
        mesh_name=mesh_name,
        link=link,
    )
    update = getattr(advertisement, "update_properties", None)
    if callable(update):
        update(props)

    set_alias = getattr(advertisement, "set_alias", None)
    clear_alias = getattr(advertisement, "clear_alias", None)
    if snap.get("role") == ROLE_PROXY and mesh_name and callable(set_alias):
        alias_props = mdns_mod.build_properties(
            version=pkg_version.package_version(),
            panel=panel_enabled,
            input_count=server.switch.input_count,
            mesh=True,
            switch_id=snap.get("switch_id"),
            started_at=snap.get("started_at"),
            role=ROLE_PROXY,
            mesh_name=mesh_name,
            kind="mesh",
            link=link,
        )
        claimed = set_alias(str(mesh_name), alias_props)
        if claimed is False:
            # Alias already on the LAN → another live proxy. Join it; do not
            # stay as a second proxy with no failover name.
            handle = getattr(mesh_controller, "handle_alias_claim_failed", None)
            if callable(handle):
                handle()
    elif callable(clear_alias):
        clear_alias()


def _start_mesh_sse_bridge(server: "WebServer", proxy_url: str) -> None:
    """Forward proxy SSE events onto this mesh client's EventHub."""
    if server.mesh_sse_stop is not None:
        server.mesh_sse_stop.set()
    stop = threading.Event()
    server.mesh_sse_stop = stop

    def loop() -> None:
        import urllib.request

        url = f"{proxy_url.rstrip('/')}/events"
        while not stop.is_set():
            try:
                request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    event_name = "message"
                    data_lines: list[str] = []
                    while not stop.is_set():
                        raw = response.readline()
                        if not raw:
                            break
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            event_name = line[6:].strip() or "message"
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                            continue
                        if line == "" and data_lines:
                            payload_text = "\n".join(data_lines)
                            data_lines = []
                            name = event_name
                            event_name = "message"
                            if name in {"hello", "mesh"}:
                                continue
                            try:
                                payload = json.loads(payload_text) if payload_text else {}
                            except json.JSONDecodeError:
                                payload = {"raw": payload_text}
                            if isinstance(payload, dict):
                                payload.setdefault("event", name)
                                payload.setdefault("mesh_relay", True)
                                server.events.publish(payload)
            except Exception:
                if stop.wait(2.0):
                    return

    threading.Thread(target=loop, name="tesmart-mesh-sse", daemon=True).start()


def _stop_http_listener(server: WebServer, advertisement: object | None) -> None:
    """Close mDNS, event streams, and the socket. Further Ctrl-C is ignored."""
    import signal

    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (OSError, ValueError):
        pass
    # Drop the mesh failover alias before resign/notify so a newly elected
    # proxy can claim the same DNS-SD name without waiting out a conflict.
    if advertisement is not None:
        try:
            clear_alias = getattr(advertisement, "clear_alias", None)
            if callable(clear_alias):
                clear_alias()
        except BaseException:
            pass
    mesh = getattr(server, "mesh", None)
    if mesh is not None:
        try:
            mesh.stop()
        except BaseException:
            pass
    if server.mesh_sse_stop is not None:
        try:
            server.mesh_sse_stop.set()
        except BaseException:
            pass
    if advertisement is not None:
        try:
            close = getattr(advertisement, "close", None)
            if close is not None:
                close()
        except BaseException:
            pass
    try:
        server.events.close_all()
    except BaseException:
        pass
    try:
        server.callbacks.stop()
    except BaseException:
        pass
    try:
        owned = getattr(server.switch, "_mesh_owned_transport", None)
        if owned is not None:
            try:
                owned.close()
            except BaseException:
                pass
        close_switch = getattr(server.switch, "close", None)
        if callable(close_switch):
            close_switch()
    except BaseException:
        pass
    try:
        server.server_close()
    except BaseException:
        pass
