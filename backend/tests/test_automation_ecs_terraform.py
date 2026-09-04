from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TERRAFORM = ROOT / "infra/terraform/production"


def _read(name: str) -> str:
    return (TERRAFORM / name).read_text(encoding="utf-8")


def _all_tf() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in TERRAFORM.glob("*.tf"))


def _resource_types(source: str) -> set[str]:
    return set(re.findall(r'^resource\s+"([^"]+)"\s+"', source, re.MULTILINE))


def test_production_root_manages_only_the_imported_stable_boundary() -> None:
    source = _all_tf()
    assert _resource_types(source) == {
        "aws_ecr_repository",
        "aws_lb_target_group",
        "aws_lb_listener_rule",
        "aws_ecs_service",
    }
    assert 'name                 = "supportportal/production"' in _read("ecr.tf")
    assert 'default = "supportportal-production-tg"' in _read("variables.tf")
    assert 'matcher             = "200"' in _read("alb.tf")
    assert 'priority     = var.automation_listener_rule_priority' in _read("alb.tf")
    assert 'default = 10' in _read("variables.tf")


def test_shared_infrastructure_is_read_only_data_or_input() -> None:
    data = _read("data.tf")
    variables = _read("variables.tf")
    for data_source in (
        "aws_ecs_cluster",
        "aws_lb",
        "aws_lb_listener",
        "aws_security_group",
        "aws_cloudwatch_log_group",
        "aws_iam_role",
        "aws_efs_file_system",
        "aws_ssm_parameter",
    ):
        assert f'data "{data_source}"' in data
    assert 'variable "shared_acm_certificate_arn"' in variables
    assert 'variable "shared_ssm_parameter_names"' in variables
    assert 'data "aws_elasticache_replication_group"' not in data
    assert 'variable "shared_redis_replication_group_id"' not in variables


def test_services_ignore_only_release_owned_task_definition_pointer() -> None:
    ecs = _read("ecs.tf")
    assert 'resource "aws_ecs_service" "automation"' in ecs
    assert "for_each = local.services" in ecs
    assert "ignore_changes = [task_definition]" in ecs
    assert "desired_count    = var.desired_count" in ecs
    assert "deployment_minimum_healthy_percent = 100" in ecs
    assert "deployment_maximum_percent         = 200" in ecs
    assert "deployment_circuit_breaker" in ecs
    assert 'availability_zone_rebalancing = "ENABLED"' in ecs
    assert re.search(r"wait_for_steady_state\s*=\s*false", ecs)
    assert "enable_ecs_managed_tags = true" in ecs
    assert 'propagate_tags          = "SERVICE"' in ecs
    assert "platform_version = each.value.platform_version" in ecs
    assert "network_configuration" in ecs
    assert 'container_name   = "api"' in ecs
    lifecycle = ecs.split("lifecycle {", 1)[1].split("}", 1)[0]
    assert "desired_count" not in lifecycle
    assert "network_configuration" not in lifecycle
    assert "load_balancer" not in lifecycle


def test_imported_resources_declare_live_tags_and_platform_versions() -> None:
    ecr = _read("ecr.tf")
    alb = _read("alb.tf")
    ecs = _read("ecs.tf")
    locals_source = _read("locals.tf")
    assert 'Name        = "supportportal/production"' in ecr
    assert 'Project = "supportportal"' in alb
    assert 'Owner   = "zac"' in alb
    assert "forward {" in alb
    assert "weight = 1" in alb
    assert "enabled  = false" in alb
    assert "duration = 3600" in alb
    assert "Component   = each.key" in ecs
    assert 'platform_version               = "LATEST"' in locals_source
    assert locals_source.count('platform_version               = "1.4.0"') == 2
    assert 'shared_log_group_name      = "/ecs/supportportal/production"' in _read(
        "terraform.tfvars.example"
    )


def test_task_definitions_and_pilot_are_not_owned_by_terraform() -> None:
    source = _all_tf().lower()
    assert 'resource "aws_ecs_task_definition"' not in source
    assert "pilot-creds" not in source
    assert "pilot_bin" not in source
    assert "/var/lib/pilot" not in source
    assert "pilot_efs" not in source


def test_removed_duplicate_or_absent_resources_do_not_reappear() -> None:
    forbidden = {
        "aws_ecs_cluster",
        "aws_lb",
        "aws_lb_listener",
        "aws_acm_certificate",
        "aws_cloudwatch_log_group",
        "aws_secretsmanager_secret",
        "aws_elasticache_replication_group",
        "aws_efs_file_system",
        "aws_iam_role",
        "aws_iam_openid_connect_provider",
        "aws_s3_bucket",
        "aws_cloudwatch_metric_alarm",
        "aws_ecr_lifecycle_policy",
        "aws_ecs_task_definition",
    }
    assert not (_resource_types(_all_tf()) & forbidden)


def test_import_runbook_requires_remote_state_and_zero_drift_before_deploy() -> None:
    readme = _read("README.md")
    backend = _read("backend.tf.example")
    assert 'backend "s3"' in backend
    assert "dynamodb_table" in backend
    assert "encrypt        = true" in backend
    for address in (
        "aws_ecr_repository.runtime",
        "aws_lb_target_group.automation",
        "aws_lb_listener_rule.automation_https",
        "aws_ecs_service.automation",
    ):
        assert f"terraform import {address}" in readme or address in readme
    assert "terraform plan -detailed-exitcode" in readme
    assert "0 to add, 0 to change, 0 to" in readme
    assert "deployment/deploy_automation_ecs_release.sh" in readme


def test_production_root_has_no_release_or_secret_values() -> None:
    variables = _read("variables.tf")
    tfvars = _read("terraform.tfvars.example")
    for name in (
        "api_image",
        "route_image",
        "worker_image",
        "release_id",
        "git_commit",
        "build_time",
        "prompt_release_id",
        "zendesk_side_effects_enabled",
    ):
        assert f'variable "{name}"' not in variables
    assert "REPLACE" in tfvars
    assert "oauth2-token" not in tfvars
    assert "postgresql://" not in tfvars
