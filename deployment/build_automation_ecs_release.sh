#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DOCKERFILE="${PROJECT_ROOT}/backend/Dockerfile.automation"
RELEASE_ROOT="${PROJECT_ROOT}/.deployments/releases"

RELEASE_ID=""
PROMPT_RELEASE_ID="${PROMPT_RELEASE_ID:-}"
MANIFEST_PATH=""
COMMIT_REF=""
BUILD_TIME=""
PYTHON_BIN="${AUTOMATION_RELEASE_PYTHON:-}"
BUILDER="${AUTOMATION_RELEASE_BUILDER:-auto}"
PODMAN_BUILD_TAGS=()

log() { printf '[release] %s\n' "$*"; }
fail() { printf '[release] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  ./deployment/build_automation_ecs_release.sh --prompt-release-id <id> [options]

Options:
      --release-id <id>          Default: rYYYYMMDD-<7-character commit>
      --prompt-release-id <id>   Active Prompt Release captured in provenance
      --manifest <path>          Default: .deployments/releases/<id>/release-manifest.json
      --builder <auto|docker|podman>
                                 Default: auto (Docker Buildx, then Podman)
  -h, --help                     Show help

Builds api, route, and worker exactly once as linux/amd64 OCI layouts. The
command creates and validates a repository-independent JSON Release Manifest.
It never logs in, pushes, promotes, or deploys.
EOF
}

require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "Missing command: $1"; }

validate_identifier() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail "Invalid identifier: $1"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --release-id) [[ $# -ge 2 ]] || fail "--release-id requires a value"; RELEASE_ID="$2"; shift 2 ;;
      --prompt-release-id) [[ $# -ge 2 ]] || fail "--prompt-release-id requires a value"; PROMPT_RELEASE_ID="$2"; shift 2 ;;
      --manifest) [[ $# -ge 2 ]] || fail "--manifest requires a path"; MANIFEST_PATH="$2"; shift 2 ;;
      --builder) [[ $# -ge 2 ]] || fail "--builder requires a value"; BUILDER="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown option: $1 (use --help)" ;;
    esac
  done
}

build_role() {
  local role="$1"
  local output="$2"
  log "Building ${role} as linux/amd64 OCI layout"
  if [[ "${BUILDER}" == "docker" ]]; then
    docker buildx build \
      --pull \
      --platform linux/amd64 \
      --provenance=false \
      --build-arg "AUTOMATION_IMAGE_ROLE=ecs-${role}" \
      --build-arg "APP_BUILD_REF=${COMMIT_REF}" \
      --build-arg "APP_BUILD_TIME=${BUILD_TIME}" \
      --file "${DOCKERFILE}" \
      --tag "supportportal-local:${role}-${RELEASE_ID}" \
      --output "type=oci,dest=${output}" \
      "${PROJECT_ROOT}"
    return
  fi

  local podman_tag="localhost/supportportal-release-build:${role}-${RELEASE_ID}-$$"
  PODMAN_BUILD_TAGS+=("${podman_tag}")
  podman build \
    --pull=always \
    --platform linux/amd64 \
    --format oci \
    --build-arg "AUTOMATION_IMAGE_ROLE=ecs-${role}" \
    --build-arg "APP_BUILD_REF=${COMMIT_REF}" \
    --build-arg "APP_BUILD_TIME=${BUILD_TIME}" \
    --file "${DOCKERFILE}" \
    --tag "${podman_tag}" \
    "${PROJECT_ROOT}"
  podman save --format oci-archive --output "${output}" "${podman_tag}"
}

cleanup() {
  local tag
  [[ "${BUILDER}" == "podman" ]] || return 0
  for tag in "${PODMAN_BUILD_TAGS[@]}"; do
    podman image rm "${tag}" >/dev/null 2>&1 || true
  done
}

select_builder() {
  case "${BUILDER}" in
    auto)
      if command -v docker >/dev/null 2>&1 && docker buildx version >/dev/null 2>&1; then
        BUILDER="docker"
      elif command -v podman >/dev/null 2>&1; then
        BUILDER="podman"
      else
        fail "Docker Buildx or Podman is required"
      fi
      ;;
    docker)
      require_cmd docker
      docker buildx version >/dev/null 2>&1 || fail "Docker buildx is required"
      ;;
    podman)
      require_cmd podman
      podman version >/dev/null 2>&1 || fail "Podman is not available"
      ;;
    *) fail "Invalid builder: ${BUILDER}" ;;
  esac
  log "Using ${BUILDER} builder"
}

main() {
  parse_args "$@"
  require_cmd git
  if [[ -z "${PYTHON_BIN}" && -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
  fi
  if [[ -z "${PYTHON_BIN}" ]]; then
    local common_git_dir shared_root
    common_git_dir="$(git -C "${PROJECT_ROOT}" rev-parse --git-common-dir)"
    [[ "${common_git_dir}" = /* ]] || common_git_dir="${PROJECT_ROOT}/${common_git_dir}"
    shared_root="$(cd -- "$(dirname -- "${common_git_dir}")" && pwd)"
    [[ ! -x "${shared_root}/.venv/bin/python" ]] || PYTHON_BIN="${shared_root}/.venv/bin/python"
  fi
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || [[ -x "${PYTHON_BIN}" ]] \
    || fail "Python runtime not found: ${PYTHON_BIN}"
  [[ -f "${DOCKERFILE}" ]] || fail "Dockerfile not found: ${DOCKERFILE}"
  [[ -z "$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=all)" ]] \
    || fail "Working tree is not clean; build from a clean commit"

  COMMIT_REF="$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
  RELEASE_ID="${RELEASE_ID:-r$(date -u +%Y%m%d)-${COMMIT_REF:0:7}}"
  PROMPT_RELEASE_ID="${PROMPT_RELEASE_ID//[[:space:]]/}"
  validate_identifier "${RELEASE_ID}"
  [[ -n "${PROMPT_RELEASE_ID}" ]] || fail "--prompt-release-id is required"
  validate_identifier "${PROMPT_RELEASE_ID}"
  BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  (
    cd "${PROJECT_ROOT}"
    "${PYTHON_BIN}" -m backend.scripts.prompt_release validate \
      --release-id "${PROMPT_RELEASE_ID}" >/dev/null
  ) || fail "Prompt Release validation failed: ${PROMPT_RELEASE_ID}"
  log "Verified deployable Prompt Release: ${PROMPT_RELEASE_ID}"
  select_builder
  trap cleanup EXIT

  local bundle_dir
  bundle_dir="${RELEASE_ROOT}/${RELEASE_ID}"
  MANIFEST_PATH="${MANIFEST_PATH:-${bundle_dir}/release-manifest.json}"
  if [[ "${MANIFEST_PATH}" != /* ]]; then
    MANIFEST_PATH="${PROJECT_ROOT}/${MANIFEST_PATH}"
  fi
  bundle_dir="$(dirname -- "${MANIFEST_PATH}")"
  mkdir -p "${bundle_dir}"

  local api_layout="${bundle_dir}/api.oci.tar"
  local route_layout="${bundle_dir}/route.oci.tar"
  local worker_layout="${bundle_dir}/worker.oci.tar"
  for layout in "${api_layout}" "${route_layout}" "${worker_layout}"; do
    [[ ! -e "${layout}" ]] || fail "Release artifact already exists: ${layout}"
  done

  build_role api "${api_layout}"
  build_role route "${route_layout}"
  build_role worker "${worker_layout}"

  (
    cd "${PROJECT_ROOT}"
    "${PYTHON_BIN}" -m backend.scripts.automation_release create \
      --release-id "${RELEASE_ID}" \
      --git-commit "${COMMIT_REF}" \
      --build-time "${BUILD_TIME}" \
      --prompt-release-id "${PROMPT_RELEASE_ID}" \
      --output "${MANIFEST_PATH}" \
      --component api "${api_layout}" \
      --component route "${route_layout}" \
      --component worker "${worker_layout}"
    "${PYTHON_BIN}" -m backend.scripts.automation_release validate --manifest "${MANIFEST_PATH}"
  )
  log "Verified Release Manifest: ${MANIFEST_PATH}"
}

main "$@"
