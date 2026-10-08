#!/usr/bin/env bash
# App-level retrieval verification against an INDEPENDENT restored environment.
#
# Runs on the WeKnora capacity instance (amd64; the app image segfaults under
# local qemu emulation on arm64) over SSM, on a dedicated docker network with
# per-run unique names, everything torn down at exit:
#
#   1. fetches the backup dump from the backup bucket via a presigned URL
#      (the instance profile has no S3 grant),
#   2. restores it into a fresh paradedb container (template0 target — the
#      image's init DB pre-creates paradedb/tiger schemas),
#   3. starts redis + the WeKnora APP container (same ECR image as :4) with
#      S3 session credentials for document reads,
#   4. through the app API: admin login → hybrid search must return the
#      expected fact (live embedding call) → knowledge file download must be
#      byte-identical with the local reference file (md5).
#
# Usage (from the operator Mac):
#   verify_restore_app_level.sh --key db/weknora-<stamp>.dump \
#       --knowledge-id <uuid> --reference-file <local-original> \
#       [--instance-id i-...] [--expect-fact "ZETA-7-IRRIGATE-42"]
set -o pipefail

S3_KEY=""
KNOWLEDGE_ID=""
REFERENCE_FILE=""
EXPECT_FACT="ZETA-7-IRRIGATE-42"
INSTANCE_ID="i-047993b8bf8162200"
AWS_CLI_BIN="${AWS_CLI_BIN:-aws}"
REGION="${AWS_REGION:-us-east-1}"
PARAM_PREFIX="/supportportal/weknora"
ACCOUNT_ID="$("$AWS_CLI_BIN" sts get-caller-identity --query Account --output text)"
BACKUP_BUCKET="supportportal-weknora-backup-${ACCOUNT_ID}-${REGION}"
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/supportportal/weknora"
APP_IMAGE_TAG="app-714065baee6564e81bb0beec0445140edb00b91d"
RS="wkr$$$(date +%s)"

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[wkrestore-appverify] $*" >&2; }

while [[ $# -ge 1 ]]; do
  case "$1" in
    --key) S3_KEY="$2"; shift 2 ;;
    --knowledge-id) KNOWLEDGE_ID="$2"; shift 2 ;;
    --reference-file) REFERENCE_FILE="$2"; shift 2 ;;
    --expect-fact) EXPECT_FACT="$2"; shift 2 ;;
    --instance-id) INSTANCE_ID="$2"; shift 2 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[[ -n "$S3_KEY" && -n "$KNOWLEDGE_ID" && -n "$REFERENCE_FILE" ]] \
  || fail "--key/--knowledge-id/--reference-file are required"
[[ -f "$REFERENCE_FILE" ]] || fail "reference file not found: $REFERENCE_FILE"

# --- operator-side inputs ---
ssm() { "$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/$1" --with-decryption --query Parameter.Value --output text; }
DB_USER="$(ssm db_username)"; DB_NAME="$(ssm db_name)"; DB_PASSWORD="$(ssm db_password)"
REDIS_PASSWORD="$(ssm redis_password)"; JWT_SECRET="$(ssm jwt_secret)"
SYSTEM_AES_KEY="$(ssm system_aes_key)"; SYSTEM_SIGNING_KEY="$(ssm system_signing_key)"
ADMIN_EMAIL="$(ssm bootstrap_admin_email)"; ADMIN_PASSWORD="$(ssm admin_password)"
REF_MD5="$(md5 -q "$REFERENCE_FILE")"

# Fresh session credentials (long runs outlive earlier exports).
eval "$("$AWS_CLI_BIN" configure export-credentials --format env)"

# Presigned GET for the dump (1800s TTL) — same SigV4 recipe as the backup
# script; the local aws build's `s3 presign` only supports GET which is
# exactly what we need here.
PRESIGN="$(python3 - "$BACKUP_BUCKET" "$S3_KEY" "$REGION" <<'PYEOF'
import datetime, hashlib, hmac, os, sys, urllib.parse
bucket, key, region = sys.argv[1], sys.argv[2], sys.argv[3]
key_id = os.environ["AWS_ACCESS_KEY_ID"]; secret = os.environ["AWS_SECRET_ACCESS_KEY"]
token = os.environ.get("AWS_SESSION_TOKEN", "")
now = datetime.datetime.now(datetime.timezone.utc)
ds, ad = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
host = f"{bucket}.s3.{region}.amazonaws.com"
uri = "/" + urllib.parse.quote(key, safe="/")
p = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256",
     "X-Amz-Credential": f"{key_id}/{ds}/{region}/s3/aws4_request",
     "X-Amz-Date": ad, "X-Amz-Expires": "1800", "X-Amz-SignedHeaders": "host"}
if token:
    p["X-Amz-Security-Token"] = token
q = chr(39)  # empty-safe marker workaround not needed; quote with safe=""
cq = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in sorted(p.items()))
cr = "\n".join(["GET", uri, cq, f"host:{host}", "", "host", "UNSIGNED-PAYLOAD"])
st = "\n".join(["AWS4-HMAC-SHA256", ad, f"{ds}/{region}/s3/aws4_request",
                hashlib.sha256(cr.encode()).hexdigest()])
def h(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
sk = h(h(h(h(("AWS4" + secret).encode(), ds), region), "s3"), "aws4_request")
sig = hmac.new(sk, st.encode(), hashlib.sha256).hexdigest()
print(f"https://{host}{uri}?{cq}&X-Amz-Signature={sig}")
PYEOF
)" || fail "presign failed"

# --- build the remote script (values interpolated on the operator side) ---
REMOTE="$(mktemp -d)/wkr.sh"
cat > "$REMOTE" <<EOF
set -e
RS=$RS
NET=\$RS-net
ECR=$ECR_REPO
sudo docker rm -f \$RS-app \$RS-redis \$RS-pg >/dev/null 2>&1 || true
sudo docker network create \$NET >/dev/null 2>&1 || true
sudo docker run -d --name \$RS-pg --network \$NET \\
  -e POSTGRES_USER=$DB_USER -e POSTGRES_PASSWORD='$DB_PASSWORD' -e POSTGRES_DB=inittmp \\
  -e PGDATA=/var/lib/postgresql/data/pgdata \$ECR:base-paradedb-v0.22.6-pg17 >/dev/null
for i in \$(seq 1 40); do sudo docker exec \$RS-pg pg_isready -U $DB_USER -d inittmp >/dev/null 2>&1 && break; sleep 3; done
sleep 5
sudo docker exec \$RS-pg dropdb --if-exists -U $DB_USER $DB_NAME >/dev/null 2>&1 || true
sudo docker exec \$RS-pg createdb -T template0 -U $DB_USER $DB_NAME
sudo docker cp /tmp/wkr.dump \$RS-pg:/tmp/r.dump >/dev/null
sudo docker exec \$RS-pg pg_restore -U $DB_USER -d $DB_NAME --no-owner --no-privileges --role=$DB_USER --exit-on-error /tmp/r.dump >/dev/null
echo RESTORE-OK
sudo docker run -d --name \$RS-redis --network \$NET -e REDIS_PASSWORD='$REDIS_PASSWORD' \\
  docker.io/library/redis:7.0-alpine sh -c "exec redis-server --appendonly yes --requirepass \\"\\\$REDIS_PASSWORD\\"" >/dev/null
sudo docker run -d --name \$RS-app --network \$NET -p 18081:8080 \\
  -e DB_DRIVER=postgres -e DB_HOST=\$RS-pg -e DB_PORT=5432 \\
  -e DB_USER=$DB_USER -e DB_PASSWORD='$DB_PASSWORD' -e DB_NAME=$DB_NAME -e DB_SSLMODE=disable \\
  -e RETRIEVE_DRIVER=postgres -e REDIS_ADDR=\$RS-redis:6379 -e REDIS_PASSWORD='$REDIS_PASSWORD' \\
  -e STORAGE_TYPE=s3 -e S3_REGION=$REGION \\
  -e S3_BUCKET_NAME=supportportal-weknora-docs-${ACCOUNT_ID}-${REGION} -e S3_USE_SSL=true \\
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \\
  -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN -e AWS_REGION=$REGION \\
  -e JWT_SECRET='$JWT_SECRET' -e SYSTEM_AES_KEY='$SYSTEM_AES_KEY' -e SYSTEM_SIGNING_KEY='$SYSTEM_SIGNING_KEY' \\
  -e DISABLE_REGISTRATION=true -e GIN_MODE=release -e LOG_LEVEL=info -e AUTO_MIGRATE=false \\
  -e FRONTEND_BASE_URL=http://localhost:18081 \\
  \$ECR:$APP_IMAGE_TAG >/dev/null
for i in \$(seq 1 60); do
  curl -sS -o /dev/null --max-time 3 http://localhost:18081/health 2>/dev/null && { echo APP-HEALTHY; break; }
  sleep 4
done
B=http://localhost:18081/api/v1
TOKEN=\$(curl -sS -X POST -H "Content-Type: application/json" \\
  -d '{"email":"$ADMIN_EMAIL","password":"$ADMIN_PASSWORD"}' \$B/auth/login \\
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
KB=\$(curl -sS -H "Authorization: Bearer \$TOKEN" \$B/knowledge-bases \\
  | python3 -c 'import sys,json;d=json.load(sys.stdin);items=(d.get("data") or d.get("list") or []);items=items.get("list",items) if isinstance(items,dict) else items;print(items[0]["id"])')
echo KB=\$KB
curl -sS -X POST -H "Content-Type: application/json" -H "Authorization: Bearer \$TOKEN" \\
  -d '{"query_text":"灌溉故障码","top_k":5}' \$B/knowledge-bases/\$KB/hybrid-search > /tmp/wkr_search.json
FACT='$EXPECT_FACT' python3 - <<'PYV'
import json, os
d = json.load(open("/tmp/wkr_search.json")); fact = os.environ["FACT"]; found = [False]
def walk(x):
    if isinstance(x, dict):
        v = x.get("content")
        if isinstance(v, str) and fact in v: found[0] = True
        for vv in x.values(): walk(vv)
    elif isinstance(x, list):
        for it in x: walk(it)
walk(d)
print("APP-SEARCH:", "PASS" if found[0] else "FAIL")
PYV
curl -sS -H "Authorization: Bearer \$TOKEN" \$B/knowledge/$KNOWLEDGE_ID/download \\
  -o /tmp/wkr_dl.bin -w "DL-HTTP %{http_code}\n"
echo "DL-MD5 \$(md5sum /tmp/wkr_dl.bin | awk '{print \$1}') EXPECT $REF_MD5"
sudo docker rm -f \$RS-app \$RS-redis \$RS-pg >/dev/null
sudo docker network rm \$NET >/dev/null
rm -f /tmp/wkr.dump /tmp/wkr_search.json /tmp/wkr_dl.bin
echo TEARDOWN-OK
EOF

# --- stage dump + remote script on the instance, run, fetch output ---
log "staging dump and script on $INSTANCE_ID"
STAGE_CID="$("$AWS_CLI_BIN" ssm send-command --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript --comment "wkrestore stage" --timeout-seconds 300 \
  --parameters "commands=[\"curl -sS '$PRESIGN' -o /tmp/wkr.dump && stat -c %s /tmp/wkr.dump\"]" \
  --query "Command.CommandId" --output text)" || fail "stage send failed"
sleep 8
"$AWS_CLI_BIN" ssm get-command-invocation --command-id "$STAGE_CID" --instance-id "$INSTANCE_ID" \
  --query StandardOutputContent --output text | grep -E "^[0-9]+$" >/dev/null || fail "dump staging failed"

B64="$(base64 -i "$REMOTE")"
PARAMS="$(mktemp).json"
python3 -c "
import json, sys
cmd = 'echo $B64 | base64 -d > /tmp/wkr_remote.sh && bash /tmp/wkr_remote.sh; RC=\$?; rm -f /tmp/wkr_remote.sh; exit \$RC'
print(json.dumps({'InstanceIds':['$INSTANCE_ID'],'DocumentName':'AWS-RunShellScript','Comment':'wkrestore app-level verify','TimeoutSeconds':1500,'Parameters':{'commands':[cmd]}}))
" > "$PARAMS"
CID="$("$AWS_CLI_BIN" ssm send-command --cli-input-json "file://$PARAMS" --query "Command.CommandId" --output text)" \
  || fail "verify send failed"
log "running verification ($CID)"
STATUS="InProgress"
for _ in $(seq 1 90); do
  sleep 15
  STATUS="$("$AWS_CLI_BIN" ssm get-command-invocation --command-id "$CID" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null)"
  case "$STATUS" in Success|Failed|TimedOut|Cancelled) break ;; esac
done
OUT="$("$AWS_CLI_BIN" ssm get-command-invocation --command-id "$CID" --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text)"
ERR="$("$AWS_CLI_BIN" ssm get-command-invocation --command-id "$CID" --instance-id "$INSTANCE_ID" --query StandardErrorContent --output text)"
echo "$OUT"
[ -n "$ERR" ] && echo "[stderr] $ERR" >&2
[ "$STATUS" = "Success" ] || fail "remote verification ended in $STATUS"

echo "$OUT" | grep -q "RESTORE-OK" || fail "restore step missing"
echo "$OUT" | grep -q "APP-HEALTHY" || fail "app never became healthy"
echo "$OUT" | grep -q "APP-SEARCH: PASS" || fail "app-level search failed"
echo "$OUT" | grep -q "DL-HTTP 200" || fail "download non-200"
DL_MD5="$(echo "$OUT" | grep -o 'DL-MD5 [0-9a-f]*' | awk '{print $2}')"
[ "$DL_MD5" = "$REF_MD5" ] || fail "downloaded md5 ($DL_MD5) != reference ($REF_MD5)"
echo "$OUT" | grep -q "TEARDOWN-OK" || fail "teardown marker missing"

jq -n --arg key "$S3_KEY" --arg kb "$(echo "$OUT" | grep -o 'KB=[0-9a-f-]*' | cut -d= -f2)" \
  --arg fact "$EXPECT_FACT" --arg md5 "$REF_MD5" \
  '{backupKey:$key, appLevelSearch:"PASS", appLevelFileReadBack:"PASS", fileMD5:$md5, fact:$fact}'
