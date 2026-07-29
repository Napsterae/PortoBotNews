"""
PortoBotNews — change detection to skip unnecessary LLM calls.
"""
import hashlib
from pathlib import Path

from .constants import CONTENT_HASH_PATH


def _ensure_cache_dir() -> None:
    CONTENT_HASH_PATH.parent.mkdir(parents=True, exist_ok=True)


def content_hash(content: str) -> str:
    """Generate a SHA-256 hash of the content (normalized)."""
    # Normalize whitespace for comparison
    normalized = " ".join(content.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_last_hash() -> str:
    """Load the hash of the last published content."""
    if not CONTENT_HASH_PATH.exists():
        return ""
    try:
        return CONTENT_HASH_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_hash(content: str) -> None:
    """Save the hash of the current content."""
    _ensure_cache_dir()
    CONTENT_HASH_PATH.write_text(content_hash(content), encoding="utf-8")


def has_content_changed(new_content: str) -> bool:
    """
    Check if the new content is meaningfully different from the last published version.
    Returns True if content changed (or no previous hash exists), False if identical.
    """
    last = load_last_hash()
    if not last:
        return True
    return content_hash(new_content) != last
