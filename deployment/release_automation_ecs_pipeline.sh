#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${AUTOMATION_RELEASE_PYTHON:-}"
if [[ -z "${PYTHON_BIN}" && -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
fi
if [[ -z "${PYTHON_BIN}" ]]; then
  GIT_COMMON_DIR="$(git -C "${PROJECT_ROOT}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  SHARED_ROOT="$(dirname -- "${GIT_COMMON_DIR}")"
  [[ ! -x "${SHARED_ROOT}/.venv/bin/python" ]] || PYTHON_BIN="${SHARED_ROOT}/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"
export AUTOMATION_RELEASE_PYTHON="${PYTHON_BIN}"

# LOCAL_* values configure the developer's Compose database.  They are not
# release inputs and must never be inherited by the Python/AWS child process
# when this wrapper is launched after sourcing the repository .env.
unset LOCAL_POSTGRES_PASSWORD LOCAL_POSTGRES_USER LOCAL_POSTGRES_DB LOCAL_POSTGRES_HOST_PORT
unset LOCAL_TICKET_DB_POOL_MAX_SIZE LOCAL_TICKET_DB_POOL_MIN_SIZE LOCAL_TICKET_DB_POOL_TIMEOUT_SECONDS LOCAL_TICKET_DB_SCHEMA

exec "${PYTHON_BIN}" -m backend.scripts.automation_ecs_release_pipeline run \
  --project-root "${PROJECT_ROOT}" "$@"
