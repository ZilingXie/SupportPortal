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
[[ "${1:-}" == "--self-check-child-fault-stop" ]] && MODE="self-check-child-fault-stop"
[[ "${1:-}" == "--self-check-child-fault-exit" ]] && MODE="self-check-child-fault-exit"

FAILURES=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1" >&2; FAILURES=$((FAILURES + 1)); }

# Unique per-invocation identity shared by the work dir and container names.
RUN_ID="$$-$(date +%s)"
WORK_DIR="$HOME/.tmp-weknora-guard-test-${RUN_ID}"

# Containers this run successfully created (and therefore owns). Ownership is
# dropped ONLY after removal is CONFIRMED; a container whose removal fails
# stays in the list, and cleanup then refuses to delete the work dir (which
# contains its mounted PGDATA) so nothing deletes a data dir still in use.
OWNED_CONTAINERS=()
CLEANUP_KEEP_WORK_DIR=0

cleanup() {
  local name confirmed_gone=1
  for name in "${OWNED_CONTAINERS[@]}"; do
    [ -n "$name" ] || continue
    if ! podman rm -f "$name" >/dev/null 2>&1 || podman container exists "$name" 2>/dev/null; then
      echo "[guard-test-cleanup] could not confirm removal of owned container $name; KEEPING container and work dir for manual cleanup" >&2
      confirmed_gone=0
      continue
    fi
  done
  # Retain ownership only for containers that provably still exist; confirmed
  # removals drop out, so a repeated cleanup call issues no duplicate rm.
  local remaining=() n
  for n in "${OWNED_CONTAINERS[@]}"; do
    [ -n "$n" ] || continue
    if podman container exists "$n" 2>/dev/null; then remaining+=("$n"); fi
  done
  OWNED_CONTAINERS=("${remaining[@]}")
  if [ "$confirmed_gone" != "1" ]; then
    CLEANUP_KEEP_WORK_DIR=1
    return
  fi
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
# never removes it twice. Refuses names this run does not own. A FAILED
# removal returns failure and KEEPS the ownership record, so the EXIT cleanup
# still knows the container exists and refuses to delete the work dir.
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
  if ! podman rm -f "$name" >/dev/null 2>&1; then
    echo "stop_owned: removal of owned container $name FAILED; keeping ownership record" >&2
    STOP_RC=1
    return
  fi
  if podman container exists "$name" 2>/dev/null; then
    echo "stop_owned: $name still exists after removal; keeping ownership record" >&2
    STOP_RC=1
    return
  fi
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
    if [ "\${SC_FAIL_RM:-0}" = "1" ]; then
      echo "mock podman rm failed (SC_FAIL_RM=1)" >&2
      exit 1
    fi
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

  echo "== child A: ownership scenarios run to full exit =="
  SC_STATE="$sc_state" GUARD_WORK_DIR="$sc_state/work" \
    PATH="$sc_state/bin:$sc_state/realbin:$PATH" GUARD_POLL_SLEEP=0 \
    bash "$this_script" --self-check-child
  local child_rc=$?
  cp "$sc_state/podman.log" "$sc_state/podman-a.log"
  local SC_A_LOG="$sc_state/podman-a.log"

  echo "== child B: B-removal fails mid-run (must not start C, must keep data dir) =="
  rm -rf "$sc_state/workB"; mkdir -p "$sc_state/workB"
  : > "$sc_state/podman.log"
  rm -f "$sc_state/real-podman.log"
  SC_STATE="$sc_state" GUARD_WORK_DIR="$sc_state/workB" SC_FAULT_STOP=1 \
    PATH="$sc_state/bin:$sc_state/realbin:$PATH" GUARD_POLL_SLEEP=0 \
    bash "$this_script" --self-check-child-fault-stop > "$sc_state/childB.out" 2> "$sc_state/childB.err"
  local childB_rc=$?

  echo "== child C: EXIT-cleanup removal fails (must keep data dir and report) =="
  rm -rf "$sc_state/workC"; mkdir -p "$sc_state/workC"
  : > "$sc_state/podman.log"
  rm -f "$sc_state/real-podman.log"
  SC_STATE="$sc_state" GUARD_WORK_DIR="$sc_state/workC" SC_FAULT_EXIT_RM=1 \
    PATH="$sc_state/bin:$sc_state/realbin:$PATH" GUARD_POLL_SLEEP=0 \
    bash "$this_script" --self-check-child-fault-exit > "$sc_state/childC.out" 2> "$sc_state/childC.err"
  local childC_rc=$?

  echo "== parent assertions on child full-exit states =="

  # --- Child A (normal path) ---
  local log="$SC_A_LOG"

  # 1. The pre-existing container survived untouched.
  if [ -f "$sc_state/alive-wkguard-pre-existing-x-0" ] \
     && ! grep -q "rm -f wkguard-pre-existing-x-0" "$log"; then
    ok "A: pre-existing container preserved (no removal, state intact)"
  else
    bad "A: pre-existing container was removed or its state vanished"
  fi

  # 2. Owned containers were actually cleaned: alive markers gone.
  if [ ! -e "$sc_state/alive-wkguard-round3-0" ] && [ ! -e "$sc_state/alive-wkguard-round3-1" ]; then
    ok "A: both owned containers removed (alive markers gone)"
  else
    bad "A: owned container alive markers survived the child exit cleanup"
  fi

  # 3. Exactly two removals, for the owned names only — this also proves the
  #    collision and failed-creation rounds removed nothing (any removal there
  #    would push the count past two or name a different container).
  local total_rm
  total_rm="$(grep -c "podman rm -f" "$log" || true)"
  if [ "$total_rm" -eq 2 ] \
     && grep -q "podman rm -f wkguard-round3-0" "$log" \
     && grep -q "podman rm -f wkguard-round3-1" "$log"; then
    ok "A: exactly two removals, each for one owned container (idempotent exit cleanup)"
  else
    bad "A: removal count/content wrong: $total_rm total"
  fi

  # 4. Child A's work dir was deleted on success cleanup.
  if [ ! -e "$sc_state/work" ]; then
    ok "A: work dir removed after confirmed cleanup"
  else
    bad "A: work dir survived a successful cleanup"
  fi

  # 5. Child A reported success.
  if [ "$child_rc" -eq 0 ]; then
    ok "A: child scenario run exited 0"
  else
    bad "A: child scenario run exited $child_rc"
  fi

  # --- Child B (stop_owned failure mid-run) ---
  local logB="$sc_state/podman.log"
  local bname="wkguard-faultb-0"
  # 6. stop failure propagated (child exited non-zero) and C was never started.
  local runB
  runB="$(grep -c "podman run -d" "$logB" || true)"
  if [ "$childB_rc" -ne 0 ] && [ "$runB" -eq 1 ]; then
    ok "B-fault: stop failure propagated and C never started (single run)"
  else
    bad "B-fault: child rc=$childB_rc, run count=$runB (expected rc!=0, runs=1)"
  fi
  # 7. The data dir still exists — cleanup refused to delete it while the
  #    container provably still holds it.
  if [ -d "$sc_state/workB/data" ] && [ -f "$sc_state/alive-$bname" ]; then
    ok "B-fault: in-use data dir and container marker preserved"
  else
    bad "B-fault: data dir or container marker was deleted despite failed stop"
  fi
  # 8. Explicit keep report present.
  if grep -q "KEEPING" "$sc_state/childB.err"; then
    ok "B-fault: explicit keep-resources report emitted"
  else
    bad "B-fault: no KEEPING report in child stderr"
  fi

  # --- Child C (EXIT-cleanup removal failure) ---
  # 9. Child C itself succeeded (scenarios fine); only cleanup failed.
  if [ "$childC_rc" -eq 0 ]; then
    ok "C-fault: scenarios passed (exit 0) despite later cleanup failure"
  else
    bad "C-fault: child exited $childC_rc"
  fi
  # 10. Data dir + marker preserved after EXIT cleanup failure.
  if [ -d "$sc_state/workC/data" ] && [ -f "$sc_state/alive-wkguard-faultc-0" ]; then
    ok "C-fault: in-use data dir and container marker preserved"
  else
    bad "C-fault: data dir or marker deleted despite failed exit cleanup"
  fi
  # 11. Explicit keep report present.
  if grep -q "KEEPING" "$sc_state/childC.err"; then
    ok "C-fault: explicit keep-resources report emitted"
  else
    bad "C-fault: no KEEPING report in child stderr"
  fi

  # 12. No real podman call in any child.
  if [ ! -f "$sc_state/real-podman.log" ]; then
    ok "no real podman invocation escaped the mock (all children)"
  else
    bad "real podman was invoked: $(cat "$sc_state/real-podman.log")"
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
# Self-check fault children: exercise the FAILURE branches of stop_owned and
# cleanup. Both must keep the in-use data dir and report explicitly.
# ---------------------------------------------------------------------------
self_check_child_fault_stop() {
  IMAGE="guard-mock-image"
  WRAPPER="$(guard_wrapper)"
  local dir="${GUARD_WORK_DIR:?GUARD_WORK_DIR required}"
  mkdir -p "$dir/data"   # the mock does not perform -v mounts; stand in for PGDATA

  # Start "B" successfully.
  DETACH_SEQ=0; RUN_ID="faultb"
  run_guard_detached "$dir/data" true
  if [ "$RUN_RC" -ne 0 ]; then
    bad "faultB: B failed to start (unexpected)"
    exit 1
  fi
  local b_name="$RUN_CONTAINER"

  # stop_owned must FAIL (mock rm fails) and KEEP the ownership record.
  # SC_FAIL_RM stays armed through process exit so the EXIT cleanup's removal
  # also fails — exactly the review's repro (stop fails, then cleanup cannot
  # confirm removal and must not delete the in-use data dir).
  export SC_FAIL_RM=1
  stop_owned "$b_name"
  if [ "$STOP_RC" -eq 0 ]; then
    bad "faultB: stop_owned reported success despite removal failure"
    exit 1
  fi
  local still_owned=0 n
  for n in "${OWNED_CONTAINERS[@]}"; do
    [ "$n" = "$b_name" ] && still_owned=1
  done
  if [ "$still_owned" -ne 1 ]; then
    bad "faultB: ownership dropped after failed stop"
    exit 1
  fi

  # Mirror the real-mode contract: a failed B stop must abort before any C.
  echo "faultB: B stop failed; refusing to start C against a live database" >&2
  exit 1
}

self_check_child_fault_exit() {
  IMAGE="guard-mock-image"
  WRAPPER="$(guard_wrapper)"
  local dir="${GUARD_WORK_DIR:?GUARD_WORK_DIR required}"
  mkdir -p "$dir/data"   # the mock does not perform -v mounts; stand in for PGDATA

  # One healthy container; scenarios all pass.
  DETACH_SEQ=0; RUN_ID="faultc"
  run_guard_detached "$dir/data" true
  if [ "$RUN_RC" -ne 0 ]; then
    bad "faultC: container failed to start (unexpected)"
    exit 1
  fi
  ok "faultC: scenario passed"

  # Arm the mock so the EXIT cleanup's rm fails; the trap must keep the work
  # dir and report, not delete a data dir it cannot prove is unreferenced.
  export SC_FAIL_RM=1
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
  self-check-child-fault-stop) self_check_child_fault_stop ;;
  self-check-child-fault-exit) self_check_child_fault_exit ;;
esac
