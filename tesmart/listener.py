"""Line-oriented command listener for changing and reading the active input.

One command per line, one reply per line. The reply is always the input the
switch actually reports, which can differ from the number that was requested.

Commands
--------
``get``
    Read the active input.
``set <n>`` or just ``<n>``
    Switch to input ``n`` (1-based) and reply with the input the switch reports.
``next`` / ``previous`` / ``rotate [1,2,4]``
    Step along the saved cycle.
``peek <n> [seconds]``
    Show input ``n`` for a few seconds, then return. Replies once back.
``peek once [seconds]``
    The next ``set``, bare number, ``next``, or ``previous`` peeks, then
    later changes stick. ``peek always`` keeps doing that until ``peek off``.
``quit``
    Close this client. On stdin, that ends the process.
``help``
    Print the command list.

Blank lines are ignored. Anything else is answered with ``error: ...`` and the
listener keeps running.
"""

from __future__ import annotations

import json
import socket
import socketserver
import sys
import threading
from typing import TextIO

from . import config
from .peek import PeekFollow, Peeker, apply_follow
from .switch import SwitchError, TesmartSwitch, next_input, parse_input_list, previous_input
from .transport import TransportError

HELP = (
    "commands: get | set <n> | <n> | next | previous | rotate [1,2,4] | "
    "peek <n> [seconds] | peek once [seconds] | peek always [seconds] | peek off | quit"
)


class SessionQuit(Exception):
    """The client asked to close its session."""


def parse_bind(text: str) -> tuple[str, int]:
    """``9753`` -> ``127.0.0.1:9753``; ``0.0.0.0:9753`` stays as given."""
    if text.isdigit():
        return "127.0.0.1", int(text)
    host, sep, port_text = text.rpartition(":")
    if not sep:
        raise ValueError(f"expected HOST:PORT or a port number, got {text!r}")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"invalid port in {text!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range in {text!r}")
    return host or "127.0.0.1", port


def parse_command(line: str) -> tuple[str, int | None, tuple[int, ...] | None, str | None]:
    """Turn one input line into ``(verb, number, only, label)``.

    Verbs are ``get``, ``set``, ``rotate``, ``previous``, ``peek``, ``quit``,
    ``help``, and ``noop``. ``only`` is the optional cycle list on ``next`` /
    ``rotate``; on ``peek`` it carries the optional duration as ``(seconds,)``.
    """
    parts = line.strip().split()
    if not parts:
        return "noop", None, None, None
    word = parts[0].lower()
    if word == "peek":
        if len(parts) >= 2 and parts[1].lower() in {"once", "always", "off"}:
            mode = parts[1].lower()
            if mode == "off":
                if len(parts) != 2:
                    raise ValueError("usage: peek off")
                return "peek-mode", None, None, mode
            if len(parts) == 2:
                return "peek-mode", None, None, mode
            if len(parts) == 3 and parts[2].isdigit() and int(parts[2]) >= 1:
                return "peek-mode", None, (int(parts[2]),), mode
            raise ValueError("usage: peek once [seconds] | peek always [seconds] | peek off")
        if len(parts) not in {2, 3}:
            raise ValueError("usage: peek <input> [seconds]")
        seconds: tuple[int, ...] | None = None
        if len(parts) == 3:
            if not parts[2].isdigit() or int(parts[2]) < 1:
                raise ValueError("peek seconds must be a whole number, 1 or more")
            seconds = (int(parts[2]),)
        if parts[1].isdigit():
            return "peek", int(parts[1]), seconds, None
        return "peek", None, seconds, parts[1]
    if word in {"get", "port", "?", "status"}:
        if len(parts) != 1:
            raise ValueError(f"usage: {word}")
        return "get", None, None, None
    if word in {"quit", "exit"}:
        return "quit", None, None, None
    if word in {"help", "h"}:
        return "help", None, None, None
    if word in {"next", "rotate", "previous", "prev", "back"}:
        direction = "previous" if word in {"previous", "prev", "back"} else "rotate"
        if len(parts) == 1:
            return direction, None, None, None
        if len(parts) != 2:
            raise ValueError(f"usage: {word} [1,2,4]")
        return direction, None, parse_input_list(parts[1]), None
    if word in {"set", "input", "switch"}:
        if len(parts) != 2:
            raise ValueError("usage: set <input>")
        if parts[1].isdigit():
            return "set", int(parts[1]), None, None
        return "set", None, None, parts[1]
    if len(parts) == 1 and word.isdigit():
        return "set", int(word), None, None
    raise ValueError(f"unknown command {parts[0]!r}; try get, set <n>, next, peek <n>, or quit")


def format_reply(
    *,
    active: int | None = None,
    requested: int | None = None,
    previous: int | None = None,
    error: str | None = None,
    as_json: bool = False,
    names: dict[int, str] | None = None,
) -> str:
    if as_json:
        payload: dict[str, int | str] = {}
        if error is not None:
            payload["error"] = error
        else:
            payload["active_input"] = active  # type: ignore[assignment]
            if requested is not None:
                payload["requested"] = requested
            if previous is not None:
                payload["previous"] = previous
        return json.dumps(config.apply_names(payload, names or {}, include_map=False))
    if error is not None:
        return f"error: {error}"
    return str(active)


def _format_follow(result: dict, *, as_json: bool, names: dict) -> str:
    if as_json:
        return json.dumps(config.apply_names(result, names))
    return str(result.get("active_input"))


def dispatch(
    switch: TesmartSwitch,
    line: str,
    *,
    as_json: bool = False,
    env_file: str | None = None,
    follow: PeekFollow | None = None,
    peeker: Peeker | None = None,
) -> str | None:
    """Run one command. ``None`` means the line was blank and needs no reply.

    ``follow`` and ``peeker`` are shared for the life of one listener so
    ``peek once`` applies to a later ``set`` on any client of that process.
    """
    verb, number, only, label = parse_command(line)
    names = config.read_names(env_file) if as_json or (label and verb != "peek-mode") else {}
    if verb == "noop":
        return None
    if verb == "quit":
        raise SessionQuit()
    if verb == "help":
        return json.dumps({"help": HELP}) if as_json else HELP
    if verb == "peek-mode":
        if follow is None:
            raise SwitchError("peek once, peek always, and peek off are listener commands")
        state = follow.set_mode(label or "off", only[0] if only else None)
        if as_json:
            return json.dumps({"peek_follow": state["mode"], "seconds": state["seconds"]})
        if state["seconds"] is None:
            return f"peek {state['mode']}"
        return f"peek {state['mode']} {state['seconds']}"
    if verb == "get":
        return format_reply(active=switch.get_active_input(), as_json=as_json, names=names)
    if verb in {"rotate", "previous"}:
        if only is None:
            only = config.read_rotate(env_file)
        direction = -1 if verb == "previous" else 1
        current = switch.get_active_input()
        step = previous_input if direction < 0 else next_input
        target = step(current, switch.input_count, only)
        if follow is not None and peeker is not None:
            followed = apply_follow(
                follow, peeker, target, config.read_peek_seconds(env_file), block=True
            )
            if followed is not None:
                followed["previous"] = current
                return _format_follow(followed, as_json=as_json, names=names)
        reported = switch.set_input(target, verify=True)
        reported = reported if reported is not None else target
        return format_reply(active=reported, requested=reported, previous=current, as_json=as_json, names=names)
    if label:
        number = config.resolve_named_input(label, input_count=switch.input_count, names=config.read_names(env_file))
    if verb == "peek":
        seconds = only[0] if only else config.read_peek_seconds(env_file)
        worker = peeker if peeker is not None else Peeker(switch)
        result = worker.run(number if number is not None else 0, seconds)
        if as_json:
            return json.dumps(config.apply_names(result.to_dict(), names))
        return str(result.active_input)
    if number is None:
        raise SwitchError("missing input number")
    if follow is not None and peeker is not None:
        followed = apply_follow(follow, peeker, number, config.read_peek_seconds(env_file), block=True)
        if followed is not None:
            return _format_follow(followed, as_json=as_json, names=names)
    reported = switch.set_input(number, verify=True)
    return format_reply(active=reported, requested=number, as_json=as_json, names=names)


def _reply_for(
    switch: TesmartSwitch,
    line: str,
    *,
    as_json: bool,
    env_file: str | None = None,
    follow: PeekFollow | None = None,
    peeker: Peeker | None = None,
) -> str | None:
    try:
        return dispatch(switch, line, as_json=as_json, env_file=env_file, follow=follow, peeker=peeker)
    except (SwitchError, TransportError, config.ConfigError, ValueError) as exc:
        return format_reply(error=str(exc), as_json=as_json)


def serve_stdio(switch: TesmartSwitch, *, as_json: bool = False, stdin: TextIO | None = None, stdout: TextIO | None = None, env_file: str | None = None) -> int:
    """Read commands from stdin until EOF, ``quit``, or Ctrl-C."""
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    print(f"Command listener on stdin for {switch.endpoint} (Ctrl-C or 'quit' to stop).", file=sys.stderr)
    print(HELP, file=sys.stderr)
    follow = PeekFollow()
    peeker = Peeker(switch)
    try:
        for line in source:
            try:
                reply = _reply_for(
                    switch, line, as_json=as_json, env_file=env_file, follow=follow, peeker=peeker
                )
            except SessionQuit:
                break
            if reply is None:
                continue
            print(reply, file=sink, flush=True)
    except KeyboardInterrupt:
        print(file=sys.stderr)
    return 0


class _CommandHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server: CommandServer = self.server  # type: ignore[assignment]
        peer = self.client_address[0]
        print(f"client {peer} connected", file=sys.stderr)
        try:
            while True:
                raw = self.rfile.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace")
                try:
                    with server.lock:
                        reply = _reply_for(
                            server.switch,
                            line,
                            as_json=server.as_json,
                            env_file=server.env_file,
                            follow=server.peek_follow,
                            peeker=server.peeker,
                        )
                except SessionQuit:
                    break
                if reply is None:
                    continue
                self.wfile.write((reply + "\n").encode("utf-8"))
                self.wfile.flush()
        except (ConnectionError, socket.timeout):
            pass
        finally:
            print(f"client {peer} disconnected", file=sys.stderr)


class CommandServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], switch: TesmartSwitch, *, as_json: bool, env_file: str | None = None):
        super().__init__(address, _CommandHandler)
        self.switch = switch
        self.as_json = as_json
        self.env_file = env_file
        self.lock = threading.Lock()
        self.peek_follow = PeekFollow()
        # Not given the server lock: dispatch already runs inside that lock,
        # and the lock is not re-entrant.
        self.peeker = Peeker(switch)


def serve_tcp(switch: TesmartSwitch, host: str, port: int, *, as_json: bool = False, env_file: str | None = None) -> int:
    """Accept line commands on ``host:port`` until Ctrl-C."""
    try:
        server = CommandServer((host, port), switch, as_json=as_json, env_file=env_file)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    bound_host, bound_port = server.server_address[:2]
    print(
        f"Command listener on {bound_host}:{bound_port} for {switch.endpoint} (Ctrl-C to stop).",
        file=sys.stderr,
    )
    print(HELP, file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(file=sys.stderr)
    finally:
        server.server_close()
    return 0
