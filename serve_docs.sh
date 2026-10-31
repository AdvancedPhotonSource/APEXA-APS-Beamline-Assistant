#!/bin/bash
# Serve APEXA documentation locally with live reload
# Access at http://localhost:8000

# Add uv to PATH
export PATH="$HOME/.local/bin:$PATH"

cd "$(dirname "$0")"

echo "======================================================================="
echo "  APEXA Documentation Server"
echo "======================================================================="
echo ""
echo "Starting documentation server..."
echo "Access the docs at: http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop the server."
echo ""

# Keep documentation dependencies out of APEXA's runtime environment. uv resolves
# the docs-only MkDocs Material package on first use and reuses its cache later.
uv tool run --with mkdocs-material mkdocs serve
