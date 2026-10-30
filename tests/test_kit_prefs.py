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


def test_lcd_brightness_round_trip_and_bounds(tmp_path):
    path = tmp_path / "kit.json"
    saved = KitPrefs(path).update({"lcd_brightness": 75})
    assert saved["lcd_brightness"] == 75
    assert KitPrefs(path).get("lcd_brightness") == 75

    # Out of range / wrong type are dropped (default is full brightness in the UI).
    again = KitPrefs(path).update({"lcd_brightness": 20})
    assert "lcd_brightness" not in again
    dropped = KitPrefs(path).update({"lcd_brightness": "bright"})
    assert "lcd_brightness" not in dropped


def test_lcd_dim_prefs_round_trip_and_bounds(tmp_path):
    path = tmp_path / "kit.json"
    saved = KitPrefs(path).update({"lcd_dim": True, "lcd_dim_seconds": 45})
    assert saved["lcd_dim"] is True
    assert saved["lcd_dim_seconds"] == 45
    again = KitPrefs(path)
    assert again.get("lcd_dim") is True
    assert again.get("lcd_dim_seconds") == 45

    cleared = KitPrefs(path).update({"lcd_dim": False, "lcd_dim_seconds": 200})
    assert cleared["lcd_dim"] is False
    assert "lcd_dim_seconds" not in cleared
