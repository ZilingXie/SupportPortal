#!/usr/bin/env bash
set -Eeuo pipefail

# Align every EC2 deployment surface with the target commit and report.
#
# Surfaces:
#   main stack  - api/api_production/workers/nginx behind the / paths
#   split stack - the three /automation/* compose projects (route + automation)
#
# The script deploys ONLY surfaces whose live build ref lags behind the target
# commit, so reruns are cheap and partial deployments are safe. Production
# split deploy still requires explicit approval (--approve-production or
# DEPLOY_PRODUCTION_APPROVED=1), matching deployment/deploy_ec2.sh convention.
#
# Usage:
#   scripts/ops/deploy_surfaces_ec2.sh [--dry-run] [--approve-production]
#        [--skip-main] [--skip-split] [--health-url <url>] [--domain <domain>]
#
# Environment overrides: DOCKER_CMD (default docker), DEPLOY_DOMAIN,
# DEPLOY_SURFACES_HEALTH_URL.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${DEPLOY_SURFACES_REPO_ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
cd "${PROJECT_ROOT}"

BRANCH="main"
DOMAIN="${DEPLOY_DOMAIN:-support.stellarix.space}"
HEALTH_URL="${DEPLOY_SURFACES_HEALTH_URL:-https://${DOMAIN}/health}"
DOCKER_CMD="${DOCKER_CMD:-docker}"
DRY_RUN=0
APPROVE_PRODUCTION=0
SKIP_MAIN=0
SKIP_SPLIT=0
LOG_DIR="/tmp/deploy-surfaces-$(date +%Y%m%d-%H%M%S)"

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --approve-production) APPROVE_PRODUCTION=1 ;;
    --skip-main) SKIP_MAIN=1 ;;
    --skip-split) SKIP_SPLIT=1 ;;
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

# --- step 0: sync repository -------------------------------------------------

[[ "$(git rev-parse --abbrev-ref HEAD)" == "${BRANCH}" ]] || fail "must run on branch ${BRANCH} (use the EC2 ~/SupportPortal checkout, not a worktree)"
git fetch origin "${BRANCH}"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail "tracked working tree is not clean; refusing to deploy"
git merge --ff-only "origin/${BRANCH}" >/dev/null
SHA12="$(git rev-parse --short=12 HEAD)"
log "target commit: ${SHA12}"

if pgrep -f 'deployment/deploy_ec2.sh|ops/auto_deploy_ec2.sh|build_automation_release.sh' >/dev/null 2>&1; then
  fail "another deploy/build process is running (deploy_ec2/auto_deploy/build_automation_release)"
fi

# --- gap detection -----------------------------------------------------------

main_ref="$(curl -sS --max-time 20 "${HEALTH_URL}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("app_build",{}).get("ref") or "")' 2>/dev/null || true)"
[[ -n "${main_ref}" ]] || fail "cannot read app_build.ref from ${HEALTH_URL}"
if [[ "${SKIP_MAIN}" == 1 ]]; then
  need_main=0; main_reason="skipped by --skip-main"
elif [[ "${main_ref}" == "${SHA12}"* ]]; then
  need_main=0; main_reason="live app_build.ref=${main_ref} already at target"
else
  need_main=1; main_reason="live app_build.ref=${main_ref} != ${SHA12}"
fi

split_image_tag() {
  local env="$1"
  "${DOCKER_CMD}" ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null \
    | awk -v s="automation_${env}" -F'\t' '$1 ~ s && $1 !~ /redis/ && $1 !~ /route/ {print $2; exit}' \
    | sed 's/.*://'
}

split_tag_commit() {
  local tag="$1" manifest
  if [[ "${tag}" =~ ^release-[0-9]{8}-[0-9]{3}$ ]]; then
    manifest=".deployments/releases/${tag}.env"
    if [[ -f "${manifest}" ]]; then
      grep '^commit=' "${manifest}" | head -1 | cut -d= -f2
      return
    fi
    echo ""
    return
  fi
  if [[ "${tag}" =~ local-([0-9a-f]{7,12}) ]]; then
    echo "${BASH_REMATCH[1]}"
    return
  fi
  echo ""
}

need_split=0
split_reasons=""
production_pending=0
for env in staging preproduction production; do
  tag="$(split_image_tag "${env}")"
  if [[ -z "${tag}" ]]; then
    [[ "${SKIP_SPLIT}" == 1 ]] || need_split=1
    split_reasons+="${env}: no running automation container; "
    continue
  fi
  commit="$(split_tag_commit "${tag}")"
  if [[ "${SKIP_SPLIT}" == 1 ]]; then
    split_reasons+="${env}: skipped by --skip-split; "
  elif [[ -z "${commit}" ]]; then
    need_split=1
    split_reasons+="${env}: image ${tag} has unresolvable commit; "
  elif [[ "${SHA12}" != "${commit}"* ]]; then
    need_split=1
    split_reasons+="${env}: image ${tag} commit=${commit} != ${SHA12}; "
  else
    split_reasons+="${env}: ${tag} at target; "
  fi
done

log "main stack : $([[ ${need_main} == 1 ]] && echo NEEDS-DEPLOY || echo aligned) (${main_reason})"
log "split stack: $([[ ${need_split} == 1 ]] && echo NEEDS-DEPLOY || echo aligned) (${split_reasons})"

if [[ "${DRY_RUN}" == 1 ]]; then
  log "dry-run: plan above; no changes made"
  exit 0
fi

deploy_step() {
  local name="$1"; shift
  log "▶ ${name} (log: ${LOG_DIR}/${name}.log)"
  if ! "$@" 2>&1 | tee "${LOG_DIR}/${name}.log"; then
    fail "step '${name}' failed; see ${LOG_DIR}/${name}.log — rollback: main='deployment/deploy_ec2.sh --rollback', split='deployment/deploy_ec2.sh --environment <env> --rollback'"
  fi
}

# --- main stack deploy --------------------------------------------------------

if [[ "${need_main}" == 1 ]]; then
  deploy_step main-stack ./deployment/deploy_ec2.sh --branch "${BRANCH}"
fi

# --- split release build + deploy ---------------------------------------------

if [[ "${need_split}" == 1 ]]; then
  today="$(date +%Y%m%d)"
  last="$(ls .deployments/releases 2>/dev/null | grep -o "release-${today}-[0-9]*" | sed 's/.*-//' | sort -n | tail -1)"
  next="$((10#${last:-0} + 1))"
  NEW_ID="$(printf 'release-%s-%03d' "${today}" "${next}")"
  log "building split release ${NEW_ID}"
  deploy_step build-split ./deployment/build_automation_release.sh --release-id "${NEW_ID}"
  manifest_commit="$(grep '^commit=' ".deployments/releases/${NEW_ID}.env" | head -1 | cut -d= -f2)"
  [[ "${manifest_commit}" == "${SHA12}" ]] || fail "manifest commit=${manifest_commit} != ${SHA12}: stale build, aborting before any split deploy"
  deploy_step split-staging ./deployment/deploy_ec2.sh --branch "${BRANCH}" --environment staging --release "${NEW_ID}"
  deploy_step split-preproduction ./deployment/deploy_ec2.sh --branch "${BRANCH}" --environment preproduction --release "${NEW_ID}"
  if [[ "${APPROVE_PRODUCTION}" == 1 || "${DEPLOY_PRODUCTION_APPROVED:-0}" == 1 ]]; then
    deploy_step split-production env DEPLOY_PRODUCTION_APPROVED=1 ./deployment/deploy_ec2.sh --branch "${BRANCH}" --environment production --release "${NEW_ID}"
  else
    production_pending=1
    log "production split deploy NOT executed: rerun with --approve-production (or DEPLOY_PRODUCTION_APPROVED=1); staging/preproduction are live on ${NEW_ID}"
  fi
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

if [[ "${need_split}" == 1 ]]; then
  if ./deployment/verify_split_environments.sh >"${LOG_DIR}/verify-split.log" 2>&1; then
    verify_summary+=("split: verify_split_environments all green")
  else
    tail -20 "${LOG_DIR}/verify-split.log"
    fail "verify_split_environments failed; see ${LOG_DIR}/verify-split.log"
  fi
  token="$(grep '^n8n_request_token=' .env | head -1 | cut -d= -f2)"
  spot="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X POST -H 'Content-Type: application/json' \
    -H "X-N8n-Request-Token: ${token}" \
    -d '{"title":"t","question":"deploy probe","source":"https://agoraio.zendesk.com/agent/tickets/1.json"}' \
    "https://${DOMAIN}/automation/staging/v1/cases")"
  if [[ "${spot}" == 200 ]]; then
    verify_summary+=("split: legacy-body staging probe 200")
  elif [[ "${spot}" == 401 ]]; then
    fail "staging probe 401: n8n_request_token mismatch between .env and running container"
  else
    body="$(curl -s --max-time 30 -X POST -H 'Content-Type: application/json' -H "X-N8n-Request-Token: ${token}" -d '{"title":"t","question":"deploy probe","source":"https://agoraio.zendesk.com/agent/tickets/1.json"}' "https://${DOMAIN}/automation/staging/v1/cases" | head -c 200)"
    fail "staging probe HTTP ${spot}: ${body}"
  fi
fi

# --- final report ----------------------------------------------------------------

echo
echo "================ deploy-surfaces report ================"
echo "target commit : ${SHA12}"
echo "main stack    : $([[ ${need_main} == 1 ]] && echo "deployed (live ref now at target)" || echo "already aligned / skipped (${main_reason})")"
echo "split stack   : $([[ ${need_split} == 1 ]] && echo "deployed (${NEW_ID:-n/a})" || echo "already aligned / skipped")"
if [[ "${production_pending}" == 1 ]]; then
  echo "PRODUCTION    : PENDING APPROVAL — rerun with --approve-production"
fi
for line in "${verify_summary[@]:-}"; do
  [[ -n "${line}" ]] && echo "verify        : ${line}"
done
echo "logs          : ${LOG_DIR}/"
echo "repo state    : $(git status --porcelain --untracked-files=no | wc -l | tr -d ' ') tracked changes; HEAD=$(git rev-parse --short HEAD)"
echo "======================================================="
