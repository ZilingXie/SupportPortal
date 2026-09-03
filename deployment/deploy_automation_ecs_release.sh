#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${AUTOMATION_RELEASE_PYTHON:-python3}"
MANIFEST_PATH=""
PROMOTION_RECORD=""
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
CLUSTER="${AUTOMATION_ECS_CLUSTER:-supportportal-production}"
API_SERVICE="${AUTOMATION_ECS_API_SERVICE:-supportportal-production-api}"
ROUTE_SERVICE="${AUTOMATION_ECS_ROUTE_SERVICE:-supportportal-production-route}"
WORKER_SERVICE="${AUTOMATION_ECS_WORKER_SERVICE:-supportportal-production-worker}"
BASE_URL="${AUTOMATION_ECS_BASE_URL:-https://supportcenter.stellarix.space/automation/production}"
EC2_BACKUP_URL="${AUTOMATION_EC2_BACKUP_URL:-https://support.stellarix.space/production/health}"
TERRAFORM_DIR="${AUTOMATION_TERRAFORM_DIR:-${PROJECT_ROOT}/infra/terraform/production}"
CHECK_ONLY=0
TEMP_DIR=""
DEPLOY_STARTED=0
ACTIVATION_STARTED=0
DEPLOY_COMPLETE=0
UPDATED_ROLES=()

log() { printf '[ecs-deploy] %s\n' "$*"; }
fail() { printf '[ecs-deploy] ERROR: %s\n' "$*" >&2; return 1; }

usage() {
  cat <<'EOF'
Usage:
  ./deployment/deploy_automation_ecs_release.sh \
    --manifest <release-manifest.json> \
    --promotion-record <promotion-record.json> [--check-only]

Both modes require the read-only source TICKET_DB_DSN. Deploy mode additionally
requires DEPLOY_PRODUCTION_APPROVED=1 and PROMPT_RELEASE_TARGET_DSN. DSN values
are never passed in argv, logged, or written to the Release Manifest or
Promotion Record.

--check-only performs only read-only validation and never syncs Prompt Releases,
registers task definitions, or updates ECS services.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --manifest) [[ $# -ge 2 ]] || fail "--manifest requires a value"; MANIFEST_PATH="$2"; shift 2 ;;
      --promotion-record) [[ $# -ge 2 ]] || fail "--promotion-record requires a value"; PROMOTION_RECORD="$2"; shift 2 ;;
      --region) [[ $# -ge 2 ]] || fail "--region requires a value"; REGION="$2"; shift 2 ;;
      --cluster) [[ $# -ge 2 ]] || fail "--cluster requires a value"; CLUSTER="$2"; shift 2 ;;
      --api-service) [[ $# -ge 2 ]] || fail "--api-service requires a value"; API_SERVICE="$2"; shift 2 ;;
      --route-service) [[ $# -ge 2 ]] || fail "--route-service requires a value"; ROUTE_SERVICE="$2"; shift 2 ;;
      --worker-service) [[ $# -ge 2 ]] || fail "--worker-service requires a value"; WORKER_SERVICE="$2"; shift 2 ;;
      --base-url) [[ $# -ge 2 ]] || fail "--base-url requires a value"; BASE_URL="${2%/}"; shift 2 ;;
      --ec2-backup-url) [[ $# -ge 2 ]] || fail "--ec2-backup-url requires a value"; EC2_BACKUP_URL="$2"; shift 2 ;;
      --terraform-dir) [[ $# -ge 2 ]] || fail "--terraform-dir requires a value"; TERRAFORM_DIR="$2"; shift 2 ;;
      --check-only) CHECK_ONLY=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown option: $1"; return 1 ;;
    esac
  done
}

service_name() {
  case "$1" in
    api) printf '%s\n' "${API_SERVICE}" ;;
    route) printf '%s\n' "${ROUTE_SERVICE}" ;;
    worker) printf '%s\n' "${WORKER_SERVICE}" ;;
  esac
}

rollback_services() {
  [[ "${DEPLOY_STARTED}" = "1" && "${ACTIVATION_STARTED}" = "0" ]] || return 0
  log "Deployment failed before Prompt activation; restoring captured task definitions"
  local index role service old_arn
  for ((index=${#UPDATED_ROLES[@]}-1; index>=0; index--)); do
    role="${UPDATED_ROLES[index]}"
    service="$(service_name "${role}")"
    old_arn="$(<"${TEMP_DIR}/${role}.old-arn")"
    aws ecs update-service --region "${REGION}" --cluster "${CLUSTER}" \
      --service "${service}" --task-definition "${old_arn}" >/dev/null || true
    aws ecs wait services-stable --region "${REGION}" --cluster "${CLUSTER}" \
      --services "${service}" || true
  done
}

cleanup() {
  local status=$?
  trap - EXIT
  if [[ ${status} -ne 0 ]]; then
    if [[ "${ACTIVATION_STARTED}" = "1" ]]; then
      printf '[ecs-deploy] ERROR: Prompt activation outcome requires reconciliation; healthy new ECS services were not rolled back.\n' >&2
    else
      rollback_services
    fi
  fi
  [[ -z "${TEMP_DIR}" || ! -d "${TEMP_DIR}" ]] || rm -rf -- "${TEMP_DIR}"
  exit "${status}"
}

run_terraform_zero_plan() {
  set +e
  terraform -chdir="${TERRAFORM_DIR}" plan \
    -detailed-exitcode -input=false -lock=false -no-color >/dev/null
  local status=$?
  set -e
  [[ ${status} -eq 0 ]] || fail "Terraform production plan must be zero drift (exit 0, got ${status})"
}

read_secret_value() {
  local reference="$1"
  if [[ "${reference}" == *":parameter/"* ]]; then
    aws ssm get-parameter --region "${REGION}" --name "${reference}" \
      --with-decryption --query 'Parameter.Value' --output text
  elif [[ "${reference}" == *":secret:"* ]]; then
    aws secretsmanager get-secret-value --region "${REGION}" --secret-id "${reference}" \
      --query 'SecretString' --output text
  else
    fail "Unsupported AUTOMATION_DB_DSN secret reference"
  fi
}

verify_running_task() {
  local role="$1" service="$2" task_definition_arn="$3" expected_digest="$4"
  local task_arn observed_definition observed_digest
  task_arn="$(aws ecs list-tasks --region "${REGION}" --cluster "${CLUSTER}" \
    --service-name "${service}" --desired-status RUNNING --query 'taskArns[0]' --output text)"
  [[ -n "${task_arn}" && "${task_arn}" != "None" ]] || fail "${role} has no running task"
  observed_definition="$(aws ecs describe-tasks --region "${REGION}" --cluster "${CLUSTER}" \
    --tasks "${task_arn}" --query 'tasks[0].taskDefinitionArn' --output text)"
  observed_digest="$(aws ecs describe-tasks --region "${REGION}" --cluster "${CLUSTER}" \
    --tasks "${task_arn}" --query "tasks[0].containers[?name=='${role}'].imageDigest | [0]" --output text)"
  [[ "${observed_definition}" = "${task_definition_arn}" ]] || fail "${role} is not running the registered revision"
  [[ "${observed_digest}" = "${expected_digest}" ]] || fail "${role} running digest mismatch"
}

verify_cloudwatch() {
  local start_ms="$1" role group count
  for role in api route worker; do
    group="$(jq -r --arg role "${role}" '.containerDefinitions[] | select(.name == $role) | .logConfiguration.options["awslogs-group"]' "${TEMP_DIR}/${role}.current.json")"
    [[ -n "${group}" && "${group}" != "null" ]] || fail "${role} CloudWatch log group is missing"
    count="$(aws logs filter-log-events --region "${REGION}" --log-group-name "${group}" \
      --start-time "${start_ms}" --filter-pattern '?ERROR ?Traceback ?Exception' \
      --query 'length(events)' --output text)"
    [[ "${count}" = "0" ]] || fail "${role} CloudWatch errors detected after deployment"
  done
}

main() {
  parse_args "$@"
  trap cleanup EXIT
  [[ -n "${MANIFEST_PATH}" && -f "${MANIFEST_PATH}" ]] || fail "Release Manifest is required"
  [[ -n "${PROMOTION_RECORD}" && -f "${PROMOTION_RECORD}" ]] || fail "Promotion Record is required"
  [[ -n "${REGION}" ]] || fail "AWS region is required"
  for command in aws curl git jq terraform; do command -v "${command}" >/dev/null 2>&1 || fail "Missing command: ${command}"; done
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || [[ -x "${PYTHON_BIN}" ]] || fail "Python runtime is required"
  [[ -n "${TICKET_DB_DSN:-}" ]] || fail "TICKET_DB_DSN is required"
  if [[ "${CHECK_ONLY}" = "0" ]]; then
    [[ "${DEPLOY_PRODUCTION_APPROVED:-}" = "1" ]] || fail "DEPLOY_PRODUCTION_APPROVED=1 is required"
    [[ -n "${PROMPT_RELEASE_TARGET_DSN:-}" ]] || fail "PROMPT_RELEASE_TARGET_DSN is required"
  fi

  MANIFEST_PATH="$(cd -- "$(dirname -- "${MANIFEST_PATH}")" && pwd)/$(basename -- "${MANIFEST_PATH}")"
  PROMOTION_RECORD="$(cd -- "$(dirname -- "${PROMOTION_RECORD}")" && pwd)/$(basename -- "${PROMOTION_RECORD}")"
  mkdir -p -- "${PROJECT_ROOT}/.deployments"
  TEMP_DIR="$(mktemp -d "${PROJECT_ROOT}/.deployments/ecs-deploy.XXXXXX")"

  "${PYTHON_BIN}" -m backend.scripts.automation_release validate --manifest "${MANIFEST_PATH}" >/dev/null
  local promotion_json registry_id record_region repository release_id git_commit prompt_release_id build_time
  promotion_json="$("${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy validate-promotion \
    --manifest "${MANIFEST_PATH}" --promotion-record "${PROMOTION_RECORD}")"
  registry_id="$(jq -r '.registry_id' <<<"${promotion_json}")"
  record_region="$(jq -r '.region' <<<"${promotion_json}")"
  repository="$(jq -r '.repository' <<<"${promotion_json}")"
  [[ "${record_region}" = "${REGION}" ]] || fail "Promotion Record region mismatch"
  release_id="$(jq -r '.release_id' "${MANIFEST_PATH}")"
  git_commit="$(jq -r '.git_commit' "${MANIFEST_PATH}")"
  prompt_release_id="$(jq -r '.prompt_release_id' "${MANIFEST_PATH}")"
  build_time="$(jq -r '.build_time' "${MANIFEST_PATH}")"
  [[ "$(git -C "${PROJECT_ROOT}" rev-parse HEAD)" = "${git_commit}" ]] || fail "Manifest Git commit is not current HEAD"
  [[ -z "$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=no)" ]] || fail "Working tree has tracked changes"

  run_terraform_zero_plan
  TICKET_DB_DSN="${TICKET_DB_DSN:-}" TICKET_DB_SCHEMA="${TICKET_DB_SCHEMA:-supportportal}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release validate --release-id "${prompt_release_id}" >/dev/null

  local role service current_arn expected_digest tag observed_digest
  for role in api route worker; do
    expected_digest="$(jq -r --arg role "${role}" '.components[$role].digest' "${MANIFEST_PATH}")"
    tag="$(jq -r --arg role "${role}" '.components[$role].tag' "${MANIFEST_PATH}")"
    observed_digest="$(aws ecr describe-images --region "${REGION}" --registry-id "${registry_id}" \
      --repository-name "${repository}" --image-ids "imageTag=${tag}" \
      --query 'imageDetails[0].imageDigest' --output text)"
    [[ "${observed_digest}" = "${expected_digest}" ]] || fail "${role} ECR digest mismatch"
    service="$(service_name "${role}")"
    current_arn="$(aws ecs describe-services --region "${REGION}" --cluster "${CLUSTER}" \
      --services "${service}" --query 'services[0].taskDefinition' --output text)"
    [[ -n "${current_arn}" && "${current_arn}" != "None" ]] || fail "${role} service/task definition not found"
    printf '%s\n' "${current_arn}" >"${TEMP_DIR}/${role}.old-arn"
    aws ecs describe-task-definition --region "${REGION}" --task-definition "${current_arn}" \
      --include TAGS >"${TEMP_DIR}/${role}.current.json"
    "${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy render-task-definition \
      --role "${role}" --current "${TEMP_DIR}/${role}.current.json" \
      --manifest "${MANIFEST_PATH}" --registry-id "${registry_id}" --region "${REGION}" \
      --output "${TEMP_DIR}/${role}.register.json" >/dev/null
  done

  local suspension_reference suspension_recipients_json
  suspension_reference="$(jq -r '.taskDefinition.containerDefinitions[] | select(.name == "worker") | .secrets[] | select(.name == "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON") | .valueFrom' "${TEMP_DIR}/worker.current.json")"
  suspension_recipients_json="$(read_secret_value "${suspension_reference}")"
  ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON="${suspension_recipients_json}" \
    "${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy \
      validate-suspension-recipients >/dev/null
  unset suspension_recipients_json

  curl -fsS "${EC2_BACKUP_URL}" >/dev/null || fail "EC2 /production backup health check failed"
  if [[ "${CHECK_ONLY}" = "1" ]]; then
    log "Check-only passed; no Prompt Release, task definition, or ECS service was changed"
    return 0
  fi

  PROMPT_RELEASE_TARGET_DSN="${PROMPT_RELEASE_TARGET_DSN}" \
    PROMPT_RELEASE_TARGET_SCHEMA="${PROMPT_RELEASE_TARGET_SCHEMA:-supportportal_production}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release sync \
      --release-id "${prompt_release_id}" --defer-activation >"${TEMP_DIR}/prompt-sync.json"
  [[ "$(jq -r '.sync.status' "${TEMP_DIR}/prompt-sync.json")" = "candidate" || "$(jq -r '.sync.status' "${TEMP_DIR}/prompt-sync.json")" = "active" ]] \
    || fail "Target Prompt Release is not deployable"

  local new_arn tags_path
  for role in api route worker; do
    tags_path="${TEMP_DIR}/${role}.tags.json"
    jq '.tags // []' "${TEMP_DIR}/${role}.current.json" >"${tags_path}"
    new_arn="$(aws ecs register-task-definition --region "${REGION}" \
      --cli-input-json "file://${TEMP_DIR}/${role}.register.json" \
      --tags "file://${tags_path}" --query 'taskDefinition.taskDefinitionArn' --output text)"
    [[ -n "${new_arn}" && "${new_arn}" != "None" ]] || fail "${role} task definition registration failed"
    printf '%s\n' "${new_arn}" >"${TEMP_DIR}/${role}.new-arn"
  done

  local deployment_start_ms
  deployment_start_ms="$(($(date -u +%s) * 1000))"
  DEPLOY_STARTED=1
  for role in route worker; do
    service="$(service_name "${role}")"
    new_arn="$(<"${TEMP_DIR}/${role}.new-arn")"
    aws ecs update-service --region "${REGION}" --cluster "${CLUSTER}" \
      --service "${service}" --task-definition "${new_arn}" >/dev/null
    UPDATED_ROLES+=("${role}")
    aws ecs wait services-stable --region "${REGION}" --cluster "${CLUSTER}" --services "${service}"
    expected_digest="$(jq -r --arg role "${role}" '.components[$role].digest' "${MANIFEST_PATH}")"
    verify_running_task "${role}" "${service}" "${new_arn}" "${expected_digest}"
  done

  local dsn_reference heartbeat_dsn
  dsn_reference="$(jq -r '.taskDefinition.containerDefinitions[] | select(.name == "worker") | .secrets[] | select(.name == "AUTOMATION_DB_DSN") | .valueFrom' "${TEMP_DIR}/worker.current.json")"
  heartbeat_dsn="$(read_secret_value "${dsn_reference}")"
  AUTOMATION_HEARTBEAT_DSN="${heartbeat_dsn}" \
    "${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy verify-heartbeats \
      --manifest "${MANIFEST_PATH}" --task-definition "${TEMP_DIR}/worker.register.json" \
      --max-age-seconds 90 >/dev/null
  unset heartbeat_dsn

  role="api"
  service="${API_SERVICE}"
  new_arn="$(<"${TEMP_DIR}/api.new-arn")"
  aws ecs update-service --region "${REGION}" --cluster "${CLUSTER}" \
    --service "${service}" --task-definition "${new_arn}" >/dev/null
  UPDATED_ROLES+=("api")
  aws ecs wait services-stable --region "${REGION}" --cluster "${CLUSTER}" --services "${service}"
  expected_digest="$(jq -r '.components.api.digest' "${MANIFEST_PATH}")"
  verify_running_task api "${service}" "${new_arn}" "${expected_digest}"

  local release_json ready_json
  curl -fsS "${BASE_URL}/health/live" >/dev/null
  release_json="$(curl -fsS "${BASE_URL}/health/release")"
  ready_json="$(curl -fsS "${BASE_URL}/health/ready")"
  [[ "$(jq -r '.status' <<<"${ready_json}")" = "ok" ]] || fail "ECS readiness check failed"
  [[ "$(jq -r '.provenance.release_id' <<<"${release_json}")" = "${release_id}" ]] || fail "ECS release id mismatch"
  [[ "$(jq -r '.provenance.git_commit' <<<"${release_json}")" = "${git_commit}" ]] || fail "ECS Git commit mismatch"
  [[ "$(jq -r '.provenance.prompt_release_id' <<<"${release_json}")" = "${prompt_release_id}" ]] || fail "ECS Prompt Release mismatch"
  [[ "$(jq -r '.provenance.build_time' <<<"${release_json}")" = "${build_time}" ]] || fail "ECS build time mismatch"
  verify_cloudwatch "${deployment_start_ms}"
  curl -fsS "${EC2_BACKUP_URL}" >/dev/null || fail "EC2 /production backup health check failed"

  ACTIVATION_STARTED=1
  TICKET_DB_DSN="${PROMPT_RELEASE_TARGET_DSN}" \
    TICKET_DB_SCHEMA="${PROMPT_RELEASE_TARGET_SCHEMA:-supportportal_production}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release activate \
      --release-id "${prompt_release_id}" >"${TEMP_DIR}/prompt-activate.json"
  TICKET_DB_DSN="${PROMPT_RELEASE_TARGET_DSN}" \
    TICKET_DB_SCHEMA="${PROMPT_RELEASE_TARGET_SCHEMA:-supportportal_production}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release validate \
      --release-id "${prompt_release_id}" >/dev/null
  [[ "$(jq -r '.release.status' "${TEMP_DIR}/prompt-activate.json")" = "active" ]] \
    || fail "Target Prompt Release activation readback failed"
  DEPLOY_COMPLETE=1
  log "Deployment verified and target Prompt Release activated: ${release_id}"
}

main "$@"
