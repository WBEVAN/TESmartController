"""Portable listener config export / import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesmart import config
from tesmart.banner import BannerBoard
from tesmart.config_bundle import (
    BundleError,
    apply_bundle,
    export_bundle,
    load_bundle,
    parse_bundle,
    summarize_bundle,
    write_bundle,
)
from tesmart.kit_prefs import KitPrefs


@pytest.fixture
def env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "env"
    monkeypatch.setenv("TESMART_ENV_FILE", str(path))
    for key in (
        "TESMART_HOST",
        "TESMART_PORT",
        "TESMART_SERIAL",
        "TESMART_INPUTS",
    ):
        monkeypatch.delenv(key, raising=False)
    return path


def test_export_includes_names_messages_and_kit(env_path: Path, tmp_path: Path) -> None:
    config.set_default("host", "10.0.0.8", str(env_path))
    config.set_input_name(2, "Office", str(env_path))
    config.write_banner_history(
        [
            {
                "id": 1,
                "text": "Door unlocked",
                "from": "Hubitat",
                "at": 1.0,
                "at_iso": "2026-01-01T00:00:00Z",
                "mode": "timed",
                "seconds": 8,
            }
        ],
        str(env_path),
    )
    kit_path = tmp_path / "kit.json"
    KitPrefs(kit_path, fresh=True).update({"body": "silver", "lcd": "amber", "show_names": True})

    bundle = export_bundle(str(env_path), include_kit=True, kit_path=kit_path)
    assert bundle["format"] == "tesmartctl-config"
    assert bundle["env"]["TESMART_HOST"] == "10.0.0.8"
    assert bundle["env"]["TESMART_NAME_2"] == "Office"
    history = json.loads(bundle["env"]["TESMART_BANNER_HISTORY"])
    assert history[0]["text"] == "Door unlocked"
    assert history[0]["from"] == "Hubitat"
    assert bundle["kit"]["body"] == "silver"
    assert bundle["kit"]["lcd"] == "amber"

    summary = summarize_bundle(bundle)
    assert summary["names"] == 1
    assert summary["messages"] == 1
    assert summary["has_kit"] is True


def test_round_trip_replace(env_path: Path, tmp_path: Path) -> None:
    config.set_default("host", "192.168.1.10", str(env_path))
    config.set_input_name(1, "KeepMe", str(env_path))
    source = tmp_path / "source.env"
    config.write_env_file(
        source,
        {
            "TESMART_HOST": "10.1.2.3",
            "TESMART_PORT": "5000",
            "TESMART_INPUTS": "16",
            "TESMART_NAME_3": "Apple TV",
            "TESMART_DISABLED": "4",
            "TESMART_BANNER_HISTORY": json.dumps(
                [{"id": 2, "text": "Meeting in 5", "from": "Calendar", "mode": "sticky", "at": 2.0}]
            ),
        },
    )
    bundle = export_bundle(str(source), include_kit=False)
    out = tmp_path / "office.json"
    write_bundle(out, bundle)

    loaded = load_bundle(out)
    result = apply_bundle(loaded, str(env_path), include_kit=False, replace=True)
    assert result["keys"] >= 4
    values = config.read_env_file(env_path)
    assert values["TESMART_HOST"] == "10.1.2.3"
    assert values["TESMART_NAME_3"] == "Apple TV"
    assert "TESMART_NAME_1" not in values
    history = config.read_banner_history(str(env_path))
    assert history[0]["text"] == "Meeting in 5"
    assert history[0]["from"] == "Calendar"


def test_merge_keeps_unrelated_keys(env_path: Path) -> None:
    config.write_env_file(
        env_path,
        {"TESMART_HOST": "1.1.1.1", "TESMART_NAME_1": "Local", "TESMART_VIA": "http://old"},
    )
    bundle = parse_bundle(
        {
            "format": "tesmartctl-config",
            "version": 1,
            "env": {"TESMART_HOST": "2.2.2.2", "TESMART_NAME_2": "Imported"},
        }
    )
    apply_bundle(bundle, str(env_path), include_kit=False, replace=False)
    values = config.read_env_file(env_path)
    assert values["TESMART_HOST"] == "2.2.2.2"
    assert values["TESMART_NAME_1"] == "Local"
    assert values["TESMART_NAME_2"] == "Imported"
    assert values["TESMART_VIA"] == "http://old"


def test_reject_bad_format() -> None:
    with pytest.raises(BundleError, match="unknown format"):
        parse_bundle({"format": "nope", "version": 1, "env": {}})


def test_banner_board_replace_history_persists_from(tmp_path: Path) -> None:
    saved: list[list[dict]] = []

    def persist(entries: list[dict]) -> None:
        saved.append(list(entries))

    board = BannerBoard(persist=persist)
    board.show("One", 5, from_="A")
    board.replace_history(
        [
            {
                "id": 9,
                "text": "Imported",
                "from": "Hubitat",
                "mode": "timed",
                "seconds": 8,
                "at": 3.0,
            }
        ]
    )
    assert board.history()[0]["text"] == "Imported"
    assert board.history()[0]["from"] == "Hubitat"
    assert board.status()["showing"] is False
    assert saved[-1][0]["from"] == "Hubitat"


def test_banner_history_round_trips_from(env_path: Path) -> None:
    config.write_banner_history(
        [{"id": 1, "text": "Hi", "from": "Hubitat", "mode": "timed", "seconds": 5, "at": 1.0}],
        str(env_path),
    )
    history = config.read_banner_history(str(env_path))
    assert history[0]["from"] == "Hubitat"
