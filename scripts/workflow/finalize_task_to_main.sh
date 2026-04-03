#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

expected_branch="${1:-}"
shift || true
[[ -n "$expected_branch" ]] || die "Usage: scripts/workflow/finalize_task_to_main.sh <task-branch> --verify \"<command>\" [--pr-title \"<title>\"] [--pr-body-file <path>] [--commit-message \"<message>\"]"

verify_command=""
pr_title=""
pr_body_file=""
commit_message=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --verify)
      shift
      verify_command="${1:-}"
      ;;
    --pr-title)
      shift
      pr_title="${1:-}"
      ;;
    --pr-body-file)
      shift
      pr_body_file="${1:-}"
      ;;
    --commit-message)
      shift
      commit_message="${1:-}"
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift || true
done

[[ -n "$verify_command" ]] || die "Missing required --verify command."

repo_root >/dev/null
ensure_branch "$expected_branch"
ensure_branch_owned_by_current_worktree "$expected_branch"
ensure_task_not_in_root_workspace "$expected_branch"
ensure_root_workspace_ready
require_gh

acquire_main_finalization_lock
trap 'release_main_finalization_lock' EXIT

"$SCRIPT_DIR/check_task_worktree.sh" "$expected_branch"

tracked_changes_count="$(
  python3 - <<'PY'
import subprocess

status = subprocess.run(
    ["git", "status", "--porcelain=v1", "--untracked-files=all"],
    capture_output=True,
    text=True,
    check=True,
).stdout.splitlines()

count = 0
for line in status:
    if not line:
        continue
    state = line[:2]
    path = line[3:]
    if path.startswith(".superpowers/") or path.endswith("/.superpowers") or path == ".DS_Store" or path.endswith("/.DS_Store"):
        continue
    if state == "??":
        continue
    count += 1

print(count)
PY
)"

if (( tracked_changes_count > 0 )); then
  git add -u
  if [[ -z "$commit_message" ]]; then
    commit_message="Finalize $(branch_to_title "$expected_branch") for direct main merge"
  fi
  git commit -m "$commit_message"
fi

git fetch origin

if ! git merge-base --is-ancestor origin/main HEAD; then
  if ! git merge --no-edit origin/main; then
    die "Refreshing $expected_branch with origin/main produced conflicts. Resolve them in this task worktree, rerun verification, then finalize again."
  fi
fi

if ! bash -lc "$verify_command"; then
  die "Verification command failed on $expected_branch: $verify_command"
fi

if ! git diff --quiet origin/main...HEAD -- "docs/feature_list.md"; then
  info "Running automatic feature list verification."
  if ! python3 "$SCRIPT_DIR/../verify_feature_list.py" "docs/feature_list.md"; then
    die "Automatic feature list verification failed."
  fi
fi

ahead_count="$(git rev-list --count origin/main..HEAD)"
if (( ahead_count == 0 )); then
  die "Branch $expected_branch has no commits ahead of origin/main to finalize."
fi

git push -u origin "$expected_branch"
head_sha="$(git rev-parse HEAD)"

existing_pr_json="$(existing_open_pr_json "$expected_branch")"
pr_url="$(
  python3 - "$existing_pr_json" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1] or "[]")
if payload:
    print(payload[0]["url"])
PY
)"

if [[ -z "$pr_url" ]]; then
  if [[ -z "$pr_title" ]]; then
    pr_title="$(git log -1 --pretty=%s)"
  fi
  if [[ -z "$pr_title" ]]; then
    pr_title="$(branch_to_title "$expected_branch")"
  fi

  if [[ -n "$pr_body_file" ]]; then
    [[ -f "$pr_body_file" ]] || die "PR body file not found: $pr_body_file"
    pr_body="$(<"$pr_body_file")"
  else
    pr_body=$(
      cat <<EOF
## Summary
- finalize $expected_branch for direct-to-main auto-merge

## Test Plan
- $verify_command
EOF
    )
  fi

  pr_url="$(gh pr create --base main --head "$expected_branch" --title "$pr_title" --body "$pr_body")"
  info "Created PR $pr_url"
else
  info "Reusing existing PR $pr_url"
fi

gh pr merge "$pr_url" --squash --auto --match-head-commit "$head_sha"

merge_timeout_seconds="${CODEX_PR_MERGE_TIMEOUT_SECONDS:-300}"
poll_interval_seconds="${CODEX_PR_POLL_INTERVAL_SECONDS:-2}"
elapsed=0
merge_commit=""

while true; do
  pr_json="$(gh pr view "$pr_url" --json state,url,mergedAt,mergeCommit,headRefName,baseRefName)"
  pr_state="$(
    python3 - "$pr_json" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
print(payload["state"])
PY
  )"

  if [[ "$pr_state" == "MERGED" ]]; then
    merge_commit="$(
      python3 - "$pr_json" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
merge_commit = payload.get("mergeCommit") or {}
print(merge_commit.get("oid", ""))
PY
    )"
    [[ -n "$merge_commit" ]] || die "PR $pr_url is merged, but GitHub did not return a merge commit SHA."
    break
  fi

  if [[ "$pr_state" == "CLOSED" ]]; then
    die "PR $pr_url closed without merging. Task cleanup is blocked."
  fi

  if (( elapsed >= merge_timeout_seconds )); then
    die "Timed out waiting for PR $pr_url to merge."
  fi

  sleep "$poll_interval_seconds"
  elapsed=$(( elapsed + poll_interval_seconds ))
done

git fetch origin
root_workspace="$(root_workspace_path)"
git -C "$root_workspace" pull --ff-only origin main

if ! git -C "$root_workspace" merge-base --is-ancestor "$merge_commit" main; then
  die "Local main at $root_workspace does not yet contain merged PR commit $merge_commit."
fi

task_worktree="$(repo_root)"
git -C "$root_workspace" worktree remove "$task_worktree"

if git -C "$root_workspace" show-ref --verify --quiet "refs/heads/$expected_branch"; then
  git -C "$root_workspace" branch -D "$expected_branch"
fi

info "Merged PR $pr_url into main."
info "Updated root main at $root_workspace."
info "Removed task worktree $task_worktree and deleted local branch $expected_branch."
