#!/usr/bin/env bash

set -euo pipefail

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*"
}

warn() {
  printf '%s\n' "$*" >&2
}

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || die "Not inside a git repository."
}

common_git_dir() {
  git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || die "Unable to resolve the shared git directory."
}

root_workspace_path() {
  local common_dir

  common_dir="$(common_git_dir)"
  if [[ "$(basename "$common_dir")" != ".git" ]]; then
    die "Unable to infer the root workspace from git common dir '$common_dir'."
  fi

  dirname "$common_dir"
}

current_branch() {
  git branch --show-current
}

current_worktree_path() {
  pwd -P
}

current_branch_at() {
  local worktree_path="$1"

  git -C "$worktree_path" branch --show-current
}

ensure_repo_root_cwd() {
  local expected_root
  local current_dir

  expected_root="$(root_workspace_path)"
  current_dir="$(pwd -P)"

  if [[ "$current_dir" != "$expected_root" ]]; then
    die "Run this script from the root workspace at $expected_root. Current directory: $current_dir"
  fi
}

ensure_branch_at() {
  local worktree_path="$1"
  local expected="$2"
  local branch

  branch="$(current_branch_at "$worktree_path")"
  if [[ -z "$branch" ]]; then
    die "Current worktree at $worktree_path is on detached HEAD. Bind it to a named branch before continuing."
  fi

  if [[ "$branch" != "$expected" ]]; then
    die "Expected branch '$expected' at $worktree_path, but found '$branch'."
  fi
}

ensure_branch() {
  local expected="$1"
  local branch

  branch="$(current_branch)"
  if [[ -z "$branch" ]]; then
    die "Current worktree is on detached HEAD. Bind it to a named branch before continuing."
  fi

  if [[ "$branch" != "$expected" ]]; then
    die "Expected current branch '$expected', but found '$branch'."
  fi
}

ensure_clean_worktree_at() {
  local worktree_path="$1"
  local status_output

  status_output="$(git -C "$worktree_path" status --porcelain=v1 --untracked-files=all)"
  if [[ -n "$status_output" ]]; then
    printf 'Current worktree must be clean before continuing.\n%s\n' "$status_output" >&2
    exit 1
  fi
}

ensure_clean_worktree() {
  ensure_clean_worktree_at "$(repo_root)"
}

ensure_root_workspace_on_main() {
  ensure_repo_root_cwd
  ensure_branch "main"
  ensure_clean_worktree
}

ensure_root_workspace_ready() {
  local root_workspace

  root_workspace="$(root_workspace_path)"
  ensure_branch_at "$root_workspace" "main"
  ensure_clean_worktree_at "$root_workspace"
}

ensure_task_not_in_root_workspace() {
  local branch="$1"
  local current_root
  local root_workspace

  [[ "$branch" == codex/* ]] || return 0

  current_root="$(repo_root)"
  root_workspace="$(root_workspace_path)"

  if [[ "$current_root" == "$root_workspace" ]]; then
    die "Task branch '$branch' is checked out in the root workspace at $root_workspace. Run scripts/workflow/rehome_task_worktree.sh $branch from the root workspace before continuing."
  fi
}

worktree_path_for_branch() {
  local branch="$1"
  local target_ref="refs/heads/$branch"
  local line
  local current_path=""

  while IFS= read -r line; do
    case "$line" in
      worktree\ *)
        current_path="${line#worktree }"
        ;;
      branch\ *)
        if [[ "${line#branch }" == "$target_ref" ]]; then
          printf '%s\n' "$current_path"
          return 0
        fi
        ;;
    esac
  done < <(git worktree list --porcelain)

  return 1
}

ensure_branch_owned_by_current_worktree() {
  local branch="$1"
  local bound_path
  local current_path

  bound_path="$(worktree_path_for_branch "$branch")" || die "Branch '$branch' is not currently attached to any named worktree."
  current_path="$(repo_root)"

  if [[ "$bound_path" != "$current_path" ]]; then
    die "Branch '$branch' is bound to $bound_path, not the current worktree $current_path."
  fi
}

resolve_worktree_base_dir() {
  local root_workspace

  root_workspace="$(root_workspace_path)"
  printf '%s\n' "$root_workspace/.worktrees"
}

ensure_project_worktree_base_ignored() {
  local root_workspace

  root_workspace="$(root_workspace_path)"
  if ! git -C "$root_workspace" check-ignore -q ".worktrees/" 2>/dev/null; then
    die "Project-local task workspaces require '.worktrees/' to be ignored. Add it to .gitignore before creating task workspaces."
  fi
}

slugify_thread_name() {
  local raw_name="${1:-}"
  local slug

  slug="$(
    printf '%s' "$raw_name" \
      | LC_ALL=C tr '[:upper:]' '[:lower:]' \
      | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'
  )"

  if [[ -z "$slug" ]]; then
    die "Thread name '$raw_name' does not produce a usable ASCII slug. Provide an explicit ASCII slug."
  fi

  printf '%s\n' "$slug"
}

branch_to_worktree_name() {
  local branch="$1"

  case "$branch" in
    codex/*)
      printf '%s\n' "${branch#codex/}"
      ;;
    */*)
      printf '%s\n' "${branch##*/}"
      ;;
    *)
      printf '%s\n' "$branch"
      ;;
  esac
}

branch_to_title() {
  local branch="$1"
  local slug

  slug="$(branch_to_worktree_name "$branch")"
  printf '%s\n' "$slug" | tr '-' ' '
}

require_command() {
  local command_name="$1"

  command -v "$command_name" >/dev/null 2>&1 || die "Required command not found: $command_name"
}

require_gh() {
  require_command "gh"
}

# Local podman hygiene for scripts that rebuild per-commit images: drop
# localhost/supportportal-* images no container uses anymore (old build tags
# left behind by rebuilds) and return the freed blocks to the host by trimming
# the podman machine disk. Images referenced by any container (running or
# stopped) are kept because podman refuses to remove them; the per-image
# failure is silenced on purpose so in-use tags are simply skipped.
reclaim_local_podman_disk() {
  podman image prune -f >/dev/null 2>&1 || true

  local removed=0 ref
  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    if podman image rm "$ref" >/dev/null 2>&1; then
      removed=$((removed + 1))
    fi
  done < <(podman images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
    | grep -E '^localhost/supportportal-' \
    | grep -v ':<none>$' || true)

  if (( removed > 0 )); then
    info "Removed ${removed} unused localhost/supportportal-* image tag(s)."
  fi
  if podman machine ssh 'sudo fstrim -v /' >/dev/null 2>&1; then
    info "Podman machine disk trimmed; reclaimed space returned to the host."
  else
    warn "Podman machine disk trim skipped (podman machine ssh unavailable)."
  fi
}

finalization_lock_dir() {
  printf '%s\n' "$(common_git_dir)/codex-finalize-main.lock"
}

acquire_main_finalization_lock() {
  local lock_dir
  local timeout_seconds
  local poll_seconds
  local waited=0

  lock_dir="$(finalization_lock_dir)"
  timeout_seconds="${CODEX_FINALIZE_LOCK_TIMEOUT_SECONDS:-300}"
  poll_seconds="${CODEX_FINALIZE_LOCK_POLL_INTERVAL_SECONDS:-1}"

  while ! mkdir "$lock_dir" 2>/dev/null; do
    if (( waited >= timeout_seconds )); then
      die "Timed out acquiring the main finalization lock at $lock_dir."
    fi
    sleep "$poll_seconds"
    waited=$(( waited + poll_seconds ))
  done

  export CODEX_MAIN_FINALIZATION_LOCK_DIR="$lock_dir"
  printf '%s\n' "$$" > "$lock_dir/pid"
}

release_main_finalization_lock() {
  local lock_dir="${CODEX_MAIN_FINALIZATION_LOCK_DIR:-}"

  if [[ -n "$lock_dir" && -d "$lock_dir" ]]; then
    rm -rf "$lock_dir"
  fi
}

existing_open_pr_json() {
  local branch="$1"

  require_gh
  gh pr list --state open --head "$branch" --base main --json number,url,title,state,headRefName,baseRefName
}

is_known_artifact() {
  local path="$1"

  case "$path" in
    .worktrees/*|*/.worktrees/*|.superpowers/*|*/.superpowers/*|.DS_Store|*/.DS_Store)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}
