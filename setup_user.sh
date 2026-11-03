#!/bin/bash
# One provider-first setup wizard for Linux, macOS and Windows (via Python).
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python scripts/setup_user.py "$@"
elif command -v uv >/dev/null 2>&1; then
    exec uv run --locked python scripts/setup_user.py "$@"
else
    echo "Install uv and run uv sync --locked, then rerun ./setup_user.sh."
    exit 1
fi
