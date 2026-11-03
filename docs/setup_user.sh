#!/bin/bash
# Compatibility entry point; keep all setup behavior in the shared wizard.
set -euo pipefail
exec bash "$(dirname "$0")/../setup_user.sh" "$@"
