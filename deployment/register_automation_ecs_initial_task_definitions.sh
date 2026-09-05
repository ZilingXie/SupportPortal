#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${AUTOMATION_RELEASE_PYTHON:-python3}"
TERRAFORM_BIN="${AUTOMATION_TERRAFORM_BIN:-terraform}"
TERRAFORM_DIR="${AUTOMATION_TERRAFORM_DIR:-${PROJECT_ROOT}/infra/terraform/preproduction}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
MANIFEST_PATH=""
PUBLISH_RECORD=""
OUTPUT_DIR=""
HERMES_MODE="disabled"
CHECK_ONLY=0

log() { printf '[ecs-initial-task-definitions] %s\n' "$*"; }
fail() { printf '[ecs-initial-task-definitions] ERROR: %s\n' "$*" >&2; return 1; }

usage() {
  cat <<'EOF'
Usage:
  ./deployment/register_automation_ecs_initial_task_definitions.sh \
    --manifest <release-manifest.json> \
    --publish-record <preproduction-publish-record.json> \
    [--terraform-dir <path>] [--output-dir <path>] [--check-only]

The mutating mode requires AUTOMATION_INITIAL_TASK_DEFINITIONS_APPROVED=1.
It registers canonical Preproduction API, Route, and Worker task definitions,
but never creates or updates an ECS service. Output contains no secret values.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --manifest) [[ $# -ge 2 ]] || fail "--manifest requires a value"; MANIFEST_PATH="$2"; shift 2 ;;
      --publish-record) [[ $# -ge 2 ]] || fail "--publish-record requires a value"; PUBLISH_RECORD="$2"; shift 2 ;;
      --terraform-dir) [[ $# -ge 2 ]] || fail "--terraform-dir requires a value"; TERRAFORM_DIR="$2"; shift 2 ;;
      --output-dir) [[ $# -ge 2 ]] || fail "--output-dir requires a value"; OUTPUT_DIR="$2"; shift 2 ;;
      --check-only) CHECK_ONLY=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown option: $1"; return 1 ;;
    esac
  done
}

main() {
  parse_args "$@"
  [[ -n "${MANIFEST_PATH}" && -f "${MANIFEST_PATH}" ]] || fail "Release Manifest is required"
  [[ -n "${PUBLISH_RECORD}" && -f "${PUBLISH_RECORD}" ]] || fail "Preproduction Publish Record is required"
  for command in aws git jq; do command -v "${command}" >/dev/null 2>&1 || fail "Missing command: ${command}"; done
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || [[ -x "${PYTHON_BIN}" ]] || fail "Python runtime is required"
  command -v "${TERRAFORM_BIN}" >/dev/null 2>&1 || [[ -x "${TERRAFORM_BIN}" ]] || fail "Terraform runtime is required"
  [[ -z "$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=no)" ]] || fail "Working tree has tracked changes"

  local record_json registry_id repository git_commit release_id bootstrap_json
  record_json="$("${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy \
    validate-preproduction-publish --manifest "${MANIFEST_PATH}" --publish-record "${PUBLISH_RECORD}")"
  registry_id="$(jq -r '.registry_id' <<<"${record_json}")"
  repository="$(jq -r '.repository' <<<"${record_json}")"
  [[ "${repository}" = "supportportal/preproduction" ]] || fail "Publish Record repository mismatch"
  git_commit="$(jq -r '.git_commit' "${MANIFEST_PATH}")"
  release_id="$(jq -r '.release_id' "${MANIFEST_PATH}")"
  [[ "$(git -C "${PROJECT_ROOT}" rev-parse HEAD)" = "${git_commit}" ]] || fail "Manifest Git commit is not current HEAD"
  aws sts get-caller-identity --region "${REGION}" >/dev/null

  bootstrap_json="$("${TERRAFORM_BIN}" -chdir="${TERRAFORM_DIR}" output -json bootstrap_contract | jq -c '.value // .')"
  [[ "$(jq -r '.environment' <<<"${bootstrap_json}")" = "preproduction" ]] || fail "Terraform bootstrap output environment mismatch"
  [[ "$(jq -r '.repository_name' <<<"${bootstrap_json}")" = "${repository}" ]] || fail "Terraform repository output mismatch"

  OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/.deployments/initial-${release_id}}"
  [[ ! -e "${OUTPUT_DIR}" ]] || fail "Output directory already exists: ${OUTPUT_DIR}"
  mkdir -p -- "${OUTPUT_DIR}"
  chmod 700 "${OUTPUT_DIR}"

  local role tag expected_digest observed_digest
  local -a common_args
  for role in api route worker; do
    tag="$(jq -r --arg role "${role}" '.components[$role].tag' "${MANIFEST_PATH}")"
    expected_digest="$(jq -r --arg role "${role}" '.components[$role].digest' "${MANIFEST_PATH}")"
    observed_digest="$(aws ecr describe-images --region "${REGION}" --registry-id "${registry_id}" \
      --repository-name "${repository}" --image-ids "imageTag=${tag}" \
      --query 'imageDetails[0].imageDigest' --output text)"
    [[ "${observed_digest}" = "${expected_digest}" ]] || fail "${role} ECR digest mismatch"
    common_args=(
      --role "${role}"
      --manifest "${MANIFEST_PATH}"
      --registry-id "${registry_id}"
      --region "${REGION}"
      --environment preproduction
      --repository "${repository}"
      --execution-role-arn "$(jq -r '.execution_role_arn' <<<"${bootstrap_json}")"
      --task-role-arn "$(jq -r '.task_role_arn' <<<"${bootstrap_json}")"
      --log-group-name "$(jq -r '.log_group_name' <<<"${bootstrap_json}")"
      --parameter-prefix-arn "$(jq -r '.parameter_prefix_arn' <<<"${bootstrap_json}")"
      --hermes-case-workflow-mode "${HERMES_MODE}"
      --output "${OUTPUT_DIR}/${role}.register.json"
    )
    if [[ "${role}" = "worker" ]]; then
      common_args+=(
        --graph-efs-file-system-id "$(jq -r '.graph_efs_file_system_id' <<<"${bootstrap_json}")"
        --graph-efs-access-point-id "$(jq -r '.graph_efs_access_point_id' <<<"${bootstrap_json}")"
      )
    fi
    "${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy \
      render-initial-task-definition "${common_args[@]}" >/dev/null
  done

  if [[ "${CHECK_ONLY}" = "1" ]]; then
    log "Check-only passed; canonical definitions rendered and no AWS resource was changed"
    return 0
  fi
  [[ "${AUTOMATION_INITIAL_TASK_DEFINITIONS_APPROVED:-}" = "1" ]] \
    || fail "AUTOMATION_INITIAL_TASK_DEFINITIONS_APPROVED=1 is required"

  local services_json active_count arn arns_json
  services_json="$(aws ecs describe-services --region "${REGION}" \
    --cluster "$(jq -r '.cluster_name' <<<"${bootstrap_json}")" \
    --services supportportal-preproduction-api supportportal-preproduction-route supportportal-preproduction-worker)"
  active_count="$(jq '[.services[] | select(.status == "ACTIVE")] | length' <<<"${services_json}")"
  [[ "${active_count}" = "0" ]] || fail "Initial registration is forbidden after a Preproduction Account service exists"

  arns_json='{}'
  for role in api route worker; do
    arn="$(aws ecs register-task-definition --region "${REGION}" \
      --cli-input-json "file://${OUTPUT_DIR}/${role}.register.json" \
      --tags key=Project,value=supportportal key=Environment,value=preproduction key=Owner,value=zac key=System,value=automation key=Component,value="${role}" \
      --query 'taskDefinition.taskDefinitionArn' --output text)"
    [[ -n "${arn}" && "${arn}" != "None" ]] || fail "${role} task definition registration failed"
    arns_json="$(jq -c --arg role "${role}" --arg arn "${arn}" '. + {($role):$arn}' <<<"${arns_json}")"
  done
  jq -n -S --argjson arns "${arns_json}" \
    '{create_account_services:true,account_task_definition_arns:$arns}' \
    >"${OUTPUT_DIR}/account-services.auto.tfvars.json"
  jq -n -S --arg schema_version "automation-initial-task-definitions-v1" \
    --arg environment "preproduction" --arg release_id "${release_id}" \
    --arg git_commit "${git_commit}" --arg repository "${repository}" \
    --argjson task_definitions "${arns_json}" \
    '{schema_version:$schema_version,environment:$environment,release_id:$release_id,git_commit:$git_commit,repository:$repository,task_definitions:$task_definitions}' \
    >"${OUTPUT_DIR}/evidence.json"
  log "Registered canonical Preproduction task definitions"
  log "Terraform variable file: ${OUTPUT_DIR}/account-services.auto.tfvars.json"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
