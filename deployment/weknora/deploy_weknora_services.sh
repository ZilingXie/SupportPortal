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

# Wait until the service runs the TARGET task definition at the desired count
# with a completed rollout. A circuit-breaker rollback reverts PRIMARY to the
# previous task definition and MUST fail this function — "stable on the old
# version" is not a successful deployment.
wait_stable() { # service target_task_definition_arn
  local svc="$1" target_td="$2" waited=0
  local json status desired total_run pend primary_td rollout primary_run
  log "waiting for $svc to run ${target_td##*:} at desired count (timeout ${TIMEOUT}s)"
  while :; do
    json="$("$AWS_CLI_BIN" ecs describe-services --cluster "$CLUSTER" --services "$svc" \
      --output json --region "$REGION")" || fail "describe-services failed for $svc"
    status="$(jq -r '.services[0].status' <<<"$json")"
    desired="$(jq -r '.services[0].desiredCount' <<<"$json")"
    total_run="$(jq -r '.services[0].runningCount' <<<"$json")"
    pend="$(jq -r '.services[0].pendingCount' <<<"$json")"
    primary_td="$(jq -r '.services[0].deployments[] | select(.status == "PRIMARY") | .taskDefinition' <<<"$json")"
    rollout="$(jq -r '.services[0].deployments[] | select(.status == "PRIMARY") | .rolloutState // "UNKNOWN"' <<<"$json")"
    primary_run="$(jq -r '.services[0].deployments[] | select(.status == "PRIMARY") | .runningCount' <<<"$json")"

    if [[ "$primary_td" != "$target_td" ]]; then
      fail "service $svc PRIMARY task definition is ${primary_td##*:}, expected ${target_td##*:} — deployment was rolled back or superseded"
    fi
    if [[ "$rollout" == "FAILED" ]]; then
      fail "service $svc deployment rolloutState=FAILED (circuit breaker)"
    fi
    if [[ "$status" == "ACTIVE" && "$rollout" == "COMPLETED" \
          && "$total_run" == "$desired" && "$pend" == "0" \
          && "$primary_run" == "$desired" && "$desired" -gt 0 ]]; then
      log "$svc stable on ${target_td##*:} (${total_run}/${desired})"
      return 0
    fi
    if (( waited >= TIMEOUT )); then
      fail "service $svc did not become stable within ${TIMEOUT}s (status=$status rollout=$rollout running=$total_run/$desired primary=$primary_run pending=$pend)"
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
  wait_stable "weknora-$SVC" "$ARN"
done

log "verifying public entry point $PUBLIC_URL"
HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$PUBLIC_URL" || true)"
[[ "$HTTP_CODE" == "200" ]] || fail "public entry point returned HTTP $HTTP_CODE (expected 200)"
log "public entry point healthy (HTTP 200)"

jq -n --arg url "$PUBLIC_URL" --arg http "200" '{publicUrl:$url, httpStatus:$http}'
