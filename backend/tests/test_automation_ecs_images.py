from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _role_block(role: str, next_role: str) -> str:
    source = (ROOT / "backend/Dockerfile.automation").read_text(encoding="utf-8")
    return source.split(f'if [ "${{AUTOMATION_IMAGE_ROLE}}" = "{role}" ]; then', 1)[1].split(
        f'elif [ "${{AUTOMATION_IMAGE_ROLE}}" = "{next_role}" ]; then', 1
    )[0]


def test_ecs_images_physically_exclude_legacy_and_local_rag_entrypoints() -> None:
    api = _role_block("ecs-api", "ecs-route")
    route = _role_block("ecs-route", "ecs-worker")
    worker = _role_block("ecs-worker", "production")
    for block in (api, route, worker):
        for forbidden in (
            "/app/backend/main.py",
            "/app/backend/automation_runtime.py",
            "/app/backend/automation_production_runtime.py",
            "/app/backend/rag_api.py",
            "/app/backend/rag_worker.py",
            "/app/backend/services/automation_rerun_contracts.py",
            "/app/backend/services/account_rerun_recovery.py",
            "/app/backend/services/rag_reset.py",
        ):
            assert forbidden in block


def test_ecs_entrypoint_exposes_only_three_long_running_roles() -> None:
    entrypoint = (ROOT / "deployment/automation_ecs_entrypoint.sh").read_text(encoding="utf-8")
    assert "backend.automation_ecs_api:create_app --factory" in entrypoint
    assert "backend.automation_ecs_route_worker" in entrypoint
    assert "backend.automation_ecs_worker" in entrypoint
    assert "AUTOMATION_ECS_ACCOUNT_ONLY=1 exec python -m backend.automation_ecs_worker" in entrypoint
    assert "backend.rag_api" not in entrypoint
    assert "backend.rag_worker" not in entrypoint
    assert 'if [ "$#" -gt 0 ]; then' in entrypoint
    assert 'exec "$@"' in entrypoint


def test_each_pruned_role_is_imported_during_image_build() -> None:
    dockerfile = (ROOT / "backend/Dockerfile.automation").read_text(encoding="utf-8")
    assert "python -c 'import backend.automation_ecs_api'" in dockerfile
    assert "python -c 'import backend.automation_ecs_route_worker'" in dockerfile
    assert "AUTOMATION_ECS_ACCOUNT_ONLY=1" in dockerfile
    assert "python -c 'import backend.automation_ecs_worker, backend.worker'" in dockerfile


def test_pruned_sources_are_copied_from_a_sibling_stage_into_final_image() -> None:
    dockerfile = (ROOT / "backend/Dockerfile.automation").read_text(encoding="utf-8")
    assert "FROM runtime-base AS role-files" in dockerfile
    assert "FROM runtime-base AS final" in dockerfile
    assert "COPY --from=role-files /app /app" in dockerfile
