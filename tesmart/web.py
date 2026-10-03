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
``/panel``, ``/api/...``      browser control panel; only with ``--panel`` (see :mod:`tesmart.panel`)
"""

from __future__ import annotations

import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

from . import config, panel
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
    "/panel": "browser control panel (only when started with --panel)",
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
    return "\n".join(lines)


class _Handler(BaseHTTPRequestHandler):
    server: "WebServer"

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.client_address[0]} {fmt % args}", file=sys.stderr)

    def _handle(self) -> None:
        if panel.handle(self, self.command, self.path):
            return
        try:
            request = _parse_request(self.path)
        except LookupError:
            self._send(HTTPStatus.NOT_FOUND, {"error": "unknown route", "routes": ROUTES}, False)
            return
        except ValueError as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, False)
            return

        if request.verb == "help":
            self._send(HTTPStatus.OK, {"routes": ROUTES}, False)
            return
        if request.verb == "peek-mode":
            state = self.server.peek_follow.set_mode(request.label or "off", request.seconds)
            payload = {"peek_follow": state["mode"]}
            if state["seconds"] is not None:
                payload["seconds"] = state["seconds"]
            self._send(HTTPStatus.OK, payload, request.as_text, state["mode"])
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
        config.apply_names(payload, names, include_map=request.verb == "status")
        if request.verb == "status":
            payload["rotate"] = config.rotate_info(self.server.env_file, input_count=self.server.switch.input_count)
            payload["peek"] = config.peek_info(self.server.env_file)
            payload["peek"]["follow"] = self.server.peek_follow.status()["mode"]
        text = _status_text(payload) if request.verb == "status" else None
        self._send(HTTPStatus.OK, payload, request.as_text, text)

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
        # Last write-only values sent through this server. The switch cannot report them.
        self.last_sent: dict[str, object] = {"buzzer": None, "led_timeout": None, "auto_detect": None}
        self.lock = threading.Lock()
        # Shared so a peek started from /peek or the panel is visible to /api/state.
        self.peeker = Peeker(switch, self.lock)
        # Armed by /peek/once or /peek/always. The next plain input change peeks.
        self.peek_follow = PeekFollow()


def serve_http(
    switch: TesmartSwitch,
    host: str,
    port: int,
    env_file: str | None = None,
    *,
    panel_enabled: bool = False,
) -> int:
    """Serve the routes above on ``host:port`` until Ctrl-C."""
    try:
        server = WebServer((host, port), switch, env_file, panel_enabled=panel_enabled)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    bound_host, bound_port = server.server_address[:2]
    print(
        f"HTTP listener on http://{bound_host}:{bound_port}/ for {switch.endpoint} (Ctrl-C to stop).",
        file=sys.stderr,
    )
    print(f"  http://{bound_host}:{bound_port}/input        read", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/status       read input and LAN settings", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/rotate       next input", file=sys.stderr)
    print(f"  http://{bound_host}:{bound_port}/input?set=3  switch", file=sys.stderr)
    if panel_enabled:
        print(f"  http://{bound_host}:{bound_port}/panel        control panel", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(file=sys.stderr)
    finally:
        server.server_close()
    return 0
