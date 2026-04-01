#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

target_branch="${1:-}"
[[ -n "$target_branch" ]] || die "Usage: scripts/workflow/rehome_task_worktree.sh <codex-branch>"
[[ "$target_branch" == codex/* ]] || die "rehome_task_worktree.sh only supports codex/* branches. Got: $target_branch"

ensure_repo_root_cwd
ensure_branch "$target_branch"

unmerged_paths="$(git diff --name-only --diff-filter=U)"
if [[ -n "$unmerged_paths" ]]; then
  {
    printf 'The root workspace has unmerged paths. Resolve them before rehoming this task branch:\n'
    printf ' - %s\n' "$unmerged_paths"
  } >&2
  exit 1
fi

status_output="$(git status --porcelain=v1 --untracked-files=all)"
stash_ref=""

if [[ -n "$status_output" ]]; then
  stash_message="rehome-${target_branch#codex/}-$(date +%s)"
  git stash push --include-untracked --message "$stash_message" >/dev/null
  stash_ref="$(git stash list --format='%gd %s' | awk -v msg="$stash_message" '$0 ~ msg {print $1; exit}')"
  [[ -n "$stash_ref" ]] || die "Created a stash for rehome, but could not resolve its ref. Inspect git stash list before continuing."
fi

git switch main
git fetch origin
git pull --ff-only origin main

worktree_base="$(resolve_worktree_base_dir)"
worktree_name="$(branch_to_worktree_name "$target_branch")"
worktree_path="$worktree_base/$worktree_name"
suffix=2

while [[ -e "$worktree_path" ]]; do
  worktree_path="$worktree_base/${worktree_name}-${suffix}"
  suffix=$((suffix + 1))
done

mkdir -p "$worktree_base"
if ! git worktree add "$worktree_path" "$target_branch"; then
  if [[ -n "$stash_ref" ]]; then
    die "Failed to create the dedicated worktree for $target_branch. The root workspace is back on main. Recover your task changes from $stash_ref and try again."
  fi
  die "Failed to create the dedicated worktree for $target_branch. The root workspace is back on main."
fi

if [[ -n "$stash_ref" ]]; then
  if ! git -C "$worktree_path" stash apply "$stash_ref" >/dev/null; then
    die "Restoring stashed task changes into $worktree_path produced conflicts. Resolve them there, then clean up stash entry $stash_ref manually."
  fi
  git stash drop "$stash_ref" >/dev/null
fi

ensure_branch "main"
ensure_clean_worktree

info "Root workspace is back on clean main."
info "Task branch $target_branch now lives at $worktree_path"
