"""Simulator mode answers input changes without a TESmart switch."""

import io

from tesmart.cli import build_parser
from tesmart.listener import serve_stdio
from tesmart.peek import Peeker
from tesmart.simulate import SimulatedSwitch
from tesmart.switch import SwitchError


def test_simulate_flags_are_on_listen_and_quickstart():
    listen = build_parser().parse_args(["listen", "--simulate", "--http", "8080"])
    quick = build_parser().parse_args(["quickstart", "--simulate"])
    assert listen.simulate is True
    assert quick.simulate is True
    assert build_parser().parse_args(["listen"]).simulate is False


def test_simulated_switch_tracks_input_peek_and_cycle():
    switch = SimulatedSwitch(input_count=8)
    assert switch.endpoint == "sim://8"
    assert switch.get_active_input() == 1

    assert switch.set_input(4) == 4
    previous, reported = switch.rotate(only=(1, 4, 6))
    assert (previous, reported) == (4, 6)

    peeker = Peeker(switch)
    peeker.start(2, 5)
    assert switch.get_active_input() == 2
    assert peeker.cancel() is True
    result = peeker.wait()
    assert result.active_input == 6
    assert switch.get_active_input() == 6

    status = switch.read_status().to_dict()
    assert status["endpoint"] == "sim://8"
    assert status["active_input"] == 6
    assert status["network"]["ip"] == "192.168.1.10"

    written = switch.write_network_config(ip="10.1.1.5")
    assert written.verified["ip"] is True
    assert switch.get_network_config().ip == "10.1.1.5"
    assert switch._pump(0) == []


def test_simulated_switch_rejects_an_unknown_input():
    switch = SimulatedSwitch(input_count=8)
    try:
        switch.set_input(9)
    except SwitchError as exc:
        assert "between 1 and 8" in str(exc)
    else:
        raise AssertionError("expected SwitchError")


def test_stdio_listener_simulates_a_session(tmp_path):
    switch = SimulatedSwitch(input_count=8)
    env = tmp_path / "env"
    env.write_text("")
    out = io.StringIO()
    code = serve_stdio(
        switch,
        stdin=io.StringIO("set 3\nnext\nquit\n"),
        stdout=out,
        env_file=str(env),
    )
    assert code == 0
    assert out.getvalue().splitlines() == ["3", "4"]
    assert switch.get_active_input() == 4
