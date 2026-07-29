"""
Tests for prompt building, refetch parsing, and stripping.
"""
from bot.prompt import parse_refetch_requests, strip_refetch_section


class TestParseRefetchRequests:
    def test_no_refetch_section(self):
        draft, queries = parse_refetch_requests("# Post content\n\nSome markdown")
        assert "# Post content" in draft
        assert queries == []

    def test_with_refetch_queries(self):
        output = """# Post content

Some markdown table here.

<!-- REFETCH_REQUESTS_START -->
- "Diogo Costa Chelsea transfer latest"
- "Alan Varela Liverpool interest"
<!-- REFETCH_REQUESTS_END -->"""
        draft, queries = parse_refetch_requests(output)
        assert "# Post content" in draft
        assert len(queries) == 2
        assert "Diogo Costa Chelsea transfer latest" in queries
        assert "Alan Varela Liverpool interest" in queries

    def test_empty_refetch(self):
        output = """# Post content

Some text.

<!-- REFETCH_REQUESTS_START -->
<!-- REFETCH_REQUESTS_END -->"""
        draft, queries = parse_refetch_requests(output)
        assert "# Post content" in draft
        assert queries == []

    def test_asterisk_bullets(self):
        output = """# Content

<!-- REFETCH_REQUESTS_START -->
* "Query one"
* "Query two"
<!-- REFETCH_REQUESTS_END -->"""
        draft, queries = parse_refetch_requests(output)
        assert len(queries) == 2
        assert "Query one" in queries

    def test_extra_whitespace(self):
        output = """# Content

<!--   REFETCH_REQUESTS_START   -->
-   "  query with spaces  "
<!--   REFETCH_REQUESTS_END   -->"""
        draft, queries = parse_refetch_requests(output)
        assert len(queries) == 1
        assert queries[0] == "query with spaces"


class TestStripRefetchSection:
    def test_strip_refetch(self):
        text = "# Content\n\nSome table\n\n<!-- REFETCH_REQUESTS_START -->\n- query\n<!-- REFETCH_REQUESTS_END -->\n\nFooter"
        result = strip_refetch_section(text)
        assert "<!-- REFETCH_REQUESTS_START -->" not in result
        assert "<!-- REFETCH_REQUESTS_END -->" not in result
        assert "query" not in result
        assert "# Content" in result

    def test_no_refetch_unchanged(self):
        text = "# Just content\n\nNo refetch here."
        result = strip_refetch_section(text)
        assert result == text
