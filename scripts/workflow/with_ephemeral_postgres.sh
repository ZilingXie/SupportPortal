#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

# Run one command against a throwaway PostgreSQL that exists only for this
# invocation. The wrapper deliberately never loads the repository .env and
# never touches podman-compose: the only database reachable through the
# injected variables is the container created below, so a PostgreSQL test
# cannot fall through to a shared or remote DSN by accident.

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/workflow/with_ephemeral_postgres.sh --dsn-env NAME [--dsn-env NAME]... [--image REF] -- <command> [args...]

Starts a temporary PostgreSQL container (default docker.io/pgvector/pgvector:pg16)
bound to a dynamic 127.0.0.1 port, exports its DSN into every --dsn-env NAME,
sets PYTHON_DOTENV_DISABLED=1, runs the command, then removes the container and
its volume. Only the named variables are set: TICKET_DB_DSN / PGVECTOR_DSN stay
untouched unless listed explicitly. The Podman machine is started when it is
stopped and stopped again afterwards when nothing else is running in it.
USAGE
}

note() {
  printf '[ephemeral-postgres] %s\n' "$*" >&2
}

dsn_env_names=()
image="docker.io/pgvector/pgvector:pg16"
command_seen=0

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --dsn-env)
      [[ "$#" -ge 2 ]] || { usage; die "Missing value for --dsn-env."; }
      dsn_env_names+=("$2")
      shift 2
      ;;
    --dsn-env=*)
      dsn_env_names+=("${1#--dsn-env=}")
      shift
      ;;
    --image)
      [[ "$#" -ge 2 ]] || { usage; die "Missing value for --image."; }
      image="$2"
      shift 2
      ;;
    --image=*)
      image="${1#--image=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      command_seen=1
      break
      ;;
    *)
      usage
      die "Unsupported argument: $1"
      ;;
  esac
done

if (( ! command_seen )) || [[ "$#" -eq 0 ]]; then
  usage
  die "A command to run is required after '--'."
fi

if [[ "${#dsn_env_names[@]}" -eq 0 ]]; then
  usage
  die "At least one --dsn-env NAME is required; without it the PostgreSQL tests would silently skip."
fi

for name in "${dsn_env_names[@]}"; do
  [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "Invalid --dsn-env name: '$name' (expected a shell variable name)."
done

[[ -n "$image" ]] || die "--image must not be empty."

require_command podman

machine_name="${SUPPORTPORTAL_PODMAN_MACHINE:-podman-machine-default}"
label_key="io.supportportal.ephemeral-postgres.run"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$-${RANDOM}"
container_name="sp-ephemeral-pg-${run_id}"
volume_name="sp-ephemeral-pgdata-${run_id}"
pg_user="ephemeral"
pg_db="ephemeral"
pg_password="pw-$$-${RANDOM}${RANDOM}"
ready_attempts="${SUPPORTPORTAL_EPHEMERAL_PG_READY_ATTEMPTS:-60}"
ready_interval="${SUPPORTPORTAL_EPHEMERAL_PG_READY_INTERVAL_SECONDS:-1}"
[[ "$ready_attempts" =~ ^[1-9][0-9]*$ ]] || die "SUPPORTPORTAL_EPHEMERAL_PG_READY_ATTEMPTS must be a positive integer."
[[ "$ready_interval" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "SUPPORTPORTAL_EPHEMERAL_PG_READY_INTERVAL_SECONDS must be non-negative."

started_machine=0
container_requested=0
volume_requested=0
child_pid=""

container_run_label() {
  podman container inspect --format "{{ index .Config.Labels \"$label_key\" }}" "$container_name" 2>/dev/null || true
}

volume_run_label() {
  podman volume inspect --format "{{ index .Labels \"$label_key\" }}" "$volume_name" 2>/dev/null || true
}

cleanup() {
  local rc=$?
  local owner
  local others
  trap - EXIT INT TERM

  if (( container_requested )) && podman container exists "$container_name" >/dev/null 2>&1; then
    owner="$(container_run_label)"
    if [[ "$owner" == "$run_id" ]]; then
      if podman rm -f -t 5 "$container_name" >/dev/null 2>&1; then
        note "Removed container $container_name."
      else
        note "Failed to remove container $container_name; remove it manually."
        (( rc != 0 )) || rc=1
      fi
    else
      note "Refusing to remove container $container_name: run label '$owner' does not belong to this run ($run_id)."
      (( rc != 0 )) || rc=1
    fi
  fi

  if (( volume_requested )) && podman volume exists "$volume_name" >/dev/null 2>&1; then
    owner="$(volume_run_label)"
    if [[ "$owner" == "$run_id" ]]; then
      if podman volume rm "$volume_name" >/dev/null 2>&1; then
        note "Removed volume $volume_name."
      else
        note "Failed to remove volume $volume_name; remove it manually."
        (( rc != 0 )) || rc=1
      fi
    else
      note "Refusing to remove volume $volume_name: run label '$owner' does not belong to this run ($run_id)."
      (( rc != 0 )) || rc=1
    fi
  fi

  if (( started_machine )); then
    others="$(podman ps --format '{{.Names}}' 2>/dev/null || true)"
    if [[ -z "$others" ]]; then
      if podman machine stop "$machine_name" >/dev/null 2>&1; then
        note "Stopped Podman machine $machine_name (it was stopped before this run)."
      else
        note "Failed to stop Podman machine $machine_name; stop it manually with 'podman machine stop $machine_name'."
        # POD-001: a failed restore-to-stopped is a failed run — the wrapper
        # must not report success while the machine keeps running in the
        # background. Earlier cleanup results (container/volume removal)
        # stay recorded above.
        (( rc != 0 )) || rc=1
      fi
    else
      note "Leaving Podman machine $machine_name running: other containers are active: $(printf '%s' "$others" | tr '\n' ' ')"
    fi
  fi

  exit "$rc"
}

on_signal() {
  local signal_number="$1"
  local waited=0

  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    while kill -0 "$child_pid" 2>/dev/null && (( waited < 50 )); do
      sleep 0.1
      waited=$((waited + 1))
    done
    kill -KILL "$child_pid" 2>/dev/null || true
  fi
  exit $((128 + signal_number))
}

trap cleanup EXIT
trap 'on_signal 2' INT
trap 'on_signal 15' TERM

machine_state="$(podman machine inspect "$machine_name" --format '{{.State}}' 2>/dev/null || echo unknown)"
case "$machine_state" in
  running)
    note "Podman machine $machine_name is already running; it will be left running."
    ;;
  stopped)
    note "Starting Podman machine $machine_name for this run."
    podman machine start "$machine_name" >/dev/null || die "Failed to start Podman machine $machine_name."
    started_machine=1
    ;;
  *)
    die "Podman machine $machine_name is in state '$machine_state'; expected running or stopped."
    ;;
esac
podman info >/dev/null 2>&1 || die "Podman is not reachable after machine check; verify 'podman info'."

note "Run id: $run_id"
volume_requested=1
podman volume create --label "$label_key=$run_id" "$volume_name" >/dev/null \
  || die "Failed to create volume $volume_name."

container_requested=1
podman run -d \
  --name "$container_name" \
  --label "$label_key=$run_id" \
  --restart=no \
  --publish 127.0.0.1::5432 \
  --env "POSTGRES_USER=$pg_user" \
  --env "POSTGRES_PASSWORD=$pg_password" \
  --env "POSTGRES_DB=$pg_db" \
  --volume "$volume_name:/var/lib/postgresql/data" \
  "$image" >/dev/null \
  || die "Failed to start PostgreSQL container $container_name from $image."

port_mapping="$(podman port "$container_name" 5432 2>/dev/null | head -n 1 || true)"
host_port="${port_mapping##*:}"
[[ "$host_port" =~ ^[0-9]+$ ]] || die "Unable to determine the dynamic host port for $container_name (got '$port_mapping')."
case "$port_mapping" in
  127.0.0.1:*) ;;
  *) die "Ephemeral PostgreSQL must be bound to 127.0.0.1, got '$port_mapping'." ;;
esac

ready=0
for (( attempt = 1; attempt <= ready_attempts; attempt++ )); do
  # -h 127.0.0.1 targets the final TCP listener; the image's init-phase
  # temporary server only answers on the unix socket and must not count.
  if podman exec "$container_name" pg_isready -h 127.0.0.1 -U "$pg_user" -d "$pg_db" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if (( attempt < ready_attempts )); then
    sleep "$ready_interval"
  fi
done
(( ready )) || die "PostgreSQL in $container_name did not become ready after $ready_attempts attempts."

dsn="postgresql://${pg_user}:${pg_password}@127.0.0.1:${host_port}/${pg_db}?sslmode=disable"
note "PostgreSQL ready on 127.0.0.1:$host_port (container $container_name, volume $volume_name)."

export PYTHON_DOTENV_DISABLED=1
for name in "${dsn_env_names[@]}"; do
  export "$name=$dsn"
done
note "Injected DSN into: ${dsn_env_names[*]}"

set +e
"$@" &
child_pid=$!
wait "$child_pid"
child_rc=$?
set -e
child_pid=""

exit "$child_rc"
