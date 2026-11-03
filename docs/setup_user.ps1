# Same provider-first wizard as ./setup_user.sh, using native Windows Python.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $Python) {
    & $Python scripts/setup_user.py @args
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --locked python scripts/setup_user.py @args
} else {
    Write-Error "Install uv and run uv sync --locked, then rerun .\docs\setup_user.ps1."
    exit 1
}
exit $LASTEXITCODE
