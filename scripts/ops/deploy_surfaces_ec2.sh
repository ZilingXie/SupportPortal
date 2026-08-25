#!/usr/bin/env bash
set -Eeuo pipefail

# Align the EC2 main stack with the target commit and report.
#
# The three /automation/* environments are retired from EC2 and targeted for ECS.
# --skip-split remains accepted so older operators and the daily wrapper can
# state that boundary explicitly.
#
# Usage:
#   scripts/ops/deploy_surfaces_ec2.sh [--branch <branch>] [--dry-run] [--daily]
#        [--skip-main] [--skip-split] [--health-url <url>] [--domain <domain>]
#
# Environment overrides: DEPLOY_DOMAIN, DEPLOY_SURFACES_HEALTH_URL.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${DEPLOY_SURFACES_REPO_ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
cd "${PROJECT_ROOT}"

BRANCH="main"
DOMAIN="${DEPLOY_DOMAIN:-support.stellarix.space}"
HEALTH_URL="${DEPLOY_SURFACES_HEALTH_URL:-https://${DOMAIN}/health}"
DRY_RUN=0
DAILY_MODE=0
SKIP_MAIN=0
LOCK_FILE="${DEPLOY_SURFACES_LOCK_FILE:-${DEPLOY_LOCK_FILE:-${PROJECT_ROOT}/.deploy_ec2.lock}}"
DEPLOY_LOCK_ALREADY_HELD="${DEPLOY_LOCK_ALREADY_HELD:-0}"
LOG_DIR="/tmp/deploy-surfaces-$(date +%Y%m%d-%H%M%S)"

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BRANCH="${2:?}"; shift ;;
    --dry-run) DRY_RUN=1 ;;
    --daily) DAILY_MODE=1 ;;
    --skip-main) SKIP_MAIN=1 ;;
    --skip-split) ;;
    --health-url) HEALTH_URL="${2:?}"; shift ;;
    --domain) DOMAIN="${2:?}"; shift ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

log() { printf '[deploy-surfaces] %s\n' "$*"; }
fail() { printf '[deploy-surfaces] ERROR: %s\n' "$*" >&2; exit 1; }

mkdir -p "${LOG_DIR}"

acquire_deploy_lock() {
  if [[ "${DEPLOY_LOCK_ALREADY_HELD}" == 1 ]]; then
    log "Using deploy lock held by the daily report wrapper: ${LOCK_FILE}"
  else
    mkdir -p "$(dirname -- "${LOCK_FILE}")"
    exec 9>"${LOCK_FILE}"
    flock -n 9 || fail "another deployment is running (lock: ${LOCK_FILE})"
    log "Acquired deploy lock: ${LOCK_FILE}"
  fi
  export DEPLOY_LOCK_ALREADY_HELD=1
  export DEPLOY_LOCK_FILE="${LOCK_FILE}"
}

# --- step 0: sync repository -------------------------------------------------

acquire_deploy_lock
[[ "$(git rev-parse --abbrev-ref HEAD)" == "${BRANCH}" ]] || fail "must run on branch ${BRANCH} (use the EC2 ~/SupportPortal checkout, not a worktree)"
git fetch origin "${BRANCH}"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail "tracked working tree is not clean; refusing to deploy"
git merge --ff-only "origin/${BRANCH}" >/dev/null
SHA12="$(git rev-parse --short=12 HEAD)"
export APP_BUILD_REF="${SHA12}"
export APP_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export APP_RUNTIME_IMAGE="localhost/supportportal-app:${SHA12}"
log "target commit: ${SHA12}"

# --- gap detection -----------------------------------------------------------

main_ref="$(curl -sS --max-time 20 "${HEALTH_URL}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("app_build",{}).get("ref") or "")' 2>/dev/null || true)"
[[ -n "${main_ref}" ]] || fail "cannot read app_build.ref from ${HEALTH_URL}"
if [[ "${SKIP_MAIN}" == 1 ]]; then
  need_main=0; main_reason="skipped by --skip-main"
elif [[ "${DAILY_MODE}" == 1 ]]; then
  need_main=1; main_reason="daily mode always runs main deployment to process Prompt Release changes"
elif [[ "${main_ref}" == "${SHA12}"* ]]; then
  need_main=0; main_reason="live app_build.ref=${main_ref} already at target"
else
  need_main=1; main_reason="live app_build.ref=${main_ref} != ${SHA12}"
fi

log "main stack : $([[ ${need_main} == 1 ]] && echo NEEDS-DEPLOY || echo aligned) (${main_reason})"
log "split stack: retired from EC2 (ECS migration pending)"

if [[ "${DRY_RUN}" == 1 ]]; then
  log "dry-run: plan above; no changes made"
  exit 0
fi

deploy_step() {
  local name="$1"; shift
  log "▶ ${name} (log: ${LOG_DIR}/${name}.log)"
  if ! "$@" 2>&1 | tee "${LOG_DIR}/${name}.log"; then
    fail "step '${name}' failed; see ${LOG_DIR}/${name}.log — rollback: main='deployment/deploy_ec2.sh --rollback'"
  fi
}

# --- main stack deploy --------------------------------------------------------

if [[ "${need_main}" == 1 ]]; then
  deploy_step main-stack ./deployment/deploy_ec2.sh --branch "${BRANCH}" --skip-pull
fi

# --- verification --------------------------------------------------------------

verify_summary=()
if [[ "${need_main}" == 1 ]]; then
  live_ref="$(curl -sS --max-time 20 "https://${DOMAIN}/health" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("app_build",{}).get("ref") or "")')"
  [[ "${live_ref}" == "${SHA12}"* ]] || fail "main stack health ref=${live_ref} != ${SHA12} after deploy"
  prod_page="$(curl -s -o /dev/null -w '%{http_code}' "https://${DOMAIN}/production/")"
  [[ "${prod_page}" == 200 ]] || fail "/production/ returned ${prod_page}"
  openapi_has_account="$(curl -s "https://${DOMAIN}/openapi.json" | grep -c '"/account"' || true)"
  [[ "${openapi_has_account}" -ge 1 ]] || fail "/openapi.json no longer lists /account"
  verify_summary+=("main: health ref=${live_ref}; /production/ 200; /account preserved")
fi

# --- final report ----------------------------------------------------------------

echo
echo "================ deploy-surfaces report ================"
echo "target commit : ${SHA12}"
echo "main stack    : $([[ ${need_main} == 1 ]] && echo "deployed (live ref now at target)" || echo "already aligned / skipped (${main_reason})")"
echo "split stack   : retired from EC2 (ECS migration pending)"
for line in "${verify_summary[@]:-}"; do
  [[ -n "${line}" ]] && echo "verify        : ${line}"
done
echo "logs          : ${LOG_DIR}/"
echo "repo state    : $(git status --porcelain --untracked-files=no | wc -l | tr -d ' ') tracked changes; HEAD=$(git rev-parse --short HEAD)"
echo "======================================================="
