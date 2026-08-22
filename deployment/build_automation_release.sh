#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DOCKERFILE="${PROJECT_ROOT}/backend/Dockerfile.automation"
RELEASE_DIR="${PROJECT_ROOT}/.deployments/releases"

REGISTRY=""
RELEASE_ID=""
MANIFEST_PATH=""
COMMIT_REF=""
BUILD_TIME=""
BUILT_DIGEST=""

log() {
  printf '[release] %s\n' "$*"
}

fail() {
  printf '[release] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  ./deployment/build_automation_release.sh --registry <registry> [options]

Options:
      --registry <registry>   Registry prefix, for example registry.example/supportportal
      --release-id <id>        Release id (default: current 12-character commit)
      --manifest <path>        Output manifest path (default: .deployments/releases/<id>.env)
  -h, --help                  Show help

The command builds and pushes three immutable role images:
  route, automation (staging/preproduction), production (production only)
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing command: $1"
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

validate_identifier() {
  local value="$1"
  [[ "${value}" =~ ^[A-Za-z0-9._-]+$ ]] || fail "Invalid release id: ${value}"
}

validate_digest() {
  local value="$1"
  [[ "${value}" =~ ^.+@sha256:[0-9a-fA-F]{64}$ ]] || fail "Docker push did not return an immutable digest: ${value}"
}

build_role_image() {
  local role="$1"
  local repository="$2"
  local tag="${repository}:${RELEASE_ID}"

  log "Building ${role} image ${tag}"
  docker build --pull \
    --build-arg "AUTOMATION_IMAGE_ROLE=${role}" \
    --build-arg "APP_BUILD_REF=${COMMIT_REF}" \
    --build-arg "APP_BUILD_TIME=${BUILD_TIME}" \
    --file "${DOCKERFILE}" \
    --tag "${tag}" \
    "${PROJECT_ROOT}"
  log "Pushing ${tag}"
  docker push "${tag}"
  BUILT_DIGEST="$(docker image inspect --format '{{index .RepoDigests 0}}' "${tag}" | head -n 1)"
  BUILT_DIGEST="$(trim "${BUILT_DIGEST}")"
  validate_digest "${BUILT_DIGEST}"
  log "Published ${role} digest ${BUILT_DIGEST}"
}

write_manifest() {
  local route_digest="$1"
  local automation_digest="$2"
  local production_digest="$3"
  local output_dir temp_path

  output_dir="$(dirname -- "${MANIFEST_PATH}")"
  mkdir -p "${output_dir}"
  temp_path="$(mktemp "${MANIFEST_PATH}.tmp.XXXXXX")"
  trap 'rm -f -- "${temp_path}"' EXIT
  printf 'release_id=%s\ncommit=%s\nbuild_time=%s\nroute_image=%s\nautomation_image=%s\nproduction_image=%s\nROUTE_STAGING_IMAGE=%s\nROUTE_PREPRODUCTION_IMAGE=%s\nROUTE_PRODUCTION_IMAGE=%s\nAUTOMATION_STAGING_IMAGE=%s\nAUTOMATION_PREPRODUCTION_IMAGE=%s\nAUTOMATION_PRODUCTION_IMAGE=%s\n' \
    "${RELEASE_ID}" "${COMMIT_REF}" "${BUILD_TIME}" \
    "${route_digest}" "${automation_digest}" "${production_digest}" \
    "${route_digest}" "${route_digest}" "${route_digest}" \
    "${automation_digest}" "${automation_digest}" "${production_digest}" \
    > "${temp_path}"
  chmod 0600 "${temp_path}"
  mv -f -- "${temp_path}" "${MANIFEST_PATH}"
  trap - EXIT
  log "Release manifest: ${MANIFEST_PATH}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --registry)
        [[ $# -ge 2 ]] || fail "--registry requires a value"
        REGISTRY="${2%/}"
        shift 2
        ;;
      --release-id)
        [[ $# -ge 2 ]] || fail "--release-id requires a value"
        RELEASE_ID="$2"
        shift 2
        ;;
      --manifest)
        [[ $# -ge 2 ]] || fail "--manifest requires a path"
        MANIFEST_PATH="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown option: $1 (use --help)"
        ;;
    esac
  done
}

main() {
  parse_args "$@"
  require_cmd docker
  require_cmd git
  require_cmd head
  require_cmd mktemp

  [[ -n "${REGISTRY}" ]] || fail "--registry is required"
  [[ -f "${DOCKERFILE}" ]] || fail "Dockerfile not found: ${DOCKERFILE}"
  [[ -z "$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=all)" ]] || fail "Working tree is not clean; build from a clean commit"

  COMMIT_REF="$(git -C "${PROJECT_ROOT}" rev-parse --short=12 HEAD)"
  RELEASE_ID="${RELEASE_ID:-${COMMIT_REF}}"
  validate_identifier "${RELEASE_ID}"
  BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  MANIFEST_PATH="${MANIFEST_PATH:-${RELEASE_DIR}/${RELEASE_ID}.env}"
  if [[ "${MANIFEST_PATH}" != /* ]]; then
    MANIFEST_PATH="${PROJECT_ROOT}/${MANIFEST_PATH}"
  fi

  local route_digest automation_digest production_digest
  build_role_image route "${REGISTRY}/supportportal-route"
  route_digest="${BUILT_DIGEST}"
  build_role_image automation "${REGISTRY}/supportportal-automation"
  automation_digest="${BUILT_DIGEST}"
  build_role_image production "${REGISTRY}/supportportal-automation-production"
  production_digest="${BUILT_DIGEST}"
  write_manifest "${route_digest}" "${automation_digest}" "${production_digest}"
}

main "$@"
