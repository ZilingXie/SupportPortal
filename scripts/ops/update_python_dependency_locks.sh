#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BASE_IMAGE="python:3.11-slim@sha256:d1e9ca7c4e78d1e8ecadb5d44bfc8e956e7a65b659a9950f569f243d72b326d0"
PIP_TOOLS_VERSION="7.5.1"
MODE="write"

usage() {
  echo "Usage: $0 [--check]" >&2
}

if [[ "${1:-}" == "--check" ]]; then
  MODE="check"
  shift
fi
if [[ "$#" -ne 0 ]]; then
  usage
  exit 2
fi

docker run --rm \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp \
  --env PIP_INDEX_URL=https://pypi.org/simple \
  --volume "${ROOT}:/workspace" \
  --workdir /workspace \
  "${PYTHON_BASE_IMAGE}" \
  sh -eu -c '
    python -m venv /tmp/lock-venv
    /tmp/lock-venv/bin/python -m pip install --disable-pip-version-check --quiet "pip-tools=='"${PIP_TOOLS_VERSION}"'"
    output_dir=/workspace
    if [ "'"${MODE}"'" = check ]; then
      output_dir=/tmp/generated-locks
      mkdir -p "$output_dir"
      cp /workspace/requirements.base.lock "$output_dir/requirements.base.lock"
      cp /workspace/requirements.full.lock "$output_dir/requirements.full.lock"
    fi
    for profile in base full; do
      input=requirements.${profile}.txt
      extra_index_url=
      if [ "$profile" = full ]; then
        input=requirements.txt
        extra_index_url="--extra-index-url=https://download.pytorch.org/whl/cpu"
      fi
      /tmp/lock-venv/bin/pip-compile \
        $extra_index_url \
        --allow-unsafe \
        --generate-hashes \
        --no-header \
        --quiet \
        --resolver=backtracking \
        --reuse-hashes \
        --strip-extras \
        --output-file "$output_dir/requirements.${profile}.lock" \
        "$input"
    done
    if [ "'"${MODE}"'" = check ]; then
      cmp /workspace/requirements.base.lock "$output_dir/requirements.base.lock"
      cmp /workspace/requirements.full.lock "$output_dir/requirements.full.lock"
    fi
  '

if [[ "${MODE}" == "check" ]]; then
  echo "Python dependency locks are current."
else
  echo "Updated requirements.base.lock and requirements.full.lock."
fi
