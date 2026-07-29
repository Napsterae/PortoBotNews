"""
PortoBotNews — prompt building and two-pass orchestration.
"""
import re
import sys
from pathlib import Path

from .constants import (
    PROMPT_PATH,
    TRUSTWORTHY_PATH,
    SKETCHY_PATH,
    FALLBACK_TRUSTWORTHY,
    FALLBACK_SKETCHY,
)


def load_file(path: Path, label: str, fallback: str = "") -> str:
    """Lê um ficheiro .md; usa fallback se não existir."""
    if not path.exists():
        if fallback:
            print(f"   ⚠️  Ficheiro {label} não encontrado em {path} — a usar defaults.")
            return fallback
        print(f"❌ Erro: ficheiro {label} não encontrado em {path}")
        sys.exit(1)
    return path.read_text(encoding="utf-8")


# ─── Pass instructions ────────────────────────────────────────────────────────
PASS1_INSTRUCTIONS = """## Passo 1: Rascunho + Identificação de itens para re-pesquisa

Gera o rascunho completo do post (todas as tabelas e secções) com base nos resultados da pesquisa.
Depois do rascunho, no **final da tua resposta**, adiciona uma secção especial com itens que
precisam de ser re-pesquisados para melhorar a qualidade do post.

### O que identificar para re-pesquisa:
- **Fontes sketchy/tier 2**: entradas suportadas apenas por fontes pouco fiáveis que poderiam ter fontes melhores.
- **Rumores desatualizados**: rumores cuja fonte mais recente tem mais de 2 semanas — precisam de atualização.
- **Informação em falta**: entradas com muitos `❓` (custo, comissão, clube) que poderiam ser completadas.
- **Rumores contraditórios**: diferentes fontes reportam valores ou destinos diferentes.
- **Jogadores sem nome real**: entradas onde o nome do jogador não foi claramente identificado.

### Formato da secção de re-pesquisa (OBRIGATÓRIO no final):
Depois de todo o Markdown do rascunho, adiciona EXATAMENTE este formato:

<!-- REFETCH_REQUESTS_START -->
- "query de pesquisa específica 1"
- "query de pesquisa específica 2"
<!-- REFETCH_REQUESTS_END -->

Cada query deve ser uma pesquisa específica que o bot pode fazer no DuckDuckGo para encontrar
informação melhor sobre esse item. Exemplos:
- "Hwang In-beom FC Porto transfer fee 2026"
- "Diogo Costa Chelsea transfer rumor July 2026"
- "Alan Varela Roma transfer latest news"

Se não há itens que precisem de re-pesquisa, inclui a secção vazia:
<!-- REFETCH_REQUESTS_START -->
<!-- REFETCH_REQUESTS_END -->

**Importante:** A secção REFETCH deve estar no final, depois de todo o Markdown do post.
O bot vai remover esta secção antes de publicar."""

PASS2_INSTRUCTIONS = """## Passo 2: Refinar o rascunho com informação adicional

Abaixo tens:
1. O **rascunho** gerado no Passo 1 (com base na pesquisa inicial).
2. **Resultados de re-pesquisa** — artigos adicionais encontrados pelo bot com base nos itens
   que identificaste como precisando de melhor informação.

Usa a nova informação para **refinar e melhorar** o rascunho:
- Substitui fontes sketchy por fontes mais fiáveis encontradas na re-pesquisa.
- Atualiza rumores desatualizados com informação mais recente.
- Preenche campos `❓` com a nova informação disponível.
- Resolve contradições usando as fontes mais fiáveis.
- Adiciona entradas novas que apareçam na re-pesquisa.
- Remove entradas que a re-pesquisa revelou serem incorretas ou desmentidas.

Gera o post **completo e final** em Markdown. Não incluis a secção REFETCH desta vez —
apenas o Markdown final do post."""


def build_prompt(existing_content: str, is_update: bool, market_label: str,
                 search_results: str, pass_instructions: str = "") -> str:
    """
    Carrega o template do prompt e preenche os tokens {{...}}.
    Inclui os resultados da pesquisa web como contexto para o LLM.
    """
    template = load_file(PROMPT_PATH, "do prompt")
    trustworthy = load_file(TRUSTWORTHY_PATH, "das fontes fiáveis", FALLBACK_TRUSTWORTHY)
    sketchy = load_file(SKETCHY_PATH, "das fontes menos fiáveis", FALLBACK_SKETCHY)

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
        .replace("{{PASS_INSTRUCTIONS}}", pass_instructions)
    )
    return prompt


def parse_refetch_requests(llm_output: str) -> tuple[str, list[str]]:
    """
    Separa o rascunho do Pass 1 das queries de re-pesquisa.
    Devolve (draft_markdown, refetch_queries).
    """
    match = re.search(
        r'<!--\s*REFETCH_REQUESTS_START\s*-->(.*?)<!--\s*REFETCH_REQUESTS_END\s*-->',
        llm_output, re.DOTALL
    )
    if not match:
        return llm_output.strip(), []

    refetch_section = match.group(1)
    draft = llm_output[:match.start()].strip()

    queries = []
    for line in refetch_section.strip().split("\n"):
        line = line.strip()
        if line.startswith("-") or line.startswith("*"):
            query = line.lstrip("-* ").strip().strip('"').strip("'").strip()
            if query:
                queries.append(query)

    return draft, queries


def strip_refetch_section(text: str) -> str:
    """Remove a secção REFETCH do output do LLM."""
    return re.sub(
        r'<!--\s*REFETCH_REQUESTS_START\s*-->.*?<!--\s*REFETCH_REQUESTS_END\s*-->',
        '',
        text,
        flags=re.DOTALL
    ).strip()
