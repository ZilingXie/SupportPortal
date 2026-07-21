#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

info "Compatibility wrapper: restart_single_host_local_stack.sh delegates to restart_single_host_stack.sh --mode local_lightweight --db local."
exec bash "$SCRIPT_DIR/restart_single_host_stack.sh" "$@" --mode local_lightweight --db local
