"""``tesmartctl install`` questionnaire, without a switch or a TTY."""

from __future__ import annotations

import sys

import pytest

from tesmart import config
from tesmart.cli import build_parser
from tesmart.version import package_version
from tesmart.quickstart import prepare, prompt_to_configure, run_quickstart
from tesmart.install import (
    Nic,
    ServerPlan,
    ansi_supported,
    describe_configuration,
    format_command,
    listen_command,
    paint,
    parse_ifconfig,
    parse_ip_addr,
    parse_windows_nic_lines,
    pip_install_command,
    run_install,
)

_ENV_KEYS = (
    "TESMART_HOST",
    "TESMART_PORT",
    "TESMART_SERIAL",
    "TESMART_INPUTS",
    "TESMART_ENV_FILE",
    "TESMART_MDNS_NAME",
    "TESMART_VIA",
)


class ScriptedPrompter:
    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def ask(self, prompt: str) -> str:
        self.lines.append(prompt)
        if not self.answers:
            joined = "\n".join(self.lines)
            raise AssertionError(f"no scripted answer for: {prompt}\n{joined}")
        return self.answers.pop(0)

    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _settings(path):
    return config.resolve(str(path))


def _ensure_ok(calls: list[str]):
    def ensure(name: str, prompter: ScriptedPrompter) -> bool:
        calls.append(name)
        prompter.write(f"{name} is already installed.")
        return True

    return ensure


def test_color_is_ansi_on_a_terminal_and_plain_otherwise(monkeypatch: pytest.MonkeyPatch):
    assert paint("Saved", "ok", enabled=False) == "Saved"
    assert paint("Saved", "ok", enabled=True) == "\033[1;32mSaved\033[0m"
    monkeypatch.setenv("NO_COLOR", "1")

    class _Tty:
        def isatty(self) -> bool:
            return True

    assert ansi_supported(_Tty()) is False


def test_install_is_a_command():
    args = build_parser().parse_args(["install"])
    assert args.command == "install"


def test_noninteractive_exits_without_reading_input(capsys, clean_env):
    assert run_install(interactive=False) == 2
    err = capsys.readouterr().err
    assert "interactive shell" in err
    assert "https://github.com/WBEVAN/TESmartController/wiki/Setup" in err


def test_missing_configuration_is_called_out(tmp_path, clean_env):
    text = describe_configuration(str(tmp_path / "env"))
    assert "No configuration file was found" in text
    assert "built-in defaults" in text


def test_found_configuration_is_used_to_reconfigure(tmp_path, clean_env):
    path = tmp_path / "env"
    path.write_text("TESMART_HOST=10.9.9.9\n", encoding="utf-8")
    text = describe_configuration(str(path))
    assert "A configuration was found" in text
    assert "reconfigure" in text


def test_listen_command_records_bind_panel_and_mdns():
    lan = ServerPlan(
        host="10.1.2.3",
        port=5000,
        inputs=8,
        serial=None,
        tested=True,
        http=True,
        http_port=9090,
        panel=True,
        mdns=True,
        mdns_name="Office HDMI",
        bind="0.0.0.0",
    )
    text = listen_command(lan)
    assert text is not None
    assert "tesmartctl listen --http 0.0.0.0:9090 --panel --mdns --mdns-name" in text
    assert '"Office HDMI"' in text

    local = ServerPlan(
        host="10.1.2.3",
        port=5000,
        inputs=8,
        serial=None,
        tested=False,
        http=True,
        http_port=8080,
        panel=False,
        mdns=False,
        mdns_name=None,
        bind="127.0.0.1",
    )
    assert listen_command(local) == "tesmartctl listen --http 8080 --no-mdns"


def test_pip_command_is_a_direct_argument_list():
    command = pip_install_command("zeroconf>=0.131")
    assert command[:3] == [sys.executable, "-m", "pip"]
    assert command[3:] == ["install", "zeroconf>=0.131"]
    assert format_command(command).startswith(sys.executable)


def test_interface_parsers_skip_loopback():
    ifconfig = """
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
	inet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
	inet 127.0.0.1 netmask 0xff000000
utun0: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
	inet 10.8.0.2 netmask 0xffffffff
"""
    parsed = parse_ifconfig(ifconfig)
    assert [(nic.name, nic.address, nic.vpn) for nic in parsed] == [
        ("en0", "192.168.1.20", False),
        ("utun0", "10.8.0.2", True),
    ]
    ip_text = "1: lo    inet 127.0.0.1/8 scope host lo\n2: eth0    inet 10.0.0.8/24 brd 10.0.0.255 scope global eth0\n"
    assert [(nic.name, nic.address) for nic in parse_ip_addr(ip_text)] == [("eth0", "10.0.0.8")]
    windows = "Ethernet|192.168.1.20\nLoopback Pseudo-Interface 1|127.0.0.1\n"
    assert [(nic.name, nic.address) for nic in parse_windows_nic_lines(windows)] == [
        ("Ethernet", "192.168.1.20")
    ]


def test_server_install_saves_switch_and_startup(tmp_path, clean_env):
    env = tmp_path / "env"
    calls: list[str] = []
    seen: dict = {}

    def probe(**kwargs):
        seen.update(kwargs)
        return True, "Connected. Active input is 2."

    prompter = ScriptedPrompter(
        [
            "1",
            "16",
            "",
            "10.1.2.3",
            "5001",
            "y",
            "y",
            "OfficeHDMI",
            "y",
            "9090",
            "",
            "2",
            "y",
            "y",
        ]
    )
    code = run_install(
        settings=_settings(env),
        prompter=prompter,
        directory=tmp_path,
        nics=[Nic("en0", "10.1.2.9")],
        probe=probe,
        ensure=_ensure_ok(calls),
    )
    assert code == 0, prompter.text()
    assert seen["host"] == "10.1.2.3"
    assert seen["port"] == 5001
    assert seen["serial"] is None
    assert calls == ["mdns", "kit"]
    values = config.read_env_file(env)
    assert values["TESMART_HOST"] == "10.1.2.3"
    assert values["TESMART_PORT"] == "5001"
    assert values["TESMART_INPUTS"] == "16"
    assert values["TESMART_MDNS_NAME"] == "OfficeHDMI"
    assert values["TESMART_HTTP"] == "0.0.0.0:9090"
    assert values["TESMART_HTTP_PORT"] == "9090"
    assert values["TESMART_HTTP_PANEL"] == "1"
    assert values["TESMART_HTTP_MDNS"] == "1"
    assert "TESMART_SERIAL" not in values
    startup = (tmp_path / "startup.txt").read_text(encoding="utf-8")
    assert "tesmartctl quickstart" in startup
    assert "tesmartctl listen --http 0.0.0.0:9090 --panel --mdns --mdns-name OfficeHDMI" in startup
    assert "tesmartctl --via auto --mdns-name OfficeHDMI status" in startup
    assert "If this is the only server on the network, clients may omit the mDNS name:" in startup
    assert "tesmartctl --via auto status" in startup
    transcript = prompter.text()
    assert "\n\nHDMI switch\n" in transcript
    assert "\n\nDiscovery\n" in transcript
    assert "\n\nHTTP\n" in transcript
    assert "\n\nListener address\n" in transcript
    assert "\n\nStart the server\n" in transcript
    assert "\n\nSave\n" in transcript
    assert "\033[" not in startup
    assert f"Running TESmartController Install ({package_version()})" in transcript
    assert "tesmartctl quickstart" in transcript
    assert "https://github.com/WBEVAN/TESmartController/wiki/Setup" in transcript
    assert "Install is complete." in transcript


def test_server_reuses_saved_host_and_can_decline(tmp_path, clean_env):
    env = tmp_path / "env"
    env.write_text("TESMART_HOST=10.9.9.9\nTESMART_SERIAL=/dev/tty.old\n", encoding="utf-8")
    prompter = ScriptedPrompter(
        [
            "1",  # server
            "",  # inputs, saved/built-in
            "1",  # TCP, even though a serial device is saved
            "",  # host stays 10.9.9.9
            "",  # port
            "n",  # skip the connection test
            "n",  # no mDNS
            "n",  # no HTTP listener
            "n",  # do not write startup.txt
            "n",  # do not write the configuration
        ]
    )
    code = run_install(
        settings=_settings(env),
        prompter=prompter,
        directory=tmp_path,
        nics=[],
        probe=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("probe should not run")),
        ensure=_ensure_ok([]),
    )
    assert code == 0, prompter.text()
    assert "reconfigure" in prompter.text()
    # Declining the last question leaves the file as it was, including serial.
    text = env.read_text(encoding="utf-8")
    assert "TESMART_HOST=10.9.9.9" in text
    assert "TESMART_SERIAL=/dev/tty.old" in text
    assert not (tmp_path / "startup.txt").exists()


def test_serial_without_pyserial_can_quit(tmp_path, clean_env):
    env = tmp_path / "env"

    def ensure(name: str, _prompter: ScriptedPrompter) -> bool:
        assert name == "serial"
        return False

    prompter = ScriptedPrompter(["1", "", "2", "2"])
    code = run_install(
        settings=_settings(env),
        prompter=prompter,
        directory=tmp_path,
        nics=[],
        ensure=ensure,
    )
    assert code == 1
    assert "not changed" in prompter.text()
    assert not env.exists()


def test_client_install_saves_mdns_name(tmp_path, clean_env):
    env = tmp_path / "env"
    calls: list[str] = []
    prompter = ScriptedPrompter(["2", "1", "OfficeHDMI", "n", "n", "n", "y"])
    code = run_install(
        settings=_settings(env),
        prompter=prompter,
        directory=tmp_path,
        nics=[],
        probe_name=lambda _name: (_ for _ in ()).throw(AssertionError("browse should not run")),
        ensure=_ensure_ok(calls),
    )
    assert code == 0, prompter.text()
    assert calls == ["mdns"]
    values = config.read_env_file(env)
    assert values["TESMART_MDNS_NAME"] == "OfficeHDMI"
    assert "TESMART_VIA" not in values
    assert "TESMART_HTTP" not in values
    transcript = prompter.text()
    assert "\n\nListener\n" in transcript
    assert "\n\nKit\n" in transcript
    assert "\n\nClient command\n" in transcript
    assert "tesmartctl --via auto --mdns-name OfficeHDMI status" in transcript
    assert "If this is the only server on the network, clients may omit the mDNS name:" in transcript


def test_client_url_replaces_a_saved_name(tmp_path, clean_env):
    env = tmp_path / "env"
    env.write_text("TESMART_MDNS_NAME=Old\nTESMART_VIA=http://10.0.0.1:8080\n", encoding="utf-8")
    prompter = ScriptedPrompter(
        ["2", "2", "http://192.168.4.4:8080", "n", "n", "n", "y"]
    )
    code = run_install(
        settings=_settings(env),
        prompter=prompter,
        directory=tmp_path,
        nics=[],
        ensure=_ensure_ok([]),
    )
    assert code == 0, prompter.text()
    values = config.read_env_file(env)
    assert values["TESMART_VIA"] == "http://192.168.4.4:8080"
    assert "TESMART_MDNS_NAME" not in values


def test_http_listener_records_port_and_panel_choice(tmp_path, clean_env):
    env = tmp_path / "env"
    config.write_http_listener("172.16.0.141:7272", panel=False, mdns=False, port=7272, env_file=str(env))
    values = config.read_env_file(env)
    assert values["TESMART_HTTP"] == "172.16.0.141:7272"
    assert values["TESMART_HTTP_PORT"] == "7272"
    assert values["TESMART_HTTP_PANEL"] == "0"
    assert values["TESMART_HTTP_MDNS"] == "0"


def test_quickstart_is_a_command():
    args = build_parser().parse_args(["quickstart"])
    assert args.command == "quickstart"


def test_quickstart_reads_the_saved_listener(tmp_path, clean_env):
    env = tmp_path / "env"
    env.write_text(
        "TESMART_HTTP=172.16.0.141:7272\n"
        "TESMART_HTTP_PANEL=1\n"
        "TESMART_HTTP_MDNS=1\n"
        "TESMART_MDNS_NAME=Office\n",
        encoding="utf-8",
    )
    ready = prepare(str(env))
    assert (ready.host, ready.port, ready.panel, ready.mdns, ready.mdns_name) == (
        "172.16.0.141",
        7272,
        True,
        True,
        "Office",
    )
    local = tmp_path / "local"
    local.write_text("TESMART_HTTP=8080\nTESMART_HTTP_MDNS=0\n", encoding="utf-8")
    plain = prepare(str(local))
    assert (plain.host, plain.port, plain.panel, plain.mdns, plain.mdns_name) == (
        "127.0.0.1",
        8080,
        True,
        False,
        None,
    )
    hidden = tmp_path / "hidden"
    hidden.write_text("TESMART_HTTP=8080\nTESMART_HTTP_PORT=9090\nTESMART_HTTP_PANEL=0\n", encoding="utf-8")
    quiet = prepare(str(hidden))
    assert (quiet.port, quiet.panel) == (9090, False)


def test_quickstart_offers_install_when_the_listener_is_missing(tmp_path, clean_env, capsys):
    env = tmp_path / "env"
    env.write_text("TESMART_HOST=10.1.2.3\n", encoding="utf-8")
    asked = ScriptedPrompter(["n"])
    calls: list[str] = []

    def configure() -> int:
        calls.append("configure")
        return 0

    def serve(_ready) -> int:
        calls.append("serve")
        return 0

    code = run_quickstart(str(env), configure=configure, serve=serve, prompter=asked)
    assert code == 1
    assert calls == []
    text = asked.text()
    assert "quickstart does not have the required information to start." in text
    assert "No HTTP listener address is saved." in text
    assert "Would you like to configure it now? [Y/n]" in text

    agreed = ScriptedPrompter(["y"])

    def configure_and_save() -> int:
        calls.append("configure")
        env.write_text(
            "TESMART_HTTP=10.1.2.9:7272\nTESMART_HTTP_PANEL=1\nTESMART_HTTP_MDNS=0\n",
            encoding="utf-8",
        )
        return 0

    code = run_quickstart(str(env), configure=configure_and_save, serve=serve, prompter=agreed)
    assert code == 0
    assert calls == ["configure", "serve"]

    declined_shell = run_quickstart(str(tmp_path / "empty"), configure=configure, serve=serve, interactive=False)
    assert declined_shell == 1
    err = capsys.readouterr().err
    assert "does not have the required information to start" in err
    assert "tesmartctl install" in err


def test_quickstart_asks_once_when_install_still_has_no_listener(tmp_path, clean_env):
    env = tmp_path / "env"
    env.write_text("TESMART_HTTP=off\n", encoding="utf-8")
    prompter = ScriptedPrompter(["y"])

    def configure() -> int:
        return 0

    code = run_quickstart(
        str(env),
        configure=configure,
        serve=lambda _ready: 0,
        prompter=prompter,
    )
    assert code == 1
    assert prompter.answers == []
    assert "Would you like to configure it now?" in prompter.text()
    assert "still does not have the required information" in prompter.text()
    assert prompt_to_configure("The HTTP listener is off in the saved configuration.", prompter=ScriptedPrompter(["n"])) is False


def test_quickstart_requires_a_saved_address(tmp_path, clean_env):
    missing = tmp_path / "env"
    missing.write_text("TESMART_HOST=10.1.2.3\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="No HTTP listener address"):
        prepare(str(missing))
    missing.write_text("TESMART_HTTP=off\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="HTTP listener is off"):
        prepare(str(missing))


def test_install_reuses_the_saved_listener_address(tmp_path, clean_env):
    env = tmp_path / "env"
    env.write_text(
        "TESMART_HOST=10.9.9.9\n"
        "TESMART_HTTP=10.1.2.9:7272\n"
        "TESMART_HTTP_PANEL=1\n"
        "TESMART_HTTP_MDNS=1\n"
        "TESMART_MDNS_NAME=Office\n",
        encoding="utf-8",
    )
    prompter = ScriptedPrompter(
        [
            "1",
            "",
            "",
            "",
            "",
            "n",
            "",
            "",
            "",
            "",
            "",
            "",
            "n",
            "y",
        ]
    )
    code = run_install(
        settings=_settings(env),
        prompter=prompter,
        directory=tmp_path,
        nics=[Nic("en0", "10.1.2.9")],
        ensure=_ensure_ok([]),
    )
    assert code == 0, prompter.text()
    values = config.read_env_file(env)
    assert values["TESMART_HTTP"] == "10.1.2.9:7272"
    assert values["TESMART_HTTP_PORT"] == "7272"
    assert values["TESMART_HTTP_PANEL"] == "1"
    assert values["TESMART_HTTP_MDNS"] == "1"
    assert values["TESMART_MDNS_NAME"] == "Office"
    assert "saved configuration" in prompter.text()
