#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"
# shellcheck source=./_local_db_env.sh
source "$SCRIPT_DIR/_local_db_env.sh"

root_path="$(repo_root)"
load_supportportal_local_env "$root_path" "optional"
export_supportportal_local_host_db_env

if [[ "${1:-}" == "--" ]]; then
  shift
fi

[[ "$#" -gt 0 ]] || die "Usage: scripts/workflow/run_with_local_db_env.sh -- <command> [args...]"

exec "$@"
