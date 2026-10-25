"""Enable and disable are stored with the listener. They skip selection and next/previous."""

from __future__ import annotations

import pytest

from tesmart import config
from tesmart.cli import build_parser, cmd_set_enabled, main
from tesmart.panel import record_input_enabled
from tesmart.switch import next_input, previous_input
from tesmart.web import _parse_request


def test_inputs_start_enabled(tmp_path):
    env = str(tmp_path / "env")
    assert config.read_disabled(env) == frozenset()
    assert config.input_is_enabled(4, env)


def test_disable_and_enable_round_trip(tmp_path):
    env = str(tmp_path / "env")
    config.set_input_enabled(4, False, env, input_count=8)
    assert config.read_disabled(env) == frozenset({4})
    config.set_input_enabled(4, True, env, input_count=8)
    assert config.read_disabled(env) == frozenset()
    assert "TESMART_DISABLED" not in config.read_env_file(config.env_file_path(env))


def test_last_enabled_input_cannot_be_disabled(tmp_path):
    env = str(tmp_path / "env")
    for number in range(1, 8):
        config.set_input_enabled(number, False, env, input_count=8)
    with pytest.raises(config.ConfigError, match="at least one input must stay enabled"):
        config.set_input_enabled(8, False, env, input_count=8)
    assert config.input_is_enabled(8, env)


def test_saved_cycle_keeps_disabled_members_and_steps_skip_them(tmp_path):
    env = str(tmp_path / "env")
    config.set_rotate((1, 2, 4, 8), env, input_count=8)
    config.set_input_enabled(2, False, env, input_count=8)
    config.set_input_enabled(4, False, env, input_count=8)
    assert config.read_rotate(env) == (1, 2, 4, 8)
    pool = config.enabled_cycle(8, config.read_rotate(env), env)
    assert pool == (1, 8)
    assert next_input(1, 8, pool) == 8
    assert next_input(3, 8, pool) == 8
    assert previous_input(8, 8, pool) == 1


def test_cycle_of_only_disabled_inputs_is_rejected(tmp_path):
    env = str(tmp_path / "env")
    config.set_input_enabled(2, False, env, input_count=8)
    with pytest.raises(config.ConfigError, match="every input in this cycle is disabled"):
        config.enabled_cycle(8, (2,), env)


def test_cli_enable_and_disable_accept_a_name(tmp_path, capsys):
    env = str(tmp_path / "env")
    config.set_input_name(2, "Office", env, input_count=8)
    parser = build_parser()
    disabled = parser.parse_args(["--env-file", env, "--inputs", "8", "disable", "Office"])
    assert cmd_set_enabled(disabled, enabled=False) == 0
    assert "disabled" in capsys.readouterr().out
    assert config.read_disabled(env) == frozenset({2})
    enabled = parser.parse_args(["--env-file", env, "--inputs", "8", "enable", "2"])
    assert cmd_set_enabled(enabled, enabled=True) == 0
    assert config.read_disabled(env) == frozenset()


def test_status_lists_enabled_and_disabled(tmp_path):
    env = str(tmp_path / "env")
    config.set_input_name(2, "Office", env, input_count=8)
    config.set_input_enabled(4, False, env, input_count=8)
    config.set_input_enabled(5, False, env, input_count=8)
    report = config.input_enablement(8, env)
    assert report["enabled"] == [1, 2, 3, 6, 7, 8]
    assert report["disabled"] == [4, 5]
    enabled_text, disabled_text = config.format_input_enablement(
        report["enabled"],
        report["disabled"],
        {"2": "Office"},
    )
    assert enabled_text == "1, 2 (Office), 3, 6, 7, 8"
    assert disabled_text == "4, 5"


def test_cli_rejects_disabling_the_last_input(tmp_path, capsys):
    env = str(tmp_path / "env")
    for number in range(1, 8):
        config.set_input_enabled(number, False, env, input_count=8)
    parser = build_parser()
    args = parser.parse_args(["--env-file", env, "--inputs", "8", "disable", "8"])
    assert cmd_set_enabled(args, enabled=False) == 2
    assert "at least one input" in capsys.readouterr().err
    assert config.input_is_enabled(8, env)


def test_short_routes_accept_a_number_or_a_name():
    numbered = _parse_request("/disable/4")
    assert numbered.verb == "disable"
    assert numbered.number == 4
    named = _parse_request("/enable/Office")
    assert named.verb == "enable"
    assert named.label == "Office"
    query = _parse_request("/enable?input=2")
    assert query.verb == "enable"
    assert query.number == 2
    with pytest.raises(ValueError, match="missing input"):
        _parse_request("/disable")


def test_record_input_enabled_notifies_clients(tmp_path):
    env = str(tmp_path / "env")
    noted: list[str] = []

    class _Hub:
        def note_enabled(self, *, source: str) -> None:
            noted.append(source)

    class _Switch:
        input_count = 8

    class _Server:
        env_file = env
        switch = _Switch()
        callbacks = _Hub()

    payload = record_input_enabled(_Server(), 4, False)
    assert payload["enabled"] is False
    assert payload["disabled"] == [4]
    assert config.read_disabled(env) == frozenset({4})
    assert noted == ["command"]


def test_via_enable_calls_the_listener_and_leaves_this_computer(tmp_path, monkeypatch, capsys):
    env = str(tmp_path / "env")
    seen: dict[str, str] = {}

    def resolve_base_url(**_kwargs):
        return "http://listener:8080"

    def request_json(base, path, **_kwargs):
        seen["base"] = base
        seen["path"] = path
        return {"input": 4, "enabled": True, "disabled": [], "inputs": [{"number": 4, "name": "Office", "enabled": True}]}

    monkeypatch.setattr("tesmart.via_client.resolve_base_url", resolve_base_url)
    monkeypatch.setattr("tesmart.via_client.request_json", request_json)
    assert main(["--env-file", env, "--via", "http://listener:8080", "enable", "4"]) == 0
    assert seen == {"base": "http://listener:8080", "path": "/enable/4"}
    assert config.read_disabled(env) == frozenset()
    assert "Input 4 (Office) enabled." in capsys.readouterr().out
