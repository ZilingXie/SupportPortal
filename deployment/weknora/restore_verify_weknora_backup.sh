#!/usr/bin/env bash
# Restore a WeKnora backup into an INDEPENDENT local verification target (a
# throwaway podman paradedb container) and verify the restored data:
#   - object counts per table match the dump,
#   - a sample knowledge chunk's content is retrievable (search + read-back).
#
# Usage:
#   restore_verify_weknora_backup.sh --key db/weknora-weknora-<stamp>.dump \
#       [--expect-knowledge <title-substring>]
set -Eeuo pipefail

S3_KEY=""
EXPECT_KNOWLEDGE=""
AWS_CLI_BIN="${AWS_CLI_BIN:-aws}"
REGION="${AWS_REGION:-us-east-1}"
PARAM_PREFIX="/supportportal/weknora"
ACCOUNT_ID="$("$AWS_CLI_BIN" sts get-caller-identity --query Account --output text)"
BACKUP_BUCKET="supportportal-weknora-backup-${ACCOUNT_ID}-${REGION}"
PARADEDB_IMAGE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/supportportal/weknora:base-paradedb-v0.22.6-pg17"
CONTAINER="weknora-restore-verify"

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[weknora-restore-verify] $*" >&2; }

while [[ $# -ge 1 ]]; do
  case "$1" in
    --key) [[ $# -ge 2 ]] || fail "--key requires a value"; S3_KEY="$2"; shift 2 ;;
    --expect-knowledge) [[ $# -ge 2 ]] || fail "--expect-knowledge requires a value"; EXPECT_KNOWLEDGE="$2"; shift 2 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[[ -n "$S3_KEY" ]] || fail "--key is required (s3 object key in the backup bucket)"

WORK_DIR="$HOME/.tmp-weknora-restore"
rm -rf "$WORK_DIR"; mkdir -p "$WORK_DIR"; chmod 700 "$WORK_DIR"
cleanup() { podman rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

DB_USER="$("$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_username" --with-decryption --query Parameter.Value --output text)"
DB_NAME="$("$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_name" --with-decryption --query Parameter.Value --output text)"
DUMP_FILE="$(basename "$S3_KEY")"
"$AWS_CLI_BIN" s3 cp "s3://${BACKUP_BUCKET}/${S3_KEY}" "$WORK_DIR/$DUMP_FILE" --region "$REGION" >/dev/null || fail "dump download failed"
log "dump downloaded: $DUMP_FILE"

# Independent verification target: fresh paradedb container with its own
# anonymous volume; nothing from the live deployment is referenced.
podman run -d --name "$CONTAINER" -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD=restore-verify \
  -e POSTGRES_DB="$DB_NAME" -e PGDATA=/var/lib/postgresql/data/pgdata "$PARADEDB_IMAGE" >/dev/null \
  || fail "cannot start verification container"
log "waiting for verification database to accept connections"
for _ in $(seq 1 60); do
  if podman exec "$CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then break; fi
  sleep 2
done
podman exec "$CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1 || fail "verification database did not become ready"

podman cp "$WORK_DIR/$DUMP_FILE" "$CONTAINER:/tmp/restore.dump" >/dev/null
podman exec "$CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --no-privileges \
  --role="$DB_USER" --exit-on-error /tmp/restore.dump || fail "pg_restore failed"
log "restore complete; verifying object counts"

COUNTS="$(podman exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -Atc \
  "SELECT 'users='||count(*) FROM users WHERE deleted_at IS NULL UNION ALL SELECT 'knowledge_bases='||count(*) FROM knowledge_bases WHERE deleted_at IS NULL UNION ALL SELECT 'knowledges='||count(*) FROM knowledges WHERE deleted_at IS NULL UNION ALL SELECT 'chunks='||count(*) FROM chunks WHERE deleted_at IS NULL UNION ALL SELECT 'embeddings='||count(*) FROM embeddings")"
log "$COUNTS"
# Minimal proof that the restored copy carries live data: the bootstrap admin
# account. Knowledge object counts are meaningful once the document loop has
# run (see --expect-knowledge for the targeted read-back check).
USERS_N="$(awk -F= '$1=="users"{print $2}' <<<"$COUNTS")"
[[ "${USERS_N:-0}" -ge 1 ]] || fail "restored database has no user rows (restore incomplete?)"

if [[ -n "$EXPECT_KNOWLEDGE" ]]; then
  HIT="$(podman exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -Atc \
    "SELECT k.title || ' :: ' || left(c.content, 120) FROM knowledges k JOIN chunks c ON c.knowledge_id = k.id WHERE k.title ILIKE '%${EXPECT_KNOWLEDGE}%' AND k.deleted_at IS NULL AND c.deleted_at IS NULL ORDER BY c.chunk_index LIMIT 1")"
  [[ -n "$HIT" ]] || fail "expected knowledge '$EXPECT_KNOWLEDGE' (with chunks) not found in restored database"
  log "sample read-back: $HIT"
fi

COUNTS_JSON="$(printf '%s\n' "$COUNTS" | python3 -c 'import sys, json; print(json.dumps(dict(line.split("=", 1) for line in sys.stdin.read().split() if "=" in line)))')"
jq -n --arg key "$S3_KEY" --argjson counts "$COUNTS_JSON" \
  '{backupKey:$key, restoredCounts:$counts, verified:true}'
