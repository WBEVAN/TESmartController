"""``--debug`` and ``--version`` plus the listener-choice line they print."""

import os

import pytest

from tesmart.cli import build_parser
from tesmart.debug import log, log_process, set_enabled
from tesmart.kit_window import probe_failure_detail
from tesmart.mdns import DiscoveredListener, address_rank_label, describe_listener
from tesmart.version import package_version


def test_version_before_command(capsys):
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["--version"])
    assert caught.value.code == 0
    assert package_version() in capsys.readouterr().out


def test_version_after_command(capsys):
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["kit", "--version"])
    assert caught.value.code == 0
    assert package_version() in capsys.readouterr().out


def test_debug_process_line_includes_the_pid(capsys):
    set_enabled(True)
    try:
        log_process()
    finally:
        set_enabled(False)
    assert f"debug: pid {os.getpid()}" in capsys.readouterr().err


def test_debug_before_or_after_the_command():
    before = build_parser().parse_args(["--debug", "examples"])
    after = build_parser().parse_args(["kit", "--debug"])
    plain = build_parser().parse_args(["examples"])
    assert before.debug is True
    assert after.debug is True
    assert getattr(plain, "debug", False) is False


def test_netdebug_is_accepted_with_debug():
    both = build_parser().parse_args(["--debug", "--netdebug", "status"])
    after = build_parser().parse_args(["kit", "--netdebug"])
    plain = build_parser().parse_args(["status"])
    assert both.debug is True and both.netdebug is True
    assert after.netdebug is True
    assert getattr(plain, "netdebug", False) is False


def test_format_adapter_line_marks_a_tunnel():
    from tesmart.mdns import format_adapter_line

    assert format_adapter_line("en0", "Ethernet", ["192.168.1.40"], vpn=False) == (
        "if en0 (Ethernet) [use] 192.168.1.40 (lan)"
    )
    assert "skip vpn" in format_adapter_line("utun4", "", ["100.64.0.8"], vpn=True)
    assert "(cgnat)" in format_adapter_line("utun4", "", ["100.64.0.8"], vpn=True)


def test_kit_window_debug_names_pywebview_and_the_window():
    from tesmart.kit_window import kit_window_debug_lines

    installed = kit_window_debug_lines(
        installed=True,
        version="5.4",
        backend="chromium",
        browser="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
    assert installed == [
        "pywebview is installed (5.4)",
        "using chromium window (Google Chrome)",
    ]
    missing = kit_window_debug_lines(installed=False, backend="webview")
    assert missing == [
        "pywebview is not installed",
        "using pywebview window",
    ]
    fallback = kit_window_debug_lines(
        installed=True,
        version="5.4",
        backend="chromium",
        browser="chromium",
        fallback="pywebview did not open: no display",
    )
    assert fallback[-1] == "pywebview did not open: no display"


def test_debug_log_is_silent_until_enabled(capsys):
    set_enabled(False)
    log("hidden")
    set_enabled(True)
    try:
        log("shown")
    finally:
        set_enabled(False)
    err = capsys.readouterr().err
    assert "hidden" not in err
    assert "debug: shown" in err


def test_describe_listener_marks_lan_ahead_of_cgnat():
    service = DiscoveredListener(
        instance_name="OfficeHDMI",
        port=8080,
        addresses=("100.64.0.1", "192.168.1.20"),
        hostname="office.local",
    )
    text = describe_listener(service)
    assert address_rank_label("192.168.1.20") == "lan"
    assert address_rank_label("100.64.0.1") == "cgnat"
    assert "192.168.1.20 (lan)" in text
    assert "100.64.0.1 (cgnat)" in text
    assert text.endswith("chosen=http://192.168.1.20:8080")


def test_probe_failure_names_the_host():
    assert probe_failure_detail(
        "http://192.168.1.20:8080/kit?app=1",
        "No route to host",
    ) == "No route to host · 192.168.1.20:8080"
