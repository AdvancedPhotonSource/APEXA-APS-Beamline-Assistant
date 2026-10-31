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
        echo ""
        echo "🔨 Building React frontend..."
        if [ ! -d frontend/node_modules ]; then
            echo "Frontend build dependencies are absent. Stage them with npm ci on a connected setup host, or deploy a prebuilt frontend/dist."
            exit 1
        fi
        (cd frontend && npm run build)
        echo "✓ Frontend built"
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
