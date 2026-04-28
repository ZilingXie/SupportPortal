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

db_mode="local"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --db)
      [[ "$#" -ge 2 ]] || die "Missing value for --db. Expected local or remote."
      db_mode="$2"
      shift 2
      ;;
    --db=*)
      db_mode="${1#--db=}"
      shift
      ;;
    *)
      die "Unsupported argument: $1"
      ;;
  esac
done

case "$db_mode" in
  local|remote) ;;
  *)
    die "Unsupported --db mode: $db_mode (expected local or remote)"
    ;;
esac

git fetch origin

current_main="$(git rev-parse HEAD)"
origin_main="$(git rev-parse origin/main)"
if [[ "$current_main" != "$origin_main" ]]; then
  die "Local main must match origin/main before restarting. Run 'git pull --ff-only origin main' from the root workspace first."
fi

env_file="$(repo_root)/.env"
[[ -f "$env_file" ]] || die "Root .env not found at $env_file"

lightweight_compose_file="deployment/docker-compose.single-host.local-lightweight.yml"
[[ -f "$lightweight_compose_file" ]] || die "Local lightweight compose override not found at $lightweight_compose_file"

compose_args=(
  -f deployment/docker-compose.single-host.yml
  -f "$lightweight_compose_file"
)
health_port=8080

if [[ "$db_mode" == "local" ]]; then
  local_db_compose_file="deployment/docker-compose.single-host.local-db.yml"
  [[ -f "$local_db_compose_file" ]] || die "Local DB compose override not found at $local_db_compose_file"
  load_supportportal_local_env "$(repo_root)" "required"
  export_supportportal_local_container_db_env
  compose_args+=(-f "$local_db_compose_file")
  health_port="${NGINX_HOST_PORT:-8080}"
else
  set -a
  # shellcheck source=/dev/null
  source "$env_file"
  set +a

  [[ -n "${TICKET_DB_DSN:-}" ]] || die "TICKET_DB_DSN is required in $env_file"
  [[ -n "${PGVECTOR_DSN:-}" ]] || die "PGVECTOR_DSN is required in $env_file"
  health_port="${NGINX_HOST_PORT:-8080}"
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
if [[ "$db_mode" == "local" ]]; then
  info "Rebuilding single-host stack in lightweight local mode with local Postgres."
  info "Local Postgres host port: ${LOCAL_POSTGRES_HOST_PORT:-15432}"
  info "Local DB schemas: ticket=$TICKET_DB_SCHEMA pgvector=$PGVECTOR_SCHEMA table=$PGVECTOR_TABLE dim=$PGVECTOR_DIM"
else
  info "Rebuilding single-host stack in lightweight local mode with remote database DSNs from .env."
fi

"$SCRIPT_DIR/cleanup_single_host_aux_stack.sh"
podman-compose "${compose_args[@]}" down
podman-compose "${compose_args[@]}" up -d --build
podman-compose "${compose_args[@]}" ps
curl -sS "http://127.0.0.1:${health_port}/health"
