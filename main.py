"""
PortoBotNews — Bot de transferências do FC Porto para o Reddit.

Arquitetura (100% gratuita, sem billing):
  1. Pesquisa web com DuckDuckGo (ddgs) — obtém artigos reais com URLs reais.
  2. LLM com Groq (preferencial) ou DeepSeek (fallback) — processa os artigos
     e gera o Markdown. Se um provider falhar (rate limit, erro), tenta o outro.
  3. Autentica-se no Reddit e vai buscar o conteúdo atual da thread.
  4. Decide: ATUALIZAR (post já tem o marcador do bot) ou CRIAR DO ZERO.
  5. Anexa o rodapé de transparência (com link do GitHub + marcador do bot).
  6. Publica: edita o post existente ou cria um novo (e imprime o ID no log).

Modo preview (--dry-run ou DRY_RUN=true):
  Gera o conteúdo mas NÃO publica no Reddit. Escreve o resultado
  em preview.md para revisão. Útil para testar antes de publicar.
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime

# Forcar UTF-8 no stdout/stderr (Windows usa cp1252 por defeito, quebra com emojis)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import praw
from dotenv import load_dotenv
from openai import OpenAI, APIError, RateLimitError, APIConnectionError
from ddgs import DDGS

# ─── Constantes ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROMPT_PATH = BASE_DIR / "prompts" / "transfer_news.md"
TRUSTWORTHY_PATH = BASE_DIR / "sources" / "trustworthy.md"
SKETCHY_PATH = BASE_DIR / "sources" / "sketchy.md"
PREVIEW_PATH = BASE_DIR / "preview.md"

SUBREDDIT = "FCPorto"
# O título é gerado dinamicamente a partir da janela de transferências (ver build_post_title).
# Isto permite ao bot encontrar automaticamente o post correto para cada mercado (verão/inverno)
# sem precisar de REDDIT_POST_ID configurado manualmente.
BOT_MARKER = "<!-- PortoBotNews:v1 -->"

# Providers de LLM (todos OpenAI-compatible).
# Ordem = prioridade. O bot usa o primeiro que tiver API key configurada e válida.
# Se o primário falhar (rate limit, erro), tenta o seguinte automaticamente.
#
# 1. OpenCode Zen (primário) — múltiplos modelos free, obtidos dinamicamente a cada run
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

# OpenCode Zen é tratado separadamente porque tem múltiplos modelos free.
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
OPENCODE_ZEN_ENV_KEY = "OPENCODE_API_KEY"

# Ranking de preferência para modelos free do OpenCode Zen (melhor primeiro).
# Score mais alto = preferido. Modelos não listados ficam com score 0.
# Critérios: capacidade geral (não-code), suporte multilingue (Português), contexto.
OPENCODE_ZEN_MODEL_PREFERENCE = {
    "deepseek-v4-flash-free": 100,  # DeepSeek V4 — forte em geral, multilingue (thinking mode desativado para eficiência)
    "mimo-v2.5-free": 95,            # MiMo — capaz, bom para tarefas gerais
    "big-pickle": 90,               # Stealth model, 200K contexto
    "qwen3.6-plus-free": 80,        # Qwen — capaz, multilingue
    "nemotron-3-ultra-free": 70,    # Nemotron — decente
    "hy3-free": 50,                 # Menos conhecido
    "north-mini-code-free": 30,     # Foco em código — menos ideal para jornalismo
}

# Lista de fallback se o endpoint /models estiver indisponível
OPENCODE_ZEN_FALLBACK_MODELS = list(OPENCODE_ZEN_MODEL_PREFERENCE.keys())


def build_post_title(market_label: str) -> str:
    """
    Gera o título do post do Reddit a partir da janela de transferências.
    Ex: "Mercado de Verão 2026" -> "🐉 FC Porto — Mercado de Verão 2026"
        "Mercado de Inverno 2026/27" -> "🐉 FC Porto — Mercado de Inverno 2026/27"

    O título é DETERMINÍSTICO e IMUTÁVEL (o Reddit não permite editar títulos de self-posts),
    o que o torna um identificador fiável para encontrar o post correto de cada janela.
    """
    return f"🐉 FC Porto — {market_label}"


def build_search_queries(market_label: str, trustworthy_domains: list[str] = None,
                         sketchy_domains: list[str] = None) -> list[str]:
    """
    Constrói queries de pesquisa dinâmicas com base na janela de transferências atual.
    Inclui o ano e a época (verão/inverno) extraídos do market_label.
    Diversifica por modalidade, direção (entradas/saídas/rumores) e rivais.
    Gera site-specific queries a partir dos domínios em sources/trustworthy.md e sources/sketchy.md.
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
        # Queries gerais (descoberta ampla)
        f"FC Porto transferências mercado {season} {year}",
        f"FC Porto contratações entradas {year}",
        f"FC Porto saídas vendas {year}",
        f"FC Porto rumores transferências {year}",
    ]

    # Site-specific queries para fontes confiáveis (Tier 1) — todas
    if trustworthy_domains:
        for domain in trustworthy_domains:
            queries.append(f"site:{domain} FC Porto transferências")

    # Site-specific queries para fontes de rumores (Tier 2) — todas
    if sketchy_domains:
        for domain in sketchy_domains:
            queries.append(f"site:{domain} FC Porto transferências")

    # Por modalidade
    queries.extend([
        f"FC Porto andebol transferências {year}",
        f"FC Porto basquetebol transferências {year}",
        f"FC Porto hóquei patins transferências {year}",
        f"FC Porto voleibol transferências {year}",
        f"FC Porto futsal transferências {year}",
    ])

    # Rivais
    queries.extend([
        f"Benfica transferências mercado {year}",
        f"Sporting transferências mercado {year}",
        f"Braga transferências mercado {year}",
        f"Vitória Guimarães transferências mercado {year}",
    ])

    return queries


# ─── Utilitários ──────────────────────────────────────────────────────────────
def load_file(path: Path, label: str) -> str:
    """Lê um ficheiro .md; aborta com mensagem clara se não existir."""
    if not path.exists():
        print(f"❌ Erro: ficheiro {label} não encontrado em {path}")
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def get_env(name: str, required: bool = True, default: str = "") -> str:
    """Lê uma variável de ambiente; aborta se for obrigatória e estiver vazia."""
    val = os.environ.get(name, "").strip()
    if required and not val:
        print(f"❌ Erro: variável de ambiente {name} não definida.")
        sys.exit(1)
    return val or default


# ─── Janela de transferências ────────────────────────────────────────────────
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


# ─── Pesquisa web (DuckDuckGo) ────────────────────────────────────────────────
def extract_domains_from_sources(path: Path) -> list[str]:
    """
    Extrai domínios de sites de um ficheiro .md de fontes.
    Procura por padrões como [abola.pt](https://...) ou domínios diretos.
    """
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    domains = set()
    # Procurar por [texto](url) — extrair domínio do URL
    for m in re.finditer(r'\[([^\]]+)\]\(https?://(?:www\.)?([^/)\s]+)', content):
        domains.add(m.group(2))
    # Procurar por domínios diretos no texto (ex: "abola.pt", "record.pt")
    for m in re.finditer(r'`([a-z0-9-]+\.[a-z]{2,}(?:\.[a-z]{2,})?)`', content):
        domains.add(m.group(1))
    return sorted(domains)


def search_web(queries: list[str], max_per_query: int = 10, source_domains: list[str] = None) -> list[dict]:
    """
    Pesquisa DuckDuckGo por artigos de transferências.
    Devolve uma lista de {"title", "url", "snippet"} deduplicada por URL.
    100% gratuito, sem API key, sem credit card.

    Se source_domains for fornecido, prioriza resultados desses domínios
    (não filtra exclusivamente, mas dá prioridade no ranking).
    """
    print("🔍 A pesquisar notícias no DuckDuckGo...")
    all_results = []
    seen_urls = set()
    ddgs = DDGS()

    for query in queries:
        print(f"   → {query}")
        try:
            results = ddgs.text(
                query,
                timelimit="m",       # último mês
                max_results=max_per_query,
            )
            for r in results:
                url = r.get("href", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "snippet": r.get("body", ""),
                    })
        except Exception as e:
            print(f"   ⚠️  Erro na pesquisa '{query}': {e}")
        time.sleep(1)  # respeitar rate limits do DuckDuckGo

    # Se temos domínios de fontes confiáveis, ordenar para dar prioridade
    if source_domains:
        def domain_priority(result):
            url_lower = result["url"].lower()
            for i, domain in enumerate(source_domains):
                if domain in url_lower:
                    return i  # menor = melhor prioridade
            return len(source_domains)  # domínios não listados ficam no fim
        all_results.sort(key=domain_priority)
        print(f"   📋 Resultados ordenados por prioridade de fontes ({len(source_domains)} domínios confiáveis).")

    print(f"📋 {len(all_results)} artigos únicos encontrados.")
    return all_results


def format_search_results(results: list[dict]) -> str:
    """Formata os resultados da pesquisa para incluir no prompt do LLM."""
    if not results:
        return "*Nenhum artigo encontrado na pesquisa.*"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    URL: {r['url']}")
        snippet = r["snippet"][:300] if r["snippet"] else ""
        lines.append(f"    Resumo: {snippet}")
        lines.append("")
    return "\n".join(lines)


# ─── Construção do prompt ────────────────────────────────────────────────────
def build_prompt(existing_content: str, is_update: bool, market_label: str,
                 search_results: str) -> str:
    """
    Carrega o template do prompt e preenche os tokens {{...}}.
    Inclui os resultados da pesquisa web como contexto para o LLM.
    """
    template = load_file(PROMPT_PATH, "do prompt")
    trustworthy = load_file(TRUSTWORTHY_PATH, "das fontes fiáveis")
    sketchy = load_file(SKETCHY_PATH, "das fontes menos fiáveis")

    if is_update:
        update_section = (
            "## Modo: ATUALIZAR\n"
            "Abaixo está o conteúdo ATUAL da thread. Atualiza-o:\n"
            "- Preserva rumores ainda válidos e relevantes.\n"
            "- Atualiza o estado de rumores que evoluíram (concretizado, cancelado, renovado).\n"
            "- Remove rumores claramente desmentidos ou caducados.\n"
            "- Adiciona os novos rumores/notícias que encontraste.\n"
            "- Mantém o formato de tabelas e NÃO inclua o rodapé (o bot trata disso).\n\n"
            "### Conteúdo atual da thread:\n"
            f"{existing_content}"
        )
    else:
        update_section = (
            "## Modo: CRIAR DO ZERO\n"
            "Gera o conteúdo completo das tabelas a partir da tua pesquisa. "
            "Não existe conteúdo prévio a preservar. "
            "Não inclua rodapé (o bot trata disso)."
        )

    prompt = (
        template
        .replace("{{TRUSTWORTHY_SOURCES}}", trustworthy)
        .replace("{{SKETCHY_SOURCES}}", sketchy)
        .replace("{{UPDATE_SECTION}}", update_section)
        .replace("{{EXISTING_CONTENT}}", existing_content)
        .replace("{{MARKET_LABEL}}", market_label)
        .replace("{{SEARCH_RESULTS}}", search_results)
    )
    return prompt


# ─── LLM (multi-provider com fallback) ────────────────────────────────────────
def fetch_opencode_zen_models(api_key: str) -> list[str]:
    """
    Obtém a lista de modelos free disponíveis no OpenCode Zen via /models endpoint.
    Devolve os modelos ordenados por preferência (melhor primeiro).
    Se o endpoint falhar, usa a lista de fallback hardcoded.
    """
    try:
        client = OpenAI(api_key=api_key, base_url=OPENCODE_ZEN_BASE_URL)
        response = client.models.list()
        all_models = [m.id for m in response.data]
        # Filtrar para modelos free (têm "free" no ID ou são conhecidos como free)
        free_models = [
            m for m in all_models
            if "free" in m.lower() or m in OPENCODE_ZEN_MODEL_PREFERENCE
        ]
        if not free_models:
            print("   ⚠️  Nenhum modelo free encontrado no /models. A usar lista de fallback.")
            return OPENCODE_ZEN_FALLBACK_MODELS
        # Ordenar por preferência (score descendente), depois alfabeticamente
        free_models.sort(
            key=lambda m: (-OPENCODE_ZEN_MODEL_PREFERENCE.get(m, 0), m)
        )
        return free_models
    except Exception as e:
        print(f"   ⚠️  Erro ao obter modelos do OpenCode Zen: {e}. A usar lista de fallback.")
        return OPENCODE_ZEN_FALLBACK_MODELS


def get_available_providers() -> list[dict]:
    """
    Devolve os providers configurados (com API key presente), por ordem de prioridade:
    1. OpenCode Zen (múltiplos modelos free, obtidos dinamicamente e ranqueados)
    2. DeepSeek
    3. Groq
    """
    available = []

    # 1. OpenCode Zen (primário) — múltiplos modelos free
    opencode_key = os.environ.get(OPENCODE_ZEN_ENV_KEY, "").strip()
    if opencode_key and not opencode_key.startswith("tua_chave") and not opencode_key.startswith("your_key"):
        print("   📋 A obter modelos free do OpenCode Zen...")
        models = fetch_opencode_zen_models(opencode_key)
        print(f"   📋 Modelos free disponíveis (por ordem de preferência): {', '.join(models)}")
        for model in models:
            available.append({
                "name": "OpenCode Zen",
                "env_key": OPENCODE_ZEN_ENV_KEY,
                "api_key": opencode_key,
                "base_url": OPENCODE_ZEN_BASE_URL,
                "model": model,
            })

    # 2. DeepSeek (fallback 1)
    for p in LLM_PROVIDERS:
        key = os.environ.get(p["env_key"], "").strip()
        if key and not key.startswith("tua_chave") and not key.startswith("your_key"):
            available.append({**p, "api_key": key})

    return available


def validate_provider(provider: dict) -> bool:
    """
    Faz uma chamada de teste mínima para validar a API key.
    Usa max_tokens=100 porque alguns modelos (ex: deepseek-v4-flash-free) são
    "thinking models" que consomem tokens em raciocínio interno antes de produzir output.
    Para modelos deepseek, desativa o thinking mode para validação rápida.
    Devolve True se a key for válida, False caso contrário.
    """
    try:
        client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"])
        kwargs = {
            "model": provider["model"],
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 100,
        }
        if "deepseek" in provider["model"].lower():
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        client.chat.completions.create(**kwargs)
        return True
    except Exception:
        return False


def try_provider(provider: dict, prompt: str) -> str:
    """
    Tenta gerar conteúdo com um provider específico.
    Levanta exceção em caso de erro (para o fallback poder apanhar).

    Para modelos "thinking" (ex: deepseek-v4-flash-free), o thinking mode é
    MANTIDO ATIVADO por defeito — o raciocínio melhora a qualidade do output
    (classificação correta de entradas/saídas, extração de nomes reais, etc.).
    Apenas é desativado se o modelo falhar (resposta vazia), como fallback.
    """
    client = OpenAI(
        api_key=provider["api_key"],
        base_url=provider["base_url"],
    )

    kwargs = {
        "model": provider["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "És um jornalista desportivo especialista no FC Porto. "
                    "Geras conteúdo em Português de Portugal. "
                    "Responde APENAS com o Markdown pedido, sem saudações ou comentários."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 32000,  # alto para permitir thinking + output
    }

    # Para modelos deepseek, tentar PRIMEIRO com thinking ativado (melhor qualidade).
    # Se falhar (resposta vazia), o fallback na função generate_content tentará
    # o próximo modelo. O thinking só é desativado se explicitamente necessário.
    # Nota: deepseek-v4-flash-free usa thinking por defeito — não precisamos de ativar explicitamente.

    response = client.chat.completions.create(**kwargs)

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise ValueError("Resposta vazia do LLM.")
    return content.strip()


def generate_content(prompt: str) -> str:
    """
    Gera conteúdo com o LLM, tentando providers por ordem de prioridade.
    Primeiro valida quais keys funcionam (chamada de teste mínima).
    Depois tenta gerar com o primário; se falhar (rate limit, erro), tenta o fallback.
    """
    providers = get_available_providers()
    if not providers:
        print("❌ Nenhuma API key de LLM configurada.")
        print("   Define pelo menos uma destas variáveis de ambiente:")
        print(f"     - {OPENCODE_ZEN_ENV_KEY} (OpenCode Zen — primário)")
        for p in LLM_PROVIDERS:
            print(f"     - {p['env_key']} ({p['name']})")
        sys.exit(1)

    # 1. Validar keys (chamada de teste mínima de 1 token)
    # Para OpenCode Zen, validar apenas o primeiro modelo — se a key funciona para um, funciona para todos.
    print(f"🔑 A validar {len(providers)} provider(s) configurado(s)...")
    valid_providers = []
    validated_opencode = False  # evitar validar cada modelo OpenCode Zen individualmente

    for p in providers:
        if p["name"] == "OpenCode Zen" and validated_opencode:
            # Já validámos a key do OpenCode Zen — saltar validação individual
            valid_providers.append(p)
            continue
        print(f"   → A validar {p['name']}" + (f" ({p['model']})" if p["name"] == "OpenCode Zen" else "") + "...", end=" ", flush=True)
        if validate_provider(p):
            print("✅ válido")
            valid_providers.append(p)
            if p["name"] == "OpenCode Zen":
                validated_opencode = True
        else:
            print("❌ inválido (key errada, expirada, ou sem acesso ao modelo)")

    if not valid_providers:
        print("❌ Nenhuma API key válida. Verifica as chaves no .env ou GitHub Secrets.")
        sys.exit(1)

    provider_names = " → ".join(p["name"] for p in valid_providers)
    print(f"🤖 A gerar conteúdo com LLM (providers válidos: {provider_names})...")

    # 2. Tentar gerar conteúdo, com fallback automático
    last_error = None
    for i, provider in enumerate(valid_providers):
        is_last = (i == len(valid_providers) - 1)
        try:
            print(f"   → A tentar {provider['name']} (modelo: {provider['model']})...")
            content = try_provider(provider, prompt)
            print(f"✅ Conteúdo gerado com {provider['name']} ({len(content)} caracteres).")
            return content
        except RateLimitError as e:
            last_error = e
            print(f"   ⚠️  {provider['name']}: rate limit atingido (429).")
            if is_last:
                print(f"   ❌ Sem mais providers para tentar.")
            else:
                print(f"   🔄 A tentar fallback: {valid_providers[i+1]['name']}...")
        except (APIConnectionError, APIError) as e:
            last_error = e
            print(f"   ⚠️  {provider['name']}: erro da API ({type(e).__name__}).")
            if is_last:
                print(f"   ❌ Sem mais providers para tentar.")
            else:
                print(f"   🔄 A tentar fallback: {valid_providers[i+1]['name']}...")
        except Exception as e:
            last_error = e
            print(f"   ⚠️  {provider['name']}: erro inesperado ({type(e).__name__}: {e}).")
            if is_last:
                print(f"   ❌ Sem mais providers para tentar.")
            else:
                print(f"   🔄 A tentar fallback: {valid_providers[i+1]['name']}...")

    print(f"❌ Todos os providers falharam. Último erro: {last_error}")
    sys.exit(1)


# ─── Reddit ────────────────────────────────────────────────────────────────────
def get_reddit() -> praw.Reddit:
    """Autentica no Reddit via PRAW."""
    print("🔑 A autenticar no Reddit...")
    reddit = praw.Reddit(
        client_id=get_env("REDDIT_CLIENT_ID"),
        client_secret=get_env("REDDIT_CLIENT_SECRET"),
        username=get_env("REDDIT_USERNAME"),
        password=get_env("REDDIT_PASSWORD"),
        user_agent="PortoBotNews/1.0 (by u/" + os.environ.get("REDDIT_USERNAME", "bot") + ")",
    )
    try:
        _ = reddit.user.me()
    except Exception as e:
        print(f"❌ Falha na autenticação do Reddit: {e}")
        sys.exit(1)
    print(f"✅ Autenticado como u/{reddit.user.me()}")
    return reddit


def find_post_by_title(reddit: praw.Reddit, title: str):
    """
    Procura nos submissions do próprio bot por um post com o título exato.
    Usa reddit.user.me().submissions.new() em vez de search() — é mais fiável
    (não depende do índice de pesquisa do Reddit, que tem lag e problemas de tokenização).
    Devolve o submission encontrado, ou None se não existir.
    """
    me = reddit.user.me()
    if me is None:
        print("⚠️  Não autenticado — reddit.user.me() devolveu None.")
        return None

    try:
        # limit=None devolve até 1000 submissions (mais recentes primeiro)
        # Filtramos por subreddit (FCPorto) e por título exato do lado do cliente
        for submission in me.submissions.new(limit=None):
            if (submission.title == title and
                    submission.subreddit.display_name.lower() == SUBREDDIT.lower()):
                return submission
    except Exception as e:
        print(f"⚠️  Erro ao procurar post por título: {e}")
    return None


def get_current_post(reddit: praw.Reddit, post_id: str, post_title: str):
    """
    Devolve (submission, existing_content, is_update).
    Estratégia:
    1. Se REDDIT_POST_ID estiver configurado, usa-o (override manual).
    2. Caso contrário, procura nos posts do bot por um com o título exato (determinístico).
    3. Se encontrar o post:
       - Se tiver o BOT_MARKER → is_update=True (atualizar)
       - Se não tiver o marker → is_update=False (criar do zero, sobrescrever)
    4. Se não encontrar → (None, "", False) → criar post novo.
    """
    # 1. Override manual via REDDIT_POST_ID
    if post_id:
        print(f"📥 REDDIT_POST_ID configurado — a obter o post (ID: {post_id})...")
        try:
            submission = reddit.submission(id=post_id)
            existing_content = submission.selftext or ""
        except Exception as e:
            print(f"❌ Não foi possível obter o post {post_id}: {e}")
            sys.exit(1)
        if BOT_MARKER in existing_content:
            print("🔄 Post já atualizado pelo bot antes — modo: ATUALIZAR.")
            return submission, existing_content, True
        else:
            print("🧹 Post ainda não marcado pelo bot (novo/lixo) — modo: CRIAR DO ZERO.")
            return submission, existing_content, False

    # 2. Procura automática por título
    print(f"🔎 A procurar post existente com o título: \"{post_title}\"...")
    submission = find_post_by_title(reddit, post_title)
    if submission:
        existing_content = submission.selftext or ""
        print(f"✅ Post encontrado: https://redd.it/{submission.id}")
        if BOT_MARKER in existing_content:
            print("🔄 Post já atualizado pelo bot antes — modo: ATUALIZAR.")
            return submission, existing_content, True
        else:
            print("🧹 Post encontrado mas sem marcador do bot — modo: CRIAR DO ZERO.")
            return submission, existing_content, False
    else:
        print("🆕 Nenhum post encontrado para esta janela — vou criar um novo.")
        return None, "", False


def build_footer(repo_url: str) -> str:
    """Constrói o rodapé de transparência com o link do GitHub + marcador do bot."""
    return (
        "\n\n---\n\n"
        "🤖 **PortoBotNews** — Bot open-source de transferências do FC Porto  \n"
        "*Gerado e atualizado automaticamente via Inteligência Artificial "
        "(LLM + DuckDuckGo) com pesquisa web.*\n\n"
        f"💻 [Código fonte no GitHub]({repo_url}) · ⚽ Para a comunidade r/FCPorto\n\n"
        f"{BOT_MARKER}"
    )


def publish(reddit: praw.Reddit, content: str, submission, post_title: str, repo_url: str) -> str:
    """
    Publica o conteúdo no Reddit.
    - Se submission for fornecido (post existente) → edita-o.
    - Se submission for None → cria um post novo com o título dinâmico.
    Devolve o ID do post (novo ou existente).
    """
    final_text = content + build_footer(repo_url)

    if submission:
        submission.edit(final_text)
        print(f"✅ Post atualizado: https://redd.it/{submission.id}")
        return submission.id
    else:
        subreddit = reddit.subreddit(SUBREDDIT)
        new_submission = subreddit.submit(
            title=post_title,
            selftext=final_text,
            flair_id=None,
        )
        print(f"✅ Novo post criado: https://redd.it/{new_submission.id}")
        print(f"   Título: {post_title}")
        return new_submission.id


def write_preview(content: str, repo_url: str, is_update: bool) -> None:
    """Escreve o conteúdo gerado em preview.md para revisão (modo dry-run)."""
    final_text = content + build_footer(repo_url)
    PREVIEW_PATH.write_text(final_text, encoding="utf-8")
    mode_label = "ATUALIZAR" if is_update else "CRIAR DO ZERO"
    print(f"📝 Preview escrito em {PREVIEW_PATH} (modo: {mode_label})")
    print("   Abre o ficheiro num editor Markdown para ver o resultado.")
    print("   Nada foi publicado no Reddit.")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Bot de transferências do FC Porto para o Reddit.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Gera o conteúdo mas NÃO publica. Escreve em preview.md.",
    )
    args = parser.parse_args()

    load_dotenv()

    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

    repo_url = get_env("GITHUB_REPO_URL", required=False,
                       default="https://github.com/PortoBotNews/PortoBotNews")
    post_id = get_env("REDDIT_POST_ID", required=False)

    if dry_run:
        print("🔍 MODO PREVIEW (dry-run) — nada será publicado no Reddit.")

    # 1. Determinar a janela de transferências e o título do post (determinístico)
    market_type, market_year = get_market_window()
    market_label = f"Mercado de {market_type} {market_year}"
    post_title = build_post_title(market_label)
    print(f"📅 Janela ativa: {market_label}")
    print(f"📝 Título do post: {post_title}")

    # 2. Autenticar no Reddit e obter o post atual (procura por título se post_id não definido)
    reddit = None
    existing_content = ""
    is_update = False
    submission = None
    reddit_creds = all(os.environ.get(v) for v in
                       ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET",
                        "REDDIT_USERNAME", "REDDIT_PASSWORD"))

    if dry_run and not (reddit_creds and post_id):
        if PREVIEW_PATH.exists():
            prev = PREVIEW_PATH.read_text(encoding="utf-8")
            if BOT_MARKER in prev:
                existing_content = prev
                is_update = True
                print("🔄 preview.md anterior encontrado com marcador do bot — modo: ATUALIZAR.")
            else:
                print("🧹 preview.md anterior sem marcador do bot — modo: CRIAR DO ZERO.")
        else:
            print("🆕 Sem preview.md anterior — modo: CRIAR DO ZERO.")
    else:
        reddit = get_reddit()
        submission, existing_content, is_update = get_current_post(reddit, post_id, post_title)

    # 3. Pesquisar web (DuckDuckGo) — queries dinâmicas + priorizar fontes confiáveis
    #    Extrair domínios dos ficheiros de fontes para construir queries site-specific
    trustworthy_domains = extract_domains_from_sources(TRUSTWORTHY_PATH)
    sketchy_domains = extract_domains_from_sources(SKETCHY_PATH)
    queries = build_search_queries(market_label, trustworthy_domains, sketchy_domains)
    print(f"📋 {len(trustworthy_domains)} domínios Tier 1 + {len(sketchy_domains)} domínios Tier 2 carregados das fontes.")
    search_results_raw = search_web(queries, source_domains=trustworthy_domains)
    search_results_formatted = format_search_results(search_results_raw)

    # 4. Construir o prompt com os resultados da pesquisa
    prompt = build_prompt(existing_content, is_update, market_label, search_results_formatted)

    # 5. Gerar conteúdo com o LLM
    content = generate_content(prompt)

    # 6. Publicar OU escrever preview
    if dry_run:
        write_preview(content, repo_url, is_update)
    else:
        publish(reddit, content, submission, post_title, repo_url)
    print("🎉 Concluído com sucesso!")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"❌ Erro fatal inesperado: {e}")
        sys.exit(1)
