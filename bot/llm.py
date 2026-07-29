"""
PortoBotNews — LLM interaction with multi-provider fallback.
"""
import os
import sys
import time

from openai import OpenAI, APIError, RateLimitError, APIConnectionError

from .constants import (
    LLM_PROVIDERS,
    OPENCODE_ZEN_BASE_URL,
    OPENCODE_ZEN_ENV_KEY,
    OPENCODE_ZEN_MODEL_PREFERENCE,
    OPENCODE_ZEN_FALLBACK_MODELS,
    LLM_RATE_LIMIT_DELAY,
    LLM_TIMEOUT,
)


def fetch_opencode_zen_models(api_key: str) -> list[str]:
    """Obtém a lista de modelos free disponíveis no OpenCode Zen via /models endpoint."""
    try:
        client = OpenAI(api_key=api_key, base_url=OPENCODE_ZEN_BASE_URL)
        response = client.models.list()
        all_models = [m.id for m in response.data]
        free_models = [
            m for m in all_models
            if "free" in m.lower() or m in OPENCODE_ZEN_MODEL_PREFERENCE
        ]
        if not free_models:
            print("   ⚠️  Nenhum modelo free encontrado no /models. A usar lista de fallback.")
            return OPENCODE_ZEN_FALLBACK_MODELS
        free_models.sort(
            key=lambda m: (-OPENCODE_ZEN_MODEL_PREFERENCE.get(m, 0), m)
        )
        return free_models
    except Exception as e:
        print(f"   ⚠️  Erro ao obter modelos do OpenCode Zen: {e}. A usar lista de fallback.")
        return OPENCODE_ZEN_FALLBACK_MODELS


def get_available_providers() -> list[dict]:
    """Devolve os providers configurados (com API key presente), por ordem de prioridade."""
    available = []

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

    for p in LLM_PROVIDERS:
        key = os.environ.get(p["env_key"], "").strip()
        if key and not key.startswith("tua_chave") and not key.startswith("your_key"):
            available.append({**p, "api_key": key})

    return available


def validate_provider(provider: dict) -> bool:
    """Faz uma chamada de teste mínima para validar a API key."""
    try:
        client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"])
        kwargs = {
            "model": provider["model"],
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 100,
            "timeout": 30,
        }
        if "deepseek" in provider["model"].lower():
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        client.chat.completions.create(**kwargs)
        return True
    except Exception:
        return False


def try_provider(provider: dict, prompt: str) -> str:
    """Tenta gerar conteúdo com um provider específico."""
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
        "max_tokens": 32000,
        "timeout": LLM_TIMEOUT,
    }

    response = client.chat.completions.create(**kwargs)

    content = response.choices[0].message.content
    if not content or not content.strip():
        raise ValueError("Resposta vazia do LLM.")
    return content.strip()


def generate_content(prompt: str, _skip_rate_limit: bool = False) -> str:
    """
    Gera conteúdo com o LLM, tentando providers por ordem de prioridade.
    Primeiro valida quais keys funcionam (chamada de teste mínima).
    Depois tenta gerar com o primário; se falhar, tenta o fallback.
    """
    providers = get_available_providers()
    if not providers:
        print("❌ Nenhuma API key de LLM configurada.")
        print("   Define pelo menos uma destas variáveis de ambiente:")
        print(f"     - {OPENCODE_ZEN_ENV_KEY} (OpenCode Zen — primário)")
        for p in LLM_PROVIDERS:
            print(f"     - {p['env_key']} ({p['name']})")
        sys.exit(1)

    # Validar keys
    print(f"🔑 A validar {len(providers)} provider(s) configurado(s)...")
    valid_providers = []
    validated_opencode = False

    for p in providers:
        if p["name"] == "OpenCode Zen" and validated_opencode:
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

    # Rate limiting between LLM calls
    if not _skip_rate_limit:
        time.sleep(LLM_RATE_LIMIT_DELAY)

    # Tentar gerar conteúdo, com fallback automático
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
