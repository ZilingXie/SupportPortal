#!/usr/bin/env sh
set -eu

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

case "${AUTOMATION_IMAGE_ROLE:-}" in
  ecs-api)
    exec python -m uvicorn backend.automation_ecs_api:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  ecs-route)
    exec python -m backend.automation_ecs_route_worker
    ;;
  ecs-worker)
    AUTOMATION_ECS_ACCOUNT_ONLY=1 exec python -m backend.automation_ecs_worker
    ;;
  *)
    echo "unsupported AUTOMATION_IMAGE_ROLE: ${AUTOMATION_IMAGE_ROLE:-unset}" >&2
    exit 64
    ;;
esac
