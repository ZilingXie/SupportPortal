#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

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

env_file="$(repo_root)/.env"
[[ -f "$env_file" ]] || die "Root .env not found at $env_file"

set -a
# shellcheck source=/dev/null
source "$env_file"
set +a

[[ -n "${TICKET_DB_DSN:-}" ]] || die "TICKET_DB_DSN is required in $env_file"
[[ -n "${PGVECTOR_DSN:-}" ]] || die "PGVECTOR_DSN is required in $env_file"

"$SCRIPT_DIR/ensure_local_db_relay.sh"

export APP_BUILD_REF
export APP_BUILD_TIME
export APP_RUNTIME_IMAGE
APP_BUILD_REF="$(git rev-parse --short=12 HEAD)"
APP_BUILD_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
APP_RUNTIME_IMAGE="localhost/supportportal-app:${APP_BUILD_REF}"

info "Current main commit: $APP_BUILD_REF"
info "Build timestamp: $APP_BUILD_TIME"
info "Runtime image: $APP_RUNTIME_IMAGE"
info "Rebuilding single-host stack in full mode."

"$SCRIPT_DIR/cleanup_single_host_aux_stack.sh"
podman-compose -f deployment/docker-compose.single-host.yml down
podman-compose -f deployment/docker-compose.single-host.yml up -d --build
podman-compose -f deployment/docker-compose.single-host.yml ps
curl -sS http://127.0.0.1:8080/health
