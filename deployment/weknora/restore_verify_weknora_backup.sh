#!/usr/bin/env bash
# Restore a WeKnora backup into an INDEPENDENT local verification target (a
# throwaway podman paradedb container) and verify the restored data at the
# DATABASE level:
#   - object counts match the pre-backup baseline manifest (when present);
#   - at minimum, the account data (users) survived;
#   - with --expect-knowledge: the named knowledge exists, its chunks are
#     non-empty, and a full chunk body can be read back.
#
# This script does NOT prove app-level retrieval (search + file read-back
# through the running WeKnora API); that is verified separately once the
# document loop has run. The output JSON states exactly which layers were
# verified.
#
# Resource safety: every run uses a unique work directory and container name,
# and the cleanup trap only removes resources THIS run created — a name
# collision fails loudly instead of deleting another run's container.
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

RUN_ID="$$-$(date +%s)"
CONTAINER="weknora-restore-verify-${RUN_ID}"
WORK_DIR="$HOME/.tmp-weknora-restore-${RUN_ID}"

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

if podman container exists "$CONTAINER" 2>/dev/null; then
  fail "container name $CONTAINER already exists (unexpected collision); refusing to touch it"
fi
if [[ -e "$WORK_DIR" ]]; then
  fail "work dir $WORK_DIR already exists; refusing to touch it"
fi
mkdir -p "$WORK_DIR"; chmod 700 "$WORK_DIR"

CONTAINER_CREATED=0
cleanup() {
  if [[ "$CONTAINER_CREATED" == "1" ]]; then
    podman rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

DB_USER="$("$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_username" --with-decryption --query Parameter.Value --output text)"
DB_NAME="$("$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_name" --with-decryption --query Parameter.Value --output text)"
DUMP_FILE="$(basename "$S3_KEY")"
"$AWS_CLI_BIN" s3 cp "s3://${BACKUP_BUCKET}/${S3_KEY}" "$WORK_DIR/$DUMP_FILE" --region "$REGION" >/dev/null || fail "dump download failed"
log "dump downloaded: $DUMP_FILE"

# Optional baseline manifest written by backup_weknora_database.sh.
MANIFEST_PATH=""
if "$AWS_CLI_BIN" s3 cp "s3://${BACKUP_BUCKET}/${S3_KEY}.manifest.json" "$WORK_DIR/manifest.json" --region "$REGION" >/dev/null 2>&1; then
  MANIFEST_PATH="$WORK_DIR/manifest.json"
  log "baseline manifest found: $(jq -c .baselineCounts "$MANIFEST_PATH")"
else
  log "no baseline manifest alongside the dump; count comparison will be skipped"
fi

# Independent verification target: fresh paradedb container with its own
# anonymous volume; nothing from the live deployment is referenced.
if ! podman run -d --name "$CONTAINER" -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD=restore-verify \
  -e POSTGRES_DB="$DB_NAME" -e PGDATA=/var/lib/postgresql/data/pgdata "$PARADEDB_IMAGE" >/dev/null 2>&1; then
  fail "cannot start verification container $CONTAINER (name collision or image missing); nothing was removed"
fi
CONTAINER_CREATED=1
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

USERS_N="$(awk -F= '$1=="users"{print $2}' <<<"$COUNTS")"
[[ "${USERS_N:-0}" -ge 1 ]] || fail "restored database has no user rows (restore incomplete?)"

COUNTS_MATCHED=false
BASELINE_JSON="{}"
if [[ -n "$MANIFEST_PATH" ]]; then
  MISMATCH=""
  while IFS='=' read -r k v; do
    base_v="$(jq -r --arg k "$k" '.baselineCounts[$k] // "missing"' "$MANIFEST_PATH")"
    [[ "$base_v" == "$v" ]] || MISMATCH="${MISMATCH}${k}: restored=${v} baseline=${base_v}; "
  done <<<"$COUNTS"
  if [[ -n "$MISMATCH" ]]; then
    fail "restored counts differ from the pre-backup baseline: $MISMATCH"
  fi
  COUNTS_MATCHED=true
  BASELINE_JSON="$(jq -c .baselineCounts "$MANIFEST_PATH")"
fi

KNOWLEDGE_READBACK=false
KNOWLEDGE_DETAIL="{}"
if [[ -n "$EXPECT_KNOWLEDGE" ]]; then
  # Exact-title match + full-body read-back of the knowledge's chunks.
  ROWS="$(podman exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -Atc \
    "SELECT k.id || '|' || count(c.id) || '|' || COALESCE(sum(length(c.content)),0) FROM knowledges k LEFT JOIN chunks c ON c.knowledge_id = k.id WHERE k.title = '${EXPECT_KNOWLEDGE}' AND k.deleted_at IS NULL GROUP BY k.id")"
  [[ -n "$ROWS" ]] || fail "expected knowledge '${EXPECT_KNOWLEDGE}' (exact title) not found in restored database"
  log "knowledge row (id|chunks|content_bytes): $ROWS"
  CHUNKS_N="$(cut -d'|' -f2 <<<"$ROWS" | head -1)"
  [[ "${CHUNKS_N:-0}" -ge 1 ]] || fail "knowledge '${EXPECT_KNOWLEDGE}' restored without chunks"
  FULL_CHUNK="$(podman exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -Atc \
    "SELECT c.content FROM knowledges k JOIN chunks c ON c.knowledge_id = k.id WHERE k.title = '${EXPECT_KNOWLEDGE}' AND k.deleted_at IS NULL ORDER BY c.chunk_index LIMIT 1")"
  [[ -n "$FULL_CHUNK" ]] || fail "could not read back chunk content for '${EXPECT_KNOWLEDGE}'"
  log "full chunk read-back (first 160 chars): ${FULL_CHUNK:0:160}"
  KNOWLEDGE_READBACK=true
  KNOWLEDGE_DETAIL="$(jq -n --arg t "$EXPECT_KNOWLEDGE" --argjson chunks "${CHUNKS_N}" '{title:$t, chunks:$chunks, fullChunkReadBack:true}')"
fi

COUNTS_JSON="$(printf '%s\n' "$COUNTS" | python3 -c 'import sys, json; print(json.dumps(dict(line.split("=", 1) for line in sys.stdin.read().split() if "=" in line)))')"
jq -n --arg key "$S3_KEY" --argjson counts "$COUNTS_JSON" --argjson baseline "$BASELINE_JSON" \
  --argjson countsMatched "$COUNTS_MATCHED" --argjson knowledge "$KNOWLEDGE_READBACK" --argjson knowledgeDetail "$KNOWLEDGE_DETAIL" \
  '{backupKey:$key,
    dbLevelVerified:true,
    restoredCounts:$counts,
    baselineCounts:($baseline | if . == {} then "not-available" else . end),
    countsMatchBaseline:$countsMatched,
    knowledgeReadBack:$knowledge,
    knowledgeDetail:$knowledgeDetail,
    appLevelRetrievalVerified:false,
    note:"db-level only: app-level search + file read-back are verified separately after the document loop runs"}'
