#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${AUTO_DEPLOY_REPO_ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
DEPLOY_SCRIPT="${AUTO_DEPLOY_DEPLOY_SCRIPT:-${PROJECT_ROOT}/scripts/ops/deploy_surfaces_ec2.sh}"
REPORT_HELPER="${AUTO_DEPLOY_REPORT_HELPER:-${SCRIPT_DIR}/build_auto_deploy_report.py}"
COMPOSE_FILE="${AUTO_DEPLOY_COMPOSE_FILE:-${PROJECT_ROOT}/deployment/docker-compose.single-host.yml}"
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
RUN_STARTED_AT_UTC=""
RUN_START_EPOCH=0
CURRENT_HOST=""
LOCAL_COMMIT="unknown"
REMOTE_COMMIT="unknown"
INTERNAL_HEALTH_STATUS="not-run"
INTERNAL_HEALTH_DETAIL=""
EXTERNAL_HEALTH_STATUS="not-run"
EXTERNAL_HEALTH_DETAIL=""
LAST_HEALTH_OUTPUT=""
REPORT_TIMEZONE=""

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

current_utc_timestamp() {
  if [[ -n "${AUTO_DEPLOY_REPORT_NOW_UTC:-}" ]]; then
    printf '%s\n' "${AUTO_DEPLOY_REPORT_NOW_UTC}"
  else
    date -u +"%Y-%m-%dT%H:%M:%SZ"
  fi
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
      LAST_HEALTH_OUTPUT="${response}"
      log "${label} health response: ${response}"
      return 0
    fi

    current_ts="$(date +%s)"
    elapsed=$((current_ts - start_ts))
    if (( elapsed >= timeout_seconds )); then
      LAST_HEALTH_OUTPUT="${response}"
      log "${label} health check last error: ${response}"
      return 1
    fi

    LAST_HEALTH_OUTPUT="${response}"
    log "Waiting for ${label} health (${elapsed}s/${timeout_seconds}s): ${response}"
    sleep "${retry_interval_seconds}"
  done
}

build_fallback_report_payload() {
  local status="$1"
  local payload_file="$2"
  local run_status run_log_tail

  run_status="success"
  if (( status != 0 )); then
    run_status="failed"
  fi
  run_log_tail="$(tail -n 120 "${LOG_FILE}" 2>/dev/null || true)"

  if ! AUTO_DEPLOY_REPORT_STATUS="${run_status}" \
    AUTO_DEPLOY_REPORT_HOST="${CURRENT_HOST}" \
    AUTO_DEPLOY_REPORT_LOCAL_COMMIT="${LOCAL_COMMIT}" \
    AUTO_DEPLOY_REPORT_REMOTE_COMMIT="${REMOTE_COMMIT}" \
    AUTO_DEPLOY_REPORT_STARTED_AT="${RUN_STARTED_AT_UTC}" \
    AUTO_DEPLOY_REPORT_ENDED_AT="$(current_utc_timestamp)" \
    AUTO_DEPLOY_REPORT_DURATION_SECONDS="$(( $(date +%s) - RUN_START_EPOCH ))" \
    AUTO_DEPLOY_REPORT_FAILED_STEP="${CURRENT_STEP}" \
    AUTO_DEPLOY_REPORT_EXECUTION_MODE="${EXECUTION_MODE}" \
    AUTO_DEPLOY_REPORT_INTERNAL_STATUS="${INTERNAL_HEALTH_STATUS}" \
    AUTO_DEPLOY_REPORT_INTERNAL_DETAIL="${INTERNAL_HEALTH_DETAIL}" \
    AUTO_DEPLOY_REPORT_EXTERNAL_STATUS="${EXTERNAL_HEALTH_STATUS}" \
    AUTO_DEPLOY_REPORT_EXTERNAL_DETAIL="${EXTERNAL_HEALTH_DETAIL}" \
    AUTO_DEPLOY_REPORT_LOG_TAIL="${run_log_tail}" \
    AUTO_DEPLOY_REPORT_TIMEZONE="${REPORT_TIMEZONE:-Asia/Shanghai}" \
    python3 - "${payload_file}" <<'PY'
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

payload_path = sys.argv[1]
timezone_name = os.environ.get("AUTO_DEPLOY_REPORT_TIMEZONE") or "Asia/Shanghai"
try:
    tz = ZoneInfo(timezone_name)
except Exception:
    tz = ZoneInfo("Asia/Shanghai")
ended_at = datetime.fromisoformat(os.environ["AUTO_DEPLOY_REPORT_ENDED_AT"].replace("Z", "+00:00"))
date_label = f"{ended_at.astimezone(tz).month}/{ended_at.astimezone(tz).day}"
status = os.environ["AUTO_DEPLOY_REPORT_STATUS"]
subject = f"SupportPortal Report {date_label}"
if status != "success":
    subject = f"[Failed] {subject}"

body = "\n".join(
    [
        "运行摘要",
        f"运行状态：{status}",
        f"执行模式：{os.environ['AUTO_DEPLOY_REPORT_EXECUTION_MODE']}",
        f"主机：{os.environ['AUTO_DEPLOY_REPORT_HOST']}",
        f"分支：{os.environ['DEPLOY_BRANCH']}",
        f"当前提交：{os.environ['AUTO_DEPLOY_REPORT_LOCAL_COMMIT']}",
        f"远端提交：{os.environ['AUTO_DEPLOY_REPORT_REMOTE_COMMIT']}",
        f"失败步骤：{os.environ['AUTO_DEPLOY_REPORT_FAILED_STEP']}",
        f"开始时间（UTC）：{os.environ['AUTO_DEPLOY_REPORT_STARTED_AT']}",
        f"结束时间（UTC）：{os.environ['AUTO_DEPLOY_REPORT_ENDED_AT']}",
        f"耗时（秒）：{os.environ['AUTO_DEPLOY_REPORT_DURATION_SECONDS']}",
        "",
        "健康检查",
        f"Internal：{os.environ['AUTO_DEPLOY_REPORT_INTERNAL_STATUS']}",
        os.environ["AUTO_DEPLOY_REPORT_INTERNAL_DETAIL"] or "(no internal health detail)",
        f"External：{os.environ['AUTO_DEPLOY_REPORT_EXTERNAL_STATUS']}",
        os.environ["AUTO_DEPLOY_REPORT_EXTERNAL_DETAIL"] or "(no external health detail)",
        "",
        "服务状态",
        "fallback: helper payload generation failed",
        "",
        "AI 日志分析",
        "AI analysis unavailable: fallback payload used.",
        "",
        "可疑原始日志",
        os.environ["AUTO_DEPLOY_REPORT_LOG_TAIL"] or "(no run log tail)",
    ]
)

payload = {
    "FromEmailAddress": os.environ["DEPLOY_ALERT_FROM"],
    "Destination": {
        "ToAddresses": [
            item.strip()
            for item in os.environ["DEPLOY_ALERT_TO"].split(",")
            if item.strip()
        ]
    },
    "Content": {
        "Simple": {
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        }
    },
}

with open(payload_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False)
PY
  then
    return 1
  fi
}

send_run_report() {
  local status="$1"
  local payload_file context_file report_status report_end_epoch run_log_tail

  if [[ -z "${DEPLOY_ALERT_TO}" || -z "${DEPLOY_ALERT_FROM}" || -z "${DEPLOY_AWS_REGION}" ]]; then
    warn "Skipping daily report because DEPLOY_ALERT_TO, DEPLOY_ALERT_FROM, or DEPLOY_AWS_REGION is missing."
    return 1
  fi

  if ! command -v aws >/dev/null 2>&1; then
    warn "Skipping daily report because aws CLI is not installed."
    return 1
  fi

  payload_file="$(mktemp "${TMPDIR:-/tmp}/supportportal-auto-deploy-email.XXXXXX")"
  context_file="$(mktemp "${TMPDIR:-/tmp}/supportportal-auto-deploy-context.XXXXXX")"
  report_status="success"
  if (( status != 0 )); then
    report_status="failed"
  fi
  report_end_epoch="$(date +%s)"
  run_log_tail="$(tail -n 120 "${LOG_FILE}" 2>/dev/null || true)"

  if ! AUTO_DEPLOY_REPORT_STATUS="${report_status}" \
    AUTO_DEPLOY_REPORT_HOST="${CURRENT_HOST}" \
    AUTO_DEPLOY_REPORT_LOCAL_COMMIT="${LOCAL_COMMIT}" \
    AUTO_DEPLOY_REPORT_REMOTE_COMMIT="${REMOTE_COMMIT}" \
    AUTO_DEPLOY_REPORT_STARTED_AT="${RUN_STARTED_AT_UTC}" \
    AUTO_DEPLOY_REPORT_ENDED_AT="$(current_utc_timestamp)" \
    AUTO_DEPLOY_REPORT_DURATION_SECONDS="$(( report_end_epoch - RUN_START_EPOCH ))" \
    AUTO_DEPLOY_REPORT_FAILED_STEP="${CURRENT_STEP}" \
    AUTO_DEPLOY_REPORT_EXECUTION_MODE="${EXECUTION_MODE}" \
    AUTO_DEPLOY_REPORT_INTERNAL_STATUS="${INTERNAL_HEALTH_STATUS}" \
    AUTO_DEPLOY_REPORT_INTERNAL_DETAIL="${INTERNAL_HEALTH_DETAIL}" \
    AUTO_DEPLOY_REPORT_EXTERNAL_STATUS="${EXTERNAL_HEALTH_STATUS}" \
    AUTO_DEPLOY_REPORT_EXTERNAL_DETAIL="${EXTERNAL_HEALTH_DETAIL}" \
    AUTO_DEPLOY_REPORT_LOG_TAIL="${run_log_tail}" \
    AUTO_DEPLOY_REPORT_TIMEZONE="${REPORT_TIMEZONE:-Asia/Shanghai}" \
    python3 - "${context_file}" <<'PY'
import json
import os
import sys

payload = {
    "status": os.environ["AUTO_DEPLOY_REPORT_STATUS"],
    "execution_mode": os.environ["AUTO_DEPLOY_REPORT_EXECUTION_MODE"],
    "host": os.environ["AUTO_DEPLOY_REPORT_HOST"],
    "branch": os.environ["DEPLOY_BRANCH"],
    "local_commit": os.environ["AUTO_DEPLOY_REPORT_LOCAL_COMMIT"],
    "remote_commit": os.environ["AUTO_DEPLOY_REPORT_REMOTE_COMMIT"],
    "failed_step": os.environ["AUTO_DEPLOY_REPORT_FAILED_STEP"],
    "domain": os.environ["DEPLOY_DOMAIN"],
    "started_at_utc": os.environ["AUTO_DEPLOY_REPORT_STARTED_AT"],
    "ended_at_utc": os.environ["AUTO_DEPLOY_REPORT_ENDED_AT"],
    "duration_seconds": int(os.environ["AUTO_DEPLOY_REPORT_DURATION_SECONDS"] or "0"),
    "internal_health_status": os.environ["AUTO_DEPLOY_REPORT_INTERNAL_STATUS"],
    "internal_health_detail": os.environ["AUTO_DEPLOY_REPORT_INTERNAL_DETAIL"],
    "external_health_status": os.environ["AUTO_DEPLOY_REPORT_EXTERNAL_STATUS"],
    "external_health_detail": os.environ["AUTO_DEPLOY_REPORT_EXTERNAL_DETAIL"],
    "run_log_tail": os.environ["AUTO_DEPLOY_REPORT_LOG_TAIL"],
    "report_timezone": os.environ["AUTO_DEPLOY_REPORT_TIMEZONE"],
}

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False)
PY
  then
    rm -f "${payload_file}" "${context_file}"
    return 1
  fi

  if ! python3 "${REPORT_HELPER}" \
    --context-file "${context_file}" \
    --output-file "${payload_file}" \
    --project-root "${PROJECT_ROOT}" \
    --env-file "${ENV_FILE}" \
    --compose-file "${COMPOSE_FILE}"; then
    warn "Daily report helper failed. Falling back to minimal payload."
    build_fallback_report_payload "${status}" "${payload_file}" || {
      rm -f "${payload_file}" "${context_file}"
      return 1
    }
  fi

  if aws sesv2 send-email \
    --region "${DEPLOY_AWS_REGION}" \
    --cli-input-json "file://${payload_file}" \
    --no-cli-pager >/dev/null; then
    log "Daily report sent via SES to ${DEPLOY_ALERT_TO}."
  else
    warn "SES daily report sending failed."
    rm -f "${payload_file}" "${context_file}"
    return 1
  fi

  rm -f "${payload_file}" "${context_file}"
}

cleanup() {
  local status="$1"
  if (( EXIT_HANDLER_ACTIVE )); then
    return
  fi
  EXIT_HANDLER_ACTIVE=1

  if (( status != 0 )); then
    log "Run failed during step: ${CURRENT_STEP}"
  fi
  send_run_report "${status}" || true

  if [[ -n "${LOG_FILE}" && -f "${LOG_FILE}" ]]; then
    rm -f "${LOG_FILE}"
  fi
}

trap 'cleanup $?' EXIT

main() {
  local current_branch local_head remote_head

  setup_logging
  RUN_STARTED_AT_UTC="$(current_utc_timestamp)"
  RUN_START_EPOCH="$(date +%s)"
  CURRENT_HOST="$(current_hostname)"
  REPORT_TIMEZONE="$(resolve_env_value DEPLOY_REPORT_TIMEZONE)"
  REPORT_TIMEZONE="${REPORT_TIMEZONE:-Asia/Shanghai}"

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
  LOCAL_COMMIT="$(git rev-parse --short HEAD)"
  log "Current git state: branch=${current_branch} commit=${LOCAL_COMMIT}"

  CURRENT_STEP="Fetch remote refs"
  git fetch origin --prune
  git show-ref --verify --quiet "refs/remotes/origin/${DEPLOY_BRANCH}" \
    || fail "Remote branch not found: origin/${DEPLOY_BRANCH}"
  REMOTE_COMMIT="$(git rev-parse --short "origin/${DEPLOY_BRANCH}")"

  CURRENT_STEP="Validate deploy ancestry"
  local_head="$(git rev-parse HEAD)"
  remote_head="$(git rev-parse "origin/${DEPLOY_BRANCH}")"
  if [[ "${local_head}" != "${remote_head}" ]] \
    && ! git merge-base --is-ancestor "${local_head}" "origin/${DEPLOY_BRANCH}"; then
    fail "Local ${DEPLOY_BRANCH} is not a clean ancestor of origin/${DEPLOY_BRANCH}; manual intervention required."
  fi
  EXECUTION_MODE="deploy"
  log "Execution mode: ${EXECUTION_MODE}"

  local deploy_build_ref deploy_build_time deploy_runtime_image
  deploy_build_ref="$(git rev-parse --short=12 "origin/${DEPLOY_BRANCH}")"
  deploy_build_time="$(current_utc_timestamp)"
  deploy_runtime_image="localhost/supportportal-app:${deploy_build_ref}"
  log "Deploy build metadata: ref=${deploy_build_ref} image=${deploy_runtime_image}"

  CURRENT_STEP="Run deploy script"
  APP_BUILD_REF="${deploy_build_ref}" \
    APP_BUILD_TIME="${deploy_build_time}" \
    APP_RUNTIME_IMAGE="${deploy_runtime_image}" \
    DEPLOY_LOCK_ALREADY_HELD=1 \
    DEPLOY_LOCK_FILE="${LOCK_FILE}" \
    "${DEPLOY_SCRIPT}" \
    --branch "${DEPLOY_BRANCH}" \
    --domain "${DEPLOY_DOMAIN}" \
    --daily \
    --skip-split
  LOCAL_COMMIT="$(git rev-parse --short HEAD)"
  INTERNAL_HEALTH_STATUS="ok"
  INTERNAL_HEALTH_DETAIL="Validated by deploy_surfaces_ec2.sh"
  EXTERNAL_HEALTH_STATUS="ok"
  EXTERNAL_HEALTH_DETAIL="Validated by deploy_surfaces_ec2.sh"
  CURRENT_STEP="Completed"
  log "Daily deploy finished successfully."
}

main "$@"
