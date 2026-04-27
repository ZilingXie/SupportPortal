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

git fetch origin

current_main="$(git rev-parse HEAD)"
origin_main="$(git rev-parse origin/main)"
if [[ "$current_main" != "$origin_main" ]]; then
  die "Local main must match origin/main before restarting. Run 'git pull --ff-only origin main' from the root workspace first."
fi

root_path="$(repo_root)"
lightweight_compose_file="deployment/docker-compose.single-host.local-lightweight.yml"
local_db_compose_file="deployment/docker-compose.single-host.local-db.yml"
[[ -f "$lightweight_compose_file" ]] || die "Local lightweight compose override not found at $lightweight_compose_file"
[[ -f "$local_db_compose_file" ]] || die "Local DB compose override not found at $local_db_compose_file"

load_supportportal_local_env "$root_path" "required"
export_supportportal_local_container_db_env

export APP_BUILD_REF
export APP_BUILD_TIME
export APP_RUNTIME_IMAGE
APP_BUILD_REF="$(git rev-parse --short=12 HEAD)"
APP_BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
APP_RUNTIME_IMAGE="localhost/supportportal-app:${APP_BUILD_REF}"

health_port="${NGINX_HOST_PORT:-8080}"
compose_args=(
  -f deployment/docker-compose.single-host.yml
  -f "$lightweight_compose_file"
  -f "$local_db_compose_file"
)

info "Current main commit: $APP_BUILD_REF"
info "Build timestamp: $APP_BUILD_TIME"
info "Runtime image: $APP_RUNTIME_IMAGE"
info "Rebuilding single-host stack in fully local lightweight mode."
info "Local Postgres host port: $LOCAL_POSTGRES_HOST_PORT"
info "Local DB schemas: ticket=$TICKET_DB_SCHEMA pgvector=$PGVECTOR_SCHEMA table=$PGVECTOR_TABLE dim=$PGVECTOR_DIM"

"$SCRIPT_DIR/cleanup_single_host_aux_stack.sh"
podman-compose "${compose_args[@]}" down
podman-compose "${compose_args[@]}" up -d --build
podman-compose "${compose_args[@]}" ps
curl -sS "http://127.0.0.1:${health_port}/health"
