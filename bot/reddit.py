"""
PortoBotNews — Reddit interaction (PRAW).
"""
import os
import sys

import praw

from .constants import SUBREDDIT, BOT_MARKER, BOT_MARKERS_ACCEPTED


def get_env(name: str, required: bool = True, default: str = "") -> str:
    """Lê uma variável de ambiente; aborta se for obrigatória e estiver vazia."""
    val = os.environ.get(name, "").strip()
    if required and not val:
        print(f"❌ Erro: variável de ambiente {name} não definida.")
        sys.exit(1)
    return val or default


def get_reddit() -> praw.Reddit:
    """Autentica no Reddit via PRAW."""
    print("🔑 A autenticar no Reddit...")
    reddit = praw.Reddit(
        client_id=get_env("REDDIT_CLIENT_ID"),
        client_secret=get_env("REDDIT_CLIENT_SECRET"),
        username=get_env("REDDIT_USERNAME"),
        password=get_env("REDDIT_PASSWORD"),
        user_agent="PortoBotNews/2.0 (by u/" + os.environ.get("REDDIT_USERNAME", "bot") + ")",
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
    Devolve o submission encontrado, ou None se não existir.
    """
    me = reddit.user.me()
    if me is None:
        print("⚠️  Não autenticado — reddit.user.me() devolveu None.")
        return None

    try:
        for submission in me.submissions.new(limit=None):
            if (submission.title == title and
                    submission.subreddit.display_name.lower() == SUBREDDIT.lower()):
                return submission
    except Exception as e:
        print(f"⚠️  Erro ao procurar post por título: {e}")
    return None


def find_previous_window_post(reddit: praw.Reddit, current_title: str):
    """
    Procura o post da janela anterior (para arquivamento/link).
    Devolve o submission da janela anterior, ou None.
    """
    me = reddit.user.me()
    if me is None:
        return None

    try:
        for submission in me.submissions.new(limit=10):
            if (submission.title != current_title and
                    submission.title.startswith("🐉 FC Porto —") and
                    submission.subreddit.display_name.lower() == SUBREDDIT.lower()):
                return submission
    except Exception as e:
        print(f"⚠️  Erro ao procurar post anterior: {e}")
    return None


def get_current_post(reddit: praw.Reddit, post_id: str, post_title: str):
    """
    Devolve (submission, existing_content, is_update).
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
        if _has_bot_marker(existing_content):
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
        if _has_bot_marker(existing_content):
            print("🔄 Post já atualizado pelo bot antes — modo: ATUALIZAR.")
            return submission, existing_content, True
        else:
            print("🧹 Post encontrado mas sem marcador do bot — modo: CRIAR DO ZERO.")
            return submission, existing_content, False
    else:
        print("🆕 Nenhum post encontrado para esta janela — vou criar um novo.")
        return None, "", False


def _has_bot_marker(content: str) -> bool:
    """True if the content carries any known PortoBotNews marker (current or legacy)."""
    return any(marker in content for marker in BOT_MARKERS_ACCEPTED)


def build_footer(repo_url: str, previous_post_url: str = "") -> str:
    """Constrói o rodapé de transparência com o link do GitHub + marcador do bot."""
    footer = (
        "\n\n---\n\n"
        "🤖 **PortoBotNews** — Bot open-source de transferências do FC Porto  \n"
        "*Gerado e atualizado automaticamente via Inteligência Artificial "
        "(LLM + DuckDuckGo) com pesquisa web.*\n\n"
        f"💻 [Código fonte no GitHub]({repo_url}) · ⚽ Para a comunidade r/FCPorto\n"
    )
    if previous_post_url:
        footer += f"\n📋 [Thread do mercado anterior]({previous_post_url})\n"
    footer += f"\n{BOT_MARKER}"
    return footer


def publish(reddit: praw.Reddit, content: str, submission, post_title: str,
            repo_url: str, previous_post_url: str = "") -> str:
    """
    Publica o conteúdo no Reddit.
    - Se submission for fornecido (post existente) → edita-o.
    - Se submission for None → cria um post novo com o título dinâmico.
    Devolve o ID do post (novo ou existente).
    """
    final_text = content + build_footer(repo_url, previous_post_url)

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
