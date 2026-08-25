#!/usr/bin/env bash
set -Eeuo pipefail

# One-shot, idempotent bootstrap of the full account-case schema in the
# /automation/production split database (supportportal_production by default).
# DDL runs through the migration DSN role; runtime grants are applied
# automatically by PostgresTicketRepository.initialize() when the migration
# and runtime roles differ. Safe to rerun on every production split deploy.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/deployment/docker-compose.single-host.yml"
ENV_FILE="${PROJECT_ROOT}/.env"

log() { printf '[automation-production-bootstrap] %s\n' "$*"; }
fail() { printf '[automation-production-bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

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

database_name() {
  printf '%s' "$1" | sed -E 's#^[a-zA-Z0-9+]+://[^/]*/##' | sed -E 's#/.*##' | sed -E 's#\?.*##'
}

command -v docker >/dev/null 2>&1 || fail 'Missing command: docker'
[[ -f "$ENV_FILE" ]] || fail "Missing $ENV_FILE"

dsn="$(resolve_env_value AUTOMATION_PRODUCTION_DB_DSN)"
dsn="${dsn:-$(resolve_env_value PRODUCTION_TICKET_DB_DSN)}"
migration_dsn="$(resolve_env_value AUTOMATION_PRODUCTION_DB_MIGRATION_DSN)"
pgvector_dsn="$(resolve_env_value PGVECTOR_DSN)"
schema="$(resolve_env_value AUTOMATION_PRODUCTION_DB_SCHEMA)"
schema="${schema:-supportportal_production}"

[[ -n "$dsn" ]] || fail 'AUTOMATION_PRODUCTION_DB_DSN or PRODUCTION_TICKET_DB_DSN is required'
[[ -n "$migration_dsn" ]] || fail 'AUTOMATION_PRODUCTION_DB_MIGRATION_DSN is required (the runtime role cannot create the split production schema)'
[[ -n "$pgvector_dsn" ]] || fail 'PGVECTOR_DSN is required'

if [[ -n "$(resolve_env_value TICKET_DB_DSN)" && "$dsn" == "$(resolve_env_value TICKET_DB_DSN)" ]]; then
  fail 'production DB DSN must differ from TICKET_DB_DSN (staging main database)'
fi
if [[ "$(database_name "$dsn")" != "$(database_name "$migration_dsn")" ]]; then
  fail "AUTOMATION_PRODUCTION_DB_MIGRATION_DSN must target the same database as the production DSN (got '$(database_name "$migration_dsn")' vs '$(database_name "$dsn")')"
fi
if [[ "$dsn" == "$migration_dsn" ]]; then
  log 'WARNING: migration DSN equals the runtime DSN; runtime-role grants will be skipped.'
fi

cd "$PROJECT_ROOT"
log "Bootstrapping schema ${schema} on database $(database_name "$dsn") via the runtime_bootstrap service"
TICKET_DB_DSN="$dsn" \
TICKET_DB_SCHEMA="$schema" \
TICKET_DB_MIGRATION_DSN="$migration_dsn" \
TICKET_DB_APPLICATION_NAME=supportportal-bootstrap-automation-production \
PGVECTOR_DSN="$pgvector_dsn" \
PGVECTOR_SCHEMA="$schema" \
docker compose \
  --project-name supportportal-automation-production-bootstrap \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  --profile bootstrap \
  run --rm --no-deps runtime_bootstrap
log "Schema ${schema} bootstrap completed"
