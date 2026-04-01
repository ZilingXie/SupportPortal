#!/usr/bin/env bash

set -euo pipefail

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*"
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

ensure_repo_root_cwd() {
  local expected_root
  local current_dir

  expected_root="$(root_workspace_path)"
  current_dir="$(pwd -P)"

  if [[ "$current_dir" != "$expected_root" ]]; then
    die "Run this script from the root workspace at $expected_root. Current directory: $current_dir"
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

ensure_clean_worktree() {
  local status_output

  status_output="$(git status --porcelain=v1 --untracked-files=all)"
  if [[ -n "$status_output" ]]; then
    printf 'Current worktree must be clean before continuing.\n%s\n' "$status_output" >&2
    exit 1
  fi
}

ensure_root_workspace_on_main() {
  ensure_repo_root_cwd
  ensure_branch "main"
  ensure_clean_worktree
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

resolve_worktree_base_dir() {
  local root_workspace
  local line
  local worktree_path=""

  root_workspace="$(root_workspace_path)"

  while IFS= read -r line; do
    case "$line" in
      worktree\ *)
        worktree_path="${line#worktree }"
        if [[ "$worktree_path" != "$root_workspace" ]]; then
          dirname "$worktree_path"
          return 0
        fi
        ;;
    esac
  done < <(git worktree list --porcelain)

  printf '%s\n' "$HOME/.config/superpowers/worktrees/$(basename "$root_workspace")"
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
    mac)
      printf '%s\n' "mac-integration"
      ;;
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

is_known_artifact() {
  local path="$1"

  case "$path" in
    .superpowers/*|*/.superpowers/*|.DS_Store|*/.DS_Store)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}
