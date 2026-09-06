#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/lib/release_aws_provider.sh"
PYTHON_BIN="${AUTOMATION_RELEASE_PYTHON:-python3}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
PROJECT_NAME="${AUTOMATION_CODEBUILD_PROJECT_NAME:-supportportal-automation-release}"
EVIDENCE_BUCKET="${AUTOMATION_RELEASE_EVIDENCE_BUCKET:-}"
GIT_COMMIT=""
RELEASE_ID=""
PROMPT_RELEASE_ID=""
OUTPUT_DIR=""
REQUEST_DIR=""

log() { printf '[codebuild-trigger] %s\n' "$*"; }
fail() { printf '[codebuild-trigger] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  ./deployment/start_automation_codebuild_release.sh \
    --git-commit <full-sha> --prompt-release-id <id> [options]

Options:
  --release-id <id>    Default: rYYYYMMDD-<commit7>
  --output-dir <path>  Default: .deployments/releases/<release-id>

Validates the fixed commit and Prompt Release, submits a secret-free versioned
request to CodeBuild, waits for completion, and downloads Manifest v2 plus the
Preproduction Publish Record. It does not update ECS services.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --git-commit) [[ $# -ge 2 ]] || fail "--git-commit requires a value"; GIT_COMMIT="$2"; shift 2 ;;
      --release-id) [[ $# -ge 2 ]] || fail "--release-id requires a value"; RELEASE_ID="$2"; shift 2 ;;
      --prompt-release-id) [[ $# -ge 2 ]] || fail "--prompt-release-id requires a value"; PROMPT_RELEASE_ID="$2"; shift 2 ;;
      --output-dir) [[ $# -ge 2 ]] || fail "--output-dir requires a value"; OUTPUT_DIR="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown option: $1" ;;
    esac
  done
}

main() {
  parse_args "$@"
  for command in aws git jq "${PYTHON_BIN}"; do command -v "${command}" >/dev/null 2>&1 || fail "Missing command: ${command}"; done
  [[ "${GIT_COMMIT}" =~ ^[0-9a-f]{40}$ ]] || fail "--git-commit must be a full 40-character SHA"
  [[ "${PROMPT_RELEASE_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail "Invalid Prompt Release id"
  [[ -n "${TICKET_DB_DSN:-}" ]] || fail "TICKET_DB_DSN is required for source Prompt validation"
  RELEASE_ID="${RELEASE_ID:-r$(date -u +%Y%m%d)-${GIT_COMMIT:0:7}}"
  [[ "${RELEASE_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail "Invalid release id"
  [[ -n "${EVIDENCE_BUCKET}" ]] || fail "AUTOMATION_RELEASE_EVIDENCE_BUCKET is required"
  [[ "${REGION}" = "us-east-1" ]] || fail "AWS region must be us-east-1"
  [[ -z "$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=all)" ]] || fail "Working tree must be clean"
  git -C "${PROJECT_ROOT}" fetch origin main --quiet
  git -C "${PROJECT_ROOT}" cat-file -e "${GIT_COMMIT}^{commit}" || fail "Requested Git commit does not exist locally"
  git -C "${PROJECT_ROOT}" merge-base --is-ancestor "${GIT_COMMIT}" origin/main \
    || fail "Requested Git commit is not reachable from origin/main"

  local request_path validation prompt_build_ref prompt_fingerprint request_key request_version
  mkdir -p "${PROJECT_ROOT}/.deployments"
  REQUEST_DIR="$(mktemp -d "${PROJECT_ROOT}/.deployments/codebuild-request.XXXXXX")"
  trap 'rm -rf -- "${REQUEST_DIR}"' EXIT
  request_path="${REQUEST_DIR}/request.json"
  validation="$(cd "${PROJECT_ROOT}" && TICKET_DB_DSN="${TICKET_DB_DSN:-}" TICKET_DB_SCHEMA="${TICKET_DB_SCHEMA:-supportportal}" \
    "${PYTHON_BIN}" -m backend.scripts.prompt_release validate --release-id "${PROMPT_RELEASE_ID}")" \
    || fail "Prompt Release validation failed"
  prompt_build_ref="$(jq -r '.identity.build_ref // empty' <<<"${validation}")"
  prompt_fingerprint="$(jq -r '.identity.content_fingerprint // empty' <<<"${validation}")"
  [[ -n "${prompt_build_ref}" && "${prompt_fingerprint}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || fail "Prompt Release identity is incomplete"
  jq -n -S \
    --arg schema_version automation-codebuild-request-v1 \
    --arg release_id "${RELEASE_ID}" \
    --arg git_commit "${GIT_COMMIT}" \
    --arg prompt_release_id "${PROMPT_RELEASE_ID}" \
    --arg prompt_build_ref "${prompt_build_ref}" \
    --arg prompt_content_fingerprint "${prompt_fingerprint}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{schema_version:$schema_version,release_id:$release_id,git_commit:$git_commit,prompt_release_id:$prompt_release_id,prompt_build_ref:$prompt_build_ref,prompt_content_fingerprint:$prompt_content_fingerprint,created_at:$created_at}' \
    >"${request_path}"
  request_key="requests/${RELEASE_ID}/request.json"
  verify_release_aws_mutation_ready 600 || fail "AWS identity/provider preflight failed before S3 request write"
  request_version="$(aws s3api put-object --region "${REGION}" --bucket "${EVIDENCE_BUCKET}" \
    --key "${request_key}" --body "${request_path}" --query VersionId --output text)"
  [[ -n "${request_version}" && "${request_version}" != "None" ]] || fail "Release request bucket must have versioning enabled"

  local overrides build_id status
  overrides="$(jq -cn \
    --arg commit "${GIT_COMMIT}" \
    --arg release "${RELEASE_ID}" \
    --arg prompt "${PROMPT_RELEASE_ID}" \
    --arg bucket "${EVIDENCE_BUCKET}" \
    --arg key "${request_key}" \
    --arg version "${request_version}" \
    '[
      {name:"AUTOMATION_RELEASE_GIT_COMMIT",value:$commit,type:"PLAINTEXT"},
      {name:"AUTOMATION_RELEASE_ID",value:$release,type:"PLAINTEXT"},
      {name:"PROMPT_RELEASE_ID",value:$prompt,type:"PLAINTEXT"},
      {name:"AUTOMATION_RELEASE_REQUEST_BUCKET",value:$bucket,type:"PLAINTEXT"},
      {name:"AUTOMATION_RELEASE_REQUEST_KEY",value:$key,type:"PLAINTEXT"},
      {name:"AUTOMATION_RELEASE_REQUEST_VERSION",value:$version,type:"PLAINTEXT"}
    ]')"
  verify_release_aws_mutation_ready 600 || fail "AWS identity/provider preflight failed before CodeBuild start"
  build_id="$(aws codebuild start-build --region "${REGION}" --project-name "${PROJECT_NAME}" \
    --environment-variables-override "${overrides}" --query 'build.id' --output text)"
  [[ -n "${build_id}" && "${build_id}" != "None" ]] || fail "CodeBuild did not return a build id"
  log "Started ${build_id}"
  while :; do
    status="$(aws codebuild batch-get-builds --region "${REGION}" --ids "${build_id}" --query 'builds[0].buildStatus' --output text)"
    case "${status}" in
      SUCCEEDED) break ;;
      FAILED|FAULT|STOPPED|TIMED_OUT) fail "CodeBuild ended with ${status}" ;;
    esac
    sleep 10
  done

  OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/.deployments/releases/${RELEASE_ID}}"
  [[ ! -e "${OUTPUT_DIR}" ]] || fail "Output directory already exists: ${OUTPUT_DIR}"
  mkdir -p "${OUTPUT_DIR}"
  aws s3api get-object --region "${REGION}" --bucket "${EVIDENCE_BUCKET}" \
    --key "releases/${RELEASE_ID}/release-manifest.json" "${OUTPUT_DIR}/release-manifest.json" >/dev/null
  aws s3api get-object --region "${REGION}" --bucket "${EVIDENCE_BUCKET}" \
    --key "releases/${RELEASE_ID}/publish-record.json" "${OUTPUT_DIR}/publish-record.json" >/dev/null
  (cd "${PROJECT_ROOT}" && "${PYTHON_BIN}" -m backend.scripts.automation_release validate-preproduction-publish \
    --manifest "${OUTPUT_DIR}/release-manifest.json" --publish-record "${OUTPUT_DIR}/publish-record.json" >/dev/null)
  log "Verified CodeBuild release evidence: ${OUTPUT_DIR}"
}

main "$@"
