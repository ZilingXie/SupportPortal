#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

thread_name="${1:-}"
[[ -n "$thread_name" ]] || die "Usage: scripts/workflow/create_task_worktree.sh <thread-name-or-slug>"

ensure_root_workspace_on_main

git fetch origin
git pull --ff-only origin main

slug="$(slugify_thread_name "$thread_name")"
branch="codex/$slug"
worktree_base="$(resolve_worktree_base_dir)"
worktree_name="$(branch_to_worktree_name "$branch")"
worktree_path="$worktree_base/$worktree_name"
suffix=2

while git show-ref --verify --quiet "refs/heads/$branch" || [[ -e "$worktree_path" ]]; do
  branch="codex/${slug}-${suffix}"
  worktree_name="$(branch_to_worktree_name "$branch")"
  worktree_path="$worktree_base/$worktree_name"
  suffix=$((suffix + 1))
done

mkdir -p "$worktree_base"
git worktree add -b "$branch" "$worktree_path" main

ensure_branch "main"
ensure_clean_worktree

info "Created task branch $branch."
info "Worktree path: $worktree_path"
info "Root workspace remains on main."
