#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${AUTOMATION_RELEASE_PYTHON:-python3}"
MANIFEST_PATH=""
PROMOTION_RECORD=""
PUBLISH_RECORD=""
DEPLOY_RECORD=""
ENVIRONMENT="${AUTOMATION_ENVIRONMENT:-production}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
CLUSTER="${AUTOMATION_ECS_CLUSTER:-}"
API_SERVICE="${AUTOMATION_ECS_API_SERVICE:-}"
ROUTE_SERVICE="${AUTOMATION_ECS_ROUTE_SERVICE:-}"
WORKER_SERVICE="${AUTOMATION_ECS_WORKER_SERVICE:-}"
BASE_URL="${AUTOMATION_ECS_BASE_URL:-}"
EC2_BACKUP_URL="${AUTOMATION_EC2_BACKUP_URL:-https://support.stellarix.space/health}"
TERRAFORM_DIR="${AUTOMATION_TERRAFORM_DIR:-}"
TERRAFORM_BIN="${AUTOMATION_TERRAFORM_BIN:-terraform}"
CHECK_ONLY=0
RESUME=0
BOOTSTRAP_ACCOUNT_SCHEMA=0
HERMES_CASE_WORKFLOW_MODE=""
HERMES_PERSONA_ENABLED=0
SCHEMA_MIGRATION_PARAMETER="${AUTOMATION_ECS_SCHEMA_MIGRATION_PARAMETER:-}"
PROMPT_TARGET_SCHEMA="${PROMPT_RELEASE_TARGET_SCHEMA:-}"
TEMP_DIR=""
STATE_DIR=""
REMOVE_TEMP_DIR=0
DEPLOY_STARTED=0
ACTIVATION_STARTED=0
DEPLOY_COMPLETE=0
UPDATED_ROLES=()
HEARTBEAT_WAIT_TIMEOUT_SECONDS=90
HEARTBEAT_RETRY_INTERVAL_SECONDS=5
SERVICE_ROLLOUT_WAIT_TIMEOUT_SECONDS=900
SERVICE_ROLLOUT_RETRY_INTERVAL_SECONDS=5
AWS_MIN_CREDENTIAL_TTL_SECONDS="${AUTOMATION_AWS_MIN_CREDENTIAL_TTL_SECONDS:-2700}"
RELEASE_ID=""
GIT_COMMIT=""
PROMPT_RELEASE_ID=""
BUILD_TIME=""
REGISTRY_ID=""
PROMOTION_REPOSITORY=""
PROMPT_SYNC_STATUS="not_started"
PROMPT_ACTIVATION_STATUS="not_started"
SOURCE_PROMPT_STATUS="not_started"
TERRAFORM_STATUS="not_started"
ECR_STATUS="not_started"
SUSPENSION_RECIPIENTS_STATUS="not_started"
HEARTBEAT_STATUS="not_started"
PUBLIC_HEALTH_STATUS="not_started"
CLOUDWATCH_STATUS="not_started"
EC2_BACKUP_STATUS="not_started"
ROLLBACK_STATUS="not_started"
SCHEMA_BOOTSTRAP_STATUS="not_requested"
SCHEMA_BOOTSTRAP_TASK_DEFINITION=""
SCHEMA_BOOTSTRAP_TASK_ARN=""
SKIP_SCHEMA_BOOTSTRAP=0

log() { printf '[ecs-deploy] %s\n' "$*"; }
fail() { printf '[ecs-deploy] ERROR: %s\n' "$*" >&2; return 1; }

usage() {
  cat <<'EOF'
Usage:
  ./deployment/deploy_automation_ecs_release.sh \
    [--environment production|preproduction] \
    --manifest <release-manifest.json> \
    (--promotion-record <promotion-record.json> | \
     --publish-record <preproduction-publish-record.json>) \
    [--check-only | --resume]

Optional activation gates:
  --bootstrap-account-schema
  --hermes-case-workflow-mode <disabled|mock|real>
  --hermes-persona-enabled

Both modes require the read-only source TICKET_DB_DSN. Deploy mode additionally
requires the environment-specific approval variable and PROMPT_RELEASE_TARGET_DSN.
DSN values are never passed in argv, logged, or written to release evidence.

--check-only performs only read-only validation and never syncs Prompt Releases,
registers task definitions, or updates ECS services.

--resume reuses an environment/release/mode-scoped checkpoint only after
revalidating its input fingerprints, registered task definitions, running
revisions, and image digests.

Hermes mock or real activation requires --bootstrap-account-schema. The one-off
bootstrap uses the release API image and the existing migration SecureString;
the DSN is never read into this process or stored in deployment evidence.
EOF
}

configure_environment_defaults() {
  case "${ENVIRONMENT}" in
    production)
      CLUSTER="${CLUSTER:-supportportal-production}"
      API_SERVICE="${API_SERVICE:-supportportal-production-api}"
      ROUTE_SERVICE="${ROUTE_SERVICE:-supportportal-production-route}"
      WORKER_SERVICE="${WORKER_SERVICE:-supportportal-production-worker}"
      BASE_URL="${BASE_URL:-https://supportcenter.stellarix.space/automation/production}"
      TERRAFORM_DIR="${TERRAFORM_DIR:-${PROJECT_ROOT}/infra/terraform/production}"
      SCHEMA_MIGRATION_PARAMETER="${SCHEMA_MIGRATION_PARAMETER:-/supportportal/production/automation-db-migration-dsn}"
      PROMPT_TARGET_SCHEMA="${PROMPT_TARGET_SCHEMA:-supportportal_production}"
      ;;
    preproduction)
      CLUSTER="${CLUSTER:-supportportal-preproduction}"
      API_SERVICE="${API_SERVICE:-supportportal-preproduction-api}"
      ROUTE_SERVICE="${ROUTE_SERVICE:-supportportal-preproduction-route}"
      WORKER_SERVICE="${WORKER_SERVICE:-supportportal-preproduction-worker}"
      BASE_URL="${BASE_URL:-https://supportcenter.stellarix.space/automation/preproduction}"
      TERRAFORM_DIR="${TERRAFORM_DIR:-${PROJECT_ROOT}/infra/terraform/preproduction}"
      SCHEMA_MIGRATION_PARAMETER="${SCHEMA_MIGRATION_PARAMETER:-/supportportal/preproduction/automation-db-migration-dsn}"
      PROMPT_TARGET_SCHEMA="${PROMPT_TARGET_SCHEMA:-supportportal_preproduction}"
      ;;
    *) fail "--environment must be preproduction or production" ;;
  esac
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --manifest) [[ $# -ge 2 ]] || fail "--manifest requires a value"; MANIFEST_PATH="$2"; shift 2 ;;
      --environment) [[ $# -ge 2 ]] || fail "--environment requires a value"; ENVIRONMENT="$2"; shift 2 ;;
      --promotion-record) [[ $# -ge 2 ]] || fail "--promotion-record requires a value"; PROMOTION_RECORD="$2"; shift 2 ;;
      --publish-record) [[ $# -ge 2 ]] || fail "--publish-record requires a value"; PUBLISH_RECORD="$2"; shift 2 ;;
      --region) [[ $# -ge 2 ]] || fail "--region requires a value"; REGION="$2"; shift 2 ;;
      --cluster) [[ $# -ge 2 ]] || fail "--cluster requires a value"; CLUSTER="$2"; shift 2 ;;
      --api-service) [[ $# -ge 2 ]] || fail "--api-service requires a value"; API_SERVICE="$2"; shift 2 ;;
      --route-service) [[ $# -ge 2 ]] || fail "--route-service requires a value"; ROUTE_SERVICE="$2"; shift 2 ;;
      --worker-service) [[ $# -ge 2 ]] || fail "--worker-service requires a value"; WORKER_SERVICE="$2"; shift 2 ;;
      --base-url) [[ $# -ge 2 ]] || fail "--base-url requires a value"; BASE_URL="${2%/}"; shift 2 ;;
      --ec2-backup-url) [[ $# -ge 2 ]] || fail "--ec2-backup-url requires a value"; EC2_BACKUP_URL="$2"; shift 2 ;;
      --terraform-dir) [[ $# -ge 2 ]] || fail "--terraform-dir requires a value"; TERRAFORM_DIR="$2"; shift 2 ;;
      --bootstrap-account-schema) BOOTSTRAP_ACCOUNT_SCHEMA=1; shift ;;
      --hermes-case-workflow-mode) [[ $# -ge 2 ]] || fail "--hermes-case-workflow-mode requires a value"; HERMES_CASE_WORKFLOW_MODE="$2"; shift 2 ;;
      --hermes-persona-enabled) HERMES_PERSONA_ENABLED=1; shift ;;
      --check-only) CHECK_ONLY=1; shift ;;
      --resume) RESUME=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown option: $1"; return 1 ;;
    esac
  done
  configure_environment_defaults
  [[ "${CHECK_ONLY}" = "0" || "${RESUME}" = "0" ]] || fail "--check-only and --resume are mutually exclusive"
  [[ -z "${HERMES_CASE_WORKFLOW_MODE}" || "${HERMES_CASE_WORKFLOW_MODE}" = "disabled" || "${HERMES_CASE_WORKFLOW_MODE}" = "mock" || "${HERMES_CASE_WORKFLOW_MODE}" = "real" ]] \
    || fail "--hermes-case-workflow-mode must be disabled, mock, or real"
  [[ "${HERMES_CASE_WORKFLOW_MODE}" != "mock" || "${BOOTSTRAP_ACCOUNT_SCHEMA}" = "1" ]] \
    || fail "Hermes mock activation requires --bootstrap-account-schema"
  [[ "${HERMES_CASE_WORKFLOW_MODE}" != "real" || "${BOOTSTRAP_ACCOUNT_SCHEMA}" = "1" ]] \
    || fail "Hermes real activation requires --bootstrap-account-schema"
}

file_sha256() {
  "${PYTHON_BIN}" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$1"
}

credential_expiration_epoch() {
  local exported expiration
  exported="$(aws configure export-credentials --format process 2>/dev/null || true)"
  expiration="$(jq -r '.Expiration // empty' <<<"${exported}" 2>/dev/null || true)"
  unset exported
  [[ -n "${expiration}" ]] || return 0
  "${PYTHON_BIN}" -c 'from datetime import datetime; import sys; print(int(datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00")).timestamp()))' "${expiration}"
}

aws_credential_provider_type() {
  aws configure list 2>/dev/null \
    | awk '$1 == "access_key" {print $5; exit}'
}

verify_aws_credential_lifetime() {
  [[ "${AWS_MIN_CREDENTIAL_TTL_SECONDS}" =~ ^[0-9]+$ ]] || fail "AUTOMATION_AWS_MIN_CREDENTIAL_TTL_SECONDS must be a non-negative integer"
  aws sts get-caller-identity --region "${REGION}" --output json >/dev/null
  local expiration_epoch now_epoch provider_type remaining
  expiration_epoch="$(credential_expiration_epoch)"
  if [[ -z "${expiration_epoch}" ]]; then
    [[ -z "${AWS_SESSION_TOKEN:-}" ]] \
      || fail "Temporary AWS session expiration is unavailable; use a refreshable AWS provider instead of exported session credentials"
    log "AWS identity passed; provider did not expose a credential expiration"
    return 0
  fi
  now_epoch="$(date -u +%s)"
  remaining=$((expiration_epoch - now_epoch))
  if ((remaining < AWS_MIN_CREDENTIAL_TTL_SECONDS)); then
    provider_type="$(aws_credential_provider_type)"
    if [[ "${provider_type}" = "login" ]]; then
      log "AWS refreshable login credential preflight passed (${remaining}s current credential lifetime)"
      return 0
    fi
  fi
  ((remaining >= AWS_MIN_CREDENTIAL_TTL_SECONDS)) \
    || fail "AWS credentials expire too soon (${remaining}s remaining; require ${AWS_MIN_CREDENTIAL_TTL_SECONDS}s)"
  log "AWS credential lifetime preflight passed (${remaining}s remaining)"
}

checkpoint_identity() {
  local manifest_sha record_sha
  manifest_sha="$(file_sha256 "${MANIFEST_PATH}")"
  DEPLOY_RECORD="${DEPLOY_RECORD:-${PUBLISH_RECORD:-${PROMOTION_RECORD}}}"
  record_sha="$(file_sha256 "${DEPLOY_RECORD}")"
  jq -n -S \
    --arg schema_version "automation-ecs-deploy-checkpoint-v1" \
    --arg manifest_sha256 "${manifest_sha}" \
    --arg deploy_record_sha256 "${record_sha}" \
    --arg release_id "${RELEASE_ID}" \
    --arg git_commit "${GIT_COMMIT}" \
    --arg prompt_release_id "${PROMPT_RELEASE_ID}" \
    --arg region "${REGION}" \
    --arg environment "${ENVIRONMENT}" \
    --arg cluster "${CLUSTER}" \
    --arg api_service "${API_SERVICE}" \
    --arg route_service "${ROUTE_SERVICE}" \
    --arg worker_service "${WORKER_SERVICE}" \
    --arg schema_bootstrap "${BOOTSTRAP_ACCOUNT_SCHEMA}" \
    --arg hermes_mode "${HERMES_CASE_WORKFLOW_MODE}" \
    --arg hermes_persona_enabled "${HERMES_PERSONA_ENABLED}" \
    '{schema_version:$schema_version,manifest_sha256:$manifest_sha256,deploy_record_sha256:$deploy_record_sha256,release_id:$release_id,git_commit:$git_commit,prompt_release_id:$prompt_release_id,region:$region,environment:$environment,cluster:$cluster,services:{api:$api_service,route:$route_service,worker:$worker_service},schema_bootstrap:($schema_bootstrap == "1"),hermes_case_workflow_mode:$hermes_mode,hermes_persona_enabled:($hermes_persona_enabled == "1")}'
}

prepare_deploy_workspace() {
  mkdir -p -- "${PROJECT_ROOT}/.deployments"
  if [[ "${CHECK_ONLY}" = "1" ]]; then
    TEMP_DIR="$(mktemp -d "${PROJECT_ROOT}/.deployments/ecs-deploy-check.XXXXXX")"
    REMOVE_TEMP_DIR=1
    return 0
  fi
  local operation_suffix=""
  if [[ -n "${HERMES_CASE_WORKFLOW_MODE}" ]]; then
    operation_suffix="-${HERMES_CASE_WORKFLOW_MODE}"
  fi
  if [[ "${HERMES_PERSONA_ENABLED}" = "1" ]]; then
    operation_suffix="${operation_suffix}-persona"
  fi
  STATE_DIR="${AUTOMATION_ECS_DEPLOY_STATE_DIR:-${PROJECT_ROOT}/.deployments/ecs-deploy-${ENVIRONMENT}-${RELEASE_ID}${operation_suffix}}"
  if [[ "${RESUME}" = "1" ]]; then
    [[ -f "${STATE_DIR}/checkpoint.json" ]] || fail "Deploy checkpoint not found for --resume: ${STATE_DIR}"
    local expected existing
    expected="$(checkpoint_identity)"
    existing="$(jq -S . "${STATE_DIR}/checkpoint.json")"
    [[ "${existing}" = "${expected}" ]] || fail "Deploy checkpoint identity does not match the requested release/environment"
    TEMP_DIR="${STATE_DIR}"
    log "Validated deploy checkpoint: ${STATE_DIR}"
    return 0
  fi
  [[ ! -e "${STATE_DIR}" ]] || fail "Deploy checkpoint already exists; use --resume after reviewing ${STATE_DIR}"
  mkdir -p -- "${STATE_DIR}"
  chmod 700 "${STATE_DIR}"
  TEMP_DIR="${STATE_DIR}"
  checkpoint_identity >"${STATE_DIR}/checkpoint.json.tmp"
  mv -- "${STATE_DIR}/checkpoint.json.tmp" "${STATE_DIR}/checkpoint.json"
}

service_name() {
  case "$1" in
    api) printf '%s\n' "${API_SERVICE}" ;;
    route) printf '%s\n' "${ROUTE_SERVICE}" ;;
    worker) printf '%s\n' "${WORKER_SERVICE}" ;;
  esac
}

add_updated_role() {
  local candidate="$1" current
  for current in "${UPDATED_ROLES[@]-}"; do
    [[ "${current}" != "${candidate}" ]] || return 0
  done
  UPDATED_ROLES+=("${candidate}")
}

read_optional_file() {
  local path="$1"
  [[ -f "${path}" ]] && sed -n '1p' "${path}" || true
}

write_evidence() {
  local status="$1" role old_arn new_arn verified expected_digest components prompt_build_ref prompt_fingerprint generated_at schema_task_arn schema_task_definition
  [[ -n "${STATE_DIR}" && -d "${STATE_DIR}" ]] || return 0
  components='{}'
  for role in api route worker; do
    old_arn="$(read_optional_file "${STATE_DIR}/${role}.old-arn")"
    new_arn="$(read_optional_file "${STATE_DIR}/${role}.new-arn")"
    verified="$(read_optional_file "${STATE_DIR}/${role}.verified")"
    expected_digest="$(jq -r --arg role "${role}" '.components[$role].digest // ""' "${MANIFEST_PATH}" 2>/dev/null || true)"
    components="$(jq -c \
      --arg role "${role}" --arg old_arn "${old_arn}" --arg new_arn "${new_arn}" \
      --arg expected_digest "${expected_digest}" --arg verified "${verified}" \
      '. + {($role):{old_task_definition:$old_arn,new_task_definition:$new_arn,expected_digest:$expected_digest,runtime_verified:($verified == "passed")}}' \
      <<<"${components}")"
  done
  prompt_build_ref="$(jq -r '.identity.build_ref // ""' "${STATE_DIR}/prompt-sync.json" 2>/dev/null || true)"
  prompt_fingerprint="$(jq -r '.identity.content_fingerprint // ""' "${STATE_DIR}/prompt-sync.json" 2>/dev/null || true)"
  schema_task_arn="$(read_optional_file "${STATE_DIR}/schema-bootstrap.task-arn")"
  schema_task_definition="$(read_optional_file "${STATE_DIR}/schema-bootstrap.task-definition-arn")"
  generated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  jq -n -S \
    --arg schema_version "automation-ecs-deploy-evidence-v1" \
    --arg status "${status}" \
    --arg release_id "${RELEASE_ID}" \
    --arg git_commit "${GIT_COMMIT}" \
    --arg generated_at "${generated_at}" \
    --arg prompt_release_id "${PROMPT_RELEASE_ID}" \
    --arg prompt_build_ref "${prompt_build_ref}" \
    --arg prompt_fingerprint "${prompt_fingerprint}" \
    --arg region "${REGION}" \
    --arg environment "${ENVIRONMENT}" \
    --arg cluster "${CLUSTER}" \
    --arg registry_id "${REGISTRY_ID}" \
    --arg repository "${PROMOTION_REPOSITORY}" \
    --arg prompt_sync "${PROMPT_SYNC_STATUS}" \
    --arg prompt_activation "${PROMPT_ACTIVATION_STATUS}" \
    --arg source_prompt "${SOURCE_PROMPT_STATUS}" \
    --arg terraform "${TERRAFORM_STATUS}" \
    --arg ecr "${ECR_STATUS}" \
    --arg suspension_recipients "${SUSPENSION_RECIPIENTS_STATUS}" \
    --arg heartbeats "${HEARTBEAT_STATUS}" \
    --arg public_health "${PUBLIC_HEALTH_STATUS}" \
    --arg cloudwatch "${CLOUDWATCH_STATUS}" \
    --arg ec2_backup "${EC2_BACKUP_STATUS}" \
    --arg rollback "${ROLLBACK_STATUS}" \
    --arg schema_bootstrap "${SCHEMA_BOOTSTRAP_STATUS}" \
    --arg schema_task_arn "${schema_task_arn}" \
    --arg schema_task_definition "${schema_task_definition}" \
    --arg hermes_mode "${HERMES_CASE_WORKFLOW_MODE}" \
    --arg hermes_persona_enabled "${HERMES_PERSONA_ENABLED}" \
    --argjson components "${components}" \
    '{schema_version:$schema_version,status:$status,generated_at:$generated_at,release_id:$release_id,git_commit:$git_commit,prompt_release:{release_id:$prompt_release_id,build_ref:$prompt_build_ref,content_fingerprint:$prompt_fingerprint},region:$region,environment:$environment,cluster:$cluster,registry:{id:$registry_id,repository:$repository},components:$components,hermes:{persona_enabled:($hermes_persona_enabled == "1")},hermes_case_workflow:{mode:$hermes_mode,schema_bootstrap_task_arn:$schema_task_arn,schema_bootstrap_task_definition:$schema_task_definition},checks:{terraform_zero_drift:$terraform,source_prompt:$source_prompt,ecr:$ecr,suspension_recipients:$suspension_recipients,schema_bootstrap:$schema_bootstrap,prompt_sync:$prompt_sync,heartbeats:$heartbeats,public_health:$public_health,cloudwatch:$cloudwatch,ec2_backup:$ec2_backup,prompt_activation:$prompt_activation,rollback:$rollback}}' \
    >"${STATE_DIR}/evidence.json.tmp"
  mv -- "${STATE_DIR}/evidence.json.tmp" "${STATE_DIR}/evidence.json"
}

prune_success_artifacts() {
  local role
  for role in api route worker; do
    rm -f -- \
      "${STATE_DIR}/${role}.current.json" \
      "${STATE_DIR}/${role}.register.json" \
      "${STATE_DIR}/${role}.registered.json" \
      "${STATE_DIR}/${role}.registered-rendered.json" \
      "${STATE_DIR}/${role}.register.normalized.json" \
      "${STATE_DIR}/${role}.registered-rendered.normalized.json" \
      "${STATE_DIR}/${role}.tags.json"
  done
  rm -f -- "${STATE_DIR}/public-health.log" "${STATE_DIR}/cloudwatch.log" "${STATE_DIR}/ec2-backup.log"
  rm -f -- \
    "${STATE_DIR}/schema-bootstrap.register.json" \
    "${STATE_DIR}/schema-bootstrap.network.json" \
    "${STATE_DIR}/schema-bootstrap.tags.json" \
    "${STATE_DIR}/schema-bootstrap.task.json"
}

rollback_services() {
  [[ "${DEPLOY_STARTED}" = "1" && "${ACTIVATION_STARTED}" = "0" ]] || return 0
  log "Deployment failed before Prompt activation; restoring captured task definitions"
  local index role service old_arn failed=0
  ROLLBACK_STATUS="in_progress"
  for ((index=${#UPDATED_ROLES[@]}-1; index>=0; index--)); do
    role="${UPDATED_ROLES[index]}"
    service="$(service_name "${role}")"
    old_arn="$(<"${TEMP_DIR}/${role}.old-arn")"
    if ! aws ecs update-service --region "${REGION}" --cluster "${CLUSTER}" \
      --service "${service}" --task-definition "${old_arn}" >/dev/null; then
      failed=1
    elif ! wait_for_service_revision "${role}" "${service}" "${old_arn}"; then
      failed=1
    fi
    rm -f -- "${TEMP_DIR}/${role}.verified"
  done
  if [[ "${failed}" = "0" ]]; then
    ROLLBACK_STATUS="succeeded"
    return 0
  fi
  ROLLBACK_STATUS="failed"
  return 1
}

wait_for_service_revision() {
  local role="$1" service="$2" expected_task_definition="$3"
  local deadline service_json rollout_state
  deadline="$(($(date -u +%s) + SERVICE_ROLLOUT_WAIT_TIMEOUT_SECONDS))"
  while true; do
    service_json="$(aws ecs describe-services --region "${REGION}" --cluster "${CLUSTER}" \
      --services "${service}")"
    [[ "$(jq '.failures | length' <<<"${service_json}")" = "0" ]] \
      || fail "${role} service readback failed during rollout"
    rollout_state="$(jq -r '.services[0].deployments[]? | select(.status == "PRIMARY") | .rolloutState // empty' <<<"${service_json}")"
    [[ "${rollout_state}" != "FAILED" ]] || fail "${role} ECS rollout failed"
    if jq -e --arg expected "${expected_task_definition}" '
      (.services | length) == 1
      and .services[0].status == "ACTIVE"
      and .services[0].taskDefinition == $expected
      and .services[0].desiredCount == 1
      and .services[0].runningCount == 1
      and .services[0].pendingCount == 0
      and (.services[0].deployments | length) == 1
      and .services[0].deployments[0].status == "PRIMARY"
      and .services[0].deployments[0].taskDefinition == $expected
      and .services[0].deployments[0].rolloutState == "COMPLETED"
      and .services[0].deployments[0].desiredCount == 1
      and .services[0].deployments[0].runningCount == 1
      and .services[0].deployments[0].pendingCount == 0
    ' <<<"${service_json}" >/dev/null; then
      return 0
    fi
    if (( $(date -u +%s) >= deadline )); then
      fail "${role} ECS revision did not converge before timeout"
      return 1
    fi
    sleep "${SERVICE_ROLLOUT_RETRY_INTERVAL_SECONDS}"
  done
}

cleanup_schema_bootstrap() {
  if [[ -n "${SCHEMA_BOOTSTRAP_TASK_ARN}" && "${SCHEMA_BOOTSTRAP_STATUS}" != "passed" ]]; then
    aws ecs stop-task --region "${REGION}" --cluster "${CLUSTER}" \
      --task "${SCHEMA_BOOTSTRAP_TASK_ARN}" \
      --reason "schema bootstrap deploy command ended before success" >/dev/null 2>&1 || true
  fi
  if [[ -n "${SCHEMA_BOOTSTRAP_TASK_DEFINITION}" ]]; then
    aws ecs deregister-task-definition --region "${REGION}" \
      --task-definition "${SCHEMA_BOOTSTRAP_TASK_DEFINITION}" >/dev/null 2>&1 || true
    SCHEMA_BOOTSTRAP_TASK_DEFINITION=""
  fi
}

cleanup() {
  local status=$?
  trap - EXIT
  cleanup_schema_bootstrap
  if [[ ${status} -ne 0 ]]; then
    if [[ "${ACTIVATION_STARTED}" = "1" ]]; then
      ROLLBACK_STATUS="not_applicable"
      printf '[ecs-deploy] ERROR: Prompt Release is or may be active; ECS services were not blindly rolled back and require reconciliation.\n' >&2
      write_evidence "reconciliation_required" || true
    else
      if rollback_services; then
        write_evidence "failed_before_activation" || true
      else
        printf '[ecs-deploy] ERROR: One or more ECS services could not be restored; reconciliation is required.\n' >&2
        write_evidence "rollback_incomplete" || true
      fi
    fi
  fi
  if [[ "${REMOVE_TEMP_DIR}" = "1" && -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
    rm -rf -- "${TEMP_DIR}"
  fi
  exit "${status}"
}

run_terraform_zero_plan() {
  set +e
  "${TERRAFORM_BIN}" -chdir="${TERRAFORM_DIR}" plan \
    -detailed-exitcode -input=false -lock-timeout=60s -no-color >/dev/null
  local status=$?
  set -e
  [[ ${status} -eq 0 ]] || fail "Terraform ${ENVIRONMENT} plan must be zero drift (exit 0, got ${status})"
  TERRAFORM_STATUS="passed"
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

render_role_task_definition() {
  local role="$1" current_path="$2" output_path="$3"
  local -a args
  args=(
    --role "${role}"
    --current "${current_path}"
    --manifest "${MANIFEST_PATH}"
    --registry-id "${REGISTRY_ID}"
    --region "${REGION}"
    --environment "${ENVIRONMENT}"
    --repository "${PROMOTION_REPOSITORY}"
    --output "${output_path}"
  )
  if [[ -n "${HERMES_CASE_WORKFLOW_MODE}" ]]; then
    args+=(--hermes-case-workflow-mode "${HERMES_CASE_WORKFLOW_MODE}")
  fi
  if [[ "${HERMES_PERSONA_ENABLED}" = "1" ]]; then
    args+=(--hermes-persona-enabled)
  fi
  "${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy \
    render-task-definition "${args[@]}" >/dev/null
}

prepare_schema_bootstrap() {
  [[ "${BOOTSTRAP_ACCOUNT_SCHEMA}" = "1" ]] || return 0
  if schema_is_current; then
    SKIP_SCHEMA_BOOTSTRAP=1
    SCHEMA_BOOTSTRAP_STATUS="skipped_current"
    log "${ENVIRONMENT} Account schema already matches the release; bootstrap skipped"
    return 0
  fi
  local migration_reference
  migration_reference="$(aws ssm get-parameter --region "${REGION}" \
    --name "${SCHEMA_MIGRATION_PARAMETER}" \
    --query 'Parameter.ARN' --output text)"
  [[ -n "${migration_reference}" && "${migration_reference}" != "None" ]] \
    || fail "${ENVIRONMENT} schema migration parameter is missing"
  "${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy \
    render-schema-bootstrap-task-definition \
    --current "${TEMP_DIR}/api.current.json" \
    --manifest "${MANIFEST_PATH}" \
    --registry-id "${REGISTRY_ID}" \
    --region "${REGION}" \
    --environment "${ENVIRONMENT}" \
    --repository "${PROMOTION_REPOSITORY}" \
    --migration-secret-reference "${migration_reference}" \
    --output "${TEMP_DIR}/schema-bootstrap.register.json" >/dev/null
  aws ecs describe-services --region "${REGION}" --cluster "${CLUSTER}" \
    --services "${API_SERVICE}" \
    --query 'services[0].networkConfiguration' \
    --output json >"${TEMP_DIR}/schema-bootstrap.network.json"
  [[ "$(jq -r '.awsvpcConfiguration.subnets | length' "${TEMP_DIR}/schema-bootstrap.network.json")" -gt 0 ]] \
    || fail "API service network configuration is unavailable for schema bootstrap"
  SCHEMA_BOOTSTRAP_STATUS="validated"
}

schema_is_current() {
  local expected_revision health_json observed_revision dsn_reference runtime_dsn
  expected_revision="$(jq -r '.schema_revision' "${MANIFEST_PATH}")"
  health_json="$(curl -fsS "${BASE_URL}/health/release" 2>/dev/null)" || return 1
  observed_revision="$(jq -r '.provenance.schema_revision // .schema_revision // ""' <<<"${health_json}")"
  [[ -n "${expected_revision}" && "${observed_revision}" = "${expected_revision}" ]] || return 1
  dsn_reference="$(jq -r '.taskDefinition.containerDefinitions[] | select(.name == "api") | .secrets[] | select(.name == "AUTOMATION_DB_DSN") | .valueFrom' "${TEMP_DIR}/api.current.json")"
  [[ -n "${dsn_reference}" && "${dsn_reference}" != "null" ]] || return 1
  runtime_dsn="$(read_secret_value "${dsn_reference}")" || return 1
  AUTOMATION_ENVIRONMENT="${ENVIRONMENT}" \
    AUTOMATION_DB_DSN="${runtime_dsn}" \
    AUTOMATION_DB_MIGRATION_DSN="${runtime_dsn}" \
    AUTOMATION_DB_SCHEMA="${PROMPT_TARGET_SCHEMA}" \
    AUTOMATION_DB_RESOURCE_ID="supportportal-${ENVIRONMENT}" \
    AUTOMATION_JOB_NAMESPACE="supportportal-${ENVIRONMENT}" \
    TICKET_DB_DSN="${runtime_dsn}" \
    TICKET_DB_SCHEMA="${PROMPT_TARGET_SCHEMA}" \
    "${PYTHON_BIN}" -m backend.scripts.automation_ecs_bootstrap check >/dev/null 2>&1
  local status=$?
  unset runtime_dsn
  return "${status}"
}

reconcile_schema_bootstrap_checkpoint() {
  [[ "${RESUME}" = "1" ]] || return 0
  local task_path definition_path previous_task previous_definition definition_family
  local task_json task_status task_definition
  task_path="${TEMP_DIR}/schema-bootstrap.task-arn"
  definition_path="${TEMP_DIR}/schema-bootstrap.task-definition-arn"
  previous_task="$(read_optional_file "${task_path}")"
  previous_definition="$(read_optional_file "${definition_path}")"
  [[ -n "${previous_task}" || -n "${previous_definition}" ]] || return 0
  [[ -n "${previous_definition}" ]] \
    || fail "Schema bootstrap checkpoint has a task without its task definition"
  definition_family="$(aws ecs describe-task-definition --region "${REGION}" \
    --task-definition "${previous_definition}" \
    --query 'taskDefinition.family' --output text)"
  [[ "${definition_family}" = "supportportal-${ENVIRONMENT}-schema-bootstrap" ]] \
    || fail "Schema bootstrap checkpoint task definition family mismatch"
  if [[ -n "${previous_task}" ]]; then
    task_json="$(aws ecs describe-tasks --region "${REGION}" --cluster "${CLUSTER}" \
      --tasks "${previous_task}")"
    task_status="$(jq -r '.tasks[0].lastStatus // ""' <<<"${task_json}")"
    task_definition="$(jq -r '.tasks[0].taskDefinitionArn // ""' <<<"${task_json}")"
    [[ -z "${task_definition}" || "${task_definition}" = "${previous_definition}" ]] \
      || fail "Schema bootstrap checkpoint task definition mismatch"
    if [[ "${task_status}" = "PENDING" || "${task_status}" = "RUNNING" ]]; then
      verify_aws_credential_lifetime
      aws ecs stop-task --region "${REGION}" --cluster "${CLUSTER}" \
        --task "${previous_task}" \
        --reason "schema bootstrap superseded by validated deploy resume" >/dev/null
      aws ecs wait tasks-stopped --region "${REGION}" --cluster "${CLUSTER}" \
        --tasks "${previous_task}"
    fi
  fi
  aws ecs deregister-task-definition --region "${REGION}" \
    --task-definition "${previous_definition}" >/dev/null
  rm -f -- "${task_path}" "${definition_path}" "${TEMP_DIR}/schema-bootstrap.task.json"
  log "Reconciled previous schema bootstrap checkpoint before retry"
}

run_schema_bootstrap() {
  [[ "${BOOTSTRAP_ACCOUNT_SCHEMA}" = "1" ]] || return 0
  [[ "${SKIP_SCHEMA_BOOTSTRAP}" = "0" ]] || return 0
  local tags_path task_result_path exit_code reason
  local -a register_args
  reconcile_schema_bootstrap_checkpoint
  tags_path="${TEMP_DIR}/schema-bootstrap.tags.json"
  jq '.tags // []' "${TEMP_DIR}/api.current.json" >"${tags_path}"
  register_args=(
    --region "${REGION}"
    --cli-input-json "file://${TEMP_DIR}/schema-bootstrap.register.json"
    --query 'taskDefinition.taskDefinitionArn'
    --output text
  )
  if [[ "$(jq 'length' "${tags_path}")" -gt 0 ]]; then
    register_args+=(--tags "file://${tags_path}")
  fi
  verify_aws_credential_lifetime
  SCHEMA_BOOTSTRAP_TASK_DEFINITION="$(aws ecs register-task-definition "${register_args[@]}")"
  [[ -n "${SCHEMA_BOOTSTRAP_TASK_DEFINITION}" && "${SCHEMA_BOOTSTRAP_TASK_DEFINITION}" != "None" ]] \
    || fail "Schema bootstrap task definition registration failed"
  printf '%s\n' "${SCHEMA_BOOTSTRAP_TASK_DEFINITION}" >"${TEMP_DIR}/schema-bootstrap.task-definition-arn"
  SCHEMA_BOOTSTRAP_TASK_ARN="$(aws ecs run-task --region "${REGION}" \
    --cluster "${CLUSTER}" --launch-type FARGATE \
    --task-definition "${SCHEMA_BOOTSTRAP_TASK_DEFINITION}" \
    --network-configuration "file://${TEMP_DIR}/schema-bootstrap.network.json" \
    --count 1 --query 'tasks[0].taskArn' --output text)"
  [[ -n "${SCHEMA_BOOTSTRAP_TASK_ARN}" && "${SCHEMA_BOOTSTRAP_TASK_ARN}" != "None" ]] \
    || fail "Schema bootstrap task did not start"
  printf '%s\n' "${SCHEMA_BOOTSTRAP_TASK_ARN}" >"${TEMP_DIR}/schema-bootstrap.task-arn"
  aws ecs wait tasks-stopped --region "${REGION}" --cluster "${CLUSTER}" \
    --tasks "${SCHEMA_BOOTSTRAP_TASK_ARN}"
  task_result_path="${TEMP_DIR}/schema-bootstrap.task.json"
  aws ecs describe-tasks --region "${REGION}" --cluster "${CLUSTER}" \
    --tasks "${SCHEMA_BOOTSTRAP_TASK_ARN}" >"${task_result_path}"
  exit_code="$(jq -r '.tasks[0].containers[] | select(.name == "api") | .exitCode // empty' "${task_result_path}")"
  if [[ "${exit_code}" != "0" ]]; then
    reason="$(jq -r '.tasks[0].stoppedReason // .tasks[0].containers[0].reason // "unknown"' "${task_result_path}")"
    fail "Schema bootstrap task failed (exit=${exit_code:-unknown}, reason=${reason})"
    return 1
  fi
  SCHEMA_BOOTSTRAP_STATUS="passed"
  SCHEMA_BOOTSTRAP_TASK_ARN=""
  aws ecs deregister-task-definition --region "${REGION}" \
    --task-definition "${SCHEMA_BOOTSTRAP_TASK_DEFINITION}" >/dev/null
  SCHEMA_BOOTSTRAP_TASK_DEFINITION=""
  log "${ENVIRONMENT} Account schema bootstrap passed"
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

reuse_registered_task_definition() {
  local role="$1" arn registered_path rendered_path expected_path actual_path
  [[ "${RESUME}" = "1" && -f "${TEMP_DIR}/${role}.new-arn" && -f "${TEMP_DIR}/${role}.register.json" ]] || return 1
  arn="$(<"${TEMP_DIR}/${role}.new-arn")"
  registered_path="${TEMP_DIR}/${role}.registered.json"
  rendered_path="${TEMP_DIR}/${role}.registered-rendered.json"
  expected_path="${TEMP_DIR}/${role}.register.normalized.json"
  actual_path="${TEMP_DIR}/${role}.registered-rendered.normalized.json"
  aws ecs describe-task-definition --region "${REGION}" --task-definition "${arn}" \
    --include TAGS >"${registered_path}" || return 1
  [[ "$(jq -r '.taskDefinition.status // ""' "${registered_path}")" = "ACTIVE" ]] || return 1
  render_role_task_definition "${role}" "${registered_path}" "${rendered_path}" || return 1
  jq -S . "${TEMP_DIR}/${role}.register.json" >"${expected_path}"
  jq -S . "${rendered_path}" >"${actual_path}"
  cmp -s "${expected_path}" "${actual_path}" || return 1
  log "Reusing validated ${role} task definition: ${arn}"
  return 0
}

register_task_definition() {
  local role="$1" tags_path new_arn
  local -a register_args
  if reuse_registered_task_definition "${role}"; then
    return 0
  fi
  tags_path="${TEMP_DIR}/${role}.tags.json"
  jq '.tags // []' "${TEMP_DIR}/${role}.current.json" >"${tags_path}"
  register_args=(
    --region "${REGION}"
    --cli-input-json "file://${TEMP_DIR}/${role}.register.json"
    --query 'taskDefinition.taskDefinitionArn'
    --output text
  )
  if [[ "$(jq 'length' "${tags_path}")" -gt 0 ]]; then
    register_args+=(--tags "file://${tags_path}")
  fi
  verify_aws_credential_lifetime
  new_arn="$(aws ecs register-task-definition "${register_args[@]}")"
  [[ -n "${new_arn}" && "${new_arn}" != "None" ]] || fail "${role} task definition registration failed"
  printf '%s\n' "${new_arn}" >"${TEMP_DIR}/${role}.new-arn"
}

update_role_if_needed() {
  local role="$1" service new_arn expected_digest
  service="$(service_name "${role}")"
  new_arn="$(<"${TEMP_DIR}/${role}.new-arn")"
  expected_digest="$(jq -r --arg role "${role}" '.components[$role].digest' "${MANIFEST_PATH}")"
  if verify_running_task "${role}" "${service}" "${new_arn}" "${expected_digest}" >/dev/null 2>&1; then
    add_updated_role "${role}"
    printf 'passed\n' >"${TEMP_DIR}/${role}.verified"
    log "${role} already runs the checkpoint revision and digest"
    return 0
  fi
  verify_aws_credential_lifetime
  aws ecs update-service --region "${REGION}" --cluster "${CLUSTER}" \
    --service "${service}" --task-definition "${new_arn}" >/dev/null
  add_updated_role "${role}"
  rm -f -- "${TEMP_DIR}/${role}.verified"
}

verify_role_and_record() {
  local role="$1" service new_arn expected_digest
  service="$(service_name "${role}")"
  new_arn="$(<"${TEMP_DIR}/${role}.new-arn")"
  expected_digest="$(jq -r --arg role "${role}" '.components[$role].digest' "${MANIFEST_PATH}")"
  verify_running_task "${role}" "${service}" "${new_arn}" "${expected_digest}"
  printf 'passed\n' >"${TEMP_DIR}/${role}.verified"
}

verify_cloudwatch() {
  local start_ms="$1" role group count
  for role in api route worker; do
    group="$(jq -r --arg role "${role}" '.taskDefinition.containerDefinitions[] | select(.name == $role) | .logConfiguration.options["awslogs-group"]' "${TEMP_DIR}/${role}.current.json")"
    [[ -n "${group}" && "${group}" != "null" ]] || fail "${role} CloudWatch log group is missing"
    count="$(aws logs filter-log-events --region "${REGION}" --log-group-name "${group}" \
      --log-stream-name-prefix "${role}/${role}/" --start-time "${start_ms}" \
      --filter-pattern '?ERROR ?Traceback ?Exception' \
      --query 'length(events)' --output text)"
    [[ "${count}" = "0" ]] || fail "${role} CloudWatch errors detected after deployment"
  done
}

verify_public_runtime() {
  local release_json ready_json
  curl -fsS "${BASE_URL}/health/live" >/dev/null
  release_json="$(curl -fsS "${BASE_URL}/health/release")"
  ready_json="$(curl -fsS "${BASE_URL}/health/ready")"
  [[ "$(jq -r '.status' <<<"${ready_json}")" = "ok" ]] || fail "ECS readiness check failed"
  [[ "$(jq -r '.provenance.release_id' <<<"${release_json}")" = "${RELEASE_ID}" ]] || fail "ECS release id mismatch"
  [[ "$(jq -r '.provenance.git_commit' <<<"${release_json}")" = "${GIT_COMMIT}" ]] || fail "ECS Git commit mismatch"
  [[ "$(jq -r '.provenance.prompt_release_id' <<<"${release_json}")" = "${PROMPT_RELEASE_ID}" ]] || fail "ECS Prompt Release mismatch"
  [[ "$(jq -r '.provenance.build_time' <<<"${release_json}")" = "${BUILD_TIME}" ]] || fail "ECS build time mismatch"
  if [[ -n "${HERMES_CASE_WORKFLOW_MODE}" ]]; then
    [[ "$(jq -r '.hermes_case_workflow.mode // ""' <<<"${release_json}")" = "${HERMES_CASE_WORKFLOW_MODE}" ]] \
      || fail "ECS Hermes Case Workflow mode mismatch"
  fi
}

run_parallel_post_deploy_checks() {
  local deployment_start_ms="$1" failed=0 public_pid cloudwatch_pid backup_pid
  (verify_public_runtime) >"${TEMP_DIR}/public-health.log" 2>&1 &
  public_pid=$!
  (verify_cloudwatch "${deployment_start_ms}") >"${TEMP_DIR}/cloudwatch.log" 2>&1 &
  cloudwatch_pid=$!
  (curl -fsS "${EC2_BACKUP_URL}" >/dev/null) >"${TEMP_DIR}/ec2-backup.log" 2>&1 &
  backup_pid=$!

  if wait "${public_pid}"; then
    PUBLIC_HEALTH_STATUS="passed"
  else
    PUBLIC_HEALTH_STATUS="failed"
    sed -n '1,80p' "${TEMP_DIR}/public-health.log" >&2
    failed=1
  fi
  if wait "${cloudwatch_pid}"; then
    CLOUDWATCH_STATUS="passed"
  else
    CLOUDWATCH_STATUS="failed"
    sed -n '1,80p' "${TEMP_DIR}/cloudwatch.log" >&2
    failed=1
  fi
  if wait "${backup_pid}"; then
    EC2_BACKUP_STATUS="passed"
  else
    EC2_BACKUP_STATUS="failed"
    sed -n '1,80p' "${TEMP_DIR}/ec2-backup.log" >&2
    failed=1
  fi
  [[ "${failed}" = "0" ]] || fail "One or more post-deploy read-only checks failed"
}

wait_for_heartbeats() {
  local deadline
  deadline=$((SECONDS + HEARTBEAT_WAIT_TIMEOUT_SECONDS))
  while true; do
    if "${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy verify-heartbeats \
      --manifest "${MANIFEST_PATH}" --task-definition "${TEMP_DIR}/worker.register.json" \
      --max-age-seconds 90 --environment "${ENVIRONMENT}" >/dev/null 2>&1; then
      return 0
    fi
    if ((SECONDS >= deadline)); then
      fail "Route/Worker heartbeat provenance did not converge within ${HEARTBEAT_WAIT_TIMEOUT_SECONDS}s"
      return 1
    fi
    sleep "${HEARTBEAT_RETRY_INTERVAL_SECONDS}"
  done
}

main() {
  parse_args "$@"
  trap cleanup EXIT
  if [[ "${ENVIRONMENT}" = "production" ]]; then
    [[ -n "${PROMOTION_RECORD}" && -z "${PUBLISH_RECORD}" ]] \
      || fail "Production deploy requires only --promotion-record"
    DEPLOY_RECORD="${PROMOTION_RECORD}"
  else
    [[ -n "${PUBLISH_RECORD}" && -z "${PROMOTION_RECORD}" ]] \
      || fail "Preproduction deploy requires only --publish-record"
    DEPLOY_RECORD="${PUBLISH_RECORD}"
  fi
  [[ -n "${MANIFEST_PATH}" && -f "${MANIFEST_PATH}" ]] || fail "Release Manifest is required"
  [[ -n "${DEPLOY_RECORD}" && -f "${DEPLOY_RECORD}" ]] || fail "Environment deployment record is required"
  [[ -n "${REGION}" ]] || fail "AWS region is required"
  for command in aws cmp curl git jq; do command -v "${command}" >/dev/null 2>&1 || fail "Missing command: ${command}"; done
  command -v "${TERRAFORM_BIN}" >/dev/null 2>&1 || [[ -x "${TERRAFORM_BIN}" ]] || fail "Terraform runtime is required"
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || [[ -x "${PYTHON_BIN}" ]] || fail "Python runtime is required"
  [[ -n "${TICKET_DB_DSN:-}" ]] || fail "TICKET_DB_DSN is required"
  if [[ "${CHECK_ONLY}" = "0" ]]; then
    if [[ "${ENVIRONMENT}" = "production" ]]; then
      [[ "${DEPLOY_PRODUCTION_APPROVED:-}" = "1" ]] || fail "DEPLOY_PRODUCTION_APPROVED=1 is required"
    else
      [[ "${DEPLOY_PREPRODUCTION_APPROVED:-}" = "1" ]] || fail "DEPLOY_PREPRODUCTION_APPROVED=1 is required"
    fi
    [[ -n "${PROMPT_RELEASE_TARGET_DSN:-}" ]] || fail "PROMPT_RELEASE_TARGET_DSN is required"
  fi

  MANIFEST_PATH="$(cd -- "$(dirname -- "${MANIFEST_PATH}")" && pwd)/$(basename -- "${MANIFEST_PATH}")"
  DEPLOY_RECORD="$(cd -- "$(dirname -- "${DEPLOY_RECORD}")" && pwd)/$(basename -- "${DEPLOY_RECORD}")"

  "${PYTHON_BIN}" -m backend.scripts.automation_release validate --manifest "${MANIFEST_PATH}" >/dev/null
  local promotion_json record_region
  if [[ "${ENVIRONMENT}" = "production" ]]; then
    promotion_json="$("${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy validate-promotion \
      --manifest "${MANIFEST_PATH}" --promotion-record "${DEPLOY_RECORD}")"
  else
    promotion_json="$("${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy validate-preproduction-publish \
      --manifest "${MANIFEST_PATH}" --publish-record "${DEPLOY_RECORD}")"
  fi
  REGISTRY_ID="$(jq -r '.registry_id' <<<"${promotion_json}")"
  record_region="$(jq -r '.region' <<<"${promotion_json}")"
  PROMOTION_REPOSITORY="$(jq -r '.repository' <<<"${promotion_json}")"
  [[ "${record_region}" = "${REGION}" ]] || fail "Promotion Record region mismatch"
  [[ "${PROMOTION_REPOSITORY}" = "supportportal/${ENVIRONMENT}" ]] || fail "Deployment record repository does not match environment"
  RELEASE_ID="$(jq -r '.release_id' "${MANIFEST_PATH}")"
  GIT_COMMIT="$(jq -r '.git_commit' "${MANIFEST_PATH}")"
  PROMPT_RELEASE_ID="$(jq -r '.prompt_release_id' "${MANIFEST_PATH}")"
  BUILD_TIME="$(jq -r '.build_time' "${MANIFEST_PATH}")"
  [[ "$(git -C "${PROJECT_ROOT}" rev-parse HEAD)" = "${GIT_COMMIT}" ]] || fail "Manifest Git commit is not current HEAD"
  [[ -z "$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=no)" ]] || fail "Working tree has tracked changes"
  prepare_deploy_workspace
  verify_aws_credential_lifetime

  run_terraform_zero_plan
  TICKET_DB_DSN="${TICKET_DB_DSN:-}" TICKET_DB_SCHEMA="${TICKET_DB_SCHEMA:-supportportal}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release validate --release-id "${PROMPT_RELEASE_ID}" >/dev/null
  SOURCE_PROMPT_STATUS="passed"

  local role service current_arn baseline_arn expected_digest tag observed_digest
  for role in api route worker; do
    expected_digest="$(jq -r --arg role "${role}" '.components[$role].digest' "${MANIFEST_PATH}")"
    tag="$(jq -r --arg role "${role}" '.components[$role].tag' "${MANIFEST_PATH}")"
    observed_digest="$(aws ecr describe-images --region "${REGION}" --registry-id "${REGISTRY_ID}" \
      --repository-name "${PROMOTION_REPOSITORY}" --image-ids "imageTag=${tag}" \
      --query 'imageDetails[0].imageDigest' --output text)"
    [[ "${observed_digest}" = "${expected_digest}" ]] || fail "${role} ECR digest mismatch"
    service="$(service_name "${role}")"
    current_arn="$(aws ecs describe-services --region "${REGION}" --cluster "${CLUSTER}" \
      --services "${service}" --query 'services[0].taskDefinition' --output text)"
    [[ -n "${current_arn}" && "${current_arn}" != "None" ]] || fail "${role} service/task definition not found"
    if [[ "${RESUME}" = "1" && -f "${TEMP_DIR}/${role}.old-arn" ]]; then
      baseline_arn="$(<"${TEMP_DIR}/${role}.old-arn")"
    else
      baseline_arn="${current_arn}"
      printf '%s\n' "${baseline_arn}" >"${TEMP_DIR}/${role}.old-arn"
    fi
    aws ecs describe-task-definition --region "${REGION}" --task-definition "${baseline_arn}" \
      --include TAGS >"${TEMP_DIR}/${role}.current.json"
    render_role_task_definition \
      "${role}" "${TEMP_DIR}/${role}.current.json" "${TEMP_DIR}/${role}.register.json"
  done
  ECR_STATUS="passed"
  prepare_schema_bootstrap

  local suspension_reference suspension_recipients_json
  suspension_reference="$(jq -r '.taskDefinition.containerDefinitions[] | select(.name == "worker") | .secrets[] | select(.name == "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON") | .valueFrom' "${TEMP_DIR}/worker.current.json")"
  suspension_recipients_json="$(read_secret_value "${suspension_reference}")"
  ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON="${suspension_recipients_json}" \
    "${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy \
      validate-suspension-recipients >/dev/null
  unset suspension_recipients_json
  SUSPENSION_RECIPIENTS_STATUS="passed"

  curl -fsS "${EC2_BACKUP_URL}" >/dev/null || fail "EC2 /production backup health check failed"
  EC2_BACKUP_STATUS="passed"
  if [[ "${CHECK_ONLY}" = "1" ]]; then
    log "Check-only passed; no schema, Prompt Release, task definition, or ECS service was changed"
    return 0
  fi

  # A fresh Preproduction identity has an empty schema. Create the runtime
  # tables before attempting to persist the target Prompt Release candidate.
  # No ECS service has been updated at this point.
  run_schema_bootstrap

  PROMPT_RELEASE_TARGET_DSN="${PROMPT_RELEASE_TARGET_DSN}" \
    PROMPT_RELEASE_TARGET_SCHEMA="${PROMPT_TARGET_SCHEMA}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release sync \
      --release-id "${PROMPT_RELEASE_ID}" --defer-activation >"${TEMP_DIR}/prompt-sync.json"
  PROMPT_SYNC_STATUS="$(jq -r '.sync.status' "${TEMP_DIR}/prompt-sync.json")"
  [[ "${PROMPT_SYNC_STATUS}" = "candidate" || "${PROMPT_SYNC_STATUS}" = "active" ]] \
    || fail "Target Prompt Release is not deployable"
  TICKET_DB_DSN="${PROMPT_RELEASE_TARGET_DSN}" \
    TICKET_DB_SCHEMA="${PROMPT_TARGET_SCHEMA}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release validate \
      --release-id "${PROMPT_RELEASE_ID}" >/dev/null
  log "Target Prompt Release activation preflight passed without schema initialization"
  if [[ "${PROMPT_SYNC_STATUS}" = "active" ]]; then
    PROMPT_ACTIVATION_STATUS="active"
    log "Target Prompt Release was already active before deployment; pre-activation failures remain rollback-safe"
  fi

  for role in api route worker; do
    register_task_definition "${role}"
  done

  local deployment_start_ms
  if [[ "${RESUME}" = "1" && -f "${TEMP_DIR}/deployment-start-ms" ]]; then
    deployment_start_ms="$(<"${TEMP_DIR}/deployment-start-ms")"
  else
    deployment_start_ms="$(($(date -u +%s) * 1000))"
    printf '%s\n' "${deployment_start_ms}" >"${TEMP_DIR}/deployment-start-ms"
  fi
  DEPLOY_STARTED=1
  for role in route worker; do
    update_role_if_needed "${role}"
  done
  verify_aws_credential_lifetime
  for role in route worker; do
    wait_for_service_revision \
      "${role}" "$(service_name "${role}")" "$(<"${TEMP_DIR}/${role}.new-arn")"
    verify_role_and_record "${role}"
  done

  local dsn_reference heartbeat_dsn
  dsn_reference="$(jq -r '.taskDefinition.containerDefinitions[] | select(.name == "worker") | .secrets[] | select(.name == "AUTOMATION_DB_DSN") | .valueFrom' "${TEMP_DIR}/worker.current.json")"
  heartbeat_dsn="$(read_secret_value "${dsn_reference}")"
  AUTOMATION_HEARTBEAT_DSN="${heartbeat_dsn}" wait_for_heartbeats
  unset heartbeat_dsn
  HEARTBEAT_STATUS="passed"

  update_role_if_needed api
  verify_aws_credential_lifetime
  wait_for_service_revision api "${API_SERVICE}" "$(<"${TEMP_DIR}/api.new-arn")"
  verify_role_and_record api
  run_parallel_post_deploy_checks "${deployment_start_ms}"

  verify_aws_credential_lifetime
  ACTIVATION_STARTED=1
  TICKET_DB_DSN="${PROMPT_RELEASE_TARGET_DSN}" \
    TICKET_DB_SCHEMA="${PROMPT_TARGET_SCHEMA}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release activate \
      --release-id "${PROMPT_RELEASE_ID}" >"${TEMP_DIR}/prompt-activate.json"
  TICKET_DB_DSN="${PROMPT_RELEASE_TARGET_DSN}" \
    TICKET_DB_SCHEMA="${PROMPT_TARGET_SCHEMA}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release validate \
      --release-id "${PROMPT_RELEASE_ID}" >/dev/null
  [[ "$(jq -r '.release.status' "${TEMP_DIR}/prompt-activate.json")" = "active" ]] \
    || fail "Target Prompt Release activation readback failed"
  PROMPT_SYNC_STATUS="active"
  PROMPT_ACTIVATION_STATUS="active"
  DEPLOY_COMPLETE=1
  write_evidence "complete"
  prune_success_artifacts
  log "Deployment verified and target Prompt Release activated: ${RELEASE_ID}"
  log "Release evidence: ${STATE_DIR}/evidence.json"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
