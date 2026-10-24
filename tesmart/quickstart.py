"""Start the HTTP listener from the options ``tesmartctl install`` saved.

When those options are missing, quickstart says so and can run ``install``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

from . import config
from .listener import parse_bind


@dataclass(frozen=True)
class Quickstart:
    """The listener ``tesmartctl quickstart`` should open."""

    host: str
    port: int
    panel: bool
    mdns: bool | None
    mdns_name: str | None
    spec: str


def prepare(env_file: str | None = None) -> Quickstart:
    """Read the saved listener. Raises :class:`config.ConfigError` when it cannot start."""
    spec = config.read_http_spec(env_file)
    if not spec:
        raise config.ConfigError(
            "No HTTP listener address is saved. "
            "Run tesmartctl install, enable the HTTP listener, and confirm. "
            "That stores the listen address for tesmartctl quickstart."
        )
    if spec == config.HTTP_OFF:
        raise config.ConfigError(
            "The HTTP listener is off in the saved configuration. "
            "Run tesmartctl install and enable it."
        )
    try:
        host, port = parse_bind(spec)
    except ValueError as exc:
        raise config.ConfigError(
            f"The saved listener address {spec!r} cannot be used ({exc}). "
            "Run tesmartctl install to set it again."
        ) from exc
    saved_port = config.read_http_port(env_file)
    if saved_port is not None:
        port = saved_port
    mdns = config.read_http_mdns(env_file)
    name = config.read_mdns_name(env_file)
    panel = config.read_http_panel(env_file)
    return Quickstart(
        host=host,
        port=port,
        panel=True if panel is None else panel,
        mdns=mdns,
        mdns_name=name if mdns is not False else None,
        spec=spec,
    )


def prompt_to_configure(
    reason: str,
    *,
    prompter: object | None = None,
    interactive: bool | None = None,
) -> bool:
    """Ask whether to run ``tesmartctl install``. False when the user declines or this is not a terminal."""
    from .install import StdioPrompter, _ask_yes, say

    if prompter is None:
        if interactive is None:
            interactive = sys.stdin.isatty()
        if not interactive:
            print("quickstart does not have the required information to start.", file=sys.stderr)
            print(reason, file=sys.stderr)
            print(
                "Run tesmartctl install from an interactive shell, then tesmartctl quickstart.",
                file=sys.stderr,
            )
            return False
        prompter = StdioPrompter()
    say(prompter, "quickstart does not have the required information to start.", style="warn")
    say(prompter, reason)
    return _ask_yes(prompter, "Would you like to configure it now?", default=True)


def run_quickstart(
    env_file: str | None,
    *,
    configure: Callable[[], int],
    serve: Callable[[Quickstart], int],
    prompter: object | None = None,
    interactive: bool | None = None,
) -> int:
    """Start the saved listener, or offer ``install`` when the address is not saved."""
    from .install import say

    try:
        ready = prepare(env_file)
    except config.ConfigError as exc:
        if not prompt_to_configure(str(exc), prompter=prompter, interactive=interactive):
            return 1
        code = configure()
        if code != 0:
            return code
        try:
            ready = prepare(env_file)
        except config.ConfigError as again:
            if prompter is not None:
                say(prompter, "quickstart still does not have the required information to start.", style="warn")
                say(prompter, str(again))
            else:
                print("quickstart still does not have the required information to start.", file=sys.stderr)
                print(again, file=sys.stderr)
            return 1
    return serve(ready)
