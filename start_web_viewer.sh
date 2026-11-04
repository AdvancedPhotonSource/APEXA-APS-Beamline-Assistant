#!/bin/bash
# Start Web Server with Image Viewer
# Uses existing .venv managed by UV
set -euo pipefail

cd "$(dirname "$0")"

echo "🔬 Starting Beamline Assistant Web Server + Image Viewer"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo "❌ Error: .venv not found!"
    echo "   Run: uv sync"
    exit 1
fi

echo "✓ Using virtual environment: .venv"

# Build React frontend if dist is missing or stale
if [ -d "frontend" ]; then
    # Any source file newer than the bundle, not two hard-coded ones. The old
    # check watched only VizLauncher.tsx and VizPanel.tsx, so editing or adding
    # any other component silently launched the STALE bundle and the change
    # appeared not to work. start_desktop_ui.sh already does it this way.
    NEED_BUILD=0
    if [ ! -f "frontend/dist/index.html" ]; then
        NEED_BUILD=1
    elif [ -n "$(find frontend/src frontend/public -type f -newer frontend/dist/index.html -print -quit)" ]; then
        NEED_BUILD=1
    fi
    for config in frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/tsconfig*.json frontend/index.html; do
        if [ "$config" -nt frontend/dist/index.html ]; then NEED_BUILD=1; fi
    done
    if [ "$NEED_BUILD" = "1" ]; then
        if [ -d frontend/node_modules ]; then
            echo ""
            echo "🔨 Building React frontend..."
            (cd frontend && npm run build)
            echo "✓ Frontend built"
        elif [ -f frontend/dist/index.html ]; then
            # Can't rebuild (no npm deps), but a committed bundle is present. The
            # shipped frontend/dist is self-contained, so SERVE IT rather than
            # failing — this is the documented no-Node deploy path. (NEED_BUILD here
            # is just the mtime heuristic firing: git checkout does not preserve
            # mtimes, so a source file routinely looks "newer" than dist/index.html
            # on a fresh clone/pull even though the bundle is current.)
            echo "ℹ Using the committed frontend/dist (npm deps absent — not rebuilding)."
            echo "  To refresh it later: run 'npm ci && npm run build' in frontend/ on a connected host."
        else
            echo "No prebuilt frontend/dist and npm build deps are absent."
            echo "Stage deps with 'npm ci' in frontend/ on a connected host, or deploy a prebuilt frontend/dist."
            exit 1
        fi
    fi
fi

echo ""
echo "🚀 Starting server..."
echo "   Web UI:         http://localhost:8001"
echo "   MIDAS Results:  Sidebar → Results → Scan a directory"
echo "   Image Viewer:   /api/viewer/* endpoints"
echo "   Viz API:        /api/viz/* endpoints"
echo ""
echo "Press Ctrl+C to stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Run with venv Python directly
exec .venv/bin/python3 web_server.py
