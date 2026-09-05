#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${AUTOMATION_RELEASE_PYTHON:-python3}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
CLUSTER="${AUTOMATION_ECS_CLUSTER:-supportportal-production}"
API_SERVICE="${AUTOMATION_ECS_API_SERVICE:-supportportal-production-api}"
WORKER_SERVICE="${AUTOMATION_ECS_WORKER_SERVICE:-supportportal-production-worker}"
BASE_URL="${AUTOMATION_ECS_BASE_URL:-https://supportcenter.stellarix.space/automation/production}"
CHECK_ONLY=0
TEMP_DIR=""
EVIDENCE_DIR=""
UPDATED_ROLES=()

log() { printf '[production-hermes-disable] %s\n' "$*"; }
fail() { printf '[production-hermes-disable] ERROR: %s\n' "$*" >&2; return 1; }

usage() {
  cat <<'EOF'
Usage: ./deployment/disable_production_hermes.sh [--check-only]

Clones the current Production API and Worker task definitions, preserves their
images and provenance, removes all Hermes endpoint/key/callback references, and
sets HERMES_CASE_WORKFLOW_MODE=disabled. The mutating mode requires
PRODUCTION_HERMES_DISABLE_APPROVED=1. It does not change Route, Prompt Releases,
databases, the Hermes ECS service, or persistent Hermes data.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --check-only) CHECK_ONLY=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown option: $1"; return 1 ;;
    esac
  done
}

service_name() {
  case "$1" in
    api) printf '%s\n' "${API_SERVICE}" ;;
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

rollback() {
  local index role old_arn failed=0
  [[ "${#UPDATED_ROLES[@]}" -gt 0 ]] || return 0
  log "Restoring captured Production task definitions"
  for ((index=${#UPDATED_ROLES[@]}-1; index>=0; index--)); do
    role="${UPDATED_ROLES[index]}"
    old_arn="$(<"${TEMP_DIR}/${role}.old-arn")"
    if ! aws ecs update-service --region "${REGION}" --cluster "${CLUSTER}" \
      --service "$(service_name "${role}")" --task-definition "${old_arn}" >/dev/null; then
      failed=1
    elif ! aws ecs wait services-stable --region "${REGION}" --cluster "${CLUSTER}" \
      --services "$(service_name "${role}")"; then
      failed=1
    fi
  done
  [[ "${failed}" = "0" ]]
}

cleanup() {
  local status=$?
  trap - EXIT
  if [[ ${status} -ne 0 && "${CHECK_ONLY}" = "0" ]]; then
    rollback || printf '[production-hermes-disable] ERROR: rollback incomplete; reconciliation required\n' >&2
  fi
  if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
    rm -rf -- "${TEMP_DIR}"
  fi
  exit "${status}"
}

capture_and_render() {
  local role="$1" service current_arn result
  service="$(service_name "${role}")"
  current_arn="$(aws ecs describe-services --region "${REGION}" --cluster "${CLUSTER}" \
    --services "${service}" --query 'services[0].taskDefinition' --output text)"
  [[ -n "${current_arn}" && "${current_arn}" != "None" ]] || fail "${role} service is missing"
  printf '%s\n' "${current_arn}" >"${TEMP_DIR}/${role}.old-arn"
  aws ecs describe-task-definition --region "${REGION}" --task-definition "${current_arn}" \
    --include TAGS >"${TEMP_DIR}/${role}.current.json"
  result="$("${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy \
    render-production-hermes-disabled-task-definition \
    --role "${role}" --current "${TEMP_DIR}/${role}.current.json" \
    --output "${TEMP_DIR}/${role}.register.json")"
  jq -r '.changed' <<<"${result}" >"${TEMP_DIR}/${role}.changed"
}

register_if_changed() {
  local role="$1" tags_path new_arn
  if [[ "$(<"${TEMP_DIR}/${role}.changed")" != "true" ]]; then
    cp -- "${TEMP_DIR}/${role}.old-arn" "${TEMP_DIR}/${role}.new-arn"
    log "${role} is already Hermes-disabled"
    return 0
  fi
  tags_path="${TEMP_DIR}/${role}.tags.json"
  jq '.tags // []' "${TEMP_DIR}/${role}.current.json" >"${tags_path}"
  local -a args=(
    --region "${REGION}"
    --cli-input-json "file://${TEMP_DIR}/${role}.register.json"
    --query 'taskDefinition.taskDefinitionArn'
    --output text
  )
  if [[ "$(jq 'length' "${tags_path}")" -gt 0 ]]; then
    args+=(--tags "file://${tags_path}")
  fi
  new_arn="$(aws ecs register-task-definition "${args[@]}")"
  [[ -n "${new_arn}" && "${new_arn}" != "None" ]] || fail "${role} registration failed"
  printf '%s\n' "${new_arn}" >"${TEMP_DIR}/${role}.new-arn"
}

update_and_verify() {
  local role="$1" service new_arn observed running_task running_definition
  [[ "$(<"${TEMP_DIR}/${role}.changed")" = "true" ]] || return 0
  service="$(service_name "${role}")"
  new_arn="$(<"${TEMP_DIR}/${role}.new-arn")"
  add_updated_role "${role}"
  aws ecs update-service --region "${REGION}" --cluster "${CLUSTER}" \
    --service "${service}" --task-definition "${new_arn}" >/dev/null
  aws ecs wait services-stable --region "${REGION}" --cluster "${CLUSTER}" \
    --services "${service}"
  observed="$(aws ecs describe-services --region "${REGION}" --cluster "${CLUSTER}" \
    --services "${service}" --query 'services[0].taskDefinition' --output text)"
  [[ "${observed}" = "${new_arn}" ]] || fail "${role} service revision mismatch"
  running_task="$(aws ecs list-tasks --region "${REGION}" --cluster "${CLUSTER}" \
    --service-name "${service}" --desired-status RUNNING --query 'taskArns[0]' --output text)"
  [[ -n "${running_task}" && "${running_task}" != "None" ]] || fail "${role} has no running task"
  running_definition="$(aws ecs describe-tasks --region "${REGION}" --cluster "${CLUSTER}" \
    --tasks "${running_task}" --query 'tasks[0].taskDefinitionArn' --output text)"
  [[ "${running_definition}" = "${new_arn}" ]] || fail "${role} running task revision mismatch"
}

write_evidence() {
  local output="$1"
  jq -n -S \
    --arg schema_version production-hermes-disable-v1 \
    --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg cluster "${CLUSTER}" \
    --arg api_old "$(<"${TEMP_DIR}/api.old-arn")" \
    --arg api_new "$(<"${TEMP_DIR}/api.new-arn")" \
    --arg worker_old "$(<"${TEMP_DIR}/worker.old-arn")" \
    --arg worker_new "$(<"${TEMP_DIR}/worker.new-arn")" \
    '{schema_version:$schema_version,generated_at:$generated_at,environment:"production",cluster:$cluster,hermes_mode:"disabled",components:{api:{old_task_definition:$api_old,new_task_definition:$api_new},worker:{old_task_definition:$worker_old,new_task_definition:$worker_new}}}' \
    >"${output}"
}

main() {
  parse_args "$@"
  trap cleanup EXIT
  for command in aws curl git jq "${PYTHON_BIN}"; do command -v "${command}" >/dev/null 2>&1 || fail "Missing command: ${command}"; done
  [[ -z "$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=no)" ]] || fail "Working tree has tracked changes"
  aws sts get-caller-identity --region "${REGION}" >/dev/null
  mkdir -p -- "${PROJECT_ROOT}/.deployments"
  TEMP_DIR="$(mktemp -d "${PROJECT_ROOT}/.deployments/production-hermes-disable.XXXXXX")"
  capture_and_render worker
  capture_and_render api
  if [[ "${CHECK_ONLY}" = "1" ]]; then
    log "Check-only passed; current images and provenance will be preserved"
    return 0
  fi
  [[ "${PRODUCTION_HERMES_DISABLE_APPROVED:-}" = "1" ]] \
    || fail "PRODUCTION_HERMES_DISABLE_APPROVED=1 is required"
  register_if_changed worker
  register_if_changed api
  update_and_verify worker
  update_and_verify api
  local release_json ready_json
  ready_json="$(curl -fsS "${BASE_URL}/health/ready")"
  release_json="$(curl -fsS "${BASE_URL}/health/release")"
  [[ "$(jq -r '.status' <<<"${ready_json}")" = "ok" ]] || fail "Production readiness failed"
  [[ "$(jq -r '.hermes_case_workflow.mode' <<<"${release_json}")" = "disabled" ]] \
    || fail "Production Hermes mode readback is not disabled"
  EVIDENCE_DIR="${PROJECT_ROOT}/.deployments/production-hermes-disabled-$(date -u +%Y%m%dT%H%M%SZ)"
  [[ ! -e "${EVIDENCE_DIR}" ]] || fail "Evidence directory already exists"
  mkdir -p -- "${EVIDENCE_DIR}"
  write_evidence "${EVIDENCE_DIR}/evidence.json"
  UPDATED_ROLES=()
  log "Production Account services are Hermes-disabled without changing images"
  log "Evidence: ${EVIDENCE_DIR}/evidence.json"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
