"""
PortoBotNews — RSS feed fetching for Portuguese sports news.

RSS feeds supplement DuckDuckGo search with fresher articles and proper
publication dates. Each feed is fetched, parsed, and filtered for FC
Porto–related content. Results are returned in the same format as DDG
results: {"title", "url", "snippet", "date"} so they merge transparently.

If `feedparser` is not installed (e.g. in a minimal CI environment), this
module degrades gracefully — it prints a warning and returns an empty list.
"""
import re
import time
from datetime import datetime

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

from .constants import RSS_FEEDS, RSS_MAX_PER_FEED


# Keywords that indicate an article is about FC Porto or transfer markets.
# We filter RSS entries to only keep relevant articles — sports feeds publish
# hundreds of articles/day, most of which have nothing to do with transfers.
FCPORTO_KEYWORDS = re.compile(
    r'\b(?:'
    r'fc\s*porto|f\.?\s*c\.?\s*porto|dragon|dragao|dragão|'
    r'transfer|transferencia|transferência|contrata|reforc|reforç|'
    r'saida|saída|venda|emprest|emprést|renov|'
    r'benfica|sporting|braga|vit[oó]ria|guimar'
    r')\b',
    re.IGNORECASE,
)


def _entry_to_result(entry, feed_name: str) -> dict | None:
    """Convert a feedparser entry to our standard result format."""
    url = getattr(entry, "link", "") or entry.get("link", "")
    if not url:
        return None

    title = getattr(entry, "title", "") or entry.get("title", "")
    # feedparser normalizes summary/content into a summary attribute
    snippet = getattr(entry, "summary", "") or entry.get("summary", "")

    # Try to parse the publication date
    date_str = ""
    for attr in ("published", "updated", "created"):
        val = getattr(entry, attr, None) or entry.get(attr)
        if val:
            date_str = val
            break

    # Parse the date string to a datetime for date filtering
    pub_date = None
    if date_str:
        try:
            parsed = feedparser._parse_date(date_str)  # feedparser's date parser
            if parsed:
                pub_date = datetime(*parsed[:6])
        except Exception:
            pass

    # Detect sport from RSS category tags (feeds often include <category> tags)
    sport = None
    from .search import detect_sport_from_result
    categories = [t.get("term", "") for t in (entry.get("tags", []) or [])]
    cat_text = " ".join(categories)
    if cat_text:
        # Check category text first (e.g., <category>Andebol</category>)
        for keyword, sport_name in [
            ("andebol", "Andebol"), ("basquetebol", "Basquetebol"),
            ("hóquei", "Hóquei em Patins"), ("hoquei", "Hóquei em Patins"),
            ("voleibol", "Voleibol"), ("futsal", "Futsal"),
        ]:
            if keyword in cat_text.lower():
                sport = sport_name
                break

    result = {
        "title": title,
        "url": url,
        "snippet": snippet[:500] if snippet else "",
        "source": feed_name,
        "date": pub_date,
    }
    if sport:
        result["sport"] = sport
    else:
        # Fall back to text/URL detection
        detected = detect_sport_from_result(result)
        if detected:
            result["sport"] = detected

    return result


def _is_porto_related(result: dict) -> bool:
    """Check if an article is about FC Porto, transfers, or rivals."""
    text = f"{result.get('title', '')} {result.get('snippet', '')}"
    return bool(FCPORTO_KEYWORDS.search(text))


def fetch_single_feed(url: str, name: str, max_results: int = RSS_MAX_PER_FEED) -> list[dict]:
    """Fetch and parse a single RSS feed. Returns FC Porto–related results."""
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            print(f"   ⚠️  Feed '{name}' retornou erro: {getattr(feed, 'bozo_exception', 'unknown')}")
            return []

        results = []
        for entry in feed.entries[:max_results]:
            result = _entry_to_result(entry, name)
            if result and _is_porto_related(result):
                results.append(result)

        return results

    except Exception as e:
        print(f"   ⚠️  Erro ao obter feed '{name}' ({url}): {type(e).__name__}: {e}")
        return []


def fetch_rss_feeds(feeds: list[dict] | None = None,
                    max_per_feed: int = RSS_MAX_PER_FEED) -> list[dict]:
    """
    Fetch articles from RSS feeds. Returns results in the same format as DDG
    search results: {"title", "url", "snippet", "date"}.

    Articles are filtered to only include FC Porto–related content.
    Duplicate URLs across feeds are removed.

    Args:
        feeds: list of {"url": str, "name": str} dicts. Defaults to RSS_FEEDS.
        max_per_feed: max articles to extract per feed before filtering.

    Returns:
        list of dicts with keys: title, url, snippet, source, date
    """
    if not HAS_FEEDPARSER:
        print("   ⚠️  feedparser não instalado — a saltar RSS feeds. (pip install feedparser)")
        return []

    if feeds is None:
        feeds = RSS_FEEDS

    if not feeds:
        return []

    print("📰 A obter RSS feeds...")
    all_results = []
    seen_urls = set()

    for feed_info in feeds:
        url = feed_info["url"]
        name = feed_info["name"]
        print(f"   → {name} ({url})")

        results = fetch_single_feed(url, name, max_per_feed)
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

        # Small delay between feeds to be respectful
        time.sleep(0.5)

    print(f"📰 {len(all_results)} artigos FC Porto–relacionados obtidos via RSS.")
    return all_results


def merge_results(ddg_results: list[dict], rss_results: list[dict]) -> list[dict]:
    """
    Merge DDG and RSS results, removing URL duplicates.

    If the same URL appears in both, the RSS version is preferred because it
    typically has a proper publication date (DDG dates are unreliable).
    """
    merged = []
    seen_urls = set()

    # RSS results first (preferred — better dates)
    for r in rss_results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(r)

    # DDG results second (skip URLs already seen in RSS)
    for r in ddg_results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(r)

    return merged
