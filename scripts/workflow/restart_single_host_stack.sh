#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"
# shellcheck source=./_local_db_env.sh
source "$SCRIPT_DIR/_local_db_env.sh"

require_command git
require_command podman
require_command podman-compose
require_command curl
require_command python3

ensure_root_workspace_on_main

deploy_lock_dir="${SUPPORTPORTAL_DEPLOY_LOCK_DIR:-${TMPDIR:-/tmp}/supportportal-single-host-stack.lock}"
lock_owner_file="$deploy_lock_dir/pid"
if ! mkdir "$deploy_lock_dir" 2>/dev/null; then
  stale_pid=""
  if [[ -f "$lock_owner_file" ]]; then
    stale_pid="$(<"$lock_owner_file")"
  fi
  if [[ "$stale_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$stale_pid" 2>/dev/null; then
    rm -f "$lock_owner_file"
    rmdir "$deploy_lock_dir" 2>/dev/null || true
    mkdir "$deploy_lock_dir" || die "Unable to reclaim stale restart lock: $deploy_lock_dir"
  else
    die "Another single-host stack restart is already running (lock: $deploy_lock_dir)."
  fi
fi
printf '%s\n' "$$" >"$lock_owner_file"
rollback_image=""
cleanup_rollback_image() {
  if [[ -n "$rollback_image" ]]; then
    podman image rm -f "$rollback_image" >/dev/null 2>&1 || true
  fi
}
cleanup_on_exit() {
  cleanup_rollback_image
  rm -f "$lock_owner_file"
  rmdir "$deploy_lock_dir" 2>/dev/null || true
}
trap cleanup_on_exit EXIT

use_local_env=0
runtime_mode_override=""
db_mode_override=""

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --use-local-env)
      use_local_env=1
      shift
      ;;
    --mode)
      [[ "$#" -ge 2 ]] || die "Missing value for --mode. Expected full or local_lightweight."
      runtime_mode_override="$2"
      shift 2
      ;;
    --mode=*)
      runtime_mode_override="${1#--mode=}"
      shift
      ;;
    --db)
      [[ "$#" -ge 2 ]] || die "Missing value for --db. Expected local or remote."
      db_mode_override="$2"
      shift 2
      ;;
    --db=*)
      db_mode_override="${1#--db=}"
      shift
      ;;
    *)
      die "Unsupported argument: $1"
      ;;
  esac
done

if [[ -n "$runtime_mode_override" ]]; then
  case "$runtime_mode_override" in
    full|local_lightweight) ;;
    *)
      die "Unsupported --mode: $runtime_mode_override (expected full or local_lightweight)"
      ;;
  esac
fi

if [[ -n "$db_mode_override" ]]; then
  case "$db_mode_override" in
    local|remote) ;;
    *)
      die "Unsupported --db mode: $db_mode_override (expected local or remote)"
      ;;
  esac
fi

git fetch origin

current_main="$(git rev-parse HEAD)"
origin_main="$(git rev-parse origin/main)"
if [[ "$current_main" != "$origin_main" ]]; then
  die "Local main must match origin/main before restarting. Run 'git pull --ff-only origin main' from the root workspace first."
fi

env_file="$(repo_root)/.env"
[[ -f "$env_file" ]] || die "Root .env not found at $env_file"

set -a
# shellcheck source=/dev/null
source "$env_file"
set +a
base_stack_db_mode="${STACK_DB_MODE:-remote}"

if (( use_local_env )); then
  warn "--use-local-env is a deprecated compatibility alias for --mode local_lightweight; only root .env is loaded."
  if [[ -z "$runtime_mode_override" ]]; then
    runtime_mode_override="local_lightweight"
  fi
fi

runtime_mode="${runtime_mode_override:-${STACK_RUNTIME_MODE:-full}}"
db_mode="${db_mode_override:-$base_stack_db_mode}"

case "$runtime_mode" in
  full|local_lightweight) ;;
  *)
    die "Unsupported STACK_RUNTIME_MODE: $runtime_mode (expected full or local_lightweight)"
    ;;
esac

case "$db_mode" in
  local|remote) ;;
  *)
    die "Unsupported STACK_DB_MODE: $db_mode (expected local or remote)"
    ;;
esac

compose_args=(-f deployment/docker-compose.single-host.yml)
health_port="${NGINX_HOST_PORT:-8080}"

if [[ "$runtime_mode" == "local_lightweight" ]]; then
  lightweight_compose_file="deployment/docker-compose.single-host.local-lightweight.yml"
  [[ -f "$lightweight_compose_file" ]] || die "Local lightweight compose override not found at $lightweight_compose_file"
  compose_args+=(-f "$lightweight_compose_file")
fi

if [[ "$db_mode" == "local" ]]; then
  local_db_compose_file="deployment/docker-compose.single-host.local-db.yml"
  [[ -f "$local_db_compose_file" ]] || die "Local DB compose override not found at $local_db_compose_file"
  load_supportportal_local_env "$(repo_root)" "required"
  export_supportportal_local_container_db_env
  compose_args+=(-f "$local_db_compose_file")
  health_port="${NGINX_HOST_PORT:-8080}"
else
  [[ -n "${TICKET_DB_DSN:-}" ]] || die "TICKET_DB_DSN is required in $env_file"
  [[ -n "${PGVECTOR_DSN:-}" ]] || die "PGVECTOR_DSN is required in $env_file"
  "$SCRIPT_DIR/ensure_local_db_relay.sh"
fi

if [[ -z "${AWS_REGION:-}" && -n "${ASSET_S3_REGION:-}" ]]; then
  export AWS_REGION="$ASSET_S3_REGION"
fi
if [[ -z "${AWS_DEFAULT_REGION:-}" && -n "${ASSET_S3_REGION:-}" ]]; then
  export AWS_DEFAULT_REGION="$ASSET_S3_REGION"
fi

export APP_BUILD_REF
export APP_BUILD_TIME
export APP_RUNTIME_IMAGE
APP_BUILD_REF="$(git rev-parse --short=12 HEAD)"
APP_BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
APP_RUNTIME_IMAGE="localhost/supportportal-app:${APP_BUILD_REF}"

info "Current main commit: $APP_BUILD_REF"
info "Build timestamp: $APP_BUILD_TIME"
info "Runtime image: $APP_RUNTIME_IMAGE"
if [[ "$runtime_mode" == "local_lightweight" ]]; then
  if [[ "$db_mode" == "local" ]]; then
    info "Rebuilding single-host stack in local_lightweight mode with local Postgres/pgvector."
    info "Local Postgres host port: ${LOCAL_POSTGRES_HOST_PORT:-15432}"
    info "Local DB schemas: ticket=$TICKET_DB_SCHEMA pgvector=$PGVECTOR_SCHEMA table=$PGVECTOR_TABLE dim=$PGVECTOR_DIM"
  else
    info "Rebuilding single-host stack in local_lightweight mode with remote database DSNs from .env."
  fi
else
  if [[ "$db_mode" == "local" ]]; then
    info "Rebuilding single-host stack in full mode with local Postgres/pgvector."
    info "Local Postgres host port: ${LOCAL_POSTGRES_HOST_PORT:-15432}"
    info "Local DB schemas: ticket=$TICKET_DB_SCHEMA pgvector=$PGVECTOR_SCHEMA table=$PGVECTOR_TABLE dim=$PGVECTOR_DIM"
  else
    info "Rebuilding single-host stack in full mode."
  fi
fi

# --- Build cache diagnostics ---

# Normalize SUPPORTPORTAL_NO_BUILD_CACHE
_cache_disabled=0
_no_cache_value="${SUPPORTPORTAL_NO_BUILD_CACHE:-}"
case "$_no_cache_value" in
  ""|0) _cache_disabled=0 ;;
  1) _cache_disabled=1 ;;
  *) die "Unsupported SUPPORTPORTAL_NO_BUILD_CACHE: $_no_cache_value (expected unset, empty, 0, or 1)" ;;
esac

# Normalize SUPPORTPORTAL_BUILD_PROGRESS
_build_progress="auto"
_progress_value="${SUPPORTPORTAL_BUILD_PROGRESS:-}"
case "$_progress_value" in
  "") _build_progress="auto" ;;
  plain) _build_progress="plain" ;;
  *) die "Unsupported SUPPORTPORTAL_BUILD_PROGRESS: $_progress_value (expected unset, empty, or plain)" ;;
esac

echo "Runtime mode: ${runtime_mode}"
if [[ "$runtime_mode" == "local_lightweight" ]]; then
  echo "INSTALL_ML_DEPS: 0"
else
  echo "INSTALL_ML_DEPS: 1"
fi
echo "Runtime image tag: ${APP_BUILD_REF}"

if (( _cache_disabled )); then
  echo "Build cache: disabled (SUPPORTPORTAL_NO_BUILD_CACHE=1)"
else
  echo "Build cache: enabled"
fi

if [[ "$_build_progress" == "plain" ]]; then
  echo "Build progress: plain"
else
  echo "Build progress: auto"
fi

echo "requirements.base.txt: $(git hash-object requirements.base.txt)"
echo "requirements.ml.txt: $(git hash-object requirements.ml.txt)"

health_attempts="${SUPPORTPORTAL_HEALTH_ATTEMPTS:-180}"
health_interval_seconds="${SUPPORTPORTAL_HEALTH_INTERVAL_SECONDS:-2}"
[[ "$health_attempts" =~ ^[1-9][0-9]*$ ]] || die "SUPPORTPORTAL_HEALTH_ATTEMPTS must be a positive integer."
[[ "$health_interval_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "SUPPORTPORTAL_HEALTH_INTERVAL_SECONDS must be non-negative."

wait_for_stack_health() {
  local expected_ref="$1"
  local attempt
  local payload

  for (( attempt = 1; attempt <= health_attempts; attempt++ )); do
    payload="$(curl -fsS "http://127.0.0.1:${health_port}/health" 2>/dev/null || true)"
    if [[ -n "$payload" ]] && python3 - "$payload" "$expected_ref" "$db_mode" <<'PY'
import json
import sys

try:
    payload = json.loads(sys.argv[1])
except (TypeError, ValueError):
    raise SystemExit(1)

if payload.get("status") != "ok":
    raise SystemExit(1)
if str((payload.get("app_build") or {}).get("ref") or "") != sys.argv[2]:
    raise SystemExit(1)
if sys.argv[3] == "remote":
    checks = {
        "ticket_storage": "postgres",
        "knowledge_storage": "postgres",
        "rag_service": "ok",
    }
    for key, expected in checks.items():
        if payload.get(key) != expected:
            raise SystemExit(1)
prompt_runtime = payload.get("prompt_runtime")
if not isinstance(prompt_runtime, dict) or prompt_runtime.get("status") != "loaded":
    raise SystemExit(1)
raise SystemExit(0)
PY
    then
      printf '%s\n' "$payload"
      return 0
    fi
    if (( attempt < health_attempts )); then
      sleep "$health_interval_seconds"
    fi
  done
  return 1
}

print_startup_diagnostics() {
  warn "Single-host stack health gate failed; collecting redacted diagnostics."
  podman-compose "${compose_args[@]}" ps 2>&1 | tail -n 80 >&2 || true
  podman ps -a --filter name=deployment_ --format '{{.Names}} {{.Status}}' 2>&1 | tail -n 80 >&2 || true
  for service in api rag_api worker_query worker_aux rag_worker; do
    podman logs --tail 100 "deployment_${service}_1" 2>&1 \
      | sed -E 's#(postgres(?:ql)?://)[^ @]+@#\1[REDACTED]@#g; s#(Bearer )[A-Za-z0-9._-]+#\1[REDACTED]#g' \
      | tail -n 100 >&2 || true
  done
}

previous_image="$(podman inspect --format '{{.ImageName}}' deployment_api_1 2>/dev/null || true)"
previous_image_id="$(podman inspect --format '{{.Image}}' deployment_api_1 2>/dev/null || true)"
previous_ref="${previous_image##*:}"
new_image="$APP_RUNTIME_IMAGE"
new_ref="$APP_BUILD_REF"
rollback_image=""

if [[ -n "$previous_image" && -n "$previous_image_id" ]]; then
  rollback_image="localhost/supportportal-app:rollback-${new_ref}-$$"
  podman tag "$previous_image_id" "$rollback_image"
fi

restore_previous_stack() {
  if [[ -z "$rollback_image" ]]; then
    warn "New stack failed and no previous image ID is available for automatic restore."
    return 1
  fi

  warn "New stack failed; restoring previous image $previous_image from its rollback tag."
  export APP_RUNTIME_IMAGE="$rollback_image"
  export APP_BUILD_REF="$previous_ref"
  podman-compose "${compose_args[@]}" down >/dev/null 2>&1 || true
  if start_stack "$previous_ref"; then
    warn "Restored previous image $previous_image."
    return 0
  fi
  warn "Failed to restore previous image $previous_image."
  return 1
}

start_stack() {
  local expected_ref="$1"
  if ! podman-compose "${compose_args[@]}" up -d --no-build redis rag_api api ws_gateway nginx; then
    return 1
  fi
  if ! wait_for_stack_health "$expected_ref"; then
    return 1
  fi
  if ! podman-compose "${compose_args[@]}" up -d --no-build rag_worker worker_query worker_aux; then
    return 1
  fi
  wait_for_stack_health "$expected_ref"
}

podman-compose "${compose_args[@]}" config >/dev/null

build_args=(build)
if (( _cache_disabled )); then
  build_args+=(--no-cache)
fi

if [[ "$_build_progress" == "plain" ]]; then
  export BUILDKIT_PROGRESS=plain
  export BUILDAH_PROGRESS=plain
fi

podman-compose "${compose_args[@]}" "${build_args[@]}"
"$SCRIPT_DIR/cleanup_single_host_aux_stack.sh"
podman-compose "${compose_args[@]}" down
remaining_containers="$(podman ps -a --filter name=deployment_ --format '{{.Names}}' 2>/dev/null || true)"
if [[ -n "$remaining_containers" ]]; then
  print_startup_diagnostics
  die "Deployment containers remain after compose down; refusing to start a second stack: $remaining_containers"
fi

if ! start_stack "$new_ref"; then
  print_startup_diagnostics
  restore_previous_stack || true
  exit 1
fi
podman-compose "${compose_args[@]}" ps
