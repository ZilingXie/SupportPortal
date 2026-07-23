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
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-${PROJECT_ROOT}/.deploy_ec2.lock}"
DEPLOY_LOCK_ALREADY_HELD="${DEPLOY_LOCK_ALREADY_HELD:-0}"
ROLLBACK_IMAGE=""
PREVIOUS_IMAGE=""
PREVIOUS_IMAGE_ID=""
PREVIOUS_BUILD_REF=""
PREVIOUS_BUILD_TIME=""
PREVIOUS_PROMPT_RELEASE_ID=""
CANDIDATE_PROMPT_RELEASE_ID=""
CANDIDATE_PROMPT_RELEASE_CREATED="false"

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

acquire_deploy_lock() {
  if [[ "${DEPLOY_LOCK_ALREADY_HELD}" == "1" ]]; then
    log "Using pre-acquired deploy lock: ${DEPLOY_LOCK_FILE}"
    return 0
  fi

  require_cmd flock
  mkdir -p "$(dirname -- "${DEPLOY_LOCK_FILE}")"
  exec 9>"${DEPLOY_LOCK_FILE}"
  flock -n 9 || fail "Another deployment is already running (lock: ${DEPLOY_LOCK_FILE})"
  log "Acquired deploy lock: ${DEPLOY_LOCK_FILE}"
}

git_head_summary() {
  git show -s --format='%h %s' HEAD
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

prepare_build_metadata() {
  export_env_value APP_BUILD_REF "$(git rev-parse --short=12 HEAD)"
  export_env_value APP_BUILD_TIME "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  export_env_value APP_RUNTIME_IMAGE "localhost/supportportal-app:${APP_BUILD_REF}"

  log "Build ref: ${APP_BUILD_REF}"
  log "Build time: ${APP_BUILD_TIME}"
  log "Runtime image: ${APP_RUNTIME_IMAGE}"
}

read_container_env_value() {
  local container_id="$1"
  local key="$2"
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${container_id}" 2>/dev/null \
    | awk -F= -v key="${key}" '$1 == key { sub("^[^=]*=", "", $0); print; exit }'
}

capture_previous_runtime() {
  local api_container
  api_container="$(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps -q api 2>/dev/null || true)"
  if [[ -z "${api_container}" ]]; then
    log "No running API container found; automatic image rollback is unavailable for this deploy."
    return 0
  fi

  PREVIOUS_IMAGE_ID="$(docker inspect --format '{{.Image}}' "${api_container}" 2>/dev/null || true)"
  PREVIOUS_IMAGE="$(docker inspect --format '{{.Config.Image}}' "${api_container}" 2>/dev/null || true)"
  PREVIOUS_BUILD_REF="$(read_container_env_value "${api_container}" APP_BUILD_REF || true)"
  PREVIOUS_BUILD_TIME="$(read_container_env_value "${api_container}" APP_BUILD_TIME || true)"
  PREVIOUS_PROMPT_RELEASE_ID="$(read_container_env_value "${api_container}" PROMPT_RELEASE_ID || true)"

  if [[ -z "${PREVIOUS_IMAGE_ID}" ]]; then
    log "Running API image ID could not be resolved; automatic image rollback is unavailable for this deploy."
    return 0
  fi

  if [[ -z "${PREVIOUS_BUILD_REF}" && -n "${PREVIOUS_IMAGE}" ]]; then
    PREVIOUS_BUILD_REF="${PREVIOUS_IMAGE##*:}"
  fi

  ROLLBACK_IMAGE="localhost/supportportal-app:rollback-${APP_BUILD_REF}-$$"
  docker image tag "${PREVIOUS_IMAGE_ID}" "${ROLLBACK_IMAGE}"
  log "Saved running API image for rollback: ${ROLLBACK_IMAGE}"
}

cleanup_rollback_image() {
  if [[ -n "${ROLLBACK_IMAGE}" ]]; then
    docker image rm -f "${ROLLBACK_IMAGE}" >/dev/null 2>&1 || true
  fi
}

restore_previous_stack() {
  local internal_url="$1"
  local timeout_seconds="$2"
  local retry_interval_seconds="$3"

  if [[ -z "${ROLLBACK_IMAGE}" ]]; then
    log "New stack failed and no previous image ID is available for automatic restore."
    return 1
  fi

  log "New stack failed; restoring previous image ${PREVIOUS_IMAGE:-${PREVIOUS_IMAGE_ID}}."
  export_env_value APP_RUNTIME_IMAGE "${ROLLBACK_IMAGE}"
  export_env_value APP_BUILD_REF "${PREVIOUS_BUILD_REF:-previous}"
  export_env_value APP_BUILD_TIME "${PREVIOUS_BUILD_TIME}"
  export_env_value PROMPT_RELEASE_ID "${PREVIOUS_PROMPT_RELEASE_ID}"
  export_env_value PROMPT_RELEASE_REQUIRED "$([[ -n "${PREVIOUS_PROMPT_RELEASE_ID}" ]] && printf true || printf false)"

  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" down >/dev/null 2>&1 || true
  if docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --no-build \
    && wait_for_http_ok "Restored internal" "${internal_url}" "${timeout_seconds}" "${retry_interval_seconds}"; then
    log "Restored previous image ${PREVIOUS_IMAGE:-${PREVIOUS_IMAGE_ID}}."
    return 0
  fi

  log "Failed to restore previous image ${PREVIOUS_IMAGE:-${PREVIOUS_IMAGE_ID}}."
  return 1
}

run_prompt_release_command() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" run --rm --no-deps api \
    python -m backend.scripts.prompt_release "$@"
}

prepare_candidate_prompt_release() {
  local output
  output="$(run_prompt_release_command prepare --build-ref "${APP_BUILD_REF}" --output shell | tail -n 1)"
  IFS=$'\t' read -r CANDIDATE_PROMPT_RELEASE_ID CANDIDATE_PROMPT_RELEASE_CREATED <<<"${output}"
  [[ -n "${CANDIDATE_PROMPT_RELEASE_ID}" ]] || return 1
  [[ "${CANDIDATE_PROMPT_RELEASE_CREATED}" == "true" || "${CANDIDATE_PROMPT_RELEASE_CREATED}" == "false" ]] || return 1
  export_env_value PROMPT_RELEASE_ID "${CANDIDATE_PROMPT_RELEASE_ID}"
  export_env_value PROMPT_RELEASE_REQUIRED "true"
  log "Prompt Release candidate: ${CANDIDATE_PROMPT_RELEASE_ID} created=${CANDIDATE_PROMPT_RELEASE_CREATED}"
}

mark_candidate_prompt_release_failed() {
  local reason="$1"
  if [[ "${CANDIDATE_PROMPT_RELEASE_CREATED}" != "true" || -z "${CANDIDATE_PROMPT_RELEASE_ID}" ]]; then
    return 0
  fi
  run_prompt_release_command fail --release-id "${CANDIDATE_PROMPT_RELEASE_ID}" --reason "${reason}" >/dev/null
}

activate_candidate_prompt_release() {
  run_prompt_release_command activate --release-id "${CANDIDATE_PROMPT_RELEASE_ID}" >/dev/null || return 1
  log "Activated Prompt Release ${CANDIDATE_PROMPT_RELEASE_ID}."
}

verify_prompt_runtime_services() {
  local service container_id actual_release
  for service in api rag_api rag_worker worker_query worker_aux; do
    container_id="$(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps -q "${service}")"
    [[ -n "${container_id}" ]] || return 1
    actual_release="$(read_container_env_value "${container_id}" PROMPT_RELEASE_ID || true)"
    [[ "${actual_release}" == "${CANDIDATE_PROMPT_RELEASE_ID}" ]] || return 1
    docker logs "${container_id}" 2>&1 \
      | grep -F "prompt_runtime_loaded service=${service} release_id=${CANDIDATE_PROMPT_RELEASE_ID}" >/dev/null \
      || return 1
  done
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T api \
    python -c 'import json,sys,urllib.request; p=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5)); sys.exit(0 if p.get("app_build",{}).get("ref")==sys.argv[1] and p.get("prompt_runtime",{}).get("release_id")==sys.argv[2] else 1)' \
    "${APP_BUILD_REF}" "${CANDIDATE_PROMPT_RELEASE_ID}" \
    || return 1
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T rag_api \
    python -c 'import json,sys,urllib.request; p=json.load(urllib.request.urlopen("http://127.0.0.1:8020/health", timeout=5)); sys.exit(0 if p.get("app_build",{}).get("ref")==sys.argv[1] and p.get("prompt_runtime",{}).get("release_id")==sys.argv[2] else 1)' \
    "${APP_BUILD_REF}" "${CANDIDATE_PROMPT_RELEASE_ID}" \
    || return 1
}

prepare_compose_env() {
  local ticket_db_dsn pgvector_dsn ticket_schema pgvector_schema pgvector_table pgvector_dim
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
  pgvector_dim="$(resolve_env_value PGVECTOR_DIM)"

  if [[ -z "${ticket_schema}" ]]; then
    export_env_value TICKET_DB_SCHEMA "supportportal"
    log "TICKET_DB_SCHEMA is missing in ${ENV_FILE}; defaulting to supportportal."
  fi

  if [[ -z "${pgvector_schema}" ]]; then
    export_env_value PGVECTOR_SCHEMA "supportportal"
    log "PGVECTOR_SCHEMA is missing in ${ENV_FILE}; defaulting to supportportal."
  fi

  if [[ -z "${pgvector_table}" ]]; then
    export_env_value PGVECTOR_TABLE "docagent_chunks_bge_m3_1024"
    pgvector_table="docagent_chunks_bge_m3_1024"
    log "PGVECTOR_TABLE is missing in ${ENV_FILE}; defaulting to docagent_chunks_bge_m3_1024."
  fi

  if [[ -z "${pgvector_dim}" ]]; then
    export_env_value PGVECTOR_DIM "1024"
    pgvector_dim="1024"
    log "PGVECTOR_DIM is missing in ${ENV_FILE}; defaulting to 1024."
  fi

  if [[ "${pgvector_table}" == "docagent" || "${pgvector_table}" == "docagent_chunks" || "${pgvector_table}" == "docagent_chunks_qwen3_1024" ]]; then
    log "PGVECTOR_TABLE=${pgvector_table} looks like a legacy table name. Current default is docagent_chunks_bge_m3_1024."
  fi

  log "Effective vector config: schema=${pgvector_schema:-supportportal} table=${pgvector_table} dim=${pgvector_dim}"
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

resolve_non_negative_integer() {
  local key="$1"
  local default_value="$2"
  local value

  value="$(resolve_env_value "${key}")"
  if [[ "${value}" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "${value}"
  else
    printf '%s\n' "${default_value}"
  fi
}

resolve_disk_check_path() {
  local configured_path
  configured_path="$(resolve_env_value DEPLOY_DISK_CHECK_PATH)"
  if [[ -n "${configured_path}" ]]; then
    printf '%s\n' "${configured_path}"
    return 0
  fi

  if [[ -d "/var/lib/containerd" ]]; then
    printf '%s\n' "/var/lib/containerd"
  elif [[ -d "/var/lib/docker" ]]; then
    printf '%s\n' "/var/lib/docker"
  else
    printf '%s\n' "/"
  fi
}

available_disk_kb() {
  local target_path="$1"
  df -Pk "${target_path}" | awk 'NR==2 { print $4 }'
}

format_gib_from_kb() {
  local available_kb="$1"
  awk -v available_kb="${available_kb}" 'BEGIN { printf "%.1f", available_kb / 1024 / 1024 }'
}

ensure_minimum_free_disk_space() {
  local disk_check_path threshold_gb threshold_kb before_kb after_kb before_gib after_gib

  threshold_gb="$(resolve_non_negative_integer DEPLOY_MIN_FREE_DISK_GB 40)"
  if (( threshold_gb == 0 )); then
    log "Disk preflight disabled because DEPLOY_MIN_FREE_DISK_GB=0."
    return 0
  fi

  disk_check_path="$(resolve_disk_check_path)"
  [[ -e "${disk_check_path}" ]] || fail "Disk check path does not exist: ${disk_check_path}"

  before_kb="$(available_disk_kb "${disk_check_path}")"
  [[ "${before_kb}" =~ ^[0-9]+$ ]] || fail "Unable to determine free disk space for ${disk_check_path}."

  threshold_kb=$(( threshold_gb * 1024 * 1024 ))
  before_gib="$(format_gib_from_kb "${before_kb}")"
  log "Available disk before build on ${disk_check_path}: ${before_gib} GiB"

  if (( before_kb >= threshold_kb )); then
    return 0
  fi

  log "Pruning Docker cache before build because available disk on ${disk_check_path} is below ${threshold_gb} GiB."
  docker builder prune -af
  docker image prune -af

  after_kb="$(available_disk_kb "${disk_check_path}")"
  [[ "${after_kb}" =~ ^[0-9]+$ ]] || fail "Unable to determine free disk space for ${disk_check_path} after Docker cache cleanup."

  after_gib="$(format_gib_from_kb "${after_kb}")"
  log "Available disk after Docker cache cleanup on ${disk_check_path}: ${after_gib} GiB"

  if (( after_kb < threshold_kb )); then
    fail "Available disk space on ${disk_check_path} is ${after_gib} GiB, below required ${threshold_gb} GiB even after docker cache cleanup."
  fi
}

show_compose_diagnostics() {
  log "Service status after failed deploy step:"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps || true
  log "Recent service logs:"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs --tail=80 api nginx rag_api ws_gateway worker_query worker_aux rag_worker || true
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
  require_cmd df
  acquire_deploy_lock

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
  log "Current git state: branch=${current_branch} commit=$(git_head_summary)"
  log "Deploy target branch: ${target_branch}"

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

  log "Resolved deploy commit: branch=$(git rev-parse --abbrev-ref HEAD) commit=$(git_head_summary)"

  prepare_build_metadata

  local host_port internal_url external_url health_timeout_seconds health_retry_interval_seconds
  host_port="$(resolve_port)"
  health_timeout_seconds="$(resolve_positive_integer DEPLOY_HEALTH_TIMEOUT_SECONDS 90)"
  health_retry_interval_seconds="$(resolve_positive_integer DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS 2)"

  ensure_minimum_free_disk_space

  log "Pre-building services before restart..."
  if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" build; then
    show_compose_diagnostics
    fail "docker compose build failed"
  fi

  capture_previous_runtime

  log "Preparing deployment-bound Prompt Release..."
  if ! prepare_candidate_prompt_release; then
    fail "Prompt Release preparation failed; the running stack was not stopped"
  fi

  log "Stopping services..."
  if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" down; then
    show_compose_diagnostics
    mark_candidate_prompt_release_failed "docker compose down failed" || true
    restore_previous_stack "http://127.0.0.1:${host_port}/health" "${health_timeout_seconds}" "${health_retry_interval_seconds}" || true
    fail "docker compose down failed; rollback image retained: ${ROLLBACK_IMAGE:-unavailable}"
  fi
  log "Starting services (detached)..."
  if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d; then
    show_compose_diagnostics
    mark_candidate_prompt_release_failed "docker compose up failed" || true
    restore_previous_stack "http://127.0.0.1:${host_port}/health" "${health_timeout_seconds}" "${health_retry_interval_seconds}" || true
    fail "docker compose up failed"
  fi

  log "Current service status:"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps

  internal_url="http://127.0.0.1:${host_port}/health"
  log "Checking internal health: ${internal_url}"
  if ! wait_for_http_ok "Internal" "${internal_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}"; then
    show_compose_diagnostics
    mark_candidate_prompt_release_failed "internal health check failed" || true
    restore_previous_stack "${internal_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}" || true
    fail "Internal health check failed after ${health_timeout_seconds}s: ${internal_url}"
  fi

  log "Verifying Prompt Release across all Prompt runtime services..."
  if ! verify_prompt_runtime_services; then
    show_compose_diagnostics
    mark_candidate_prompt_release_failed "Prompt runtime service verification failed" || true
    restore_previous_stack "${internal_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}" || true
    fail "Prompt Release verification failed for ${CANDIDATE_PROMPT_RELEASE_ID}"
  fi

  if [[ "${SKIP_EXTERNAL_CHECK}" -eq 0 ]]; then
    external_url="https://${DOMAIN}/health"
    log "Checking external health: ${external_url}"
    if ! wait_for_http_ok "External" "${external_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}"; then
      show_compose_diagnostics
      mark_candidate_prompt_release_failed "external health check failed" || true
      restore_previous_stack "${internal_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}" || true
      fail "External health check failed after ${health_timeout_seconds}s: ${external_url}"
    fi
  else
    log "Skipping external health check."
  fi

  if ! activate_candidate_prompt_release; then
    mark_candidate_prompt_release_failed "Prompt Release activation failed" || true
    restore_previous_stack "${internal_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}" || true
    fail "Prompt Release activation failed for ${CANDIDATE_PROMPT_RELEASE_ID}"
  fi

  cleanup_rollback_image
  ROLLBACK_IMAGE=""

  if [[ "${FOLLOW_LOGS}" -eq 1 ]]; then
    log "Following logs (Ctrl+C to exit)..."
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs -f --tail=120 api ws_gateway worker_query worker_aux nginx rag_api rag_worker
  fi

  log "Deploy finished."
}

main "$@"
