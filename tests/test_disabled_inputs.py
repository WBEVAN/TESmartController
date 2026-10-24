"""Enable and disable are local. They skip selection and next/previous."""

from __future__ import annotations

import pytest

from tesmart import config
from tesmart.cli import build_parser, cmd_set_enabled
from tesmart.switch import next_input, previous_input


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
