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

current_branch() {
  git branch --show-current
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
