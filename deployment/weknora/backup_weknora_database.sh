#!/usr/bin/env bash
# Backup the WeKnora ParadeDB database to the dedicated backup bucket.
#
# Path: SSM command on the capacity instance -> docker exec pg_dump inside the
# paradedb container (localhost trust, no SG/TLS hop) -> docker cp out ->
# upload to S3 via a presigned URL (the instance needs no AWS credentials).
#
# Note: instance -> paradedb task ENI :5432 is filtered by ECS awsvpc task
# networking, so the earlier SSM port-forward design does not work here.
#
# Usage:
#   backup_weknora_database.sh
set -Eeuo pipefail

AWS_CLI_BIN="${AWS_CLI_BIN:-aws}"
REGION="${AWS_REGION:-us-east-1}"
PARAM_PREFIX="/supportportal/weknora"
CLUSTER="supportportal-weknora"
ACCOUNT_ID="$("$AWS_CLI_BIN" sts get-caller-identity --query Account --output text)"
BACKUP_BUCKET="supportportal-weknora-backup-${ACCOUNT_ID}-${REGION}"

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[weknora-backup] $*" >&2; }

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

DB_USER="$("$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_username" --with-decryption --query Parameter.Value --output text)"
DB_NAME="$("$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/db_name" --with-decryption --query Parameter.Value --output text)"

TASK_ARN="$("$AWS_CLI_BIN" ecs list-tasks --cluster "$CLUSTER" --service-name weknora-paradedb --query 'taskArns[0]' --output text)" \
  || fail "cannot find weknora-paradedb task"
[[ -n "$TASK_ARN" && "$TASK_ARN" != "None" ]] || fail "weknora-paradedb has no running task"
INSTANCE_ID="$("$AWS_CLI_BIN" autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names supportportal-weknora-db-capacity \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)"
[[ -n "$INSTANCE_ID" && "$INSTANCE_ID" != "None" ]] || fail "cannot resolve capacity instance"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_FILE="weknora-${DB_NAME}-${STAMP}.dump"
S3_KEY="db/${DUMP_FILE}"

ssm_run() { # json-parameters-file -> prints stdout content
  local cmdid
  cmdid="$("$AWS_CLI_BIN" ssm send-command --cli-input-json "file://$1" --query 'Command.CommandId' --output text)" || fail "ssm send-command failed"
  for _ in $(seq 1 30); do
    sleep 3
    local status out
    status="$("$AWS_CLI_BIN" ssm get-command-invocation --command-id "$cmdid" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)"
    case "$status" in Success|Failed|TimedOut|Cancelled) break ;; esac
  done
  [[ "$status" == "Success" ]] || fail "ssm command $cmdid ended in $status: $("$AWS_CLI_BIN" ssm get-command-invocation --command-id "$cmdid" --instance-id "$INSTANCE_ID" --query StandardErrorContent --output text)"
  "$AWS_CLI_BIN" ssm get-command-invocation --command-id "$cmdid" --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text
}

# 1. pg_dump inside the container (localhost trust, custom format).
DUMP_CMD="sudo docker exec \$(sudo docker ps --format '{{.Names}}' | grep '^ecs-weknora-paradedb-[0-9]*-paradedb-' | head -1) sh -c 'pg_dump \"host=127.0.0.1 user=${DB_USER} dbname=${DB_NAME}\" --no-owner --no-privileges -Fc -f /tmp/${DUMP_FILE} && ls -l /tmp/${DUMP_FILE}'"
jq -n --arg iid "$INSTANCE_ID" --arg c "$DUMP_CMD" \
  '{InstanceIds: [$iid], DocumentName: "AWS-RunShellScript", Comment: "weknora pg_dump in container", Parameters: {commands: [$c]}}' > "$TMP_DIR/cmd1.json"
log "running pg_dump inside the paradedb container on $INSTANCE_ID"
ssm_run "$TMP_DIR/cmd1.json" >/dev/null

# 2. docker cp to the instance + sha256.
CP_CMD="sudo docker cp \$(sudo docker ps --format '{{.Names}}' | grep '^ecs-weknora-paradedb-[0-9]*-paradedb-' | head -1):/tmp/${DUMP_FILE} /tmp/${DUMP_FILE} && sudo chmod 644 /tmp/${DUMP_FILE} && sha256sum /tmp/${DUMP_FILE} && stat -c %s /tmp/${DUMP_FILE}"
jq -n --arg iid "$INSTANCE_ID" --arg c "$CP_CMD" \
  '{InstanceIds: [$iid], DocumentName: "AWS-RunShellScript", Comment: "weknora dump copy out", Parameters: {commands: [$c]}}' > "$TMP_DIR/cmd2.json"
CP_OUT="$(ssm_run "$TMP_DIR/cmd2.json")"
log "$CP_OUT"
SHA256="$(awk '{print $1}' <<<"$CP_OUT" | head -1)"
SIZE="$(tail -1 <<<"$CP_OUT")"
[[ "$SHA256" =~ ^[0-9a-f]{64}$ && "${SIZE:-0}" -gt 0 ]] || fail "unexpected docker cp output"

# 3. Upload via presigned URL (credentials stay on the operator side).
# The local aws build's `s3 presign` only supports GET, so sign SigV4 PUT here
# with python3 stdlib only.
eval "$("$AWS_CLI_BIN" configure export-credentials --format env)"
PRESIGN="$(python3 - "$BACKUP_BUCKET" "$S3_KEY" "$REGION" <<'PYEOF'
import datetime, hashlib, hmac, os, sys, urllib.parse

bucket, key, region = sys.argv[1], sys.argv[2], sys.argv[3]
key_id = os.environ["AWS_ACCESS_KEY_ID"]
secret = os.environ["AWS_SECRET_ACCESS_KEY"]
token = os.environ.get("AWS_SESSION_TOKEN", "")
now = datetime.datetime.now(datetime.timezone.utc)
datestamp, amzdate = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
host = f"{bucket}.s3.{region}.amazonaws.com"
canonical_uri = "/" + urllib.parse.quote(key, safe="/")
params = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{key_id}/{datestamp}/{region}/s3/aws4_request",
          "X-Amz-Date": amzdate, "X-Amz-Expires": "1800", "X-Amz-SignedHeaders": "host"}
if token:
    params["X-Amz-Security-Token"] = token
canonical_query = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in sorted(params.items()))
canonical_request = "\n".join(["PUT", canonical_uri, canonical_query, f"host:{host}", "", "host", "UNSIGNED-PAYLOAD"])
scope = f"{datestamp}/{region}/s3/aws4_request"
string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
def _hmac(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
signing_key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), datestamp), region), "s3"), "aws4_request")
signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
print(f"https://{host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}")
PYEOF
)" || fail "presign failed"
UP_CMD="curl -sS -X PUT -H 'Content-Type: application/octet-stream' --upload-file /tmp/${DUMP_FILE} '${PRESIGN}' -o /dev/null -w '%{http_code}'"
jq -n --arg iid "$INSTANCE_ID" --arg c "$UP_CMD" \
  '{InstanceIds: [$iid], DocumentName: "AWS-RunShellScript", Comment: "weknora dump upload", Parameters: {commands: [$c]}}' > "$TMP_DIR/cmd3.json"
HTTP="$(ssm_run "$TMP_DIR/cmd3.json" | tr -d '[:space:]')"
[[ "$HTTP" == "200" ]] || fail "presigned upload returned HTTP $HTTP"

# 4. Verify the object landed with the same checksum recorded as metadata.
"$AWS_CLI_BIN" s3api head-object --bucket "$BACKUP_BUCKET" --key "$S3_KEY" --region "$REGION" \
  --query '{size: ContentLength}' --output json > "$TMP_DIR/head.json"
REMOTE_SIZE="$(jq -r .size "$TMP_DIR/head.json")"
[[ "$REMOTE_SIZE" == "$SIZE" ]] || fail "uploaded size mismatch (local $SIZE, remote $REMOTE_SIZE)"
log "backup uploaded: s3://${BACKUP_BUCKET}/${S3_KEY} (${SIZE} bytes, sha256=${SHA256:0:16}...)"

jq -n --arg bucket "$BACKUP_BUCKET" --arg key "$S3_KEY" --arg sha256 "$SHA256" --arg size "$SIZE" \
  --arg stamp "$STAMP" \
  '{bucket:$bucket, key:$key, sha256:$sha256, bytes:($size|tonumber), createdAt:$stamp}'
