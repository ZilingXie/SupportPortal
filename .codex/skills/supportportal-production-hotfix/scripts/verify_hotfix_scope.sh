#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage:
  verify_hotfix_scope.sh --repo <worktree> --baseline <full-sha> --hotfix <full-sha> \
    --allow <relative-file-or-directory> [--allow <...>]

Each --allow value is a repository-relative exact path or directory prefix.
Every path changed by baseline..hotfix must match at least one allow value.
The command is read-only and prints the changed path/status evidence.
EOF
}

fail() {
  printf '[hotfix-scope] ERROR: %s\n' "$*" >&2
  exit 1
}

repo=""
baseline=""
hotfix=""
allows=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -ge 2 ]] || fail "--repo requires a value"
      repo="$2"
      shift 2
      ;;
    --baseline)
      [[ $# -ge 2 ]] || fail "--baseline requires a value"
      baseline="$2"
      shift 2
      ;;
    --hotfix)
      [[ $# -ge 2 ]] || fail "--hotfix requires a value"
      hotfix="$2"
      shift 2
      ;;
    --allow)
      [[ $# -ge 2 ]] || fail "--allow requires a value"
      allows+=("$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

[[ -n "$repo" ]] || fail "--repo is required"
[[ -d "$repo/.git" || -f "$repo/.git" ]] || fail "--repo must be a Git worktree"
[[ "$baseline" =~ ^[0-9a-f]{40}$ ]] || fail "--baseline must be a full 40-character lowercase SHA"
[[ "$hotfix" =~ ^[0-9a-f]{40}$ ]] || fail "--hotfix must be a full 40-character lowercase SHA"
(( ${#allows[@]} > 0 )) || fail "at least one --allow is required"

git_cmd=(git -C "$repo")
resolved_baseline="$("${git_cmd[@]}" rev-parse "${baseline}^{commit}")" \
  || fail "baseline does not resolve to a commit"
resolved_hotfix="$("${git_cmd[@]}" rev-parse "${hotfix}^{commit}")" \
  || fail "hotfix does not resolve to a commit"
[[ "$resolved_baseline" == "$baseline" ]] || fail "baseline is not the requested full SHA"
[[ "$resolved_hotfix" == "$hotfix" ]] || fail "hotfix is not the requested full SHA"

"${git_cmd[@]}" merge-base --is-ancestor "$baseline" "$hotfix" \
  || fail "hotfix is not descended from baseline"
head="$("${git_cmd[@]}" rev-parse HEAD)" || fail "cannot read worktree HEAD"
[[ "$head" == "$hotfix" ]] || fail "worktree HEAD ($head) does not equal --hotfix"
[[ -z "$("${git_cmd[@]}" status --porcelain --untracked-files=all)" ]] \
  || fail "worktree must be clean"

for allow in "${allows[@]}"; do
  [[ -n "$allow" && "$allow" != /* && "$allow" != ../* && "$allow" != */../* ]] \
    || fail "--allow must be a non-empty repository-relative path: $allow"
done

printf 'baseline=%s\nhotfix=%s\n' "$baseline" "$hotfix"
printf 'changed_status:\n'
"${git_cmd[@]}" diff --name-status --diff-filter=ACDMRTUXB "$baseline..$hotfix"
printf 'changed_paths:\n'
changed_paths=()
while IFS= read -r path; do
  [[ -n "$path" ]] && changed_paths+=("$path")
done < <("${git_cmd[@]}" diff --name-only --diff-filter=ACDMRTUXB "$baseline..$hotfix")
if (( ${#changed_paths[@]} == 0 )); then
  fail "hotfix has no changed path relative to baseline"
fi

unmatched=()
for path in "${changed_paths[@]}"; do
  printf '  %s\n' "$path"
  matched=0
  for allow in "${allows[@]}"; do
    if [[ "$path" == "$allow" || "$path" == "$allow"/* ]]; then
      matched=1
      break
    fi
  done
  (( matched == 1 )) || unmatched+=("$path")
done

if (( ${#unmatched[@]} > 0 )); then
  printf '[hotfix-scope] ERROR: paths outside --allow:\n' >&2
  printf '  %s\n' "${unmatched[@]}" >&2
  exit 1
fi

printf 'scope=passed\n'
