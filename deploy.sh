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

  local host_port internal_url internal_resp external_url external_resp
  host_port="$(resolve_port)"

  log "Stopping services..."
  docker compose -f "${COMPOSE_FILE}" down
  log "Starting services (build + detached)..."
  docker compose -f "${COMPOSE_FILE}" up -d --build

  log "Current service status:"
  docker compose -f "${COMPOSE_FILE}" ps

  internal_url="http://127.0.0.1:${host_port}/health"
  log "Checking internal health: ${internal_url}"
  internal_resp="$(curl -fsS --max-time 20 "${internal_url}")" || fail "Internal health check failed: ${internal_url}"
  log "Internal health response: ${internal_resp}"

  if [[ "${SKIP_EXTERNAL_CHECK}" -eq 0 ]]; then
    external_url="https://${DOMAIN}/health"
    log "Checking external health: ${external_url}"
    external_resp="$(curl -fsS --max-time 20 "${external_url}")" || fail "External health check failed: ${external_url}"
    log "External health response: ${external_resp}"
  else
    log "Skipping external health check."
  fi

  if [[ "${FOLLOW_LOGS}" -eq 1 ]]; then
    log "Following logs (Ctrl+C to exit)..."
    docker compose -f "${COMPOSE_FILE}" logs -f --tail=120 api ws_gateway worker nginx
  fi

  log "Deploy finished."
}

main "$@"
