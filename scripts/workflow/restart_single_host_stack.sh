#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"
# shellcheck source=./_local_db_env.sh
source "$SCRIPT_DIR/_local_db_env.sh"

require_command git
require_command podman-compose
require_command curl

ensure_root_workspace_on_main

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
local_env_file="$(repo_root)/.env.local"

set -a
# shellcheck source=/dev/null
source "$env_file"
set +a
base_stack_db_mode="${STACK_DB_MODE:-remote}"

if (( use_local_env )); then
  [[ -f "$local_env_file" ]] || die "Root .env.local not found at $local_env_file. Copy .env.local.example to .env.local for local development defaults."
  set -a
  # shellcheck source=/dev/null
  source "$local_env_file"
  set +a
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

echo "backend/requirements.base.txt: $(git hash-object backend/requirements.base.txt)"
echo "backend/requirements.ml.txt: $(git hash-object backend/requirements.ml.txt)"

"$SCRIPT_DIR/cleanup_single_host_aux_stack.sh"
podman-compose "${compose_args[@]}" down

up_args=(up -d --build)
if (( _cache_disabled )); then
  up_args+=(--no-cache)
fi

if [[ "$_build_progress" == "plain" ]]; then
  export BUILDKIT_PROGRESS=plain
  export BUILDAH_PROGRESS=plain
fi

podman-compose "${compose_args[@]}" "${up_args[@]}"
podman-compose "${compose_args[@]}" ps
curl -sS "http://127.0.0.1:${health_port}/health"
