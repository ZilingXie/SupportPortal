from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TERRAFORM = ROOT / "infra/terraform/production"
PREPRODUCTION_TERRAFORM = ROOT / "infra/terraform/preproduction"


def _read(name: str) -> str:
    return (TERRAFORM / name).read_text(encoding="utf-8")


def _all_tf() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in TERRAFORM.glob("*.tf"))


def _resource_types(source: str) -> set[str]:
    return set(re.findall(r'^resource\s+"([^"]+)"\s+"', source, re.MULTILINE))


def _preproduction_all_tf() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in PREPRODUCTION_TERRAFORM.glob("*.tf")
    )


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


def test_preproduction_root_owns_only_isolated_environment_resources() -> None:
    source = _preproduction_all_tf()
    resources = _resource_types(source)
    assert {
        "aws_ecs_cluster",
        "aws_ecr_repository",
        "aws_lb_target_group",
        "aws_lb_listener_rule",
        "aws_security_group",
        "aws_cloudwatch_log_group",
        "aws_iam_role",
        "aws_efs_access_point",
        "aws_service_discovery_private_dns_namespace",
        "aws_service_discovery_service",
        "aws_s3_bucket",
        "aws_ecs_service",
    } <= resources
    assert 'resource "aws_rds_cluster"' not in source
    assert 'resource "aws_db_instance"' not in source
    assert 'resource "aws_lb"' not in source
    assert 'resource "aws_efs_file_system"' not in source
    assert 'resource "aws_ssm_parameter"' not in source
    assert "postgresql://" not in source


def test_preproduction_bootstrap_is_staged_without_target_or_task_definition_ownership() -> None:
    variables = (PREPRODUCTION_TERRAFORM / "variables.tf").read_text(encoding="utf-8")
    ecs = (PREPRODUCTION_TERRAFORM / "ecs.tf").read_text(encoding="utf-8")
    readme = (PREPRODUCTION_TERRAFORM / "README.md").read_text(encoding="utf-8")
    assert 'variable "create_account_services"' in variables
    assert "default     = false" in variables
    assert "var.create_account_services ? local.account_services : {}" in ecs
    assert "ignore_changes = [task_definition]" in ecs
    lifecycle = ecs.split("lifecycle {", 1)[1].split("}", 1)[0]
    assert "desired_count" not in lifecycle
    assert "network_configuration" not in lifecycle
    assert 'resource "aws_ecs_task_definition"' not in _preproduction_all_tf()
    assert "terraform -target" in readme
    assert "without `terraform -target`" in readme


def test_preproduction_network_and_runtime_identity_are_explicit() -> None:
    source = _preproduction_all_tf()
    network = (PREPRODUCTION_TERRAFORM / "network.tf").read_text(encoding="utf-8")
    ecs = (PREPRODUCTION_TERRAFORM / "ecs.tf").read_text(encoding="utf-8")
    assert 'name = "supportportal-preproduction"' in source
    assert 'name                 = "supportportal/preproduction"' in source
    assert 'path                = "/automation/preproduction/health/live"' in network
    assert 'values = ["/automation/preproduction", "/automation/preproduction/*"]' in network
    assert "referenced_security_group_id = var.shared_alb_security_group_id" in network
    assert "referenced_security_group_id = aws_security_group.ecs.id" in network
    assert "security_group_id            = var.shared_rds_security_group_id" in network
    postgres_rule = network.split(
        'resource "aws_vpc_security_group_ingress_rule" "postgres_from_preproduction"', 1
    )[1]
    assert "from_port                    = 5432" in postgres_rule
    assert "to_port                      = 5432" in postgres_rule
    assert 'ip_protocol                  = "tcp"' in postgres_rule
    assert 'cidr_ipv4' not in postgres_rule
    assert 'subnets          = each.key == "worker" ? [var.efs_subnet_id] : var.public_subnet_ids' in ecs
    assert "contains(var.public_subnet_ids, var.efs_subnet_id)" in ecs
    assert "assign_public_ip = true" in ecs
    assert 'name = "preproduction.supportportal.local"' in network
    assert 'name = "hermes"' in network


def test_preproduction_retains_three_releases_per_role() -> None:
    source = (PREPRODUCTION_TERRAFORM / "main.tf").read_text(encoding="utf-8")
    assert '["api", "route", "worker"]' in source
    assert 'tagPrefixList = ["${role}-"]' in source
    assert "countNumber   = 3" in source
    assert "imageCountMoreThan" in source


def test_preproduction_account_and_hermes_state_roles_are_isolated() -> None:
    iam = (PREPRODUCTION_TERRAFORM / "iam.tf").read_text(encoding="utf-8")
    outputs = (PREPRODUCTION_TERRAFORM / "outputs.tf").read_text(encoding="utf-8")
    assert 'resource "aws_iam_role" "hermes_task"' in iam
    account_policy = iam.split('resource "aws_iam_role_policy" "task_efs"', 1)[1].split(
        'resource "aws_iam_role" "hermes_task"', 1
    )[0]
    assert "var.hermes_efs_access_point_arns" not in account_policy
    hermes_policy = iam.split('resource "aws_iam_role_policy" "hermes_task"', 1)[1]
    assert "var.hermes_efs_access_point_arns" in hermes_policy
    assert 'Resource = "${aws_s3_bucket.hermes_backup.arn}/migration/*"' in hermes_policy
    assert "hermes_task_role_arn" in outputs


def test_preproduction_default_backup_bucket_name_fits_s3_limit() -> None:
    source = (PREPRODUCTION_TERRAFORM / "main.tf").read_text(encoding="utf-8")
    expected = "supportportal-hermes-preprod-backup-891612554546-us-east-1"
    assert len(expected) <= 63
    assert (
        '"supportportal-hermes-preprod-backup-'
        '${data.aws_caller_identity.current.account_id}-${var.aws_region}"'
    ) in source


def test_preproduction_backend_and_secret_boundary_are_documented() -> None:
    backend = (PREPRODUCTION_TERRAFORM / "backend.tf.example").read_text(encoding="utf-8")
    readme = (PREPRODUCTION_TERRAFORM / "README.md").read_text(encoding="utf-8")
    assert 'key            = "supportportal/ecs-preproduction/terraform.tfstate"' in backend
    assert 'dynamodb_table = "supportportal-terraform-locks"' in backend
    assert "SecureString values are never Terraform resources or variables" in readme
    assert "register_automation_ecs_initial_task_definitions.sh" in readme


def test_codebuild_release_project_allows_only_one_build_at_a_time() -> None:
    source = (ROOT / "infra/terraform/release/main.tf").read_text(encoding="utf-8")
    assert "concurrent_build_limit = 1" in source


def test_codebuild_role_reads_requests_but_writes_only_release_evidence() -> None:
    source = (ROOT / "infra/terraform/release/main.tf").read_text(encoding="utf-8")
    request_statement = source.split('Sid      = "VersionedReleaseRequestRead"', 1)[1].split(
        "},", 1
    )[0]
    evidence_statement = source.split(
        'Sid      = "VersionedReleaseEvidenceWrite"', 1
    )[1].split("},", 1)[0]
    assert "s3:GetObjectVersion" in request_statement
    assert "s3:PutObject" not in request_statement
    assert 'requests/*' in request_statement
    assert "s3:PutObject" in evidence_statement
    assert 'releases/*' in evidence_statement
    assert "s3:GetObject" not in evidence_statement
