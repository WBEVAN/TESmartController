"""Interactive setup for a tesmartctl server or client.

``tesmartctl install`` asks for the switch (or the listener), checks optional
dependencies, and writes the same env file as ``tesmartctl config``. The
listener command itself is printed and can be saved as ``startup.txt`` in the
current directory. Nothing is written until the last confirmation.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from . import config
from .mdns import default_instance_name
from .listener import parse_bind
from .transport import DEFAULT_BAUD_RATE, DEFAULT_TCP_PORT
from .version import package_version

# Printed at the start and the end, and copied into startup.txt.
DOC_LINKS: tuple[tuple[str, str], ...] = (
    ("Guided setup", "https://github.com/WBEVAN/TESmartController/blob/main/Docs/install.md"),
    ("Setup", "https://github.com/WBEVAN/TESmartController/wiki/Setup"),
    ("Listener and HTTP", "https://github.com/WBEVAN/TESmartController/wiki/Listener-and-HTTP"),
    ("mDNS", "https://github.com/WBEVAN/TESmartController/wiki/mDNS"),
    ("Control panel", "https://github.com/WBEVAN/TESmartController/wiki/Control-Panel"),
    ("CLI reference", "https://github.com/WBEVAN/TESmartController/wiki/CLI-Reference"),
    ("README", "https://github.com/WBEVAN/TESmartController#readme"),
    ("Project site", "https://wbevan.github.io/TESmartController/"),
)

# extra name -> (import module, pip requirement, extras marker)
EXTRAS: dict[str, tuple[str, str, str]] = {
    "serial": ("serial", "pyserial>=3.5", "tesmartctl[serial]"),
    "mdns": ("zeroconf", "zeroconf>=0.131", "tesmartctl[mdns]"),
    "kit": ("webview", "pywebview>=5.0", "tesmartctl[kit]"),
}

DEFAULT_HTTP_PORT = 8080
STARTUP_NAME = "startup.txt"
_CONFIG_KEYS = tuple(config.SETTINGS[name][0] for name in config.SETTINGS) + (
    config.MDNS_NAME_KEY,
    config.VIA_KEY,
    config.HTTP_KEY,
    config.HTTP_PORT_KEY,
    config.HTTP_PANEL_KEY,
    config.HTTP_MDNS_KEY,
)


class InstallCancelled(Exception):
    """The user closed the prompt or asked to stop."""


class Prompter(Protocol):
    def write(self, text: str) -> None: ...

    def ask(self, prompt: str) -> str: ...


class StdioPrompter:
    """Questions on stdin, narrative on stdout. Works in cmd, PowerShell, and a Unix shell."""

    def __init__(self) -> None:
        self.color = ansi_supported()

    def write(self, text: str) -> None:
        print(text, flush=True)

    def ask(self, prompt: str) -> str:
        try:
            return input(f"{prompt} ")
        except EOFError as exc:
            raise InstallCancelled("input closed") from exc


# ANSI styles. Windows 10 consoles need virtual-terminal mode; macOS and Linux
# terminals already understand these codes. ``NO_COLOR`` or a non-terminal
# stdout leaves the text plain. ``startup.txt`` is never colored.
_STYLES = {
    "title": "1;36",
    "heading": "1;36",
    "ok": "1;32",
    "warn": "33",
    "dim": "2",
    "command": "1",
    "url": "4",
}


def ansi_supported(stream: object | None = None) -> bool:
    """True when ``stream`` can show ANSI color on Windows, macOS, or Linux."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    target = sys.stdout if stream is None else stream
    if not hasattr(target, "isatty") or not target.isatty():  # type: ignore[union-attr]
        return False
    if sys.platform == "win32":
        return _enable_windows_vt(target)
    return True


def _enable_windows_vt(stream: object) -> bool:
    """Turn on ANSI processing for a Windows console. False on older hosts."""
    try:
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle_id = -12 if stream is sys.stderr else -11
        handle = kernel.GetStdHandle(handle_id)
        mode = ctypes.c_uint()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False
        enable = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return kernel.SetConsoleMode(handle, mode.value | enable) != 0
    except (AttributeError, OSError):
        return False


def paint(text: str, style: str | None, *, enabled: bool) -> str:
    """Wrap ``text`` in an ANSI style, or return it unchanged."""
    if not enabled or not style or not text:
        return text
    code = _STYLES.get(style)
    if not code:
        return text
    return f"\033[{code}m{text}\033[0m"


def _color_on(prompter: Prompter) -> bool:
    return bool(getattr(prompter, "color", False))


def say(prompter: Prompter, text: str = "", *, style: str | None = None) -> None:
    prompter.write(paint(text, style, enabled=_color_on(prompter)))


def heading(prompter: Prompter, text: str) -> None:
    """A blank line, then a section title."""
    say(prompter)
    say(prompter, text, style="heading")


def show_block(prompter: Prompter, text: str) -> None:
    """Print a sample block. Indented lines are commands; others are labels."""
    for line in text.splitlines():
        if not line:
            say(prompter)
        elif line.startswith("  "):
            say(prompter, line, style="command")
        else:
            say(prompter, line, style="heading")


def write_documentation(prompter: Prompter) -> None:
    say(prompter)
    say(prompter, "Documentation", style="heading")
    width = max(len(label) for label, _url in DOC_LINKS)
    enabled = _color_on(prompter)
    for label, url in DOC_LINKS:
        prompter.write(f"  {label:<{width}}  {paint(url, 'url', enabled=enabled)}")


@dataclass(frozen=True)
class Nic:
    """One IPv4 address on a local interface."""

    name: str
    address: str
    vpn: bool = False

    def label(self) -> str:
        note = " (VPN or tunnel)" if self.vpn else ""
        return f"{self.name} — {self.address}{note}"


@dataclass
class ServerPlan:
    host: str
    port: int
    inputs: int
    serial: str | None
    tested: bool
    http: bool
    http_port: int
    panel: bool
    mdns: bool
    mdns_name: str | None
    bind: str


@dataclass
class ClientPlan:
    use_mdns: bool
    mdns_name: str | None
    via_url: str | None
    kit: bool
    tested: bool


def pip_install_command(spec: str) -> list[str]:
    """Install ``spec`` with the interpreter that is running tesmartctl.

    An argument list, with no shell, is the form that works on Windows, macOS, and Linux.
    """
    return [sys.executable, "-m", "pip", "install", spec]


def module_available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def quote_arg(text: str) -> str:
    """Quote one command token for cmd, PowerShell, and a Unix shell."""
    if text and all(char.isalnum() or char in "._:@/+-" for char in text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_command(argv: list[str]) -> str:
    return " ".join(quote_arg(part) for part in argv)


def documentation_text() -> str:
    lines = ["Documentation:"]
    width = max(len(label) for label, _url in DOC_LINKS)
    for label, url in DOC_LINKS:
        lines.append(f"  {label:<{width}}  {url}")
    return "\n".join(lines)


def describe_configuration(env_file: str | None = None) -> str:
    """Say whether questions will start from a saved file or the built-in defaults."""
    path = config.env_file_path(env_file)
    values = config.read_env_file(path) if path.exists() else {}
    found = any(str(values.get(key) or "").strip() for key in _CONFIG_KEYS)
    if found:
        return (
            f"A configuration was found at {path}. "
            "This install will use that configuration as the defaults so you can reconfigure it."
        )
    if path.exists():
        return (
            f"A configuration file exists at {path}, but it has no connection settings. "
            "This install will use the built-in defaults. You can accept or change each value."
        )
    return (
        f"No configuration file was found at {path}. "
        "This install will use the built-in defaults. You can accept or change each value."
    )


def _source_label(source: str) -> str:
    if source == "default":
        return "built-in default"
    if source == "flag":
        return "command-line flag"
    if source.startswith("file:"):
        return "saved configuration"
    if source.startswith("env:"):
        return f"environment variable {source.split(':', 1)[1]}"
    return source


def http_bind_spec(plan: ServerPlan) -> str:
    """The ``--http`` argument for this plan. A bare port means this computer only."""
    if plan.bind in {"127.0.0.1", "localhost"}:
        return str(plan.http_port)
    return f"{plan.bind}:{plan.http_port}"


def listen_command(plan: ServerPlan) -> str | None:
    """The ``tesmartctl listen`` line for this plan, or None when HTTP is off."""
    if not plan.http:
        return None
    argv = ["tesmartctl", "listen", "--http", http_bind_spec(plan)]
    if plan.panel:
        argv.append("--panel")
    if plan.mdns:
        argv.append("--mdns")
        if plan.mdns_name:
            argv.extend(["--mdns-name", plan.mdns_name])
    else:
        argv.append("--no-mdns")
    return format_command(argv)


def server_switch_summary(plan: ServerPlan) -> str:
    if plan.serial:
        return f"serial://{plan.serial}@{DEFAULT_BAUD_RATE}"
    return f"tcp://{plan.host}:{plan.port}"


def server_samples(plan: ServerPlan, nics: list[Nic]) -> str:
    """Commands a client can run against the listener this plan would start."""
    lines: list[str] = []
    if not plan.http:
        lines.append("HTTP is off. On this computer, after the configuration is saved:")
        lines.append("  tesmartctl status")
        lines.append("  tesmartctl input 1")
        return "\n".join(lines)

    port = plan.http_port
    lines.append("On this computer:")
    lines.append(f"  curl http://127.0.0.1:{port}/status")
    lines.append(f"  curl http://127.0.0.1:{port}/input")
    lines.append(f"  curl http://127.0.0.1:{port}/input/1")
    if plan.panel:
        lines.append(f"  panel  http://127.0.0.1:{port}/panel")
        lines.append(f"  kit    http://127.0.0.1:{port}/kit")
        lines.append(f"  tesmartctl kit --listen {port}")

    lan_hosts: list[str] = []
    if plan.bind == "0.0.0.0":
        lan_hosts = [nic.address for nic in nics if not nic.vpn]
    elif plan.bind not in {"127.0.0.1", "localhost"}:
        lan_hosts = [plan.bind]
    if lan_hosts:
        lines.append("")
        lines.append("From another computer on the same network (no authentication):")
        for host in lan_hosts:
            lines.append(f"  curl http://{host}:{port}/status")
            if plan.panel:
                lines.append(
                    "  " + format_command(["tesmartctl", "kit", f"http://{host}:{port}/kit?app=1"])
                )

    if plan.mdns and plan.mdns_name:
        quoted = quote_arg(plan.mdns_name)
        lines.append("")
        lines.append("When zeroconf is installed, clients can find this listener by name:")
        lines.append("  tesmartctl discover")
        lines.append(f"  tesmartctl --via auto --mdns-name {quoted} status")
        lines.append(f"  tesmartctl --via auto --mdns-name {quoted} input 1")
        if plan.panel:
            lines.append(f"  tesmartctl --via auto --mdns-name {quoted} kit")
        lines.append("")
        lines.append("If this is the only server on the network, clients may omit the mDNS name:")
        lines.append("  tesmartctl --via auto status")
        lines.append("  tesmartctl --via auto input 1")
        if plan.panel:
            lines.append("  tesmartctl --via auto kit")
    return "\n".join(lines)


def client_samples(plan: ClientPlan) -> str:
    lines: list[str] = []
    if plan.use_mdns:
        name = quote_arg(plan.mdns_name) if plan.mdns_name else ""
        flag = f" --mdns-name {name}" if name else ""
        lines.append("Find the listener on the LAN and talk through it:")
        lines.append("  tesmartctl discover")
        lines.append(f"  tesmartctl --via auto{flag} status")
        lines.append(f"  tesmartctl --via auto{flag} input 1")
        if plan.kit:
            lines.append(f"  tesmartctl --via auto{flag} kit")
        if name:
            lines.append("")
            lines.append("If this is the only server on the network, clients may omit the mDNS name:")
            lines.append("  tesmartctl --via auto status")
            lines.append("  tesmartctl --via auto input 1")
            if plan.kit:
                lines.append("  tesmartctl --via auto kit")
        if plan.via_url:
            lines.append("")
            lines.append(f"A listener was found at {plan.via_url}. That address is saved as a fallback.")
    elif plan.via_url:
        url = plan.via_url.rstrip("/")
        lines.append("Talk to the listener at the address you entered:")
        lines.append(f"  tesmartctl --via {url} status")
        lines.append(f"  tesmartctl --via {url} input 1")
        lines.append(f"  curl {url}/status")
        if plan.kit:
            lines.append("  " + format_command(["tesmartctl", "kit", f"{url}/kit?app=1"]))
    return "\n".join(lines)


def startup_text(
    *,
    role: str,
    command: str | None,
    detail: str,
    samples: str,
    env_path: Path,
    expanded: str | None = None,
) -> str:
    title = "server" if role == "server" else "client"
    lines = [
        f"tesmartctl {title} startup",
        "Generated by `tesmartctl install`.",
        "The same commands work in Command Prompt, PowerShell, and a Unix shell when tesmartctl is on PATH.",
        "",
    ]
    if command:
        heading = "Start the server and leave it running:" if role == "server" else "Run the client:"
        lines.append(heading)
        lines.append("")
        lines.append(f"    {command}")
        lines.append("")
        if expanded:
            lines.append("The same start, with the saved options written out:")
            lines.append("")
            lines.append(f"    {expanded}")
            lines.append("")
    if detail:
        lines.append(detail)
        lines.append("")
    lines.append(f"Saved configuration: {env_path}")
    lines.append("")
    lines.append("Checks:")
    lines.append("")
    for sample in samples.splitlines():
        lines.append(f"  {sample}" if sample and not sample.startswith(" ") else sample)
    lines.append("")
    lines.append(documentation_text())
    lines.append("")
    return "\n".join(lines)


def parse_ip_addr(text: str) -> list[Nic]:
    """Parse ``ip -4 -o addr`` output into IPv4 rows. Loopback is omitted."""
    found: list[Nic] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        name = parts[1].rstrip(":")
        address = parts[3].split("/", 1)[0]
        if not _ipv4(address) or address.startswith("127.") or address in seen:
            continue
        seen.add(address)
        found.append(Nic(name or "nic", address, vpn=_vpn_name(name)))
    return found


def parse_ifconfig(text: str) -> list[Nic]:
    """Parse ``ifconfig`` output (macOS and Linux). Loopback addresses are omitted."""
    found: list[Nic] = []
    seen: set[str] = set()
    current = ""
    for raw in text.splitlines():
        if raw and not raw[0].isspace():
            current = raw.split(":", 1)[0].strip()
            continue
        parts = raw.split()
        if len(parts) >= 2 and parts[0] == "inet":
            address = parts[1]
            if not _ipv4(address) or address.startswith("127.") or address in seen:
                continue
            seen.add(address)
            found.append(Nic(current or "nic", address, vpn=_vpn_name(current)))
    return found


def parse_windows_nic_lines(text: str) -> list[Nic]:
    """Parse ``InterfaceAlias|IPAddress`` lines. Loopback addresses are omitted."""
    found: list[Nic] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        if "|" not in raw:
            continue
        name, _, address = raw.partition("|")
        name = name.strip()
        address = address.strip()
        if not _ipv4(address) or address.startswith("127.") or address in seen:
            continue
        seen.add(address)
        found.append(Nic(name or "nic", address, vpn=_vpn_name(name)))
    return found


def local_nics() -> list[Nic]:
    """IPv4 addresses on this computer, with interface names when the OS provides them."""
    found = _nics_from_ifaddr()
    if found:
        return found
    if sys.platform == "win32":
        found = _nics_from_windows()
    else:
        found = _nics_from_ip() or _nics_from_ifconfig()
    if found:
        return found
    return _nics_from_socket()


def _vpn_name(name: str) -> bool:
    from .mdns import _looks_like_vpn_iface

    return _looks_like_vpn_iface(name)


def _ipv4(text: str) -> bool:
    parts = text.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 and str(int(part)) == part for part in parts)
    except ValueError:
        return False


def _nics_from_ifaddr() -> list[Nic]:
    try:
        import ifaddr  # type: ignore[import-not-found]
    except ImportError:
        return []
    found: list[Nic] = []
    seen: set[str] = set()
    for adapter in ifaddr.get_adapters():
        name = getattr(adapter, "nice_name", "") or getattr(adapter, "name", "") or "nic"
        raw_name = getattr(adapter, "name", "") or ""
        for entry in adapter.ips:
            address = entry.ip
            if not isinstance(address, str) or not _ipv4(address) or address.startswith("127."):
                continue
            if address in seen:
                continue
            seen.add(address)
            found.append(Nic(name, address, vpn=_vpn_name(name) or _vpn_name(raw_name)))
    return found


def _run_text(argv: list[str], timeout: float) -> str:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout or ""


def _nics_from_windows() -> list[Nic]:
    script = (
        "Get-NetIPAddress -AddressFamily IPv4 | "
        "ForEach-Object { $_.InterfaceAlias + '|' + $_.IPAddress }"
    )
    return parse_windows_nic_lines(
        _run_text(["powershell", "-NoProfile", "-Command", script], timeout=15)
    )


def _nics_from_ip() -> list[Nic]:
    return parse_ip_addr(_run_text(["ip", "-4", "-o", "addr", "show"], timeout=5))


def _nics_from_ifconfig() -> list[Nic]:
    return parse_ifconfig(_run_text(["ifconfig"], timeout=5))


def _nics_from_socket() -> list[Nic]:
    found: list[Nic] = []
    seen: set[str] = set()

    def add(address: str, name: str) -> None:
        if not _ipv4(address) or address.startswith("127.") or address in seen:
            return
        seen.add(address)
        found.append(Nic(name, address, vpn=False))

    try:
        hostname = socket.gethostname() or "nic"
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            add(info[4][0], hostname)
    except OSError:
        pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 9))
            add(sock.getsockname()[0], "default route")
        finally:
            sock.close()
    except OSError:
        pass
    return found


def serial_port_names() -> list[str]:
    if not module_available("serial"):
        return []
    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]
    except ImportError:
        return []
    try:
        return [port.device for port in list_ports.comports() if getattr(port, "device", "")]
    except (OSError, AttributeError):
        return []


def probe_switch(
    *,
    host: str,
    port: int,
    serial: str | None,
    inputs: int,
    timeout: float = 3.0,
) -> tuple[bool, str]:
    """Open the switch and read the active input. Returns ``(ok, message)``."""
    from .switch import SwitchError, TesmartSwitch
    from .transport import SerialTransport, TcpTransport, TransportError

    try:
        transport = SerialTransport(serial, DEFAULT_BAUD_RATE) if serial else TcpTransport(host, port, connect_timeout=timeout)
        with TesmartSwitch(transport, input_count=inputs, reply_timeout=timeout) as switch:
            active = switch.get_active_input()
    except (TransportError, SwitchError, OSError) as exc:
        return False, str(exc)
    where = server_switch_summary(
        ServerPlan(
            host=host,
            port=port,
            inputs=inputs,
            serial=serial,
            tested=True,
            http=False,
            http_port=DEFAULT_HTTP_PORT,
            panel=False,
            mdns=False,
            mdns_name=None,
            bind="127.0.0.1",
        )
    )
    return True, f"Connected to {where}. Active input is {active}."


def probe_listener(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """GET ``/version`` on a listener URL."""
    target = url.rstrip("/") + "/version"
    request = urllib.request.Request(target, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace").strip()
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"Could not reach {target} ({reason})."
    return True, f"Listener answered {target}: {body[:180]}"


def probe_mdns(name: str | None, timeout: float = 2.0) -> tuple[bool, str, str | None]:
    """Browse for a listener. Returns ``(ok, message, base_url)``."""
    from .mdns import MdnsError, browse, pick_listener

    try:
        services = browse(timeout=timeout)
        chosen = pick_listener(services, name=name)
    except MdnsError as exc:
        return False, str(exc), None
    return True, f"Found {chosen.instance_name} at {chosen.base_url}.", chosen.base_url


def ensure_extra(name: str, prompter: Prompter) -> bool:
    """Return True when ``name`` can be imported, installing it with pip when the user agrees."""
    module, spec, extra = EXTRAS[name]
    say(prompter)
    if module_available(module):
        say(prompter, f"{extra} is already installed.", style="ok")
        return True
    command = pip_install_command(spec)
    shown = format_command(command)
    say(prompter, f"{extra} is not installed.", style="warn")
    say(
        prompter,
        "Install it now? This uses the same Python that is running tesmartctl, "
        "which works on Windows, macOS, and Linux:",
    )
    say(prompter, f"  {shown}", style="command")
    say(prompter, f'The same package is also installed by: pip install "{extra}"')
    if not _ask_yes(prompter, "Install it now?", default=True):
        say(prompter, f"Skipped. Install it yourself with: {shown}", style="warn")
        return False
    say(prompter, f"Running {shown}", style="command")
    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        say(prompter, f"Could not run pip ({exc}). Install it yourself with: {shown}", style="warn")
        return False
    importlib.invalidate_caches()
    if completed.returncode == 0 and module_available(module):
        say(prompter, f"Installed {extra}.", style="ok")
        return True
    say(
        prompter,
        f"pip did not install {extra} (exit {completed.returncode}). "
        f"Install it yourself with: {shown}",
        style="warn",
    )
    return False


def _ask_yes(prompter: Prompter, prompt: str, *, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        say(prompter)
        raw = prompter.ask(f"{prompt} [{hint}]").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        prompter.write("Answer yes or no.")


def _choose(prompter: Prompter, title: str, options: list[tuple[str, str]], *, default: str) -> str:
    default_index = next(index for index, (key, _label) in enumerate(options, start=1) if key == default)
    say(prompter)
    prompter.write(title)
    for index, (key, label) in enumerate(options, start=1):
        mark = " (default)" if key == default else ""
        prompter.write(f"  {index}) {label}{mark}")
    while True:
        raw = prompter.ask(f"Choice [{default_index}]").strip()
        if not raw:
            return default
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(options):
                return options[index - 1][0]
        folded = raw.lower()
        for key, label in options:
            if folded == key.lower() or folded == label.lower():
                return key
        prompter.write(f"Enter a number from 1 to {len(options)}.")


def _ask_text(
    prompter: Prompter,
    label: str,
    *,
    default: str | None,
    source: str | None = None,
    validate: Callable[[str], str | None] | None = None,
) -> str:
    hint = f" [{default}]" if default else ""
    where = paint(f" ({source})", "dim", enabled=_color_on(prompter)) if source else ""
    while True:
        say(prompter)
        raw = prompter.ask(f"{label}{hint}{where}").strip()
        if not raw:
            if default is None:
                say(prompter, "A value is required.", style="warn")
                continue
            value = default
        else:
            value = raw
        error = validate(value) if validate else None
        if error:
            say(prompter, error, style="warn")
            continue
        return value


def _valid_port(text: str) -> str | None:
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        return "Enter a TCP port from 1 to 65535."
    return None


def _valid_inputs(text: str) -> str | None:
    if not text.isdigit() or not 1 <= int(text) <= 32:
        return "Enter the number of inputs, usually 8 or 16."
    return None


def _valid_host(text: str) -> str | None:
    if not text or any(char.isspace() for char in text):
        return "Enter a hostname or IP address."
    return None


def _valid_mdns(text: str) -> str | None:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "Enter an mDNS name."
    if len(cleaned) > 63:
        return "An mDNS name must be 63 characters or fewer."
    return None


def _valid_url(text: str) -> str | None:
    if not text.startswith(("http://", "https://")):
        return "Enter an absolute URL, for example http://192.168.1.20:8080."
    return None


def _valid_serial(text: str) -> str | None:
    if not text or any(char.isspace() for char in text):
        return "Enter a serial device path, for example /dev/tty.usbserial-10 or COM3."
    return None


def _after_failed_check(prompter: Prompter, message: str) -> str:
    say(prompter)
    say(prompter, message, style="warn")
    return _choose(
        prompter,
        "The check did not succeed.",
        [
            ("retry", "Enter the details again and retry"),
            ("continue", "Continue with these details anyway"),
            ("quit", "Quit without saving"),
        ],
        default="retry",
    )


def _save_server(plan: ServerPlan, env_file: str | None) -> Path:
    config.set_default("host", plan.host, env_file)
    config.set_default("port", str(plan.port), env_file)
    config.set_default("inputs", str(plan.inputs), env_file)
    if plan.serial:
        config.set_default("serial", plan.serial, env_file)
    else:
        config.unset_default("serial", env_file)
    config.write_mdns_name(plan.mdns_name if plan.mdns else None, env_file)
    if plan.http:
        return config.write_http_listener(
            http_bind_spec(plan),
            panel=plan.panel,
            mdns=plan.mdns,
            port=plan.http_port,
            env_file=env_file,
        )
    return config.write_http_listener(config.HTTP_OFF, env_file=env_file)


def _save_client(plan: ClientPlan, env_file: str | None) -> Path:
    path = config.env_file_path(env_file)
    if plan.via_url:
        path = config.write_via_url(plan.via_url, env_file)
    path = config.write_mdns_name(plan.mdns_name if plan.use_mdns else None, env_file)
    config.write_http_listener(None, env_file=env_file)
    return path


def _write_startup(directory: Path, text: str, prompter: Prompter) -> Path | None:
    path = directory / STARTUP_NAME
    if path.exists() and not _ask_yes(prompter, f"{path} already exists. Replace it?", default=False):
        prompter.write(f"Left {path} unchanged.")
        return None
    path.write_text(text, encoding="utf-8")
    say(prompter)
    say(prompter, f"Wrote {path}", style="ok")
    return path


def _saved_http(env_file: str) -> tuple[bool | None, int | None, bool | None, bool | None, str | None]:
    """Return enabled, port, panel, mDNS, and the bind choice stored for quickstart.

    ``enabled`` is None when install has not saved a listener yet. ``panel`` is
    None when the browser UI choice has not been recorded.
    """
    spec = config.read_http_spec(env_file)
    saved_port = config.read_http_port(env_file)
    if not spec:
        return None, saved_port, None, None, None
    if spec == config.HTTP_OFF:
        return False, saved_port, None, None, None
    try:
        host, port = parse_bind(spec)
    except ValueError:
        return None, saved_port, None, None, None
    if host == "127.0.0.1":
        choice = "localhost"
    elif host == "0.0.0.0":
        choice = "all"
    else:
        choice = host
    return True, saved_port or port, config.read_http_panel(env_file), config.read_http_mdns(env_file), choice


def _gather_server(
    prompter: Prompter,
    settings: config.Settings,
    *,
    nics: list[Nic],
    probe: Callable[..., tuple[bool, str]],
    ensure: Callable[[str, Prompter], bool],
) -> ServerPlan | None:
    saved_http, saved_port, saved_panel, saved_mdns, saved_bind = _saved_http(str(settings.env_file))
    while True:
        heading(prompter, "HDMI switch")
        inputs = int(
            _ask_text(
                prompter,
                "How many HDMI inputs does the switch have? Usual models are 8 or 16",
                default=str(settings.inputs),
                source=_source_label(settings.sources.get("inputs", "default")),
                validate=_valid_inputs,
            )
        )
        transport = _choose(
            prompter,
            "How should this computer connect to the switch?",
            [
                ("tcp", "TCP on the LAN (the usual connection)"),
                ("serial", "Serial RS232 (9600 8N1, needs pyserial)"),
            ],
            default="serial" if settings.serial else "tcp",
        )
        serial: str | None = None
        host = settings.host
        port = settings.port
        if transport == "serial":
            if not ensure("serial", prompter):
                nxt = _choose(
                    prompter,
                    "RS232 needs pyserial before a serial connection can be opened.",
                    [("tcp", "Use TCP instead"), ("quit", "Quit without saving")],
                    default="tcp",
                )
                if nxt == "quit":
                    return None
                transport = "tcp"
            else:
                ports = serial_port_names()
                if ports:
                    prompter.write("Serial devices visible on this computer:")
                    for device in ports:
                        prompter.write(f"  {device}")
                serial_default = settings.serial
                serial_source = _source_label(settings.sources.get("serial", "default")) if serial_default else None
                serial = _ask_text(
                    prompter,
                    "Serial device",
                    default=serial_default,
                    source=serial_source,
                    validate=_valid_serial,
                )
        if transport == "tcp":
            serial = None
            host = _ask_text(
                prompter,
                "Switch address",
                default=settings.host,
                source=_source_label(settings.sources.get("host", "default")),
                validate=_valid_host,
            )
            port = int(
                _ask_text(
                    prompter,
                    "Switch TCP port",
                    default=str(settings.port),
                    source=_source_label(settings.sources.get("port", "default")),
                    validate=_valid_port,
                )
            )

        tested = False
        if _ask_yes(prompter, "Test this connection now?", default=True):
            ok, message = probe(host=host, port=port, serial=serial, inputs=inputs)
            if ok:
                say(prompter)
                say(prompter, message, style="ok")
                tested = True
            else:
                action = _after_failed_check(prompter, message)
                if action == "quit":
                    return None
                if action == "retry":
                    continue
                tested = False
        break

    heading(prompter, "Discovery")
    if saved_mdns is None:
        mdns_default = bool(config.read_mdns_name(str(settings.env_file)) or module_available("zeroconf"))
    else:
        mdns_default = saved_mdns
    mdns = _ask_yes(
        prompter,
        "Advertise this server with zeroconf (mDNS) so other computers can find it?",
        default=mdns_default,
    )
    mdns_name: str | None = None
    if mdns:
        if not ensure("mdns", prompter):
            say(prompter, "Continuing without zeroconf. Clients will need a typed address.", style="warn")
            mdns = False
        else:
            saved = config.read_mdns_name(str(settings.env_file))
            current = saved or default_instance_name()
            current = current[:63]
            mdns_name = " ".join(
                _ask_text(
                    prompter,
                    "mDNS name for this listener",
                    default=current,
                    source="saved configuration" if saved else "this computer's hostname",
                    validate=_valid_mdns,
                ).split()
            )

    heading(prompter, "HTTP")
    http_default = saved_http is not False
    http = _ask_yes(
        prompter,
        "Enable the HTTP listener for client applications "
        "(curl, Hubitat, Stream Deck, Companion, the panel, and the kit)?",
        default=http_default,
    )
    if mdns and not http:
        say(prompter, "mDNS advertises the HTTP listener. Zeroconf is off because HTTP is off.", style="warn")
        mdns = False
        mdns_name = None

    http_port = saved_port or DEFAULT_HTTP_PORT
    panel = False
    if http:
        http_port = int(
            _ask_text(
                prompter,
                "HTTP listen port",
                default=str(saved_port or DEFAULT_HTTP_PORT),
                source="saved configuration" if saved_port else "built-in default",
                validate=_valid_port,
            )
        )
        heading(prompter, "Browser UI")
        say(prompter, "  /kit    floating UI application kit")
        say(prompter, "  /panel  browser-only control panel")
        panel = _ask_yes(
            prompter,
            "Serve these pages?",
            default=True if saved_panel is None else saved_panel,
        )
        if panel:
            say(prompter)
            say(prompter, "The pages ship with tesmartctl.")
            say(prompter, "tesmartctl kit uses pywebview when it is installed, and Chrome or Edge otherwise.")
            if not ensure("kit", prompter):
                say(prompter)
                say(prompter, "The panel and kit pages will still be served.", style="warn")
                say(prompter, "tesmartctl kit will use Chrome or Edge until pywebview is installed.", style="warn")

    bind = "127.0.0.1"
    if http:
        heading(prompter, "Listener address")
        options: list[tuple[str, str]] = [
            ("localhost", "This computer only (127.0.0.1)"),
            ("all", "All network interfaces (0.0.0.0)"),
        ]
        if nics:
            say(prompter, "IPv4 addresses on this computer:")
            for nic in nics:
                prompter.write(f"  {nic.label()}")
        for nic in nics:
            options.append((nic.address, nic.label()))
        default_bind = "all" if mdns else "localhost"
        option_keys = {key for key, _label in options}
        if saved_bind in option_keys:
            default_bind = saved_bind
        elif saved_bind and saved_http:
            say(
                prompter,
                f"The saved listener address {saved_bind} is not on this computer now.",
                style="warn",
            )
        while True:
            chosen = _choose(
                prompter,
                "Where should the HTTP listener accept connections?",
                options,
                default=default_bind,
            )
            if mdns and chosen == "localhost":
                say(
                    prompter,
                    "Other computers cannot connect to 127.0.0.1. "
                    "mDNS would advertise this listener, and clients on the LAN would then fail to connect.",
                    style="warn",
                )
                if not _ask_yes(prompter, "Keep localhost anyway?", default=False):
                    continue
            bind = "127.0.0.1" if chosen == "localhost" else "0.0.0.0" if chosen == "all" else chosen
            break

    return ServerPlan(
        host=host,
        port=port,
        inputs=inputs,
        serial=serial,
        tested=tested,
        http=http,
        http_port=http_port,
        panel=panel,
        mdns=mdns,
        mdns_name=mdns_name,
        bind=bind,
    )


def _gather_client(
    prompter: Prompter,
    settings: config.Settings,
    *,
    ensure: Callable[[str, Prompter], bool],
    probe_url: Callable[[str], tuple[bool, str]],
    probe_name: Callable[[str | None], tuple[bool, str, str | None]],
) -> ClientPlan | None:
    heading(prompter, "Listener")
    saved_via = config.read_via_url(str(settings.env_file))
    saved_name = config.read_mdns_name(str(settings.env_file))
    default_mode = "url" if saved_via and not saved_name else "mdns"
    mode = _choose(
        prompter,
        "How should this client find the listener?",
        [
            ("mdns", "mDNS / zeroconf (tesmartctl --via auto)"),
            ("url", "A listener URL (http://host:port)"),
        ],
        default=default_mode,
    )
    use_mdns = mode == "mdns"
    mdns_name: str | None = None
    via_url: str | None = saved_via
    tested = False

    if use_mdns:
        if not ensure("mdns", prompter):
            nxt = _choose(
                prompter,
                "Finding a listener by name needs zeroconf.",
                [("url", "Type a listener URL instead"), ("quit", "Quit without saving")],
                default="url",
            )
            if nxt == "quit":
                return None
            use_mdns = False
        else:
            current = (saved_name or default_instance_name())[:63]
            mdns_name = " ".join(
                _ask_text(
                    prompter,
                    "mDNS name to attach to (the name the server is advertising)",
                    default=current,
                    source="saved configuration" if saved_name else "this computer's hostname",
                    validate=_valid_mdns,
                ).split()
            )
            if _ask_yes(prompter, "Look for that listener on the LAN now?", default=True):
                ok, message, found = probe_name(mdns_name)
                if ok:
                    say(prompter)
                    say(prompter, message, style="ok")
                    tested = True
                    via_url = found or via_url
                else:
                    action = _after_failed_check(prompter, message)
                    if action == "quit":
                        return None
                    if action == "retry":
                        return _gather_client(
                            prompter,
                            settings,
                            ensure=ensure,
                            probe_url=probe_url,
                            probe_name=probe_name,
                        )

    if not use_mdns:
        via_url = _ask_text(
            prompter,
            "Listener URL",
            default=saved_via or f"http://127.0.0.1:{DEFAULT_HTTP_PORT}",
            source="saved configuration" if saved_via else "built-in default",
            validate=_valid_url,
        ).rstrip("/")
        if _ask_yes(prompter, "Test this listener URL now?", default=True):
            ok, message = probe_url(via_url)
            if ok:
                say(prompter)
                say(prompter, message, style="ok")
                tested = True
            else:
                action = _after_failed_check(prompter, message)
                if action == "quit":
                    return None
                if action == "retry":
                    return _gather_client(
                        prompter,
                        settings,
                        ensure=ensure,
                        probe_url=probe_url,
                        probe_name=probe_name,
                    )

    heading(prompter, "Kit")
    kit = _ask_yes(
        prompter,
        "Install the local UI kit so `tesmartctl kit` can open a frameless window on this computer?",
        default=False,
    )
    if kit:
        say(prompter)
        say(prompter, "tesmartctl kit uses pywebview when it is installed, and Chrome or Edge otherwise.")
        say(prompter, "The listener that serves /kit still has to be running on the server.")
        if not ensure("kit", prompter):
            say(prompter)
            say(prompter, "tesmartctl kit will use Chrome or Edge until pywebview is installed.", style="warn")

    return ClientPlan(use_mdns=use_mdns, mdns_name=mdns_name, via_url=via_url, kit=kit, tested=tested)


def run_install(
    *,
    env_file: str | None = None,
    settings: config.Settings | None = None,
    prompter: Prompter | None = None,
    interactive: bool | None = None,
    directory: Path | None = None,
    nics: list[Nic] | None = None,
    probe: Callable[..., tuple[bool, str]] | None = None,
    probe_url: Callable[[str], tuple[bool, str]] | None = None,
    probe_name: Callable[[str | None], tuple[bool, str, str | None]] | None = None,
    ensure: Callable[[str, Prompter], bool] | None = None,
) -> int:
    """Run the questionnaire. Returns a process status code."""
    if prompter is None:
        if interactive is None:
            interactive = sys.stdin.isatty()
        if not interactive:
            print(
                "tesmartctl install asks questions in the terminal. Run it from an interactive shell.",
                file=sys.stderr,
            )
            print(documentation_text(), file=sys.stderr)
            return 2
        prompter = StdioPrompter()

    resolved = settings or config.resolve(env_file)
    folder = directory or Path.cwd()
    interfaces = list(nics) if nics is not None else local_nics()
    check_switch = probe or probe_switch
    check_url = probe_url or probe_listener
    check_name = probe_name or probe_mdns
    check_extra = ensure or ensure_extra

    try:
        say(prompter, f"Running TESmartController Install ({package_version()})", style="title")
        say(prompter)
        say(prompter, describe_configuration(str(resolved.env_file)))
        write_documentation(prompter)
        say(prompter)
        say(
            prompter,
            "Nothing is written until you confirm at the end. "
            "Press Enter to accept the value shown in brackets.",
        )
        heading(prompter, "This computer")
        role = _choose(
            prompter,
            "What are you setting up on this computer?",
            [
                ("server", "Server — this computer talks to the HDMI switch and can serve clients"),
                ("client", "Client — this computer talks to a tesmartctl listener that is already running"),
            ],
            default="server",
        )
        if role == "server":
            plan = _gather_server(
                prompter,
                resolved,
                nics=interfaces,
                probe=check_switch,
                ensure=check_extra,
            )
            if plan is None:
                say(prompter, "Install cancelled. The configuration file was not changed.", style="warn")
                return 1
            command = listen_command(plan)
            samples = server_samples(plan, interfaces)
            detail = f"HDMI switch: {server_switch_summary(plan)} ({plan.inputs} inputs)"
            heading(prompter, "Start the server")
            if command:
                say(prompter, "  tesmartctl quickstart", style="command")
                say(
                    prompter,
                    "quickstart reads the listener address, panel, and mDNS options saved by this install.",
                )
                say(prompter, "The same start, written out:")
                say(prompter, f"  {command}", style="command")
            else:
                say(prompter, "HTTP is off. After saving, control the switch directly:")
                say(prompter, "  tesmartctl status", style="command")
            text = startup_text(
                role="server",
                command="tesmartctl quickstart" if command else "tesmartctl status",
                expanded=command,
                detail=detail,
                samples=samples,
                env_path=resolved.env_file,
            )
        else:
            plan_client = _gather_client(
                prompter,
                resolved,
                ensure=check_extra,
                probe_url=check_url,
                probe_name=check_name,
            )
            if plan_client is None:
                say(prompter, "Install cancelled. The configuration file was not changed.", style="warn")
                return 1
            samples = client_samples(plan_client)
            if plan_client.use_mdns and plan_client.mdns_name:
                command = format_command(
                    ["tesmartctl", "--via", "auto", "--mdns-name", plan_client.mdns_name, "status"]
                )
            elif plan_client.via_url:
                command = format_command(["tesmartctl", "--via", plan_client.via_url, "status"])
            else:
                command = "tesmartctl --via auto status"
            heading(prompter, "Client command")
            say(prompter, f"  {command}", style="command")
            detail = ""
            text = startup_text(
                role="client",
                command=command,
                detail=detail,
                samples=samples,
                env_path=resolved.env_file,
            )
            plan = plan_client

        say(prompter)
        show_block(prompter, samples)
        if _ask_yes(
            prompter,
            f"Write these commands to {folder / STARTUP_NAME} for future reference?",
            default=True,
        ):
            _write_startup(folder, text, prompter)

        heading(prompter, "Save")
        say(prompter, f"Ready to save the configuration to {resolved.env_file}.")
        if isinstance(plan, ServerPlan):
            say(prompter, f"  switch:  {server_switch_summary(plan)}")
            say(prompter, f"  inputs:  {plan.inputs}")
            if plan.http:
                say(prompter, f"  listen:  {http_bind_spec(plan)}")
                say(prompter, f"  panel:   {'yes' if plan.panel else 'no'}")
                if plan.mdns and plan.mdns_name:
                    say(prompter, f"  mdns:    {plan.mdns_name}")
                else:
                    say(prompter, "  mdns:    off")
                say(prompter, "  start:   tesmartctl quickstart", style="command")
            else:
                say(prompter, "  listen:  HTTP off")
        else:
            if plan.use_mdns:
                say(prompter, f"  discover:  {plan.mdns_name}")
            if plan.via_url:
                say(prompter, f"  via:  {plan.via_url}")
            say(prompter, f"  kit dependency requested:  {'yes' if plan.kit else 'no'}")
        if not _ask_yes(prompter, "Write this configuration now?", default=True):
            say(prompter, "The configuration file was not changed.")
            write_documentation(prompter)
            return 0
        if isinstance(plan, ServerPlan):
            path = _save_server(plan, str(resolved.env_file))
        else:
            path = _save_client(plan, str(resolved.env_file))
        say(prompter)
        say(prompter, f"Saved the configuration to {path}.", style="ok")
        say(prompter, "Install is complete.", style="ok")
        write_documentation(prompter)
        return 0
    except InstallCancelled:
        say(prompter, "Install cancelled. The configuration file was not changed.", style="warn")
        return 2
    except KeyboardInterrupt:
        say(prompter)
        say(prompter, "Install cancelled. The configuration file was not changed.", style="warn")
        return 2
