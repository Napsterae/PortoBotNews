"""
PortoBotNews — Bot de transferências do FC Porto para o Reddit.

Arquitetura (100% gratuita, sem billing):
  1. Pesquisa web com DuckDuckGo (ddgs) — obtém artigos reais com URLs reais.
  2. LLM com OpenCode Zen / DeepSeek / Groq (fallback automático) — processa os artigos
     e gera o Markdown. Se um provider falhar, tenta o seguinte.
  3. Autentica-se no Reddit e vai buscar o conteúdo atual da thread.
  4. Decide: ATUALIZAR (post já tem o marcador do bot) ou CRIAR DO ZERO.
  5. Valida o conteúdo gerado (URLs, tabelas, nomes) antes de publicar.
  6. Anexa o rodapé de transparência (com link do GitHub + marcador do bot).
  7. Publica: edita o post existente ou cria um novo.

Modo preview (--dry-run ou DRY_RUN=true):
  Gera o conteúdo mas NÃO publica no Reddit. Escreve o resultado
  em preview.md para revisão. Útil para testar antes de publicar.
"""
import argparse
import os
import sys

# Forcar UTF-8 no stdout/stderr (Windows usa cp1252 por defeito, quebra com emojis)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from bot.constants import (
    PREVIEW_PATH,
    BOT_MARKERS_ACCEPTED,
    TRUSTWORTHY_PATH,
    SKETCHY_PATH,
    DEFAULT_REPO_URL,
)
from bot.market import get_market_window, build_post_title
from bot.search import (
    extract_domains_from_sources,
    build_search_queries,
    search_web,
    format_search_results,
)
from bot.cache import filter_new_urls, mark_as_processed
from bot.feeds import fetch_rss_feeds, merge_results
from bot.prompt import (
    build_prompt,
    parse_refetch_requests,
    strip_refetch_section,
    PASS1_INSTRUCTIONS,
    PASS2_INSTRUCTIONS,
)
from bot.llm import generate_content
from bot.validation import validate_content
from bot.diff import has_content_changed, save_hash
from bot.reddit import (
    get_reddit,
    get_current_post,
    find_previous_window_post,
    publish,
    get_env,
)
from bot.notify import notify_failure


def _has_bot_marker(content: str) -> bool:
    """True if content carries any known PortoBotNews marker (current or legacy)."""
    return any(marker in content for marker in BOT_MARKERS_ACCEPTED)


def write_preview(content: str, repo_url: str, is_update: bool,
                  previous_post_url: str = "") -> None:
    """Escreve o conteúdo gerado em preview.md para revisão (modo dry-run)."""
    from bot.reddit import build_footer
    final_text = content + build_footer(repo_url, previous_post_url)
    PREVIEW_PATH.write_text(final_text, encoding="utf-8")
    mode_label = "ATUALIZAR" if is_update else "CRIAR DO ZERO"
    print(f"📝 Preview escrito em {PREVIEW_PATH} (modo: {mode_label})")
    print("   Abre o ficheiro num editor Markdown para ver o resultado.")
    print("   Nada foi publicado no Reddit.")


def main():
    parser = argparse.ArgumentParser(description="Bot de transferências do FC Porto para o Reddit.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Gera o conteúdo mas NÃO publica. Escreve em preview.md.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Força regeneração mesmo que o conteúdo não tenha mudado.",
    )
    args = parser.parse_args()

    load_dotenv()

    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

    repo_url = get_env("GITHUB_REPO_URL", required=False,
                       default=DEFAULT_REPO_URL)
    post_id = get_env("REDDIT_POST_ID", required=False)

    if dry_run:
        print("🔍 MODO PREVIEW (dry-run) — nada será publicado no Reddit.")

    # 1. Determinar a janela de transferências e o título do post (determinístico)
    market_type, market_year = get_market_window()
    market_label = f"Mercado de {market_type} {market_year}"
    post_title = build_post_title(market_label)
    print(f"📅 Janela ativa: {market_label}")
    print(f"📝 Título do post: {post_title}")

    # 2. Autenticar no Reddit e obter o post atual
    reddit = None
    existing_content = ""
    is_update = False
    submission = None
    previous_post_url = ""
    reddit_creds = all(os.environ.get(v) for v in
                       ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET",
                        "REDDIT_USERNAME", "REDDIT_PASSWORD"))

    if dry_run and not (reddit_creds and post_id):
        if PREVIEW_PATH.exists():
            prev = PREVIEW_PATH.read_text(encoding="utf-8")
            if _has_bot_marker(prev):
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
        # Procurar post da janela anterior para arquivamento
        prev = find_previous_window_post(reddit, post_title)
        if prev:
            previous_post_url = f"https://redd.it/{prev.id}"
            print(f"📋 Post da janela anterior encontrado: {previous_post_url}")

    # 3. Pesquisar web (DuckDuckGo) + RSS feeds — queries dinâmicas + fontes múltiplas
    trustworthy_domains = extract_domains_from_sources(TRUSTWORTHY_PATH)
    sketchy_domains = extract_domains_from_sources(SKETCHY_PATH)
    queries = build_search_queries(market_label, trustworthy_domains, sketchy_domains)
    print(f"📋 {len(trustworthy_domains)} domínios Tier 1 + {len(sketchy_domains)} domínios Tier 2 carregados das fontes.")

    # 3a. Pesquisar DuckDuckGo
    ddg_results = search_web(queries, source_domains=trustworthy_domains,
                             market_year=market_year)

    # 3b. Obter RSS feeds (artigos mais frescos, com datas reais)
    rss_results = fetch_rss_feeds()

    # 3c. Combinar resultados — RSS tem prioridade (datas mais fiáveis)
    search_results_raw = merge_results(ddg_results, rss_results)
    print(f"📊 Total: {len(search_results_raw)} artigos únicos ({len(ddg_results)} DDG + {len(rss_results)} RSS).")

    # 3d. Deduplicação — remover URLs já processados em runs anteriores
    new_results = filter_new_urls(search_results_raw)
    skipped = len(search_results_raw) - len(new_results)
    if skipped > 0:
        print(f"🗂️  {skipped} artigos já processados em runs anteriores — ignorados.")
    if not new_results and not is_update:
        print("⚠️  Nenhum artigo novo encontrado e sem conteúdo prévio — nada a fazer.")
        return

    # 3e. Change detection — se o conteúdo não mudou, saltar LLM
    if not args.force and is_update and existing_content:
        # Usar os novos resultados para gerar um "esboço rápido" e comparar
        # Se não há novos artigos E o conteúdo existente já foi gerado pelo bot,
        # é provável que nada tenha mudado
        if not new_results:
            print("✅ Nenhum artigo novo desde a última run — conteúdo provavelmente inalterado.")
            print("   (Usa --force para regenerar mesmo assim.)")
            return

    search_results_formatted = format_search_results(new_results)

    # 4. PASSO 1: Gerar rascunho + identificar itens para re-pesquisa
    print("\n" + "=" * 60)
    print("📝 PASSO 1: Gerar rascunho + identificar itens para re-pesquisa")
    print("=" * 60)
    prompt_pass1 = build_prompt(existing_content, is_update, market_label,
                                search_results_formatted, PASS1_INSTRUCTIONS)
    pass1_output = generate_content(prompt_pass1)

    # 5. Separar rascunho das queries de re-pesquisa
    draft, refetch_queries = parse_refetch_requests(pass1_output)
    print(f"📋 Rascunho gerado ({len(draft)} caracteres).")

    refetch_results_raw: list[dict] = []
    if refetch_queries:
        print(f"🔎 {len(refetch_queries)} itens identificados para re-pesquisa:")
        for i, q in enumerate(refetch_queries, 1):
            print(f"   [{i}] {q}")

        # 6. Re-pesquisar os itens identificados
        print("\n" + "=" * 60)
        print("🔍 RE-PESQUISA: A procurar informação adicional")
        print("=" * 60)
        refetch_results_raw = search_web(refetch_queries, max_per_query=5,
                                         source_domains=trustworthy_domains,
                                         market_year=market_year)
        refetch_results_formatted = format_search_results(refetch_results_raw)

        # 7. PASSO 2: Refinar o rascunho com a nova informação
        print("\n" + "=" * 60)
        print("📝 PASSO 2: Refinar rascunho com informação adicional")
        print("=" * 60)
        combined_results = (
            "## Resultados da pesquisa inicial\n\n"
            f"{search_results_formatted}\n\n"
            "## Resultados da re-pesquisa (informação adicional)\n\n"
            f"{refetch_results_formatted}\n\n"
            "## Rascunho do Passo 1 (a refinar)\n\n"
            f"{draft}"
        )
        prompt_pass2 = build_prompt(existing_content, is_update, market_label,
                                    combined_results, PASS2_INSTRUCTIONS)
        content = generate_content(prompt_pass2)
        content = strip_refetch_section(content)
        print(f"✅ Post final gerado ({len(content)} caracteres).")
    else:
        print("✅ Nenhum item precisou de re-pesquisa — a usar o rascunho do Passo 1.")
        content = strip_refetch_section(draft)

    # 7a. Validação de conteúdo — verificar URLs, tabelas, nomes
    print("\n" + "=" * 60)
    print("🔍 VALIDAÇÃO: A verificar conteúdo gerado")
    print("=" * 60)
    refetch_raw: list[dict] = []
    if refetch_queries:
        refetch_raw = refetch_results_raw
    all_results = new_results + refetch_raw
    validation = validate_content(content, all_results,
                                 banned_domains=sketchy_domains)

    if validation["warnings"]:
        for w in validation["warnings"]:
            print(f"   ⚠️  {w}")

    if not validation["valid"]:
        print(f"   ❌ {len(validation['issues'])} problema(s) encontrado(s):")
        for issue in validation["issues"]:
            print(f"      • {issue}")
        print("\n⚠️  O conteúdo tem problemas mas será publicado mesmo assim.")
        print("   Revisa o output acima e considera corrigir manualmente.")
    else:
        print("   ✅ Conteúdo validado com sucesso — sem problemas detetados.")

    # 8. Publicar OU escrever preview
    if dry_run:
        write_preview(content, repo_url, is_update, previous_post_url)
    else:
        # Change detection: se o conteúdo gerado é idêntico ao último publicado,
        # saltar o edit do Reddit (poupa uma API call e evita edits "vazios").
        if not args.force and is_update and not has_content_changed(content):
            print("✅ Conteúdo gerado idêntico ao último publicado — não é preciso editar o post.")
        else:
            publish(reddit, content, submission, post_title, repo_url, previous_post_url)
            # Guardar hash do conteúdo publicado para change detection futura
            save_hash(content)
            # Marcar URLs como processados
            mark_as_processed(all_results)

    print("🎉 Concluído com sucesso!")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"❌ Erro fatal inesperado: {error_msg}")
        notify_failure(error_msg)
        sys.exit(1)
