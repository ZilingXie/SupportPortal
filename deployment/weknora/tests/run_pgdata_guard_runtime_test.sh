#!/usr/bin/env bash
# Runtime test for the ParadeDB data guard (REQUIRE_EXISTING_PGDATA) using the
# REAL paradedb image the deployment runs, against the same entrypoint command
# wrapper shape as register_weknora_task_definitions.sh:
#
#   A. guard=true + empty volume  -> MUST refuse to start (non-zero, message)
#   B. guard off + empty volume   -> initializes successfully (pg_isready ok)
#      -> B is EXPLICITLY STOPPED AND REMOVED (data dir kept) before C runs,
#         so C proves "restart with guard on the initialized data dir" and
#         never races a live database on the same PGDATA.
#   C. guard=true + initialized   -> starts again (upgrade path keeps data)
#
# Resource ownership (R4/R5 review findings): every run uses a UNIQUE
# container name, never pre-deletes anything, and cleanup removes exactly the
# containers THIS run successfully created — once (idempotent).
#
# Modes:
#   (default)            real guard scenarios against the actual image
#   --self-check         parent harness: drives the ownership behavior in a
#                        CHILD PROCESS against a mocked podman and asserts on
#                        the child's FULL-EXIT state (existing containers kept,
#                        owned containers cleaned, no extra removals, no real
#                        podman calls)
#   --self-check-child   internal: the child driven by --self-check
set -o pipefail

MODE="real"
[[ "${1:-}" == "--self-check" ]] && MODE="self-check"
[[ "${1:-}" == "--self-check-child" ]] && MODE="self-check-child"

FAILURES=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1" >&2; FAILURES=$((FAILURES + 1)); }

# Unique per-invocation identity shared by the work dir and container names.
RUN_ID="$$-$(date +%s)"
WORK_DIR="$HOME/.tmp-weknora-guard-test-${RUN_ID}"

# Containers this run successfully created (and therefore owns). cleanup is
# idempotent: it clears the list as it removes, so a second invocation (e.g.
# the EXIT trap after a manual call) is a no-op.
OWNED_CONTAINERS=()

cleanup() {
  local name
  for name in "${OWNED_CONTAINERS[@]}"; do
    [ -n "$name" ] && podman rm -f "$name" >/dev/null 2>&1 || true
  done
  OWNED_CONTAINERS=()
  [ -n "${GUARD_WORK_DIR:-}" ] && rm -rf "$GUARD_WORK_DIR"
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
# successful creation records ownership for cleanup.
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

# Stop-and-remove an OWNED container, dropping it from ownership so cleanup
# never removes it twice. Refuses names this run does not own.
stop_owned() { # container_name -> STOP_RC
  local name="$1"
  local owned=() found=0
  local n
  for n in "${OWNED_CONTAINERS[@]}"; do
    if [[ "$n" == "$name" ]]; then found=1; else owned+=("$n"); fi
  done
  if [[ "$found" != "1" ]]; then
    echo "refusing to stop $name: not owned by this run" >&2
    STOP_RC=1
    return
  fi
  podman rm -f "$name" >/dev/null 2>&1 || true
  OWNED_CONTAINERS=("${owned[@]}")
  STOP_RC=0
}

# ---------------------------------------------------------------------------
# Self-check harness (parent). Ownership behavior runs in a CHILD process;
# assertions read the child's full-exit state.
# ---------------------------------------------------------------------------
self_check_parent() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local this_script="$script_dir/$(basename "${BASH_SOURCE[0]}")"

  local sc_state="$HOME/.tmp-weknora-guard-sc-${RUN_ID}"
  rm -rf "$sc_state"
  mkdir -p "$sc_state/bin" "$sc_state/work" "$sc_state/realbin"

  # Mock podman: alive-state tracked under sc_state; dispatch on $1 so
  # "rm -f name" / "exec name cmd" / "logs name" match correctly.
  cat > "$sc_state/bin/podman" <<STUB
#!/usr/bin/env bash
echo "podman \$*" >> "\${SC_STATE}/podman.log"
case "\$1" in
  container)
    case "\$3" in
      *pre-existing-*) exit 0 ;;
    esac
    [ -f "\${SC_STATE}/alive-\$3" ] && exit 0
    exit 1 ;;
  run)
    name=""
    prev=""
    for a in "\$@"; do
      if [ "\$prev" = "--name" ]; then name="\$a"; break; fi
      prev="\$a"
    done
    if [ "\${SC_FAIL_RUN:-0}" = "1" ]; then exit 1; fi
    [ -n "\$name" ] && touch "\${SC_STATE}/alive-\$name"
    exit 0 ;;
  exec)
    # pg_isready succeeds immediately
    exit 0 ;;
  logs)
    echo "database system is ready to accept connections"
    exit 0 ;;
  rm)
    # "rm -f <name>": the name is the last argument.
    rm -f "\${SC_STATE}/alive-\${@: -1}"
    exit 0 ;;
esac
exit 0
STUB
  chmod +x "$sc_state/bin/podman"

  # Real-podman detector: only reachable if the mock (earlier on PATH) is
  # gone. Any entry here means the test escaped its isolation boundary.
  cat > "$sc_state/realbin/podman" <<STUB
#!/usr/bin/env bash
echo "REAL-PODMAN \$*" >> "\${SC_STATE}/real-podman.log"
exit 0
STUB
  chmod +x "$sc_state/realbin/podman"

  # Pre-existing container state for the collision round (name shape must
  # match wkguard-<RUN_ID>-<seq> with RUN_ID "pre-existing-x", seq 0).
  touch "$sc_state/alive-wkguard-pre-existing-x-0"

  echo "== child: ownership scenarios run to full exit =="
  SC_STATE="$sc_state" GUARD_WORK_DIR="$sc_state/work" \
    PATH="$sc_state/bin:$sc_state/realbin:$PATH" GUARD_POLL_SLEEP=0 \
    bash "$this_script" --self-check-child
  local child_rc=$?

  echo "== parent assertions on child full-exit state =="
  local log="$sc_state/podman.log"

  # 1. The pre-existing container survived untouched.
  if [ -f "$sc_state/alive-wkguard-pre-existing-x-0" ] \
     && ! grep -q "rm -f wkguard-pre-existing-x-0" "$log"; then
    ok "pre-existing container preserved (no removal, state intact)"
  else
    bad "pre-existing container was removed or its state vanished"
  fi

  # 2. Owned containers were actually cleaned: alive markers gone.
  if [ ! -e "$sc_state/alive-wkguard-round3-0" ] && [ ! -e "$sc_state/alive-wkguard-round3-1" ]; then
    ok "both owned containers removed (alive markers gone)"
  else
    bad "owned container alive markers survived the child exit cleanup"
  fi

  # 3. Exactly two removals, for the owned names only — this also proves the
  #    collision and failed-creation rounds removed nothing (any removal there
  #    would push the count past two or name a different container).
  local total_rm
  total_rm="$(grep -c "podman rm -f" "$log" || true)"
  if [ "$total_rm" -eq 2 ] \
     && grep -q "podman rm -f wkguard-round3-0" "$log" \
     && grep -q "podman rm -f wkguard-round3-1" "$log"; then
    ok "exactly two removals, each for one owned container (idempotent exit cleanup)"
  else
    bad "removal count/content wrong: $total_rm total"
  fi

  # 4. No real podman call ever happened.
  if [ ! -f "$sc_state/real-podman.log" ]; then
    ok "no real podman invocation escaped the mock"
  else
    bad "real podman was invoked: $(cat "$sc_state/real-podman.log")"
  fi

  # 5. The child itself reported success.
  if [ "$child_rc" -eq 0 ]; then
    ok "child scenario run exited 0"
  else
    bad "child scenario run exited $child_rc"
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
# Self-check child: exercises the REAL functions with the mock on PATH and
# exits; the EXIT trap performs the single, natural cleanup.
# ---------------------------------------------------------------------------
self_check_child() {
  IMAGE="guard-mock-image"
  WRAPPER="$(guard_wrapper)"
  local dir="${GUARD_WORK_DIR:?GUARD_WORK_DIR required}"
  mkdir -p "$dir"

  echo "== child round 1: name collision keeps the existing container =="
  DETACH_SEQ=0; RUN_ID="pre-existing-x"
  run_guard_detached "$dir/data" true
  if [ "$RUN_RC" -ne 0 ]; then
    ok "collision refused"
  else
    bad "collision round unexpectedly succeeded"
  fi

  echo "== child round 2: failed creation =="
  DETACH_SEQ=0; RUN_ID="round2"
  SC_FAIL_RUN=1 run_guard_detached "$dir/data" true
  if [ "$RUN_RC" -ne 0 ]; then
    ok "failed creation reported failure"
  else
    bad "failed creation round unexpectedly succeeded"
  fi
  unset SC_FAIL_RUN

  echo "== child round 3: two owned rounds (exit trap cleans both once) =="
  DETACH_SEQ=0; RUN_ID="round3"
  run_guard_detached "$dir/data" true
  local first_rc="$RUN_RC" first_name="$RUN_CONTAINER"
  run_guard_detached "$dir/data" true
  local second_rc="$RUN_RC" second_name="$RUN_CONTAINER"
  if [ "$first_rc" -eq 0 ] && [ "$second_rc" -eq 0 ] \
     && [ -n "$first_name" ] && [ -n "$second_name" ] && [ "$first_name" != "$second_name" ]; then
    ok "two rounds used distinct owned names"
  else
    bad "round names/failure wrong (rc=$first_rc/$second_rc)"
  fi
  echo "child owned: ${OWNED_CONTAINERS[*]}"
  # No manual cleanup: the EXIT trap runs once at process exit.
  [ "$FAILURES" -eq 0 ] || exit 1
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

  GUARD_WORK_DIR="$WORK_DIR"
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
  if [ ! -f "$DATA_DIR/pgdata/PG_VERSION" ]; then
    ok "refusal created no database"
  else
    bad "refusal still initialized a database"
  fi

  echo "== B: first initialization (guard off), then EXPLICIT stop =="
  run_guard_detached "$DATA_DIR" false
  if [ "$RUN_RC" -eq 0 ] && grep -q "database system is ready to accept connections" <<<"$RUN_OUT"; then
    ok "initialization with guard off succeeded"
  else
    bad "initialization failed (rc=$RUN_RC): $(tail -5 <<<"$RUN_OUT")"
  fi
  if [ -f "$DATA_DIR/pgdata/PG_VERSION" ]; then
    ok "PG_VERSION created"
  else
    bad "PG_VERSION missing after init"
  fi
  local b_name="$RUN_CONTAINER"
  stop_owned "$b_name"
  if [ "$STOP_RC" -eq 0 ] && ! podman container exists "$b_name" 2>/dev/null; then
    ok "B stopped and removed before C (no live database on the data dir)"
  else
    bad "B stop failed; refusing to start C against a live database"
    exit 1
  fi

  echo "== C: guard=true restarts on the stopped, initialized volume =="
  run_guard_detached "$DATA_DIR" true
  if [ "$RUN_RC" -eq 0 ] && grep -q "database system is ready to accept connections" <<<"$RUN_OUT"; then
    ok "initialized volume with guard started successfully after B stopped"
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

case "$MODE" in
  real) real_run ;;
  self-check) self_check_parent ;;
  self-check-child) self_check_child ;;
esac
