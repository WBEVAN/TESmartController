"""Kit window preferences stay in kit.json, including the key-face options."""

from tesmart.kit_prefs import KitPrefs


def test_hide_disabled_and_display_names_round_trip(tmp_path):
    path = tmp_path / "kit.json"
    saved = KitPrefs(path).update(
        {"body": "silver", "hide_disabled": True, "show_names": False, "not_a_pref": True}
    )
    assert saved["body"] == "silver"
    assert saved["hide_disabled"] is True
    assert saved["show_names"] is False
    assert "not_a_pref" not in saved

    again = KitPrefs(path)
    assert again.get("hide_disabled") is True
    assert again.get("show_names") is False
    assert again.get("body") == "silver"
