#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DOCKERFILE="${PROJECT_ROOT}/backend/Dockerfile.automation"
RELEASE_DIR="${PROJECT_ROOT}/.deployments/releases"
IMAGE_PREFIX="localhost/supportportal"

RELEASE_ID=""
MANIFEST_PATH=""
COMMIT_REF=""
BUILD_TIME=""
BUILT_IMAGE_REF=""
BUILT_IMAGE_ID=""

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
  ./deployment/build_automation_release.sh [options]

Options:
      --release-id <id>        Release id (default: current 12-character commit)
      --manifest <path>        Output manifest path (default: .deployments/releases/<id>.env)
  -h, --help                  Show help

The command builds three local role images on the current host:
  route, automation (staging/preproduction), production (production only)

The generated manifest contains local image references and image IDs. It does not
push to or pull from a remote registry.
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

validate_image_id() {
  local value="$1"
  [[ "${value}" =~ ^sha256:[0-9a-fA-F]{64}$ ]] || fail "Docker image inspect did not return an immutable image ID: ${value}"
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
  BUILT_IMAGE_REF="${tag}"
  BUILT_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "${tag}")"
  BUILT_IMAGE_ID="$(trim "${BUILT_IMAGE_ID}")"
  validate_image_id "${BUILT_IMAGE_ID}"
  log "Built ${role} image ${BUILT_IMAGE_REF} (${BUILT_IMAGE_ID})"
}

write_manifest() {
  local route_image="$1"
  local route_image_id="$2"
  local automation_image="$3"
  local automation_image_id="$4"
  local production_image="$5"
  local production_image_id="$6"
  local output_dir temp_path

  output_dir="$(dirname -- "${MANIFEST_PATH}")"
  mkdir -p "${output_dir}"
  temp_path="$(mktemp "${MANIFEST_PATH}.tmp.XXXXXX")"
  trap 'rm -f -- "${temp_path}"' EXIT
  printf 'release_id=%s\ncommit=%s\nbuild_time=%s\nroute_image=%s\nroute_image_id=%s\nautomation_image=%s\nautomation_image_id=%s\nproduction_image=%s\nproduction_image_id=%s\nROUTE_STAGING_IMAGE=%s\nROUTE_STAGING_IMAGE_ID=%s\nROUTE_PREPRODUCTION_IMAGE=%s\nROUTE_PREPRODUCTION_IMAGE_ID=%s\nROUTE_PRODUCTION_IMAGE=%s\nROUTE_PRODUCTION_IMAGE_ID=%s\nAUTOMATION_STAGING_IMAGE=%s\nAUTOMATION_STAGING_IMAGE_ID=%s\nAUTOMATION_PREPRODUCTION_IMAGE=%s\nAUTOMATION_PREPRODUCTION_IMAGE_ID=%s\nAUTOMATION_PRODUCTION_IMAGE=%s\nAUTOMATION_PRODUCTION_IMAGE_ID=%s\n' \
    "${RELEASE_ID}" "${COMMIT_REF}" "${BUILD_TIME}" \
    "${route_image}" "${route_image_id}" \
    "${automation_image}" "${automation_image_id}" \
    "${production_image}" "${production_image_id}" \
    "${route_image}" "${route_image_id}" \
    "${route_image}" "${route_image_id}" \
    "${route_image}" "${route_image_id}" \
    "${automation_image}" "${automation_image_id}" \
    "${automation_image}" "${automation_image_id}" \
    "${production_image}" "${production_image_id}" \
    > "${temp_path}"
  chmod 0600 "${temp_path}"
  mv -f -- "${temp_path}" "${MANIFEST_PATH}"
  trap - EXIT
  log "Release manifest: ${MANIFEST_PATH}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
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
  require_cmd mktemp

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

  local route_image route_image_id automation_image automation_image_id production_image production_image_id
  build_role_image route "${IMAGE_PREFIX}-route"
  route_image="${BUILT_IMAGE_REF}"
  route_image_id="${BUILT_IMAGE_ID}"
  build_role_image automation "${IMAGE_PREFIX}-automation"
  automation_image="${BUILT_IMAGE_REF}"
  automation_image_id="${BUILT_IMAGE_ID}"
  build_role_image production "${IMAGE_PREFIX}-automation-production"
  production_image="${BUILT_IMAGE_REF}"
  production_image_id="${BUILT_IMAGE_ID}"
  write_manifest \
    "${route_image}" "${route_image_id}" \
    "${automation_image}" "${automation_image_id}" \
    "${production_image}" "${production_image_id}"
}

main "$@"
