#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '%s\n' "Compatibility wrapper: restart_single_host_lightweight_stack.sh now delegates to restart_single_host_stack.sh --mode local_lightweight." >&1
exec bash "$SCRIPT_DIR/restart_single_host_stack.sh" "$@" --mode local_lightweight
