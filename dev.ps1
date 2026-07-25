# dev.ps1 - QuickVoice Windows dev launcher
# Bypasses the need for `task`, Docker, and bash for local demos.
#
# Usage:
#   .\dev.ps1                    # Show help
#   .\dev.ps1 env                # Create .env files from examples
#   .\dev.ps1 ai-setup           # Create Python venv + install requirements
#   .\dev.ps1 ai-api             # Run the AI FastAPI service (port 5555)
#   .\dev.ps1 ai-worker          # Run the LiveKit AI worker
#   .\dev.ps1 test-langfuse      # Run the Langfuse handler unit tests

param([string]$Command = "help")

$ROOT = $PSScriptRoot
$AI_DIR = Join-Path $ROOT "apps\ai"

function Write-Info  { param($msg) Write-Host "[info]  $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "[ok]    $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[warn]  $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "[fail]  $msg" -ForegroundColor Red }

function Invoke-EnvSetup {
    $pairs = @(
        @{ src = ".env.dev.example";               dst = ".env.dev" }
        @{ src = "apps\server\.env.dev.example";   dst = "apps\server\.env.dev" }
        @{ src = "apps\ai\.env.dev.example";        dst = "apps\ai\.env.dev" }
        @{ src = "apps\console\.env.dev.example";  dst = "apps\console\.env.local" }
        @{ src = "apps\web\.env.dev.example";       dst = "apps\web\.env.local" }
    )
    foreach ($p in $pairs) {
        $src = Join-Path $ROOT $p.src
        $dst = Join-Path $ROOT $p.dst
        if (Test-Path $dst) {
            Write-Info "kept    $($p.dst)"
        } elseif (Test-Path $src) {
            Copy-Item $src $dst
            Write-Ok   "created $($p.dst)"
        } else {
            Write-Warn "missing template: $($p.src)"
        }
    }
    Write-Host ""
    Write-Ok "Env files ready. Edit apps\ai\.env.dev to add your API keys and Langfuse credentials."
}

function Invoke-AISetup {
    $venv = Join-Path $AI_DIR ".venv"
    if (-not (Test-Path $venv)) {
        Write-Info "Creating Python venv in apps\ai\.venv ..."
        & python -m venv $venv
        Write-Ok "venv created"
    } else {
        Write-Info "venv already exists -- skipping creation"
    }
    $pip = Join-Path $venv "Scripts\pip.exe"
    $req = Join-Path $AI_DIR "requirements.txt"
    Write-Info "Installing requirements (including langfuse) ..."
    & $pip install --upgrade pip --quiet
    & $pip install -r $req
    Write-Ok "AI requirements installed"
}

function Invoke-AIApi {
    $venv    = Join-Path $AI_DIR ".venv"
    $python  = Join-Path $venv "Scripts\python.exe"
    $envFile = Join-Path $AI_DIR ".env.dev"
    $mainPy  = Join-Path $AI_DIR "main.py"

    if (-not (Test-Path $python)) { Write-Fail "Run: .\dev.ps1 ai-setup first"; exit 1 }
    if (-not (Test-Path $envFile)) { Write-Fail "Run: .\dev.ps1 env first"; exit 1 }

    Get-Content $envFile | Where-Object { $_ -match "^\s*[^#\s].*=.*" } | ForEach-Object {
        $parts = $_ -split "=", 2
        if ($parts.Count -eq 2) {
            [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim())
        }
    }

    $aiPort = if ($env:AI_API_PORT) { $env:AI_API_PORT } else { "5555" }
    $lf = if ($env:LANGFUSE_PUBLIC_KEY) { "ENABLED (traces will appear in Langfuse dashboard)" } else { "disabled (set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY to enable)" }
    Write-Ok "Starting QuickVoice AI FastAPI on port $aiPort ..."
    Write-Info "Langfuse: $lf"
    Write-Host ""
    Set-Location $AI_DIR
    & $python $mainPy api
}

function Invoke-AIWorker {
    $venv    = Join-Path $AI_DIR ".venv"
    $python  = Join-Path $venv "Scripts\python.exe"
    $envFile = Join-Path $AI_DIR ".env.dev"
    $mainPy  = Join-Path $AI_DIR "main.py"

    if (-not (Test-Path $python)) { Write-Fail "Run: .\dev.ps1 ai-setup first"; exit 1 }
    Get-Content $envFile | Where-Object { $_ -match "^\s*[^#\s].*=.*" } | ForEach-Object {
        $parts = $_ -split "=", 2
        if ($parts.Count -eq 2) { [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim()) }
    }
    Write-Ok "Starting QuickVoice LiveKit AI worker ..."
    Set-Location $AI_DIR
    & $python $mainPy dev
}

function Invoke-TestLangfuse {
    $venv   = Join-Path $AI_DIR ".venv"
    $python = Join-Path $venv "Scripts\python.exe"
    if (-not (Test-Path $python)) { Write-Fail "Run: .\dev.ps1 ai-setup first"; exit 1 }
    Write-Info "Running Langfuse handler unit tests ..."
    Set-Location $AI_DIR
    & $python -m unittest tests/test_langfuse_handler.py tests/test_finalization_handler.py -v
}

function Show-Help {
    Write-Host @"

QuickVoice Windows Dev Launcher  (dev.ps1)
==========================================

Commands:
  env              Create .env files from .env.dev.example templates
  ai-setup         Create Python venv + install all requirements (incl. langfuse)
  ai-api           Start the AI FastAPI service -> http://localhost:5555/health
  ai-worker        Start the LiveKit AI voice worker
  test-langfuse    Run the Langfuse handler unit tests (29 tests)

Typical first-time setup for Langfuse demo:
  .\dev.ps1 env
  .\dev.ps1 ai-setup
  # Edit apps\ai\.env.dev  ->  add LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY
  .\dev.ps1 test-langfuse
  .\dev.ps1 ai-api

"@
}

switch ($Command.ToLower()) {
    "env"            { Invoke-EnvSetup }
    "ai-setup"       { Invoke-AISetup }
    "ai-api"         { Invoke-AIApi }
    "ai-worker"      { Invoke-AIWorker }
    "test-langfuse"  { Invoke-TestLangfuse }
    default          { Show-Help }
}
