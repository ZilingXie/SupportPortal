#!/usr/bin/env bash
# Backup the WeKnora ParadeDB database to the dedicated backup bucket.
#
# Path: local Mac --(SSM port-forward via the capacity instance)--> paradedb
# task ENI :5432 --TLS--> pg_dump (run inside the version-matched paradedb
# ECR image) --custom format--> S3.
#
# Usage:
#   backup_weknora_database.sh [--local-port 15432]
set -Eeuo pipefail

LOCAL_PORT=15432
AWS_CLI_BIN="${AWS_CLI_BIN:-aws}"
REGION="${AWS_REGION:-us-east-1}"
PARAM_PREFIX="/supportportal/weknora"
CLUSTER="supportportal-weknora"
ACCOUNT_ID="$("$AWS_CLI_BIN" sts get-caller-identity --query Account --output text)"
BACKUP_BUCKET="supportportal-weknora-backup-${ACCOUNT_ID}-${REGION}"
PARADEDB_IMAGE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/supportportal/weknora:base-paradedb-v0.22.6-pg17"

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[weknora-backup] $*" >&2; }

while [[ $# -ge 1 ]]; do
  case "$1" in
    --local-port) [[ $# -ge 2 ]] || fail "--local-port requires a value"; LOCAL_PORT="$2"; shift 2 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

WORK_DIR="$HOME/.tmp-weknora-backup"
rm -rf "$WORK_DIR"; mkdir -p "$WORK_DIR"; chmod 700 "$WORK_DIR"
SESSION_ID=""
cleanup() {
  [[ -n "$SESSION_ID" ]] && "$AWS_CLI_BIN" ssm terminate-session --session-id "$SESSION_ID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

DB_USER="$("$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_username" --with-decryption --query Parameter.Value --output text)"
DB_NAME="$("$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_name" --with-decryption --query Parameter.Value --output text)"
DB_PASSWORD="$("$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_password" --with-decryption --query Parameter.Value --output text)"
"$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_tls_ca_pem" --with-decryption --query Parameter.Value --output text > "$WORK_DIR/ca.pem"

# Resolve the paradedb task ENI IP and the capacity instance id.
TASK_ARN="$("$AWS_CLI_BIN" ecs list-tasks --cluster "$CLUSTER" --service-name weknora-paradedb --query 'taskArns[0]' --output text)" \
  || fail "cannot find weknora-paradedb task"
[[ -n "$TASK_ARN" && "$TASK_ARN" != "None" ]] || fail "weknora-paradedb has no running task"
TASK_IP="$("$AWS_CLI_BIN" ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].attachments[?name==`eni`].details[?name==`privateIPv4Address`].value|[0][0]' --output text)"
[[ -n "$TASK_IP" && "$TASK_IP" != "None" ]] || fail "cannot resolve paradedb task private IP"
INSTANCE_ID="$("$AWS_CLI_BIN" autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names supportportal-weknora-db-capacity \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)"
[[ -n "$INSTANCE_ID" && "$INSTANCE_ID" != "None" ]] || fail "cannot resolve capacity instance"
log "paradedb task $TASK_IP via instance $INSTANCE_ID -> local port $LOCAL_PORT"

"$AWS_CLI_BIN" ssm start-session \
  --target "$INSTANCE_ID" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"$TASK_IP\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}" \
  --region "$REGION" > "$WORK_DIR/session.log" 2>&1 &
SESSION_LAUNCH_PID=$!
for _ in $(seq 1 30); do
  sleep 2
  SESSION_ID="$(grep -o 'i-[0-9a-f]*-[0-9a-zA-Z]*' "$WORK_DIR/session.log" 2>/dev/null | head -1 || true)"
  if [[ -n "$SESSION_ID" ]] && nc -z localhost "$LOCAL_PORT" 2>/dev/null; then break; fi
done
nc -z localhost "$LOCAL_PORT" 2>/dev/null || fail "port-forward did not come up (see $WORK_DIR/session.log)"
log "port-forward session $SESSION_ID up"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_FILE="weknora-${DB_NAME}-${STAMP}.dump"

# pg_dump must match the server major version: run it inside the same
# paradedb image the deployment uses. ECR login first.
"$AWS_CLI_BIN" ecr get-login-password --region "$REGION" | podman login --username AWS --password-stdin \
  "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null 2>&1 || fail "ecr login failed"
podman pull --quiet "$PARADEDB_IMAGE" >/dev/null || fail "cannot pull paradedb image"
podman run --rm \
  -v "$WORK_DIR:/work:Z" \
  -e PGPASSWORD="$DB_PASSWORD" \
  "$PARADEDB_IMAGE" \
  pg_dump "host=host.containers.internal port=${LOCAL_PORT} user=${DB_USER} dbname=${DB_NAME} sslmode=verify-ca sslrootcert=/work/ca.pem" \
    --no-owner --no-privileges -Fc -f "/work/$DUMP_FILE" \
  || fail "pg_dump failed"

SIZE="$(stat -f%z "$WORK_DIR/$DUMP_FILE" 2>/dev/null || stat -c%s "$WORK_DIR/$DUMP_FILE")"
[[ "${SIZE:-0}" -gt 0 ]] || fail "dump file missing or empty"
SHA256="$(shasum -a 256 "$WORK_DIR/$DUMP_FILE" | cut -d' ' -f1)"
"$AWS_CLI_BIN" s3 cp "$WORK_DIR/$DUMP_FILE" "s3://${BACKUP_BUCKET}/db/${DUMP_FILE}" --region "$REGION" >/dev/null \
  || fail "s3 upload failed"
log "backup uploaded: s3://${BACKUP_BUCKET}/db/${DUMP_FILE} (${SIZE} bytes, sha256=${SHA256:0:16}...)"

jq -n --arg bucket "$BACKUP_BUCKET" --arg key "db/${DUMP_FILE}" --arg sha256 "$SHA256" --arg size "$SIZE" \
  --arg stamp "$STAMP" \
  '{bucket:$bucket, key:$key, sha256:$sha256, bytes:($size|tonumber), createdAt:$stamp}'
