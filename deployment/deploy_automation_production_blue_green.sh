#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/deployment/docker-compose.single-host.yml"
ENV_FILE="${PROJECT_ROOT}/.env"
RUNTIME_DIR="${PROJECT_ROOT}/deployment/nginx/runtime"
ACTIVE_FILE="${RUNTIME_DIR}/automation_production_active.conf"
STATE_FILE="${PROJECT_ROOT}/.deployments/automation-production-blue-green.manifest"
RELEASE_DIR="${PROJECT_ROOT}/.deployments/releases"
OVERRIDE_DIR="${PROJECT_ROOT}/.deployments/automation-production-blue-green"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-${PROJECT_ROOT}/.deploy_ec2.lock}"
DEPLOY_LOCK_ALREADY_HELD="${DEPLOY_LOCK_ALREADY_HELD:-0}"
RELEASE="${BLUE_GREEN_RELEASE:-$(date -u +%Y%m%d%H%M%S)}"
ACTION=deploy
DRAIN_SECONDS="${BLUE_GREEN_DRAIN_SECONDS:-360}"
SKIP_HEALTH=0
RESOLVED_APP_RUNTIME_IMAGE=""

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
command -v flock >/dev/null 2>&1 || fail 'Missing command: flock'
[[ "$SKIP_HEALTH" == 1 ]] || command -v curl >/dev/null 2>&1 || fail 'Missing command: curl'
[[ -f "$ENV_FILE" ]] || fail "Missing $ENV_FILE"
[[ "${DEPLOY_PRODUCTION_APPROVED:-0}" == 1 ]] || fail 'set DEPLOY_PRODUCTION_APPROVED=1 for production blue-green deployment'
cd "$PROJECT_ROOT"
mkdir -p "$RUNTIME_DIR" "${PROJECT_ROOT}/.deployments" "$OVERRIDE_DIR"

if [[ "$DEPLOY_LOCK_ALREADY_HELD" != 1 ]]; then
  mkdir -p "$(dirname -- "$DEPLOY_LOCK_FILE")"
  exec 9>"$DEPLOY_LOCK_FILE"
  flock -n 9 || fail "Another deployment is already running (lock: $DEPLOY_LOCK_FILE)"
  log "Acquired deployment lock: $DEPLOY_LOCK_FILE"
fi

manifest_value() {
  local manifest="$1" key="$2"
  awk -F= -v key="$key" '$1 == key { sub("^[^=]*=", "", $0); print; exit }' "$manifest"
}

read_env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  awk -F= -v key="$key" '$0 ~ "^[[:space:]]*" key "[[:space:]]*=" {sub("^[^=]*=[[:space:]]*", "", $0); gsub(/^["]|["]$/, "", $0); print; exit}' "$ENV_FILE"
}

resolve_env_value() {
  local key="$1"
  if [[ -n "${!key:-}" ]]; then
    printf '%s' "${!key}"
  else
    read_env_value "$key"
  fi
}

validate_release_id() {
  [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] || fail "Invalid release id: $1"
}

validate_service_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || fail "Invalid upstream service name: $1"
}

validate_image_id() {
  [[ "$1" =~ ^sha256:[0-9a-fA-F]{64}$ ]] || fail "Invalid image id in release manifest: $1"
}

release_from_override() {
  local override="$1" filename release_id
  [[ "$override" == "$OVERRIDE_DIR/"* ]] || fail "Rollback override is outside the managed directory: $override"
  filename="${override##*/}"
  [[ "$filename" == *.yml ]] || fail "Rollback override has an unexpected filename: $override"
  release_id="${filename%.yml}"
  validate_release_id "$release_id"
  printf '%s' "$release_id"
}

load_release_manifest() {
  local manifest="$RELEASE_DIR/${RELEASE}.env" key image image_id actual manifest_release
  [[ -f "$manifest" ]] || fail "Release manifest not found: $manifest"
  manifest_release="$(manifest_value "$manifest" release_id)"
  [[ -z "$manifest_release" || "$manifest_release" == "$RELEASE" ]] || fail "Release manifest id does not match --release: $manifest_release"
  for key in ROUTE_PRODUCTION_IMAGE AUTOMATION_PRODUCTION_IMAGE; do
    image="$(manifest_value "$manifest" "$key")"
    image_id="$(manifest_value "$manifest" "${key}_ID")"
    [[ -n "$image" && -n "$image_id" ]] || fail "$key is missing from $manifest"
    case "$key" in
      ROUTE_PRODUCTION_IMAGE) [[ "$image" == localhost/supportportal-route:* ]] || fail "$key has an unexpected image reference: $image" ;;
      AUTOMATION_PRODUCTION_IMAGE) [[ "$image" == localhost/supportportal-automation-production:* ]] || fail "$key has an unexpected image reference: $image" ;;
    esac
    validate_image_id "$image_id"
    actual="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
    [[ "$actual" == "$image_id" ]] || fail "$key image identity mismatch: expected $image_id, found ${actual:-missing}"
    export "$key=$image"
    export "${key}_ID=$image_id"
  done
  export ROUTE_PRODUCTION_BUILD_REF="$(manifest_value "$manifest" commit)"
  export AUTOMATION_PRODUCTION_BUILD_REF="$ROUTE_PRODUCTION_BUILD_REF"
  export ROUTE_PRODUCTION_BUILD_TIME="$(manifest_value "$manifest" build_time)"
  export AUTOMATION_PRODUCTION_BUILD_TIME="$ROUTE_PRODUCTION_BUILD_TIME"
  log "Loaded release manifest $manifest (commit=${ROUTE_PRODUCTION_BUILD_REF:-unknown})"
}

load_production_resource_identity() {
  local dsn schema table queue channel
  dsn="$(resolve_env_value AUTOMATION_PRODUCTION_DB_DSN)"
  dsn="${dsn:-$(resolve_env_value PRODUCTION_TICKET_DB_DSN)}"
  [[ -n "$dsn" ]] || fail 'AUTOMATION_PRODUCTION_DB_DSN or PRODUCTION_TICKET_DB_DSN is required'
  if [[ -n "$(resolve_env_value TICKET_DB_DSN)" && "$dsn" == "$(resolve_env_value TICKET_DB_DSN)" ]]; then
    fail 'production DB DSN must differ from TICKET_DB_DSN'
  fi
  schema="$(resolve_env_value AUTOMATION_PRODUCTION_DB_SCHEMA)"
  table="$(resolve_env_value AUTOMATION_PRODUCTION_DB_TABLE)"
  queue="$(resolve_env_value AUTOMATION_PRODUCTION_QUEUE)"
  channel="$(resolve_env_value AUTOMATION_PRODUCTION_EVENT_CHANNEL)"
  export AUTOMATION_PRODUCTION_DB_DSN="$dsn"
  export AUTOMATION_PRODUCTION_DB_SCHEMA="${schema:-supportportal_production}"
  export AUTOMATION_PRODUCTION_DB_TABLE="${table:-automation_executions_production}"
  export AUTOMATION_PRODUCTION_QUEUE="${queue:-automation.production}"
  export AUTOMATION_PRODUCTION_EVENT_CHANNEL="${channel:-automation.events.production}"
}

resolve_app_runtime_image() {
  local image container build_ref
  image="$(resolve_env_value APP_RUNTIME_IMAGE)"
  if [[ -z "$image" ]]; then
    container="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q api | sed -n '1p')"
    [[ -n "$container" ]] || fail 'APP_RUNTIME_IMAGE is unset and the official api container is not running'
    image="$(docker inspect --format '{{.Config.Image}}' "$container" 2>/dev/null || true)"
    [[ -n "$image" ]] || fail 'Unable to resolve APP_RUNTIME_IMAGE from the official api container'
    build_ref="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" 2>/dev/null \
      | awk -F= '$1 == "APP_BUILD_REF" {sub("^[^=]*=", "", $0); print; exit}')"
    [[ "$build_ref" == "$ROUTE_PRODUCTION_BUILD_REF" ]] || fail "official api build ref ${build_ref:-missing} does not match release commit $ROUTE_PRODUCTION_BUILD_REF"
    log "Resolved APP_RUNTIME_IMAGE from the official api container: $image"
  fi
  docker image inspect "$image" >/dev/null 2>&1 || fail "APP_RUNTIME_IMAGE not present locally: $image"
  RESOLVED_APP_RUNTIME_IMAGE="$image"
}

compose() {
  local project="$1" override="$2"; shift 2
  docker compose --project-name "$project" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$override" --profile automation "$@"
}

nginx_container() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q nginx | sed -n '1p'
}

ensure_nginx_runtime_mount() {
  local nginx
  nginx="$(nginx_container)"
  [[ -n "$nginx" ]] || fail 'official nginx container is not running'
  if ! docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/nginx/runtime"}}mounted{{end}}{{end}}' "$nginx" | grep -q mounted; then
    log 'Recreating official nginx once to install the runtime upstream mount; application containers are untouched.'
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build --no-deps nginx
    nginx="$(nginx_container)"
  fi
  docker exec "$nginx" nginx -t >/dev/null || fail 'official nginx configuration is invalid'
}

write_upstream_file() {
  local target="$1" tmp="$2"
  printf 'set $automation_production_active %s:8000;\n' "$target" > "$tmp"
}

switch_upstream() {
  local target="$1" project="$2" tmp backup nginx
  validate_service_name "$target"
  [[ -f "$ACTIVE_FILE" ]] || { log 'ERROR: active upstream pointer is missing'; return 1; }
  tmp="$(mktemp "${RUNTIME_DIR}/.automation-production-active.XXXXXX")"
  backup="$(mktemp "${RUNTIME_DIR}/.automation-production-active-backup.XXXXXX")"
  if ! cp "$ACTIVE_FILE" "$backup"; then
    rm -f "$tmp" "$backup"
    log 'ERROR: could not back up the active upstream pointer'
    return 1
  fi
  write_upstream_file "$target" "$tmp"
  nginx="$(nginx_container)" || {
    rm -f "$tmp" "$backup"
    log 'ERROR: official nginx container is not running'
    return 1
  }
  if [[ -z "$nginx" ]]; then
    rm -f "$tmp" "$backup"
    log 'ERROR: official nginx container is not running'
    return 1
  fi
  if ! mv -f "$tmp" "$ACTIVE_FILE"; then
    rm -f "$tmp" "$backup"
    log 'ERROR: could not install the new active upstream pointer'
    return 1
  fi
  if ! docker exec "$nginx" nginx -t >/dev/null; then
    mv -f "$backup" "$ACTIVE_FILE"
    log 'ERROR: nginx -t rejected active config; previous pointer was restored'
    return 1
  fi
  if docker exec "$nginx" nginx -s reload >/dev/null; then
    rm -f "$backup"
    return 0
  fi

  log 'ERROR: nginx reload failed; restoring the previous upstream pointer'
  if mv -f "$backup" "$ACTIVE_FILE" \
    && docker exec "$nginx" nginx -t >/dev/null \
    && docker exec "$nginx" nginx -s reload >/dev/null; then
    log 'Previous upstream pointer was restored and Nginx reloaded.'
  else
    log 'WARNING: Nginx may still serve the candidate; inspect Nginx immediately'
  fi
  return 1
}

wait_for_service() {
  local project="$1" override="$2" service="$3" port="$4" timeout start
  timeout="${DEPLOY_HEALTH_TIMEOUT_SECONDS:-90}"
  start="$(date +%s)"
  while ! compose "$project" "$override" exec -T "$service" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${port}/health', timeout=3)" >/dev/null 2>&1; do
    (( $(date +%s) - start >= timeout )) && return 1
    sleep "${DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS:-2}"
  done
}

stop_candidate() {
  local project="$1" override="$2" route="$3" automation="$4"
  compose "$project" "$override" stop "$route" "$automation" >/dev/null 2>&1 || true
}

if [[ "$ACTION" == rollback ]]; then
  [[ -f "$STATE_FILE" ]] || fail 'no blue-green state exists for rollback'
  previous_target="$(awk -F= '$1=="previous_target"{print $2}' "$STATE_FILE")"
  previous_project="$(awk -F= '$1=="previous_project"{print $2}' "$STATE_FILE")"
  previous_override="$(awk -F= '$1=="previous_override"{print $2}' "$STATE_FILE")"
  previous_route="$(awk -F= '$1=="previous_route"{print $2}' "$STATE_FILE")"
  previous_automation="$(awk -F= '$1=="previous_automation"{print $2}' "$STATE_FILE")"
  current_target="$(awk -F= '$1=="target"{print $2}' "$STATE_FILE")"
  current_project="$(awk -F= '$1=="project"{print $2}' "$STATE_FILE")"
  current_override="$(awk -F= '$1=="override"{print $2}' "$STATE_FILE")"
  current_release="$(awk -F= '$1=="release"{print $2}' "$STATE_FILE")"
  previous_release="$(awk -F= '$1=="previous_release"{print $2}' "$STATE_FILE")"
  [[ -n "$previous_target" && -n "$previous_project" && -n "$previous_route" && -n "$previous_automation" ]] || fail 'rollback pointer is incomplete'
  [[ -z "$previous_override" || -f "$previous_override" ]] || fail "rollback compose override is missing: $previous_override"
  if [[ -n "$previous_override" ]]; then
    derived_previous_release="$(release_from_override "$previous_override")"
    [[ -z "$previous_release" || "$previous_release" == "$derived_previous_release" ]] || fail 'rollback release does not match its compose override'
    previous_release="$derived_previous_release"
    RELEASE="$previous_release"
    load_release_manifest
  else
    previous_release="${previous_release:-baseline}"
    log 'Rollback uses the baseline production Compose services from .env.'
  fi
  if [[ -z "$current_release" && -n "$current_override" ]]; then
    current_release="$(release_from_override "$current_override")"
  fi
  load_production_resource_identity
  ensure_nginx_runtime_mount
  compose "$previous_project" "${previous_override:-${COMPOSE_FILE}}" up -d --no-build "$previous_route" "$previous_automation"
  wait_for_service "$previous_project" "${previous_override:-${COMPOSE_FILE}}" "$previous_route" 8100 || fail 'rollback route readiness failed'
  wait_for_service "$previous_project" "${previous_override:-${COMPOSE_FILE}}" "$previous_automation" 8000 || fail 'rollback automation readiness failed'
  if ! switch_upstream "$previous_target" "$previous_project"; then
    fail 'rollback upstream switch failed; active state was preserved when possible'
  fi
  printf 'target=%s\nproject=%s\noverride=%s\nroute=%s\nautomation=%s\nprevious_target=%s\nprevious_project=%s\nprevious_override=%s\nprevious_route=%s\nprevious_automation=%s\nrelease=%s\nprevious_release=%s\ntime=%s\n' \
    "$previous_target" "$previous_project" "$previous_override" "$previous_route" "$previous_automation" \
    "$current_target" "$current_project" "$current_override" \
    "$(awk -F= '$1=="route"{print $2}' "$STATE_FILE")" "$(awk -F= '$1=="automation"{print $2}' "$STATE_FILE")" \
    "$previous_release" "${current_release:-baseline}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_FILE"
  log "Rolled back upstream to $previous_target; no request was replayed."
  exit 0
fi

validate_release_id "$RELEASE"
load_release_manifest
load_production_resource_identity
resolve_app_runtime_image
app_runtime_image="$RESOLVED_APP_RUNTIME_IMAGE"
ensure_nginx_runtime_mount
if [[ "$SKIP_HEALTH" == 0 ]]; then
  nginx_host_port="$(resolve_env_value NGINX_HOST_PORT)"
  nginx_host_port="${nginx_host_port:-8080}"
  [[ "$nginx_host_port" =~ ^[0-9]+$ ]] || fail "NGINX_HOST_PORT must be numeric: $nginx_host_port"
fi
redis_container="$(docker compose --project-name supportportal-automation-production --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q automation_redis_production | sed -n '1p')"
[[ -n "$redis_container" ]] || fail 'existing production Redis is not running; refusing to create a second Redis'

suffix="$RELEASE"
project="supportportal-automation-production-bg-${suffix}"
route="route_production_candidate_${suffix}"
automation="automation_production_candidate_${suffix}"
override="${OVERRIDE_DIR}/${RELEASE}.yml"
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
    image: ${AUTOMATION_PRODUCTION_IMAGE}
    command: ["uvicorn", "backend.automation_production_runtime:app", "--host", "0.0.0.0", "--port", "8000"]
    environment:
      AUTOMATION_ENVIRONMENT: production
      AUTOMATION_DB_DSN: \${AUTOMATION_PRODUCTION_DB_DSN:-}
      AUTOMATION_DB_RESOURCE_ID: production
      AUTOMATION_DB_SCHEMA: \${AUTOMATION_PRODUCTION_DB_SCHEMA:-supportportal_production}
      AUTOMATION_DB_TABLE: \${AUTOMATION_PRODUCTION_DB_TABLE:-automation_executions_production}
      AUTOMATION_RUNTIME_ALLOW_MEMORY: \${AUTOMATION_RUNTIME_ALLOW_MEMORY:-0}
      AUTOMATION_RUNTIME_REQUIRE_RESOURCES: "1"
      AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED: \${PRODUCTION_ZENDESK_SIDE_EFFECTS_ENABLED:-0}
      AUTOMATION_TARGET_TICKET_STATUS: \${PRODUCTION_TARGET_TICKET_STATUS:-}
      AUTOMATION_REDIS_URL: redis://automation_redis_production:6379/0
      AUTOMATION_QUEUE_NAME: \${AUTOMATION_PRODUCTION_QUEUE:-automation.production}
      AUTOMATION_EVENT_CHANNEL: \${AUTOMATION_PRODUCTION_EVENT_CHANNEL:-automation.events.production}
      AUTOMATION_RESOURCE_ID: production
      n8n_request_token: \${n8n_request_token:-}
      zendesk_basic_auth: \${zendesk_basic_auth:-}
      ZENDESK_AI_ASSIGNEE_EMAIL: \${ZENDESK_AI_ASSIGNEE_EMAIL:-}
      TICKET_DB_DSN: \${AUTOMATION_PRODUCTION_DB_DSN:-\${PRODUCTION_TICKET_DB_DSN:-}}
      TICKET_DB_SCHEMA: \${AUTOMATION_PRODUCTION_DB_SCHEMA:-supportportal_production}
      TICKET_DB_APPLICATION_NAME: supportportal-automation-production
      INTERNAL_EMAIL_SUBJECT_NAMESPACE: "[automation]"
      OPENAI_API_KEY: \${OPENAI_API_KEY:-}
      HTTP_PROXY: \${SUPPORTPORTAL_HTTP_PROXY:-}
      HTTPS_PROXY: \${SUPPORTPORTAL_HTTPS_PROXY:-}
      NO_PROXY: \${SUPPORTPORTAL_NO_PROXY:-localhost,127.0.0.1,$route}
      ACCOUNT_ROUTE_MODEL: \${ACCOUNT_ROUTE_MODEL:-gpt-5.6-luna}
      ACCOUNT_ROUTE_BASE_URL: \${ACCOUNT_ROUTE_BASE_URL:-}
      ACCOUNT_ROUTE_REASONING_EFFORT: \${ACCOUNT_ROUTE_REASONING_EFFORT:-xhigh}
      ACCOUNT_ROUTE_TIMEOUT_SECONDS: \${ACCOUNT_ROUTE_TIMEOUT_SECONDS:-120}
      DEEPSEEK_API_KEY: \${DEEPSEEK_API_KEY:-}
      DEEPSEEK_BASE_URL: \${DEEPSEEK_BASE_URL:-https://api.deepseek.com}
      DEEPSEEK_FALLBACK_MODEL: \${DEEPSEEK_FALLBACK_MODEL:-deepseek-v4-pro}
      DEEPSEEK_FALLBACK_ENABLED: \${DEEPSEEK_FALLBACK_ENABLED:-true}
      BILLING_AUTOMATION_GRAPH_TENANT_ID: \${BILLING_AUTOMATION_GRAPH_TENANT_ID:-}
      BILLING_AUTOMATION_GRAPH_CLIENT_ID: \${BILLING_AUTOMATION_GRAPH_CLIENT_ID:-}
      BILLING_AUTOMATION_GRAPH_CLIENT_SECRET: \${BILLING_AUTOMATION_GRAPH_CLIENT_SECRET:-}
      BILLING_AUTOMATION_GRAPH_USERNAME: \${BILLING_AUTOMATION_GRAPH_USERNAME:-}
      BILLING_AUTOMATION_GRAPH_TOKEN_CACHE: \${BILLING_AUTOMATION_GRAPH_TOKEN_CACHE:-.msgraph/billing-automation-token.json}
      RAG_SERVICE_URL: \${AUTOMATION_PRODUCTION_RAG_SERVICE_URL:-}
      RAG_SERVICE_TIMEOUT_SECONDS: \${RAG_SERVICE_TIMEOUT_SECONDS:-45.0}
      RAG_SERVICE_SHARED_TOKEN: \${RAG_SERVICE_SHARED_TOKEN:-supportportal-rag-internal}
      RAGFLOW_BASE_URL: \${RAGFLOW_BASE_URL:-}
      RAGFLOW_API_KEY: \${RAGFLOW_API_KEY:-}
      ROUTE_SERVICE_URL: http://$route:8100
      ROUTE_SERVICE_TOKEN: \${ROUTE_PRODUCTION_SERVICE_TOKEN:-}
      APP_BUILD_REF: \${AUTOMATION_PRODUCTION_BUILD_REF:-}
      APP_BUILD_TIME: \${AUTOMATION_PRODUCTION_BUILD_TIME:-}
    expose: ["8000"]
    restart: unless-stopped
    networks:
      automation_edge:
        aliases: [$automation]
      automation_internal_production:
        aliases: [$automation]
EOF

log "Starting candidate project $project"
compose "$project" "$override" up -d --no-build "$route" "$automation"
wait_for_service "$project" "$override" "$route" 8100 || { stop_candidate "$project" "$override" "$route" "$automation"; fail 'candidate route readiness failed'; }
wait_for_service "$project" "$override" "$automation" 8000 || { stop_candidate "$project" "$override" "$route" "$automation"; fail 'candidate automation readiness failed'; }

old_target="$(awk '
  $1 == "set" && $2 == "$automation_production_active" {gsub(";", "", $3); sub(":8000$", "", $3); print $3; exit}
  $1 == "server" && $2 ~ /:8000;$/ {gsub(":8000;", "", $2); print $2; exit}
' "$ACTIVE_FILE" 2>/dev/null || true)"
old_project="$(awk -F= '$1=="project"{print $2}' "$STATE_FILE" 2>/dev/null || true)"
old_override="$(awk -F= '$1=="override"{print $2}' "$STATE_FILE" 2>/dev/null || true)"
old_route="$(awk -F= '$1=="route"{print $2}' "$STATE_FILE" 2>/dev/null || true)"
old_automation="$(awk -F= '$1=="automation"{print $2}' "$STATE_FILE" 2>/dev/null || true)"
if [[ -n "$old_override" ]]; then
  old_release="$(release_from_override "$old_override")"
else
  old_release=baseline
fi
if [[ -z "$old_project" && "$old_target" == "automation_production" ]]; then
  old_project="supportportal-automation-production"
  old_route="route_production"
  old_automation="automation_production"
fi
if [[ -z "$old_route" && "$old_target" == automation_production_candidate_* ]]; then
  old_suffix="${old_target#automation_production_candidate_}"
  old_route="route_production_candidate_${old_suffix}"
  old_automation="$old_target"
fi
[[ -n "$old_target" && -n "$old_project" && -n "$old_route" && -n "$old_automation" ]] || fail 'active upstream state is incomplete'
if ! switch_upstream "$automation" "$project"; then
  stop_candidate "$project" "$override" "$route" "$automation"
  fail 'candidate upstream switch failed; candidate was stopped'
fi
if [[ "$SKIP_HEALTH" == 0 ]]; then
  if ! curl --fail --silent --show-error "http://127.0.0.1:${nginx_host_port}/automation/production/health" >/dev/null; then
    log 'Candidate failed through-nginx health check; restoring the previous upstream.'
    if switch_upstream "$old_target" "$old_project"; then
      stop_candidate "$project" "$override" "$route" "$automation"
      fail 'candidate is not reachable through nginx; previous upstream was restored'
    fi
    fail 'candidate health failed and previous upstream restoration failed; candidate was left running for immediate inspection'
  fi
fi
printf 'target=%s\nproject=%s\noverride=%s\nroute=%s\nautomation=%s\nprevious_target=%s\nprevious_project=%s\nprevious_override=%s\nprevious_route=%s\nprevious_automation=%s\nrelease=%s\nprevious_release=%s\ntime=%s\n' \
  "$automation" "$project" "$override" "$route" "$automation" "$old_target" "$old_project" "$old_override" "$old_route" "$old_automation" "$RELEASE" "$old_release" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_FILE"
# The parity worker follows the main app image train, not the automation
# release manifest: recreate it in the split project against the validated
# APP_RUNTIME_IMAGE. Reply-job claims keep the brief overlap safe.
log "Recreating split production worker against ${app_runtime_image}"
APP_RUNTIME_IMAGE="$app_runtime_image" docker compose \
  --project-name supportportal-automation-production \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  --profile automation \
  up -d --no-build --no-deps automation_production_worker
if [[ -n "$old_project" && "$old_project" != "$project" ]]; then
  log "Draining old project $old_project for ${DRAIN_SECONDS}s"
  sleep "$DRAIN_SECONDS"
  stop_candidate "$old_project" "${old_override:-${COMPOSE_FILE}}" "$old_route" "$old_automation"
fi
log "Candidate $RELEASE is active; /production was not restarted. Split worker recreated against ${app_runtime_image}."
