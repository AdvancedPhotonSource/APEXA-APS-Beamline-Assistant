@echo off
setlocal
REM APEXA CLI launcher for Windows. Double-click, or run in a terminal.
cd /d "%~dp0"
set NUMEXPR_MAX_THREADS=10
REM Let older CMake-based native deps (e.g. midas-index) build under CMake 4.x
set CMAKE_POLICY_VERSION_MINIMUM=3.5

if not exist ".env" (
    echo [!] .env not found. Run the Windows setup to create it:
    echo       powershell -ExecutionPolicy Bypass -File .\docs\setup_user.ps1
    echo     or copy .env.template to .env and set ANL_USERNAME by hand.
)

where uv >nul 2>nul
if errorlevel 1 (
    echo [!] uv not found on PATH. Install it and REOPEN this window:
    echo       winget install --id=astral-sh.uv -e
    echo     then run:  uv sync
    echo.
    python launch.py %*
) else (
    uv run python launch.py %*
)

REM Keep the window open if launch failed, so the error is readable on double-click.
if errorlevel 1 (
    echo.
    echo ============================================================
    echo APEXA exited with an error. Read the message above.
    echo Common fixes:  reopen terminal after installing uv  ^|  run "uv sync"
    echo ============================================================
    pause
)
