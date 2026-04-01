#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

expected_branch="${1:-}"
[[ -n "$expected_branch" ]] || die "Usage: scripts/workflow/check_task_worktree.sh <task-branch>"

repo_root >/dev/null
ensure_branch "$expected_branch"
ensure_task_not_in_root_workspace "$expected_branch"

tracked_changes=()
known_artifacts=()
ambiguous_untracked=()

while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  status="${line:0:2}"
  path="${line:3}"

  if is_known_artifact "$path"; then
    known_artifacts+=("$path")
    continue
  fi

  if [[ "$status" == "??" ]]; then
    ambiguous_untracked+=("$path")
    continue
  fi

  tracked_changes+=("$path")
done < <(git status --porcelain=v1 --untracked-files=all)

if (( ${#ambiguous_untracked[@]} > 0 )); then
  {
    printf 'Ambiguous untracked paths remain in the task worktree.\n'
    printf 'Stage them if they belong to the task, or clean them before finalization:\n'
    printf ' - %s\n' "${ambiguous_untracked[@]}"
  } >&2
  exit 1
fi

if (( ${#tracked_changes[@]} > 0 )); then
  info "Task changes may be committed on $expected_branch before finalization:"
  printf ' - %s\n' "${tracked_changes[@]}"
else
  info "No tracked task changes are pending on $expected_branch."
fi

if (( ${#known_artifacts[@]} > 0 )); then
  info "Known non-task artifacts were detected and must stay out of the finalization commit:"
  printf ' - %s\n' "${known_artifacts[@]}"
fi
