#!/usr/bin/env bash
# Point the five WeKnora services at the given task definitions (dependency
# order: paradedb -> redis -> docreader -> app -> frontend), wait for steady
# state, and verify the public entry point.
#
# Usage:
#   deploy_weknora_services.sh --task-definitions <arn-map.json> [--timeout 1800]
set -Eeuo pipefail

TD_FILE=""
TIMEOUT=1800
AWS_CLI_BIN="${AWS_CLI_BIN:-aws}"
REGION="${AWS_REGION:-us-east-1}"
CLUSTER="supportportal-weknora"
PUBLIC_URL="https://supportcenter.stellarix.space/dashboard/weknora/"

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[deploy-weknora] $*" >&2; }

while [[ $# -ge 1 ]]; do
  case "$1" in
    --task-definitions) [[ $# -ge 2 ]] || fail "--task-definitions requires a value"; TD_FILE="$2"; shift 2 ;;
    --timeout) [[ $# -ge 2 ]] || fail "--timeout requires a value"; TIMEOUT="$2"; shift 2 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[[ -n "$TD_FILE" && -f "$TD_FILE" ]] || fail "--task-definitions <file> is required"

td() { jq -r --arg k "$1" '.[$k] // empty' "$TD_FILE"; }

wait_stable() { # service
  local svc="$1" waited=0 status
  log "waiting for $svc to reach steady state (timeout ${TIMEOUT}s)"
  while :; do
    status="$("$AWS_CLI_BIN" ecs describe-services --cluster "$CLUSTER" --services "$svc" \
      --query 'services[0].{st:status,run:runningCount,dep:deployments[?status==`PRIMARY`].runningCount|[0],pend:pendingCount}' \
      --output json --region "$REGION")"
    if [[ "$(jq -r .st <<<"$status")" == "ACTIVE" && "$(jq -r .pend <<<"$status")" == "0" ]] \
       && [[ "$(jq -r .dep <<<"$status")" == "$(jq -r .run <<<"$status")" && "$(jq -r .run <<<"$status")" != "0" ]]; then
      log "$svc stable"
      return 0
    fi
    if (( waited >= TIMEOUT )); then
      fail "service $svc did not become stable within ${TIMEOUT}s (last: $status)"
    fi
    sleep 30; waited=$((waited + 30))
  done
}

for SVC in paradedb redis docreader app frontend; do
  ARN="$(td "$SVC")"
  [[ -n "$ARN" ]] || fail "task definition for $SVC missing in $TD_FILE"
  log "updating $SVC -> ${ARN}"
  "$AWS_CLI_BIN" ecs update-service --cluster "$CLUSTER" --service "weknora-$SVC" \
    --task-definition "$ARN" --region "$REGION" \
    --query 'service.taskDefinition' --output text >/dev/null || fail "update-service failed for $SVC"
  wait_stable "weknora-$SVC"
done

log "verifying public entry point $PUBLIC_URL"
HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$PUBLIC_URL" || true)"
[[ "$HTTP_CODE" == "200" ]] || fail "public entry point returned HTTP $HTTP_CODE (expected 200)"
log "public entry point healthy (HTTP 200)"

jq -n --arg url "$PUBLIC_URL" --arg http "200" '{publicUrl:$url, httpStatus:$http}'
