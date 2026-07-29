"""
PortoBotNews — web search with DuckDuckGo, with retry and date filtering.
"""
import re
import time
from datetime import datetime, timedelta

from ddgs import DDGS

from .constants import DDG_QUERY_DELAY, DDG_MAX_RETRIES, DDG_TIMELIMIT, STATS_DOMAINS


# ─── Blocked domains ──────────────────────────────────────────────────────────
# Domains that should never appear as search results — encyclopedias, social
# media, commerce, etc. Updated from the Portuguese Reddit community guidelines.
BLOCKED_DOMAINS = [
    "wikipedia.org", "wikimedia.org",
    "youtube.com", "youtu.be",
    "tiktok.com",
    "facebook.com",
    "instagram.com",
    "twitter.com", "x.com",
    "reddit.com",
    "google.com",
    "amazon.com",
    "pinterest.com",
    "linkedin.com",
    "threads.net",
    "bsky.app",
    "mastodon.social",
    "weibo.com",
    "medium.com",
]


def is_blocked(url: str) -> bool:
    url_lower = url.lower()
    return any(d in url_lower for d in BLOCKED_DOMAINS)


def extract_domains_from_sources(path) -> list[str]:
    """
    Extrai domínios de sites de um ficheiro .md de fontes.
    Procura por padrões como [abola.pt](https://...) ou domínios diretos em backticks.
    Filtra falsos positivos como nomes de ficheiros (.md, .txt, etc.).
    """
    from pathlib import Path
    path = Path(path)
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")

    # TLDs known to appear in our source lists — reject anything else from backticks
    VALID_TLDS = {
        ".pt", ".com", ".fr", ".de", ".it", ".es", ".net", ".org",
        ".co.uk", ".co", ".io", ".tv", ".me", ".eu",
    }
    # File extensions that look like domains but aren't (e.g. "trustworthy.md")
    FILE_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".py", ".js", ".ts"}

    domains = set()

    # Pattern 1: markdown links [label](https://domain/...)
    for m in re.finditer(r'\[([^\]]+)\]\(https?://(?:www\.)?([^/)\s]+)', content):
        domain = m.group(2)
        if not any(domain.endswith(ext) for ext in FILE_EXTENSIONS):
            domains.add(domain)

    # Pattern 2: backtick-enclosed text that looks like a domain
    for m in re.finditer(r'`([a-z0-9-]+\.[a-z]{2,}(?:\.[a-z]{2,})?)`', content, re.IGNORECASE):
        domain = m.group(1).lower()
        # Reject file extensions (trustworthy.md, config.json, etc.)
        if any(domain.endswith(ext) for ext in FILE_EXTENSIONS):
            continue
        # Reject if it doesn't end with a known TLD
        if not any(domain.endswith(tld) for tld in VALID_TLDS):
            continue
        domains.add(domain)

    return sorted(domains)


def _is_stats_domain(domain: str) -> bool:
    """True if the domain is a stats/data site that never publishes transfer articles."""
    domain_lower = domain.lower()
    return any(d in domain_lower for d in STATS_DOMAINS)


# ─── Query building ───────────────────────────────────────────────────────────

# Diverse query terms rotated across `site:` queries so different articles
# surface from different angles (some sites index "transferências", others
# "contratações", etc.). This catches articles one generic query would miss.
SITE_QUERY_TERMS = [
    "FC Porto transferências",
    "FC Porto contratações",
    "FC Porto reforços",
    "FC Porto saídas",
    "FC Porto novo jogador",
    "FC Porto mercado",
    "FC Porto inscrição",
]


# ─── Sport detection ──────────────────────────────────────────────────────────
# Maps sport keywords to their section name. Used to tag articles so the LLM
# knows which sport each article is about — prevents andebol/basquetebol/etc.
# articles from being misclassified as football.

SPORT_KEYWORDS: list[tuple[str, str]] = [
    ("andebol", "Andebol"),
    ("handball", "Andebol"),
    ("basquetebol", "Basquetebol"),
    ("basketball", "Basquetebol"),
    ("basquete", "Basquetebol"),
    ("hóquei em patins", "Hóquei em Patins"),
    ("hoquei em patins", "Hóquei em Patins"),
    ("hóquei", "Hóquei em Patins"),
    ("hoquei", "Hóquei em Patins"),
    ("roller hockey", "Hóquei em Patins"),
    ("voleibol", "Voleibol"),
    ("volleyball", "Voleibol"),
    ("vólei", "Voleibol"),
    ("futsal", "Futsal"),
    ("futebol sala", "Futsal"),
]

# URL path segments that indicate sport (e.g., record.pt/modalidades/andebol/...)
SPORT_URL_PATTERNS: list[tuple[str, str]] = [
    ("/andebol", "Andebol"),
    ("/handball", "Andebol"),
    ("/basquetebol", "Basquetebol"),
    ("/basketball", "Basquetebol"),
    ("/basquete", "Basquetebol"),
    ("/hoquei", "Hóquei em Patins"),
    ("/hockey", "Hóquei em Patins"),
    ("/voleibol", "Voleibol"),
    ("/volleyball", "Voleibol"),
    ("/volei", "Voleibol"),
    ("/futsal", "Futsal"),
]


def detect_sport_from_query(query: str) -> str | None:
    """Detect sport from a search query (e.g., 'FC Porto andebol reforços' → 'Andebol')."""
    query_lower = query.lower()
    for keyword, sport in SPORT_KEYWORDS:
        if keyword in query_lower:
            return sport
    return None


def detect_sport_from_result(result: dict) -> str | None:
    """
    Detect sport from an article's title, snippet, and URL.

    Checks URL path first (most reliable — sites like record.pt embed sport in
    the path), then title+snippet text. Returns None if no sport detected
    (article is likely about football, the default).
    """
    url = result.get("url", "").lower()
    text = f"{result.get('title', '')} {result.get('snippet', '')}".lower()

    # Layer 1: URL path patterns (most reliable)
    for pattern, sport in SPORT_URL_PATTERNS:
        if pattern in url:
            return sport

    # Layer 2: Text keywords in title + snippet
    for keyword, sport in SPORT_KEYWORDS:
        if keyword in text:
            return sport

    return None


def build_search_queries(market_label: str, trustworthy_domains: list[str] | None = None,
                         sketchy_domains: list[str] | None = None) -> list[str]:
    """
    Constrói queries de pesquisa dinâmicas com base na janela de transferências atual.

    Strategy:
    1. Broad discovery queries (general web search).
    2. Phase-aware queries (rumor / negotiation / confirmation — catches news
       at every stage of the transfer lifecycle, not just broad "transferências").
    3. Site-specific queries for NEWS domains only (stats/data domains excluded
       — they never publish transfer articles, so querying them wastes API calls).
       Query terms are rotated across domains to catch different article types.
    4. Sport-specific queries (non-football modalities need targeted terms).
    5. Rival queries (for the Rivals section).
    """
    now = datetime.now()
    year = str(now.year)
    season = "verão" if 6 <= now.month <= 8 else "inverno"
    if "Verão" in market_label:
        season = "verão"
        m = re.search(r"(\d{4})", market_label)
        if m:
            year = m.group(1)
    elif "Inverno" in market_label:
        season = "inverno"
        m = re.search(r"(\d{4})", market_label)
        if m:
            year = m.group(1)

    queries = [
        # ─── Broad discovery ───────────────────────────────────────────────
        f"FC Porto transferências mercado {season} {year}",
        f"FC Porto contratações entradas {year}",
        f"FC Porto saídas vendas {year}",
        f"FC Porto rumores transferências {year}",
        f"FC Porto reforços {season} {year}",
        f"FC Porto mercado transferências últimas notícias",

        # ─── Phase-aware: confirmation (catches official announcements) ────
        f"FC Porto oficial apresentou anunciou {year}",
        f"FC Porto assinou contratou {year}",
        f"FC Porto negócio fechado oficial {year}",

        # ─── Phase-aware: rumor / negotiation (catches early-stage news) ──
        f"FC Porto interesse sonda apontado {year}",
        f"FC Porto acordo proposta negocia {year}",
        f"FC Porto pretende quer contratar {year}",
    ]

    # ─── Site-specific queries (NEWS domains only, diverse terms) ──────────
    # Stats/data domains are excluded — they never return transfer articles
    # via DDG search. This saves API calls without losing coverage.
    all_domains = (trustworthy_domains or []) + (sketchy_domains or [])
    term_idx = 0
    for domain in all_domains:
        if _is_stats_domain(domain):
            continue
        term = SITE_QUERY_TERMS[term_idx % len(SITE_QUERY_TERMS)]
        queries.append(f"site:{domain} {term}")
        term_idx += 1

    # ─── Sport-specific queries (non-football) ─────────────────────────────
    # Portuguese media barely covers these via DDG, so we use targeted terms.
    queries.extend([
        f"FC Porto andebol reforços jogadores {year}",
        f"FC Porto basquetebol reforços jogadores {year}",
        f"FC Porto hóquei patins reforços {year}",
        f"FC Porto voleibol reforços jogadores {year}",
        f"FC Porto futsal reforços jogadores {year}",
    ])

    # ─── Rival queries (for the Rivals section) ────────────────────────────
    queries.extend([
        f"Benfica transferências mercado {year}",
        f"Sporting transferências mercado {year}",
        f"Braga transferências mercado {year}",
        f"Vitória Guimarães transferências mercado {year}",
    ])

    return queries


# ─── Single query execution ───────────────────────────────────────────────────

def _search_single_query(ddgs: DDGS, query: str, max_results: int,
                         timelimit: str = DDG_TIMELIMIT) -> list[dict]:
    """Execute a single DuckDuckGo query with retry logic."""
    for attempt in range(1, DDG_MAX_RETRIES + 1):
        try:
            results = ddgs.text(
                query,
                timelimit=timelimit,
                max_results=max_results,
            )
            return [
                {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                for r in results
            ]
        except Exception as e:
            if attempt < DDG_MAX_RETRIES:
                wait = 2 ** attempt  # exponential backoff: 2s, 4s
                print(f"   ⚠️  Tentativa {attempt} falhou para '{query}': {e}. A repetir em {wait}s...")
                time.sleep(wait)
            else:
                print(f"   ⚠️  Erro na pesquisa '{query}' após {DDG_MAX_RETRIES} tentativas: {e}")
    return []


# ─── Date parsing ─────────────────────────────────────────────────────────────

def _parse_date_from_snippet(snippet: str) -> datetime | None:
    """
    Try to extract a publication date from a search result snippet.
    DuckDuckGo sometimes includes dates in the snippet text.
    """
    months_pt = {
        "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
        "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    }
    # "20 jun 2026" or "20 junho 2026"
    m = re.search(r'(\d{1,2})\s+(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\w*\s+(\d{4})', snippet, re.IGNORECASE)
    if m:
        day, month_str, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = months_pt.get(month_str[:3])
        if month:
            try:
                return datetime(year, month, day)
            except ValueError:
                pass
    # "20/06/2026" or "20-06-2026"
    m = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', snippet)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(year, month, day)
        except ValueError:
            pass
    # ISO: "2026-06-20"
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', snippet)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(year, month, day)
        except ValueError:
            pass
    return None


def _parse_date_from_url(url: str) -> datetime | None:
    """
    Extract a publication date from a URL.

    Many Portuguese news sites embed dates in article URLs:
      abola.pt/.../20250729...  (YYYYMMDD)
      ojogo.pt/2026/07/15/...
      record.pt/noticia/2026-07-15-...
    """
    # YYYYMMDD pattern (abola.pt style): /20250729...
    m = re.search(r'/(\d{4})(\d{2})(\d{2})\d*\b', url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # /YYYY/MM/DD/ pattern
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # /YYYY-MM-DD pattern
    m = re.search(r'/(\d{4})-(\d{2})-(\d{2})', url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _parse_date(result: dict) -> datetime | None:
    """Try to extract a publication date from URL first, then snippet."""
    url_date = _parse_date_from_url(result.get("url", ""))
    if url_date:
        return url_date
    return _parse_date_from_snippet(result.get("snippet", ""))


def _extract_years_from_text(text: str) -> set[int]:
    """
    Extract all 4-digit years mentioned in text.
    Only matches years from 2000-2099 to avoid false positives from
    scorelines like '2-0' or phone-number-like patterns.
    """
    return {int(m) for m in re.findall(r'\b(20\d{2})\b', text)}


def filter_by_date(results: list[dict], max_age_days: int = 30,
                   market_year: str | None = None) -> list[dict]:
    """
    Best-effort filter to drop stale articles.

    Two-layer approach:
    1. **Date-based**: If we can parse a publication date (from URL or snippet)
       and it's older than `max_age_days`, drop it.
    2. **Year-based**: If the article has no parseable date but its title or
       snippet mentions a specific year (e.g., "2024") that doesn't match the
       current transfer window, drop it. This catches old articles that DDG
       re-indexed recently and that have no date embedded in their URL.

    Articles with no parseable date AND no year mention are kept (benefit of
    the doubt) — we'd rather over-include than miss a real transfer.
    """
    cutoff = datetime.now() - timedelta(days=max_age_days)
    # Extract the target year(s) from the market label if provided.
    # e.g., "Mercado de Verão 2026" → {2026}
    # e.g., "Mercado de Inverno 2026/27" → {2026, 2027} (expand /27 → 2027)
    target_years: set[int] = set()
    if market_year:
        # Full 4-digit years
        target_years = {int(m) for m in re.findall(r'\b(20\d{2})\b', market_year)}
        # Shorthand "/YY" at the end (e.g., "2026/27" → 2027)
        short_match = re.search(r'/(\d{2})\b', market_year)
        if short_match:
            target_years.add(2000 + int(short_match.group(1)))
    else:
        target_years = {datetime.now().year}

    # Also accept articles mentioning the previous year (transfers that started
    # last summer and are still relevant, like a January loan that ends in June).
    target_years.add(max(target_years) - 1)

    filtered = []
    for r in results:
        date = _parse_date(r)
        if date is not None:
            # Layer 1: parsed date is reliable
            if date >= cutoff:
                filtered.append(r)
            else:
                print(f"   🗑️  Artigo antigo removido ({date.strftime('%d/%m/%Y')}): {r.get('title', '')[:60]}")
        else:
            # Layer 2: no parseable date — check for year mentions in title+snippet
            text = f"{r.get('title', '')} {r.get('snippet', '')}"
            mentioned_years = _extract_years_from_text(text)

            if mentioned_years and not mentioned_years.intersection(target_years):
                # The article mentions specific years, none of which match the
                # current transfer window. This is almost certainly stale.
                years_str = ", ".join(str(y) for y in sorted(mentioned_years))
                print(f"   🗑️  Artigo com ano fora da janela ({years_str}): {r.get('title', '')[:60]}")
            else:
                # No date and no conflicting years, or years match current window
                filtered.append(r)
    return filtered


# ─── Main search function ─────────────────────────────────────────────────────

def search_web(queries: list[str], max_per_query: int = 10,
               source_domains: list[str] | None = None,
               max_age_days: int = 30,
               timelimit: str = DDG_TIMELIMIT,
               market_year: str | None = None) -> list[dict]:
    """
    Pesquisa DuckDuckGo por artigos de transferências.
    Devolve uma lista de {"title", "url", "snippet", "sport?"} deduplicada por URL.
    Com retry automático, filtragem por data (date-based + year-based), e
    tagging automático de modalidade (para evitar andebol no futebol, etc.).
    """
    print("🔍 A pesquisar notícias no DuckDuckGo...")
    all_results = []
    seen_urls = set()
    ddgs = DDGS()

    for query in queries:
        print(f"   → {query}")
        # Detect sport from the query itself (e.g., "FC Porto andebol" → Andebol)
        query_sport = detect_sport_from_query(query)
        results = _search_single_query(ddgs, query, max_per_query, timelimit=timelimit)
        for r in results:
            url = r.get("url", "")
            if url and url not in seen_urls and not is_blocked(url):
                # Tag with sport from query context (most reliable signal)
                if query_sport:
                    r["sport"] = query_sport
                seen_urls.add(url)
                all_results.append(r)
        time.sleep(DDG_QUERY_DELAY)

    # Post-detect sport for articles without a query-context tag
    # (found by general queries — check URL paths and snippet text)
    for r in all_results:
        if "sport" not in r:
            sport = detect_sport_from_result(r)
            if sport:
                r["sport"] = sport

    # Filtrar por data (remover artigos antigos)
    all_results = filter_by_date(all_results, max_age_days=max_age_days,
                                 market_year=market_year)

    # Ordenar por prioridade de fontes
    if source_domains:
        def domain_priority(result):
            url_lower = result["url"].lower()
            for i, domain in enumerate(source_domains):
                if domain in url_lower:
                    return i
            return len(source_domains)
        all_results.sort(key=domain_priority)
        print(f"   📋 Resultados ordenados por prioridade de fontes ({len(source_domains)} domínios confiáveis).")

    tagged = sum(1 for r in all_results if r.get("sport"))
    if tagged:
        print(f"   🏷️  {tagged} artigos taggados com modalidade (não-futebol).")

    print(f"📋 {len(all_results)} artigos únicos encontrados (após filtragem por data).")
    return all_results


def format_search_results(results: list[dict]) -> str:
    """Formata os resultados da pesquisa para incluir no prompt do LLM."""
    if not results:
        return "*Nenhum artigo encontrado na pesquisa.*"
    lines = []
    for i, r in enumerate(results, 1):
        sport_tag = f" [{r['sport']}]" if r.get("sport") else ""
        lines.append(f"[{i}]{sport_tag} {r['title']}")
        lines.append(f"    URL: {r['url']}")
        snippet = r["snippet"][:300] if r["snippet"] else ""
        lines.append(f"    Resumo: {snippet}")
        lines.append("")
    return "\n".join(lines)
