#!/usr/bin/env bash
# Start the split Route/Automation environments locally under podman so code
# changes can be verified without an EC2 release cycle.
#
# What this script does:
#   - builds the three automation image roles (route / automation / production)
#     from the CURRENT working tree (task worktrees are supported; uncommitted
#     changes get a "-wip" image tag suffix),
#   - idempotently creates the four automation networks and the three
#     n8n_request_token value in the root .env (auto-generated),
#   - starts one compose project per environment (matching the EC2 shape),
#   - runs a dedicated local nginx (port 18080) for the /automation/* paths --
#     the official nginx config hardcodes Docker's embedded DNS resolver
#     (127.0.0.11), which does not exist under podman,
#   - verifies health and the 401 auth negative case through that nginx.
#
# Local safety defaults: Zendesk side effects stay disabled (0) and the
# preproduction allowlist stays empty unless explicitly set in .env, so local
# executions fail closed instead of writing Zendesk.
#
# Usage:
#   scripts/workflow/start_local_split_environments.sh [--skip-build]
#
# The three environments are served at http://localhost:18080/automation/*/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

require_command git
require_command podman
require_command podman-compose
require_command curl

skip_build=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --skip-build) skip_build=1; shift ;;
    *) die "Unknown argument: $1 (expected --skip-build)" ;;
  esac
done

GIT_ROOT="$(git rev-parse --show-toplevel)"
# Always read configuration from the ROOT repository .env (also when this
# script runs from a task worktree): git-common-dir points at the main .git.
ROOT_ENV_FILE="${SUPPORTPORTAL_ROOT_ENV_FILE:-$(dirname "$(git -C "${GIT_ROOT}" rev-parse --path-format=absolute --git-common-dir)")/.env}"
[[ -f "${ROOT_ENV_FILE}" ]] || die "Root .env not found at ${ROOT_ENV_FILE}; it provides TICKET_DB_DSN and stores the automation tokens."

ENV_FILE_VALUE() {
  awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "${ROOT_ENV_FILE}"
}

APPEND_ENV_VALUE() {
  printf '%s=%s\n' "$1" "$2" >>"${ROOT_ENV_FILE}"
}

LOCAL_NGINX_PORT="${SUPPORTPORTAL_LOCAL_SPLIT_PORT:-18080}"
LOCAL_NGINX_BASE="http://localhost:${LOCAL_NGINX_PORT}"

COMPOSE_FILE="${GIT_ROOT}/deployment/docker-compose.single-host.yml"
[[ -f "${COMPOSE_FILE}" ]] || die "Compose file not found: ${COMPOSE_FILE}"

# --- tokens -------------------------------------------------------------

ensure_token() {
  local key="$1" value
  value="$(ENV_FILE_VALUE "${key}")"
  if [[ -z "${value}" ]]; then
    value="$(openssl rand -hex 32)"
    APPEND_ENV_VALUE "${key}" "${value}"
    info "Generated ${key} in ${ROOT_ENV_FILE}"
  fi
}
ensure_token n8n_request_token

# --- networks -----------------------------------------------------------

create_network_if_missing() {
  local key="$1" default_name="$2" network_name
  network_name="$(ENV_FILE_VALUE "${key}")"
  network_name="${network_name:-${default_name}}"
  if ! podman network exists "${network_name}" >/dev/null 2>&1; then
    podman network create "${network_name}" >/dev/null
    info "Created network: ${network_name}"
  fi
}
create_network_if_missing AUTOMATION_EDGE_NETWORK_NAME supportportal_automation_edge
create_network_if_missing AUTOMATION_STAGING_INTERNAL_NETWORK_NAME supportportal_automation_internal_staging
create_network_if_missing AUTOMATION_PREPRODUCTION_INTERNAL_NETWORK_NAME supportportal_automation_internal_preproduction
create_network_if_missing AUTOMATION_PRODUCTION_INTERNAL_NETWORK_NAME supportportal_automation_internal_production

# --- images -------------------------------------------------------------

BUILD_REF="$(git -C "${GIT_ROOT}" rev-parse --short=12 HEAD)"
IMAGE_TAG="local-${BUILD_REF}"
if [[ -n "$(git -C "${GIT_ROOT}" status --porcelain)" ]]; then
  IMAGE_TAG="local-${BUILD_REF}-wip"
fi
ROUTE_IMAGE="localhost/supportportal-route:${IMAGE_TAG}"
AUTOMATION_IMAGE="localhost/supportportal-automation:${IMAGE_TAG}"
PRODUCTION_IMAGE="localhost/supportportal-automation-production:${IMAGE_TAG}"

image_exists() {
  podman image inspect "$1" >/dev/null 2>&1
}

build_role() {
  local role="$1" tag="$2"
  info "Building ${role} image -> ${tag}"
  podman build -q \
    -f "${GIT_ROOT}/backend/Dockerfile.automation" \
    --build-arg "AUTOMATION_IMAGE_ROLE=${role}" \
    --build-arg "APP_BUILD_REF=${BUILD_REF}" \
    --build-arg "APP_BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    -t "${tag}" \
    "${GIT_ROOT}" >/dev/null
}

if [[ "${skip_build}" -eq 1 ]]; then
  image_exists "${ROUTE_IMAGE}" || die "Image ${ROUTE_IMAGE} not found; run without --skip-build at least once."
  image_exists "${AUTOMATION_IMAGE}" || die "Image ${AUTOMATION_IMAGE} not found; run without --skip-build at least once."
  image_exists "${PRODUCTION_IMAGE}" || die "Image ${PRODUCTION_IMAGE} not found; run without --skip-build at least once."
  info "Skipping build; reusing ${IMAGE_TAG} images."
else
  build_role route "${ROUTE_IMAGE}"
  build_role automation "${AUTOMATION_IMAGE}"
  build_role production "${PRODUCTION_IMAGE}"
fi

# --- environment startup ------------------------------------------------

TICKET_DSN="$(ENV_FILE_VALUE TICKET_DB_DSN)"
[[ -n "${TICKET_DSN}" ]] || die "TICKET_DB_DSN is required in ${ROOT_ENV_FILE}"
PRODUCTION_DSN="$(ENV_FILE_VALUE PRODUCTION_TICKET_DB_DSN)"
if [[ -z "${PRODUCTION_DSN}" ]]; then
  warn "PRODUCTION_TICKET_DB_DSN is not set; the local production environment will be skipped."
fi

# Compose resolves the image pointers and per-environment DSNs from the
# environment at invocation time, so they must be exported before `up`.
export ROUTE_STAGING_IMAGE="${ROUTE_IMAGE}"
export ROUTE_PREPRODUCTION_IMAGE="${ROUTE_IMAGE}"
export ROUTE_PRODUCTION_IMAGE="${ROUTE_IMAGE}"
export AUTOMATION_STAGING_IMAGE="${AUTOMATION_IMAGE}"
export AUTOMATION_PREPRODUCTION_IMAGE="${AUTOMATION_IMAGE}"
export AUTOMATION_PRODUCTION_IMAGE="${PRODUCTION_IMAGE}"
export AUTOMATION_STAGING_DB_DSN="${TICKET_DSN}"
export AUTOMATION_PREPRODUCTION_DB_DSN="${TICKET_DSN}"
if [[ -n "${PRODUCTION_DSN}" ]]; then
  export AUTOMATION_PRODUCTION_DB_DSN="${PRODUCTION_DSN}"
fi

start_environment() {
  local environment="$1"
  local project="supportportal-automation-${environment}"
  info "Starting ${environment} (project ${project})"
  podman-compose \
    --project-name "${project}" \
    --env-file "${ROOT_ENV_FILE}" \
    -f "${COMPOSE_FILE}" \
    --profile automation \
    up -d --no-build \
    "route_${environment}" "automation_${environment}" "automation_redis_${environment}" >/dev/null
}

start_environment staging
start_environment preproduction
if [[ -n "${PRODUCTION_DSN}" ]]; then
  start_environment production
fi

# --- dedicated local nginx ----------------------------------------------
# The official nginx config hardcodes Docker's embedded DNS resolver
# (127.0.0.11), which does not exist under podman, so variable upstreams for
# the split services would always fail with 502 locally. Serve /automation/*
# from a dedicated nginx with static upstream blocks instead.

LOCAL_ENVIRONMENTS=(staging preproduction)
if [[ -n "${PRODUCTION_DSN}" ]]; then
  LOCAL_ENVIRONMENTS+=(production)
fi

NGINX_CONF_DIR="$(mktemp -d)"
LOCAL_NGINX_CONF="${NGINX_CONF_DIR}/automation-local.conf"
{
  for environment in "${LOCAL_ENVIRONMENTS[@]}"; do
    echo "upstream automation_${environment} { server automation_${environment}:8000; }"
  done
  echo "server {"
  echo "    listen 80;"
  echo "    absolute_redirect off;"
  for environment in "${LOCAL_ENVIRONMENTS[@]}"; do
    cat <<SERVER_BLOCK
    location = /automation/${environment} {
        return 301 /automation/${environment}/;
    }
    location /automation/${environment}/ {
        rewrite ^/automation/${environment}(/.*)\$ \$1 break;
        proxy_pass http://automation_${environment};
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
    }
SERVER_BLOCK
  done
  echo "}"
} >"${LOCAL_NGINX_CONF}"

EDGE_NETWORK="$(ENV_FILE_VALUE AUTOMATION_EDGE_NETWORK_NAME)"
EDGE_NETWORK="${EDGE_NETWORK:-supportportal_automation_edge}"
podman rm -f supportportal-automation-nginx >/dev/null 2>&1 || true
podman run -d --name supportportal-automation-nginx \
  --network "${EDGE_NETWORK}" \
  -p "127.0.0.1:${LOCAL_NGINX_PORT}:80" \
  -v "${LOCAL_NGINX_CONF}:/etc/nginx/conf.d/default.conf:ro,Z" \
  docker.io/library/nginx:1.27-alpine >/dev/null
info "Local split nginx serving ${LOCAL_NGINX_BASE}/automation/*/"

# --- verification --------------------------------------------------------

failures=0
expect_http() {
  local label="$1" expected="$2" url="$3" extra="${4:-}"
  local code
  if [[ -n "${extra}" ]]; then
    code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H "Authorization: Bearer ${extra}" -d '{}' "${url}" || echo 000)"
  else
    code="$(curl -s -o /dev/null -w '%{http_code}' "${url}" || echo 000)"
  fi
  if [[ "${code}" == "${expected}" ]]; then
    info "PASS ${label}"
  else
    warn "FAIL ${label} (expected ${expected}, got ${code})"
    failures=$((failures + 1))
  fi
}

info "Waiting for split environments to become healthy..."
sleep 10

for environment in "${LOCAL_ENVIRONMENTS[@]}"; do
  base="${LOCAL_NGINX_BASE}/automation/${environment}"
  expect_http "${environment} /health" 200 "${base}/health"
  expect_http "${environment} unauthenticated POST is rejected" 401 "${base}/v1/cases" "wrong-token-on-purpose"
done

if [[ "${failures}" -gt 0 ]]; then
  die "${failures} verification check(s) failed"
fi

info "Local split environments are running (images: ${IMAGE_TAG})."
info "UI and API base: ${LOCAL_NGINX_BASE}/automation/{staging,preproduction,production}/"
info "Note: local Zendesk side effects are disabled by default (fail-closed);"
info "the unified execution token lives in ${ROOT_ENV_FILE} (n8n_request_token)."
