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
``/names``                   local input names (GET list; POST JSON to set)
``/version``                 package name, version, and feature tags
``/compat``                  GET/POST compatibility check (min server version + features)
``/events``                  Server-Sent Events stream for browser clients
``/callbacks``               GET list; POST {\"url\": \"...\", \"name\": \"...\"} register; DELETE remove;
                             POST {\"timeout\": 10} set wait; GET|POST /callbacks/health probe clients
``/panel``, ``/kit``, ``/api/...``  browser UIs; only with ``--panel`` (see :mod:`tesmart.panel`)
"""

from __future__ import annotations

import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

from . import callbacks, config, panel, version as pkg_version
from .banner import BannerBoard
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
    "/status": "active input plus saved IP, port, gateway, and mask",
    "/status?network=0": "active input only (skips the LAN queries)",
    "/version": "tesmartctl package version and feature tags",
    "/compat": "GET/POST {\"min_server\":\"0.0.7.9\",\"features\":[\"callbacks\"]} → compatible?",
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
    "/names": "GET local input names; POST {\"names\": {\"2\": \"Office\", \"3\": \"\"}} to set or clear",
    "/banner": "GET last LCD message; POST {\"text\",\"seconds\"|\"sticky\":true}; POST {\"show\":true}|{\"dismiss\":true}|{\"clear\":true}",
    "/events": "Server-Sent Events stream (input_changed, names_changed, banner); kit/panel use /api/events",
    "/callbacks": "GET list; POST {\"url\",\"name\"} register; POST {\"timeout\":10}; GET|POST /health probe; DELETE remove",
    "/panel": "browser control panel (only when started with --panel)",
    "/kit": "alternate floating HDMI UI kit (Pop out = chrome-less window on listener host)",
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
    if head == "names":
        if len(segments) != 1:
            raise LookupError(parts.path)
        return WebRequest("names", None, as_text, include_network)
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
    network = payload.get("network") or {}
    for key in ("ip", "port", "gateway", "mask"):
        if key in network:
            lines.append(f"{key}: {network[key]}")
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
            payload["mdns_name"] = getattr(self.server, "mdns_name", None)
            text = f"{payload['name']} {payload['version']}" if request.as_text else None
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
            state = self.server.peek_follow.set_mode(request.label or "off", request.seconds)
            payload = {"peek_follow": state["mode"]}
            if state["seconds"] is not None:
                payload["seconds"] = state["seconds"]
            panel._publish_peek_follow(self.server, state)
            self._send(HTTPStatus.OK, payload, request.as_text, state["mode"])
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
            payload["version"] = pkg_version.package_version()
            payload["mdns_name"] = getattr(self.server, "mdns_name", None)
            payload["rotate"] = config.rotate_info(self.server.env_file, input_count=self.server.switch.input_count)
            payload["peek"] = config.peek_info(self.server.env_file)
            payload["peek"]["follow"] = self.server.peek_follow.status()["mode"]
            # Remembered write-only settings (buzzer / LED / auto-detect) from this host.
            payload["last_sent"] = dict(self.server.last_sent)
            banner_payload = self.server.banner.payload()
            payload["banner"] = banner_payload["banner"]
            payload["banner_history"] = banner_payload["banner_history"]
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
        board.show(body.get("text"), body.get("seconds"), sticky=body.get("sticky"))
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
        return self.server.peeker.run(self._resolve(request), seconds).to_dict()

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
        with self.server.lock:
            if request.verb == "status":
                return switch.read_status(include_network=request.include_network).to_dict()
            if request.verb == "get":
                return {"active_input": switch.get_active_input()}
            number = self._resolve(request)
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
        # Shared so a peek started from /peek or the panel is visible to /api/state.
        self.peeker = Peeker(switch, self.lock)
        # Armed by /peek/once or /peek/always. The next plain input change peeks.
        self.peek_follow = PeekFollow()
        # Browser kit/panel subscribe via GET /events or /api/events (SSE).
        self.events = EventHub()
        self.callbacks = callbacks.CallbackHub(switch, self.lock, env_file, events=self.events)
        # LCD banner + history (restored from env file so kit icons return on startup).
        self.banner = BannerBoard(
            history=config.read_banner_history(env_file),
            persist=lambda entries: config.write_banner_history(entries, env_file),
        )
        # Instance name actually registered with mDNS, or None when not advertising.
        # Reconnecting clients compare this to the name they attached with.
        self.mdns_name: str | None = None

    def handle_error(self, request: object, client_address: tuple[str, int] | str) -> None:
        """Log client disconnects as one line; keep a traceback only for real bugs."""
        exc = sys.exc_info()[1]
        if isinstance(exc, self._CLIENT_GONE):
            host = client_address[0] if isinstance(client_address, tuple) else client_address
            print(f"{host} connection closed by peer", file=sys.stderr)
            return
        super().handle_error(request, client_address)


def serve_http(
    switch: TesmartSwitch,
    host: str,
    port: int,
    env_file: str | None = None,
    *,
    panel_enabled: bool = False,
    mdns: bool | None = None,
    mdns_name: str | None = None,
) -> int:
    """Serve the routes above on ``host:port`` until Ctrl-C.

    ``mdns`` is True/False for ``--mdns`` / ``--no-mdns``, or None for the
    default (advertise when the bind is not loopback-only).
    """
    from . import mdns as mdns_mod

    try:
        server = WebServer((host, port), switch, env_file, panel_enabled=panel_enabled)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    bound_host, bound_port = server.server_address[:2]
    print(
        f"HTTP listener on http://{bound_host}:{bound_port}/ (Ctrl-C to stop).",
        file=sys.stderr,
    )
    print(f"  HDMI switch:  {switch.endpoint}", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/input        read", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/status       read input and LAN settings", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/version      {pkg_version.package_version()}", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/compat       client/server compatibility check", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/names        local input names (GET / POST)", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/banner       brief LCD message (GET / POST)", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/events       Server-Sent Events (kit/panel push)", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/callbacks    change notifications (GET / POST / DELETE)", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/rotate       next input", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/input?set=3  switch", file=sys.stderr)
    if panel_enabled:
        print(f"  http://{bound_host}:{bound_port}/panel        control panel", file=sys.stderr)
        print(f"  http://{bound_host}:{bound_port}/kit          floating UI kit (Pop out = chrome-less)", file=sys.stderr)
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
    try:
        advertisement = mdns_mod.start_for_listener(
            bind_host=host,
            port=bound_port,
            force=mdns,
            instance_name=mdns_name,
            version=pkg_version.package_version(),
            panel=panel_enabled,
            input_count=switch.input_count,
        )
    except mdns_mod.MdnsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        server.server_close()
        return 1
    server.mdns_name = advertisement.instance_name if advertisement is not None else None

    server.callbacks.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping.", file=sys.stderr)
    finally:
        _stop_http_listener(server, advertisement)
    return 0


def _stop_http_listener(server: WebServer, advertisement: object | None) -> None:
    """Close mDNS, event streams, and the socket. Further Ctrl-C is ignored."""
    import signal

    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (OSError, ValueError):
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
        server.server_close()
    except BaseException:
        pass
