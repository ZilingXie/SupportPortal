#!/usr/bin/env bash
# Runtime test for the ParadeDB data guard (REQUIRE_EXISTING_PGDATA) using the
# REAL paradedb image the deployment runs, against the same entrypoint command
# wrapper shape as register_weknora_task_definitions.sh:
#
#   A. guard=true + empty volume  -> MUST refuse to start (non-zero, message)
#   B. guard off + empty volume   -> initializes successfully (pg_isready ok)
#   C. guard=true + initialized   -> starts again (upgrade path keeps data)
#
# Resource ownership (R4 review finding): every run uses a UNIQUE container
# name, never pre-deletes anything, and the cleanup trap only removes
# containers THIS run successfully created. Pass --self-check to verify the
# ownership behavior itself against a mocked podman (no image needed).
#
# Requires: podman, openssl, jq, and ECR pull access for
# supportportal/weknora:base-paradedb-v0.22.6-pg17 (real mode only).
set -o pipefail

MODE="real"
[[ "${1:-}" == "--self-check" ]] && MODE="self-check"

FAILURES=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1" >&2; FAILURES=$((FAILURES + 1)); }

# Unique per-invocation identity shared by the work dir and container names.
RUN_ID="$$-$(date +%s)"
WORK_DIR="$HOME/.tmp-weknora-guard-test-${RUN_ID}"

# Containers this run successfully created (and therefore owns).
OWNED_CONTAINERS=()

cleanup() {
  local name
  for name in "${OWNED_CONTAINERS[@]}"; do
    [ -n "$name" ] && podman rm -f "$name" >/dev/null 2>&1 || true
  done
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

guard_wrapper() { # prints the wrapper script (kept in sync with PARADEDB_CMD)
  cat <<'EOF'
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
}

# Foreground guard run: expects a quick exit (the refusal path). Runs with
# --rm and no assigned name, so this path never owns a removable container.
run_guard_foreground() { # data_dir guard -> RUN_RC / RUN_OUT
  local data_dir="$1" guard="$2"
  RUN_OUT="$(podman run --rm \
    -v "$data_dir":/var/lib/postgresql/data:Z \
    -e POSTGRES_USER=weknora -e POSTGRES_PASSWORD=guardtest -e POSTGRES_DB=weknora \
    -e PGDATA=/var/lib/postgresql/data/pgdata \
    -e REQUIRE_EXISTING_PGDATA="$guard" \
    -e DB_TLS_SERVER_CERT_PEM="${CERT_PEM:-}" \
    -e DB_TLS_SERVER_KEY_PEM="${KEY_PEM:-}" \
    --entrypoint sh "$IMAGE" -ec "$WRAPPER" 2>&1)"
  RUN_RC=$?
}

# Detached guard run: UNIQUE container name per call (RUN_ID + sequence); a
# name collision fails loudly WITHOUT deleting the colliding container; only
# successful creation records ownership for the cleanup trap.
run_guard_detached() { # data_dir guard -> RUN_RC / RUN_OUT / RUN_CONTAINER
  local data_dir="$1" guard="$2"
  local name="wkguard-${RUN_ID}-${DETACH_SEQ}"
  DETACH_SEQ=$((DETACH_SEQ + 1))
  RUN_CONTAINER=""
  if podman container exists "$name" 2>/dev/null; then
    RUN_RC=1
    RUN_OUT="container name $name already exists; refusing to touch it"
    return
  fi
  if ! podman run -d --name "$name" \
    -v "$data_dir":/var/lib/postgresql/data:Z \
    -e POSTGRES_USER=weknora -e POSTGRES_PASSWORD=guardtest -e POSTGRES_DB=weknora \
    -e PGDATA=/var/lib/postgresql/data/pgdata \
    -e REQUIRE_EXISTING_PGDATA="$guard" \
    -e DB_TLS_SERVER_CERT_PEM="${CERT_PEM:-}" \
    -e DB_TLS_SERVER_KEY_PEM="${KEY_PEM:-}" \
    --entrypoint sh "$IMAGE" -ec "$WRAPPER" >/dev/null 2>&1; then
    RUN_RC=1
    RUN_OUT="podman run failed for $name; nothing was removed"
    return
  fi
  OWNED_CONTAINERS+=("$name")
  RUN_CONTAINER="$name"
  RUN_RC=0
  local _
  for _ in $(seq 1 45); do
    if podman exec "$name" pg_isready -U weknora -d weknora >/dev/null 2>&1; then
      RUN_OUT="$(podman logs "$name" 2>&1)"
      return
    fi
    if ! podman container exists "$name" 2>/dev/null; then
      RUN_RC=1
      RUN_OUT="$(podman logs "$name" 2>&1 || true)"
      return
    fi
    sleep "${GUARD_POLL_SLEEP:-2}"
  done
  RUN_RC=124
  RUN_OUT="$(podman logs "$name" 2>&1 || true)"
}

# ---------------------------------------------------------------------------
# Self-check mode: verify resource ownership against a mocked podman.
# ---------------------------------------------------------------------------
self_check() {
  local dir="$WORK_DIR"
  mkdir -p "$dir/bin"
  # The mock's call log lives OUTSIDE the cleanup-managed work dir so the
  # post-cleanup assertions can still read it.
  local sc_state="$HOME/.tmp-weknora-guard-sc-${RUN_ID}"
  rm -rf "$sc_state"; mkdir -p "$sc_state"
  IMAGE="guard-mock-image"
  WRAPPER="$(guard_wrapper)"

  cat > "$dir/bin/podman" <<'STUB'
#!/usr/bin/env bash
echo "podman $*" >> "${STUB_STATE_DIR}/podman.log"
case "$1 $2" in
  "container exists")
    # Names marked PRE_EXISTING report as existing to force the collision path.
    case "$3" in *pre-existing-*) exit 0 ;; esac
    [ -f "${STUB_STATE_DIR}/alive-$3" ] && exit 0
    exit 1 ;;
esac
if [ "$1" = "run" ]; then
  name=""
  prev=""
  for a in "$@"; do
    if [ "$prev" = "--name" ]; then name="$a"; break; fi
    prev="$a"
  done
  if [ "${STUB_FAIL_RUN:-0}" = "1" ]; then
    exit 1
  fi
  [ -n "$name" ] && touch "${STUB_STATE_DIR}/alive-$name"
  exit 0
fi
if [ "$1 $2" = "exec" ]; then exit 0; fi   # pg_isready succeeds immediately
if [ "$1 $2" = "logs" ]; then echo "database system is ready to accept connections"; exit 0; fi
if [ "$1 $2" = "rm" ]; then
  rm -f "${STUB_STATE_DIR}/alive-$3"
  exit 0
fi
exit 0
STUB
  chmod +x "$dir/bin/podman"
  export STUB_STATE_DIR="$sc_state"
  export PATH="$dir/bin:$PATH"
  export GUARD_POLL_SLEEP=0
  local log="$sc_state/podman.log"

  echo "== self-check 1: name collision keeps the existing container =="
  DETACH_SEQ=0; RUN_ID="pre-existing-x"
  run_guard_detached "$dir/data" true
  if [ "$RUN_RC" -ne 0 ] && ! grep -q "podman rm" "$log"; then
    ok "collision refused without any removal"
  else
    bad "collision path removed something or did not fail (rc=$RUN_RC)"
  fi

  echo "== self-check 2: failed creation never removes anything =="
  : > "$log"
  DETACH_SEQ=0; RUN_ID="round2"
  STUB_FAIL_RUN=1 run_guard_detached "$dir/data" true
  if [ "$RUN_RC" -ne 0 ] && ! grep -q "podman rm" "$log"; then
    ok "failed creation issued zero podman rm"
  else
    bad "failed creation issued a removal (rc=$RUN_RC)"
  fi
  unset STUB_FAIL_RUN

  echo "== self-check 3: two rounds use distinct names and clean only their own =="
  : > "$log"
  DETACH_SEQ=0; RUN_ID="round3"
  run_guard_detached "$dir/data" true
  local first_rc="$RUN_RC" first_name="$RUN_CONTAINER"
  run_guard_detached "$dir/data" true
  local second_rc="$RUN_RC" second_name="$RUN_CONTAINER"
  if [ "$first_rc" -eq 0 ] && [ "$second_rc" -eq 0 ] \
     && [ -n "$first_name" ] && [ -n "$second_name" ] && [ "$first_name" != "$second_name" ]; then
    ok "two rounds used distinct container names ($first_name vs $second_name)"
  else
    bad "round names not distinct or failed (rc=$first_rc/$second_rc)"
  fi
  if [ "$(grep -c 'podman rm -f' "$log" || true)" -eq 0 ]; then
    ok "no removal before cleanup (ownership deferred)"
  else
    bad "removals happened outside cleanup"
  fi
  cleanup
  local rm_count
  rm_count="$(grep -c 'podman rm -f' "$log" || true)"
  if [ "$rm_count" -eq 2 ]; then
    ok "cleanup removed exactly the two owned containers"
  else
    bad "cleanup removed ${rm_count} container(s), expected 2"
  fi
  rm -rf "$sc_state"

  echo
  if [ "$FAILURES" -eq 0 ]; then
    echo "GUARD TEST SELF-CHECK PASSED"
  else
    echo "FAILED CHECKS: $FAILURES" >&2
    exit 1
  fi
  exit 0
}

# ---------------------------------------------------------------------------
# Real mode.
# ---------------------------------------------------------------------------
real_run() {
  AWS_CLI_BIN="${AWS_CLI_BIN:-aws}"
  REGION="${AWS_REGION:-us-east-1}"
  ACCOUNT_ID="$("$AWS_CLI_BIN" sts get-caller-identity --query Account --output text)"
  IMAGE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/supportportal/weknora:base-paradedb-v0.22.6-pg17"

  DATA_DIR="$WORK_DIR/pgdata"
  mkdir -p "$DATA_DIR"
  WRAPPER="$(guard_wrapper)"

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

  echo "== A: guard=true on an empty volume must refuse to start =="
  run_guard_foreground "$DATA_DIR" true
  if [ "$RUN_RC" -ne 0 ] && grep -q "refusing to start an empty database" <<<"$RUN_OUT"; then
    ok "empty volume with guard refused to start"
  else
    bad "empty volume with guard did not refuse (rc=$RUN_RC): $(head -3 <<<"$RUN_OUT")"
  fi
  [ ! -f "$DATA_DIR/pgdata/PG_VERSION" ] && ok "refusal created no database" || bad "refusal still initialized a database"

  echo "== B: first initialization (guard off) succeeds =="
  run_guard_detached "$DATA_DIR" false
  if [ "$RUN_RC" -eq 0 ] && grep -q "database system is ready to accept connections" <<<"$RUN_OUT"; then
    ok "initialization with guard off succeeded"
  else
    bad "initialization failed (rc=$RUN_RC): $(tail -5 <<<"$RUN_OUT")"
  fi
  [ -f "$DATA_DIR/pgdata/PG_VERSION" ] && ok "PG_VERSION created" || bad "PG_VERSION missing after init"

  echo "== C: guard=true on the initialized volume starts (upgrade path) =="
  run_guard_detached "$DATA_DIR" true
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
}

if [ "$MODE" = "self-check" ]; then
  self_check
else
  real_run
fi
