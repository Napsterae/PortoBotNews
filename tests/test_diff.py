"""
Tests for change detection.
"""
from bot.diff import content_hash


class TestContentHash:
    def test_same_content_same_hash(self):
        text1 = "Hello World"
        text2 = "Hello World"
        assert content_hash(text1) == content_hash(text2)

    def test_different_content_different_hash(self):
        text1 = "Hello World"
        text2 = "Hello, World!"
        assert content_hash(text1) != content_hash(text2)

    def test_normalized_whitespace(self):
        text1 = "Hello   World"
        text2 = "Hello World"
        assert content_hash(text1) == content_hash(text2)

    def test_empty_string(self):
        h = content_hash("")
        assert isinstance(h, str)
        assert len(h) == 64
