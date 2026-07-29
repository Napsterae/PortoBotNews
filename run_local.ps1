<#
.SYNOPSIS
  PortoBotNews - script local de teste.
  Verifica dependencias, instala se faltar, e corre o bot em modo preview (dry-run).

.USAGE
  .\run_local.ps1              # instala deps (se faltar) + corre preview
  .\run_local.ps1 -NoInstall  # nao instala/atualiza deps, so corre o preview
  .\run_local.ps1 -Live       # corre a serio (publica no Reddit) em vez de preview
#>

param(
  [switch]$NoInstall,
  [switch]$Live,
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Die($msg)        { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

# --- 1. Verificar Python ---
Write-Step "Verificar Python"
try {
  $pyVersion = (python --version 2>&1).ToString()
  Write-Ok "Python encontrado: $pyVersion"
} catch {
  Die "Python nao encontrado. Instala Python 3.11+ e adiciona ao PATH."
}

# --- 2. Criar venv se nao existir ---
$venvPath = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  Write-Step "Criar virtualenv (.venv)"
  python -m venv "$venvPath"
  if (-not $?) { Die "Falha ao criar o virtualenv." }
  Write-Ok "Virtualenv criado."
} else {
  Write-Ok "Virtualenv ja existe."
}

# --- 3. Instalar / atualizar dependencias ---
if (-not $NoInstall) {
  Write-Step "Instalar dependencias (requirements.txt)"
  & $venvPython -m pip install --upgrade pip --quiet
  & $venvPython -m pip install -r (Join-Path $repoRoot "requirements.txt") --quiet
  if (-not $?) { Die "Falha ao instalar dependencias. Corre: .venv\Scripts\python -m pip install -r requirements.txt" }
  Write-Ok "Dependencias instaladas."
} else {
  Write-Warn "A saltar instalacao de dependencias (-NoInstall)."
}

# --- 4. Verificar .env e pelo menos uma API key de LLM ---
Write-Step "Verificar configuracao"
$envFile = Join-Path $repoRoot ".env"
if (-not (Test-Path $envFile)) {
  Copy-Item (Join-Path $repoRoot ".env.example") $envFile
  Write-Warn ".env criado a partir do .env.example. Edita-o e mete pelo menos uma API key de LLM."
  Die "Edita o ficheiro .env com pelo menos uma das: OPENCODE_API_KEY, DEEPSEEK_API_KEY, ou GROQ_API_KEY."
}

# Carregar .env para verificar as chaves (leitura simples)
$envContent = Get-Content $envFile -Encoding UTF8

# Verificar qual(is) provider(s) de LLM esta(o) configurado(s)
$llmKeys = @(
  @{ Name = "OpenCode Zen"; EnvKey = "OPENCODE_API_KEY" },
  @{ Name = "DeepSeek";     EnvKey = "DEEPSEEK_API_KEY" },
  @{ Name = "Groq";         EnvKey = "GROQ_API_KEY" }
)

$foundProviders = @()
foreach ($provider in $llmKeys) {
  $keyValue = ($envContent | Where-Object { $_ -match "^$($provider.EnvKey)\s*=\s*(.+)$" } |
               ForEach-Object { $Matches[1].Trim() } | Select-Object -First 1)
  if ($keyValue -and $keyValue -notlike "*tua_chave*" -and $keyValue -notlike "*your_key*" -and $keyValue -ne "") {
    $foundProviders += $provider.Name
    Write-Ok "$($provider.Name) key encontrada no .env ($($provider.EnvKey))."
  }
}

if ($foundProviders.Count -eq 0) {
  Write-Host ""
  Write-Host "Nenhuma API key de LLM configurada no .env." -ForegroundColor Red
  Write-Host "Configura pelo menos uma destas:" -ForegroundColor Yellow
  Write-Host "  - OPENCODE_API_KEY  (OpenCode Zen - primario)    -> https://opencode.ai/zen"
  Write-Host "  - DEEPSEEK_API_KEY  (DeepSeek - fallback 1)      -> https://platform.deepseek.com"
  Write-Host "  - GROQ_API_KEY      (Groq - fallback 2, free)    -> https://console.groq.com/keys"
  Write-Host ""
  Die "Edita o .env com pelo menos uma API key de LLM e volta a correr este script."
}

Write-Ok "Providers configurados: $($foundProviders -join ', ')"

# --- 5. Correr testes (se não for live) ---
if (-not $Live) {
  Write-Step "Correr testes unitários"
  & $venvPython -m pytest tests/ -v
  if (-not $?) { Write-Warn "Alguns testes falharam — a continuar mesmo assim." }
}

# --- 6. Correr o bot ---
if ($Live) {
  Write-Step "Correr o bot (LIVE - vai publicar no Reddit)"
  $cmdArgs = @("main.py")
  if ($Force) { $cmdArgs += "--force" }
} else {
  Write-Step "Correr o bot (PREVIEW / dry-run - nao publica)"
  $cmdArgs = @("main.py", "--dry-run")
  if ($Force) { $cmdArgs += "--force" }
}

& $venvPython @cmdArgs
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
  if (-not $Live) {
    $preview = Join-Path $repoRoot "preview.md"
    Write-Host "`n--- preview.md ---" -ForegroundColor Magenta
    if (Test-Path $preview) { Get-Content $preview -Encoding UTF8 }
    Write-Host "`n[OK] Preview gerado em: $preview" -ForegroundColor Green
  }
  Write-Ok "Concluido."
} else {
  Die "O bot terminou com erro (exit code $exitCode)."
}
