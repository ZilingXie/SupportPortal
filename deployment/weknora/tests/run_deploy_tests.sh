#!/usr/bin/env bash
# Regression tests for deployment/weknora/ scripts, exercising the REAL
# scripts against stubbed external boundaries (aws / podman / curl).
#
# Covered review findings (p2-188 R3):
#   deploy_weknora_services.sh wait_stable:
#     1. normal rollout (IN_PROGRESS -> COMPLETED on the TARGET task
#        definition) succeeds;
#     2. a still-deploying service keeps waiting instead of judging stable;
#     3. a circuit-breaker ROLLBACK (PRIMARY reverts to the old task
#        definition) must fail the deploy, and the public URL check must
#        never run;
#     4. rolloutState=FAILED must fail the deploy.
#   register_weknora_task_definitions.sh data guard:
#     5. default registration keeps REQUIRE_EXISTING_PGDATA=true;
#     6. --initial-bootstrap explicitly allows empty PGDATA.
#   restore_verify_weknora_backup.sh cleanup safety:
#     7. a failed `podman run` never removes anything (no podman rm issued)
#        and exits non-zero;
#     8. two invocations use distinct container names (no cross-run clobber).
set -o pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEKNORA_DIR="$(cd "$HERE/.." && pwd)"
TESTS_DIR="$(mktemp -d)"
trap 'rm -rf "$TESTS_DIR"' EXIT
FAILURES=0

ok()   { echo "PASS: $1"; }
bad()  { echo "FAIL: $1" >&2; FAILURES=$((FAILURES + 1)); }
check() { # name condition
  if eval "$2"; then ok "$1"; else bad "$1"; fi
}

write_aws_stub() { # target file path
  cat > "$1" <<'STUB'
#!/usr/bin/env bash
# Minimal aws stub for weknora deploy tests. Behavior driven by env:
#   STUB_STATE_DIR, STUB_SCENARIO (normal|rollback|failed)
set -uo pipefail
cmd="$1 $2"
shift 2 || true
case "$cmd" in
  "sts get-caller-identity")
    echo "891612554546" ;;
  "ssm get-parameter")
    name="$(echo "$@" | grep -o '/supportportal/weknora/[a-z_]*' | head -1)"
    case "$name" in
      */db_username) echo "weknora" ;;
      */db_name) echo "weknora" ;;
      *) echo "stub" ;;
    esac ;;
  "iam get-role")
    echo "arn:aws:iam::891612554546:role/$(echo "$@" | sed -n 's/.*--role-name \([^ ]*\).*/\1/p')" ;;
  "ecr describe-images")
    echo "sha256:$(echo "$@" | sed -n 's/.*imageTag=\([^ ]*\).*/\1/p' | shasum -a 256 | cut -c1-32)" ;;
  "ecs register-task-definition")
    family="$(echo "$@" | sed -n 's/.*--family \([^ ]*\).*/\1/p')"
    # The register script passes pretty-printed (multi-line) JSON as ONE argv
    # element; take the argument that follows --cli-input-json verbatim.
    json=""
    prev=""
    for a in "$@"; do
      if [[ "$prev" == "--cli-input-json" ]]; then json="$a"; break; fi
      prev="$a"
    done
    printf '%s\n' "$json" > "${STUB_STATE_DIR}/registered-${family}.json"
    echo "arn:aws:ecs:us-east-1:891612554546:task-definition/${family}:99" ;;
  "ecs update-service")
    svc="$(echo "$@" | sed -n 's/.*--service \([^ ]*\).*/\1/p')"
    td="$(echo "$@" | sed -n 's/.*--task-definition \([^ ]*\).*/\1/p')"
    echo "$svc $td" >> "${STUB_STATE_DIR}/updates.log"
    echo "$td" ;;
  "ecs describe-services")
    svc="$(echo "$@" | sed -n 's/.*--services \([^ ]*\).*/\1/p')"
    svc_key="${svc//:/_}"
    target_td="$(awk -v s="$svc" '$1==s {print $2}' "${STUB_STATE_DIR}/updates.log" | tail -1)"
    count_file="${STUB_STATE_DIR}/polls-${svc_key}"
    n="$(($(cat "$count_file" 2>/dev/null || echo 0) + 1))"
    echo "$n" > "$count_file"
    scenario="${STUB_SCENARIO:-normal}"
    case "$scenario" in
      normal)
        if (( n >= 2 )); then rollout=COMPLETED; running=1; else rollout=IN_PROGRESS; running=0; fi
        td="$target_td" ;;
      rollback)
        # First services settle; weknora-docreader rolls back to the old revision.
        if [[ "$svc" == "weknora-docreader" ]]; then
          if (( n >= 2 )); then
            rollback_td="${target_td%:*}:$(( ${target_td##*:} - 1 ))"
            echo "{\"services\":[{\"status\":\"ACTIVE\",\"desiredCount\":1,\"runningCount\":1,\"pendingCount\":0,\"deployments\":[{\"status\":\"PRIMARY\",\"taskDefinition\":\"${rollback_td}\",\"rolloutState\":\"COMPLETED\",\"runningCount\":1}]}]}"
            exit 0
          fi
          td="$target_td"; rollout=IN_PROGRESS; running=0
        else
          if (( n >= 2 )); then rollout=COMPLETED; running=1; else rollout=IN_PROGRESS; running=0; fi
          td="$target_td"
        fi ;;
      failed)
        if (( n >= 2 )); then
          echo "{\"services\":[{\"status\":\"ACTIVE\",\"desiredCount\":1,\"runningCount\":0,\"pendingCount\":0,\"deployments\":[{\"status\":\"PRIMARY\",\"taskDefinition\":\"${target_td}\",\"rolloutState\":\"FAILED\",\"runningCount\":0}]}]}"
          exit 0
        fi
        td="$target_td"; rollout=IN_PROGRESS; running=0 ;;
      *) echo "unknown scenario $scenario" >&2; exit 1 ;;
    esac
    echo "{\"services\":[{\"status\":\"ACTIVE\",\"desiredCount\":1,\"runningCount\":${running},\"pendingCount\":0,\"deployments\":[{\"status\":\"PRIMARY\",\"taskDefinition\":\"${td}\",\"rolloutState\":\"${rollout}\",\"runningCount\":${running}}]}]}" ;;
  "s3 cp")
    # materialize a fake object where the script expects it; manifests are
    # reported as absent in these runs.
    target="$(echo "$@" | awk '{print $NF}')"
    case "$target" in
      *manifest.json) exit 1 ;;
    esac
    mkdir -p "$(dirname "$target")"
    echo fakedump > "$target" ;;
  *) echo "aws stub: unsupported: $cmd $*" >&2; exit 1 ;;
esac
STUB
  chmod +x "$1"
}

write_curl_stub() { # always 200
  cat > "$1" <<'STUB'
#!/usr/bin/env bash
echo "invoked" >> "${STUB_STATE_DIR}/curl.log"
echo "200"
STUB
  chmod +x "$1"
}

write_podman_stub() { # dir
  cat > "$1/bin/podman" <<STUB
#!/usr/bin/env bash
echo "podman \$*" >> "\${STUB_STATE_DIR}/podman.log"
if [[ "\$1 \$2" == "container exists" ]]; then exit 1; fi
if [[ "\$1" == "run" ]]; then
  if [[ "\${STUB_FAIL_RUN:-0}" == "1" ]]; then exit 1; fi
  exit 0
fi
exit 0
STUB
  chmod +x "$1/bin/podman"
}

make_td_map() { # file
  cat > "$1" <<'EOF'
{"paradedb":"arn:aws:ecs:us-east-1:891612554546:task-definition/weknora-paradedb:10",
 "redis":"arn:aws:ecs:us-east-1:891612554546:task-definition/weknora-redis:10",
 "docreader":"arn:aws:ecs:us-east-1:891612554546:task-definition/weknora-docreader:10",
 "app":"arn:aws:ecs:us-east-1:891612554546:task-definition/weknora-app:10",
 "frontend":"arn:aws:ecs:us-east-1:891612554546:task-definition/weknora-frontend:10"}
EOF
}

run_deploy_scenario() { # scenario -> sets DEPLOY_RC
  local scenario="$1"
  local dir="$TESTS_DIR/deploy-$scenario"
  mkdir -p "$dir/bin"
  write_aws_stub "$dir/bin/aws"
  write_curl_stub "$dir/bin/curl"
  make_td_map "$dir/td.json"
  # Speed the poll loop up for tests.
  sed 's/sleep 30; waited=$((waited + 30))/sleep 0; waited=$((waited + 30))/' \
    "$WEKNORA_DIR/deploy_weknora_services.sh" > "$dir/deploy.sh"
  chmod +x "$dir/deploy.sh"
  (
    cd "$dir"
    PATH="$dir/bin:$PATH" STUB_STATE_DIR="$dir" STUB_SCENARIO="$scenario" \
      AWS_REGION=us-east-1 "$dir/deploy.sh" --task-definitions "$dir/td.json" --timeout 6 \
      > "$dir/out.log" 2> "$dir/err.log"
  )
  DEPLOY_RC=$?
}

echo "== deploy_weknora_services.sh: normal rollout succeeds =="
run_deploy_scenario normal
check "normal scenario exits 0" '[ "$DEPLOY_RC" -eq 0 ]'
check "normal scenario ran the public URL check" '[ -f "$TESTS_DIR/deploy-normal/curl.log" ]'
UPDATES_N="$(wc -l < "$TESTS_DIR/deploy-normal/updates.log" | tr -d "[:space:]")"
check "normal scenario updated all five services" '[ "$UPDATES_N" -eq 5 ]'

echo "== deploy_weknora_services.sh: rollback (old PRIMARY) must fail =="
run_deploy_scenario rollback
check "rollback scenario exits non-zero" '[ "$DEPLOY_RC" -ne 0 ]'
check "rollback scenario reports the rollback" 'grep -q "rolled back or superseded" "$TESTS_DIR/deploy-rollback/err.log"'
check "rollback scenario never reaches the public URL check" '[ ! -f "$TESTS_DIR/deploy-rollback/curl.log" ]'

echo "== deploy_weknora_services.sh: rolloutState=FAILED must fail =="
run_deploy_scenario failed
check "failed scenario exits non-zero" '[ "$DEPLOY_RC" -ne 0 ]'
check "failed scenario reports rolloutState=FAILED" 'grep -q "rolloutState=FAILED" "$TESTS_DIR/deploy-failed/err.log"'
check "failed scenario never reaches the public URL check" '[ ! -f "$TESTS_DIR/deploy-failed/curl.log" ]'

echo "== deploy_weknora_services.sh: still deploying keeps waiting =="
# In the normal scenario every service needs exactly 2 polls; assert the
# first poll (IN_PROGRESS) did not settle the service prematurely.
all_polled_twice=1
for f in "$TESTS_DIR"/deploy-normal/polls-*; do
  (( $(cat "$f") >= 2 )) || all_polled_twice=0
done
check "every service was polled at least twice before judged stable" '[ "$all_polled_twice" -eq 1 ]'

echo "== register_weknora_task_definitions.sh: data guard defaults =="
for mode in default bootstrap; do
  dir="$TESTS_DIR/register-$mode"; mkdir -p "$dir/bin"
  write_aws_stub "$dir/bin/aws"
  extra=""
  [[ "$mode" == "bootstrap" ]] && extra="--initial-bootstrap"
  (
    cd "$dir"
    PATH="$dir/bin:$PATH" STUB_STATE_DIR="$dir" \
      "$WEKNORA_DIR/register_weknora_task_definitions.sh" \
        --commit 0000000000000000000000000000000000000000 \
        --admin-email test@example.com $extra \
        > out.json 2> err.log
  )
  rc=$?
  check "register $mode exits 0" '[ "$rc" -eq 0 ]'
  guard="$(jq -r '.containerDefinitions[0].environment[] | select(.name=="REQUIRE_EXISTING_PGDATA") | .value' "$dir/registered-weknora-paradedb.json")"
  if [[ "$mode" == "default" ]]; then
    check "default registration keeps REQUIRE_EXISTING_PGDATA=true" '[ "$guard" = "true" ]'
    reg="$(jq -r '.containerDefinitions[0].environment[] | select(.name=="DISABLE_REGISTRATION") | .value' "$dir/registered-weknora-app.json")"
    check "default registration keeps registration closed" '[ "$reg" = "true" ]'
  else
    check "--initial-bootstrap explicitly allows empty PGDATA" '[ "$guard" = "false" ]'
  fi
done

echo "== restore_verify_weknora_backup.sh: cleanup safety =="
NAME_1=""
NAME_2=""
for run_i in 1 2; do
  dir="$TESTS_DIR/restore-$run_i"; mkdir -p "$dir/bin"
  write_aws_stub "$dir/bin/aws"
  write_podman_stub "$dir"
  (
    cd "$dir"
    PATH="$dir/bin:$PATH" STUB_STATE_DIR="$dir" STUB_FAIL_RUN=1 AWS_REGION=us-east-1 \
      "$WEKNORA_DIR/restore_verify_weknora_backup.sh" --key db/weknora-test.dump \
      > out.json 2> err.log
  )
  rc=$?
  check "restore run $run_i: failed run exits non-zero" '[ "$rc" -ne 0 ]'
  check "restore run $run_i: no container removal attempted" '! grep -q "podman rm" "$dir/podman.log"'
  name_i="$(grep -m1 "podman run" "$dir/podman.log" | grep -o 'weknora-restore-verify-[0-9-]*' | head -1)"
  if [[ "$run_i" == "1" ]]; then NAME_1="$name_i"; else NAME_2="$name_i"; fi
done
check "two invocations use distinct container names" '[ -n "$NAME_1" ] && [ -n "$NAME_2" ] && [ "$NAME_1" != "$NAME_2" ]'

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL DEPLOY-SCRIPT REGRESSION TESTS PASSED"
else
  echo "FAILED CHECKS: $FAILURES" >&2
  exit 1
fi
