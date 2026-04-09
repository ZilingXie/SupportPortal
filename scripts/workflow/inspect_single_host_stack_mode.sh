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
  podman exec "$container_name" python -c "import importlib.util, json, os; print(json.dumps({'runtime_profile': (os.getenv('RUNTIME_PROFILE') or 'full').strip() or 'full', 'sentiment_provider': (os.getenv('SENTIMENT_PROVIDER') or '').strip().lower(), 'torch_available': importlib.util.find_spec('torch') is not None}))"
}

parse_json_field() {
  local json_payload="$1"
  local field_name="$2"
  python3 - "$json_payload" "$field_name" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
field = sys.argv[2]
value = payload.get(field, "")
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

official_image="$(container_image "$official_api_container")"
[[ -n "$official_image" ]] || die "Official single-host stack is not running (missing $official_api_container)."

official_port="$(container_port "$official_nginx_container")" || die "Unable to resolve host port for $official_nginx_container."
official_health_url="http://127.0.0.1:${official_port}/health"
official_health_payload="$(curl -fsS "$official_health_url")"
official_health_status="$(parse_json_field "$official_health_payload" "status")"
official_runtime_payload="$(container_runtime_snapshot "$official_api_container")"
official_runtime_profile="$(parse_json_field "$official_runtime_payload" "runtime_profile")"
official_sentiment_provider="$(parse_json_field "$official_runtime_payload" "sentiment_provider")"
official_torch_available="$(parse_json_field "$official_runtime_payload" "torch_available")"

auxiliary_image="$(container_image "$aux_api_container")"
auxiliary_stack_present="false"
auxiliary_health_url=""
if [[ -n "$auxiliary_image" ]]; then
  auxiliary_stack_present="true"
  if auxiliary_port="$(container_port "$aux_nginx_container")"; then
    auxiliary_health_url="http://127.0.0.1:${auxiliary_port}/health"
  fi
fi

printf 'official_project=deployment\n'
printf 'official_health_url=%s\n' "$official_health_url"
printf 'official_image=%s\n' "$official_image"
printf 'official_health_status=%s\n' "$official_health_status"
printf 'official_runtime_profile=%s\n' "$official_runtime_profile"
printf 'official_sentiment_provider=%s\n' "$official_sentiment_provider"
printf 'official_torch_available=%s\n' "$official_torch_available"
printf 'auxiliary_stack_present=%s\n' "$auxiliary_stack_present"
if [[ "$auxiliary_stack_present" == "true" ]]; then
  printf 'auxiliary_project=deploymentlw\n'
  printf 'auxiliary_health_url=%s\n' "$auxiliary_health_url"
  printf 'auxiliary_image=%s\n' "$auxiliary_image"
fi
