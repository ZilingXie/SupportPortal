#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${BOOTSTRAP_REPO_ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
ENV_FILE="${BOOTSTRAP_ENV_FILE:-${PROJECT_ROOT}/.env}"
SYSTEMD_TEMPLATE_DIR="${BOOTSTRAP_SYSTEMD_TEMPLATE_DIR:-${PROJECT_ROOT}/deployment/systemd}"
SERVICE_TEMPLATE="${BOOTSTRAP_SERVICE_TEMPLATE:-${SYSTEMD_TEMPLATE_DIR}/supportportal-auto-deploy.service}"
TIMER_TEMPLATE="${BOOTSTRAP_TIMER_TEMPLATE:-${SYSTEMD_TEMPLATE_DIR}/supportportal-auto-deploy.timer}"
AUTO_DEPLOY_ETC_DIR="${BOOTSTRAP_AUTO_DEPLOY_ETC_DIR:-/etc/supportportal}"
SYSTEMD_TARGET_DIR="${BOOTSTRAP_SYSTEMD_TARGET_DIR:-/etc/systemd/system}"
TARGET_BRANCH="${BOOTSTRAP_TARGET_BRANCH:-main}"
SYSTEMD_USER="${BOOTSTRAP_SYSTEMD_USER:-${SUDO_USER:-${USER:-ubuntu}}}"

DEPLOY_BRANCH=""
DEPLOY_DOMAIN=""
DEPLOY_ALERT_TO=""
DEPLOY_ALERT_FROM=""
DEPLOY_AWS_REGION=""
CURRENT_STEP="startup"

log() {
  printf '[bootstrap-auto-deploy] %s\n' "$*"
}

warn() {
  printf '[bootstrap-auto-deploy] WARN: %s\n' "$*" >&2
}

fail() {
  printf '[bootstrap-auto-deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing command: $1"
}

run_privileged() {
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  elif [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    fail "sudo is required for privileged setup commands."
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

resolve_first_value() {
  local key value
  for key in "$@"; do
    value="$(resolve_env_value "${key}")"
    if [[ -n "${value}" ]]; then
      printf '%s' "${value}"
      return 0
    fi
  done
  return 1
}

detect_awscli_url() {
  case "$(uname -m)" in
    x86_64)
      printf '%s\n' "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
      ;;
    aarch64|arm64)
      printf '%s\n' "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip"
      ;;
    *)
      fail "Unsupported CPU architecture for AWS CLI install: $(uname -m)"
      ;;
  esac
}

ensure_base_packages() {
  CURRENT_STEP="install base packages"
  log "Installing base packages required for bootstrap."
  run_privileged apt-get update
  run_privileged apt-get install -y curl unzip ca-certificates git python3
}

ensure_aws_cli() {
  local awscli_url tmpdir zip_path

  CURRENT_STEP="install aws cli"
  if command -v aws >/dev/null 2>&1; then
    log "AWS CLI already installed: $(aws --version 2>&1 | head -n 1)"
    return 0
  fi

  awscli_url="$(detect_awscli_url)"
  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/supportportal-awscli.XXXXXX")"
  zip_path="${tmpdir}/awscliv2.zip"

  log "Installing AWS CLI from ${awscli_url}."
  curl -fsSL "${awscli_url}" -o "${zip_path}"
  (
    cd "${tmpdir}"
    unzip -q awscliv2.zip
    run_privileged ./aws/install --update
  )
  rm -rf "${tmpdir}"

  command -v aws >/dev/null 2>&1 || fail "AWS CLI install finished but 'aws' is still unavailable on PATH."
  log "Installed AWS CLI: $(aws --version 2>&1 | head -n 1)"
}

sync_main_checkout() {
  CURRENT_STEP="sync main checkout"
  cd "${PROJECT_ROOT}"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Not inside a git repository: ${PROJECT_ROOT}"
  git fetch origin --prune
  git switch "${TARGET_BRANCH}"
  git pull --ff-only origin "${TARGET_BRANCH}"
  log "Repository synced to ${TARGET_BRANCH}: $(git rev-parse --short HEAD)"
}

load_bootstrap_config() {
  CURRENT_STEP="load deploy bootstrap config"
  [[ -f "${ENV_FILE}" ]] || fail "Missing ${ENV_FILE}. Populate it before running bootstrap."

  DEPLOY_BRANCH="$(resolve_first_value DEPLOY_BRANCH || printf '%s' "${TARGET_BRANCH}")"
  DEPLOY_DOMAIN="$(resolve_first_value DEPLOY_DOMAIN)"
  DEPLOY_ALERT_FROM="$(resolve_first_value DEPLOY_ALERT_FROM ALERT_FROM_EMAIL)"
  DEPLOY_ALERT_TO="$(resolve_first_value DEPLOY_ALERT_TO ALERT_TO_EMAIL)"
  DEPLOY_AWS_REGION="$(resolve_first_value DEPLOY_AWS_REGION AWS_REGION)"

  [[ -n "${DEPLOY_DOMAIN}" ]] || fail "DEPLOY_DOMAIN is required in ${ENV_FILE}."
  [[ -n "${DEPLOY_ALERT_FROM}" ]] || fail "DEPLOY_ALERT_FROM or ALERT_FROM_EMAIL is required in ${ENV_FILE}."
  [[ -n "${DEPLOY_ALERT_TO}" ]] || fail "DEPLOY_ALERT_TO or ALERT_TO_EMAIL is required in ${ENV_FILE}."
  [[ -n "${DEPLOY_AWS_REGION}" ]] || fail "DEPLOY_AWS_REGION or AWS_REGION is required in ${ENV_FILE}."
}

check_aws_credentials() {
  CURRENT_STEP="validate aws credentials"
  if ! aws sts get-caller-identity --no-cli-pager >/dev/null; then
    fail "AWS credentials unavailable. Attach an IAM role to this EC2 instance, then rerun bootstrap."
  fi
}

report_ses_account_mode() {
  local account_json production_enabled
  CURRENT_STEP="inspect ses account mode"

  if ! account_json="$(aws sesv2 get-account --region "${DEPLOY_AWS_REGION}" --no-cli-pager 2>/dev/null)"; then
    warn "Unable to inspect SES account mode. Continuing with identity setup."
    return 0
  fi

  production_enabled="$(
    python3 - "${account_json}" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1] or "{}")
print("true" if payload.get("ProductionAccessEnabled") else "false")
PY
  )"

  if [[ "${production_enabled}" == "true" ]]; then
    log "SES account has production access enabled."
  else
    log "SES account is still in sandbox mode. Keep DEPLOY_ALERT_TO on a verified address until production access is enabled."
  fi
}

ensure_email_identity() {
  local identity="$1"
  CURRENT_STEP="ensure ses email identity ${identity}"

  if aws sesv2 get-email-identity \
    --region "${DEPLOY_AWS_REGION}" \
    --email-identity "${identity}" \
    --no-cli-pager >/dev/null 2>&1; then
    log "SES identity already exists: ${identity}"
    return 0
  fi

  aws sesv2 create-email-identity \
    --region "${DEPLOY_AWS_REGION}" \
    --email-identity "${identity}" \
    --no-cli-pager >/dev/null
  log "Started SES email identity verification for ${identity}."
}

write_auto_deploy_env() {
  local tmpfile target_file
  CURRENT_STEP="write auto deploy env"
  target_file="${AUTO_DEPLOY_ETC_DIR}/auto-deploy.env"
  tmpfile="$(mktemp "${TMPDIR:-/tmp}/supportportal-auto-deploy-env.XXXXXX")"

  cat > "${tmpfile}" <<EOF
DEPLOY_BRANCH=${DEPLOY_BRANCH}
DEPLOY_DOMAIN=${DEPLOY_DOMAIN}
DEPLOY_ALERT_TO=${DEPLOY_ALERT_TO}
DEPLOY_ALERT_FROM=${DEPLOY_ALERT_FROM}
DEPLOY_AWS_REGION=${DEPLOY_AWS_REGION}
EOF

  run_privileged install -d -m 0755 "${AUTO_DEPLOY_ETC_DIR}"
  run_privileged cp "${tmpfile}" "${target_file}"
  run_privileged chmod 0644 "${target_file}"
  rm -f "${tmpfile}"
  log "Wrote auto deploy environment file: ${target_file}"
}

install_systemd_units() {
  local rendered_service target_service target_timer tmp_service
  CURRENT_STEP="install systemd units"
  target_service="${SYSTEMD_TARGET_DIR}/supportportal-auto-deploy.service"
  target_timer="${SYSTEMD_TARGET_DIR}/supportportal-auto-deploy.timer"
  tmp_service="$(mktemp "${TMPDIR:-/tmp}/supportportal-auto-deploy-service.XXXXXX")"

  [[ -f "${SERVICE_TEMPLATE}" ]] || fail "Missing service template: ${SERVICE_TEMPLATE}"
  [[ -f "${TIMER_TEMPLATE}" ]] || fail "Missing timer template: ${TIMER_TEMPLATE}"

  sed \
    -e "s#/opt/supportportal/SupportPortal#${PROJECT_ROOT}#g" \
    -e "s#User=ubuntu#User=${SYSTEMD_USER}#g" \
    "${SERVICE_TEMPLATE}" > "${tmp_service}"

  run_privileged install -d -m 0755 "${SYSTEMD_TARGET_DIR}"
  run_privileged cp "${tmp_service}" "${target_service}"
  run_privileged cp "${TIMER_TEMPLATE}" "${target_timer}"
  rm -f "${tmp_service}"

  run_privileged systemctl daemon-reload
  run_privileged systemctl enable --now supportportal-auto-deploy.timer
  log "Installed systemd service and timer under ${SYSTEMD_TARGET_DIR}."
}

main() {
  log "Bootstrapping EC2 auto deploy from ${PROJECT_ROOT}"
  ensure_base_packages
  require_cmd git
  require_cmd curl
  require_cmd python3
  ensure_aws_cli
  sync_main_checkout
  load_bootstrap_config
  check_aws_credentials
  report_ses_account_mode
  ensure_email_identity "${DEPLOY_ALERT_FROM}"
  if [[ "${DEPLOY_ALERT_TO}" != "${DEPLOY_ALERT_FROM}" ]]; then
    ensure_email_identity "${DEPLOY_ALERT_TO}"
  fi
  write_auto_deploy_env
  install_systemd_units

  log "Bootstrap complete."
  log "Next steps:"
  log "1. Click the SES verification links sent to ${DEPLOY_ALERT_FROM} and ${DEPLOY_ALERT_TO} if they were newly created."
  log "2. Inspect the timer with: systemctl list-timers supportportal-auto-deploy.timer"
  log "3. Optionally trigger one run with: sudo systemctl start supportportal-auto-deploy.service"
}

main "$@"
