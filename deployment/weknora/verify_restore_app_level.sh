#!/usr/bin/env bash
# App-level retrieval verification against an INDEPENDENT restored environment.
#
# Runs on the WeKnora capacity instance (amd64; the app image segfaults under
# local qemu emulation on arm64) over SSM, on a dedicated docker network with
# per-run unique names. Resource ownership is fail-closed end to end:
#
#   - the REMOTE script preflight-refuses any name/network collision (never
#     pre-deletes), records every resource it successfully creates into an
#     ownership file, and its EXIT trap removes exactly those resources —
#     on success AND on every failure path (single-query semantics: a
#     container that cannot be confirmed absent is KEPT and reported);
#   - the OUTER script installs an EXIT-trap RESCUE that replays the remote
#     ownership file, so an SSM interruption/timeout (SIGKILL skips the
#     remote trap) still cleans only this run's recorded resources;
#   - fault injections (restore / app-start / login / search / download /
#     hang+SSM-timeout / name collision) are built in and exercised by
#     --self-check, which asserts: failed runs leave NOTHING behind, and a
#     pre-existing same-named container survives untouched.
#
# Real mode steps on the instance: presigned dump fetch → paradedb (template0
# target) restore → redis + WeKnora APP (same ECR image as :4) → admin login
# → hybrid search must return --expect-fact → file download byte-compared
# (md5) with the local reference file.
#
# Usage (from the operator Mac):
#   verify_restore_app_level.sh --key db/weknora-<stamp>.dump \
#       --knowledge-id <uuid> --reference-file <local-original> \
#       [--instance-id i-...] [--expect-fact "ZETA-7-IRRIGATE-42"]
#   verify_restore_app_level.sh --self-check [--instance-id i-...]
set -o pipefail

MODE="real"
[[ "${1:-}" == "--self-check" ]] && MODE="self-check"

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

FAILURES=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1" >&2; FAILURES=$((FAILURES + 1)); }
fail() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[wkrestore-appverify] $*" >&2; }

# ssm_run <comment> <timeout-seconds> <remote-script-file> [env-prefix]
# Sends a base64-wrapped script; sets CID globally; polls to completion.
ssm_run() {
  local comment="$1" tmo="$2" script="$3" envpfx="${4:-}"
  local b64 params cid
  b64="$(base64 -i "$script")"
  params="$(mktemp).json"
  python3 - "$b64" "$envpfx" "$INSTANCE_ID" "$comment" "$tmo" > "$params" <<'PYEOF'
import json, sys
b64, envpfx, inst, comment, tmo = sys.argv[1:6]
cmd = f"{envpfx}echo {b64} | base64 -d > /tmp/wkr_inner.sh && bash /tmp/wkr_inner.sh; RC=$?; rm -f /tmp/wkr_inner.sh; exit $RC"
print(json.dumps({"InstanceIds": [inst], "DocumentName": "AWS-RunShellScript",
                  "Comment": comment, "TimeoutSeconds": int(tmo),
                  "Parameters": {"commands": [cmd]}}))
PYEOF
  cid="$("$AWS_CLI_BIN" ssm send-command --cli-input-json "file://$params" --query "Command.CommandId" --output text)" \
    || { echo "send failed" >&2; return 1; }
  CID="$cid"
  local st="InProgress"
  for _ in $(seq 1 220); do
    sleep 5
    st="$("$AWS_CLI_BIN" ssm get-command-invocation --command-id "$cid" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null)"
    case "$st" in Success|Failed|TimedOut|Cancelled) break ;; esac
  done
  REMOTE_STATUS="$st"
  REMOTE_OUT="$("$AWS_CLI_BIN" ssm get-command-invocation --command-id "$cid" --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text)"
  REMOTE_ERR="$("$AWS_CLI_BIN" ssm get-command-invocation --command-id "$cid" --instance-id "$INSTANCE_ID" --query StandardErrorContent --output text)"
}

# ---------------------------------------------------------------------------
# Shared operator-side inputs.
# ---------------------------------------------------------------------------
common_setup() {
  ssm() { "$AWS_CLI_BIN" ssm get-parameter --name "${PARAM_PREFIX}/$1" --with-decryption --query Parameter.Value --output text; }
  WK_DB_USER="$(ssm db_username)"; WK_DB_NAME="$(ssm db_name)"; WK_DB_PASSWORD="$(ssm db_password)"
  WK_REDIS_PASSWORD="$(ssm redis_password)"; WK_JWT_SECRET="$(ssm jwt_secret)"
  WK_AES_KEY="$(ssm system_aes_key)"; WK_SIGNING_KEY="$(ssm system_signing_key)"
  WK_ADMIN_EMAIL="$(ssm bootstrap_admin_email)"; WK_ADMIN_PASSWORD="$(ssm admin_password)"
  eval "$("$AWS_CLI_BIN" configure export-credentials --format env)"
  "$AWS_CLI_BIN" ecr get-login-password --region "$REGION" >/dev/null 2>&1 || fail "no AWS access"
}

# build_remote_script <output-file> <rs> [fault]
# Emits the remote verification script with operator values interpolated and
# RS_FAULT baked in. The remote script is ownership-tracked and trap-cleaned.
build_remote_script() {
  local out="$1" rs="$2" fault="${3:-}" presign="${4:-}"
  local prelude=""
  if [ -n "$presign" ]; then
    prelude="curl -sS '$presign' -o /tmp/wkr-$rs.dump || { echo 'dump fetch failed' >&2; exit 80; }
"
  fi
  cat > "$out" <<EOF
$prelude
set -u
RS=$rs
FAULT='${fault}'
NET=\$RS-net
PG=\$RS-pg; RD=\$RS-redis; AP=\$RS-app
OWNED=/tmp/wkr-owned-\$RS          # one marker per line: container:<name> | net:<name> | file:<path>
CLEANLOG=/tmp/wkr-clean-\$RS.log
touch "\$OWNED"

container_state() {  # present|absent|unknown  (single query, fail-closed)
  # NOTE: this host\'s docker lacks the "container exists" subcommand (it
  # prints usage and exits 1); derive state from the name list instead,
  # mapping a docker failure to unknown.
  local names
  if names="\$(sudo docker ps -a --format "{{.Names}}" 2>/dev/null)"; then
    if printf "%s\n" "\$names" | grep -qx "\$1"; then echo present; else echo absent; fi
  else
    echo unknown
  fi
}

# Owner proof: every resource THIS run creates carries the per-run label
# wkrestore-owner=<RS>. Deletion requires name AND label to match; a
# same-named resource WITHOUT our label was created by someone else (e.g. a
# racer that won the TOCTOU window after preflight) and is never touched.
owner_of_container() {
  sudo docker inspect -f "{{index .Config.Labels \"wkrestore-owner\"}}" "\$1" 2>/dev/null
}
owner_of_network() {
  sudo docker inspect -f "{{index .Labels \"wkrestore-owner\"}}" "\$1" 2>/dev/null
}
OWNED_LABEL="\$RS"

# release_foreign <kind> <name>: a pre-registered name exists but carries a
# foreign/absent owner label — drop OUR marker (we never owned the object)
# and record the decision; never delete.
release_foreign() {
  echo "[cleanup] \$1 \$2 exists but owner label does not match this run; FOREIGN — not touching it" >> "\$CLEANLOG"
  sed -i "\|^\$1:\$2\$|d" "\$OWNED"
}

cleanup() {
  local kept=0 line kind name st lbl
  # Pass 1: containers and files (containers first so network endpoints go).
  while IFS= read -r line; do
    [ -n "\$line" ] || continue
    kind="\${line%%:*}"; name="\${line#*:}"
    case "\$kind" in
      container)
        st="\$(container_state "\$name")"
        if [ "\$st" != present ]; then
          # absent or unknown-but-gone-listed: drop marker (absent path);
          # unknown (docker failed) keeps it fail-closed below via label check skip.
          if [ "\$st" = absent ]; then
            sed -i "\|^container:\$name\$|d" "\$OWNED"
          else
            echo "[cleanup] container \$name state \$st (query failed); KEEPING" >> "\$CLEANLOG"
            kept=1
          fi
        else
          lbl="\$(owner_of_container "\$name")"
          if [ "\$lbl" != "\$OWNED_LABEL" ]; then
            release_foreign container "\$name"      # foreign racer (or inspect failed) — never delete
          else
            sudo docker rm -f "\$name" >/dev/null 2>&1 || true
            st="\$(container_state "\$name")"
            if [ "\$st" = absent ]; then
              sed -i "\|^container:\$name\$|d" "\$OWNED"
            else
              echo "[cleanup] container \$name is \$st after removal; KEEPING for manual cleanup" >> "\$CLEANLOG"
              kept=1
            fi
          fi
        fi ;;
      file)
        rm -f "\$name"
        sed -i "\|^file:\$name\$|d" "\$OWNED" ;;
    esac
  done < "\$OWNED"
  # Pass 2: networks (endpoint detach is asynchronous after container rm).
  while IFS= read -r line; do
    [ -n "\$line" ] || continue
    kind="\${line%%:*}"; name="\${line#*:}"
    [ "\$kind" = net ] || continue
    if ! sudo docker network inspect "\$name" >/dev/null 2>&1; then
      sed -i "\|^net:\$name\$|d" "\$OWNED"
      continue
    fi
    lbl="\$(owner_of_network "\$name")"
    if [ "\$lbl" != "\$OWNED_LABEL" ]; then
      release_foreign net "\$name"
      continue
    fi
    local gone=0 ep
    for _ in \$(seq 1 15); do
      if sudo docker network rm "\$name" >/dev/null 2>&1; then gone=1; break; fi
      sudo docker network inspect "\$name" >/dev/null 2>&1 || { gone=1; break; }
      # stale endpoints from killed containers block network rm on this old
      # docker; force-disconnect whatever the network still references.
      for ep in \$(sudo docker network inspect -f "{{range .Containers}}{{.Name}} {{end}}" "\$name" 2>/dev/null); do
        sudo docker network disconnect -f "\$name" "\$ep" >/dev/null 2>&1 || true
      done
      sleep 2
    done
    if [ "\$gone" = 1 ]; then
      sed -i "\|^net:\$name\$|d" "\$OWNED"
    else
      echo "[cleanup] network \$name still exists after retries; KEEPING" >> "\$CLEANLOG"
      kept=1
    fi
  done < "\$OWNED"
  if [ "\$kept" = 0 ]; then
    rm -f "\$OWNED" "\$CLEANLOG" 2>/dev/null
    echo TEARDOWN-OK >&2
  else
    echo "TEARDOWN-PARTIAL (ownership file kept: \$OWNED)" >&2
    exit 90   # propagate "resources kept" from the trap
  fi
}
trap cleanup EXIT

# --- preflight: fail-closed on ANY name/network collision; never pre-delete ---
for n in "\$PG" "\$RD" "\$AP"; do
  st="\$(container_state "\$n")"
  [ "\$st" = absent ] || { echo "COLLISION: container \$n is \$st; refusing to touch it" >&2; exit 81; }
done
if sudo docker network inspect "\$NET" >/dev/null 2>&1; then
  echo "COLLISION: network \$NET exists; refusing to reuse it" >&2; exit 81
fi
echo "/tmp/wkr-\$RS.dump" >> "\$OWNED" | true
sed -i "s|^/tmp/wkr-\$RS.dump\$|file:/tmp/wkr-\$RS.dump|" "\$OWNED"

echo "net:\$NET" >> "\$OWNED"   # pre-register: no window between create and ownership
# TOCTOU injection: a "foreign" racer wins the name after preflight (no label).
[ "\$FAULT" = racenet ] && sudo docker network create "\$NET" >/dev/null 2>&1
sudo docker network create --label wkrestore-owner="\$RS" "\$NET" >/dev/null \
  || { echo "network create failed" >&2; exit 82; }
[ "\$FAULT" = killnet ] && kill -9 \$\$

ECR=$ECR_REPO
echo "container:\$PG" >> "\$OWNED"   # pre-register
# TOCTOU injection: foreign same-named container appears after preflight.
[ "\$FAULT" = racepg ] && sudo docker run -d --name "\$PG" docker.io/library/redis:7.0-alpine sleep 600 >/dev/null 2>&1
sudo docker run -d --name "\$PG" --network "\$NET" --label wkrestore-owner="\$RS" \\
  -e POSTGRES_USER='$WK_DB_USER' -e POSTGRES_PASSWORD='$WK_DB_PASSWORD' -e POSTGRES_DB=inittmp \\
  -e PGDATA=/var/lib/postgresql/data/pgdata "\$ECR":base-paradedb-v0.22.6-pg17 >/dev/null || exit 83
[ "\$FAULT" = killpg ] && kill -9 \$\$
for i in \$(seq 1 40); do sudo docker exec "\$PG" pg_isready -U '$WK_DB_USER' -d inittmp >/dev/null 2>&1 && break; sleep 3; done
sleep 5
sudo docker exec "\$PG" dropdb --if-exists -U '$WK_DB_USER' '$WK_DB_NAME' >/dev/null 2>&1 || true
sudo docker exec "\$PG" createdb -T template0 -U '$WK_DB_USER' '$WK_DB_NAME' || exit 84
sudo docker cp "/tmp/wkr-\$RS.dump" "\$PG":/tmp/r.dump >/dev/null
sudo docker exec "\$PG" pg_restore -U '$WK_DB_USER' -d '$WK_DB_NAME' --no-owner --no-privileges --role='$WK_DB_USER' --exit-on-error /tmp/r.dump >/dev/null || exit 84
echo RESTORE-OK
[ "\$FAULT" = restore ] && exit 85

echo "container:\$RD" >> "\$OWNED"   # pre-register
sudo docker run -d --name "\$RD" --network "\$NET" --label wkrestore-owner="\$RS" -e REDIS_PASSWORD='$WK_REDIS_PASSWORD' \\
  docker.io/library/redis:7.0-alpine sh -c "exec redis-server --appendonly yes --requirepass \\"\\\$REDIS_PASSWORD\\"" >/dev/null || exit 86
[ "\$FAULT" = killredis ] && kill -9 \$\$

[ "\$FAULT" = hang ] && { echo HANGING-FOR-TIMEOUT-TEST; sleep 600; }

echo "container:\$AP" >> "\$OWNED"   # pre-register
sudo docker run -d --name "\$AP" --network "\$NET" --label wkrestore-owner="\$RS" -p 18081:8080 \\
  -e DB_DRIVER=postgres -e DB_HOST="\$PG" -e DB_PORT=5432 \\
  -e DB_USER='$WK_DB_USER' -e DB_PASSWORD='$WK_DB_PASSWORD' -e DB_NAME='$WK_DB_NAME' -e DB_SSLMODE=disable \\
  -e RETRIEVE_DRIVER=postgres -e REDIS_ADDR="\$RD":6379 -e REDIS_PASSWORD='$WK_REDIS_PASSWORD' \\
  -e STORAGE_TYPE=s3 -e S3_REGION=$REGION \\
  -e S3_BUCKET_NAME=supportportal-weknora-docs-${ACCOUNT_ID}-${REGION} -e S3_USE_SSL=true \\
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \\
  -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN -e AWS_REGION=$REGION \\
  -e JWT_SECRET='$WK_JWT_SECRET' -e SYSTEM_AES_KEY='$WK_AES_KEY' -e SYSTEM_SIGNING_KEY='$WK_SIGNING_KEY' \\
  -e DISABLE_REGISTRATION=true -e GIN_MODE=release -e LOG_LEVEL=info -e AUTO_MIGRATE=false \\
  -e FRONTEND_BASE_URL=http://localhost:18081 \\
  "\$ECR":$APP_IMAGE_TAG >/dev/null || exit 87
[ "\$FAULT" = killapp ] && kill -9 \$\$

for i in \$(seq 1 60); do
  curl -sS -o /dev/null --max-time 3 http://localhost:18081/health 2>/dev/null && { echo APP-HEALTHY; break; }
  sleep 4
done
[ "\$FAULT" = appstart ] && exit 88
B=http://localhost:18081/api/v1
if [ "\$FAULT" = login ]; then
  TOKEN=""
else
  TOKEN=\$(curl -sS -X POST -H "Content-Type: application/json" \\
    -d '{"email":"$WK_ADMIN_EMAIL","password":"$WK_ADMIN_PASSWORD"}' \$B/auth/login \\
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])') || exit 89
fi
[ -n "\$TOKEN" ] || { echo "login failed" >&2; exit 89; }
KB=\$(curl -sS -H "Authorization: Bearer \$TOKEN" \$B/knowledge-bases \\
  | python3 -c 'import sys,json;d=json.load(sys.stdin);items=(d.get("data") or d.get("list") or []);items=items.get("list",items) if isinstance(items,dict) else items;print(items[0]["id"])') || exit 89
echo KB=\$KB
curl -sS -X POST -H "Content-Type: application/json" -H "Authorization: Bearer \$TOKEN" \\
  -d '{"query_text":"灌溉故障码","top_k":5}' \$B/knowledge-bases/\$KB/hybrid-search > /tmp/wkr_search.json
[ "\$FAULT" = search ] && exit 91
FACT='$EXPECT_FACT' python3 - <<'PYV'
import json, os, sys
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
sys.exit(0 if found[0] else 3)
PYV
[ \$? -eq 0 ] || exit 91
curl -sS -H "Authorization: Bearer \$TOKEN" \$B/knowledge/$KNOWLEDGE_ID/download \\
  -o /tmp/wkr_dl.bin -w "DL-HTTP %{http_code}\n"
[ "\$FAULT" = download ] && exit 92
echo "DL-MD5 \$(md5sum /tmp/wkr_dl.bin | awk '{print \$1}') EXPECT $WK_REF_MD5"
rm -f /tmp/wkr_search.json /tmp/wkr_dl.bin
exit 0
EOF
}

# rescue <rs> — replay the remote ownership file (removes ONLY recorded
# resources). Safe on success runs (ownership file already emptied/removed)
# and after SIGKILL-style interruptions (file persists with markers).
# The dump is removed deterministically FIRST: its path is unique to this
# run, so it is unambiguously ours even when the remote script never started
# (SSM failure between staging and launch — no ownership file exists then).
rescue() {
  local rs="$1"
  local rscript; rscript="$(mktemp)"
  cat > "$rscript" <<EOF
RS=$rs
OWNED=/tmp/wkr-owned-\$RS
CLEANLOG=/tmp/wkr-clean-\$RS.log
DUMP=/tmp/wkr-\$RS.dump
rm -f "\$DUMP"   # deterministic: unique per-run path, always ours
[ -f "\$OWNED" ] || { echo "rescue: nothing owned (dump removed)"; echo "rescue-done kept=0"; exit 0; }
container_state() {
  local names
  if names="\$(sudo docker ps -a --format "{{.Names}}" 2>/dev/null)"; then
    if printf "%s\n" "\$names" | grep -qx "\$1"; then echo present; else echo absent; fi
  else
    echo unknown
  fi
}
owner_of_container() {
  sudo docker inspect -f "{{index .Config.Labels \"wkrestore-owner\"}}" "\$1" 2>/dev/null
}
owner_of_network() {
  sudo docker inspect -f "{{index .Labels \"wkrestore-owner\"}}" "\$1" 2>/dev/null
}
kept=0
# containers + files first; label proof before any delete
while IFS= read -r line; do
  [ -n "\$line" ] || continue
  kind="\${line%%:*}"; name="\${line#*:}"
  case "\$kind" in
    container)
      st="\$(container_state "\$name")"
      if [ "\$st" != present ]; then
        if [ "\$st" = absent ]; then sed -i "\|^container:\$name\$|d" "\$OWNED"
        else echo "rescue: container \$name state \$st; KEEPING" >> "\$CLEANLOG"; kept=1; fi
      else
        lbl="\$(owner_of_container "\$name")"
        if [ "\$lbl" != "\$RS" ]; then
          echo "rescue: container \$name owner label mismatch; FOREIGN — not touching" >> "\$CLEANLOG"
          sed -i "\|^container:\$name\$|d" "\$OWNED"
        else
          sudo docker rm -f "\$name" >/dev/null 2>&1 || true
          st="\$(container_state "\$name")"
          if [ "\$st" = absent ]; then sed -i "\|^container:\$name\$|d" "\$OWNED"
          else echo "rescue: container \$name is \$st after removal; KEEPING" >> "\$CLEANLOG"; kept=1; fi
        fi
      fi ;;
    file)
      rm -f "\$name"; sed -i "\|^file:\$name\$|d" "\$OWNED" ;;
  esac
done < "\$OWNED"
# networks with detach retries; label proof before any delete
while IFS= read -r line; do
  [ -n "\$line" ] || continue
  kind="\${line%%:*}"; name="\${line#*:}"
  [ "\$kind" = net ] || continue
  if ! sudo docker network inspect "\$name" >/dev/null 2>&1; then
    sed -i "\|^net:\$name\$|d" "\$OWNED"; continue
  fi
  lbl="\$(owner_of_network "\$name")"
  if [ "\$lbl" != "\$RS" ]; then
    echo "rescue: network \$name owner label mismatch; FOREIGN — not touching" >> "\$CLEANLOG"
    sed -i "\|^net:\$name\$|d" "\$OWNED"; continue
  fi
  gone=0
  for _ in \$(seq 1 15); do
    sudo docker network rm "\$name" >/dev/null 2>&1 && { gone=1; break; }
    sudo docker network inspect "\$name" >/dev/null 2>&1 || { gone=1; break; }
    for ep in \$(sudo docker network inspect -f "{{range .Containers}}{{.Name}} {{end}}" "\$name" 2>/dev/null); do
      sudo docker network disconnect -f "\$name" "\$ep" >/dev/null 2>&1 || true
    done
    sleep 2
  done
  if [ "\$gone" = 1 ]; then sed -i "\|^net:\$name\$|d" "\$OWNED"
  else echo "rescue: network \$name kept" >> "\$CLEANLOG"; kept=1; fi
done < "\$OWNED"
[ "\$kept" = 0 ] && rm -f "\$OWNED" "\$CLEANLOG" 2>/dev/null
echo "rescue-done kept=\$kept"
EOF
  local tmo=240
  ssm_run "wkrestore rescue $rs" "$tmo" "$rscript" || return 1
  [ "$REMOTE_STATUS" = "Success" ] || return 1
  echo "$REMOTE_OUT" | grep -q "rescue-done"
}

# rescue_assert <rs> — rescue + full clean-check in ONE remote command.
rescue_assert() {
  local rs="$1"
  local s; s="$(mktemp)"
  cat > "$s" <<EOF
RS=$rs
OWNED=/tmp/wkr-owned-\$RS
CLEANLOG=/tmp/wkr-clean-\$RS.log
DUMP=/tmp/wkr-\$RS.dump
rm -f "\$DUMP"
if [ -f "\$OWNED" ]; then
  container_state() {
    local names
    if names="\$(sudo docker ps -a --format "{{.Names}}" 2>/dev/null)"; then
      if printf "%s
" "\$names" | grep -qx "\$1"; then echo present; else echo absent; fi
    else
      echo unknown
    fi
  }
  owner_of_container() { sudo docker inspect -f "{{index .Config.Labels \"wkrestore-owner\"}}" "\$1" 2>/dev/null; }
  owner_of_network() { sudo docker inspect -f "{{index .Labels \"wkrestore-owner\"}}" "\$1" 2>/dev/null; }
  while IFS= read -r line; do
    [ -n "\$line" ] || continue
    kind="\${line%%:*}"; name="\${line#*:}"
    case "\$kind" in
      container)
        st="\$(container_state "\$name")"
        if [ "\$st" != present ]; then
          [ "\$st" = absent ] && sed -i "\|^container:\$name\$|d" "\$OWNED"
        else
          lbl="\$(owner_of_container "\$name")"
          if [ "\$lbl" != "\$RS" ]; then sed -i "\|^container:\$name\$|d" "\$OWNED"
          else
            sudo docker rm -f "\$name" >/dev/null 2>&1 || true
            st="\$(container_state "\$name")"
            [ "\$st" = absent ] && sed -i "\|^container:\$name\$|d" "\$OWNED"
          fi
        fi ;;
      net)
        if ! sudo docker network inspect "\$name" >/dev/null 2>&1; then sed -i "\|^net:\$name\$|d" "\$OWNED"; continue; fi
        lbl="\$(owner_of_network "\$name")"
        if [ "\$lbl" != "\$RS" ]; then sed -i "\|^net:\$name\$|d" "\$OWNED"; continue; fi
        for _ in \$(seq 1 15); do
          sudo docker network rm "\$name" >/dev/null 2>&1 && break
          sudo docker network inspect "\$name" >/dev/null 2>&1 || break
          for ep in \$(sudo docker network inspect -f "{{range .Containers}}{{.Name}} {{end}}" "\$name" 2>/dev/null); do
            sudo docker network disconnect -f "\$name" "\$ep" >/dev/null 2>&1 || true
          done
          sleep 2
        done
        sudo docker network inspect "\$name" >/dev/null 2>&1 || sed -i "\|^net:\$name\$|d" "\$OWNED" ;;
      file) rm -f "\$name"; sed -i "\|^file:\$name\$|d" "\$OWNED" ;;
    esac
  done < "\$OWNED"
  rm -f "\$OWNED" "\$CLEANLOG" 2>/dev/null
fi
miss=0
for n in \$RS-pg \$RS-redis \$RS-app; do
  if sudo docker ps -a --format "{{.Names}}" | grep -qx "\$n"; then echo "LEFT container \$n"; miss=1; fi
done
if sudo docker network ls --format "{{.Name}}" | grep -qx "\$RS-net"; then echo "LEFT network \$RS-net"; miss=1; fi
for f in /tmp/wkr-\$RS.dump /tmp/wkr-owned-\$RS /tmp/wkr-clean-\$RS.log; do
  [ -e "\$f" ] && { echo "LEFT file \$f"; miss=1; }
done
echo "RESCUE-ASSERT miss=\$miss"
EOF
  ssm_run "wkrestore rescue+assert $rs" 240 "$s" || return 1
  [ "$REMOTE_STATUS" = "Success" ] || return 1
  echo "$REMOTE_OUT" | grep -q "RESCUE-ASSERT miss=0"
}

# stage_dump <rs> <presign> — download the backup dump to a per-run path.
stage_dump() {
  local rs="$1" presign="$2"
  local s; s="$(mktemp)"
  printf "%s\n" "curl -sS '$presign' -o /tmp/wkr-$rs.dump && stat -c %s /tmp/wkr-$rs.dump" > "$s"
  ssm_run "wkrestore stage $rs" 300 "$s" || return 1
  [ "$REMOTE_STATUS" = "Success" ] || return 1
  echo "$REMOTE_OUT" | grep -E "^[0-9]+$" >/dev/null
}

# assert_clean <rs> — every per-run resource must be gone.
assert_clean() {
  local rs="$1"
  local s; s="$(mktemp)"
  cat > "$s" <<EOF
RS=$rs
miss=0
for n in \$RS-pg \$RS-redis \$RS-app; do
  if sudo docker ps -a --format '{{.Names}}' | grep -qx "\$n"; then echo "LEFT container \$n"; miss=1; fi
done
if sudo docker network ls --format '{{.Name}}' | grep -qx "\$RS-net"; then echo "LEFT network \$RS-net"; miss=1; fi
for f in /tmp/wkr-\$RS.dump /tmp/wkr-owned-\$RS /tmp/wkr-clean-\$RS.log; do
  [ -e "\$f" ] && { echo "LEFT file \$f"; miss=1; }
done
echo "CLEAN-CHECK miss=\$miss"
EOF
  ssm_run "wkrestore clean-check $rs" 120 "$s" || return 1
  [ "$REMOTE_STATUS" = "Success" ] || return 1
  echo "$REMOTE_OUT" | grep -q "CLEAN-CHECK miss=0"
}

# purge_sc_leftovers — removes ONLY resources named by THIS self-check run's
# base prefix (pattern wksc<$$><epoch>-*), never foreign containers.
purge_sc_leftovers() {
  local base="$1"
  local s; s="$(mktemp)"
  cat > "$s" <<EOF
BASE=$base
sudo docker ps -a --format '{{.Names}}' | grep "^\$BASE" | xargs -r sudo docker rm -f >/dev/null 2>&1
sudo docker network ls --format '{{.Name}}' | grep "^\$BASE" | xargs -r sudo docker network rm >/dev/null 2>&1
rm -f /tmp/wkr-owned-\$BASE* /tmp/wkr-clean-\$BASE* /tmp/wkr-\$BASE* 2>/dev/null
echo PURGED
EOF
  ssm_run "wkrestore sc purge" 120 "$s" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Self-check: fault-injection suite (remote, real instance, small images).
# ---------------------------------------------------------------------------
self_check() {
  common_setup
  local base_rs="wksc$$$(date +%s)"
  local pass=0

  echo "== S1: name collision refuses and preserves the existing container =="
  local rs1="$base_rs-c1"
  local s; s="$(mktemp)"
  printf "%s\n" "sudo docker run -d --name $rs1-pg docker.io/library/redis:7.0-alpine sleep 300 >/dev/null && echo DUMMY-UP" > "$s"
  ssm_run "wkrestore sc pre-create" 120 "$s" || bad "S1 pre-create send failed"
  echo "$REMOTE_OUT" | grep -q DUMMY-UP || bad "S1 dummy did not start"
  build_remote_script "$s.mk" "$rs1"
  ssm_run "wkrestore sc collision" 300 "$s.mk"
  if [ "$REMOTE_STATUS" != "Success" ] && echo "$REMOTE_ERR" | grep -q "COLLISION"; then
    ok "S1: collision refused with explicit error"
  else
    bad "S1: expected collision refusal, got $REMOTE_STATUS (stderr: $(echo "$REMOTE_ERR" | head -2))"
  fi
  printf "%s\n" "sudo docker ps -a --format '{{.Names}}' | grep -qx '$rs1-pg' && echo DUMMY-ALIVE; sudo docker rm -f $rs1-pg >/dev/null 2>&1; echo DUMMY-REMOVED" > "$s"
  ssm_run "wkrestore sc dummy-check" 120 "$s"
  echo "$REMOTE_OUT" | grep -q DUMMY-ALIVE && ok "S1: pre-existing container survived untouched" || bad "S1: pre-existing container vanished"

  echo "== S2-S6: failure paths leave nothing behind =="
  local f
  for f in restore appstart login search download; do
    local rs="$base_rs-$f"
    local sc; sc="$(mktemp)"
    build_remote_script "$sc" "$rs" "$f" "$(presign_for "$S3_KEY")"
    ssm_run "wkrestore sc fault-$f" 900 "$sc"
    if [ "$REMOTE_STATUS" = "Success" ]; then
      bad "fault-$f: unexpectedly succeeded"
    else
      ok "fault-$f: run failed as designed ($REMOTE_STATUS)"
    fi
    rescue_assert "$rs" && ok "fault-$f: all resources cleaned" || bad "fault-$f: resources left behind"
  done

  echo "== S7: SSM timeout (SIGKILL path) → rescue via ownership file =="
  local rs7="$base_rs-hang"
  local sc7; sc7="$(mktemp)"
  build_remote_script "$sc7" "$rs7" hang "$(presign_for "$S3_KEY")"
  # Short SSM timeout: the remote shell is killed mid-hang (trap skipped);
  # ownership file must still enable the outer rescue.
  local b64 params
  b64="$(base64 -i "$sc7")"; params="$(mktemp).json"
  python3 - "$b64" "$INSTANCE_ID" "$rs7" > "$params" <<'PYEOF'
import json, sys
b64, inst, rs = sys.argv[1:4]
cmd = f"echo {b64} | base64 -d > /tmp/wkr_inner.sh && bash /tmp/wkr_inner.sh; RC=$?; rm -f /tmp/wkr_inner.sh; exit $RC"
print(json.dumps({"InstanceIds": [inst], "DocumentName": "AWS-RunShellScript",
                  "Comment": f"wkrestore sc hang {rs}", "TimeoutSeconds": 45,
                  "Parameters": {"commands": [cmd]}}))
PYEOF
  local cid
  cid="$("$AWS_CLI_BIN" ssm send-command --cli-input-json "file://$params" --query "Command.CommandId" --output text)" || bad "S7: send failed"
  local st="InProgress"
  for _ in $(seq 1 12); do
    sleep 10
    st="$("$AWS_CLI_BIN" ssm get-command-invocation --command-id "$cid" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null)"
    case "$st" in Success|Failed|TimedOut|Cancelled) break ;; esac
  done
  [ "$st" = "TimedOut" ] || [ "$st" = "Cancelled" ] || [ "$st" = "Failed" ] \
    && ok "S7: SSM command ended in $st (killed mid-run)" || bad "S7: unexpected hang status $st"
  rescue_assert "$rs7" && ok "S7: rescue + clean after kill" || bad "S7: resources left behind"

  echo "== S8: dump staged, remote NEVER started → rescue must still remove it =="
  local rs8="$base_rs-dumpwin"
  stage_dump "$rs8" "$(presign_for "$S3_KEY")" >/dev/null 2>&1 || bad "S8: dump staging failed"
  # Simulate an operator/SSM abort between staging and launch: no remote
  # script ever runs, so no ownership file exists. The rescue's deterministic
  # dump removal must still clean the per-run path.
  rescue_assert "$rs8" && ok "S8: rescue ran; dump removed, nothing else left" || bad "S8: leftovers after dump-boundary abort"

  echo "== S9-S12: kill -9 at each create boundary (marker-first design) =="
  local kf
  for kf in killnet killpg killredis killapp; do
    local rs="$base_rs-$kf"
    local sck; sck="$(mktemp)"
    build_remote_script "$sck" "$rs" "$kf" "$(presign_for "$S3_KEY")"
    ssm_run "wkrestore sc $kf" 900 "$sck"
    # kill -9 makes bash exit 137; SSM reports Failed. The trap is skipped,
    # so ONLY the outer rescue (ownership file, markers written pre-create)
    # can clean the already-created resource.
    if [ "$REMOTE_STATUS" = "Success" ]; then
      bad "$kf: unexpectedly succeeded"
    else
      ok "$kf: killed at boundary ($REMOTE_STATUS)"
    fi
    rescue_assert "$rs" && ok "$kf: rescue cleaned all resources" || bad "$kf: resources left behind ($(echo "$REMOTE_OUT" | grep -E "LEFT|miss=" | tr '\n' ' '))"
  done

  echo "== S13-S14: TOCTOU race — foreign same-named resource wins AFTER preflight =="
  # race_foreign_check <rs> <kind:net|container> <name> <expect-foreign-survives>
  # Returns 0 iff: the FOREIGN resource still exists, all other per-run names
  # are gone, and dump/ownership files are gone.
  race_foreign_check() {
    local rs="$1" kind="$2" fname="$3"
    local s; s="$(mktemp)"
    cat > "$s" <<EOF
RS=$rs
miss=0
# every per-run name EXCEPT the foreign-occupied one must be gone
for n in \$RS-pg \$RS-redis \$RS-app; do
  [ "\$n" = "$fname" ] && continue
  if sudo docker ps -a --format "{{.Names}}" | grep -qx "\$n"; then echo "LEFT container \$n"; miss=1; fi
done
if [ "$kind" != net ] && sudo docker network ls --format "{{.Name}}" | grep -qx "\$RS-net"; then echo "LEFT network \$RS-net"; miss=1; fi
# the foreign resource must SURVIVE
if [ "$kind" = net ]; then
  sudo docker network ls --format "{{.Name}}" | grep -qx "$fname" || { echo "FOREIGN-GONE $fname"; miss=1; }
else
  sudo docker ps -a --format "{{.Names}}" | grep -qx "$fname" || { echo "FOREIGN-GONE $fname"; miss=1; }
fi
for f in /tmp/wkr-\$RS.dump /tmp/wkr-owned-\$RS /tmp/wkr-clean-\$RS.log; do
  [ -e "\$f" ] && { echo "LEFT file \$f"; miss=1; }
done
echo "RACE-CHECK miss=\$miss"
EOF
    ssm_run "wkrestore race-check $rs" 120 "$s" || return 1
    [ "$REMOTE_STATUS" = "Success" ] || return 1
    echo "$REMOTE_OUT" | grep -q "RACE-CHECK miss=0"
  }
  remove_named() { # <kind:net|container> <name>
    local kind="$1" fname="$2"
    local s; s="$(mktemp)"
    printf "%s\n" "if [ '$kind' = net ]; then sudo docker network rm '$fname' >/dev/null 2>&1; else sudo docker rm -f '$fname' >/dev/null 2>&1; fi; echo REMOVED" > "$s"
    ssm_run "wkrestore race-decoy-remove" 120 "$s" >/dev/null 2>&1 || true
  }
  local rf
  for rf in racenet racepg; do
    local rs="$base_rs-$rf"
    local scr; scr="$(mktemp)"
    build_remote_script "$scr" "$rs" "$rf" "$(presign_for "$S3_KEY")"
    ssm_run "wkrestore sc $rf" 900 "$scr"
    if [ "$REMOTE_STATUS" = "Success" ]; then
      bad "$rf: unexpectedly succeeded (create should have lost the race)"
    else
      ok "$rf: run failed as designed ($REMOTE_STATUS)"
    fi
    local fname kind
    if [ "$rf" = racenet ]; then kind=net; fname="$rs-net"; else kind=container; fname="$rs-pg"; fi
    # ONE command: verify foreign survived + ours cleaned, then remove decoy,
    # then full assert.
    local rc2; rc2="$(mktemp)"
    cat > "$rc2" <<EOF
RS=$rs
KIND=$kind
FNAME=$fname
miss=0
for n in \$RS-pg \$RS-redis \$RS-app; do
  [ "\$n" = "\$FNAME" ] && continue
  if sudo docker ps -a --format "{{.Names}}" | grep -qx "\$n"; then echo "LEFT container \$n"; miss=1; fi
done
if [ "\$KIND" != net ] && sudo docker network ls --format "{{.Name}}" | grep -qx "\$RS-net"; then echo "LEFT network \$RS-net"; miss=1; fi
if [ "\$KIND" = net ]; then
  sudo docker network ls --format "{{.Name}}" | grep -qx "\$FNAME" || { echo "FOREIGN-GONE \$FNAME"; miss=1; }
else
  sudo docker ps -a --format "{{.Names}}" | grep -qx "\$FNAME" || { echo "FOREIGN-GONE \$FNAME"; miss=1; }
fi
for f in /tmp/wkr-\$RS.dump /tmp/wkr-owned-\$RS /tmp/wkr-clean-\$RS.log; do
  [ -e "\$f" ] && { echo "LEFT file \$f"; miss=1; }
done
echo "RACE-CHECK miss=\$miss"
if [ "\$miss" = 0 ]; then
  if [ "\$KIND" = net ]; then sudo docker network rm "\$FNAME" >/dev/null 2>&1; else sudo docker rm -f "\$FNAME" >/dev/null 2>&1; fi
  echo "DECOY-REMOVED"
fi
for n in \$RS-pg \$RS-redis \$RS-app; do
  if sudo docker ps -a --format "{{.Names}}" | grep -qx "\$n"; then echo "LEFT2 container \$n"; miss=1; fi
done
if sudo docker network ls --format "{{.Name}}" | grep -qx "\$RS-net"; then echo "LEFT2 network \$RS-net"; miss=1; fi
echo "POST-CLEAN miss=\$miss"
EOF
    ssm_run "wkrestore race-verify $rf" 240 "$rc2"
    if [ "$REMOTE_STATUS" = "Success" ] \
       && echo "$REMOTE_OUT" | grep -q "RACE-CHECK miss=0" \
       && echo "$REMOTE_OUT" | grep -q "DECOY-REMOVED" \
       && echo "$REMOTE_OUT" | grep -q "POST-CLEAN miss=0"; then
      ok "$rf: foreign survived; ours + dump + records cleaned; decoy then removed; full clean"
    else
      bad "$rf: race contract violated ($(echo "$REMOTE_OUT" | grep -E 'miss=|FOREIGN-GONE|LEFT' | tr '
' ' '))"
    fi
  done

  echo
  purge_sc_leftovers "$base_rs"
  if [ "$FAILURES" -eq 0 ]; then
    echo "RESTORE-VERIFY SELF-CHECK PASSED"
    exit 0
  fi
  echo "FAILED CHECKS: $FAILURES" >&2
  exit 1
}

presign_for() {  # <key> -> presigned GET URL (stdout)
  python3 - "$BACKUP_BUCKET" "$1" "$REGION" <<'PYEOF'
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
cq = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in sorted(p.items()))
cr = "\n".join(["GET", uri, cq, f"host:{host}", "", "host", "UNSIGNED-PAYLOAD"])
st = "\n".join(["AWS4-HMAC-SHA256", ad, f"{ds}/{region}/s3/aws4_request",
                hashlib.sha256(cr.encode()).hexdigest()])
def h(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
sk = h(h(h(h(("AWS4" + secret).encode(), ds), region), "s3"), "aws4_request")
sig = hmac.new(sk, st.encode(), hashlib.sha256).hexdigest()
print(f"https://{host}{uri}?{cq}&X-Amz-Signature={sig}")
PYEOF
}

# ---------------------------------------------------------------------------
# Real mode.
# ---------------------------------------------------------------------------
real_run() {
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

  common_setup
  WK_REF_MD5="$(md5 -q "$REFERENCE_FILE")"
  export WK_REF_MD5

  RS="wkr$$$(date +%s)"
  RESCUED=0
  outer_rescue() {
    # Runs on outer EXIT. On a successful remote run the ownership file is
    # already empty (no-op). Otherwise replay it: removes ONLY this run's
    # recorded resources even when the remote shell was killed.
    [ "$RESCUED" = 0 ] && rescue "$RS" >/dev/null 2>&1
    RESCUED=1
  }
  trap outer_rescue EXIT

  log "staging dump ($RS)"
  stage_dump "$RS" "$(presign_for "$S3_KEY")" || fail "dump staging failed"

  log "running verification"
  REMOTE_BUILT="$(mktemp)"
  build_remote_script "$REMOTE_BUILT" "$RS"
  ssm_run "wkrestore app-level verify $RS" 1500 "$REMOTE_BUILT" || fail "verify send failed"
  echo "$REMOTE_OUT"
  [ -n "$REMOTE_ERR" ] && echo "[stderr] $REMOTE_ERR" >&2
  if [ "$REMOTE_STATUS" != "Success" ]; then
    outer_rescue
    assert_clean "$RS" || fail "rescue left resources behind"
    fail "remote verification ended in $REMOTE_STATUS"
  fi

  echo "$REMOTE_OUT" | grep -q "RESTORE-OK" || fail "restore step missing"
  echo "$REMOTE_OUT" | grep -q "APP-HEALTHY" || fail "app never became healthy"
  echo "$REMOTE_OUT" | grep -q "APP-SEARCH: PASS" || fail "app-level search failed"
  echo "$REMOTE_OUT" | grep -q "DL-HTTP 200" || fail "download non-200"
  DL_MD5="$(echo "$REMOTE_OUT" | grep -o 'DL-MD5 [0-9a-f]*' | awk '{print $2}')"
  [ "$DL_MD5" = "$WK_REF_MD5" ] || fail "downloaded md5 ($DL_MD5) != reference ($WK_REF_MD5)"
  assert_clean "$RS" || fail "post-success clean check failed"

  RESCUED=1   # success path already cleaned; make EXIT trap a no-op
  jq -n --arg key "$S3_KEY" --arg kb "$(echo "$REMOTE_OUT" | grep -o 'KB=[0-9a-f-]*' | cut -d= -f2)" \
    --arg fact "$EXPECT_FACT" --arg md5 "$WK_REF_MD5" \
    '{backupKey:$key, appLevelSearch:"PASS", appLevelFileReadBack:"PASS", fileMD5:$md5, fact:$fact}'
}

if [ "$MODE" = "self-check" ]; then
  while [[ $# -ge 1 ]]; do
    case "$1" in
      --instance-id) INSTANCE_ID="$2"; shift 2 ;;
      --key) S3_KEY="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  self_check
else
  real_run "$@"
fi
