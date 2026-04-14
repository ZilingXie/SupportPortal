#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

require_command python3

readonly DEFAULT_HOSTADDR="192.168.127.254"
readonly DEFAULT_LISTEN_PORT="15433"
readonly DEFAULT_UPSTREAM_PORT="5432"

listen_port="${SUPPORTPORTAL_LOCAL_DB_RELAY_PORT:-$DEFAULT_LISTEN_PORT}"
upstream_port="${SUPPORTPORTAL_LOCAL_DB_RELAY_UPSTREAM_PORT:-$DEFAULT_UPSTREAM_PORT}"
pid_file="${SUPPORTPORTAL_LOCAL_DB_RELAY_PID_FILE:-/tmp/supportportal_local_db_relay.pid}"
log_file="${SUPPORTPORTAL_LOCAL_DB_RELAY_LOG_FILE:-/tmp/supportportal_local_db_relay.log}"
listen_host="${SUPPORTPORTAL_LOCAL_DB_RELAY_LISTEN_HOST:-0.0.0.0}"

repo_dir="$(repo_root)"
env_file="$repo_dir/.env"
[[ -f "$env_file" ]] || die "Root .env not found at $env_file"

set -a
# shellcheck source=/dev/null
source "$env_file"
set +a

parse_dsn() {
  local dsn="$1"
  python3 - "$dsn" <<'PY'
import json
import sys
from urllib.parse import parse_qs, urlsplit

dsn = sys.argv[1].strip()
if not dsn:
    print("{}")
    raise SystemExit(0)

parsed = urlsplit(dsn)
query = parse_qs(parsed.query)
payload = {
    "host": parsed.hostname or "",
    "port": parsed.port or 5432,
    "hostaddr": (query.get("hostaddr") or [""])[0],
}
print(json.dumps(payload))
PY
}

relay_config_json() {
  local ticket_dsn="${TICKET_DB_DSN:-}"
  local vector_dsn="${PGVECTOR_DSN:-}"
  python3 - "$ticket_dsn" "$vector_dsn" "$DEFAULT_HOSTADDR" "$DEFAULT_LISTEN_PORT" <<'PY'
import json
import sys
from urllib.parse import parse_qs, urlsplit

ticket_dsn, vector_dsn, hostaddr_expected, relay_port_expected = sys.argv[1:]
relay_port_expected = int(relay_port_expected)


def parse(dsn: str) -> dict[str, object] | None:
    dsn = dsn.strip()
    if not dsn:
        return None
    parsed = urlsplit(dsn)
    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname or "",
        "port": parsed.port or 5432,
        "hostaddr": (query.get("hostaddr") or [""])[0],
        "requires_relay": (
            (query.get("hostaddr") or [""])[0] == hostaddr_expected
            and (parsed.port or 5432) == relay_port_expected
        ),
    }


ticket = parse(ticket_dsn)
vector = parse(vector_dsn)
selected = None
for candidate in (ticket, vector):
    if candidate and candidate["requires_relay"]:
        selected = candidate
        break

print(json.dumps({
    "relay_required": bool(selected),
    "upstream_host": (selected or {}).get("host", ""),
}))
PY
}

relay_healthy() {
  python3 - "$listen_port" <<'PY'
import socket
import struct
import sys

port = int(sys.argv[1])
sock = None
try:
    sock = socket.create_connection(("127.0.0.1", port), timeout=1.5)
    sock.settimeout(1.5)
    sock.sendall(struct.pack("!II", 8, 80877103))
    data = sock.recv(16)
    raise SystemExit(0 if data.startswith(b"S") else 1)
except OSError:
    raise SystemExit(1)
finally:
    if sock is not None:
        sock.close()
PY
}

port_in_use() {
  python3 - "$listen_port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = None
try:
    sock = socket.create_connection(("127.0.0.1", port), timeout=0.5)
    raise SystemExit(0)
except OSError:
    raise SystemExit(1)
finally:
    if sock is not None:
        sock.close()
PY
}

cleanup_pid_file_process() {
  [[ -f "$pid_file" ]] || return 0

  local pid
  pid="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
  fi
  rm -f "$pid_file"
}

start_relay() {
  local upstream_host="$1"
  local relay_pid

  rm -f "$pid_file"
  : > "$log_file"
  relay_pid="$(
    python3 - "$SCRIPT_DIR/local_db_relay.py" "$log_file" "$listen_host" "$listen_port" "$upstream_host" "$upstream_port" <<'PY'
import os
import subprocess
import sys

script_path, log_path, listen_host, listen_port, target_host, target_port = sys.argv[1:]
env = dict(os.environ)
env["SUPPORTPORTAL_LOCAL_DB_RELAY_LISTEN_HOST"] = listen_host
env["SUPPORTPORTAL_LOCAL_DB_RELAY_PORT"] = listen_port
env["SUPPORTPORTAL_LOCAL_DB_RELAY_TARGET_HOST"] = target_host
env["SUPPORTPORTAL_LOCAL_DB_RELAY_TARGET_PORT"] = target_port

with open(log_path, "ab", buffering=0) as log_handle:
    proc = subprocess.Popen(
        ["python3", script_path],
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
        close_fds=True,
    )
print(proc.pid)
PY
  )"
  printf '%s\n' "$relay_pid" > "$pid_file"

  for _ in {1..20}; do
    if relay_healthy; then
      info "Started local DB relay on $listen_host:$listen_port -> $upstream_host:$upstream_port"
      return 0
    fi
    sleep 0.2
  done

  cleanup_pid_file_process
  die "Local DB relay failed to become healthy. See $log_file"
}

config="$(relay_config_json)"
relay_required="$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin)["relay_required"] else "false")' <<<"$config")"
upstream_host="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["upstream_host"])' <<<"$config")"

if [[ "$relay_required" != "true" ]]; then
  info "Local DB relay is not required for current DSN configuration."
  exit 0
fi

if relay_healthy; then
  info "Reusing existing healthy local DB relay on 127.0.0.1:$listen_port."
  exit 0
fi

if [[ -f "$pid_file" ]]; then
  cleanup_pid_file_process
fi

if port_in_use; then
  die "Local DB relay port $listen_port is occupied by an unknown unhealthy listener."
fi

start_relay "$upstream_host"
