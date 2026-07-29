"""
Tests for search query building, result formatting, and date filtering.
"""
from bot.search import (
    build_search_queries,
    format_search_results,
    extract_domains_from_sources,
    filter_by_date,
    _parse_date_from_url,
    _is_stats_domain,
    detect_sport_from_query,
    detect_sport_from_result,
    BLOCKED_DOMAINS,
)
from datetime import datetime, timedelta


class TestBuildSearchQueries:
    def test_basic_queries(self):
        queries = build_search_queries("Mercado de Verão 2026")
        assert len(queries) >= 4
        assert any("FC Porto transferências mercado verão 2026" in q for q in queries)
        assert any("FC Porto contratações entradas" in q for q in queries)
        assert any("FC Porto saídas vendas" in q for q in queries)

    def test_site_specific_queries(self):
        domains = ["ojogo.pt", "record.pt"]
        queries = build_search_queries("Mercado de Verão 2026", trustworthy_domains=domains)
        site_queries = [q for q in queries if q.startswith("site:")]
        assert len(site_queries) == 2
        assert any("ojogo.pt" in q for q in site_queries)
        assert any("record.pt" in q for q in site_queries)

    def test_sketchy_queries(self):
        domains = ["fichajes.net"]
        queries = build_search_queries("Mercado de Verão 2026", sketchy_domains=domains)
        assert any("fichajes.net" in q for q in queries)

    def test_sport_queries(self):
        queries = build_search_queries("Mercado de Verão 2026")
        sport_queries = ["andebol", "basquetebol", "hóquei", "voleibol", "futsal"]
        for sport in sport_queries:
            assert any(sport in q for q in queries), f"Missing sport query: {sport}"

    def test_rival_queries(self):
        queries = build_search_queries("Mercado de Verão 2026")
        rivals = ["Benfica", "Sporting", "Braga", "Vitória"]
        for rival in rivals:
            assert any(rival in q for q in queries), f"Missing rival query: {rival}"

    def test_winter_queries(self):
        queries = build_search_queries("Mercado de Inverno 2026/27")
        assert any("inverno" in q for q in queries)

    def test_phase_aware_queries(self):
        """Phase-aware queries should cover confirmation, rumor, and negotiation phases."""
        queries = build_search_queries("Mercado de Verão 2026")
        # Confirmation phase
        assert any("oficial" in q and "apresentou" in q for q in queries), \
            "Missing confirmation-phase query"
        assert any("assinou" in q or "contratou" in q for q in queries), \
            "Missing signing-phase query"
        # Rumor phase
        assert any("interesse" in q or "sonda" in q for q in queries), \
            "Missing rumor-phase query"
        # Negotiation phase
        assert any("acordo" in q or "negocia" in q for q in queries), \
            "Missing negotiation-phase query"

    def test_stats_domains_excluded_from_site_queries(self):
        """Stats/data domains should NOT get site: queries."""
        domains = ["ojogo.pt", "fbref.com", "record.pt", "sofascore.com"]
        queries = build_search_queries("Mercado de Verão 2026", trustworthy_domains=domains)
        site_queries = [q for q in queries if q.startswith("site:")]
        site_domains = [q.split()[0].replace("site:", "") for q in site_queries]
        assert "ojogo.pt" in site_domains
        assert "record.pt" in site_domains
        assert "fbref.com" not in site_domains, "fbref.com should be excluded from site: queries"
        assert "sofascore.com" not in site_domains, "sofascore.com should be excluded"

    def test_site_query_diversity(self):
        """Site-specific queries should use diverse terms, not all identical."""
        domains = [f"site{i}.pt" for i in range(10)]
        queries = build_search_queries("Mercado de Verão 2026", trustworthy_domains=domains)
        site_queries = [q for q in queries if q.startswith("site:")]
        # Extract the query term (everything after "site:domain ")
        terms = set()
        for q in site_queries:
            parts = q.split(" ", 1)
            if len(parts) > 1:
                terms.add(parts[1])
        assert len(terms) > 1, f"Site queries should have diverse terms, got: {terms}"


class TestFormatSearchResults:
    def test_empty(self):
        result = format_search_results([])
        assert result == "*Nenhum artigo encontrado na pesquisa.*"

    def test_single_result(self):
        results = [{"title": "FC Porto contrata avançado", "url": "https://example.com/1", "snippet": "O FC Porto..."}]
        output = format_search_results(results)
        assert "[1]" in output
        assert "FC Porto contrata avançado" in output
        assert "https://example.com/1" in output
        assert "O FC Porto..." in output

    def test_multiple_results(self):
        results = [
            {"title": "Notícia A", "url": "https://example.com/a", "snippet": "Conteúdo A"},
            {"title": "Notícia B", "url": "https://example.com/b", "snippet": "Conteúdo B"},
        ]
        output = format_search_results(results)
        assert "[1]" in output
        assert "[2]" in output
        assert "Notícia A" in output
        assert "Notícia B" in output


class TestFilterByDate:
    def test_keep_recent(self):
        results = [{"title": "Noticia recente", "snippet": "Noticia de 15 jun 2026 sobre o FC Porto", "url": ""}]
        filtered = filter_by_date(results, max_age_days=60)
        assert len(filtered) == 1

    def test_remove_old_no_date(self):
        """Results without parsable dates should be kept (benefit of the doubt)."""
        results = [{"snippet": "Notícia sem data visível", "url": ""}]
        filtered = filter_by_date(results, max_age_days=30)
        assert len(filtered) == 1


class TestYearBasedFiltering:
    """Tests for the year-based secondary date filter.

    This catches articles that have no parseable date but mention a specific
    year in their title or snippet — e.g., a 2024 article that DuckDuckGo
    re-indexed recently and that would otherwise slip through.
    """

    def test_old_year_in_title_dropped(self):
        """Article mentioning 2024 when window is 2026 should be dropped."""
        results = [{
            "title": "Pepe termina contrato com FC Porto em 2024",
            "snippet": "O capitão saiu em agosto de 2024",
            "url": "https://example.com/pepe-sai",
        }]
        filtered = filter_by_date(results, max_age_days=30, market_year="2026")
        assert len(filtered) == 0

    def test_current_year_kept(self):
        """Article mentioning 2026 when window is 2026 should be kept."""
        results = [{
            "title": "FC Porto contrata jogador em 2026",
            "snippet": "O reforço chegou em julho de 2026",
            "url": "https://example.com/reforco",
        }]
        filtered = filter_by_date(results, max_age_days=365, market_year="2026")
        assert len(filtered) == 1

    def test_previous_year_kept(self):
        """Article mentioning previous year (2025) when window is 2026 should be kept.
        Transfers that started last summer and are still relevant."""
        results = [{
            "title": "Jogador emprestado em 2025 regressa ao FC Porto",
            "snippet": "O empréstimo terminou em 2025",
            "url": "https://example.com/regresso",
        }]
        filtered = filter_by_date(results, max_age_days=365, market_year="2026")
        assert len(filtered) == 1

    def test_no_year_mention_kept(self):
        """Article with no date and no year mention should be kept (benefit of doubt)."""
        results = [{
            "title": "FC Porto contrata novo avançado",
            "snippet": "O jogador chega ao Dragão",
            "url": "https://example.com/avançado",
        }]
        filtered = filter_by_date(results, max_age_days=30, market_year="2026")
        assert len(filtered) == 1

    def test_winter_window_accepts_both_years(self):
        """Winter 2026/27 window should accept articles mentioning 2026 or 2027."""
        results = [
            {
                "title": "FC Porto contrata em janeiro 2027",
                "snippet": "",
                "url": "https://example.com/2027",
            },
            {
                "title": "FC Porto vende em dezembro 2026",
                "snippet": "",
                "url": "https://example.com/2026",
            },
            {
                "title": "Transferência antiga de 2024",
                "snippet": "Saiu em 2024",
                "url": "https://example.com/2024",
            },
        ]
        filtered = filter_by_date(results, max_age_days=365, market_year="2026/27")
        assert len(filtered) == 2  # 2026 and 2027 kept, 2024 dropped

    def test_pepe_scenario(self):
        """Reproduce the exact bug: old Pepe article with no date, mentioning 2024."""
        results = [{
            "title": "Capitão Pepe termina contrato com FC Porto e tem futuro em aberto",
            "snippet": "Pepe deixou o FC Porto em 2024 após o fim do contrato",
            "url": "https://www.cmjornal.pt/desporto/futebol/detalhe/capitao-pepe-termina-contrato-com-fc-porto",
        }]
        filtered = filter_by_date(results, max_age_days=30, market_year="2026")
        assert len(filtered) == 0, "Old 2024 Pepe article should be filtered out"

    def test_pepe_without_year_kept(self):
        """If the Pepe article has NO year mention at all, it's kept (benefit of doubt).
        This is the edge case the LLM prompt handles with temporal awareness."""
        results = [{
            "title": "Capitão Pepe termina contrato com FC Porto",
            "snippet": "O jogador está sem clube",
            "url": "https://www.cmjornal.pt/desporto/futebol/detalhe/pepe",
        }]
        filtered = filter_by_date(results, max_age_days=30, market_year="2026")
        assert len(filtered) == 1, "Article with no date/year should be kept (benefit of doubt)"

    def test_iso_date(self):
        results = [{"snippet": "Publicado em 2026-06-15. FC Porto anuncia reforço.", "url": ""}]
        filtered = filter_by_date(results, max_age_days=365)
        assert len(filtered) == 1

    def test_old_url_date_removed(self):
        """Articles with old dates in URLs should be filtered out."""
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y/%m/%d")
        results = [{
            "snippet": "Artigo sobre o FC Porto",
            "url": f"https://ojogo.pt/{old_date}/noticia.html",
        }]
        filtered = filter_by_date(results, max_age_days=30)
        assert len(filtered) == 0

    def test_recent_url_date_kept(self):
        """Articles with recent dates in URLs should be kept."""
        recent_date = datetime.now().strftime("%Y%m%d")
        results = [{
            "snippet": "Artigo sobre o FC Porto",
            "url": f"https://www.abola.pt/noticias/{recent_date}123456",
        }]
        filtered = filter_by_date(results, max_age_days=30)
        assert len(filtered) == 1


class TestParseDateFromUrl:
    def test_yyyymmdd_pattern(self):
        date = _parse_date_from_url("https://www.abola.pt/noticias/20260729123456789")
        assert date == datetime(2026, 7, 29)

    def test_slash_pattern(self):
        date = _parse_date_from_url("https://ojogo.pt/2026/07/29/fc-porto-noticia.html")
        assert date == datetime(2026, 7, 29)

    def test_dash_pattern(self):
        date = _parse_date_from_url("https://record.pt/noticia/2026-07-29-fc-porto")
        assert date == datetime(2026, 7, 29)

    def test_no_date_in_url(self):
        date = _parse_date_from_url("https://example.com/noticia-sem-data")
        assert date is None


class TestBlockedDomains:
    def test_linkedin_blocked(self):
        assert any("linkedin.com" in d for d in BLOCKED_DOMAINS)

    def test_threads_blocked(self):
        assert any("threads.net" in d for d in BLOCKED_DOMAINS)


class TestSportDetection:
    """Tests for sport detection from queries, text, and URLs."""

    def test_detect_from_query_andebol(self):
        assert detect_sport_from_query("FC Porto andebol reforços jogadores 2026") == "Andebol"

    def test_detect_from_query_basquetebol(self):
        assert detect_sport_from_query("FC Porto basquetebol reforços 2026") == "Basquetebol"

    def test_detect_from_query_hoquei(self):
        assert detect_sport_from_query("FC Porto hóquei patins reforços 2026") == "Hóquei em Patins"

    def test_detect_from_query_voleibol(self):
        assert detect_sport_from_query("FC Porto voleibol reforços 2026") == "Voleibol"

    def test_detect_from_query_futsal(self):
        assert detect_sport_from_query("FC Porto futsal reforços 2026") == "Futsal"

    def test_detect_from_query_football_returns_none(self):
        assert detect_sport_from_query("FC Porto transferências mercado verão 2026") is None

    def test_detect_from_result_url(self):
        """Sport detected from URL path (record.pt/modalidades/andebol/...)."""
        result = {
            "title": "FC Porto oficializa saídas",
            "url": "https://www.record.pt/modalidades/andebol/detalhe/fc-porto-oficializa-saidas",
            "snippet": "",
        }
        assert detect_sport_from_result(result) == "Andebol"

    def test_detect_from_result_title(self):
        """Sport detected from title text."""
        result = {
            "title": "Andebol: Timmy Petit é reforço do FC Porto",
            "url": "https://www.zerozero.pt/noticias/timmy-petit",
            "snippet": "",
        }
        assert detect_sport_from_result(result) == "Andebol"

    def test_detect_from_result_snippet(self):
        """Sport detected from snippet text."""
        result = {
            "title": "FC Porto oficializa três saídas",
            "url": "https://www.zerozero.pt/noticias/fc-porto-oficializa-tres-saidas/1151806",
            "snippet": "O andebol do FC Porto comunicou as saídas de três jogadores...",
        }
        assert detect_sport_from_result(result) == "Andebol"

    def test_detect_from_result_football_returns_none(self):
        """Football articles return None (no sport tag needed)."""
        result = {
            "title": "FC Porto contrata Diogo Costa",
            "url": "https://www.abola.pt/noticias/fc-porto-contrata/20260720",
            "snippet": "O guarda-redes chega ao Dragão por 10M",
        }
        assert detect_sport_from_result(result) is None

    def test_detect_from_result_url_overrides_text(self):
        """URL path takes priority over text (more reliable)."""
        result = {
            "title": "Jogador reforça plantel",
            "url": "https://www.record.pt/modalidades/futsal/detalhe/reforco",
            "snippet": "O jogador chega para reforçar a equipa",
        }
        assert detect_sport_from_result(result) == "Futsal"

    def test_sport_tag_in_format_search_results(self):
        """Sport tag should appear in formatted output as [Andebol]."""
        results = [
            {
                "title": "Timmy Petit é reforço do andebol",
                "url": "https://example.com/andebol/timmy",
                "snippet": "Jogador de andebol chega ao FC Porto",
                "sport": "Andebol",
            }
        ]
        output = format_search_results(results)
        assert "[Andebol]" in output

    def test_no_sport_tag_in_format_search_results(self):
        """Articles without sport tag should not have a bracket tag."""
        results = [
            {
                "title": "FC Porto contrata jogador",
                "url": "https://example.com/futebol",
                "snippet": "Reforço para o futebol",
            }
        ]
        output = format_search_results(results)
        assert "[Andebol]" not in output
        assert "[Futsal]" not in output


class TestExtractDomains:
    def test_extract_from_markdown_links(self, tmp_path):
        content = "[ojogo.pt](https://www.ojogo.pt) e [record.pt](https://www.record.pt)"
        p = tmp_path / "sources.md"
        p.write_text(content, encoding="utf-8")
        domains = extract_domains_from_sources(p)
        assert "ojogo.pt" in domains
        assert "record.pt" in domains

    def test_extract_from_backtick(self, tmp_path):
        content = "Dominios: `abola.pt`, `fcporto.pt`"
        p = tmp_path / "sources.md"
        p.write_text(content, encoding="utf-8")
        domains = extract_domains_from_sources(p)
        assert "abola.pt" in domains
        assert "fcporto.pt" in domains

    def test_no_duplicates(self, tmp_path):
        content = "[site.pt](https://site.pt) e [site.pt](https://site.pt)"
        p = tmp_path / "sources.md"
        p.write_text(content, encoding="utf-8")
        domains = extract_domains_from_sources(p)
        assert len(domains) == 1  # deduplicated

    def test_missing_file(self, tmp_path):
        p = tmp_path / "nonexistent.md"
        domains = extract_domains_from_sources(p)
        assert domains == []
