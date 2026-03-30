#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

repo_root >/dev/null
ensure_branch "mac"
ensure_clean_worktree

git fetch origin

if ! git merge-base --is-ancestor origin/main HEAD; then
  die "mac does not yet include the latest origin/main. Run scripts/workflow/sync_mac_from_main.sh first."
fi

info "mac is ready to open a fresh release PR."
info "base=main"
info "head=mac"
info "Create a new PR from mac to main. Do not reuse an older mac->main PR."

if git show-ref --verify --quiet refs/remotes/origin/mac; then
  local_head="$(git rev-parse HEAD)"
  remote_mac="$(git rev-parse origin/mac)"
  if [[ "$local_head" != "$remote_mac" ]]; then
    info "Push mac before opening the PR so GitHub compares the current commit."
  fi
fi
