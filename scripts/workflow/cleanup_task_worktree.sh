#!/usr/bin/env bash

# Remove a finalized task worktree (and its local task branch) from the root
# workspace.
#
# Worktree removal deliberately lives OUTSIDE finalize_task_to_main.sh: the
# finalize caller's shell sits inside the task worktree, and deleting that
# directory from under a persistent shell cwd breaks the caller's next shell
# spawn (ZCode harness "spawn /bin/zsh ENOENT"). This script therefore must
# run from the root workspace and refuses to remove the worktree the caller
# is currently inside.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

branch="${1:-}"
[[ -n "$branch" ]] || die "Usage: scripts/workflow/cleanup_task_worktree.sh <task-branch-or-slug>"

ensure_repo_root_cwd
root_workspace="$(root_workspace_path)"

worktree_name="$(branch_to_worktree_name "$branch")"
task_worktree="$root_workspace/.worktrees/$worktree_name"

if [[ ! -d "$task_worktree" ]]; then
  bound_path="$(worktree_path_for_branch "$branch" || true)"
  if [[ -n "$bound_path" && "$bound_path" == "$root_workspace/.worktrees/"* ]]; then
    task_worktree="$bound_path"
  else
    git -C "$root_workspace" worktree prune
    info "No task workspace found at $task_worktree; nothing to clean up."
    exit 0
  fi
fi

if [[ "$(pwd -P)" == "$task_worktree" || "$(pwd -P)" == "$task_worktree"/* ]]; then
  die "Current directory is inside $task_worktree. Run this script from the root workspace (cd $root_workspace) so the removed directory is never the caller's cwd."
fi

ensure_clean_worktree_at "$task_worktree"
git -C "$root_workspace" worktree remove "$task_worktree"
git -C "$root_workspace" worktree prune

for candidate_branch in "$branch" "codex/$worktree_name"; do
  if [[ "$candidate_branch" != "codex/"* ]]; then
    continue
  fi
  if git -C "$root_workspace" show-ref --verify --quiet "refs/heads/$candidate_branch"; then
    git -C "$root_workspace" branch -D "$candidate_branch"
    info "Removed task workspace $task_worktree and deleted local branch $candidate_branch."
    exit 0
  fi
done

info "Removed task workspace $task_worktree."
