# PortoBotNews

Bot open-source que mantém uma thread no Reddit (r/FCPorto) sempre atualizada com as últimas notícias e rumores de transferências do FC Porto, em todas as modalidades.

O bot pesquisa a web com o **DuckDuckGo** e processa os resultados com um **LLM gratuito** (OpenCode Zen, DeepSeek ou Groq — com fallback automático), organiza a informação em tabelas de Markdown bonitas para o Reddit, classifica a confiança dos rumores consoante as fontes, e atualiza (ou cria) o post automaticamente — com um link para este repositório no rodapé, para total transparência.

Corre automaticamente no **GitHub Actions** durante os períodos de mercado aberto (verão e inverno), sem servidor e 100% gratuito.

---

## Como funciona

1. **Pesquisa web** com o DuckDuckGo — obtém artigos reais de transferências de todas as fontes listadas em `sources/trustworthy.md` e `sources/sketchy.md` (queries dinâmicas com `site:` por cada domínio).
2. **Geração de conteúdo** com LLM — os artigos encontrados são passados a um LLM (OpenCode Zen → DeepSeek → Groq, com fallback automático) que os organiza em tabelas Markdown com ícones, ranking de confiança e fontes hiperlinkadas.
3. **Atualização do Reddit** — o bot autentica-se no Reddit, vai buscar o conteúdo atual da thread e decide:
   - **Se a thread já tiver sido atualizada pelo bot** → pede ao LLM que **atualize** o conteúdo existente (preserva rumores válidos, atualiza estados, adiciona novos, remove rumores caducados).
   - **Se a thread ainda for nova / "lixo" / nunca atualizada pelo bot** → pede ao LLM que **crie o conteúdo do zero**.
4. **Publicação** — anexa um rodapé com o link do GitHub (transparência) e publica/edita o post.

> Cada rumor inclui uma coluna **Fontes** com hiperlinks reais para os artigos (múltiplas fontes quando disponíveis), e uma coluna **Confiança** (🟢 Alta / 🟡 Média / 🔴 Baixa) baseada no tier das fontes e no número de fontes.

## Estrutura do projeto

```
PortoBotNews/
├── .github/workflows/tracker.yml   # Pipeline do GitHub Actions (agenda + manual)
├── prompts/
│   └── transfer_news.md            # Prompt do LLM (personalizável)
├── sources/
│   ├── trustworthy.md              # Sites fiáveis (Tier 1) — gera queries site:
│   └── sketchy.md                  # Sites menos fiáveis (Tier 2) — gera queries site:
├── main.py                         # Lógica do bot
├── run_local.ps1                   # Script local de teste (Windows)
├── requirements.txt                # Dependências Python
├── .env.example                    # Modelo das variáveis de ambiente
├── .gitignore                      # Protege .env e preview.md
├── LICENSE                         # MIT
└── README.md
```

## Configurar os segredos no GitHub

Como o bot corre diretamente no GitHub Actions, as credenciais reais ficam guardadas nos **GitHub Secrets** (nunca no código). Em **Settings → Secrets and variables → Actions → New repository secret**, adiciona:

### LLM Providers (pelo menos um obrigatório)

| Secret | Descrição |
|--------|-----------|
| `OPENCODE_API_KEY` | Chave da API do OpenCode Zen (primário — modelos free com subscrição ativa) — [obter aqui](https://opencode.ai/zen) |
| `DEEPSEEK_API_KEY` | Chave da API do DeepSeek (fallback 1 — 5M tokens grátis) — [obter aqui](https://platform.deepseek.com) |
| `GROQ_API_KEY` | Chave da API do Groq (fallback 2 — free forever, sem credit card) — [obter aqui](https://console.groq.com/keys) |

> Configura pelo menos um. Se configurares vários, o bot tenta por ordem de prioridade e faz fallback automático se um falhar (rate limit, erro, etc.).

### Reddit (obrigatórios para publicar)

| Secret | Descrição |
|--------|-----------|
| `REDDIT_CLIENT_ID` | Client ID da app "script" criada no Reddit — [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) |
| `REDDIT_CLIENT_SECRET` | Client secret da app Reddit |
| `REDDIT_USERNAME` | Username da conta do bot no Reddit |
| `REDDIT_PASSWORD` | Password da conta do bot (⚠️ a conta **não pode ter 2FA** com este fluxo; usa uma conta dedicada ao bot) |
| `REDDIT_POST_ID` | ID da thread a editar (ex.: `1abc2d`). **Opcional** — se vazio, o bot procura automaticamente nos seus posts por um com o título correspondente à janela atual (ex: "🐉 FC Porto — Mercado de Verão 2026"). Se não encontrar, cria um novo. |

> `GITHUB_REPO_URL` é injetado automaticamente pelo workflow (não é preciso configurar).

### Criar a app no Reddit
1. Vai a [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) → **create another app**.
2. Tipo: **script**. Redirect URI: `http://localhost`.
3. Copia o **client ID** (por baixo do nome) e o **secret**.

## LLM Providers — fallback automático

O bot suporta **três providers de LLM** com fallback automático. Basta configurar pelo menos um:

| Prioridade | Provider | Secret | Custo | Como obter |
|------------|----------|--------|-------|------------|
| 1º (primário) | **OpenCode Zen** | `OPENCODE_API_KEY` | Modelos free (requer subscrição ativa) | [opencode.ai/zen](https://opencode.ai/zen) |
| 2º (fallback) | **DeepSeek** | `DEEPSEEK_API_KEY` | 5M tokens grátis, depois pay-as-you-go | [platform.deepseek.com](https://platform.deepseek.com) |
| 3º (fallback) | **Groq** | `GROQ_API_KEY` | Free forever, sem credit card | [console.groq.com/keys](https://console.groq.com/keys) |

**Como funciona:**
- O bot obtém dinamicamente os modelos free disponíveis no OpenCode Zen a cada execução (via endpoint `/models`), ranqueia-os por preferência e usa o melhor.
- Se o OpenCode Zen falhar (rate limit, erro, modelo indisponível), tenta automaticamente o **DeepSeek**.
- Se o DeepSeek também falhar, tenta o **Groq** como último recurso.
- Antes de gerar conteúdo, o bot **valida cada API key** com uma chamada de teste mínima — keys inválidas são saltadas automaticamente.
- Se nenhum provider estiver configurado ou válido, o bot aborta com mensagem de erro clara.

> Para adicionar uma key no GitHub: **Settings → Secrets → Actions → New repository secret**. Podes configurar apenas um ou todos — o bot adapta-se automaticamente.

## Agenda — janelas de transferências

O bot só corre durante os períodos de mercado aberto, para não desperdiçar recursos fora da época de transferências:

| Período | Ativo de | Ativo até | Motivo |
|---------|----------|-----------|--------|
| **Mercado de Verão** | 1 de junho | 11 de setembro | 1 mês antes da abertura (1 jul) + 1 semana após o fecho (~4 set) |
| **Mercado de Inverno** | 4 de dezembro | 8 de fevereiro | 1 mês antes da abertura (~4 jan) + 1 semana após o fecho (~1 fev) |

- Durante a janela ativa: corre **2x por dia** (12:00 e 20:00 UTC).
- Fora da janela: as runs agendadas abortam automaticamente no primeiro step (sem chamadas à API, sem custos).
- **Runs manuais** (`workflow_dispatch`) ignoram a verificação de data — podes correr quando quiseres.
- Para forçar uma run agendada fora da janela, usa o input **"Forçar execução"** no workflow manual.

> Para ajustar as datas, edita as variáveis no step "Verificar janela de transferências" em [`.github/workflows/tracker.yml`](.github/workflows/tracker.yml).

## Primeira execução

O bot é **totalmente autónomo** — não precisas de criar o post manualmente nem de configurar o `REDDIT_POST_ID`:

1. Configura os segredos no GitHub (pelo menos uma API key de LLM + as credenciais do Reddit).
2. Na primeira execução de cada janela de transferências (ex: Mercado de Verão 2026), o bot **procura nos seus próprios posts** por um com o título `🐉 FC Porto — Mercado de Verão 2026`.
3. Como não encontra (é a primeira vez), **cria um novo post** com esse título.
4. Nas execuções seguintes, o bot **encontra o post pelo título** e **edita-o** (atualiza o conteúdo).
5. Quando muda a janela (ex: passa de Verão 2026 para Inverno 2026/27), o título muda → o bot não encontra post com o novo título → cria um novo. O post antigo fica intacto.

> O título é **determinístico** (gerado por código a partir da data, não pelo LLM), pelo que é sempre o mesmo para a mesma janela. Isto garante que o bot encontra sempre o post correto sem risco de criar duplicados.

> Se quiseres forçar o uso de um post específico (override), define `REDDIT_POST_ID` — o bot usa esse ID diretamente em vez de procurar por título.

## Testar localmente

### Windows (script automático)
```powershell
.\run_local.ps1              # instala deps (se faltar) + corre preview
.\run_local.ps1 -NoInstall   # só corre o preview (deps já instaladas)
.\run_local.ps1 -Live        # publica a sério no Reddit (não usa dry-run)
```

O script:
- Verifica o Python e cria o virtualenv se faltar.
- Instala as dependências do `requirements.txt`.
- Verifica se pelo menos uma API key de LLM está configurada no `.env`.
- Corre o bot em modo **preview** (dry-run) — gera o conteúdo mas **não publica** no Reddit.
- Escreve o resultado em `preview.md` para revisão.
- Em runs subsequentes, se o `preview.md` anterior tiver o marcador do bot, corre em modo **ATUALIZAR** (simula o workflow real de update do post).

### Manual (qualquer OS)
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
cp .env.example .env            # preenche as variáveis
python main.py --dry-run        # preview (não publica)
python main.py                  # publicar a sério
```

### No GitHub Actions (dry-run)
Vai a **Actions → "Bot Mercado FCPorto" → Run workflow** → tick **"Modo preview"** → Run.
- Não publica no Reddit.
- Gera `preview.md` como artifact descarregável (7 dias).

## Personalizar

- **Prompt:** edita [`prompts/transfer_news.md`](prompts/transfer_news.md) (mantém os tokens `{{...}}`).
- **Fontes / ranking:** edita [`sources/trustworthy.md`](sources/trustworthy.md) e [`sources/sketchy.md`](sources/sketchy.md). Os domínios listados geram automaticamente queries `site:` no DuckDuckGo — adicionar ou remover um site aqui muda a pesquisa sem tocar no código.
- **Agenda:** edita o `cron` em [`.github/workflows/tracker.yml`](.github/workflows/tracker.yml).
- **Datas das janelas:** edita as variáveis no step "Verificar janela de transferências" no workflow.
- **Queries de pesquisa:** edita a função `build_search_queries()` em [`main.py`](main.py).

## Transparência

Cada post gerado inclui no rodapé um link para este repositório, indicando que o conteúdo foi gerado automaticamente por IA. Isto é uma boa prática para bots no Reddit e mantém a comunidade informada.

---

*Projeto open-source feito por um Portista. 🐉*
