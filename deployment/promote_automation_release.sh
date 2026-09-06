#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/lib/release_aws_provider.sh"
SOURCE_REPOSITORY="supportportal/preproduction"
TARGET_REPOSITORY="supportportal/production"
MANIFEST_PATH=""
PROMOTION_RECORD=""
PUBLISH_RECORD=""
PREPRODUCTION_EVIDENCE=""
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
REGISTRY_ID=""
PYTHON_BIN="${AUTOMATION_RELEASE_PYTHON:-python3}"
DIRECT_PRODUCTION=0
SOURCE_REPOSITORY_EXPLICIT=0

fail() { printf '[promotion] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  ./deployment/promote_automation_release.sh --manifest <release-manifest.json> --region <region> [options]

Options:
  --source-repository <name>   Default: supportportal/preproduction
  --target-repository <name>   Default: supportportal/production
  --registry-id <account-id>   Optional AWS account registry id
  --promotion-record <path>    Default: next to Release Manifest
  --publish-record <path>      Required for normal Preproduction promotion
  --preproduction-evidence <path>
                               Required complete Preproduction deploy evidence
  --direct-production          Publish the Manifest's local OCI archives directly
                               to Production and record source_repository=local-oci

The default mode uses crane to copy the exact OCI manifests and layers accepted
in Preproduction into Production. The explicitly approved direct-production
mode uses skopeo --preserve-digests with the local OCI archives. Neither mode
builds an image or changes the Release Manifest. The target ECR repository must
have immutable tags enabled.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --manifest) [[ $# -ge 2 ]] || fail "--manifest requires a value"; MANIFEST_PATH="$2"; shift 2 ;;
      --region) [[ $# -ge 2 ]] || fail "--region requires a value"; REGION="$2"; shift 2 ;;
      --registry-id) [[ $# -ge 2 ]] || fail "--registry-id requires a value"; REGISTRY_ID="$2"; shift 2 ;;
      --source-repository) [[ $# -ge 2 ]] || fail "--source-repository requires a value"; SOURCE_REPOSITORY="$2"; SOURCE_REPOSITORY_EXPLICIT=1; shift 2 ;;
      --target-repository) [[ $# -ge 2 ]] || fail "--target-repository requires a value"; TARGET_REPOSITORY="$2"; shift 2 ;;
      --promotion-record) [[ $# -ge 2 ]] || fail "--promotion-record requires a value"; PROMOTION_RECORD="$2"; shift 2 ;;
      --publish-record) [[ $# -ge 2 ]] || fail "--publish-record requires a value"; PUBLISH_RECORD="$2"; shift 2 ;;
      --preproduction-evidence) [[ $# -ge 2 ]] || fail "--preproduction-evidence requires a value"; PREPRODUCTION_EVIDENCE="$2"; shift 2 ;;
      --direct-production) DIRECT_PRODUCTION=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown option: $1" ;;
    esac
  done
}

aws_ecr() {
  local args=(--region "${REGION}")
  [[ -z "${REGISTRY_ID}" ]] || args+=(--registry-id "${REGISTRY_ID}")
  aws ecr "$@" "${args[@]}"
}

main() {
  parse_args "$@"
  [[ -n "${MANIFEST_PATH}" && -f "${MANIFEST_PATH}" ]] || fail "Release Manifest is required"
  [[ -n "${REGION}" ]] || fail "AWS region is required"
  [[ "${REGION}" = "us-east-1" ]] || fail "AWS region must be us-east-1"
  [[ "${DEPLOY_PRODUCTION_APPROVED:-}" = "1" ]] || fail "DEPLOY_PRODUCTION_APPROVED=1 is required before Production ECR promotion"
  command -v aws >/dev/null 2>&1 || fail "aws CLI is required"
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || [[ -x "${PYTHON_BIN}" ]] || fail "Python runtime is required"
  if [[ "${DIRECT_PRODUCTION}" = "1" ]]; then
    [[ "${SOURCE_REPOSITORY_EXPLICIT}" = "0" ]] || fail "--direct-production cannot be combined with --source-repository"
    [[ -z "${PUBLISH_RECORD}" && -z "${PREPRODUCTION_EVIDENCE}" ]] \
      || fail "--direct-production does not accept Preproduction evidence"
    command -v skopeo >/dev/null 2>&1 || fail "skopeo is required for direct Production publishing"
    SOURCE_REPOSITORY="local-oci"
  else
    [[ "${SOURCE_REPOSITORY}" = "supportportal/preproduction" ]] \
      || fail "normal promotion source must be supportportal/preproduction"
    [[ "${TARGET_REPOSITORY}" = "supportportal/production" ]] \
      || fail "normal promotion target must be supportportal/production"
    [[ -n "${PUBLISH_RECORD}" && -f "${PUBLISH_RECORD}" ]] \
      || fail "Preproduction Publish Record is required"
    [[ -n "${PREPRODUCTION_EVIDENCE}" && -f "${PREPRODUCTION_EVIDENCE}" ]] \
      || fail "complete Preproduction deploy evidence is required"
    command -v crane >/dev/null 2>&1 || fail "crane is required for digest-preserving registry copy"
  fi

  local manifest_dir
  manifest_dir="$(cd -- "$(dirname -- "${MANIFEST_PATH}")" && pwd)"
  MANIFEST_PATH="${manifest_dir}/$(basename -- "${MANIFEST_PATH}")"
  PROMOTION_RECORD="${PROMOTION_RECORD:-${manifest_dir}/promotion-record.json}"
  [[ ! -e "${PROMOTION_RECORD}" ]] || fail "Promotion Record already exists: ${PROMOTION_RECORD}"

  local release_id acceptance_json publish_record_sha256 deploy_evidence_sha256
  if [[ "${DIRECT_PRODUCTION}" = "1" ]]; then
    PYTHONPATH="${PROJECT_ROOT}" "${PYTHON_BIN}" -m backend.scripts.automation_release \
      validate --manifest "${MANIFEST_PATH}" >/dev/null \
      || fail "Release Manifest OCI validation failed"
  fi
  release_id="$(PYTHONPATH="${PROJECT_ROOT}" "${PYTHON_BIN}" -c 'import sys; from pathlib import Path; from backend.services.automation_release_manifest import read_manifest; print(read_manifest(Path(sys.argv[1])).release_id)' "${MANIFEST_PATH}")" \
    || fail "Release Manifest validation failed"
  if [[ "${DIRECT_PRODUCTION}" = "0" ]]; then
    acceptance_json="$(PYTHONPATH="${PROJECT_ROOT}" "${PYTHON_BIN}" -m backend.scripts.automation_ecs_deploy \
      validate-preproduction-acceptance \
      --manifest "${MANIFEST_PATH}" \
      --publish-record "${PUBLISH_RECORD}" \
      --deploy-evidence "${PREPRODUCTION_EVIDENCE}")" \
      || fail "Preproduction release evidence validation failed"
    publish_record_sha256="$(jq -r '.publish_record_sha256' <<<"${acceptance_json}")"
    deploy_evidence_sha256="$(jq -r '.deploy_evidence_sha256' <<<"${acceptance_json}")"
  fi
  if [[ -z "${REGISTRY_ID}" ]]; then
    if [[ "${DIRECT_PRODUCTION}" = "0" ]]; then
      REGISTRY_ID="$(jq -r '.registry_id' <<<"${acceptance_json}")"
    else
      REGISTRY_ID="$(aws sts get-caller-identity --query Account --output text)"
    fi
  fi
  [[ "${REGISTRY_ID}" =~ ^[0-9]{12}$ ]] || fail "AWS registry id must be a 12-digit account id"
  if [[ "${DIRECT_PRODUCTION}" = "0" ]]; then
    [[ "$(jq -r '.registry_id' <<<"${acceptance_json}")" = "${REGISTRY_ID}" ]] \
      || fail "Preproduction evidence registry id mismatch"
    [[ "$(jq -r '.region' <<<"${acceptance_json}")" = "${REGION}" ]] \
      || fail "Preproduction evidence region mismatch"
  fi
  local registry
  registry="${REGISTRY_ID}.dkr.ecr.${REGION}.amazonaws.com"
  verify_release_aws_mutation_ready 1800 || fail "AWS identity/provider preflight failed before Production ECR promotion"
  local target_mutability
  target_mutability="$(aws_ecr describe-repositories \
    --repository-names "${TARGET_REPOSITORY}" \
    --query 'repositories[0].imageTagMutability' --output text)"
  [[ "${target_mutability}" = "IMMUTABLE" ]] \
    || fail "Target ECR repository must use immutable tags"
  if [[ "${DIRECT_PRODUCTION}" = "1" ]]; then
    aws ecr get-login-password --region "${REGION}" \
      | skopeo login "${registry}" --username AWS --password-stdin >/dev/null
  else
    aws ecr get-login-password --region "${REGION}" \
      | crane auth login "${registry}" --username AWS --password-stdin >/dev/null
  fi
  local records_file
  records_file="$(mktemp "${manifest_dir}/promotion.XXXXXX")"
  trap 'rm -f -- "${records_file}"' EXIT

  local role tag expected layout_path source_digest target_digest existing
  for role in api route worker; do
    read -r tag expected < <("${PYTHON_BIN}" -c 'import json,sys; c=json.load(open(sys.argv[1]))["components"][sys.argv[2]]; print(c["tag"], c["digest"])' "${MANIFEST_PATH}" "${role}")
    if [[ "${DIRECT_PRODUCTION}" = "1" ]]; then
      layout_path="${manifest_dir}/$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(open(sys.argv[1]))["components"][sys.argv[2]]["oci_layout"])' "${MANIFEST_PATH}" "${role}")"
      source_digest="${expected}"
    else
      source_digest="$(aws_ecr batch-get-image \
        --repository-name "${SOURCE_REPOSITORY}" \
        --image-ids "imageDigest=${expected}" \
        --query 'images[0].imageId.imageDigest' --output text)"
    fi
    [[ "${source_digest}" = "${expected}" ]] || fail "${role} source digest mismatch: ${source_digest}"
    existing="$(aws_ecr batch-get-image \
      --repository-name "${TARGET_REPOSITORY}" \
      --image-ids "imageTag=${tag}" \
      --query 'images[0].imageId.imageDigest' --output text 2>/dev/null || true)"
    if [[ -n "${existing}" && "${existing}" != "None" ]]; then
      [[ "${existing}" = "${expected}" ]] || fail "immutable target tag ${tag} already points to ${existing}"
      target_digest="${existing}"
    elif [[ "${DIRECT_PRODUCTION}" = "1" ]]; then
      skopeo copy --preserve-digests \
        --retry-times 3 \
        --retry-delay 5s \
        --dest-precompute-digests \
        "oci-archive:${layout_path}" \
        "docker://${registry}/${TARGET_REPOSITORY}:${tag}"
      target_digest="$(aws_ecr batch-get-image \
        --repository-name "${TARGET_REPOSITORY}" \
        --image-ids "imageTag=${tag}" \
        --query 'images[0].imageId.imageDigest' --output text)"
    else
      crane copy \
        "${registry}/${SOURCE_REPOSITORY}@${expected}" \
        "${registry}/${TARGET_REPOSITORY}:${tag}"
      target_digest="$(aws_ecr batch-get-image \
        --repository-name "${TARGET_REPOSITORY}" \
        --image-ids "imageTag=${tag}" \
        --query 'images[0].imageId.imageDigest' --output text)"
    fi
    [[ "${target_digest}" = "${expected}" ]] || fail "${role} promoted digest mismatch: ${target_digest}"
    printf '%s\t%s\t%s\n' "${role}" "${tag}" "${expected}" >> "${records_file}"
  done

  "${PYTHON_BIN}" - "${PROMOTION_RECORD}" "${release_id}" "${SOURCE_REPOSITORY}" "${TARGET_REPOSITORY}" "${REGION}" "${REGISTRY_ID}" "${records_file}" "${publish_record_sha256:-}" "${deploy_evidence_sha256:-}" <<'PY'
import datetime
import json
import pathlib
import sys

output, release_id, source, target, region, registry_id, rows, publish_sha, deploy_sha = sys.argv[1:]
components = {}
for line in pathlib.Path(rows).read_text(encoding="utf-8").splitlines():
    role, tag, digest = line.split("\t")
    components[role] = {"tag": tag, "source_digest": digest, "target_digest": digest}
payload = {
    "schema_version": "automation-promotion-v1",
    "release_id": release_id,
    "promoted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "source_repository": source,
    "target_repository": target,
    "region": region,
    "registry_id": registry_id or None,
    "components": components,
}
if source == "supportportal/preproduction":
    payload["source_publish_record_sha256"] = publish_sha
    payload["preproduction_deploy_evidence_sha256"] = deploy_sha
path = pathlib.Path(output)
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
PY
  trap - EXIT
  rm -f -- "${records_file}"
  printf '[promotion] Verified Promotion Record: %s\n' "${PROMOTION_RECORD}"
}

main "$@"
