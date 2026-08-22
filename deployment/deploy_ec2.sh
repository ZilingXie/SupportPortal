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
TARGET_ENVIRONMENT=""
ROLLBACK_SPLIT=0
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
COMPOSE_PROFILE_ARGS=()
SPLIT_SERVICES=()
SPLIT_COMPOSE_ARGS=()
SPLIT_IMAGE_KEYS=()
SPLIT_PROJECT_NAME=""
SPLIT_DB_SCHEMA_KEY=""
SPLIT_QUEUE_KEY=""
SPLIT_EVENT_KEY=""
SPLIT_RESOURCE_ID=""

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
      --environment <name>   Deploy one split environment: staging, preproduction, production, route-staging, route-preproduction, route-production
      --rollback              Restore the previous image pointers for --environment
      --skip-pull            Skip git fetch/pull
      --skip-external-check  Skip https://<domain>/health check
      --logs                 Follow key service logs after deployment
  -h, --help                 Show help

Examples:
  ./deployment/deploy_ec2.sh
  ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space
  ./deployment/deploy_ec2.sh --skip-pull --logs
  ./deployment/deploy_ec2.sh --environment staging --skip-pull
  ./deployment/deploy_ec2.sh --environment staging --rollback --skip-pull
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

ensure_automation_networks() {
  local network_key network_name
  for network_key in \
    AUTOMATION_EDGE_NETWORK_NAME \
    AUTOMATION_STAGING_INTERNAL_NETWORK_NAME \
    AUTOMATION_PREPRODUCTION_INTERNAL_NETWORK_NAME \
    AUTOMATION_PRODUCTION_INTERNAL_NETWORK_NAME; do
    network_name="$(resolve_env_value "${network_key}")"
    case "${network_key}" in
      AUTOMATION_EDGE_NETWORK_NAME) network_name="${network_name:-supportportal_automation_edge}" ;;
      AUTOMATION_STAGING_INTERNAL_NETWORK_NAME) network_name="${network_name:-supportportal_automation_internal_staging}" ;;
      AUTOMATION_PREPRODUCTION_INTERNAL_NETWORK_NAME) network_name="${network_name:-supportportal_automation_internal_preproduction}" ;;
      AUTOMATION_PRODUCTION_INTERNAL_NETWORK_NAME) network_name="${network_name:-supportportal_automation_internal_production}" ;;
    esac
    if docker network inspect "${network_name}" >/dev/null 2>&1; then
      continue
    fi
    if [[ "${network_key}" != "AUTOMATION_EDGE_NETWORK_NAME" ]]; then
      docker network create --internal "${network_name}" >/dev/null || fail "Unable to create shared automation network: ${network_name}"
    else
      docker network create "${network_name}" >/dev/null || fail "Unable to create shared automation network: ${network_name}"
    fi
    log "Created shared automation network: ${network_name}"
  done
}

export_env_value() {
  local key="$1"
  local value="$2"
  printf -v "${key}" '%s' "${value}"
  export "${key}"
}

resolve_compose_profile_args() {
  local production_dsn staging_dsn
  COMPOSE_PROFILE_ARGS=()
  production_dsn="$(resolve_env_value PRODUCTION_TICKET_DB_DSN)"
  if [[ -z "${production_dsn}" ]]; then
    log "PRODUCTION_TICKET_DB_DSN not set; the /production environment services stay disabled."
    return 0
  fi
  staging_dsn="$(resolve_env_value TICKET_DB_DSN)"
  if [[ -n "${staging_dsn}" && "${production_dsn}" == "${staging_dsn}" ]]; then
    fail "PRODUCTION_TICKET_DB_DSN must differ from TICKET_DB_DSN; refusing to point the /production environment at the staging database"
  fi
  COMPOSE_PROFILE_ARGS=(--profile production)
  log "PRODUCTION_TICKET_DB_DSN detected; enabling compose profile production (/production environment)."
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
  if docker image tag "${PREVIOUS_IMAGE_ID}" "${ROLLBACK_IMAGE}"; then
    log "Saved running API image for rollback: ${ROLLBACK_IMAGE}"
    return 0
  fi

  if [[ "${PREVIOUS_BUILD_REF}" == "${APP_BUILD_REF}" ]] \
    && docker image inspect "${APP_RUNTIME_IMAGE}" >/dev/null 2>&1 \
    && docker image tag "${APP_RUNTIME_IMAGE}" "${ROLLBACK_IMAGE}"; then
    log "Running API image manifest is missing; using the existing same-build image for rollback: ${ROLLBACK_IMAGE}"
    return 0
  fi

  ROLLBACK_IMAGE=""
  log "Running API image could not be preserved for rollback: ${PREVIOUS_IMAGE_ID}"
  return 1
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

  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "${COMPOSE_PROFILE_ARGS[@]-}" down >/dev/null 2>&1 || true
  if docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "${COMPOSE_PROFILE_ARGS[@]-}" up -d --no-build \
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

validate_candidate_prompt_release() {
  run_prompt_release_command validate --release-id "${CANDIDATE_PROMPT_RELEASE_ID}" >/dev/null || return 1
  log "Validated Prompt Release candidate ${CANDIDATE_PROMPT_RELEASE_ID}."
}

mark_candidate_prompt_release_failed() {
  local reason="$1"
  if [[ "${CANDIDATE_PROMPT_RELEASE_CREATED}" != "true" || -z "${CANDIDATE_PROMPT_RELEASE_ID}" ]]; then
    return 0
  fi
  run_prompt_release_command fail --release-id "${CANDIDATE_PROMPT_RELEASE_ID}" --reason "${reason}" >/dev/null
}

activate_candidate_prompt_release() {
  local active_release_id
  if run_prompt_release_command activate --release-id "${CANDIDATE_PROMPT_RELEASE_ID}" >/dev/null; then
    log "Activated Prompt Release ${CANDIDATE_PROMPT_RELEASE_ID}."
    return 0
  fi

  log "Prompt Release activation command failed; reconciling committed database state."
  active_release_id="$(run_prompt_release_command current --output shell | tail -n 1)" || return 1
  if [[ "${active_release_id}" == "${CANDIDATE_PROMPT_RELEASE_ID}" ]]; then
    log "Prompt Release ${CANDIDATE_PROMPT_RELEASE_ID} is already active; treating activation as successful."
    return 0
  fi
  return 1
}

sync_candidate_prompt_release_to_production() {
  if [[ ${#COMPOSE_PROFILE_ARGS[@]} -eq 0 ]]; then
    return 0
  fi
  local target_dsn
  target_dsn="$(resolve_env_value PRODUCTION_TICKET_DB_DSN)"
  [[ -n "${target_dsn}" ]] || fail "PRODUCTION_TICKET_DB_DSN disappeared while the production profile was enabled"
  if ! run_prompt_release_command sync --release-id "${CANDIDATE_PROMPT_RELEASE_ID}" --target-dsn "${target_dsn}" >/dev/null; then
    return 1
  fi
  log "Synced Prompt Release ${CANDIDATE_PROMPT_RELEASE_ID} to the /production database."
  return 0
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
      --environment)
        [[ $# -ge 2 ]] || fail "--environment requires a value"
        TARGET_ENVIRONMENT="$2"
        shift 2
        ;;
      --rollback)
        ROLLBACK_SPLIT=1
        shift
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

manifest_value() {
  local manifest_path="$1"
  local key="$2"
  [[ -f "${manifest_path}" ]] || return 0
  awk -F= -v key="${key}" '$1 == key { sub("^[^=]*=", "", $0); print; exit }' "${manifest_path}"
}

split_environment_config() {
  local environment="$1"
  SPLIT_SERVICES=()
  SPLIT_IMAGE_KEYS=()
  SPLIT_DB_SCHEMA_KEY=""
  SPLIT_QUEUE_KEY=""
  SPLIT_EVENT_KEY=""
  case "${environment}" in
    staging)
      SPLIT_ROUTE_SERVICE="route_staging"
      SPLIT_AUTOMATION_SERVICE="automation_staging"
      SPLIT_PATH="staging"
      SPLIT_IMAGE_KEY="AUTOMATION_STAGING_IMAGE"
      SPLIT_IMAGE_KEYS=(ROUTE_STAGING_IMAGE AUTOMATION_STAGING_IMAGE)
      SPLIT_DB_KEY="AUTOMATION_STAGING_DB_DSN"
      SPLIT_DB_SCHEMA_KEY="AUTOMATION_STAGING_DB_SCHEMA"
      SPLIT_QUEUE_KEY="AUTOMATION_STAGING_QUEUE"
      SPLIT_EVENT_KEY="AUTOMATION_STAGING_EVENT_CHANNEL"
      SPLIT_RESOURCE_ID="staging"
      SPLIT_TOKEN_KEY="ROUTE_STAGING_SERVICE_TOKEN"
      SPLIT_SERVICES=(route_staging automation_staging)
      ;;
    preproduction)
      SPLIT_ROUTE_SERVICE="route_preproduction"
      SPLIT_AUTOMATION_SERVICE="automation_preproduction"
      SPLIT_PATH="preproduction"
      SPLIT_IMAGE_KEY="AUTOMATION_PREPRODUCTION_IMAGE"
      SPLIT_IMAGE_KEYS=(ROUTE_PREPRODUCTION_IMAGE AUTOMATION_PREPRODUCTION_IMAGE)
      SPLIT_DB_KEY="AUTOMATION_PREPRODUCTION_DB_DSN"
      SPLIT_DB_SCHEMA_KEY="AUTOMATION_PREPRODUCTION_DB_SCHEMA"
      SPLIT_QUEUE_KEY="AUTOMATION_PREPRODUCTION_QUEUE"
      SPLIT_EVENT_KEY="AUTOMATION_PREPRODUCTION_EVENT_CHANNEL"
      SPLIT_RESOURCE_ID="preproduction"
      SPLIT_TOKEN_KEY="ROUTE_PREPRODUCTION_SERVICE_TOKEN"
      SPLIT_SERVICES=(route_preproduction automation_preproduction)
      ;;
    production)
      SPLIT_ROUTE_SERVICE="route_production"
      SPLIT_AUTOMATION_SERVICE="automation_production"
      SPLIT_PATH="production"
      SPLIT_IMAGE_KEY="AUTOMATION_PRODUCTION_IMAGE"
      SPLIT_IMAGE_KEYS=(ROUTE_PRODUCTION_IMAGE AUTOMATION_PRODUCTION_IMAGE)
      SPLIT_DB_KEY="AUTOMATION_PRODUCTION_DB_DSN"
      SPLIT_DB_SCHEMA_KEY="AUTOMATION_PRODUCTION_DB_SCHEMA"
      SPLIT_QUEUE_KEY="AUTOMATION_PRODUCTION_QUEUE"
      SPLIT_EVENT_KEY="AUTOMATION_PRODUCTION_EVENT_CHANNEL"
      SPLIT_RESOURCE_ID="production"
      SPLIT_TOKEN_KEY="ROUTE_PRODUCTION_SERVICE_TOKEN"
      SPLIT_SERVICES=(route_production automation_production)
      ;;
    route-staging)
      SPLIT_ROUTE_SERVICE="route_staging"
      SPLIT_AUTOMATION_SERVICE=""
      SPLIT_PATH="staging"
      SPLIT_IMAGE_KEY="ROUTE_STAGING_IMAGE"
      SPLIT_IMAGE_KEYS=(ROUTE_STAGING_IMAGE)
      SPLIT_DB_KEY=""
      SPLIT_RESOURCE_ID="staging"
      SPLIT_TOKEN_KEY="ROUTE_STAGING_SERVICE_TOKEN"
      SPLIT_SERVICES=(route_staging)
      ;;
    route-preproduction)
      SPLIT_ROUTE_SERVICE="route_preproduction"
      SPLIT_AUTOMATION_SERVICE=""
      SPLIT_PATH="preproduction"
      SPLIT_IMAGE_KEY="ROUTE_PREPRODUCTION_IMAGE"
      SPLIT_IMAGE_KEYS=(ROUTE_PREPRODUCTION_IMAGE)
      SPLIT_DB_KEY=""
      SPLIT_RESOURCE_ID="preproduction"
      SPLIT_TOKEN_KEY="ROUTE_PREPRODUCTION_SERVICE_TOKEN"
      SPLIT_SERVICES=(route_preproduction)
      ;;
    route-production)
      SPLIT_ROUTE_SERVICE="route_production"
      SPLIT_AUTOMATION_SERVICE=""
      SPLIT_PATH="production"
      SPLIT_IMAGE_KEY="ROUTE_PRODUCTION_IMAGE"
      SPLIT_IMAGE_KEYS=(ROUTE_PRODUCTION_IMAGE)
      SPLIT_DB_KEY=""
      SPLIT_RESOURCE_ID="production"
      SPLIT_TOKEN_KEY="ROUTE_PRODUCTION_SERVICE_TOKEN"
      SPLIT_SERVICES=(route_production)
      ;;
    *) fail "Unsupported split environment: ${environment}" ;;
  esac
}

deploy_split_environment() {
  local environment="$1"
  local image_value token_value db_value db_schema queue_name event_channel
  local route_image automation_image previous_manifest previous_route_image previous_automation_image
  local current_manifest_route_image current_manifest_automation_image
  split_environment_config "${environment}"
  SPLIT_PROJECT_NAME="supportportal-automation-${environment}"
  SPLIT_COMPOSE_ARGS=(--project-name "${SPLIT_PROJECT_NAME}" --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" --profile automation)
  previous_manifest="${PROJECT_ROOT}/.deployments/${environment}.manifest"
  current_manifest_route_image="$(manifest_value "${previous_manifest}" route_image)"
  current_manifest_automation_image="$(manifest_value "${previous_manifest}" automation_image)"
  previous_route_image=""
  previous_automation_image=""
  route_image="$(resolve_env_value "${SPLIT_IMAGE_KEYS[0]}")"
  automation_image=""
  if [[ ${#SPLIT_IMAGE_KEYS[@]} -gt 1 ]]; then
    automation_image="$(resolve_env_value "${SPLIT_IMAGE_KEYS[1]}")"
  fi
  if [[ "${ROLLBACK_SPLIT}" == "1" ]]; then
    previous_route_image="${current_manifest_route_image}"
    previous_automation_image="${current_manifest_automation_image}"
    route_image="$(manifest_value "${previous_manifest}" previous_route_image)"
    [[ -n "${previous_route_image}" ]] || fail "No previous route image pointer exists for ${environment}"
    [[ -n "${route_image}" ]] || fail "No previous route image pointer exists for ${environment}"
    export_env_value "${SPLIT_IMAGE_KEYS[0]}" "${route_image}"
    if [[ ${#SPLIT_IMAGE_KEYS[@]} -gt 1 ]]; then
      automation_image="$(manifest_value "${previous_manifest}" previous_automation_image)"
      [[ -n "${previous_automation_image}" ]] || fail "No previous automation image pointer exists for ${environment}"
      [[ -n "${automation_image}" ]] || fail "No previous automation image pointer exists for ${environment}"
      export_env_value "${SPLIT_IMAGE_KEYS[1]}" "${automation_image}"
    fi
  else
    previous_route_image="${current_manifest_route_image}"
    previous_automation_image="${current_manifest_automation_image}"
    for image_key in "${SPLIT_IMAGE_KEYS[@]}"; do
      image_value="$(resolve_env_value "${image_key}")"
      [[ -n "${image_value}" && "${image_value}" != *replace* && "${image_value}" == *@sha256:* ]] || fail "${image_key} must be an immutable digest image pointer"
    done
  fi
  token_value="$(resolve_env_value "${SPLIT_TOKEN_KEY}")"
  [[ -n "${token_value}" ]] || fail "${SPLIT_TOKEN_KEY} is required"
  if [[ -n "${SPLIT_DB_KEY}" ]]; then
    db_value="$(resolve_env_value "${SPLIT_DB_KEY}")"
    [[ -n "${db_value}" ]] || fail "${SPLIT_DB_KEY} is required"
    db_schema="$(resolve_env_value "${SPLIT_DB_SCHEMA_KEY}")"
    queue_name="$(resolve_env_value "${SPLIT_QUEUE_KEY}")"
    event_channel="$(resolve_env_value "${SPLIT_EVENT_KEY}")"
    [[ -n "${db_schema}" && -n "${queue_name}" && -n "${event_channel}" ]] || fail "${environment} DB/schema/queue/event identity is incomplete"
  fi
  if [[ "${environment}" == "production" || "${environment}" == "route-production" ]]; then
    [[ "${DEPLOY_PRODUCTION_APPROVED:-0}" == "1" ]] || fail "Production split deployment requires DEPLOY_PRODUCTION_APPROVED=1"
  fi
  ensure_automation_networks
  log "Deploying split environment ${environment} as project ${SPLIT_PROJECT_NAME} with route=${route_image} automation=${automation_image:-n/a}"
  docker compose "${SPLIT_COMPOSE_ARGS[@]}" pull "${SPLIT_SERVICES[@]}"
  docker compose "${SPLIT_COMPOSE_ARGS[@]}" up -d --no-build "${SPLIT_SERVICES[@]}"
  for service in "${SPLIT_SERVICES[@]}"; do
    wait_for_split_service "${service}" "$(resolve_positive_integer DEPLOY_HEALTH_TIMEOUT_SECONDS 90)" "$(resolve_positive_integer DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS 2)" || fail "${environment} ${service} health check failed"
  done
  mkdir -p "${PROJECT_ROOT}/.deployments"
  printf 'environment=%s\nproject=%s\nroute_image=%s\nautomation_image=%s\nprevious_route_image=%s\nprevious_automation_image=%s\ncommit=%s\ndb_schema=%s\nqueue=%s\nevent_channel=%s\nresource_id=%s\ntime=%s\n' \
    "${environment}" "${SPLIT_PROJECT_NAME}" "${route_image}" "${automation_image}" \
    "${previous_route_image}" "${previous_automation_image}" "$(git rev-parse --short=12 HEAD)" \
    "${db_schema:-}" "${queue_name:-}" "${event_channel:-}" "${SPLIT_RESOURCE_ID}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "${PROJECT_ROOT}/.deployments/${environment}.manifest"
  log "Split environment ${environment} deployed; rollback scope is ${SPLIT_SERVICES[*]} only."
}

wait_for_split_service() {
  local service="$1"
  local timeout_seconds="$2"
  local retry_interval_seconds="$3"
  local start_ts current_ts elapsed
  start_ts="$(date +%s)"
  while true; do
    if docker compose "${SPLIT_COMPOSE_ARGS[@]}" exec -T "${service}" python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3)' >/dev/null 2>&1 || \
       docker compose "${SPLIT_COMPOSE_ARGS[@]}" exec -T "${service}" python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8100/health", timeout=3)' >/dev/null 2>&1; then
      return 0
    fi
    current_ts="$(date +%s)"
    elapsed=$((current_ts - start_ts))
    (( elapsed >= timeout_seconds )) && return 1
    sleep "${retry_interval_seconds}"
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

  if [[ -n "${TARGET_ENVIRONMENT}" ]]; then
    deploy_split_environment "${TARGET_ENVIRONMENT}"
    exit 0
  fi

  [[ "${ROLLBACK_SPLIT}" == "0" ]] || fail "--rollback requires --environment; refusing to run a full-stack deployment"

  prepare_compose_env
  resolve_compose_profile_args
  ensure_automation_networks

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

  if ! capture_previous_runtime; then
    fail "Unable to preserve the running API image before build; the running stack was not stopped"
  fi

  log "Pre-building services before restart..."
  if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "${COMPOSE_PROFILE_ARGS[@]-}" build; then
    show_compose_diagnostics
    cleanup_rollback_image
    ROLLBACK_IMAGE=""
    fail "docker compose build failed"
  fi

  log "Preparing deployment-bound Prompt Release..."
  if ! prepare_candidate_prompt_release; then
    cleanup_rollback_image
    ROLLBACK_IMAGE=""
    fail "Prompt Release preparation failed; the running stack was not stopped"
  fi
  if ! validate_candidate_prompt_release; then
    mark_candidate_prompt_release_failed "Prompt Release validation failed" || true
    cleanup_rollback_image
    ROLLBACK_IMAGE=""
    fail "Prompt Release validation failed; the running stack was not stopped"
  fi

  if ! sync_candidate_prompt_release_to_production; then
    mark_candidate_prompt_release_failed "Prompt Release production sync failed" || true
    cleanup_rollback_image
    ROLLBACK_IMAGE=""
    fail "Prompt Release production sync failed for ${CANDIDATE_PROMPT_RELEASE_ID}; the running stack was not stopped"
  fi

  log "Stopping services..."
  if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "${COMPOSE_PROFILE_ARGS[@]-}" down; then
    show_compose_diagnostics
    mark_candidate_prompt_release_failed "docker compose down failed" || true
    restore_previous_stack "http://127.0.0.1:${host_port}/health" "${health_timeout_seconds}" "${health_retry_interval_seconds}" || true
    fail "docker compose down failed; rollback image retained: ${ROLLBACK_IMAGE:-unavailable}"
  fi
  log "Starting services (detached)..."
  if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "${COMPOSE_PROFILE_ARGS[@]-}" up -d; then
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

  if [[ ${#COMPOSE_PROFILE_ARGS[@]} -gt 0 ]]; then
    local production_page_url
    production_page_url="http://127.0.0.1:${host_port}/production/"
    log "Checking production environment page: ${production_page_url}"
    if ! wait_for_http_ok "Production page" "${production_page_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}"; then
      show_compose_diagnostics
      mark_candidate_prompt_release_failed "production environment page check failed" || true
      restore_previous_stack "${internal_url}" "${health_timeout_seconds}" "${health_retry_interval_seconds}" || true
      fail "Production environment page check failed after ${health_timeout_seconds}s: ${production_page_url}"
    fi
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

  if ! sync_candidate_prompt_release_to_production; then
    log "WARNING: post-activation Prompt Release production sync failed; the /production database keeps release ${CANDIDATE_PROMPT_RELEASE_ID} as a deployable candidate."
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
