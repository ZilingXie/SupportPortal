from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TERRAFORM = ROOT / "infra/terraform/production"


def _read(name: str) -> str:
    return (TERRAFORM / name).read_text(encoding="utf-8")


def test_task_definitions_use_three_new_ecs_roles_and_amd64() -> None:
    ecs = _read("ecs.tf")
    assert "backend.automation_ecs_api:create_app" in ecs
    assert '"--factory"' in ecs
    assert "backend.automation_ecs_route_worker" in ecs
    assert "backend.automation_ecs_worker" in ecs
    assert 'resource "aws_ecs_task_definition" "route"' in ecs
    assert 'resource "aws_ecs_service" "route"' in ecs
    assert ecs.count('cpu_architecture        = "X86_64"') == 3
    assert "backend.automation_production_runtime" not in ecs
    assert "backend.route_service" not in ecs
    assert '"python", "-m", "backend.worker"' not in ecs


def test_health_and_runtime_provenance_contracts_are_complete() -> None:
    ecs = _read("ecs.tf")
    alb = _read("alb.tf")
    locals_source = _read("locals.tf")
    health_path = "/automation/production/health/live"
    assert health_path in ecs
    assert f'path                = "{health_path}"' in alb
    assert 'path                = "/health"' not in alb
    for name in (
        "AUTOMATION_BASE_PATH",
        "AUTOMATION_DB_RESOURCE_ID",
        "AUTOMATION_DB_SCHEMA",
        "AUTOMATION_JOB_NAMESPACE",
        "AUTOMATION_RELEASE_ID",
        "AUTOMATION_IMAGE_DIGEST",
        "APP_BUILD_REF",
        "APP_BUILD_TIME",
        "PROMPT_RELEASE_ID",
    ):
        assert name in ecs or name in locals_source
    assert "AUTOMATION_INTAKE_SHARED_TOKEN" in locals_source
    assert "AUTOMATION_DB_MIGRATION_DSN" not in ecs


def test_worker_receives_account_mail_rag_and_delivery_contracts() -> None:
    ecs = _read("ecs.tf")
    locals_source = _read("locals.tf")
    for name in (
        "ACCOUNT_DEFAULT_PROCESSING_PROFILE",
        "AUTOMATION_REPLY_POLL_ENABLED",
        "INTERNAL_EMAIL_SUBJECT_NAMESPACE",
        "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE",
        "AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED",
    ):
        assert name in ecs
    for name in (
        "TICKET_DB_DSN",
        "AUTOMATION_DB_DSN",
        "RAG_SERVICE_URL",
        "RAG_SERVICE_SHARED_TOKEN",
        "zendesk_basic_auth",
        "ENGINEER_SLACK_ACCESS_TOKEN",
        "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET",
    ):
        assert name in locals_source
    assert 'value = var.zendesk_side_effects_enabled ? "1" : "0"' in ecs


def test_one_environment_repository_and_digest_only_service_images() -> None:
    ecr = _read("ecr.tf")
    data = _read("data.tf")
    assert "for_each" not in ecr
    assert "ecr_repository_name = local.name_prefix" in _read("locals.tf")
    assert "aws_ecr_repository.runtime.repository_url}@sha256:" in data
    assert "supportportal-production ECR references pinned by sha256 digest" in data
    assert 'trimspace(var.release_id) != "unreleased"' in data
