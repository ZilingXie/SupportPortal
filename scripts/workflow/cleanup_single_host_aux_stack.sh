#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_common.sh
source "$SCRIPT_DIR/_common.sh"

require_command git
require_command podman-compose

ensure_root_workspace_on_main

info "Cleaning stray auxiliary single-host stack project deploymentlw if present."
podman-compose -p deploymentlw -f deployment/docker-compose.single-host.yml down
