"""Worked examples for every ``tesmartctl`` command.

Shown by ``tesmartctl <command> --help`` and ``tesmartctl examples``. Keeping
them here, rather than inline in the parser, means the help text and the
README describe the same invocations.
"""

from __future__ import annotations

# command -> list of (invocation, what it does)
EXAMPLES: dict[str, list[tuple[str, str]]] = {
    "status": [
        ("tesmartctl status", "active input, local names, saved IP/port/gateway/mask"),
        ("tesmartctl status --no-network", "active input only; skips the four LAN queries"),
        ("tesmartctl --json status", "same data as one JSON object"),
        ("tesmartctl --host 192.168.1.10 status", "ask a switch at a different address"),
    ],
    "input": [
        ("tesmartctl input", "print the active input"),
        ("tesmartctl input 3", "switch to HDMI 3 and read it back"),
        ("tesmartctl input Office", "switch by a local name set with `tesmartctl name`"),
        ("tesmartctl input 3 --no-verify", "send the switch command without reading back"),
        ("tesmartctl input next", "same as `tesmartctl rotate`"),
        ("tesmartctl input next --only 1,2,4", "rotate within that list only"),
        ("tesmartctl --json input 3", '{"requested": 3, "active_input": 3, ...}'),
    ],
    "rotate": [
        ("tesmartctl next", "next input in the saved cycle"),
        ("tesmartctl previous", "previous input in the saved cycle"),
        ("tesmartctl cycle inputs 1,2,4", "remember which inputs next/previous walk"),
        ("tesmartctl cycle mode manual", "only move when next or previous is asked"),
        ("tesmartctl cycle mode automatic --every 15", "remember a 15s timer; does not start it"),
        ("tesmartctl cycle run", "step through the saved inputs until Ctrl-C (automatic mode)"),
        ("tesmartctl cycle show", "print mode, interval, and inputs"),
    ],
    "next": [
        ("tesmartctl next", "next input in the saved cycle"),
        ("tesmartctl next --only 1,3", "this step only; does not change the saved list"),
    ],
    "previous": [
        ("tesmartctl previous", "previous input in the saved cycle"),
        ("tesmartctl previous --only 1,2,4", "this step only; does not change the saved list"),
    ],
    "peek": [
        ("tesmartctl peek 3", "show input 3 for 5 seconds, then come back"),
        ("tesmartctl peek Office --seconds 10", "by local name, for 10 seconds"),
        ("tesmartctl peek 3 --seconds 8 --save", "and make 8 seconds the default"),
        ("tesmartctl status", "shows the saved peek duration"),
    ],
    "cycle": [
        ("tesmartctl cycle inputs 1,2,4", "save the inputs to walk; stored locally, not on the switch"),
        ("tesmartctl cycle mode manual", "use next and previous"),
        ("tesmartctl cycle mode automatic --every 15", "advance every 15 seconds when `cycle run` is going"),
        ("tesmartctl cycle run", "start the automatic loop (Ctrl-C to stop)"),
        ("tesmartctl cycle show", "mode, interval, and input list"),
        ("tesmartctl cycle clear", "forget the list; the cycle becomes every input"),
        ("tesmartctl status", "shows the mode and whether a list is saved"),
    ],
    "buzzer": [
        ("tesmartctl buzzer off", "mute the beep on input change"),
        ("tesmartctl buzzer on", "unmute (also accepts 1/0, true/false, mute/unmute)"),
    ],
    "led": [
        ("tesmartctl led 10", "front-panel display goes dark after 10 seconds"),
        ("tesmartctl led 30", "after 30 seconds"),
        ("tesmartctl led never", "stays on"),
    ],
    "autodetect": [
        ("tesmartctl autodetect on", "switch follows a newly connected/powered source"),
        ("tesmartctl autodetect off", "stay on the selected input"),
    ],
    "net": [
        ("tesmartctl net show", "IP, port, gateway, mask stored in the switch"),
        (
            "tesmartctl net set --ip 192.168.1.50 --gateway 192.168.1.1 --mask 255.255.255.0",
            "without --yes: prints the risk notice and refuses (warning 1 of 2)",
        ),
        (
            "tesmartctl net set --ip 192.168.1.50 --gateway 192.168.1.1 --mask 255.255.255.0 --yes",
            "warns again, writes, reads back; exit 0 only if every value is stored (at your own risk)",
        ),
        (
            "tesmartctl net set --ip 192.168.1.50 --yes --save-default",
            "also make 192.168.1.50 the CLI default host once the read-back confirms it",
        ),
        ("tesmartctl net set --tcp-port 5001 --yes", "change the switch's own listen port"),
    ],
    "net set": [
        (
            "tesmartctl net set --ip 192.168.1.50 --gateway 192.168.1.1 --mask 255.255.255.0",
            "without --yes: prints the risk notice and refuses (warning 1 of 2)",
        ),
        (
            "tesmartctl net set --ip 192.168.1.50 --gateway 192.168.1.1 --mask 255.255.255.0 --yes",
            "warns again, writes, reads back each value; then power-cycle (at your own risk)",
        ),
        ("tesmartctl net set --ip 192.168.1.50 --yes --save-default", "and update the CLI default host"),
    ],
    "config": [
        ("tesmartctl config show", "effective host/port/serial/inputs and where each came from"),
        ("tesmartctl config set host 192.168.1.50", "persist the switch address"),
        ("tesmartctl config set inputs 16", "for a 16x1 switch"),
        ("tesmartctl config set serial /dev/tty.usbserial-A1", "use RS232 instead of LAN"),
        ("tesmartctl config unset serial", "back to LAN"),
        ("tesmartctl config path", "print the env file location"),
    ],
    "config set": [
        ("tesmartctl config set host 192.168.1.50", "persist the switch address"),
        ("tesmartctl config set port 5000", "TCP port of the switch"),
        ("tesmartctl config set inputs 16", "for a 16x1 switch"),
        ("tesmartctl config set serial /dev/tty.usbserial-A1", "use RS232 instead of LAN"),
    ],
    "name": [
        ("tesmartctl name set 2 Office", "label input 2 (stored locally, not on the switch)"),
        ('tesmartctl name set 1 "Living room"', "labels can contain spaces"),
        ("tesmartctl name list", "show all labels"),
        ("tesmartctl name unset 1", "remove a label"),
        ("tesmartctl input Office", "labels can be used wherever an input number is accepted"),
    ],
    "name set": [
        ("tesmartctl name set 2 Office", "label input 2"),
        ('tesmartctl name set 1 "Living room"', "labels can contain spaces"),
    ],
    "raw": [
        ("tesmartctl raw AA BB 03 10 00 EE", "query active input; reply AA BB 03 11 <n-1> EE"),
        ("tesmartctl raw AA BB 03 01 03 EE", "switch to input 3"),
        ("tesmartctl raw AA BB 03 02 00 EE", "mute buzzer"),
        ("tesmartctl raw AA BB 03 10 00 EE --listen 3", "wait 3 s collecting replies"),
    ],
    "monitor": [
        ("tesmartctl monitor", "print a line each time the input changes (Ctrl-C to stop)"),
        ("tesmartctl --json monitor", 'one {"active_input": n} per change'),
    ],
    "listen": [
        ("tesmartctl listen", "read commands from stdin: get, set 3, 3, next, peek 3, quit"),
        ("printf 'get\\nset 3\\nquit\\n' | tesmartctl listen", "scripted session"),
        ("tesmartctl listen --bind 9753", "raw TCP on 127.0.0.1:9753"),
        ("echo set 4 | nc -w 3 127.0.0.1 9753", "client for the TCP mode; prints the input the switch reports"),
        ("tesmartctl listen --http 8080", "HTTP on http://127.0.0.1:8080/"),
        ("curl http://127.0.0.1:8080/input", '{"active_input": 2}'),
        ("curl http://127.0.0.1:8080/input/3", "switch to 3"),
        ("curl http://127.0.0.1:8080/input/Office", "switch by local name"),
        ("curl http://127.0.0.1:8080/rotate", "next input"),
        ("curl http://127.0.0.1:8080/peek/3", "show 3 briefly, then back; replies once back"),
        ("curl 'http://127.0.0.1:8080/peek?input=Office&seconds=10'", "same, by name, for 10 seconds"),
        ("curl http://127.0.0.1:8080/status", "input, names and LAN settings"),
        ("curl 'http://127.0.0.1:8080/input/3?format=text'", "plain `3` instead of JSON"),
        ("tesmartctl listen --http 0.0.0.0:8080", "accept other machines (no authentication)"),
        ("tesmartctl listen --http 8080 --panel", "also serve the control panel at http://127.0.0.1:8080/panel"),
        ("curl http://127.0.0.1:8080/api/state", "panel API: everything the panel shows, as JSON"),
    ],
}

GLOBAL_EXAMPLES: list[tuple[str, str]] = [
    ("tesmartctl status", "everything the switch will report"),
    ("tesmartctl input 3", "switch to HDMI 3"),
    ("tesmartctl rotate", "next input"),
    ("tesmartctl name set 2 Office", "label input 2 locally"),
    ("tesmartctl config set host 192.168.1.50", "save the switch address"),
    ("tesmartctl listen --http 8080", "control via http://127.0.0.1:8080/input/3"),
    ("tesmartctl <command> --help", "flags and more examples for one command"),
    ("tesmartctl examples", "every example in one place"),
]


def format_examples(pairs: list[tuple[str, str]], indent: str = "  ") -> str:
    """Render ``(command, note)`` pairs as aligned lines."""
    if not pairs:
        return ""
    width = min(max(len(command) for command, _ in pairs), 60)
    lines = []
    for command, note in pairs:
        if len(command) > width:
            lines.append(f"{indent}{command}")
            lines.append(f"{indent}{'':<{width}}  # {note}")
        else:
            lines.append(f"{indent}{command:<{width}}  # {note}")
    return "\n".join(lines)


def epilog_for(command: str) -> str:
    """Epilog text for a subparser, or an empty string."""
    pairs = EXAMPLES.get(command)
    return f"examples:\n{format_examples(pairs)}" if pairs else ""


def all_examples_text() -> str:
    sections = []
    for command in (
        "status", "input", "rotate", "next", "previous", "peek", "cycle", "buzzer", "led", "autodetect",
        "net", "config", "name", "raw", "monitor", "listen",
    ):
        sections.append(f"{command}\n{format_examples(EXAMPLES[command])}")
    return "\n\n".join(sections)
