# APEXA - User Setup (Windows / PowerShell)
#   Run:  .\docs\setup_user.ps1
#   If blocked: powershell -ExecutionPolicy Bypass -File .\docs\setup_user.ps1
#
# Windows twin of docs/setup_user.sh. Native Windows has no bash, so Windows users
# run THIS. It writes .env in the repo root. On Windows there is no local native
# MIDAS to auto-detect (the C/CUDA stack has no Windows wheels), so instead of
# APEXA_MIDAS_BIN it offers to configure SSH-routing to a Linux analysis host
# (the normal beamline path) -- see docs/WINDOWS_SETUP.md.

$ErrorActionPreference = "Stop"

Write-Host "======================================================================="
Write-Host "  APEXA - User Setup (Windows)"
Write-Host "======================================================================="
Write-Host ""

# Run from the repo root (parent of docs/)
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$EnvPath = Join-Path $RepoRoot ".env"

if (Test-Path $EnvPath) {
    $ans = Read-Host "WARNING: .env already exists. Overwrite? (y/N)"
    if ($ans -notmatch '^[Yy]') { Write-Host "Setup cancelled; .env untouched."; exit 0 }
}

# Step 1: ANL username
Write-Host "Step 1: ANL Authentication"
Write-Host "-------------------------"
$AnlUser = Read-Host "Enter your ANL username"
if ([string]::IsNullOrWhiteSpace($AnlUser)) { Write-Host "Error: ANL username cannot be empty"; exit 1 }

# Step 2: model
Write-Host ""
Write-Host "Step 2: AI Model Selection"
Write-Host "-------------------------"
Write-Host "  1) claudeopus5   (newest Opus, best planning/agentic - DEFAULT)"
Write-Host "  2) gpt56sol      (GPT-5.6 frontier - reliable tool calling)"
Write-Host "  3) gpt54         (strong all-round, lower cost, 1M ctx)"
Write-Host "  4) claudesonnet5 (newest Sonnet)"
$mc = Read-Host "Select model [1]"
switch ($mc) {
    "2" { $ArgoModel = "gpt56sol" }
    "3" { $ArgoModel = "gpt54" }
    "4" { $ArgoModel = "claudesonnet5" }
    default { $ArgoModel = "claudeopus5" }
}

# Step 3: network reachability (bounded 3s TCP connects)
Write-Host ""
Write-Host "Step 3: Network Environment"
Write-Host "-------------------------"
Write-Host "Probing what this host can reach (3s timeout each)..."

function Test-Tcp([string]$HostName, [int]$Port = 443, [int]$TimeoutMs = 3000) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if ($ok -and $client.Connected) { $client.EndConnect($iar); $client.Close(); return $true }
        $client.Close(); return $false
    } catch { return $false }
}

$web = Test-Tcp "huggingface.co"
$anl = (Test-Tcp "apps.inside.anl.gov") -or (Test-Tcp "inference-api.alcf.anl.gov")
if     ($web) { $Network = "web" }
elseif ($anl) { $Network = "internal" }
else          { $Network = "data" }

switch ($Network) {
    "web"      { Write-Host "  OK  public internet reachable  -> APEXA_NETWORK=web" }
    "internal" {
        Write-Host "  OK  ANL internal reachable, no public internet -> APEXA_NETWORK=internal"
        Write-Host "      Argo / ALCF / SSH / MIDAS routing all work. Web-only tools disabled;"
        Write-Host "      HuggingFace forced offline so startup cannot hang on a model download."
        Write-Host "      Pre-stage the RAG embedder cache (see docs/OFFLINE_DEPLOYMENT.md)."
    }
    default    { Write-Host "  WARN no network reachable -> APEXA_NETWORK=data (check VPN/networking)" }
}

# Step 4: MIDAS runtime (no native MIDAS on Windows -> SSH-route to Linux, or WSL2)
Write-Host ""
Write-Host "Step 4: MIDAS Runtime (Windows)"
Write-Host "-------------------------"
Write-Host "MIDAS compute does not run natively on Windows. APEXA routes it to a Linux"
Write-Host "analysis host over SSH (recommended), or you can run it under WSL2."
Write-Host "See docs/WINDOWS_SETUP.md."
$AnalysisHost = ""
$DataRoots = ""
$ans = Read-Host "Configure SSH routing to a Linux analysis host now? (y/N)"
if ($ans -match '^[Yy]') {
    $AnalysisHost = Read-Host "  Analysis host (e.g. copland)"
    $DataRoots    = Read-Host "  Remote data root(s), ':'-separated (e.g. /gdata)"
    Write-Host "  Note: key-based SSH is required. See docs/WINDOWS_SETUP.md section 5A to set it up."
}

# Write .env
Write-Host ""
Write-Host "Creating .env ..."
$lines = @(
    "# APEXA Configuration (Windows)",
    "# Generated: $(Get-Date)",
    "",
    "# ANL Authentication",
    "ANL_USERNAME=$AnlUser",
    "",
    "# AI Model",
    "ARGO_MODEL=$ArgoModel",
    "",
    "# Network tier: web | internal | data  (detected at setup; edit if the host moves)",
    "APEXA_NETWORK=$Network",
    "",
    "# MIDAS runtime (Windows): no native MIDAS; route to a Linux host or use WSL2."
)
if ($AnalysisHost) {
    $lines += "APEXA_ANALYSIS_HOST=$AnalysisHost"
    if ($DataRoots) { $lines += "APEXA_REMOTE_DATA_ROOTS=$DataRoots" }
    $lines += "APEXA_REMOTE_MIDAS_ACTIVATE=conda deactivate && export PATH=/home/beams12/S1IDUSER/opt/envs/midas/bin:`$PATH"
} else {
    $lines += "# APEXA_ANALYSIS_HOST=copland         # set to SSH-route MIDAS to a Linux host"
    $lines += "# APEXA_REMOTE_DATA_ROOTS=/gdata      # ':'-separated remote data prefixes"
}
Set-Content -Path $EnvPath -Value $lines -Encoding UTF8

Write-Host ""
Write-Host "Done. Configuration saved to .env:"
Write-Host "  - ANL Username : $AnlUser"
Write-Host "  - AI Model     : $ArgoModel"
Write-Host "  - Network tier : $Network"
if ($AnalysisHost) { Write-Host "  - MIDAS route  : SSH -> $AnalysisHost" }
else               { Write-Host "  - MIDAS route  : not configured (edit .env or use WSL2)" }
Write-Host ""
Write-Host "Next:"
Write-Host "  uv sync"
Write-Host "  .\start_beamline_assistant.bat        (CLI)   or   .\start_web_viewer.bat   (Web UI)"
Write-Host "======================================================================="
