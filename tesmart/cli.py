"""``tesmartctl`` command-line interface.

Connection settings are resolved by :mod:`tesmart.config` in this order:
command-line flag, env file (``~/.config/tesmartctl/env``), environment
variable (``TESMART_HOST`` / ``TESMART_PORT`` / ``TESMART_SERIAL`` /
``TESMART_INPUTS``), built-in default.  Use ``tesmartctl config set host ...``
to persist a default.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from . import config, notices
from .examples import GLOBAL_EXAMPLES, all_examples_text, epilog_for, format_examples
from .listener import parse_bind, serve_stdio, serve_tcp
from .peek import Peeker
from .web import serve_http
from .protocol import LedTimeout, parse_frames, parse_hex_string
from .switch import SwitchError, TesmartSwitch, parse_input_list
from .transport import (
    DEFAULT_BAUD_RATE,
    DEFAULT_TCP_PORT,
    SerialTransport,
    TcpTransport,
    Transport,
    TransportError,
)


# --- argument parsing -------------------------------------------------------


def _bool_arg(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in {"on", "1", "true", "yes", "enable", "enabled", "unmute"}:
        return True
    if lowered in {"off", "0", "false", "no", "disable", "disabled", "mute"}:
        return False
    raise argparse.ArgumentTypeError(f"expected on/off, got {text!r}")


def _led_timeout_arg(text: str) -> LedTimeout:
    try:
        return LedTimeout.from_text(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _only_arg(text: str) -> tuple[int, ...]:
    try:
        return parse_input_list(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _bind_arg(text: str) -> tuple[str, int]:
    try:
        return parse_bind(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


_RAW = argparse.RawDescriptionHelpFormatter


def _add(sub: argparse._SubParsersAction, name: str, help: str, **kwargs: object) -> argparse.ArgumentParser:
    """``add_parser`` with the command's worked examples appended to ``--help``."""
    return sub.add_parser(name, help=help, epilog=epilog_for(name), formatter_class=_RAW, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tesmartctl",
        description="Control a TESmart 8x1 / 16x1 HDMI switch over LAN or RS232.",
        epilog="examples:\n" + format_examples(GLOBAL_EXAMPLES),
        formatter_class=_RAW,
    )
    conn = parser.add_argument_group(
        "connection",
        "Precedence: flag > env file > environment variable > default. "
        "Persist defaults with `tesmartctl config set`.",
    )
    conn.add_argument(
        "--host", default=None,
        help=f"switch IP/hostname (default {config.DEFAULT_HOST}, env TESMART_HOST)",
    )
    conn.add_argument(
        "--port", type=int, default=None,
        help=f"TCP port (default {DEFAULT_TCP_PORT}, env TESMART_PORT)",
    )
    conn.add_argument(
        "--serial", default=None,
        help="serial device (e.g. /dev/tty.usbserial-xxx); overrides --host (env TESMART_SERIAL)",
    )
    conn.add_argument("--baud", type=int, default=DEFAULT_BAUD_RATE, help=argparse.SUPPRESS)
    conn.add_argument(
        "--inputs", type=int, default=None,
        help=f"number of inputs on the switch (default {config.DEFAULT_INPUTS}, env TESMART_INPUTS)",
    )
    conn.add_argument(
        "--env-file", default=None,
        help=f"settings file (default {config.DEFAULT_ENV_FILE}, env {config.ENV_FILE_VAR})",
    )
    conn.add_argument(
        "--timeout", type=float, default=1.5,
        help="seconds to wait for a reply (default 1.5)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    _add(sub, "status", "show every readable parameter").add_argument(
        "--no-network", action="store_true", help="skip the ASCII network-config queries"
    )

    p_input = _add(sub, "input", "get or set the active input")
    p_input.add_argument("number", nargs="?", help="input to select (1-N or a local name), or 'next' to rotate; omit to read")
    p_input.add_argument("--no-verify", action="store_true", help="do not read back after switching")
    p_input.add_argument(
        "--only", type=_only_arg, default=None, metavar="LIST",
        help="with 'next', rotate only through these inputs, e.g. 1,2,4",
    )

    p_rotate = _add(
        sub, "rotate",
        "switch to the next input (wraps after the last; the switch cannot report which inputs have a signal)",
    )
    p_rotate.add_argument(
        "--only", type=_only_arg, default=None, metavar="LIST",
        help="rotate only through these inputs, e.g. 1,2,4",
    )
    p_rotate.add_argument(
        "--save", action="store_true",
        help="store --only as the rotate cycle used by later rotate commands and by status",
    )
    p_rotate.add_argument(
        "--clear", action="store_true",
        help="forget the saved cycle so rotate walks every input; does not change the switch",
    )

    for step_name, step_help in (
        ("next", "move to the next input in the saved cycle"),
        ("previous", "move to the previous input in the saved cycle"),
    ):
        step = _add(sub, step_name, step_help)
        step.add_argument(
            "--only", type=_only_arg, default=None, metavar="LIST",
            help="use this list for this step only; does not change the saved cycle",
        )

    p_peek = _add(sub, "peek", "show another input for a few seconds, then come back (not a cycle)")
    p_peek.add_argument("number", help="input to look at (1-N or a local name)")
    p_peek.add_argument(
        "--seconds", type=int, default=None, metavar="S",
        help=f"how long to stay there (default {config.DEFAULT_PEEK_SECONDS}, or the saved value)",
    )
    p_peek.add_argument("--save", action="store_true", help="store --seconds as the default for later peeks")

    p_cycle = _add(sub, "cycle", "choose the inputs to cycle and whether that happens automatically or by next/previous")
    cycle_sub = p_cycle.add_subparsers(dest="cycle_command", required=True, metavar="<show|inputs|mode|clear|run>")
    cycle_sub.add_parser("show", help="print the saved cycle, mode, and interval")
    p_cycle_inputs = cycle_sub.add_parser("inputs", help="save the inputs next/previous/automatic walk, e.g. 1,2,4")
    p_cycle_inputs.add_argument("list", type=_only_arg, help="comma-separated inputs, e.g. 1,2,4")
    p_cycle_mode = cycle_sub.add_parser("mode", help="manual (next/previous) or automatic (timed)")
    p_cycle_mode.add_argument("mode", choices=["manual", "automatic", "auto"])
    p_cycle_mode.add_argument("--every", type=int, metavar="SECONDS", help="seconds between steps when mode is automatic")
    cycle_sub.add_parser("clear", help="forget the saved input list; the cycle becomes every input")
    cycle_sub.add_parser("run", help="step through the saved inputs on the timer (mode must be automatic)")

    _add(sub, "buzzer", "mute/unmute the buzzer (write-only)").add_argument(
        "state", type=_bool_arg, help="on|off"
    )
    _add(sub, "led", "set front-panel LED timeout (write-only)").add_argument(
        "timeout", type=_led_timeout_arg, help="never|10|30"
    )
    _add(sub, "autodetect", "enable/disable auto input detection (write-only)").add_argument(
        "state", type=_bool_arg, help="on|off"
    )

    p_net = _add(sub, "net", "read or write LAN settings (ASCII protocol)")
    net_sub = p_net.add_subparsers(dest="net_command", required=True, metavar="<show|set>")
    net_sub.add_parser("show", help="read IP, port, gateway, mask")
    p_net_set = net_sub.add_parser(
        "set", help="write LAN settings (applied after power cycle)",
        epilog=epilog_for("net set"), formatter_class=_RAW,
    )
    p_net_set.add_argument("--ip", help="new IP address for the switch")
    p_net_set.add_argument("--tcp-port", dest="net_port", help="TCP listen port for the switch itself")
    p_net_set.add_argument("--gateway", help="default gateway, e.g. 192.168.1.1")
    p_net_set.add_argument("--mask", help="subnet mask, e.g. 255.255.255.0")
    p_net_set.add_argument(
        "--yes", action="store_true",
        help="required: you accept that a bad value can make the switch unreachable, that only RS232 can "
             "recover it, and that you do this at your own risk with no liability on this software",
    )
    p_net_set.add_argument(
        "--save-default", action="store_true",
        help="after the switch acknowledges, store the new --ip (and --tcp-port) as the CLI default",
    )

    p_cfg = _add(sub, "config", "show or persist CLI defaults (no connection needed)")
    cfg_sub = p_cfg.add_subparsers(dest="config_command", required=True, metavar="<show|set|unset|path>")
    cfg_sub.add_parser("show", help="print effective settings and where each came from")
    cfg_sub.add_parser("path", help="print the env file location")
    p_cfg_set = cfg_sub.add_parser(
        "set", help="persist a default, e.g. `config set host 192.168.1.50`",
        epilog=epilog_for("config set"), formatter_class=_RAW,
    )
    p_cfg_set.add_argument("name", choices=sorted(config.SETTINGS))
    p_cfg_set.add_argument("value")
    cfg_sub.add_parser("unset", help="remove a persisted default").add_argument(
        "name", choices=sorted(config.SETTINGS)
    )

    p_name = _add(sub, "name", "label an input locally (stored in the env file, not on the switch)")
    name_sub = p_name.add_subparsers(dest="name_command", required=True, metavar="<list|set|unset>")
    name_sub.add_parser("list", help="show saved input names")
    p_name_set = name_sub.add_parser(
        "set", help="name an input, e.g. `name set 2 Office`",
        epilog=epilog_for("name set"), formatter_class=_RAW,
    )
    p_name_set.add_argument("number", type=int, help="input number (1-N)")
    p_name_set.add_argument("label", nargs="+", help="display name; not sent to the switch")
    name_sub.add_parser("unset", help="remove a local input name").add_argument("number", type=int)

    p_raw = _add(sub, "raw", "send arbitrary hex bytes and print the reply")
    p_raw.add_argument("hex", nargs="+", help='bytes, e.g. "AA BB 03 10 00 EE"')
    p_raw.add_argument("--listen", type=float, default=None, help="seconds to collect replies")

    _add(sub, "monitor", "print the input number whenever the switch reports a change")

    _add(sub, "examples", "print worked examples for every command (no connection needed)")

    p_listen = _add(sub, "listen", "stay running and accept commands to read or change the active input")
    listen_mode = p_listen.add_mutually_exclusive_group()
    listen_mode.add_argument(
        "--bind",
        default=None,
        type=_bind_arg,
        metavar="[HOST:]PORT",
        help="raw TCP, one line per command (e.g. 127.0.0.1:9753 or just 9753)",
    )
    listen_mode.add_argument(
        "--http",
        default=None,
        type=_bind_arg,
        metavar="[HOST:]PORT",
        help="HTTP with URL params, e.g. http://127.0.0.1:8080/input?set=3",
    )
    p_listen.add_argument(
        "--panel",
        action="store_true",
        help="with --http: also serve a browser control panel at /panel (off by default)",
    )
    p_listen.description = "Without --bind or --http, commands are read from stdin."

    return parser


# --- helpers ----------------------------------------------------------------


def resolve_settings(args: argparse.Namespace) -> config.Settings:
    return config.resolve(
        args.env_file, host=args.host, port=args.port, serial=args.serial, inputs=args.inputs
    )


def open_transport(settings: config.Settings) -> Transport:
    if settings.serial:
        return SerialTransport(settings.serial, DEFAULT_BAUD_RATE)
    return TcpTransport(settings.host, settings.port)


def emit(args: argparse.Namespace, data: dict[str, Any], text: str) -> None:
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(text)


def _named(number: object, name: object) -> str:
    if number is None:
        return "unknown (no reply)"
    return f"{number} ({name})" if name else str(number)


def _render_status(status: dict[str, Any]) -> str:
    lines = [f"Endpoint:      {status['endpoint']}"]
    lines.append(
        f"Active input:  {_named(status['active_input'], status.get('active_name'))} of {status['input_count']}"
    )
    names = status.get("names") or {}
    if names:
        lines.append("Input names (stored locally, not on the switch):")
        for number, label in names.items():
            lines.append(f"  {number:<2} {label}")
    network = status.get("network")
    if network:
        lines.append("Network (persisted; applied after power cycle):")
        lines.append(f"  IP:          {network['ip'] or 'n/a'}")
        lines.append(f"  Port:        {network['port'] or 'n/a'}")
        lines.append(f"  Gateway:     {network['gateway'] or 'n/a'}")
        lines.append(f"  Mask:        {network['mask'] or 'n/a'}")
    if status.get("unsolicited_reports"):
        lines.append(f"Unsolicited input reports seen: {status['unsolicited_reports']}")
    rotate = status.get("rotate") or {}
    cycle = rotate.get("cycle") or []
    cycle_text = ", ".join(str(number) for number in cycle) if cycle else "none"
    chosen = "chosen inputs" if rotate.get("configured") else "all inputs"
    if rotate.get("mode") == "automatic":
        lines.append(
            f"Cycle:         automatic every {rotate.get('seconds')}s; {cycle_text} ({chosen}); start with `cycle run`"
        )
    else:
        lines.append(f"Cycle:         manual (next/previous); {cycle_text} ({chosen})")
    peek = status.get("peek") or {}
    if peek:
        lines.append(f"Peek:          {peek.get('seconds')}s on another input, then back (`peek N`)")
    lines.append("Write-only (not readable from the device): buzzer, LED timeout, auto-detect")
    return "\n".join(lines)


# --- command handlers -------------------------------------------------------


def _names(args: argparse.Namespace) -> dict[int, str]:
    return config.read_names(args.env_file)


def cmd_status(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    status = switch.read_status(include_network=not args.no_network).to_dict()
    config.apply_names(status, _names(args), include_map=True)
    status["rotate"] = config.rotate_info(args.env_file, input_count=switch.input_count)
    status["peek"] = config.peek_info(args.env_file)
    emit(args, status, _render_status(status))
    return 0


def cmd_peek(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    names = _names(args)
    number = config.resolve_named_input(args.number, input_count=switch.input_count, names=names)
    if args.save:
        if args.seconds is None:
            print("error: --save needs --seconds, for example --seconds 8 --save", file=sys.stderr)
            return 2
        config.set_peek_seconds(args.seconds, args.env_file)
    seconds = args.seconds if args.seconds is not None else config.read_peek_seconds(args.env_file)
    peeker = Peeker(switch)
    started = peeker.start(number, seconds)
    if not args.json:
        print(
            f"Peeking at {_named(started['peeked'], names.get(started['peeked']))} for {seconds}s, "
            f"then back to {_named(started['previous'], names.get(started['previous']))} (Ctrl-C returns now)",
            flush=True,
        )
    result = peeker.wait()
    data = config.apply_names(result.to_dict(), names)
    current = _named(result.active_input, data.get("active_name"))
    if result.interrupted:
        emit(args, data, f"Input was changed to {current} during the peek; left as is")
        return 1
    emit(args, data, f"Active input: {current}")
    return 0


def _emit_rotate(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    if getattr(args, "clear", False):
        if args.only is not None or getattr(args, "save", False):
            print("error: --clear does not take --only or --save", file=sys.stderr)
            return 2
        path = config.clear_rotate(args.env_file)
        emit(args, {"rotate": config.rotate_info(args.env_file, input_count=switch.input_count)}, f"Cleared saved rotate cycle in {path}")
        return 0
    if getattr(args, "save", False) and args.only is None:
        print("error: --save needs --only, for example --only 1,2,4 --save", file=sys.stderr)
        return 2
    if getattr(args, "save", False):
        config.set_rotate(args.only, args.env_file, input_count=switch.input_count)
    only = args.only if args.only is not None else config.read_rotate(args.env_file)
    previous, reported = switch.rotate(only, direction=getattr(args, "direction", 1))
    data = {"previous": previous, "requested": reported, "active_input": reported}
    config.apply_names(data, _names(args))
    current = _named(reported, data.get("active_name"))
    if reported == previous:
        emit(args, data, f"Active input: {current}")
    else:
        emit(args, data, f"Active input: {current} (was {_named(previous, data.get('previous_name'))})")
    return 0 if reported is not None else 1


def cmd_rotate(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    return _emit_rotate(switch, args)


def cmd_next(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    args.direction = 1
    return _emit_rotate(switch, args)


def cmd_previous(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    args.direction = -1
    return _emit_rotate(switch, args)


def _cycle_summary(env_file: str | None, input_count: int) -> tuple[dict, str]:
    info = config.rotate_info(env_file, input_count=input_count)
    cycle_text = ", ".join(str(number) for number in info["cycle"])
    chosen = "chosen inputs" if info["configured"] else "all inputs"
    if info["mode"] == "automatic":
        text = f"automatic every {info['seconds']}s; {cycle_text} ({chosen})"
    else:
        text = f"manual (next/previous); {cycle_text} ({chosen})"
    return info, text


def cmd_cycle_config(args: argparse.Namespace) -> int:
    """Saved cycle settings. Does not talk to the switch."""
    settings = resolve_settings(args)
    if args.cycle_command == "show":
        info, text = _cycle_summary(args.env_file, settings.inputs)
        emit(args, {"rotate": info, "env_file": str(settings.env_file)}, text)
        return 0
    if args.cycle_command == "clear":
        path = config.clear_rotate(args.env_file)
        info, text = _cycle_summary(args.env_file, settings.inputs)
        emit(args, {"rotate": info, "env_file": str(path)}, f"Cleared saved inputs in {path}\n{text}")
        return 0
    if args.cycle_command == "inputs":
        path = config.set_rotate(args.list, args.env_file, input_count=settings.inputs)
        info, text = _cycle_summary(args.env_file, settings.inputs)
        emit(args, {"rotate": info, "env_file": str(path)}, f"Saved cycle inputs in {path}\n{text}")
        return 0
    path = config.set_cycle_mode(args.mode, args.env_file, seconds=args.every)
    info, text = _cycle_summary(args.env_file, settings.inputs)
    emit(args, {"rotate": info, "env_file": str(path)}, f"Saved cycle mode in {path}\n{text}")
    return 0


def cmd_cycle_run(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    info = config.rotate_info(args.env_file, input_count=switch.input_count)
    if info["mode"] != "automatic":
        print(
            "error: cycle mode is manual. Use `next` and `previous`, or `cycle mode automatic --every 10`.",
            file=sys.stderr,
        )
        return 2
    seconds = info["seconds"] or config.DEFAULT_CYCLE_SECONDS
    cycle_text = ", ".join(str(number) for number in info["cycle"])
    print(f"Cycling {cycle_text} every {seconds}s (Ctrl-C to stop).", file=sys.stderr)
    try:
        while True:
            args.direction = 1
            args.only = None
            args.save = False
            args.clear = False
            _emit_rotate(switch, args)
            time.sleep(seconds)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 0


def cmd_input(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    if args.number is None:
        if args.only is not None:
            print("error: --only applies to 'input next' or 'rotate'", file=sys.stderr)
            return 2
        active = switch.get_active_input()
        data = {"active_input": active}
        config.apply_names(data, _names(args))
        emit(args, data, f"Active input: {_named(active, data.get('active_name'))}")
        return 0
    if args.number.lower() == "next":
        return _emit_rotate(switch, args)
    try:
        number = config.resolve_named_input(args.number, input_count=switch.input_count, names=_names(args))
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.only is not None:
        print("error: --only applies to 'input next' or 'rotate'", file=sys.stderr)
        return 2
    reported = switch.set_input(number, verify=not args.no_verify)
    data = {"requested": number, "active_input": reported}
    config.apply_names(data, _names(args))
    requested = _named(number, data.get("requested_name"))
    if reported is None:
        emit(args, data, f"Sent switch to input {requested} (not verified)")
    elif reported == number:
        emit(args, data, f"Active input: {_named(reported, data.get('active_name'))}")
    else:
        emit(args, data, f"Requested input {requested} but switch reports {_named(reported, data.get('active_name'))}")
        return 1
    return 0


def cmd_buzzer(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    switch.set_buzzer(args.state)
    emit(args, {"buzzer": args.state}, f"Buzzer {'unmuted' if args.state else 'muted'}")
    return 0


def cmd_led(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    switch.set_led_timeout(args.timeout)
    emit(args, {"led_timeout": args.timeout.describe()}, f"LED timeout set to {args.timeout.describe()}")
    return 0


def cmd_autodetect(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    switch.set_auto_detect(args.state)
    emit(args, {"auto_detect": args.state}, f"Auto input detection {'enabled' if args.state else 'disabled'}")
    return 0


def cmd_net(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    if args.net_command == "show":
        config = switch.get_network_config().to_dict()
        text = "\n".join(f"{key:<8} {value or 'n/a'}" for key, value in config.items())
        emit(args, config, text)
        return 0

    values = {k: v for k, v in (("ip", args.ip), ("port", args.net_port), ("gateway", args.gateway), ("mask", args.mask)) if v}
    if not values:
        print("error: nothing to set; pass --ip/--tcp-port/--gateway/--mask", file=sys.stderr)
        return 2
    summary = ", ".join(f"{key}={value}" for key, value in values.items())
    if not args.yes:
        print(
            f"WARNING (1 of 2): refusing to write {summary} without --yes.\n"
            f"{notices.network_risk_text()}\n"
            "Re-run with --yes to accept this and continue.",
            file=sys.stderr,
        )
        return 2
    print(
        f"WARNING (2 of 2): writing {summary} to the switch now.\n{notices.network_risk_text()}",
        file=sys.stderr,
    )
    result = switch.write_network_config(**values)
    lines = []
    for key in values:
        ack = "OK" if result.acknowledged.get(key) else "NO ACK"
        check = "stored" if result.verified.get(key) else "MISMATCH"
        lines.append(f"{key:<8} {ack:<7} read back: {result.stored.get(key) or 'n/a':<18} {check}")
    if result.all_verified:
        lines.append("Confirmed: the switch reports the new values. " + notices.NETWORK_AFTER_WRITE)
    else:
        lines.append(
            "NOT confirmed: the read-back differs from what was requested. Do not power-cycle until "
            "`net show` reports the values you expect."
        )
    saved: dict[str, str] = {}
    if args.save_default and result.all_verified:
        if args.ip:
            saved["host"] = args.ip
        if args.net_port:
            saved["port"] = args.net_port
        for name, value in saved.items():
            path = config.set_default(name, value, args.env_file)
        if saved:
            lines.append(f"Saved default {', '.join(f'{k}={v}' for k, v in saved.items())} to {path}")
    elif args.save_default:
        lines.append("Not saving defaults because the switch did not confirm every value.")
    data = result.to_dict()
    data["saved_defaults"] = saved
    emit(args, data, "\n".join(lines))
    return 0 if result.all_verified else 1


def cmd_raw(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    payload = parse_hex_string(" ".join(args.hex))
    reply = switch.send_raw(payload, listen=args.listen)
    frames, rest = parse_frames(reply)
    data = {
        "sent": payload.hex(" ").upper(),
        "received": reply.hex(" ").upper(),
        "frames": [{"command": f"0x{f.command:02X}", "value": f"0x{f.value:02X}", "terminator": f"0x{f.terminator:02X}"} for f in frames],
        "ascii": rest.decode("ascii", errors="replace") if rest else "",
    }
    lines = [f"Sent:     {data['sent']}", f"Received: {data['received'] or '(nothing)'}"]
    for frame in frames:
        note = f" -> input {frame.input_number}" if frame.is_input_report else ""
        lines.append(f"  frame cmd=0x{frame.command:02X} value=0x{frame.value:02X} term=0x{frame.terminator:02X}{note}")
    if rest:
        lines.append(f"  ascii: {data['ascii']!r}")
    emit(args, data, "\n".join(lines))
    return 0


def cmd_monitor(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    print(f"Listening on {switch.endpoint} for input changes (Ctrl-C to stop)...", file=sys.stderr)
    try:
        for number in switch.monitor():
            emit(args, {"active_input": number}, f"Active input: {number}")
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_listen(switch: TesmartSwitch, args: argparse.Namespace) -> int:
    if args.http is not None:
        host, port = args.http
        return serve_http(switch, host, port, args.env_file, panel_enabled=args.panel)
    if args.panel:
        raise SystemExit("--panel needs --http PORT")
    if args.bind is None:
        return serve_stdio(switch, as_json=args.json, env_file=args.env_file)
    host, port = args.bind
    return serve_tcp(switch, host, port, as_json=args.json, env_file=args.env_file)


def cmd_name(args: argparse.Namespace) -> int:
    """Runs without a device connection. Names never leave this machine."""
    settings = resolve_settings(args)
    if args.name_command == "list":
        names = config.read_names(args.env_file)
        data = {"names": {str(number): label for number, label in names.items()}, "env_file": str(settings.env_file)}
        if not names:
            emit(args, data, f"No input names in {settings.env_file}")
        else:
            lines = [f"{number:<2} {label}" for number, label in names.items()]
            emit(args, data, "\n".join(lines))
        return 0
    if args.name_command == "unset":
        path = config.unset_input_name(args.number, args.env_file)
        emit(args, {"removed": args.number, "env_file": str(path)}, f"Removed name for input {args.number} from {path}")
        return 0
    label = " ".join(args.label)
    path = config.set_input_name(args.number, label, args.env_file, input_count=settings.inputs)
    stored = config.lookup_name(args.number, env_file=args.env_file)
    emit(args, {"input": args.number, "name": stored, "env_file": str(path)}, f"Named input {args.number} {stored!r} in {path}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Runs without a device connection."""
    if args.config_command == "path":
        print(config.env_file_path(args.env_file))
        return 0
    if args.config_command == "set":
        path = config.set_default(args.name, args.value, args.env_file)
        emit(args, {"saved": {args.name: args.value}, "env_file": str(path)}, f"Saved {args.name}={args.value} to {path}")
        return 0
    if args.config_command == "unset":
        path = config.unset_default(args.name, args.env_file)
        emit(args, {"removed": args.name, "env_file": str(path)}, f"Removed {args.name} from {path}")
        return 0
    settings = resolve_settings(args)
    data = settings.to_dict()
    width = max(len(name) for name in config.SETTINGS)
    lines = [f"Env file: {settings.env_file}{'' if settings.env_file.exists() else ' (not present)'}"]
    for name in config.SETTINGS:
        value = data[name]
        lines.append(f"  {name:<{width}}  {value if value is not None else '-':<22} ({settings.sources[name]})")
    emit(args, data, "\n".join(lines))
    return 0


HANDLERS = {
    "status": cmd_status,
    "input": cmd_input,
    "rotate": cmd_rotate,
    "next": cmd_next,
    "previous": cmd_previous,
    "peek": cmd_peek,
    "cycle": cmd_cycle_run,
    "buzzer": cmd_buzzer,
    "led": cmd_led,
    "autodetect": cmd_autodetect,
    "net": cmd_net,
    "raw": cmd_raw,
    "monitor": cmd_monitor,
    "listen": cmd_listen,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "config":
            return cmd_config(args)
        if args.command == "name":
            return cmd_name(args)
        if args.command == "cycle" and args.cycle_command != "run":
            return cmd_cycle_config(args)
        if args.command == "examples":
            print(all_examples_text())
            return 0
        settings = resolve_settings(args)
        with open_transport(settings) as transport:
            switch = TesmartSwitch(transport, input_count=settings.inputs, reply_timeout=args.timeout)
            return HANDLERS[args.command](switch, args)
    except (TransportError, SwitchError, config.ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
