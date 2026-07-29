"""
Tests for market window detection and post title generation.
"""
from datetime import datetime
from bot.market import get_market_window, build_post_title


class TestMarketWindow:
    def test_summer_june(self, monkeypatch):
        monkeypatch.setattr("bot.market.datetime", _mock_datetime(2026, 6, 15))
        season, year = get_market_window()
        assert season == "Verão"
        assert year == "2026"

    def test_summer_july(self, monkeypatch):
        monkeypatch.setattr("bot.market.datetime", _mock_datetime(2026, 7, 1))
        season, year = get_market_window()
        assert season == "Verão"
        assert year == "2026"

    def test_summer_august(self, monkeypatch):
        monkeypatch.setattr("bot.market.datetime", _mock_datetime(2026, 8, 20))
        season, year = get_market_window()
        assert season == "Verão"
        assert year == "2026"

    def test_winter_january(self, monkeypatch):
        monkeypatch.setattr("bot.market.datetime", _mock_datetime(2026, 1, 15))
        season, year = get_market_window()
        assert season == "Inverno"
        assert year == "2025/26"

    def test_winter_december(self, monkeypatch):
        monkeypatch.setattr("bot.market.datetime", _mock_datetime(2026, 12, 15))
        season, year = get_market_window()
        assert season == "Inverno"
        assert year == "2026/27"

    def test_early_year_january(self, monkeypatch):
        monkeypatch.setattr("bot.market.datetime", _mock_datetime(2027, 1, 5))
        season, year = get_market_window()
        assert season == "Inverno"
        assert year == "2026/27"

    def test_february(self, monkeypatch):
        monkeypatch.setattr("bot.market.datetime", _mock_datetime(2026, 2, 15))
        season, year = get_market_window()
        # February is outside normal windows - should default to Verão
        assert season == "Verão"
        assert year == "2026"


class TestBuildPostTitle:
    def test_summer_title(self):
        title = build_post_title("Mercado de Verão 2026")
        assert title == "🐉 FC Porto — Mercado de Verão 2026"

    def test_winter_title(self):
        title = build_post_title("Mercado de Inverno 2026/27")
        assert title == "🐉 FC Porto — Mercado de Inverno 2026/27"


def _mock_datetime(year: int, month: int, day: int):
    """Create a mock datetime module for monkeypatching."""
    class MockDateTime:
        def __init__(self):
            self.now = lambda: datetime(year, month, day)
    return MockDateTime()
