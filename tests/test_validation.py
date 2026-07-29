"""
Tests for content validation before publishing.
"""
from bot.validation import (
    validate_urls_against_search_results,
    validate_markdown_tables,
    validate_no_banned_sources,
    validate_player_names,
    validate_content,
)


class TestValidateURLs:
    def test_all_urls_valid(self):
        search = [
            {"url": "https://example.com/news/1"},
            {"url": "https://example.com/news/2"},
        ]
        content = "[source](https://example.com/news/1) and [source2](https://example.com/news/2)"
        result = validate_urls_against_search_results(content, search)
        assert result == []

    def test_hallucinated_url(self):
        search = [{"url": "https://example.com/news/1"}]
        content = "[source](https://example.com/news/1) and [fake](https://fake.com/story)"
        result = validate_urls_against_search_results(content, search)
        assert len(result) == 1
        assert "https://fake.com/story" in result

    def test_all_hallucinated(self):
        search = []
        content = "[fake](https://fake.pt/story)"
        result = validate_urls_against_search_results(content, search)
        assert len(result) == 1


class TestValidateTables:
    def test_well_formed_table(self):
        content = """| Header1 | Header2 |
|---------|---------|
| Cell1   | Cell2   |"""
        issues = validate_markdown_tables(content)
        assert issues == []

    def test_mismatched_columns(self):
        content = """| H1 | H2 |
|----|----|
| C1 | C2 | C3 |"""
        issues = validate_markdown_tables(content)
        assert len(issues) == 1
        assert "expected 2" in issues[0]  # header has 2, row has 3

    def test_no_tables(self):
        content = "# Just text\n\nNo tables here."
        issues = validate_markdown_tables(content)
        assert issues == []


class TestValidateBannedSources:
    def test_no_banned(self):
        content = "Notícia do [ojogo.pt](https://ojogo.pt)"
        banned = ["fichajes.net"]
        result = validate_no_banned_sources(content, banned)
        assert result == []

    def test_banned_source_present(self):
        content = "Segundo [fichajes.net](https://fichajes.net)"
        banned = ["fichajes.net"]
        result = validate_no_banned_sources(content, banned)
        assert len(result) == 1

    def test_multiple_banned(self):
        content = "Segundo fichajes.net e EkremKonur"
        banned = ["fichajes.net", "ekremkonur"]
        result = validate_no_banned_sources(content, banned)
        assert len(result) >= 1  # at least one found


class TestValidatePlayerNames:
    def test_real_names_ok(self):
        content = "| Diogo Costa |"
        issues = validate_player_names(content)
        assert issues == []

    def test_fake_pattern_detected(self):
        content = "| Pérola do Real Madrid |"
        issues = validate_player_names(content)
        assert len(issues) >= 1


class TestValidateContent:
    def test_valid_content(self):
        search = [{"url": "https://example.com/story"}]
        content = (
            "| Jogador | Custo | Confiança | Fontes |\n"
            "|---------|-------|-----------|--------|\n"
            "| Diogo Costa | ❓ | 🟢 Alta | [source](https://example.com/story) |\n"
            "| Alan Varela | ~€15M | 🟡 Média | [source](https://example.com/story) |\n\n"
            "## Secção adicional para garantir que o conteúdo ultrapassa o mínimo de 200 caracteres\n"
            "Mais texto aqui só para encher e passar na validação de tamanho mínimo.\n"
        )
        result = validate_content(content, search)
        assert result["valid"] is True, f"Issues: {result['issues']}"
        assert result["issues"] == []

    def test_hallucinated_urls(self):
        search = [{"url": "https://real.com/story"}]
        content = "[real](https://real.com/story) e [fake](https://fake.pt/lie)"
        result = validate_content(content, search)
        assert result["valid"] is False
        assert any("Hallucinated" in i for i in result["issues"])

    def test_short_content(self):
        result = validate_content("Hi", [])
        assert result["valid"] is False
        assert any("Content too short" in i for i in result["issues"])
