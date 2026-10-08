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
# Resource ownership (R4-R7 review findings): every run uses a UNIQUE
# container name, never pre-deletes anything, and cleanup removes exactly the
# containers THIS run successfully created — once (idempotent). Removal is
# only "confirmed" when the container query says ABSENT; a query failure
# (any exit code other than 0=present / 1=absent) is UNKNOWN, keeps the
# ownership record, keeps the work dir, and reports KEEPING.
#
# Modes:
#   (default)            real guard scenarios against the actual image
#   --self-check         parent harness: drives the ownership behavior in
#                        CHILD processes against a mocked podman (per-child
#                        call logs; a cumulative real-podman escape log) and
#                        asserts on each child's FULL-EXIT state. Includes a
#                        bug-simulation child that validates the harness's own
#                        detection (extra run / escaped real call).
#   --self-check-child             internal children (SC_LOG_TAG selects)
#   --self-check-child-fault-stop
#   --self-check-child-fault-exit
#   --self-check-child-bug
set -o pipefail

MODE="real"
[[ "${1:-}" == "--self-check" ]] && MODE="self-check"
[[ "${1:-}" == "--self-check-child" ]] && MODE="self-check-child"
[[ "${1:-}" == "--self-check-child-fault-stop" ]] && MODE="self-check-child-fault-stop"
[[ "${1:-}" == "--self-check-child-fault-exit" ]] && MODE="self-check-child-fault-exit"
[[ "${1:-}" == "--self-check-child-bug" ]] && MODE="self-check-child-bug"

FAILURES=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1" >&2; FAILURES=$((FAILURES + 1)); }

# Unique per-invocation identity shared by the work dir and container names.
RUN_ID="$$-$(date +%s)"
WORK_DIR="$HOME/.tmp-weknora-guard-test-${RUN_ID}"

# Containers this run successfully created (and therefore owns). Ownership is
# dropped ONLY after removal is CONFIRMED ABSENT; presence or an unknown
# query result keeps the record, and cleanup then refuses to delete the work
# dir (which contains the mounted PGDATA).
OWNED_CONTAINERS=()

# container_state <name> -> echoes present|absent|unknown.
# `podman container exists` contract: 0 = exists, 1 = does not exist,
# anything else (e.g. 125) = query failure — status UNKNOWN.
container_state() {
  local name="$1" rc
  podman container exists "$name" >/dev/null 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "present"
  elif [ "$rc" -eq 1 ]; then
    echo "absent"
  else
    echo "unknown"
  fi
}

cleanup() {
  local name state confirmed_all=1
  for name in "${OWNED_CONTAINERS[@]}"; do
    [ -n "$name" ] || continue
    podman rm -f "$name" >/dev/null 2>&1 || true
    state="$(container_state "$name")"
    case "$state" in
      absent)
        # Confirmed gone: drop from ownership below via the rebuild pass.
        ;;
      present)
        echo "[guard-test-cleanup] owned container $name still exists after removal; KEEPING container and work dir for manual cleanup" >&2
        confirmed_all=0
        ;;
      *)
        echo "[guard-test-cleanup] cannot confirm removal of $name (container query failed, state unknown); KEEPING container and work dir for manual cleanup" >&2
        confirmed_all=0
        ;;
    esac
  done
  # Retain ownership ONLY for containers confirmed present or unknown;
  # confirmed-absent ones drop out, so a repeated cleanup call issues no
  # duplicate rm and never deletes a dir that was kept for an unknown one.
  local remaining=() n st
  for n in "${OWNED_CONTAINERS[@]}"; do
    [ -n "$n" ] || continue
    st="$(container_state "$n")"
    if [ "$st" != "absent" ]; then remaining+=("$n"); fi
  done
  OWNED_CONTAINERS=("${remaining[@]}")
  if [ "$confirmed_all" != "1" ]; then
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
  local st="$(container_state "$name")"
  if [ "$st" != "absent" ]; then
    RUN_RC=1
    RUN_OUT="container name $name is ${st}; refusing to touch it"
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
    st="$(container_state "$name")"
    if [ "$st" != "present" ]; then
      RUN_RC=1
      RUN_OUT="$(podman logs "$name" 2>&1 || true)"
      return
    fi
    sleep "${GUARD_POLL_SLEEP:-2}"
  done
  RUN_RC=124
  RUN_OUT="$(podman logs "$name" 2>&1 || true)"
}

# Stop-and-remove an OWNED container. Removal counts only when the container
# is CONFIRMED ABSENT afterwards; present or unknown keeps the ownership
# record and returns failure. Refuses names this run does not own.
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
  local st="$(container_state "$name")"
  if [ "$st" = "absent" ]; then
    OWNED_CONTAINERS=("${owned[@]}")
    STOP_RC=0
    return
  fi
  echo "stop_owned: $name is ${st} after removal; keeping ownership record" >&2
  STOP_RC=1
}

# ---------------------------------------------------------------------------
# Self-check harness (parent). Each child gets its OWN call log via SC_LOG_TAG;
# the real-podman escape log is cumulative across all children and is never
# truncated. A bug-simulation child validates the harness's own detection.
# ---------------------------------------------------------------------------
# run_child <tag> <workdir> <mode> — sets CHILD_RC; per-child log via SC_LOG_TAG.
run_child() {
  local tag="$1" work="$2" mode="$3"
  rm -rf "$work"; mkdir -p "$work"
  SC_STATE="$SC_STATE" SC_LOG_TAG="$tag" GUARD_WORK_DIR="$work" \
    PATH="$SC_BIN:$SC_REALBIN:$PATH" GUARD_POLL_SLEEP=0 \
    bash "$SC_SCRIPT" "$mode" > "$SC_STATE/child-${tag}.out" 2> "$SC_STATE/child-${tag}.err"
  CHILD_RC=$?
}

self_check_parent() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local this_script="$script_dir/$(basename "${BASH_SOURCE[0]}")"

  local sc_state="$HOME/.tmp-weknora-guard-sc-${RUN_ID}"
  rm -rf "$sc_state"
  mkdir -p "$sc_state/bin" "$sc_state/realbin"

  # Mock podman: per-child call log (SC_LOG_TAG), alive-state under sc_state,
  # dispatch on $1. Injections: SC_FAIL_RUN, SC_FAIL_RM, SC_QUERY_RC.
  cat > "$sc_state/bin/podman" <<STUB
#!/usr/bin/env bash
echo "podman \$*" >> "\${SC_STATE}/podman-\${SC_LOG_TAG:-x}.log"
case "\$1" in
  container)
    if [ -n "\${SC_QUERY_RC:-}" ]; then exit "\$SC_QUERY_RC"; fi
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
    exit 0 ;;
  logs)
    echo "database system is ready to accept connections"
    exit 0 ;;
  rm)
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

  # Real-podman detector: only reachable when invoked deliberately or when
  # the mock is missing. CUMULATIVE log — never truncated by the parent.
  cat > "$sc_state/realbin/podman" <<STUB
#!/usr/bin/env bash
echo "REAL-PODMAN \$*" >> "\${SC_STATE}/real-podman.log"
exit 0
STUB
  chmod +x "$sc_state/realbin/podman"

  # Pre-existing container state for the collision round.
  touch "$sc_state/alive-wkguard-pre-existing-x-0"

  SC_STATE="$sc_state" SC_BIN="$sc_state/bin" SC_REALBIN="$sc_state/realbin" SC_SCRIPT="$this_script"

  echo "== child A: ownership scenarios run to full exit =="
  run_child a "$sc_state/work" --self-check-child
  local CHILD_RC_A="$CHILD_RC"

  echo "== child B: stop unconfirmed (rm ok, query unknown) — keep ownership + data dir =="
  run_child b "$sc_state/workB" --self-check-child-fault-stop
  local CHILD_RC_B="$CHILD_RC"

  echo "== child C: EXIT-cleanup removal fails (must keep data dir and report) =="
  run_child c "$sc_state/workC" --self-check-child-fault-exit
  local CHILD_RC_C="$CHILD_RC"

  echo "== child BUG: extra run + deliberate real-podman call (harness detection) =="
  run_child bug "$sc_state/workBUG" --self-check-child-bug
  local CHILD_RC_BUG="$CHILD_RC"

  echo "== parent assertions on child full-exit states =="

  # --- Child A (normal path) ---
  local logA="$sc_state/podman-a.log"

  # 1. The pre-existing container survived untouched.
  if [ -f "$sc_state/alive-wkguard-pre-existing-x-0" ] \
     && ! grep -q "rm -f wkguard-pre-existing-x-0" "$logA"; then
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

  # 3. Exactly two removals in A's OWN log, for the owned names only.
  local total_rmA
  total_rmA="$(grep -c "podman rm -f" "$logA" || true)"
  if [ "$total_rmA" -eq 2 ] \
     && grep -q "podman rm -f wkguard-round3-0" "$logA" \
     && grep -q "podman rm -f wkguard-round3-1" "$logA"; then
    ok "A: exactly two removals, each for one owned container (idempotent exit cleanup)"
  else
    bad "A: removal count/content wrong: $total_rmA total"
  fi

  # 4. Child A's work dir was deleted on success cleanup.
  if [ ! -e "$sc_state/work" ]; then
    ok "A: work dir removed after confirmed cleanup"
  else
    bad "A: work dir survived a successful cleanup"
  fi

  # 5. Child A reported success.
  if [ "$CHILD_RC_A" -eq 0 ]; then
    ok "A: child scenario run exited 0"
  else
    bad "A: child scenario run exited $CHILD_RC_A"
  fi

  # --- Child B (rm ok, confirmation query unknown) ---
  local logB="$sc_state/podman-b.log"

  # 6. stop failure propagated (child exited non-zero) and C was never started
  #    in B's OWN log.
  local runB
  runB="$(grep -c "podman run -d" "$logB" || true)"
  if [ "$CHILD_RC_B" -ne 0 ] && [ "$runB" -eq 1 ]; then
    ok "B-fault: unconfirmed stop propagated and C never started (single run in B's own log)"
  else
    bad "B-fault: child rc=$CHILD_RC_B, B-log run count=$runB (expected rc!=0, runs=1)"
  fi

  # 7. The data dir is preserved across repeated cleanups while the state is
  #    unknown (rm succeeded but existence could not be confirmed).
  if [ -d "$sc_state/workB/data" ]; then
    ok "B-fault: data dir preserved under unknown confirmation (incl. repeated cleanup)"
  else
    bad "B-fault: data dir deleted despite unknown confirmation"
  fi

  # 8. Explicit keep report present.
  if grep -q "KEEPING" "$sc_state/child-b.err"; then
    ok "B-fault: explicit keep-resources report emitted"
  else
    bad "B-fault: no KEEPING report in child stderr"
  fi

  # --- Child C (EXIT-cleanup removal failure) ---
  # 9. Child C itself succeeded; only cleanup failed.
  if [ "$CHILD_RC_C" -eq 0 ]; then
    ok "C-fault: scenarios passed (exit 0) despite later cleanup failure"
  else
    bad "C-fault: child exited $CHILD_RC_C"
  fi
  # 10. Data dir + marker preserved after EXIT cleanup failure.
  if [ -d "$sc_state/workC/data" ] && [ -f "$sc_state/alive-wkguard-faultc-0" ]; then
    ok "C-fault: in-use data dir and container marker preserved"
  else
    bad "C-fault: data dir or marker deleted despite failed exit cleanup"
  fi
  # 11. Explicit keep report present.
  if grep -q "KEEPING" "$sc_state/child-c.err"; then
    ok "C-fault: explicit keep-resources report emitted"
  else
    bad "C-fault: no KEEPING report in child stderr"
  fi

  # --- Child BUG (harness self-validation) ---
  local logBUG="$sc_state/podman-bug.log"
  # 12. The extra-run detector flags the bug child (>=2 runs in ITS OWN log).
  local runBUG
  runBUG="$(grep -c "podman run -d" "$logBUG" || true)"
  if [ "$runBUG" -ge 2 ]; then
    ok "harness: extra-run detection triggers on a simulated extra start (bug child had $runBUG runs)"
  else
    bad "harness: bug child only shows $runBUG runs; detection would miss extra starts"
  fi
  # 13-14. The cumulative escape log caught exactly the one deliberate
  #        real-podman call, proving A/B/C never escaped.
  local escapes
  escapes="$(grep -c "REAL-PODMAN" "$sc_state/real-podman.log" 2>/dev/null || true)"
  if [ "$escapes" -eq 1 ] && grep -q "REAL-PODMAN ps" "$sc_state/real-podman.log"; then
    ok "harness: cumulative escape log detects exactly the one deliberate real-podman call; children A/B/C never escaped"
  else
    bad "harness: escape log count=$escapes (expected exactly 1 deliberate call)"
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
# Self-check children.
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
  [ "$FAILURES" -eq 0 ] || exit 1
  exit 0
}

# Child B: B starts normally; stop_owned's rm SUCCEEDS (mock removes the
# alive marker) but the follow-up query returns 125 (unknown) — the review's
# case 2. stop_owned must fail and keep ownership; repeated cleanup calls
# must keep the dir (review's case 3).
self_check_child_fault_stop() {
  IMAGE="guard-mock-image"
  WRAPPER="$(guard_wrapper)"
  local dir="${GUARD_WORK_DIR:?GUARD_WORK_DIR required}"
  mkdir -p "$dir/data"

  DETACH_SEQ=0; RUN_ID="faultb"
  run_guard_detached "$dir/data" true
  if [ "$RUN_RC" -ne 0 ]; then
    bad "faultB: B failed to start (unexpected)"
    exit 1
  fi
  local b_name="$RUN_CONTAINER"

  # rm succeeds, but the confirmation query is unknown (125).
  export SC_QUERY_RC=125
  stop_owned "$b_name"
  if [ "$STOP_RC" -eq 0 ]; then
    bad "faultB: stop_owned reported success despite unknown confirmation"
    exit 1
  fi
  local still_owned=0 n
  for n in "${OWNED_CONTAINERS[@]}"; do
    [ "$n" = "$b_name" ] && still_owned=1
  done
  if [ "$still_owned" -ne 1 ]; then
    bad "faultB: ownership dropped after unknown confirmation"
    exit 1
  fi
  ok "faultB: unknown confirmation kept ownership and failed the stop"

  # Repeat-cleanup contract (case 3): two explicit cleanups must both keep
  # the dir; SC_QUERY_RC stays armed so the state remains unknown.
  cleanup
  [ -d "$dir/data" ] || { bad "faultB: first cleanup deleted the data dir (unknown state)"; exit 1; }
  cleanup
  [ -d "$dir/data" ] || { bad "faultB: second cleanup deleted the data dir (unknown state)"; exit 1; }
  ok "faultB: repeated cleanup kept the data dir"

  echo "faultB: B stop unconfirmed; refusing to start C against a possibly live database" >&2
  exit 1
}

# Child C: one healthy container; scenarios pass; the EXIT cleanup's rm fails.
self_check_child_fault_exit() {
  IMAGE="guard-mock-image"
  WRAPPER="$(guard_wrapper)"
  local dir="${GUARD_WORK_DIR:?GUARD_WORK_DIR required}"
  mkdir -p "$dir/data"

  DETACH_SEQ=0; RUN_ID="faultc"
  run_guard_detached "$dir/data" true
  if [ "$RUN_RC" -ne 0 ]; then
    bad "faultC: container failed to start (unexpected)"
    exit 1
  fi
  ok "faultC: scenario passed"
  export SC_FAIL_RM=1
  exit 0
}

# Child BUG: simulates the two defects the harness must catch — starting an
# extra container after a failed stop, and a real-podman escape. The PARENT's
# assertions 12-14 prove the detectors fire.
self_check_child_bug() {
  IMAGE="guard-mock-image"
  WRAPPER="$(guard_wrapper)"
  local dir="${GUARD_WORK_DIR:?GUARD_WORK_DIR required}"
  mkdir -p "$dir/data"

  DETACH_SEQ=0; RUN_ID="bug"
  run_guard_detached "$dir/data" true
  if [ "$RUN_RC" -ne 0 ]; then
    bad "bug child: first run failed (unexpected)"
    exit 1
  fi

  export SC_QUERY_RC=125
  stop_owned "$RUN_CONTAINER" >/dev/null 2>&1 || true   # fails; ownership kept
  unset SC_QUERY_RC

  # DEFECT 1: start another container despite the unconfirmed stop.
  DETACH_SEQ=1
  run_guard_detached "$dir/data" true >/dev/null 2>&1 || true

  # DEFECT 2: deliberately bypass the mock (direct realbin path).
  "${SC_STATE}/realbin/podman" ps >/dev/null 2>&1 || true

  exit 1
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
  local b_state="$(container_state "$b_name")"
  if [ "$STOP_RC" -eq 0 ] && [ "$b_state" = "absent" ]; then
    ok "B stopped and confirmed absent before C (no live database on the data dir)"
  else
    bad "B stop unconfirmed (state=$b_state); refusing to start C against the data dir"
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
  self-check-child-bug) self_check_child_bug ;;
esac
