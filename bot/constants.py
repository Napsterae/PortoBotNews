"""
PortoBotNews — core constants.

The package directory is `bot/` for brevity, but the project's public name and
all user-facing strings remain "PortoBotNews". Do not rename these — the live
Reddit thread and GitHub Issues depend on them.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_PATH = BASE_DIR / "prompts" / "transfer_news.md"
TRUSTWORTHY_PATH = BASE_DIR / "sources" / "trustworthy.md"
SKETCHY_PATH = BASE_DIR / "sources" / "sketchy.md"
PREVIEW_PATH = BASE_DIR / "preview.md"
CACHE_PATH = BASE_DIR / ".cache" / "processed_urls.json"
CONTENT_HASH_PATH = BASE_DIR / ".cache" / "last_content_hash.txt"

SUBREDDIT = "FCPorto"

# Current marker written to new/edited Reddit posts.
BOT_MARKER = "<!-- PortoBotNews:v1 -->"
# Markers we accept when *reading* an existing post (so older published threads
# are still recognized as "already maintained by the bot" → update mode).
# Keep the current BOT_MARKER at the front so writes overwrite with the canonical form.
BOT_MARKERS_ACCEPTED = (BOT_MARKER, "<!-- bot:v1 -->")

# Default source link shown in the Reddit footer when GITHUB_REPO_URL is unset.
DEFAULT_REPO_URL = "https://github.com/PortoBotNews/PortoBotNews"

# Delay between LLM calls (seconds) — avoids rate limits on free tiers
LLM_RATE_LIMIT_DELAY = 2

# Maximum age of cached URLs (days). Short window: DDG re-crawls frequently and a
# 7-day window can hide genuine updates (bid raised, transfer completed) behind a
# URL already seen. 3 days still de-dups noise across the ~daily CI runs.
CACHE_MAX_AGE_DAYS = 3

# Delay between DuckDuckGo queries (seconds) — be respectful
DDG_QUERY_DELAY = 1

# Maximum retries for a failed DuckDuckGo query
DDG_MAX_RETRIES = 2

# DuckDuckGo time filter: "d" (day), "w" (week), "m" (month).
# "m" is the widest useful window for a transfer tracker that runs daily.
DDG_TIMELIMIT = "m"

# Stats/data-only domains that never publish transfer articles.
# These are excluded from `site:` queries (waste of API calls) but remain
# in the source lists shown to the LLM (useful for the prompt's context).
STATS_DOMAINS = {
    "fbref.com",
    "sofascore.com",
    "whoscored.com",
    "football-observatory.com",
    "flashscore.pt",
    "fotmob.com",
    "transfermarkt.pt",  # has structured transfers, but not article-based — handled separately
}

# ─── RSS Feeds (supplement to DuckDuckGo) ─────────────────────────────────────
# RSS feeds return fresher articles with proper publication dates.
# Each feed is fetched and filtered for FC Porto–related content.
# A Bola and O Jogo don't expose public RSS endpoints; they're still covered
# via DuckDuckGo `site:` queries. Verified working as of 2025-07.
RSS_FEEDS = [
    {"url": "https://feeds.feedburner.com/ojogo", "name": "O Jogo (feedburner)"},
    {"url": "https://record.pt/rss", "name": "Record"},
    {"url": "https://maisfutebol.iol.pt/rss", "name": "Maisfutebol"},
    {"url": "https://observador.pt/feed/", "name": "Observador"},
    {"url": "https://www.noticiasaominuto.com/rss/desporto", "name": "Notícias ao Minuto"},
]

# Maximum articles to keep per RSS feed (sorted by date, most recent first)
RSS_MAX_PER_FEED = 15

# No article cap — send all found articles to the LLM.
# DeepSeek has 128K context (~96K usable). Even 600 articles at ~400 chars
# each = 240K chars ≈ 60K tokens, which fits. If the prompt grows beyond
# the model's context, the API returns an error and the bot falls back to
# Groq (smaller but still capable).
# We compact-format results to ~150 chars each to keep prompt size manageable.

# Timeout for a single LLM API call (seconds).
# DeepSeek can take 2-5 min on large prompts with long outputs (two-pass, 4K+ chars).
# Extra headroom for the 200+ article case.
LLM_TIMEOUT = 600

# ─── LLM Providers ────────────────────────────────────────────────────────────
# 1. OpenCode Zen (primário) — múltiplos modelos free, obtidos dinamicamente
# 2. DeepSeek (fallback 1)
# 3. Groq (fallback 2)
LLM_PROVIDERS = [
    {
        "name": "DeepSeek",
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    },
    {
        "name": "Groq",
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
    },
]

OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
OPENCODE_ZEN_ENV_KEY = "OPENCODE_API_KEY"

OPENCODE_ZEN_MODEL_PREFERENCE = {
    "deepseek-v4-flash-free": 100,
    "mimo-v2.5-free": 95,
    "big-pickle": 90,
    "qwen3.6-plus-free": 80,
    "nemotron-3-ultra-free": 70,
    "hy3-free": 50,
    "north-mini-code-free": 30,
}

OPENCODE_ZEN_FALLBACK_MODELS = list(OPENCODE_ZEN_MODEL_PREFERENCE.keys())

# ─── Fallback sources (used if sources/*.md files are missing) ────────────────
FALLBACK_TRUSTWORTHY = """# Sites de Confiança — Tier 1

- **FC Porto (oficial)** — [fcporto.pt](https://www.fcporto.pt)
- **Liga Portugal** — [ligaportugal.pt](https://www.ligaportugal.pt)
- **O Jogo** — [ojogo.pt](https://www.ojogo.pt)
- **Record** — [record.pt](https://www.record.pt)
- **A Bola** — [abola.pt](https://www.abola.pt)
- **ZeroZero** — [zerozero.pt](https://www.zerozero.pt)
- **Maisfutebol** — [maisfutebol.iol.pt](https://maisfutebol.iol.pt)
- **RTP** — [rtp.pt](https://www.rtp.pt)
- **SIC Notícias** — [sicnoticias.pt](https://sicnoticias.pt)
- **Porto Canal** — [portocanal.pt](https://www.portocanal.pt)
- **Público** — [publico.pt](https://www.publico.pt)
- **Diário de Notícias** — [dn.pt](https://www.dn.pt)
- **Jornal de Notícias** — [jn.pt](https://www.jn.pt)
- **Fabrizio Romano** — @FabrizioRomano
- **David Ornstein** — @David_Ornstein
- **The Athletic** — [theathletic.com](https://theathletic.com)
- **BBC Sport** — [bbc.com/sport](https://www.bbc.com/sport)
- **Sky Sports** — [skysports.com](https://www.skysports.com)
- **ESPN** — [espn.com/soccer](https://www.espn.com/soccer)
- **Marca** — [marca.com](https://www.marca.com)
- **AS** — [as.com](https://as.com)
- **L'Équipe** — [lequipe.fr](https://www.lequipe.fr)
- **Kicker** — [kicker.de](https://www.kicker.de)
- **Transfermarkt** — [transfermarkt.pt](https://www.transfermarkt.pt)
"""

FALLBACK_SKETCHY = """# Sites Menos Fiáveis — Tier 2

- **Fichajes.net** — [fichajes.net](https://www.fichajes.net)
- **Calciomercato** — [calciomercato.com](https://www.calciomercato.com)
- **TalkSport** — [talksport.com](https://www.talksport.com)
- **Daily Mail (sports)** — [dailymail.co.uk/sport](https://www.dailymail.co.uk/sport)
- **The Sun (sports)** — [thesun.co.uk/sport](https://www.thesun.co.uk/sport)
- **Ekrem Konur** — @Ekremkonur — EVITAR
- **Nicolò Schira** — @NicoSchira — EVITAR
- **Rudy Galetti** — @RudyGaletti — EVITAR
"""
