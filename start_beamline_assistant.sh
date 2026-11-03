#!/bin/bash
# APEXA Startup Script
# Advanced Photon EXperiment Assistant

# Add uv to PATH
export PATH="$HOME/.local/bin:$PATH"

# Prevent stale VIRTUAL_ENV from another project causing uv warnings
unset VIRTUAL_ENV

# Silence numexpr thread count banner
export NUMEXPR_MAX_THREADS=10

# Change to script directory
cd "$(dirname "$0")"

# Check servers.config
if [ ! -f "servers.config" ]; then
    echo "Error: servers.config not found!"
    exit 1
fi

# argo_mcp_client.py loads .env with python-dotenv, like the WebUI.
# Do not source configuration as shell code; setup writes dotenv quoting and
# expands paths itself. The selected LLM lives in APEXA_HOME/connection.json.

# Parse servers.config and build server argument list
SERVER_ARGS=""
while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ $line =~ ^([^:]+):(.+)$ ]]; then
        server_name="${BASH_REMATCH[1]}"
        server_path="${BASH_REMATCH[2]}"
        if [ -f "$server_path" ]; then
            SERVER_ARGS="$SERVER_ARGS ${server_name}:${server_path}"
        else
            echo "  ${server_name}: not found (${server_path})"
        fi
    fi
done < servers.config

if [ -z "$SERVER_ARGS" ]; then
    echo "Error: No valid servers found in servers.config"
    exit 1
fi

# Start APEXA
uv run argo_mcp_client.py $SERVER_ARGS
