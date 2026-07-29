"""
PortoBotNews — transfer window utilities.
"""
from datetime import datetime


def get_market_window() -> tuple[str, str]:
    """Determina a janela de transferências atual ou mais próxima com base na data."""
    now = datetime.now()
    month = now.month
    year = now.year

    if 6 <= month <= 8:
        return ("Verão", str(year))
    elif month == 1:
        return ("Inverno", f"{year - 1}/{str(year)[2:]}")
    elif month in (9, 10, 11, 12):
        return ("Inverno", f"{year}/{str(year + 1)[2:]}")
    else:
        return ("Verão", str(year))


def build_post_title(market_label: str) -> str:
    """
    Gera o título do post do Reddit a partir da janela de transferências.
    Ex: "Mercado de Verão 2026" -> "🐉 FC Porto — Mercado de Verão 2026"
    """
    return f"🐉 FC Porto — {market_label}"
