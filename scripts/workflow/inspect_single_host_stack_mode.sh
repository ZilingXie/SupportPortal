#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

require_command git
require_command podman
require_command curl
require_command python3

ensure_root_workspace_on_main

official_api_container="deployment_api_1"
official_nginx_container="deployment_nginx_1"
aux_api_container="deploymentlw_api_1"
aux_nginx_container="deploymentlw_nginx_1"

container_rows="$(podman ps --format '{{.Names}}|{{.Image}}')"

container_image() {
  local container_name="$1"
  printf '%s\n' "$container_rows" | awk -F'|' -v target="$container_name" '$1 == target { print $2; exit }'
}

container_port() {
  local container_name="$1"
  local mapping

  mapping="$(podman port "$container_name" 80/tcp 2>/dev/null | tail -n 1 || true)"
  if [[ -z "$mapping" ]]; then
    return 1
  fi

  printf '%s\n' "$mapping" | sed -E 's/.*:([0-9]+)$/\1/'
}

container_runtime_snapshot() {
  local container_name="$1"
  podman exec "$container_name" python -c "import importlib.util, json, os; print(json.dumps({'runtime_profile': (os.getenv('RUNTIME_PROFILE') or 'full').strip() or 'full', 'sentiment_provider': (os.getenv('SENTIMENT_PROVIDER') or '').strip().lower(), 'torch_available': importlib.util.find_spec('torch') is not None, 'app_build_ref': (os.getenv('APP_BUILD_REF') or '').strip(), 'app_build_time': (os.getenv('APP_BUILD_TIME') or '').strip()}))"
}

parse_json_field() {
  local json_payload="$1"
  local field_name="$2"
  python3 - "$json_payload" "$field_name" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
field = sys.argv[2]
value = payload
for part in field.split("."):
    if isinstance(value, dict):
        value = value.get(part, "")
    else:
        value = ""
        break
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

image_tag_from_ref() {
  local image_ref="$1"
  local without_digest
  local after_last_slash

  without_digest="${image_ref%%@*}"
  after_last_slash="${without_digest##*/}"
  if [[ "$after_last_slash" == *:* ]]; then
    printf '%s\n' "${after_last_slash##*:}"
    return 0
  fi
  printf '%s\n' ""
}

official_image="$(container_image "$official_api_container")"
[[ -n "$official_image" ]] || die "Official single-host stack is not running (missing $official_api_container)."
root_main_ref="$(git rev-parse --short=12 HEAD)"
official_image_tag="$(image_tag_from_ref "$official_image")"

official_port="$(container_port "$official_nginx_container")" || die "Unable to resolve host port for $official_nginx_container."
official_health_url="http://127.0.0.1:${official_port}/health"
official_health_payload="$(curl -fsS "$official_health_url")"
official_health_status="$(parse_json_field "$official_health_payload" "status")"
official_health_build_ref="$(parse_json_field "$official_health_payload" "app_build.ref")"
official_runtime_payload="$(container_runtime_snapshot "$official_api_container")"
official_runtime_profile="$(parse_json_field "$official_runtime_payload" "runtime_profile")"
official_sentiment_provider="$(parse_json_field "$official_runtime_payload" "sentiment_provider")"
official_torch_available="$(parse_json_field "$official_runtime_payload" "torch_available")"
official_runtime_build_ref="$(parse_json_field "$official_runtime_payload" "app_build_ref")"
official_runtime_build_time="$(parse_json_field "$official_runtime_payload" "app_build_time")"

auxiliary_image="$(container_image "$aux_api_container")"
if [[ -n "$auxiliary_image" ]]; then
  die "Unsupported auxiliary single-host stack detected: deploymentlw. Clean it with bash scripts/workflow/cleanup_single_host_aux_stack.sh before relying on the official stack."
fi

if [[ "$official_image_tag" != "$root_main_ref" || "$official_health_build_ref" != "$root_main_ref" || "$official_runtime_build_ref" != "$root_main_ref" ]]; then
  die "Official single-host build provenance mismatch: root_main_ref=$root_main_ref official_image_tag=$official_image_tag official_health_build_ref=$official_health_build_ref official_runtime_build_ref=$official_runtime_build_ref"
fi

printf 'official_project=deployment\n'
printf 'root_main_ref=%s\n' "$root_main_ref"
printf 'official_health_url=%s\n' "$official_health_url"
printf 'official_image=%s\n' "$official_image"
printf 'official_image_tag=%s\n' "$official_image_tag"
printf 'official_health_status=%s\n' "$official_health_status"
printf 'official_health_build_ref=%s\n' "$official_health_build_ref"
printf 'official_runtime_profile=%s\n' "$official_runtime_profile"
printf 'official_sentiment_provider=%s\n' "$official_sentiment_provider"
printf 'official_torch_available=%s\n' "$official_torch_available"
printf 'official_runtime_build_ref=%s\n' "$official_runtime_build_ref"
if [[ -n "$official_runtime_build_time" ]]; then
  printf 'official_runtime_build_time=%s\n' "$official_runtime_build_time"
fi
printf 'build_provenance_status=matched\n'
printf 'auxiliary_stack_present=false\n'
