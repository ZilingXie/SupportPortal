#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

verify_only=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --verify-only)
      verify_only=true
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift
done

repo_root >/dev/null
require_gh

RULESET_NAME="codex-main-direct-pr"

build_ruleset_payload() {
  cat <<'JSON'
{
  "name": "codex-main-direct-pr",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "rules": [
    {
      "type": "pull_request",
      "parameters": {
        "allowed_merge_methods": ["squash"],
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_approving_review_count": 0,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_linear_history"
    },
    {
      "type": "non_fast_forward"
    },
    {
      "type": "deletion"
    }
  ]
}
JSON
}

apply_repo_settings() {
  gh repo edit \
    --enable-auto-merge \
    --delete-branch-on-merge \
    --enable-squash-merge \
    --enable-merge-commit=false \
    --enable-rebase-merge=false
}

upsert_ruleset() {
  local rulesets_json
  local ruleset_id
  local payload_file

  rulesets_json="$(gh api repos/{owner}/{repo}/rulesets)"
  ruleset_id="$(
    python3 - "$RULESET_NAME" "$rulesets_json" <<'PY'
import json
import sys

name = sys.argv[1]
rulesets = json.loads(sys.argv[2] or "[]")
for ruleset in rulesets:
    if ruleset.get("name") == name:
        print(ruleset["id"])
        break
PY
  )"

  payload_file="$(mktemp)"
  trap 'rm -f "$payload_file"' RETURN
  build_ruleset_payload > "$payload_file"

  if [[ -n "$ruleset_id" ]]; then
    gh api --method PUT "repos/{owner}/{repo}/rulesets/$ruleset_id" --input "$payload_file" >/dev/null
  else
    gh api --method POST "repos/{owner}/{repo}/rulesets" --input "$payload_file" >/dev/null
  fi

  rm -f "$payload_file"
  trap - RETURN
}

verify_repo_policy() {
  local repo_json
  local rulesets_json
  local ruleset_id
  local ruleset_json

  repo_json="$(gh api repos/{owner}/{repo})"
  rulesets_json="$(gh api repos/{owner}/{repo}/rulesets)"
  ruleset_id="$(
    python3 - "$RULESET_NAME" "$rulesets_json" <<'PY'
import json
import sys

name = sys.argv[1]
rulesets = json.loads(sys.argv[2] or "[]")
for ruleset in rulesets:
    if ruleset.get("name") == name:
        print(ruleset["id"])
        break
PY
  )"

  if [[ -n "$ruleset_id" ]]; then
    ruleset_json="$(gh api "repos/{owner}/{repo}/rulesets/$ruleset_id")"
  else
    ruleset_json="null"
  fi

  python3 - "$RULESET_NAME" "$repo_json" "$ruleset_json" <<'PY'
import json
import sys

ruleset_name = sys.argv[1]
repo = json.loads(sys.argv[2])
ruleset = json.loads(sys.argv[3] or "null")
errors = []

if not repo.get("allow_auto_merge"):
    errors.append("Repository auto-merge is not enabled.")
if not repo.get("delete_branch_on_merge"):
    errors.append("Repository delete-branch-on-merge is not enabled.")
if not repo.get("allow_squash_merge"):
    errors.append("Repository squash merge is not enabled.")
if repo.get("allow_merge_commit"):
    errors.append("Repository merge commits are still enabled.")
if repo.get("allow_rebase_merge"):
    errors.append("Repository rebase merges are still enabled.")

if not ruleset:
    errors.append(f"Ruleset {ruleset_name!r} does not exist.")
else:
    if ruleset.get("enforcement") != "active":
        errors.append(f"Ruleset {ruleset_name!r} is not active.")

    conditions = (ruleset.get("conditions") or {}).get("ref_name") or {}
    include = conditions.get("include") or []
    if "~DEFAULT_BRANCH" not in include and "refs/heads/main" not in include and "main" not in include:
        errors.append(f"Ruleset {ruleset_name!r} does not target main.")

    rules = {rule.get("type"): rule for rule in ruleset.get("rules") or []}
    for required_rule in ("pull_request", "required_linear_history", "non_fast_forward"):
        if required_rule not in rules:
            errors.append(f"Ruleset {ruleset_name!r} is missing rule {required_rule!r}.")

    pull_request_rule = rules.get("pull_request") or {}
    parameters = pull_request_rule.get("parameters") or {}
    if parameters.get("required_approving_review_count", 0) not in (0, None):
        errors.append("Ruleset requires manual approving reviews.")
    allowed_merge_methods = parameters.get("allowed_merge_methods") or []
    if allowed_merge_methods and allowed_merge_methods != ["squash"]:
        errors.append("Ruleset pull_request must allow only squash merges.")

if errors:
    for error in errors:
        print(error, file=sys.stderr)
        sys.exit(1)
PY
}

if [[ "$verify_only" == false ]]; then
  apply_repo_settings
  upsert_ruleset
fi

verify_repo_policy
info "Verified direct-to-main repository policy for main."
