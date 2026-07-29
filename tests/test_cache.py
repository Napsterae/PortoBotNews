"""
Tests for URL cache deduplication.
"""
from datetime import datetime, timedelta
from bot.cache import filter_new_urls, load_cache, save_cache, mark_as_processed


class TestFilterNewUrls:
    def test_all_new_urls(self, tmp_path):
        import bot.cache as cache_mod
        cache_mod.CACHE_PATH = tmp_path / "cache.json"

        results = [
            {"url": "https://example.com/a", "title": "A", "snippet": "sa"},
            {"url": "https://example.com/b", "title": "B", "snippet": "sb"},
        ]
        new = filter_new_urls(results)
        assert len(new) == 2

    def test_some_seen_before(self, tmp_path):
        import bot.cache as cache_mod
        cache_mod.CACHE_PATH = tmp_path / "cache.json"
        # Pre-populate cache by marking results as processed (writes correct sig)
        mark_as_processed([
            {"url": "https://example.com/a", "title": "A", "snippet": "sa"},
        ])

        results = [
            {"url": "https://example.com/a", "title": "A", "snippet": "sa"},
            {"url": "https://example.com/b", "title": "B", "snippet": "sb"},
        ]
        new = filter_new_urls(results)
        assert len(new) == 1
        assert new[0]["url"] == "https://example.com/b"

    def test_all_seen(self, tmp_path):
        import bot.cache as cache_mod
        cache_mod.CACHE_PATH = tmp_path / "cache.json"
        results = [
            {"url": "https://example.com/a", "title": "A", "snippet": "sa"},
            {"url": "https://example.com/b", "title": "B", "snippet": "sb"},
        ]
        mark_as_processed(results)

        new = filter_new_urls(results)
        assert len(new) == 0

    def test_updated_article_resurfaces(self, tmp_path):
        """An article whose content changed since last run is NOT deduped."""
        import bot.cache as cache_mod
        cache_mod.CACHE_PATH = tmp_path / "cache.json"
        # First run: mark article as processed
        old = [{"url": "https://example.com/a", "title": "Old title", "snippet": "old"}]
        mark_as_processed(old)

        # Second run: same URL but title changed (bid raised, transfer completed)
        updated = [{"url": "https://example.com/a", "title": "CONFIRMED deal", "snippet": "new"}]
        new = filter_new_urls(updated)
        assert len(new) == 1  # should NOT be filtered — content changed


class TestMarkAsProcessed:
    def test_adds_urls(self, tmp_path):
        import bot.cache as cache_mod
        cache_mod.CACHE_PATH = tmp_path / "cache.json"

        results = [
            {"url": "https://example.com/a", "title": "A", "snippet": "sa"},
            {"url": "https://example.com/b", "title": "B", "snippet": "sb"},
        ]
        mark_as_processed(results)

        cache = load_cache()
        assert "https://example.com/a" in cache
        assert "https://example.com/b" in cache

    def test_legacy_format_migration(self, tmp_path):
        """Old cache format ({url: iso_string}) is migrated without data loss."""
        import bot.cache as cache_mod
        cache_mod.CACHE_PATH = tmp_path / "cache.json"
        # Write legacy format directly
        save_cache({"https://example.com/a": datetime.now().isoformat()})
        # Migration: load_cache should accept it; the URL key still exists
        cache = load_cache()
        assert "https://example.com/a" in cache
        # Legacy entries have empty sig → treated as "changed" next time seen
        assert cache["https://example.com/a"].get("sig", "") == ""

    def test_expired_entries_pruned(self, tmp_path):
        """Entries older than CACHE_MAX_AGE_DAYS are removed on load."""
        import bot.cache as cache_mod
        cache_mod.CACHE_PATH = tmp_path / "cache.json"
        old_date = (datetime.now() - timedelta(days=30)).isoformat()
        save_cache({"https://example.com/old": {"date": old_date, "sig": "abc"}})
        cache = load_cache()
        assert "https://example.com/old" not in cache
