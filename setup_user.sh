#!/bin/bash
# APEXA - User Setup Script
# Creates .env configuration for a new user

set -e

echo ""
echo "  APEXA - Advanced Photon EXperiment Assistant"
echo "  User Setup"
echo ""

# Check if .env already exists
# NOTE: use full-line reads (no `-n 1`) for every y/N prompt. `read -n 1` returns
# after one character WITHOUT consuming the Enter keystroke, and that leftover
# newline is then swallowed by the next full-line read — which silently blanked
# the MIDAS path prompt and made a typed "~/..." path leak into later fields.
if [ -f ".env" ]; then
    echo "  .env file already exists."
    read -p "  Overwrite? (y/N): " -r REPLY
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "  Setup cancelled."
        exit 0
    fi
fi

# Step 1: ANL username
echo "  1. ANL Username (domain username, not email)"
read -p "     Username: " ANL_USERNAME
if [ -z "$ANL_USERNAME" ]; then
    echo "  Error: username cannot be empty"
    exit 1
fi

# Step 2: AI Model
echo ""
echo "  2. Default AI Model"
echo "     1) gpt55          - GPT-5.5 (default)"
echo "     2) gpt56sol       - GPT-5.6 frontier"
echo "     3) gpt54          - GPT-5.4"
echo "     4) claudeopus5    - Claude Opus 5"
echo "     5) gpt4o          - GPT-4o"
echo "     6) claudesonnet5  - Claude Sonnet 5"
echo "     7) gemini35flash  - Gemini 3.5 Flash"
echo ""
echo "     Enter a number or type a model alias; run 'models' for the current gateway catalog."
read -p "     Select [1]: " model_choice
model_choice=${model_choice:-1}

case $model_choice in
    1) ARGO_MODEL="gpt55" ;;
    2) ARGO_MODEL="gpt56sol" ;;
    3) ARGO_MODEL="gpt54" ;;
    4) ARGO_MODEL="claudeopus5" ;;
    5) ARGO_MODEL="gpt4o" ;;
    6) ARGO_MODEL="claudesonnet5" ;;
    7) ARGO_MODEL="gemini35flash" ;;
    ""|*[!a-zA-Z0-9]*)
        # empty or contains punctuation → not a valid model token; fall back
        echo "     Unrecognized selection '$model_choice' — using default gpt55"
        ARGO_MODEL="gpt55" ;;
    *)
        # any other bare alphanumeric token → treat as a typed model name
        ARGO_MODEL="$model_choice"
        _KNOWN_MODELS=" gpt4o gpt54 gpt55 gpt56sol claudeopus5 claudesonnet5 gemini35flash "
        if [[ "$_KNOWN_MODELS" != *" $ARGO_MODEL "* ]]; then
            echo "     Note: '$ARGO_MODEL' is not in the known model list — saving it anyway."
        fi
        ;;
esac

# Step 3: MIDAS pip runtime (primary route)
echo ""
echo "  3. MIDAS Runtime"
echo "     APEXA uses the pip midas-suite environment when available."
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
    echo "     Found MIDAS tools in: $APEXA_MIDAS_BIN"
else
    echo "     No midas-pipeline found on PATH or in standard locations."
    read -p "     MIDAS environment bin path (Enter to skip): " -r APEXA_MIDAS_BIN
    APEXA_MIDAS_BIN="${APEXA_MIDAS_BIN/#\~/$HOME}"
fi
if [ -n "$APEXA_MIDAS_BIN" ] && [ ! -x "$APEXA_MIDAS_BIN/midas-pipeline" ]; then
    echo "     Note: $APEXA_MIDAS_BIN/midas-pipeline was not found; check this path in .env."
fi
if [ -z "$APEXA_MIDAS_BIN" ]; then
    echo "     You can later set APEXA_MIDAS_BIN or run: uv sync --extra midas"
fi

# MIDAS_PATH is a repository clone for forward simulation and legacy C tools,
# not the primary MIDAS runtime.
echo ""
read -p "     MIDAS repo clone for forward simulation / legacy tools? (y/N): " -r HAS_MIDAS_CLONE
MIDAS_PATH=""
if [[ "$HAS_MIDAS_CLONE" =~ ^[Yy]$ ]]; then
    read -p "     MIDAS clone path: " -r MIDAS_PATH
    MIDAS_PATH="${MIDAS_PATH/#\~/$HOME}"
    if [ -n "$MIDAS_PATH" ] && [ ! -d "$MIDAS_PATH" ]; then
        echo "     Note: $MIDAS_PATH does not exist yet — saved anyway; edit .env when available."
    fi
fi

# Step 4: Materials Project API key (optional)
echo ""
echo "  4. Materials Project API Key (optional, for CIF file fetching)"
echo "     Get one at: https://next-gen.materialsproject.org/api"
read -p "     API Key (Enter to skip): " MP_API_KEY

MP_LINE="# MP_API_KEY not set (optional, for fetch_cif_from_mp)"
if [ -n "$MP_API_KEY" ]; then
    MP_LINE="MP_API_KEY=$MP_API_KEY"
fi

# Write .env
cat > .env << EOF
ANL_USERNAME=$ANL_USERNAME
ARGO_MODEL=$ARGO_MODEL
$([ -n "$APEXA_MIDAS_BIN" ] && echo "APEXA_MIDAS_BIN=$APEXA_MIDAS_BIN" || echo "# APEXA_MIDAS_BIN=path/to/midas-suite/bin")
$([ -n "$MIDAS_PATH" ] && echo "MIDAS_PATH=$MIDAS_PATH" || echo "# MIDAS_PATH is only needed for forward simulation / legacy tools")
$MP_LINE
EOF

chmod 600 .env

# ── Off-site access (optional) ───────────────────────────────────────────────
# Beamline users increasingly need APEXA away from the facility, where the
# shared ANL service identity is unreachable. A personal key is stored in the OS
# KEYRING, never in .env: this file lives on shared storage, is world-readable
# until the chmod above, and gets copied between users during setup.
echo ""
echo "  Off-site access (optional)"
echo "     Away from ANL, APEXA can use an institutional gateway, your own API"
echo "     key, or a local model. It auto-detects which is reachable."
echo "     Note: a Claude Pro / ChatGPT Plus subscription is NOT API access —"
echo "     personal profiles need a pay-per-token key."
read -p "     Store a personal API key now? (y/N): " -r SETUP_KEY
if [[ "$SETUP_KEY" =~ ^[Yy] ]]; then
    echo "     Presets: anthropic  openai  gemini  custom (institutional)  mag"
    read -p "     Preset: " -r KEY_PRESET
    read -rsp "     API key (hidden): " KEY_VALUE
    echo ""
    if [ -n "$KEY_PRESET" ] && [ -n "$KEY_VALUE" ]; then
        APEXA_SETUP_KEY="$KEY_VALUE" APEXA_SETUP_PRESET="$KEY_PRESET" uv run python -c "
import os, sys
sys.path.insert(0, '.')
from apexa_llm_endpoints import PRESETS, keyring_set
p = os.environ['APEXA_SETUP_PRESET'].strip()
if p not in PRESETS:
    sys.exit(f'     Unknown preset {p!r} — nothing stored.')
print('     Stored in the OS keyring.' if keyring_set(p, os.environ['APEXA_SETUP_KEY'])
      else '     No keyring backend — export APEXA_LLM_API_KEY instead.')
" || echo "     Could not store the key; export APEXA_LLM_API_KEY instead."
    else
        echo "     Skipped (preset or key empty)."
    fi
    unset KEY_VALUE
fi

echo ""
echo "  Setup complete!"
echo "    Username: $ANL_USERNAME"
echo "    Model:    $ARGO_MODEL"
echo "    MIDAS tools: ${APEXA_MIDAS_BIN:-not detected}"
echo "    MIDAS clone: ${MIDAS_PATH:-not configured (optional)}"
echo ""
echo "  Start APEXA:"
echo "    ./start_beamline_assistant.sh    # CLI"
echo "    ./start_gradio_ui.sh             # Gradio UI"
echo ""
