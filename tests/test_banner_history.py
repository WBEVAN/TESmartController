"""Banner history keeps at most HISTORY_MAX entries (oldest dropped)."""

from __future__ import annotations

from tesmart.banner import DEFAULT_FROM, HISTORY_MAX, BannerBoard


def test_eleventh_message_drops_oldest() -> None:
    board = BannerBoard()
    for index in range(1, HISTORY_MAX + 2):
        board.show(f"message {index}", 5)
    history = board.history()
    assert len(history) == HISTORY_MAX
    assert history[0]["text"] == f"message {HISTORY_MAX + 1}"
    assert history[-1]["text"] == "message 2"
    assert [entry["id"] for entry in history] == list(range(HISTORY_MAX + 1, 1, -1))


def test_clear_empties_history() -> None:
    board = BannerBoard()
    board.show("one", 5)
    board.show("two", sticky=True)
    board.clear()
    assert board.history() == []
    assert board.status() is None


def test_from_defaults_to_message() -> None:
    board = BannerBoard()
    snap = board.show("hello", 5)
    assert snap["from"] == DEFAULT_FROM
    assert board.history()[0]["from"] == DEFAULT_FROM


def test_from_custom_label() -> None:
    board = BannerBoard()
    snap = board.show("Door unlocked", 8, from_="Hubitat")
    assert snap["from"] == "Hubitat"
    assert board.history()[0]["from"] == "Hubitat"
    board.show("Alarm", sticky=True, from_="Alarm")
    assert board.status()["from"] == "Alarm"
