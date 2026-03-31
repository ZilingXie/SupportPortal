#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

repo_root >/dev/null
ensure_branch "mac"
ensure_clean_worktree

git fetch origin

if git merge-base --is-ancestor origin/main HEAD; then
  info "mac already contains origin/main."
  exit 0
fi

if ! git merge --no-edit origin/main; then
  die "Merging origin/main into mac produced conflicts. Resolve them in this mac worktree, verify again, then continue."
fi

info "Merged origin/main into mac."
