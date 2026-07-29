"""
PortoBotNews — content validation before publishing.
"""
import re
from urllib.parse import urlparse


def extract_urls_from_text(text: str) -> list[str]:
    """Extract all URLs from markdown-formatted text."""
    # Match [text](url) and bare URLs
    urls = re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', text)
    bare = re.findall(r'(?<!\()\bhttps?://[^\s\)\]<>"\']+', text)
    return [u for _, u in urls] + bare


def validate_urls_against_search_results(content: str, search_results: list[dict]) -> list[str]:
    """
    Check that all URLs in the content came from the actual search results.
    Returns a list of hallucinated/invented URLs found in the content.
    """
    valid_urls = {r["url"] for r in search_results}
    content_urls = extract_urls_from_text(content)
    hallucinated = []
    for url in content_urls:
        # Normalize: strip trailing slashes and fragments for comparison
        normalized = url.rstrip("/").split("#")[0]
        if normalized not in {v.rstrip("/").split("#")[0] for v in valid_urls}:
            hallucinated.append(url)
    return hallucinated


def validate_markdown_tables(content: str) -> list[str]:
    """
    Check that markdown tables are well-formed (consistent pipe counts).
    Returns a list of issues found.
    """
    issues = []
    lines = content.split("\n")
    in_table = False
    expected_cols = 0
    table_start = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cols = stripped.count("|") - 1
            if not in_table:
                in_table = True
                expected_cols = cols
                table_start = i
            elif cols != expected_cols:
                issues.append(
                    f"Table starting at line {table_start}: row {i} has {cols} columns, "
                    f"expected {expected_cols}"
                )
        else:
            if in_table:
                in_table = False

    return issues


def validate_no_banned_sources(content: str, banned_domains: list[str]) -> list[str]:
    """
    Check that no banned/sketchy sources appear as sole sources in the content.
    Returns a list of banned sources found.
    """
    found = []
    content_lower = content.lower()
    for domain in banned_domains:
        if domain.lower() in content_lower:
            found.append(domain)
    return found


def validate_player_names(content: str) -> list[str]:
    """
    Check for obviously fabricated player names (common LLM hallucination patterns).
    Returns a list of suspicious entries.
    """
    issues = []
    # Check for description-based "names" instead of real names
    fake_patterns = [
        r'\|\s*Pérola\s',
        r'\|\s*Jovem\s+promessa',
        r'\|\s*Reforço\s+para',
        r'\|\s*Jogador\s+do\s',
        r'\|\s*Avançado\s+do\s',
        r'\|\s*Médio\s+do\s',
        r'\|\s*Defesa\s+do\s',
        r'\|\s*Guarda-redes\s+do\s',
    ]
    for pattern in fake_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            issues.append(f"Suspicious description-as-name found: {matches[0]}")
    return issues


def validate_content(content: str, search_results: list[dict],
                     banned_domains: list[str] | None = None) -> dict:
    """
    Run all validations on the generated content.
    Returns a dict with:
      - valid: bool (True if no critical issues)
      - issues: list of issue descriptions
      - warnings: list of warning descriptions
    """
    issues = []
    warnings = []

    # 1. URL validation
    hallucinated = validate_urls_against_search_results(content, search_results)
    if hallucinated:
        issues.append(f"Hallucinated URLs ({len(hallucinated)}): {', '.join(hallucinated[:5])}")

    # 2. Table structure validation
    table_issues = validate_markdown_tables(content)
    if table_issues:
        issues.extend(table_issues)

    # 3. Banned sources (warning only — they might appear in context)
    if banned_domains:
        banned_found = validate_no_banned_sources(content, banned_domains)
        if banned_found:
            warnings.append(f"Banned sources mentioned: {', '.join(banned_found)}")

    # 4. Player name validation
    name_issues = validate_player_names(content)
    if name_issues:
        issues.extend(name_issues)

    # 5. Check minimum content length (LLM sometimes returns very short/empty responses)
    if len(content.strip()) < 200:
        issues.append(f"Content too short ({len(content.strip())} chars) — likely incomplete")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
    }
