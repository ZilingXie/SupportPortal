#!/usr/bin/env bash

set -euo pipefail

load_supportportal_local_env() {
  local root_path="$1"
  local require_base_env="${2:-optional}"
  local env_file="$root_path/.env"

  if [[ "$require_base_env" == "required" && ! -f "$env_file" ]]; then
    die "Root .env not found at $env_file. Copy .env.example to .env for shared API/model settings."
  fi

  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$env_file"
    set +a
  fi

  export LOCAL_POSTGRES_USER="${LOCAL_POSTGRES_USER:-supportportal}"
  export LOCAL_POSTGRES_PASSWORD="${LOCAL_POSTGRES_PASSWORD:-supportportal}"
  export LOCAL_POSTGRES_DB="${LOCAL_POSTGRES_DB:-supportportal}"
  export LOCAL_POSTGRES_HOST_PORT="${LOCAL_POSTGRES_HOST_PORT:-15432}"
  export LOCAL_TICKET_DB_SCHEMA="${LOCAL_TICKET_DB_SCHEMA:-supportportal}"
  export LOCAL_PGVECTOR_SCHEMA="${LOCAL_PGVECTOR_SCHEMA:-supportportal}"
  export LOCAL_PGVECTOR_TABLE="${LOCAL_PGVECTOR_TABLE:-docagent_chunks_bge_m3_1024}"
  export LOCAL_PGVECTOR_DIM="${LOCAL_PGVECTOR_DIM:-1024}"
}

supportportal_local_db_dsn() {
  local host="$1"
  local port="$2"
  printf 'postgresql://%s:%s@%s:%s/%s?sslmode=disable\n' \
    "$LOCAL_POSTGRES_USER" \
    "$LOCAL_POSTGRES_PASSWORD" \
    "$host" \
    "$port" \
    "$LOCAL_POSTGRES_DB"
}

export_supportportal_local_container_db_env() {
  local dsn

  dsn="$(supportportal_local_db_dsn "local_postgres" "5432")"
  export TICKET_DB_DSN="$dsn"
  export PGVECTOR_DSN="$dsn"
  export TICKET_DB_SCHEMA="$LOCAL_TICKET_DB_SCHEMA"
  export PGVECTOR_SCHEMA="$LOCAL_PGVECTOR_SCHEMA"
  export PGVECTOR_TABLE="$LOCAL_PGVECTOR_TABLE"
  export PGVECTOR_DIM="$LOCAL_PGVECTOR_DIM"
}

export_supportportal_local_host_db_env() {
  local dsn

  dsn="$(supportportal_local_db_dsn "127.0.0.1" "$LOCAL_POSTGRES_HOST_PORT")"
  export TICKET_DB_DSN="$dsn"
  export PGVECTOR_DSN="$dsn"
  export TICKET_DB_SCHEMA="$LOCAL_TICKET_DB_SCHEMA"
  export PGVECTOR_SCHEMA="$LOCAL_PGVECTOR_SCHEMA"
  export PGVECTOR_TABLE="$LOCAL_PGVECTOR_TABLE"
  export PGVECTOR_DIM="$LOCAL_PGVECTOR_DIM"
  export RUNTIME_PROFILE="${RUNTIME_PROFILE:-local_lightweight}"
}
