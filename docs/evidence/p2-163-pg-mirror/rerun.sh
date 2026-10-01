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
cleanup() {
  # Safety net for early exits (set -e): never delete the data directory
  # while the instance might still be running.
  if [[ "$STOPPED" != "1" ]]; then
    "$PG_CTL" -D "$CLUSTER_DIR" stop > /dev/null 2>&1 || true
    STOPPED=1
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
"$PG_CTL" -D "$CLUSTER_DIR" stop > /dev/null 2>&1
STOPPED=1
echo "pg_ctl stop: ok"
rm -rf "$CLUSTER_DIR"
[[ ! -d "$CLUSTER_DIR" ]] || { echo "cleanup failed: $CLUSTER_DIR still exists" >&2; exit 1; }
echo "cleanup verified: $CLUSTER_DIR removed and gone"
echo "code baseline: $(git -C "$REPO" rev-parse HEAD)"
