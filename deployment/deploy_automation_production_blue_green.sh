#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/deployment/docker-compose.single-host.yml"
ENV_FILE="${PROJECT_ROOT}/.env"
RUNTIME_DIR="${PROJECT_ROOT}/deployment/nginx/runtime"
ACTIVE_FILE="${RUNTIME_DIR}/automation_production_active.conf"
STATE_FILE="${PROJECT_ROOT}/.deployments/automation-production-blue-green.manifest"
RELEASE="${BLUE_GREEN_RELEASE:-$(date -u +%Y%m%d%H%M%S)}"
ACTION=deploy
DRAIN_SECONDS="${BLUE_GREEN_DRAIN_SECONDS:-360}"
SKIP_HEALTH=0

log() { printf '[blue-green] %s\n' "$*"; }
fail() { printf '[blue-green] ERROR: %s\n' "$*" >&2; exit 1; }
usage() { printf '%s\n' 'Usage: deploy_automation_production_blue_green.sh [--release id] [--rollback] [--drain-seconds n] [--skip-health]'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release) [[ $# -ge 2 ]] || fail '--release requires a value'; RELEASE="$2"; shift 2 ;;
    --rollback) ACTION=rollback; shift ;;
    --drain-seconds) [[ $# -ge 2 ]] || fail '--drain-seconds requires a value'; DRAIN_SECONDS="$2"; shift 2 ;;
    --skip-health) SKIP_HEALTH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done
[[ "$RELEASE" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail 'release contains unsupported characters'
[[ "$DRAIN_SECONDS" =~ ^[0-9]+$ ]] || fail '--drain-seconds must be a non-negative integer'
command -v docker >/dev/null 2>&1 || fail 'Missing command: docker'
[[ -f "$ENV_FILE" ]] || fail "Missing $ENV_FILE"
[[ "${DEPLOY_PRODUCTION_APPROVED:-0}" == 1 ]] || fail 'set DEPLOY_PRODUCTION_APPROVED=1 for production blue-green deployment'
mkdir -p "$RUNTIME_DIR" "${PROJECT_ROOT}/.deployments"

compose() {
  local project="$1" override="$2"; shift 2
  docker compose --project-name "$project" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$override" --profile automation "$@"
}

nginx_container() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q nginx | sed -n '1p'
}

switch_upstream() {
  local target="$1" project="$2" tmp backup nginx
  tmp="$(mktemp "${RUNTIME_DIR}/.automation-production-active.XXXXXX")"
  backup="$(mktemp "${RUNTIME_DIR}/.automation-production-active-backup.XXXXXX")"
  cp "$ACTIVE_FILE" "$backup"
  printf '# project=%s\nupstream automation_production_active {\n    server %s:8000;\n    keepalive 64;\n}\n' "$project" "$target" > "$tmp"
  nginx="$(nginx_container)"; [[ -n "$nginx" ]] || fail 'official nginx container is not running'
  mv -f "$tmp" "$ACTIVE_FILE"
  if ! docker exec "$nginx" nginx -t >/dev/null; then
    mv -f "$backup" "$ACTIVE_FILE"
    fail 'nginx -t rejected active config; previous pointer was restored'
  fi
  rm -f "$backup"
  docker exec "$nginx" nginx -s reload >/dev/null
}

if [[ "$ACTION" == rollback ]]; then
  [[ -f "$STATE_FILE" ]] || fail 'no blue-green state exists for rollback'
  previous_target="$(awk -F= '$1=="previous_target"{print $2}' "$STATE_FILE")"
  previous_project="$(awk -F= '$1=="previous_project"{print $2}' "$STATE_FILE")"
  [[ -n "$previous_target" && -n "$previous_project" ]] || fail 'rollback pointer is incomplete'
  switch_upstream "$previous_target" "$previous_project"
  log "Rolled back upstream to $previous_target; no request was replayed."
  exit 0
fi

suffix="$RELEASE"
project="supportportal-automation-production-bg-${suffix}"
route="route_production_candidate_${suffix}"
automation="automation_production_candidate_${suffix}"
override="$(mktemp "${PROJECT_ROOT}/.deployments/automation-production-compose.XXXXXX.yml")"
trap 'rm -f "$override"' EXIT
cat > "$override" <<EOF
services:
  $route:
    extends:
      file: $COMPOSE_FILE
      service: route_production
    networks:
      automation_internal_production:
        aliases: [$route]
  $automation:
    extends:
      file: $COMPOSE_FILE
      service: automation_production
    environment:
      ROUTE_SERVICE_URL: http://$route:8100
    depends_on:
      $route:
        condition: service_started
      automation_redis_production:
        condition: service_healthy
    networks:
      automation_edge:
        aliases: [$automation]
      automation_internal_production:
        aliases: [$automation]
EOF

log "Starting candidate project $project"
compose "$project" "$override" up -d --no-build "$route" "$automation"
timeout="${DEPLOY_HEALTH_TIMEOUT_SECONDS:-90}"
start="$(date +%s)"
while ! compose "$project" "$override" exec -T "$automation" python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3)' >/dev/null 2>&1; do
  (( $(date +%s) - start >= timeout )) && fail 'candidate automation readiness failed'
  sleep "${DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS:-2}"
done
compose "$project" "$override" exec -T "$route" python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8100/health", timeout=3)' >/dev/null || fail 'candidate route readiness failed'

old_target="$(awk '/server [^:]+:8000;/{gsub("server |:8000;",""); print; exit}' "$ACTIVE_FILE" 2>/dev/null || true)"
old_project="$(awk -F= '$1=="project"{print $2}' "$STATE_FILE" 2>/dev/null || true)"
if [[ -z "$old_project" && "$old_target" == "automation_production" ]]; then
  old_project="supportportal-automation-production"
fi
switch_upstream "$automation" "$project"
if [[ "$SKIP_HEALTH" == 0 ]]; then
  port="${NGINX_HOST_PORT:-8080}"
  curl --fail --silent --show-error "http://127.0.0.1:${port}/automation/production/health" >/dev/null || fail 'candidate is not reachable through nginx'
fi
printf 'target=%s\nproject=%s\nprevious_target=%s\nprevious_project=%s\nrelease=%s\ntime=%s\n' "$automation" "$project" "$old_target" "$old_project" "$RELEASE" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_FILE"
if [[ -n "$old_project" && "$old_project" != "$project" ]]; then
  log "Draining old project $old_project for ${DRAIN_SECONDS}s"
  sleep "$DRAIN_SECONDS"
  docker compose --project-name "$old_project" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down --remove-orphans || log 'old project cleanup failed; inspect manually'
fi
log "Candidate $RELEASE is active; /production was not restarted."
