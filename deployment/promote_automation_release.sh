#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SOURCE_REPOSITORY="supportportal/preproduction"
TARGET_REPOSITORY="supportportal/production"
MANIFEST_PATH=""
PROMOTION_RECORD=""
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
  command -v aws >/dev/null 2>&1 || fail "aws CLI is required"
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || [[ -x "${PYTHON_BIN}" ]] || fail "Python runtime is required"
  if [[ "${DIRECT_PRODUCTION}" = "1" ]]; then
    [[ "${SOURCE_REPOSITORY_EXPLICIT}" = "0" ]] || fail "--direct-production cannot be combined with --source-repository"
    command -v skopeo >/dev/null 2>&1 || fail "skopeo is required for direct Production publishing"
    SOURCE_REPOSITORY="local-oci"
  else
    command -v crane >/dev/null 2>&1 || fail "crane is required for digest-preserving registry copy"
  fi

  local manifest_dir
  manifest_dir="$(cd -- "$(dirname -- "${MANIFEST_PATH}")" && pwd)"
  MANIFEST_PATH="${manifest_dir}/$(basename -- "${MANIFEST_PATH}")"
  PROMOTION_RECORD="${PROMOTION_RECORD:-${manifest_dir}/promotion-record.json}"
  [[ ! -e "${PROMOTION_RECORD}" ]] || fail "Promotion Record already exists: ${PROMOTION_RECORD}"

  local release_id
  if [[ "${DIRECT_PRODUCTION}" = "1" ]]; then
    PYTHONPATH="${PROJECT_ROOT}" "${PYTHON_BIN}" -m backend.scripts.automation_release \
      validate --manifest "${MANIFEST_PATH}" >/dev/null \
      || fail "Release Manifest OCI validation failed"
  fi
  release_id="$(PYTHONPATH="${PROJECT_ROOT}" "${PYTHON_BIN}" -c 'import sys; from pathlib import Path; from backend.services.automation_release_manifest import read_manifest; print(read_manifest(Path(sys.argv[1])).release_id)' "${MANIFEST_PATH}")" \
    || fail "Release Manifest validation failed"
  if [[ -z "${REGISTRY_ID}" ]]; then
    REGISTRY_ID="$(aws sts get-caller-identity --query Account --output text)"
  fi
  [[ "${REGISTRY_ID}" =~ ^[0-9]{12}$ ]] || fail "AWS registry id must be a 12-digit account id"
  local registry
  registry="${REGISTRY_ID}.dkr.ecr.${REGION}.amazonaws.com"
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
        --accepted-media-types application/vnd.oci.image.manifest.v1+json \
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

  "${PYTHON_BIN}" - "${PROMOTION_RECORD}" "${release_id}" "${SOURCE_REPOSITORY}" "${TARGET_REPOSITORY}" "${REGION}" "${REGISTRY_ID}" "${records_file}" <<'PY'
import datetime
import json
import pathlib
import sys

output, release_id, source, target, region, registry_id, rows = sys.argv[1:]
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
