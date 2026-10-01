#!/usr/bin/env bash
# From-zero isolated PostgreSQL verification for the PP-EN-QUICK mirror fix
# (p2-163): real publish_account_reply + close transaction must record
# zendesk_ticket_status='solved' on the case mirror.
# See pg-test-output.txt for a captured run.
set -Eeuo pipefail

export PATH="/opt/homebrew/bin:$PATH"
# The repository checkout that carries the fix and its .venv (the fixture
# uses $REPO/.venv/bin/python). Override with PP_REPO when running from a
# worktree.
REPO="${PP_REPO:-/Users/xieziling/Desktop/personal_proj/SupportPortal}"
CLUSTER_DIR=/tmp/pp-pg-r9
PORT=54400
DB_NAME=pp_mirror_test2
SERVER_LOG=/tmp/pp-r9-server.log

cleanup() {
  "$REPO/.venv/bin/pg_ctl" -D "$CLUSTER_DIR" stop > /dev/null 2>&1 || true
  rm -rf "$CLUSTER_DIR"
  echo "cleanup: $CLUSTER_DIR removed"
}
trap cleanup EXIT

rm -rf "$CLUSTER_DIR"
echo "=== 1. initdb ==="
initdb -D "$CLUSTER_DIR" -U testuser --auth=trust > /dev/null 2>&1
echo "initdb: ok"
echo "=== 2. pg_ctl start (port $PORT) ==="
# NOTE: start output must be redirected (or logged via -l); piping it makes
# the detached postgres hold the pipe open and `tail` hang forever.
pg_ctl -D "$CLUSTER_DIR" -o "-p $PORT" -l "$SERVER_LOG" start > /dev/null 2>&1
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
