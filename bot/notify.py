"""
PortoBotNews — error notifications via GitHub Issues.
"""
import json
import os
import sys
import urllib.parse
import urllib.request


NOTIFY_LABEL = "automated"
NOTIFY_TITLE_PREFIX = "🤖 PortoBotNews — Falha na execução"


def _gh_request(url: str, method: str = "GET", token: str = "", payload: dict | None = None):
    """Thin helper around urllib for the GitHub REST API."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=10)


def _find_open_automation_issue(repo: str, token: str):
    """Return the first open issue carrying our automation label, or None.

    We search (labels-filtered) rather than paging all issues so a busy repo
    doesn't cost us a full scan on every failure."""
    try:
        q = f'repo:{repo} is:issue is:open label:"{NOTIFY_LABEL}"'
        url = f"https://api.github.com/search/issues?q={urllib.parse.quote(q)}"
        with _gh_request(url, token=token) as resp:
            if resp.status != 200:
                return None
            items = json.loads(resp.read().decode()).get("items", [])
            return items[0] if items else None
    except Exception:
        return None


def notify_failure(error_message: str, run_url: str = "") -> None:
    """
    Surface a bot failure as a GitHub Issue.

    To avoid a flood of duplicate issues on recurring failures, we reuse the
    first already-open issue tagged with our automation label: we append the new
    error as a comment instead of creating yet another issue. A new issue is only
    opened when there is no open automation issue.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()

    if not token or not repo:
        print(f"⚠️  Notificação de falha (sem GitHub Actions): {error_message}", file=sys.stderr)
        return

    body = f"## Erro\n\n```\n{error_message}\n```\n"
    if run_url:
        body += f"\n## Run\n\n{run_url}\n"
    body += "\n---\n*Registado automaticamente pelo PortoBotNews*"

    try:
        existing = _find_open_automation_issue(repo, token)
        if existing:
            number = existing["number"]
            url = f"https://api.github.com/repos/{repo}/issues/{number}/comments"
            with _gh_request(url, method="POST", token=token,
                             payload={"body": body}) as resp:
                if resp.status == 201:
                    print(f"📋 Comentário adicionado ao issue #{number} (falha recorrente).")
                else:
                    print(f"⚠️  Falha ao comentar issue #{number} (HTTP {resp.status})",
                          file=sys.stderr)
            return

        url = f"https://api.github.com/repos/{repo}/issues"
        with _gh_request(url, method="POST", token=token,
                         payload={
                             "title": NOTIFY_TITLE_PREFIX,
                             "body": body,
                             "labels": ["bug", NOTIFY_LABEL],
                         }) as resp:
            if resp.status == 201:
                created = json.loads(resp.read().decode())
                print(f"📋 Issue #{created.get('number')} criada no GitHub para notificar a falha.")
            else:
                print(f"⚠️  Falha ao criar issue (HTTP {resp.status})", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  Falha ao reportar notificação: {e}", file=sys.stderr)
