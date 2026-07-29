"""
PortoBotNews — cache for deduplicating processed URLs.

Storage format (processed_urls.json):
    {
        "https://example.com/article": {
            "date": "2026-07-29T12:00:00",
            "sig":  "a1b2c3..."   # short hash of title+snippet
        },
        ...
    }

Why store a `sig` and not just a timestamp:
    A URL reused across days (the same article URL is re-served by DuckDuckGo)
    is only *newsworthy again* if its content changed (headline updated, fee
    confirmed, etc.). Keying on URL alone suppresses genuine follow-ups for the
    whole TTL window; keying on URL+sig lets an *updated* article resurface
    immediately while still de-duping byte-identical repeats.
"""
import hashlib
import json
from datetime import datetime, timedelta

from .constants import CACHE_PATH, CACHE_MAX_AGE_DAYS


def _ensure_cache_dir() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _signature(result: dict) -> str:
    """Short, stable signature of a result's textual content (title + snippet)."""
    raw = (result.get("title", "") + "\n" + result.get("snippet", "")).strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_cache() -> dict:
    """Load cache, pruning entries older than CACHE_MAX_AGE_DAYS.

    Tolerates the previous format ({url: iso_string}) so an upgrade never loses
    history — legacy timestamps are migrated into the new {date, sig} shape with
    an empty sig (treated as 'different next time we see it')."""
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

    cutoff = datetime.now() - timedelta(days=CACHE_MAX_AGE_DAYS)
    out: dict = {}
    for url, entry in data.items():
        if isinstance(entry, str):
            entry = {"date": entry, "sig": ""}
        try:
            when = datetime.fromisoformat(entry["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if when > cutoff:
            out[url] = entry
    return out


def save_cache(cache: dict) -> None:
    """Save the URL cache to disk."""
    _ensure_cache_dir()
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def filter_new_urls(results: list[dict]) -> list[dict]:
    """
    Return only results not seen before — where 'seen' means same URL AND same
    content signature. A URL whose article has been updated since the last run is
    treated as new, so follow-up reporting isn't hidden for CACHE_MAX_AGE_DAYS.
    """
    cache = load_cache()
    new_results = []
    for r in results:
        url = r.get("url", "")
        if not url:
            continue
        sig = _signature(r)
        prev = cache.get(url)
        if prev is None or prev.get("sig", "") != sig:
            new_results.append(r)
        # else: identical URL + identical content → skip (still de-duped)
    return new_results


def mark_as_processed(results: list[dict]) -> None:
    """Record URLs (with their content signature) as seen with today's date."""
    cache = load_cache()
    now = datetime.now().isoformat()
    for r in results:
        url = r.get("url", "")
        if url:
            cache[url] = {"date": now, "sig": _signature(r)}
    save_cache(cache)
