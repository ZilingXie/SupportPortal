#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${AUTOMATION_RELEASE_PYTHON:-python3}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
REPOSITORY="${AUTOMATION_PREPRODUCTION_REPOSITORY:-supportportal/preproduction}"
CACHE_REPOSITORY="${AUTOMATION_CODEBUILD_CACHE_REPOSITORY:-supportportal/build-cache}"
EVIDENCE_BUCKET="${AUTOMATION_RELEASE_EVIDENCE_BUCKET:-}"
RELEASE_ID="${AUTOMATION_RELEASE_ID:-}"
GIT_COMMIT="${AUTOMATION_RELEASE_GIT_COMMIT:-}"
PROMPT_RELEASE_ID="${PROMPT_RELEASE_ID:-}"
REQUEST_BUCKET="${AUTOMATION_RELEASE_REQUEST_BUCKET:-}"
REQUEST_KEY="${AUTOMATION_RELEASE_REQUEST_KEY:-}"
REQUEST_VERSION="${AUTOMATION_RELEASE_REQUEST_VERSION:-}"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OUTPUT_DIR="${CODEBUILD_SRC_DIR:-/tmp}/release-evidence"

log() { printf '[codebuild-release] %s\n' "$*"; }
fail() { printf '[codebuild-release] ERROR: %s\n' "$*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "Missing command: $1"; }

validate_inputs() {
  [[ "${GIT_COMMIT}" =~ ^[0-9a-f]{40}$ ]] || fail "AUTOMATION_RELEASE_GIT_COMMIT must be a full Git SHA"
  [[ "${RELEASE_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail "Invalid release id"
  [[ "${PROMPT_RELEASE_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail "Invalid Prompt Release id"
  [[ "${REPOSITORY}" = "supportportal/preproduction" ]] || fail "CodeBuild may publish only to supportportal/preproduction"
  [[ -n "${EVIDENCE_BUCKET}" && -n "${REQUEST_BUCKET}" && -n "${REQUEST_KEY}" && -n "${REQUEST_VERSION}" ]] \
    || fail "Versioned release request and evidence bucket are required"
  [[ "$(git -C "${PROJECT_ROOT}" rev-parse HEAD)" = "${GIT_COMMIT}" ]] || fail "Checked out Git commit does not match request"
  [[ -z "$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=all)" ]] || fail "CodeBuild checkout must be clean"
}

read_release_request() {
  local request_path="${OUTPUT_DIR}/release-request.json"
  aws s3api get-object \
    --region "${REGION}" \
    --bucket "${REQUEST_BUCKET}" \
    --key "${REQUEST_KEY}" \
    --version-id "${REQUEST_VERSION}" \
    "${request_path}" >/dev/null
  jq -e \
    --arg release_id "${RELEASE_ID}" \
    --arg git_commit "${GIT_COMMIT}" \
    --arg prompt_release_id "${PROMPT_RELEASE_ID}" \
    '.schema_version == "automation-codebuild-request-v1"
      and .release_id == $release_id
      and .git_commit == $git_commit
      and .prompt_release_id == $prompt_release_id
      and (.prompt_build_ref | type == "string" and length > 0)
      and (.prompt_content_fingerprint | test("^sha256:[0-9a-f]{64}$"))' \
    "${request_path}" >/dev/null || fail "Versioned release request does not match CodeBuild inputs"
}

build_and_push_role() {
  local role="$1"
  local registry="$2"
  local tag="${role}-${RELEASE_ID}"
  local observed_digest
  log "Building ${role} for linux/amd64" >&2
  docker buildx build \
    --platform linux/amd64 \
    --provenance=false \
    --pull \
    --build-arg "AUTOMATION_IMAGE_ROLE=ecs-${role}" \
    --build-arg "APP_BUILD_REF=${GIT_COMMIT}" \
    --build-arg "APP_BUILD_TIME=${BUILD_TIME}" \
    --cache-from "type=registry,ref=${registry}/${CACHE_REPOSITORY}:automation" \
    --cache-to "type=registry,ref=${registry}/${CACHE_REPOSITORY}:automation,mode=max,image-manifest=true,oci-mediatypes=true" \
    --file "${PROJECT_ROOT}/backend/Dockerfile.automation" \
    --tag "${registry}/${REPOSITORY}:${tag}" \
    --push \
    "${PROJECT_ROOT}" >&2
  observed_digest="$(aws ecr describe-images \
    --region "${REGION}" \
    --repository-name "${REPOSITORY}" \
    --image-ids "imageTag=${tag}" \
    --query 'imageDetails[0].imageDigest' \
    --output text)"
  [[ "${observed_digest}" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "ECR returned an invalid ${role} image digest"
  printf '%s\n' "${observed_digest}"
}

release_tool() {
  local registry="$1"
  shift
  docker run --rm \
    --entrypoint python \
    --env "PYTHONPATH=${PROJECT_ROOT}" \
    --volume "${PROJECT_ROOT}:${PROJECT_ROOT}:ro" \
    --volume "${OUTPUT_DIR}:${OUTPUT_DIR}" \
    --workdir "${PROJECT_ROOT}" \
    "${registry}/${REPOSITORY}:api-${RELEASE_ID}" \
    -m backend.scripts.automation_release "$@"
}

write_publish_record() {
  local registry_id="$1"
  local manifest_version="$2"
  "${PYTHON_BIN}" - "${OUTPUT_DIR}/release-manifest.json" "${OUTPUT_DIR}/publish-record.json" \
    "${CODEBUILD_BUILD_ARN}" "${CODEBUILD_BUILD_NUMBER}" "${registry_id}" "${REGION}" "${manifest_version}" <<'PY'
import datetime
import json
import pathlib
import sys

manifest_path, output_path, build_arn, build_number, registry_id, region, manifest_version = sys.argv[1:]
manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
payload = {
    "schema_version": "automation-preproduction-publish-v1",
    "release_id": manifest["release_id"],
    "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "source_git_commit": manifest["git_commit"],
    "codebuild_build_arn": build_arn,
    "codebuild_build_number": int(build_number),
    "registry_id": registry_id,
    "region": region,
    "target_repository": "supportportal/preproduction",
    "evidence_object_version": manifest_version,
    "components": {
        role: {"tag": item["tag"], "digest": item["digest"]}
        for role, item in manifest["components"].items()
    },
}
path = pathlib.Path(output_path)
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

main() {
  for command in aws docker git jq "${PYTHON_BIN}"; do require_cmd "${command}"; done
  validate_inputs
  mkdir -p "${OUTPUT_DIR}"
  read_release_request

  local registry_id registry api_digest route_digest worker_digest
  registry_id="$(aws sts get-caller-identity --query Account --output text)"
  [[ "${registry_id}" =~ ^[0-9]{12}$ ]] || fail "AWS account id is invalid"
  registry="${registry_id}.dkr.ecr.${REGION}.amazonaws.com"
  aws ecr get-login-password --region "${REGION}" \
    | docker login --username AWS --password-stdin "${registry}" >/dev/null
  docker buildx create --name supportportal-release-builder --use >/dev/null 2>&1 || docker buildx use supportportal-release-builder
  docker buildx inspect --bootstrap >/dev/null

  api_digest="$(build_and_push_role api "${registry}")"
  route_digest="$(build_and_push_role route "${registry}")"
  worker_digest="$(build_and_push_role worker "${registry}")"
  for digest in "${api_digest}" "${route_digest}" "${worker_digest}"; do
    [[ "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "ECR returned an invalid image digest"
  done

  release_tool "${registry}" create-registry \
    --release-id "${RELEASE_ID}" \
    --git-commit "${GIT_COMMIT}" \
    --build-time "${BUILD_TIME}" \
    --prompt-release-id "${PROMPT_RELEASE_ID}" \
    --output "${OUTPUT_DIR}/release-manifest.json" \
    --component api "${api_digest}" \
    --component route "${route_digest}" \
    --component worker "${worker_digest}"
  release_tool "${registry}" validate \
    --manifest "${OUTPUT_DIR}/release-manifest.json" >/dev/null

  local release_prefix manifest_version
  release_prefix="releases/${RELEASE_ID}"
  manifest_version="$(aws s3api put-object \
    --region "${REGION}" \
    --bucket "${EVIDENCE_BUCKET}" \
    --key "${release_prefix}/release-manifest.json" \
    --body "${OUTPUT_DIR}/release-manifest.json" \
    --query VersionId --output text)"
  [[ -n "${manifest_version}" && "${manifest_version}" != "None" ]] || fail "Manifest evidence object is not versioned"
  write_publish_record "${registry_id}" "${manifest_version}"
  release_tool "${registry}" validate-preproduction-publish \
    --manifest "${OUTPUT_DIR}/release-manifest.json" \
    --publish-record "${OUTPUT_DIR}/publish-record.json" >/dev/null
  aws s3api put-object \
    --region "${REGION}" \
    --bucket "${EVIDENCE_BUCKET}" \
    --key "${release_prefix}/publish-record.json" \
    --body "${OUTPUT_DIR}/publish-record.json" >/dev/null
  log "Published immutable release evidence for ${RELEASE_ID}"
}

main "$@"
