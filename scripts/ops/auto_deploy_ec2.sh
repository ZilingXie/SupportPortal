#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${AUTO_DEPLOY_REPO_ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
DEPLOY_SCRIPT="${AUTO_DEPLOY_DEPLOY_SCRIPT:-${PROJECT_ROOT}/deployment/deploy_ec2.sh}"
ENV_FILE="${AUTO_DEPLOY_ENV_FILE:-${PROJECT_ROOT}/.env}"
LOCK_FILE="${AUTO_DEPLOY_LOCK_FILE:-${DEPLOY_LOCK_FILE:-${PROJECT_ROOT}/.deploy_ec2.lock}}"

DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_DOMAIN="${DEPLOY_DOMAIN:-support.stellarix.space}"
DEPLOY_ALERT_TO="${DEPLOY_ALERT_TO:-}"
DEPLOY_ALERT_FROM="${DEPLOY_ALERT_FROM:-}"
DEPLOY_AWS_REGION="${DEPLOY_AWS_REGION:-}"

EXECUTION_MODE="startup"
CURRENT_STEP="startup"
LOG_FILE=""
EXIT_HANDLER_ACTIVE=0

log() {
  printf '[auto-deploy] %s\n' "$*"
}

warn() {
  printf '[auto-deploy] WARN: %s\n' "$*" >&2
}

fail() {
  printf '[auto-deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing command: $1"
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

strip_wrapping_quotes() {
  local value="$1"
  if [[ ${#value} -ge 2 ]]; then
    if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  printf '%s' "${value}"
}

read_env_file_value() {
  local key="$1"
  local value
  [[ -f "${ENV_FILE}" ]] || return 0
  value="$(awk -F= -v key="${key}" '
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      sub("^[[:space:]]*" key "[[:space:]]*=[[:space:]]*", "", $0)
      print $0
      exit
    }
  ' "${ENV_FILE}")"
  value="${value%$'\r'}"
  value="$(trim "${value}")"
  value="$(strip_wrapping_quotes "${value}")"
  printf '%s' "${value}"
}

resolve_env_value() {
  local key="$1"
  if [[ -n "${!key:-}" ]]; then
    printf '%s' "${!key}"
    return 0
  fi
  read_env_file_value "${key}"
}

resolve_positive_integer() {
  local key="$1"
  local default_value="$2"
  local value

  value="$(resolve_env_value "${key}")"
  if [[ "${value}" =~ ^[0-9]+$ ]] && (( value > 0 )); then
    printf '%s\n' "${value}"
  else
    printf '%s\n' "${default_value}"
  fi
}

resolve_port() {
  local port
  port="$(resolve_env_value NGINX_HOST_PORT)"
  if [[ -z "${port}" ]]; then
    port="8080"
  fi
  printf '%s\n' "${port}"
}

current_hostname() {
  if hostname -f >/dev/null 2>&1; then
    hostname -f
  else
    hostname
  fi
}

setup_logging() {
  LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/supportportal-auto-deploy.XXXXXX")"
  exec > >(tee -a "${LOG_FILE}")
  exec 2>&1
}

wait_for_http_ok() {
  local label="$1"
  local url="$2"
  local timeout_seconds="$3"
  local retry_interval_seconds="$4"
  local start_ts current_ts elapsed response

  start_ts="$(date +%s)"
  while true; do
    if response="$(curl -fsS --max-time 5 "${url}" 2>&1)"; then
      log "${label} health response: ${response}"
      return 0
    fi

    current_ts="$(date +%s)"
    elapsed=$((current_ts - start_ts))
    if (( elapsed >= timeout_seconds )); then
      log "${label} health check last error: ${response}"
      return 1
    fi

    log "Waiting for ${label} health (${elapsed}s/${timeout_seconds}s): ${response}"
    sleep "${retry_interval_seconds}"
  done
}

send_failure_email() {
  local status="$1"
  local payload_file

  if [[ -z "${DEPLOY_ALERT_TO}" || -z "${DEPLOY_ALERT_FROM}" || -z "${DEPLOY_AWS_REGION}" ]]; then
    warn "Skipping failure email because DEPLOY_ALERT_TO, DEPLOY_ALERT_FROM, or DEPLOY_AWS_REGION is missing."
    return 1
  fi

  if ! command -v aws >/dev/null 2>&1; then
    warn "Skipping failure email because aws CLI is not installed."
    return 1
  fi

  payload_file="$(mktemp "${TMPDIR:-/tmp}/supportportal-auto-deploy-email.XXXXXX")"
  if ! AUTO_DEPLOY_FAILURE_STATUS="${status}" \
    AUTO_DEPLOY_FAILURE_HOST="$(current_hostname)" \
    AUTO_DEPLOY_FAILURE_COMMIT="$(git -C "${PROJECT_ROOT}" rev-parse --short HEAD 2>/dev/null || printf 'unknown')" \
    AUTO_DEPLOY_FAILURE_TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    AUTO_DEPLOY_FAILURE_STEP="${CURRENT_STEP}" \
    AUTO_DEPLOY_FAILURE_MODE="${EXECUTION_MODE}" \
    AUTO_DEPLOY_LOG_FILE="${LOG_FILE}" \
    python3 - "${payload_file}" <<'PY'
import json
import os
import sys
from pathlib import Path

payload_path = Path(sys.argv[1])
log_file = os.environ.get("AUTO_DEPLOY_LOG_FILE", "")
log_path = Path(log_file) if log_file else None

to_addresses = [
    item.strip()
    for item in os.environ["DEPLOY_ALERT_TO"].split(",")
    if item.strip()
]
subject = (
    f"[SupportPortal][auto-deploy failed] "
    f"{os.environ['AUTO_DEPLOY_FAILURE_HOST']} "
    f"{os.environ['DEPLOY_BRANCH']} "
    f"{os.environ['AUTO_DEPLOY_FAILURE_COMMIT']}"
)

if log_path is not None and log_path.exists():
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    log_tail = "\n".join(lines[-80:])
else:
    log_tail = "(no log output captured)"

body = "\n".join(
    [
        "SupportPortal auto deploy failed.",
        "",
        f"Host: {os.environ['AUTO_DEPLOY_FAILURE_HOST']}",
        f"Execution mode: {os.environ['AUTO_DEPLOY_FAILURE_MODE']}",
        f"Branch: {os.environ['DEPLOY_BRANCH']}",
        f"Commit: {os.environ['AUTO_DEPLOY_FAILURE_COMMIT']}",
        f"Failed step: {os.environ['AUTO_DEPLOY_FAILURE_STEP']}",
        f"Exit status: {os.environ['AUTO_DEPLOY_FAILURE_STATUS']}",
        f"Timestamp (UTC): {os.environ['AUTO_DEPLOY_FAILURE_TIMESTAMP']}",
        f"Domain: {os.environ['DEPLOY_DOMAIN']}",
        "",
        "Recent log tail:",
        log_tail or "(no log output captured)",
    ]
)

payload = {
    "FromEmailAddress": os.environ["DEPLOY_ALERT_FROM"],
    "Destination": {"ToAddresses": to_addresses},
    "Content": {
        "Simple": {
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        }
    },
}

payload_path.write_text(json.dumps(payload), encoding="utf-8")
PY
  then
    rm -f "${payload_file}"
    return 1
  fi

  if aws sesv2 send-email \
    --region "${DEPLOY_AWS_REGION}" \
    --cli-input-json "file://${payload_file}" \
    --no-cli-pager >/dev/null; then
    log "Failure alert sent via SES to ${DEPLOY_ALERT_TO}."
  else
    warn "SES failure alert sending failed."
    rm -f "${payload_file}"
    return 1
  fi

  rm -f "${payload_file}"
}

cleanup() {
  local status="$1"
  if (( EXIT_HANDLER_ACTIVE )); then
    return
  fi
  EXIT_HANDLER_ACTIVE=1

  if (( status != 0 )); then
    log "Run failed during step: ${CURRENT_STEP}"
    send_failure_email "${status}" || true
  fi

  if [[ -n "${LOG_FILE}" && -f "${LOG_FILE}" ]]; then
    rm -f "${LOG_FILE}"
  fi
}

trap 'cleanup $?' EXIT

main() {
  local current_branch local_head remote_head host_port internal_url external_url
  local health_timeout_seconds health_retry_interval_seconds

  setup_logging

  CURRENT_STEP="Validate required commands"
  require_cmd git
  require_cmd curl
  require_cmd flock
  require_cmd python3
  require_cmd tail

  CURRENT_STEP="Validate repo paths"
  [[ -d "${PROJECT_ROOT}" ]] || fail "Project root not found: ${PROJECT_ROOT}"
  [[ -x "${DEPLOY_SCRIPT}" ]] || fail "Deploy script not found or not executable: ${DEPLOY_SCRIPT}"

  cd "${PROJECT_ROOT}"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Not inside a git repository: ${PROJECT_ROOT}"

  CURRENT_STEP="Validate deploy configuration"
  [[ -n "${DEPLOY_BRANCH}" ]] || fail "DEPLOY_BRANCH is required."
  [[ -n "${DEPLOY_DOMAIN}" ]] || fail "DEPLOY_DOMAIN is required."
  [[ -n "${DEPLOY_ALERT_TO}" ]] || fail "DEPLOY_ALERT_TO is required."
  [[ -n "${DEPLOY_ALERT_FROM}" ]] || fail "DEPLOY_ALERT_FROM is required."
  [[ -n "${DEPLOY_AWS_REGION}" ]] || fail "DEPLOY_AWS_REGION is required."

  CURRENT_STEP="Acquire deploy lock"
  mkdir -p "$(dirname -- "${LOCK_FILE}")"
  exec 9>"${LOCK_FILE}"
  flock -n 9 || fail "Another deployment or auto health check is already running (lock: ${LOCK_FILE})."
  log "Acquired deploy lock: ${LOCK_FILE}"

  CURRENT_STEP="Validate local checkout"
  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  [[ "${current_branch}" != "HEAD" ]] || fail "Detached HEAD detected in deploy checkout."
  [[ "${current_branch}" == "${DEPLOY_BRANCH}" ]] || fail "Deploy checkout must stay on ${DEPLOY_BRANCH}; found ${current_branch}."
  [[ -z "$(git status --porcelain)" ]] || fail "Deploy checkout must be clean before auto deploy."
  log "Current git state: branch=${current_branch} commit=$(git rev-parse --short HEAD)"

  CURRENT_STEP="Fetch remote refs"
  git fetch origin --prune
  git show-ref --verify --quiet "refs/remotes/origin/${DEPLOY_BRANCH}" \
    || fail "Remote branch not found: origin/${DEPLOY_BRANCH}"

  CURRENT_STEP="Determine execution mode"
  local_head="$(git rev-parse HEAD)"
  remote_head="$(git rev-parse "origin/${DEPLOY_BRANCH}")"
  if [[ "${local_head}" == "${remote_head}" ]]; then
    EXECUTION_MODE="health-only"
  elif git merge-base --is-ancestor "${local_head}" "origin/${DEPLOY_BRANCH}"; then
    EXECUTION_MODE="deploy"
  else
    fail "Local ${DEPLOY_BRANCH} is not a clean ancestor of origin/${DEPLOY_BRANCH}; manual intervention required."
  fi
  log "Execution mode: ${EXECUTION_MODE}"

  if [[ "${EXECUTION_MODE}" == "deploy" ]]; then
    CURRENT_STEP="Run deploy script"
    DEPLOY_LOCK_ALREADY_HELD=1 DEPLOY_LOCK_FILE="${LOCK_FILE}" "${DEPLOY_SCRIPT}" \
      --branch "${DEPLOY_BRANCH}" \
      --domain "${DEPLOY_DOMAIN}"
    log "Deploy mode finished successfully."
    return 0
  fi

  host_port="$(resolve_port)"
  health_timeout_seconds="$(resolve_positive_integer DEPLOY_HEALTH_TIMEOUT_SECONDS 90)"
  health_retry_interval_seconds="$(resolve_positive_integer DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS 2)"
  internal_url="${AUTO_DEPLOY_INTERNAL_HEALTH_URL:-http://127.0.0.1:${host_port}/health}"
  external_url="${AUTO_DEPLOY_EXTERNAL_HEALTH_URL:-https://${DEPLOY_DOMAIN}/health}"

  CURRENT_STEP="Internal health check"
  log "Checking internal health: ${internal_url}"
  wait_for_http_ok "Internal" "${internal_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}" \
    || fail "Internal health check failed: ${internal_url}"

  CURRENT_STEP="External health check"
  log "Checking external health: ${external_url}"
  wait_for_http_ok "External" "${external_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}" \
    || fail "External health check failed: ${external_url}"

  log "Health-only mode finished successfully."
}

main "$@"
