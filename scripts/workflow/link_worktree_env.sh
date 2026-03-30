#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

target_dir="${1:-}"
[[ -n "$target_dir" ]] || die "Usage: scripts/workflow/link_worktree_env.sh <worktree-path>"

root="$(repo_root)"
source_env="$root/.env"
[[ -f "$source_env" ]] || die "Root .env not found at $source_env. Create or restore it before linking a worktree."

[[ -d "$target_dir" ]] || die "Target worktree path does not exist: $target_dir"

target_env="$target_dir/.env"
if [[ -L "$target_env" ]]; then
  linked_target="$(readlink "$target_env")"
  if [[ "$linked_target" == "$source_env" ]]; then
    info "Worktree .env is already linked to $source_env."
    exit 0
  fi
  die "Target worktree already has a different .env symlink at $target_env."
fi

if [[ -e "$target_env" ]]; then
  die "Target worktree already has a concrete .env at $target_env. Remove it or move it aside first."
fi

ln -s "$source_env" "$target_env"
info "Linked $target_env -> $source_env"
