#!/usr/bin/env bash
# Runtime test for the ParadeDB data guard (REQUIRE_EXISTING_PGDATA) using the
# REAL paradedb image the deployment runs, against the same entrypoint command
# wrapper shape as register_weknora_task_definitions.sh:
#
#   A. guard=true + empty volume  -> MUST refuse to start (non-zero, message)
#   B. guard off + empty volume   -> initializes successfully (pg_isready ok)
#   C. guard=true + initialized   -> starts again (upgrade path keeps data)
#
# Requires: podman, openssl, jq, and ECR pull access for
# supportportal/weknora:base-paradedb-v0.22.6-pg17.
set -uo pipefail

AWS_CLI_BIN="${AWS_CLI_BIN:-aws}"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$("$AWS_CLI_BIN" sts get-caller-identity --query Account --output text)"
IMAGE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/supportportal/weknora:base-paradedb-v0.22.6-pg17"

FAILURES=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1" >&2; FAILURES=$((FAILURES + 1)); }

WORK_DIR="$HOME/.tmp-weknora-guard-test-$$-$(date +%s)"
DATA_DIR="$WORK_DIR/pgdata"
mkdir -p "$DATA_DIR"
cleanup() { podman rm -f wkguard >/dev/null 2>&1 || true; rm -rf "$WORK_DIR"; }
trap cleanup EXIT

# Must stay in sync with PARADEDB_CMD in register_weknora_task_definitions.sh.
read -r -d '' WRAPPER <<'EOF' || true
set -e
TLS_DIR=/var/lib/postgresql/tls
mkdir -p "$TLS_DIR"
printenv DB_TLS_SERVER_CERT_PEM > "$TLS_DIR/server.crt"
printenv DB_TLS_SERVER_KEY_PEM > "$TLS_DIR/server.key"
chmod 600 "$TLS_DIR/server.crt" "$TLS_DIR/server.key"
chown postgres:postgres "$TLS_DIR/server.crt" "$TLS_DIR/server.key"
if [ "${REQUIRE_EXISTING_PGDATA:-}" = "true" ] && [ ! -f "$PGDATA/PG_VERSION" ]; then
  echo "REQUIRE_EXISTING_PGDATA=true but $PGDATA/PG_VERSION is missing; refusing to start an empty database" >&2
  exit 1
fi
exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/var/lib/postgresql/tls/server.crt -c ssl_key_file=/var/lib/postgresql/tls/server.key
EOF

# Pin the contract: the register script must still contain the same guard.
REGISTER_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/register_weknora_task_definitions.sh"
if grep -q 'REQUIRE_EXISTING_PGDATA=true but $PGDATA/PG_VERSION is missing' "$REGISTER_SCRIPT"; then
  ok "register script still carries the data guard contract"
else
  bad "register script guard text changed; update this test's WRAPPER to match"
fi

"$AWS_CLI_BIN" ecr get-login-password --region "$REGION" | podman login --username AWS --password-stdin \
  "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null 2>&1
podman pull --quiet "$IMAGE" >/dev/null 2>&1 || { echo "cannot pull $IMAGE" >&2; exit 1; }

openssl req -x509 -newkey rsa:2048 -nodes -keyout "$WORK_DIR/tls.key" -out "$WORK_DIR/tls.crt" \
  -days 2 -subj "/CN=guard-test" >/dev/null 2>&1
CERT_PEM="$(cat "$WORK_DIR/tls.crt")"
KEY_PEM="$(cat "$WORK_DIR/tls.key")"

run_guard_foreground() { # guard_value -> RUN_RC / RUN_OUT (expects quick exit)
  local guard="$1"
  RUN_OUT="$(podman run --rm \
    -v "$DATA_DIR":/var/lib/postgresql/data:Z \
    -e POSTGRES_USER=weknora -e POSTGRES_PASSWORD=guardtest -e POSTGRES_DB=weknora \
    -e PGDATA=/var/lib/postgresql/data/pgdata \
    -e REQUIRE_EXISTING_PGDATA="$guard" \
    -e DB_TLS_SERVER_CERT_PEM="$CERT_PEM" \
    -e DB_TLS_SERVER_KEY_PEM="$KEY_PEM" \
    --entrypoint sh "$IMAGE" -ec "$WRAPPER" 2>&1)"
  RUN_RC=$?
}

run_guard_detached() { # guard_value -> RUN_RC / RUN_OUT (postgres serves; poll readiness)
  local guard="$1" name="wkguard-detached"
  podman rm -f "$name" >/dev/null 2>&1 || true
  podman run -d --name "$name" \
    -v "$DATA_DIR":/var/lib/postgresql/data:Z \
    -e POSTGRES_USER=weknora -e POSTGRES_PASSWORD=guardtest -e POSTGRES_DB=weknora \
    -e PGDATA=/var/lib/postgresql/data/pgdata \
    -e REQUIRE_EXISTING_PGDATA="$guard" \
    -e DB_TLS_SERVER_CERT_PEM="$CERT_PEM" \
    -e DB_TLS_SERVER_KEY_PEM="$KEY_PEM" \
    --entrypoint sh "$IMAGE" -ec "$WRAPPER" >/dev/null 2>&1 || { RUN_RC=$?; RUN_OUT="podman run failed"; return; }
  RUN_RC=0
  for _ in $(seq 1 45); do
    if podman exec "$name" pg_isready -U weknora -d weknora >/dev/null 2>&1; then
      RUN_OUT="$(podman logs "$name" 2>&1)"
      podman rm -f "$name" >/dev/null 2>&1 || true
      return
    fi
    if ! podman container exists "$name" 2>/dev/null; then
      RUN_RC=1
      RUN_OUT="$(podman logs "$name" 2>&1 || true)"
      podman rm -f "$name" >/dev/null 2>&1 || true
      return
    fi
    sleep 2
  done
  RUN_RC=124
  RUN_OUT="$(podman logs "$name" 2>&1 || true)"
  podman rm -f "$name" >/dev/null 2>&1 || true
}

echo "== A: guard=true on an empty volume must refuse to start =="
run_guard_foreground true
if [ "$RUN_RC" -ne 0 ] && grep -q "refusing to start an empty database" <<<"$RUN_OUT"; then
  ok "empty volume with guard refused to start"
else
  bad "empty volume with guard did not refuse (rc=$RUN_RC): $(head -3 <<<"$RUN_OUT")"
fi
[ ! -f "$DATA_DIR/pgdata/PG_VERSION" ] && ok "refusal created no database" || bad "refusal still initialized a database"

echo "== B: first initialization (guard off) succeeds =="
run_guard_detached false
if [ "$RUN_RC" -eq 0 ] && grep -q "database system is ready to accept connections" <<<"$RUN_OUT"; then
  ok "initialization with guard off succeeded"
else
  bad "initialization failed (rc=$RUN_RC): $(tail -5 <<<"$RUN_OUT")"
fi
[ -f "$DATA_DIR/pgdata/PG_VERSION" ] && ok "PG_VERSION created" || bad "PG_VERSION missing after init"

echo "== C: guard=true on the initialized volume starts (upgrade path) =="
run_guard_detached true
if [ "$RUN_RC" -eq 0 ] && grep -q "database system is ready to accept connections" <<<"$RUN_OUT"; then
  ok "initialized volume with guard started successfully"
else
  bad "initialized volume with guard failed (rc=$RUN_RC): $(tail -5 <<<"$RUN_OUT")"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL PGDATA GUARD RUNTIME TESTS PASSED"
else
  echo "FAILED CHECKS: $FAILURES" >&2
  exit 1
fi
