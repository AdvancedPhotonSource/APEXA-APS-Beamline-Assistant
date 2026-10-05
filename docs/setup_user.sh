#!/bin/bash
# Beamline Assistant - User Setup Script
# Sets up configuration for a new user

set -e

echo "======================================================================="
echo "  Beamline Assistant - User Setup"
echo "======================================================================="
echo ""

# Check if .env already exists
if [ -f ".env" ]; then
    echo "⚠️  Warning: .env file already exists"
    read -p "Do you want to overwrite it? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Setup cancelled. Your existing .env was not modified."
        exit 0
    fi
fi

# Get ANL username
echo "Step 1: ANL Authentication"
echo "-------------------------"
read -p "Enter your ANL username: " ANL_USERNAME

if [ -z "$ANL_USERNAME" ]; then
    echo "Error: ANL username cannot be empty"
    exit 1
fi

# Select AI model
echo ""
echo "Step 2: AI Model Selection"
echo "-------------------------"
echo "Available models:"
echo "  1) claudeopus5  (Claude Opus 5 - newest, best planning/agentic - DEFAULT)"
echo "  2) gpt56sol     (GPT-5.6 frontier - reliable tool calling)"
echo "  3) gpt54        (GPT-5.4 - strong all-round, lower cost, 1M ctx)"
echo "  4) claudesonnet5 (Claude Sonnet 5 - newest Sonnet)"
echo ""
read -p "Select model [1]: " model_choice
model_choice=${model_choice:-1}

case $model_choice in
    1) ARGO_MODEL="claudeopus5" ;;
    2) ARGO_MODEL="gpt56sol" ;;
    3) ARGO_MODEL="gpt54" ;;
    4) ARGO_MODEL="claudesonnet5" ;;
    *) ARGO_MODEL="claudeopus5" ;;
esac

# MIDAS runtime
echo ""
echo "Step 3: MIDAS Runtime"
echo "-------------------------"
echo "APEXA routes to a NATIVE MIDAS install -- it does not bundle its own copy"
echo "(a bundled copy drifts from the operator's gate-checked env and voids runs)."
echo "The primary mechanism is APEXA_MIDAS_BIN = the pip midas-suite env's bin/."
echo ""
echo "Probing for a native MIDAS (midas-pipeline) ..."

# Auto-detect the pip midas-suite env's bin/ from midas-pipeline on PATH or in the
# common APS/dev locations. The bin dir is what APEXA_MIDAS_BIN must point at.
APEXA_MIDAS_BIN=""
_mp="$(command -v midas-pipeline 2>/dev/null || true)"
if [ -n "$_mp" ]; then
    APEXA_MIDAS_BIN="$(cd "$(dirname "$_mp")" && pwd)"
else
    for _cand in \
        /home/beams*/*/opt/envs/midas/bin \
        "$HOME"/opt/envs/midas/bin \
        "$HOME"/miniconda3/envs/midas*/bin \
        "$HOME"/anaconda3/envs/midas*/bin \
        /opt/conda/envs/midas*/bin ; do
        if [ -x "$_cand/midas-pipeline" ]; then
            APEXA_MIDAS_BIN="$_cand"
            break
        fi
    done
fi

if [ -n "$APEXA_MIDAS_BIN" ]; then
    echo "  ✓ native MIDAS found -> APEXA_MIDAS_BIN=$APEXA_MIDAS_BIN"
    echo "    Reconstruction / calibration / integration will route here."
else
    echo "  ⚠ no native MIDAS (midas-pipeline) found on this host."
    echo "    After setup, install the pip stack INTO APEXA's own .venv with:"
    echo "        uv sync --extra midas"
    echo "    (midas-suite is an opt-in extra, intentionally not bundled in base.)"
fi

# Optional: a MIDAS *repo clone* (MIDAS_PATH) -- needed ONLY for forward simulation
# (ForwardSimulationCompressed / simulateNF are C-only) and legacy C tools.
echo ""
read -p "Do you have a MIDAS C build / repo clone (for forward simulation)? (y/N): " -n 1 -r
echo

MIDAS_PATH=""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    read -p "Enter MIDAS clone path (MIDAS_PATH): " MIDAS_PATH
    MIDAS_PATH="${MIDAS_PATH/#\~/$HOME}"
    if [ ! -d "$MIDAS_PATH" ]; then
        echo "⚠️  Warning: Directory $MIDAS_PATH does not exist"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Setup cancelled."
            exit 0
        fi
    fi
fi

# Detect network reachability
echo ""
echo "Step 3: Network Environment"
echo "-------------------------"
echo "Probing what this host can reach (3s timeout each)..."

APEXA_NETWORK=$(python3 - <<'PYEOF'
import socket
def up(h, p=443, t=3.0):
    try:
        with socket.create_connection((h, p), timeout=t):
            return True
    except OSError:
        return False
web = up("huggingface.co")
anl = up("apps.inside.anl.gov") or up("inference-api.alcf.anl.gov")
print("web" if web else ("internal" if anl else "data"))
PYEOF
)

case "$APEXA_NETWORK" in
  web)
    echo "  ✓ public internet reachable  -> APEXA_NETWORK=web"
    echo "    All tools available, including Materials Project and DOI lookup."
    ;;
  internal)
    echo "  ✓ ANL internal reachable, no public internet -> APEXA_NETWORK=internal"
    echo "    Argo / ALCF / SSH / MIDAS all work. Web-only tools are disabled and"
    echo "    HuggingFace is forced offline, so startup cannot hang on a model"
    echo "    download (this is what stalled APEXA on copland)."
    echo ""
    echo "    IMPORTANT: pre-stage the RAG embedder cache on a web-connected"
    echo "    machine and copy ~/.cache/huggingface across, or query_hedm_knowledge"
    echo "    stays unavailable. See docs/OFFLINE_DEPLOYMENT.md."
    ;;
  *)
    echo "  ⚠ no network reachable -> APEXA_NETWORK=data"
    echo "    APEXA needs an LLM endpoint; check the VPN/host networking."
    ;;
esac

# Create .env file
echo ""
echo "Creating .env file..."

cat > .env << EOF
# Beamline Assistant Configuration
# Generated: $(date)

# ANL Authentication
ANL_USERNAME=$ANL_USERNAME

# AI Model
ARGO_MODEL=$ARGO_MODEL

# Network tier: web | internal | data  (detected at setup; edit if the host moves)
#   internal = ANL-only. Web-only tools are disabled and HuggingFace is forced
#   offline so the server cannot hang at startup on a model download.
APEXA_NETWORK=$APEXA_NETWORK

# MIDAS Runtime
EOF

# APEXA_MIDAS_BIN -- the native pip midas-suite env (primary route).
if [ -n "$APEXA_MIDAS_BIN" ]; then
    echo "APEXA_MIDAS_BIN=$APEXA_MIDAS_BIN" >> .env
else
    echo "# No native MIDAS detected. Either set APEXA_MIDAS_BIN to a midas-suite" >> .env
    echo "# env's bin/, or run 'uv sync --extra midas' to install it into .venv." >> .env
    echo "# APEXA_MIDAS_BIN=" >> .env
fi

# MIDAS_PATH -- a repo clone, only for forward simulation / legacy C tools.
if [ -n "$MIDAS_PATH" ]; then
    echo "MIDAS_PATH=$MIDAS_PATH" >> .env
else
    echo "# MIDAS_PATH (repo clone) will be auto-detected; needed only for forward sim." >> .env
fi

# Set secure permissions
chmod 600 .env

echo ""
echo "✓ Setup complete!"
echo ""
echo "Configuration saved to .env:"
echo "  - ANL Username: $ANL_USERNAME"
echo "  - AI Model: $ARGO_MODEL"
echo "  - Network tier: $APEXA_NETWORK"
if [ -n "$APEXA_MIDAS_BIN" ]; then
    echo "  - MIDAS runtime: native env ($APEXA_MIDAS_BIN)"
else
    echo "  - MIDAS runtime: none detected -> run 'uv sync --extra midas' (or set APEXA_MIDAS_BIN)"
fi
if [ -n "$MIDAS_PATH" ]; then
    echo "  - MIDAS clone (forward sim): $MIDAS_PATH"
else
    echo "  - MIDAS clone (forward sim): auto-detect (needed only for forward simulation)"
fi
echo ""
echo "File permissions set to 600 (owner read/write only)"
echo ""
echo "To start the assistant, run:"
echo "  ./start_beamline_assistant.sh"
echo ""
echo "======================================================================="
