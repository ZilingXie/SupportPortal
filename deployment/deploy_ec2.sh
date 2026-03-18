#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/deployment/docker-compose.single-host.yml"
ENV_FILE="${PROJECT_ROOT}/.env"

DOMAIN="support.stellarix.space"
TARGET_BRANCH=""
SKIP_PULL=0
SKIP_EXTERNAL_CHECK=0
FOLLOW_LOGS=0

log() {
  printf '[deploy] %s\n' "$*"
}

fail() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  ./deployment/deploy_ec2.sh [options]

Options:
  -b, --branch <branch>      Deploy from the given git branch (default: current branch)
  -d, --domain <domain>      External domain for HTTPS health check (default: support.stellarix.space)
      --skip-pull            Skip git fetch/pull
      --skip-external-check  Skip https://<domain>/health check
      --logs                 Follow key service logs after deployment
  -h, --help                 Show help

Examples:
  ./deployment/deploy_ec2.sh
  ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space
  ./deployment/deploy_ec2.sh --skip-pull --logs
EOF
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

export_env_value() {
  local key="$1"
  local value="$2"
  printf -v "${key}" '%s' "${value}"
  export "${key}"
}

prepare_compose_env() {
  local ticket_db_dsn pgvector_dsn ticket_schema pgvector_schema pgvector_table
  ticket_db_dsn="$(resolve_env_value TICKET_DB_DSN)"
  pgvector_dsn="$(resolve_env_value PGVECTOR_DSN)"

  if [[ -z "${ticket_db_dsn}" && -n "${pgvector_dsn}" ]]; then
    export_env_value TICKET_DB_DSN "${pgvector_dsn}"
    ticket_db_dsn="${pgvector_dsn}"
    log "TICKET_DB_DSN is missing in ${ENV_FILE}; reusing PGVECTOR_DSN for deploy."
  fi

  if [[ -z "${pgvector_dsn}" && -n "${ticket_db_dsn}" ]]; then
    export_env_value PGVECTOR_DSN "${ticket_db_dsn}"
    pgvector_dsn="${ticket_db_dsn}"
    log "PGVECTOR_DSN is missing in ${ENV_FILE}; reusing TICKET_DB_DSN for deploy."
  fi

  [[ -n "${ticket_db_dsn}" ]] || fail "Missing TICKET_DB_DSN in ${ENV_FILE}. Set it to your AWS Postgres DSN."
  [[ -n "${pgvector_dsn}" ]] || fail "Missing PGVECTOR_DSN in ${ENV_FILE}. Set it to your AWS Postgres DSN."

  ticket_schema="$(resolve_env_value TICKET_DB_SCHEMA)"
  pgvector_schema="$(resolve_env_value PGVECTOR_SCHEMA)"
  pgvector_table="$(resolve_env_value PGVECTOR_TABLE)"

  if [[ -z "${ticket_schema}" ]]; then
    export_env_value TICKET_DB_SCHEMA "supportportal"
    log "TICKET_DB_SCHEMA is missing in ${ENV_FILE}; defaulting to supportportal."
  fi

  if [[ -z "${pgvector_schema}" ]]; then
    export_env_value PGVECTOR_SCHEMA "supportportal"
    log "PGVECTOR_SCHEMA is missing in ${ENV_FILE}; defaulting to supportportal."
  fi

  if [[ -z "${pgvector_table}" ]]; then
    export_env_value PGVECTOR_TABLE "docagent_chunks"
    log "PGVECTOR_TABLE is missing in ${ENV_FILE}; defaulting to docagent_chunks."
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -b|--branch)
        [[ $# -ge 2 ]] || fail "--branch requires a value"
        TARGET_BRANCH="$2"
        shift 2
        ;;
      -d|--domain)
        [[ $# -ge 2 ]] || fail "--domain requires a value"
        DOMAIN="$2"
        shift 2
        ;;
      --skip-pull)
        SKIP_PULL=1
        shift
        ;;
      --skip-external-check)
        SKIP_EXTERNAL_CHECK=1
        shift
        ;;
      --logs)
        FOLLOW_LOGS=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown option: $1 (use --help)"
        ;;
    esac
  done
}

resolve_port() {
  local port
  port="$(awk -F= '/^[[:space:]]*NGINX_HOST_PORT[[:space:]]*=/{v=$2} END{gsub(/[[:space:]\r"]/,"",v); print v}' "${ENV_FILE}")"
  if [[ -z "${port}" ]]; then
    port="8080"
  fi
  printf '%s\n' "${port}"
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

show_compose_diagnostics() {
  log "Service status after failed health check:"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps || true
  log "Recent service logs:"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs --tail=80 api nginx rag_api ws_gateway worker rag_worker || true
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

main() {
  parse_args "$@"

  require_cmd git
  require_cmd docker
  require_cmd curl

  [[ -f "${COMPOSE_FILE}" ]] || fail "Compose file not found: ${COMPOSE_FILE}"

  cd "${PROJECT_ROOT}"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Not inside a git repository: ${PROJECT_ROOT}"

  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f "${PROJECT_ROOT}/.env.example" ]]; then
      cp "${PROJECT_ROOT}/.env.example" "${ENV_FILE}"
      log "Created ${ENV_FILE} from .env.example. Please ensure secrets are correct."
    else
      fail "Missing ${ENV_FILE} and .env.example"
    fi
  fi

  prepare_compose_env

  local current_branch target_branch
  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  [[ "${current_branch}" != "HEAD" ]] || fail "Detached HEAD detected. Checkout a branch first."
  target_branch="${TARGET_BRANCH:-${current_branch}}"

  if [[ "${SKIP_PULL}" -eq 0 ]]; then
    if [[ -n "$(git status --porcelain)" ]]; then
      fail "Working tree is not clean. Commit/stash changes before deploy, or use --skip-pull."
    fi
    log "Fetching latest refs from origin..."
    git fetch origin --prune
    git show-ref --verify --quiet "refs/remotes/origin/${target_branch}" || fail "Remote branch not found: origin/${target_branch}"
    if [[ "${current_branch}" != "${target_branch}" ]]; then
      log "Switching branch ${current_branch} -> ${target_branch}"
      git checkout "${target_branch}"
    fi
    log "Pulling latest code from origin/${target_branch}..."
    git pull --ff-only origin "${target_branch}"
  else
    log "Skipping git pull."
  fi

  local host_port internal_url external_url health_timeout_seconds health_retry_interval_seconds
  host_port="$(resolve_port)"
  health_timeout_seconds="$(resolve_positive_integer DEPLOY_HEALTH_TIMEOUT_SECONDS 90)"
  health_retry_interval_seconds="$(resolve_positive_integer DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS 2)"

  log "Stopping services..."
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" down
  log "Starting services (build + detached)..."
  if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --build; then
    show_compose_diagnostics
    fail "docker compose up failed"
  fi

  log "Current service status:"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps

  internal_url="http://127.0.0.1:${host_port}/health"
  log "Checking internal health: ${internal_url}"
  if ! wait_for_http_ok "Internal" "${internal_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}"; then
    show_compose_diagnostics
    fail "Internal health check failed after ${health_timeout_seconds}s: ${internal_url}"
  fi

  if [[ "${SKIP_EXTERNAL_CHECK}" -eq 0 ]]; then
    external_url="https://${DOMAIN}/health"
    log "Checking external health: ${external_url}"
    wait_for_http_ok "External" "${external_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}" \
      || fail "External health check failed after ${health_timeout_seconds}s: ${external_url}"
  else
    log "Skipping external health check."
  fi

  if [[ "${FOLLOW_LOGS}" -eq 1 ]]; then
    log "Following logs (Ctrl+C to exit)..."
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs -f --tail=120 api ws_gateway worker nginx rag_api rag_worker
  fi

  log "Deploy finished."
}

main "$@"
