#!/usr/bin/env bash
# From-zero isolated PostgreSQL verification for the PP-EN-QUICK mirror fix
# (p2-163): real publish_account_reply + close transaction must record
# zendesk_ticket_status='solved' on the case mirror.
# Run: bash docs/evidence/p2-163-pg-mirror/rerun.sh
# The full captured output of the reference run lives in pg-test-output.txt.
set -Eeuo pipefail

export PATH="/opt/homebrew/bin:$PATH"
# The repository checkout that carries the fix and its .venv (the test uses
# $REPO/.venv/bin/python). Override with PP_REPO when running from a worktree.
REPO="${PP_REPO:-/Users/xieziling/Desktop/personal_proj/SupportPortal}"
CLUSTER_DIR=/tmp/pp-pg-r9
PORT=54400
DB_NAME=pp_mirror_test2
SERVER_LOG=/tmp/pp-r9-server.log

# ONE pg_ctl for the whole script: start and stop must resolve the same
# binary. $REPO/.venv has no pg_ctl, and a bare name depends on PATH —
# resolve once and fail hard when absent.
PG_CTL="$(command -v pg_ctl)" || { echo "pg_ctl not found on PATH" >&2; exit 1; }
echo "pg_ctl resolved: $PG_CTL"

STOPPED=0
stop_and_clean() {
  # Stop the instance and remove the data directory ONLY after confirming
  # no postgres process remains. Fail closed: if the stop fails and the
  # postmaster is still alive, the data directory is KEPT (destroying the
  # data directory of a running cluster is never acceptable) and the
  # caller/operator must recover it manually.
  "$PG_CTL" -D "$CLUSTER_DIR" stop > /dev/null 2>&1
  STOPPED=1
  if pgrep -f "postgres -D $CLUSTER_DIR" > /dev/null 2>&1; then
    echo "cleanup FAILED: postgres still running for $CLUSTER_DIR; data directory KEPT" >&2
    return 1
  fi
  rm -rf "$CLUSTER_DIR"
  if [[ -d "$CLUSTER_DIR" ]]; then
    echo "cleanup FAILED: $CLUSTER_DIR still exists" >&2
    return 1
  fi
  echo "cleanup verified: $CLUSTER_DIR removed and no postgres process remains"
}
cleanup() {
  # Safety net for early exits (set -e): the failure path must also leave
  # nothing behind — attempt the same fail-closed stop-and-clean.
  if [[ "$STOPPED" != "1" ]]; then
    set +e
    stop_and_clean
    set -Eeuo pipefail
  fi
}
trap cleanup EXIT

rm -rf "$CLUSTER_DIR"
echo "=== 1. initdb ==="
initdb -D "$CLUSTER_DIR" -U testuser --auth=trust > /dev/null 2>&1
echo "initdb: ok"
echo "=== 2. pg_ctl start (port $PORT) ==="
# NOTE: start output must be redirected (or logged via -l); piping it makes
# the detached postgres hold the pipe open and `tail` hang forever.
"$PG_CTL" -D "$CLUSTER_DIR" -o "-p $PORT" -l "$SERVER_LOG" start > /dev/null 2>&1
sleep 2
echo "pg_ctl start: ok"
echo "=== 3. createdb $DB_NAME ==="
createdb -h 127.0.0.1 -p "$PORT" -U testuser "$DB_NAME"
echo "createdb: ok"
echo "=== 4. run test ==="
cd "$REPO"
RUN_POSTGRES_INTEGRATION=1 \
TICKET_DB_DSN="postgresql://testuser@127.0.0.1:${PORT}/${DB_NAME}" \
.venv/bin/python -m pytest backend/tests/test_account_reply_publication_postgres.py \
  -k "solved_close_records_mirror" -q
echo "=== 5. stop, then clean and verify ==="
stop_and_clean
echo "code baseline: $(git -C "$REPO" rev-parse HEAD)"
