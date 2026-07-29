"""
Tests for RSS feed fetching and result merging.
"""
from bot.feeds import (
    _is_porto_related,
    merge_results,
    fetch_rss_feeds,
)


class TestIsPortoRelated:
    def test_fc_porto_in_title(self):
        result = {"title": "FC Porto contrata novo avançado", "snippet": ""}
        assert _is_porto_related(result) is True

    def test_transfer_in_snippet(self):
        result = {"title": "Notícia geral", "snippet": "Há uma transferência em curso"}
        assert _is_porto_related(result) is True

    def test_dragon_keyword(self):
        result = {"title": "Dragão procura reforços", "snippet": ""}
        assert _is_porto_related(result) is True

    def test_benfica_keyword(self):
        result = {"title": "Benfica contrata jogador", "snippet": ""}
        assert _is_porto_related(result) is True

    def test_unrelated_article(self):
        result = {"title": "Resultado do jogo de ténis", "snippet": "Murray vence"}
        assert _is_porto_related(result) is False

    def test_empty_content(self):
        result = {"title": "", "snippet": ""}
        assert _is_porto_related(result) is False


class TestMergeResults:
    def test_no_duplicates(self):
        ddg = [{"url": "https://example.com/a", "title": "A"}]
        rss = [{"url": "https://example.com/b", "title": "B"}]
        merged = merge_results(ddg, rss)
        urls = [r["url"] for r in merged]
        assert len(urls) == len(set(urls))

    def test_rss_preferred_on_conflict(self):
        ddg = [{"url": "https://example.com/a", "title": "DDG title"}]
        rss = [{"url": "https://example.com/a", "title": "RSS title"}]
        merged = merge_results(ddg, rss)
        assert len(merged) == 1
        assert merged[0]["title"] == "RSS title"

    def test_empty_both(self):
        merged = merge_results([], [])
        assert merged == []

    def test_one_empty(self):
        ddg = [{"url": "https://example.com/a", "title": "A"}]
        merged = merge_results(ddg, [])
        assert len(merged) == 1
        assert merged[0]["title"] == "A"

    def test_preserves_all_unique(self):
        ddg = [
            {"url": "https://example.com/a", "title": "A"},
            {"url": "https://example.com/c", "title": "C"},
        ]
        rss = [
            {"url": "https://example.com/b", "title": "B"},
            {"url": "https://example.com/a", "title": "A from RSS"},
        ]
        merged = merge_results(ddg, rss)
        assert len(merged) == 3
        urls = {r["url"] for r in merged}
        assert urls == {"https://example.com/a", "https://example.com/b", "https://example.com/c"}


class TestFetchRssFeeds:
    def test_no_feedparser_returns_empty(self, monkeypatch):
        """Without feedparser installed, fetch returns empty gracefully."""
        import bot.feeds as feeds_mod
        monkeypatch.setattr(feeds_mod, "HAS_FEEDPARSER", False)
        results = fetch_rss_feeds()
        assert results == []

    def test_empty_feeds_list(self):
        results = fetch_rss_feeds(feeds=[])
        assert results == []
