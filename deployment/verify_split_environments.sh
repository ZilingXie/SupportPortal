#!/usr/bin/env bash
# Read-only acceptance probes for the split Route/Automation environments.
#
# Run on the EC2 host that serves the single-host stack (same host assumptions
# as deploy_ec2.sh). Every check is read-only: no executions are created, no
# Zendesk writes are attempted. The Zendesk credential probe issues a single
# GET so a stale token surfaces in seconds instead of after a failed
# side-effect execution.
#
# Usage:
#   ./deployment/verify_split_environments.sh [--zendesk-ticket <id>]
#
# Exits non-zero if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"
NGINX_BASE="http://localhost:${NGINX_HOST_PORT:-8080}"
ZENDESK_PREFLIGHT_TICKET="12895"

failures=0

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --zendesk-ticket)
      [[ "$#" -ge 2 ]] || { echo "--zendesk-ticket requires a ticket id" >&2; exit 2; }
      ZENDESK_PREFLIGHT_TICKET="$2"
      shift 2
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; failures=$((failures + 1)); }

read_env_value() {
  local key="$1" value
  value="$(awk -F= -v k="${key}" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "${ENV_FILE}" 2>/dev/null || true)"
  printf '%s' "${value}"
}

expect_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "${actual}" == "${expected}" ]]; then
    pass "${label}"
  else
    fail "${label} (expected '${expected}', got '${actual}')"
  fi
}

expect_http() {
  local label="$1" expected="$2" method="${3:-GET}" url="$4" data="${5:-}"
  local code
  if [[ -n "${data}" ]]; then
    code="$(curl -s -o /dev/null -w '%{http_code}' -X "${method}" -H 'Content-Type: application/json' -d "${data}" "${url}" || echo 000)"
  else
    code="$(curl -s -o /dev/null -w '%{http_code}' -X "${method}" "${url}" || echo 000)"
  fi
  expect_eq "${label}" "${expected}" "${code}"
}

json_field() {
  local url="$1" field="$2"
  curl -s "${url}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('${field}', ''))" 2>/dev/null || echo ''
}

echo "== Split environment acceptance probes (nginx at ${NGINX_BASE})"

for env_name in staging preproduction production; do
  base="${NGINX_BASE}/automation/${env_name}"
  expect_http "${env_name} /health" 200 GET "${base}/health"
  expect_eq "${env_name} health.environment" "${env_name}" "$(json_field "${base}/health" environment)"

  capabilities="$(curl -s "${base}/v1/capabilities")"
  case "${env_name}" in
    staging)
      expect_eq "staging rerun" "True" "$(echo "${capabilities}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["rerun"])')"
      expect_eq "staging zendesk visibility" "[]" "$(echo "${capabilities}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["comment_visibility"])')"
      ;;
    preproduction)
      expect_eq "preproduction comment_visibility" "['internal']" "$(echo "${capabilities}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["comment_visibility"])')"
      ;;
    production)
      expect_eq "production rerun" "False" "$(echo "${capabilities}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["rerun"])')"
      expect_eq "production comment_visibility" "['internal', 'external']" "$(echo "${capabilities}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["comment_visibility"])')"
      expect_http "production POST /v1/reruns returns 404" 404 POST "${base}/v1/reruns" '{}'
      ;;
  esac

  expect_http "${env_name} empty-body without token returns 401" 401 POST "${base}/v1/cases" '{}'
done
for env_name in staging preproduction production; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H 'Authorization: Bearer wrong-token' -d '{}' "${NGINX_BASE}/automation/${env_name}/v1/cases" || echo 000)"
  expect_eq "${env_name} Bearer wrong-token returns 401" 401 "${code}"
done

echo "== Container and network invariants"

automation_container() {
  docker ps --format '{{.Names}}' | grep "supportportal-automation-${1}-automation_${1}-1" | head -1
}

for env_name in staging preproduction production; do
  container="$(automation_container "${env_name}")"
  if [[ -n "${container}" ]]; then
    pass "${env_name} automation container running (${container})"
  else
    fail "${env_name} automation container not running"
    continue
  fi
  if [[ "${env_name}" == "staging" ]]; then
    if docker exec "${container}" sh -c 'env | grep -q "^zendesk_basic_auth="' 2>/dev/null; then
      fail "staging container unexpectedly holds zendesk_basic_auth"
    else
      pass "staging container has no zendesk_basic_auth"
    fi
  else
    side_effects="$(docker exec "${container}" sh -c 'printenv AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED' 2>/dev/null || echo '')"
    target_status="$(docker exec "${container}" sh -c 'printenv AUTOMATION_TARGET_TICKET_STATUS' 2>/dev/null || echo '')"
    expect_eq "${env_name} side-effect switch" "${SIDE_EFFECTS_EXPECTED:-1}" "${side_effects}"
    expect_eq "${env_name} target ticket status" "${TARGET_STATUS_EXPECTED:-pending}" "${target_status}"
  fi
done

for network_name in \
  "${AUTOMATION_STAGING_INTERNAL_NETWORK_NAME:-supportportal_automation_internal_staging}" \
  "${AUTOMATION_PREPRODUCTION_INTERNAL_NETWORK_NAME:-supportportal_automation_internal_preproduction}" \
  "${AUTOMATION_PRODUCTION_INTERNAL_NETWORK_NAME:-supportportal_automation_internal_production}"; do
  internal_flag="$(docker network inspect --format '{{.Internal}}' "${network_name}" 2>/dev/null || echo missing)"
  expect_eq "network ${network_name} is not internal" "false" "${internal_flag}"
done

echo "== Route outbound and Zendesk credential preflight"

route_container="$(docker ps --format '{{.Names}}' | grep 'supportportal-automation-staging-route_staging-1' | head -1)"
if [[ -n "${route_container}" ]]; then
  if docker exec "${route_container}" python -c 'import socket; socket.getaddrinfo("api.openai.com", 443)' >/dev/null 2>&1; then
    pass "route container resolves api.openai.com (outbound DNS)"
  else
    fail "route container cannot resolve api.openai.com (outbound DNS broken)"
  fi
else
  fail "route_staging container not running for outbound DNS probe"
fi

zendesk_auth="$(read_env_value zendesk_basic_auth)"
if [[ -z "${zendesk_auth}" ]]; then
  fail "zendesk_basic_auth is not set in ${ENV_FILE}"
elif [[ "${zendesk_auth}" != *:* ]]; then
  fail "zendesk_basic_auth has no ':' separator (expected email:token format)"
else
  zendesk_code="$(docker exec "$(automation_container preproduction)" python -c "
import base64, os, urllib.request
request = urllib.request.Request('https://agoraio.zendesk.com/api/v2/tickets/${ZENDESK_PREFLIGHT_TICKET}.json')
request.add_header('Authorization', 'Basic ' + base64.b64encode(os.environ['zendesk_basic_auth'].encode()).decode())
try:
    urllib.request.urlopen(request, timeout=15)
    print(200)
except urllib.error.HTTPError as error:
    print(error.code)
except Exception:
    print(0)
" 2>/dev/null || echo 000)"
  expect_eq "zendesk credential GET ticket ${ZENDESK_PREFLIGHT_TICKET}" 200 "${zendesk_code}"
fi

echo "== Legacy endpoints"
expect_http "/account/" 200 GET "${NGINX_BASE}/account/"
expect_http "/production/" 200 GET "${NGINX_BASE}/production/"

echo
if [[ "${failures}" -gt 0 ]]; then
  echo "RESULT: ${failures} check(s) FAILED"
  exit 1
fi
echo "RESULT: all checks passed"
