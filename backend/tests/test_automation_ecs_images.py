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


def test_only_ecs_api_image_retains_read_only_dashboard_assets() -> None:
    api = _role_block("ecs-api", "ecs-route")
    route = _role_block("ecs-route", "ecs-worker")
    worker = _role_block("ecs-worker", "production")
    assert "! -name automation-ecs-production" in api
    assert "rm -rf /app/ui" not in api
    assert "rm -rf /app/backend/tests" in api
    assert "/app/ui /app/docs" in route
    assert "/app/ui /app/docs" in worker


def test_ecs_worker_retains_vendored_ragflow_skill() -> None:
    dockerfile = (ROOT / "backend/Dockerfile.automation").read_text(encoding="utf-8")
    worker = _role_block("ecs-worker", "production")
    skill_root = ROOT / "backend/skills/ragflow-docs-search"
    assert (skill_root / "SKILL.md").is_file()
    assert (skill_root / "scripts/search.py").is_file()
    assert "/app/backend/skills/ragflow-docs-search" not in worker
    assert "COPY backend /tmp/backend-src/backend" in dockerfile


def test_only_ecs_worker_contains_archer_skill_and_no_pilot() -> None:
    dockerfile = (ROOT / "backend/Dockerfile.automation").read_text(encoding="utf-8")
    api = _role_block("ecs-api", "ecs-route")
    route = _role_block("ecs-route", "ecs-worker")
    worker = _role_block("ecs-worker", "production")
    skill_root = ROOT / "backend/skills/archer-cross-channel-hosting"
    assert (skill_root / "SKILL.md").is_file()
    assert (skill_root / "scripts/enable_cross_channel_hosting.py").is_file()
    # p2-139: the unsigned, rotating Pilot download is gone from every image.
    assert not (ROOT / "backend/scripts/install_pilot.py").exists()
    assert "install_pilot" not in dockerfile
    assert "/app/backend/skills/archer-cross-channel-hosting" in api
    assert "/app/backend/skills/archer-cross-channel-hosting" in route
    assert "/app/backend/skills/archer-cross-channel-hosting" not in worker
    assert "/app/bin/pilot" not in dockerfile


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


def test_ecs_images_drop_host_python_cache_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (
        "**/__pycache__",
        "**/*.pyc",
        "**/*.pyo",
        "**/*.pyd",
        ".deployments",
    ):
        assert pattern in dockerignore

    dockerfile = (ROOT / "backend/Dockerfile.automation").read_text(encoding="utf-8")
    assert "find /app -type f" in dockerfile
    assert "-name '*.pyc'" in dockerfile
    assert "-name '__pycache__'" in dockerfile
