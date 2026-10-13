from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from backend.services.automation_release_manifest import (
    read_manifest,
    read_preproduction_publish_record,
    validate_preproduction_publish_record,
)
from backend.services.account_internal_email_recipients import (
    resolve_account_internal_email_recipients,
)
from backend.services.knowledge_source_allowlist import validate_allowlist


PROVENANCE_ENV_NAMES = {
    "AUTOMATION_RELEASE_ID",
    "AUTOMATION_IMAGE_DIGEST",
    "APP_BUILD_REF",
    "APP_BUILD_TIME",
    "PROMPT_RELEASE_ID",
}
HERMES_CASE_WORKFLOW_MODES = {"disabled", "mock", "real"}
AUTOMATION_CASE_ENGINES = {"legacy", "hermes"}
HERMES_OUTBOUND_SECRET_NAMES = {
    "ENGINEER_INVESTIGATION_REPLY_BASE_URL",
    "ENGINEER_INVESTIGATION_REPLY_API_KEY",
}
HERMES_SECRET_NAMES = HERMES_OUTBOUND_SECRET_NAMES | {"HERMES_CALLBACK_TOKEN"}
HERMES_SECRET_SUFFIXES = {
    "ENGINEER_INVESTIGATION_REPLY_BASE_URL": "hermes-base-url",
    "ENGINEER_INVESTIGATION_REPLY_API_KEY": "hermes-api-server-key",
    "HERMES_CALLBACK_TOKEN": "hermes-callback-token",
}
HERMES_AGENT_SECRET_NAMES = {
    "HERMES_AGENT_BASE_URL",
    "HERMES_AGENT_API_TOKEN",
}
HERMES_AGENT_SECRET_SUFFIXES = {
    "HERMES_AGENT_BASE_URL": "hermes-agent-base-url",
    "HERMES_AGENT_API_TOKEN": "hermes-api-server-key",
}
# Zendesk readback credentials required by the API-role hermes-review approve
# path when it falls back to the live ownership snapshot for comments_revision.
API_ZENDESK_READBACK_SECRET_SUFFIXES = {
    "zendesk_basic_auth": "zendesk-basic-auth",
    "ZENDESK_AI_ASSIGNEE_EMAIL": "zendesk-ai-assignee-email",
}
# LLM credential for API-role tool executions.  Under the hermes case engine
# the agent calls execute_automation_action on the API role, whose enablement
# branch runs grounded field extraction (an LLM call); without this key the
# tool 500s with account_ai_missing_credentials (ticket 13549).
API_RUNTIME_LLM_SECRET_SUFFIXES = {
    "OPENAI_API_KEY": "openai-api-key",
}
# Graph mail execution chain (13922): the hermes automation tool sends
# the suspension/fraud internal email from the API container, so API task
# definitions carry the Graph identity, token-cache/reply-record paths,
# and the internal-email recipient secrets — mirroring worker config.
API_GRAPH_MAIL_ENV = {
    "BILLING_AUTOMATION_GRAPH_TENANT_ID": "60275374-3eaa-49c2-83c3-cc189d126981",
    "BILLING_AUTOMATION_GRAPH_CLIENT_ID": "cb5aaefe-2ee2-4ac9-a3ee-5490ddf70d80",
    "BILLING_AUTOMATION_GRAPH_USERNAME": "ai-support-agent@agora.io",
    "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE": "/app/.msgraph/billing-automation-token.json",
    "BILLING_AUTOMATION_REPLY_RECORD_PATH": "/app/.msgraph/billing-request-replies.jsonl",
}
API_GRAPH_MAIL_SECRET_SUFFIXES = {
    "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET": "billing-graph-client-secret",
    "FRAUD_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": "fraud-internal-email-recipients",
    "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": (
        "account-suspension-internal-email-recipients"
    ),
}
# Enablement execution mode switch (p2-152): "manual" keeps the p2-149 human
# review flow; "archer" restores the Archer auto-enablement workflow.
ENABLEMENT_WORKFLOW_MODES = {"manual", "archer"}
# Preproduction-only LLM policy (p2-160): engineer investigation runs
# gpt-6-astra at medium effort inside the Hermes stack (configured on the
# Hermes task itself); every other SupportPortal scenario is pinned to
# gpt-5.6-luna at max effort. Env names follow the canonical (non-deprecated)
# spellings from services/llm_profiles.py. Scenarios without an effort knob
# (ticket_title hardcodes "none", web_search / knowledge_ingestion /
# benchmark_judge carry none) get the model pin only. Production renders strip
# every name below so the observed-td inheritance cannot leak the
# preproduction policy into Production task definitions.
PREPRODUCTION_LLM_ENV_OVERRIDES = {
    "ROUTE_AGENT_ROUTER_MODEL": "gpt-5.6-luna",
    "ROUTE_AGENT_ROUTER_REASONING_EFFORT": "max",
    "ACCOUNT_ROUTE_MODEL": "gpt-5.6-luna",
    "ACCOUNT_ROUTE_REASONING_EFFORT": "max",
    "PRODUCT_AGENT_MODEL": "gpt-5.6-luna",
    "PRODUCT_AGENT_REASONING_EFFORT": "max",
    "ROUTE_AGENT_WEB_SEARCH_MODEL": "gpt-5.6-luna",
    "CLIENT_ACK_MODEL": "gpt-5.6-luna",
    "CLIENT_ACK_REASONING_EFFORT": "max",
    "INPUT_GUARDRAIL_MODEL": "gpt-5.6-luna",
    "INPUT_GUARDRAIL_REASONING_EFFORT": "max",
    "TICKET_TITLE_MODEL": "gpt-5.6-luna",
    "BILLING_REPLY_MODEL": "gpt-5.6-luna",
    "BILLING_REPLY_REASONING_EFFORT": "max",
    "ENABLEMENT_REPLY_MODEL": "gpt-5.6-luna",
    "ENABLEMENT_REPLY_REASONING_EFFORT": "max",
    "ENABLEMENT_COMPLETION_CLASSIFIER_MODEL": "gpt-5.6-luna",
    "ENABLEMENT_COMPLETION_CLASSIFIER_REASONING_EFFORT": "max",
    "AUTOMATION_PERSONA_MODEL": "gpt-5.6-luna",
    "AUTOMATION_PERSONA_REASONING_EFFORT": "max",
    "RAG_AGENT_ANSWER_MODEL": "gpt-5.6-luna",
    "RAG_AGENT_ANSWER_REASONING_EFFORT": "max",
    "RAGFLOW_ANSWER_MODEL": "gpt-5.6-luna",
    "RAGFLOW_ANSWER_REASONING_EFFORT": "max",
    "ACCOUNT_EXTRACTOR_MODEL": "gpt-5.6-luna",
    "ACCOUNT_EXTRACTOR_REASONING_EFFORT": "max",
    "REVIEW_AGENT_POSTCHECK_MODEL": "gpt-5.6-luna",
    "REVIEW_AGENT_POSTCHECK_REASONING_EFFORT": "max",
    "RAG_AGENT_QUERY_EXPANSION_MODEL": "gpt-5.6-luna",
    "RAG_AGENT_QUERY_EXPANSION_REASONING_EFFORT": "max",
    "RAG_AGENT_PLANNER_MODEL": "gpt-5.6-luna",
    "RAG_AGENT_PLANNER_REASONING_EFFORT": "max",
    "RAG_AGENT_CONTEXT_COMPRESSION_MODEL": "gpt-5.6-luna",
    "RAG_AGENT_CONTEXT_COMPRESSION_REASONING_EFFORT": "max",
    "REQUEST_BODY_ANALYZER_MODEL": "gpt-5.6-luna",
    "REQUEST_BODY_ANALYZER_REASONING_EFFORT": "max",
    "REVIEW_AGENT_INTAKE_MODEL": "gpt-5.6-luna",
    "REVIEW_AGENT_INTAKE_REASONING_EFFORT": "max",
    "ENGINEER_HELPER_MODEL": "gpt-5.6-luna",
    "ENGINEER_HELPER_REASONING_EFFORT": "max",
    "KNOWLEDGE_INGESTION_MODEL": "gpt-5.6-luna",
    "DEPLOY_REPORT_MODEL": "gpt-5.6-luna",
    "DEPLOY_REPORT_REASONING_EFFORT": "max",
    # Investigation keeps its astra/medium pin for request-body echo
    # consistency; the Hermes gateway ignores the client model name and
    # resolves the real execution model from its own config.yaml.
    "ENGINEER_INVESTIGATION_REPLY_MODEL": "gpt-6-astra",
    "ENGINEER_INVESTIGATION_REPLY_REASONING_EFFORT": "medium",
}
# Deployment-pinned single-model tiering: Preproduction renders read the SSM
# agent-model parameter once per release and pin AGENT_MODEL_ID into the
# same api/route/worker revision. Production renders must never carry the
# pin (the policy is Preproduction-only), and the retired p2-160
# per-scenario LLM overrides are stripped in both environments.
AGENT_MODEL_ENV_NAME = "AGENT_MODEL_ID"
# AgentRelay transport for the enablement auto workflow (p2-163): the worker
# always carries the relay identity in both modes so late results stay
# receivable after a manual-mode switch; values live under the environment SSM
# prefix and are provisioned via docs/operations/agentrelay-server-provisioning-prompt.md.
AGENTRELAY_SECRET_SUFFIXES = {
    "AGENTRELAY_BASE_URL": "agentrelay-base-url",
    "AGENTRELAY_AGENT_ID": "agentrelay-agent-id",
    "AGENTRELAY_USERNAME": "agentrelay-username",
    "AGENTRELAY_TOKEN": "agentrelay-token",
}
# WeKnora env names known to the renderer. The dual-write client set below
# is rendered fresh on every Preproduction worker revision (stage 6,
# p2-197); WEKNORA_MEMORY_IDENTITY is deliberately NOT part of it — the
# pinned fork contract has no memory operations, so the identity stays
# unset and the client fails closed rather than guessing one. All names in
# this map are stripped from observed definitions before the canonical
# client set is rendered, so repeat renders never resurrect retired or
# stale references.
WEKNORA_SECRET_SUFFIXES = {
    "WEKNORA_BASE_URL": "weknora-base-url",
    "WEKNORA_API_TOKEN": "weknora-api-token",
    "WEKNORA_API_CONTRACT_JSON": "weknora-api-contract-json",
    "WEKNORA_KNOWLEDGE_BASE_ID": "weknora-knowledge-base-id",
    "WEKNORA_MEMORY_IDENTITY": "weknora-memory-identity",
    "HERMES_WEKNORA_BASE_URL": "weknora-base-url",
    "HERMES_WEKNORA_API_TOKEN": "weknora-api-token",
    "HERMES_WEKNORA_KNOWLEDGE_BASE_ID": "weknora-knowledge-base-id",
}
WEKNORA_CLIENT_SECRET_SUFFIXES = {
    name: suffix
    for name, suffix in WEKNORA_SECRET_SUFFIXES.items()
    if name != "WEKNORA_MEMORY_IDENTITY"
}
# The fork instance authenticates API keys via the X-API-Key header with no
# scheme prefix (the client's Authorization/Bearer default is rejected 401).
WEKNORA_CLIENT_ENV = {
    "WEKNORA_AUTH_HEADER_NAME": "X-API-Key",
    "WEKNORA_AUTH_SCHEME": "",
}
# Retired Enablement runtime dependency gate (p2-149/p2-163): Worker task
# definitions must not carry the Archer credential in either mode — since
# p2-163 the auto workflow dispatches AgentRelay tasks executed on the Mac via
# the pilot CLI, so the ECS runtime holds no personal Archer credentials.
# Formal upgrades strip the secret from observed definitions and the
# register-time contract fails closed on it.
RETIRED_WORKER_SECRET_NAMES = {"ARCHER_OAUTH_COOKIE"}
KNOWLEDGE_GOVERNANCE_SWITCHES = (
    "KNOWLEDGE_SOURCE_INTAKE_ENABLED",
    "KNOWLEDGE_SUMMARY_REVIEW_ENABLED",
    "KNOWLEDGE_DUALWRITE_WORKER_ENABLED",
    "KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED",
    "KNOWLEDGE_WEKNORA_DELIVERY_ENABLED",
    "KNOWLEDGE_SLACK_NOTIFY_ENABLED",
)
KNOWLEDGE_PREPRODUCTION_SECRET_SUFFIXES = {
    "AGENT_MEMORY_WIKI_BASE_URL": "agent-memory-wiki-base-url",
    "AGENT_MEMORY_WIKI_USER_KEY": "hermes-wiki-core-user-key",
    "AGENT_MEMORY_WIKI_SERVICE_ID": "agent-memory-wiki-service-id",
    "AGENT_MEMORY_WIKI_TEAM_ID": "agent-memory-wiki-team-id",
    "KNOWLEDGE_SLACK_WORKSPACE_DOMAIN": "knowledge-slack-workspace-domain",
}
KNOWLEDGE_SLACK_REVIEW_LEASE_SECONDS = "300"
# Stage-6 staged enablement (p2-197 step 2): the knowledge stage config is
# the ONLY formal way the governance switches turn on. It is validated at
# render time, rendered verbatim into the task definitions, bound into the
# release pipeline checkpoint, and re-verified after render — manual ECS
# edits are never the enablement path.
KNOWLEDGE_MASTER_SWITCH_ENV = "HERMES_KNOWLEDGE_WORKFLOW_ENABLED"
KNOWLEDGE_STAGE_CONFIG_KEYS = frozenset({"master", "switches", "source_allowlist"})
# A switch may only be on when its prerequisite is on. The layering comes
# from the runtime contracts (knowledge_dual_write: the six switches are
# layered under the master; delivery rows are processed only by the
# dual-write worker; Slack notifications are sent by the same worker) — a
# configuration that violates it would queue work nothing consumes.
KNOWLEDGE_STAGE_SWITCH_DEPENDENCIES = {
    "KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED": "KNOWLEDGE_DUALWRITE_WORKER_ENABLED",
    "KNOWLEDGE_WEKNORA_DELIVERY_ENABLED": "KNOWLEDGE_DUALWRITE_WORKER_ENABLED",
    "KNOWLEDGE_SLACK_NOTIFY_ENABLED": "KNOWLEDGE_DUALWRITE_WORKER_ENABLED",
}


def default_knowledge_stage_config() -> dict[str, Any]:
    """The all-off stage: every release without an explicit stage config."""
    return {
        "master": "0",
        "switches": {name: "0" for name in KNOWLEDGE_GOVERNANCE_SWITCHES},
        "source_allowlist": "",
    }


def load_knowledge_stage_config(source: str | None) -> dict[str, Any]:
    """Load and validate a knowledge stage config from a JSON file path.

    Returns the normalized config: master and all six switches as "0"/"1"
    plus the source allowlist string. Defaults (no source) are all-off with
    an empty allowlist. Invalid files, unknown keys, non-"0"/"1" values,
    dead allowlist entries, and dependency violations are hard errors —
    the rollout never renders a config the runtime would silently half
    honor."""
    config = default_knowledge_stage_config()
    if source is None:
        return config
    path = Path(source)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"knowledge stage config {source} is not a readable JSON file: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("knowledge stage config must be a JSON object")
    unknown = set(payload) - KNOWLEDGE_STAGE_CONFIG_KEYS
    if unknown:
        raise ValueError(
            f"knowledge stage config has unknown keys {sorted(unknown)}; "
            f"valid keys are {sorted(KNOWLEDGE_STAGE_CONFIG_KEYS)}"
        )
    if "master" in payload:
        master = payload["master"]
        if not (isinstance(master, str) and master in {"0", "1"}):
            raise ValueError('knowledge stage config master must be the string "0" or "1"')
        config["master"] = master
    if "switches" in payload:
        switches = payload["switches"]
        if not isinstance(switches, dict):
            raise ValueError("knowledge stage config switches must be an object")
        unknown_switches = set(switches) - set(KNOWLEDGE_GOVERNANCE_SWITCHES)
        if unknown_switches:
            raise ValueError(
                f"knowledge stage config has unknown switches {sorted(unknown_switches)}; "
                f"valid switches are {list(KNOWLEDGE_GOVERNANCE_SWITCHES)}"
            )
        for name, value in switches.items():
            if not (isinstance(value, str) and value in {"0", "1"}):
                raise ValueError(
                    f'knowledge stage config switch {name} must be the string "0" or "1"'
                )
            config["switches"][name] = value
    if "source_allowlist" in payload:
        allowlist = payload["source_allowlist"]
        if not isinstance(allowlist, str):
            raise ValueError("knowledge stage config source_allowlist must be a string")
        config["source_allowlist"] = allowlist
    validate_allowlist(config["source_allowlist"])
    if config["master"] != "1" and any(
        value == "1" for value in config["switches"].values()
    ):
        raise ValueError(
            "knowledge stage config switches are layered under "
            f"{KNOWLEDGE_MASTER_SWITCH_ENV}: no switch may be \"1\" while master is \"0\""
        )
    for name, prerequisite in KNOWLEDGE_STAGE_SWITCH_DEPENDENCIES.items():
        if config["switches"][name] == "1" and config["switches"][prerequisite] != "1":
            raise ValueError(
                f"knowledge stage config {name}=\"1\" requires {prerequisite}=\"1\" "
                "(delivery rows and Slack notifications are processed only by the "
                "dual-write worker)"
            )
    return config
REGISTER_TASK_DEFINITION_FIELDS = {
    "family",
    "taskRoleArn",
    "executionRoleArn",
    "networkMode",
    "containerDefinitions",
    "volumes",
    "placementConstraints",
    "requiresCompatibilities",
    "cpu",
    "memory",
    "runtimePlatform",
    "ephemeralStorage",
    "proxyConfiguration",
    "inferenceAccelerators",
    "pidMode",
    "ipcMode",
}


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def validate_promotion(manifest_path: str | Path, record_path: str | Path) -> dict[str, Any]:
    manifest = read_manifest(Path(manifest_path))
    record = _read_json(record_path)
    if record.get("schema_version") != "automation-promotion-v1":
        raise ValueError("invalid Promotion Record schema")
    if record.get("release_id") != manifest.release_id:
        raise ValueError("Promotion Record release_id does not match manifest")
    if record.get("target_repository") != "supportportal/production":
        raise ValueError("Promotion Record target repository must be supportportal/production")
    source_repository = record.get("source_repository")
    if source_repository not in {
        "local-oci",
        "supportportal/preproduction",
    }:
        raise ValueError("Promotion Record source repository is not approved")
    promotion_mode = record.get("promotion_mode")
    if promotion_mode is None:
        promotion_mode = (
            "preproduction-accepted"
            if source_repository == "supportportal/preproduction"
            else "local-oci-direct-production"
        )
    expected_source = {
        "preproduction-accepted": "supportportal/preproduction",
        "codebuild-direct-production": "supportportal/preproduction",
        "local-oci-direct-production": "local-oci",
    }.get(promotion_mode)
    if expected_source is None:
        raise ValueError("Promotion Record promotion_mode is not approved")
    if source_repository != expected_source:
        raise ValueError("Promotion Record source repository does not match promotion_mode")
    if promotion_mode in {"preproduction-accepted", "codebuild-direct-production"}:
        value = str(record.get("source_publish_record_sha256") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("Promotion Record source_publish_record_sha256 is invalid")
    if promotion_mode == "preproduction-accepted":
        for field in (
            "preproduction_deploy_evidence_sha256",
        ):
            value = str(record.get(field) or "")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError(f"Promotion Record {field} is invalid")
    elif "preproduction_deploy_evidence_sha256" in record:
        raise ValueError(
            "Promotion Record preproduction_deploy_evidence_sha256 is not allowed for this promotion_mode"
        )
    if (
        promotion_mode == "local-oci-direct-production"
        and "source_publish_record_sha256" in record
    ):
        raise ValueError(
            "Promotion Record source_publish_record_sha256 is not allowed for this promotion_mode"
        )
    if set(record.get("components") or {}) != {"api", "route", "worker"}:
        raise ValueError("Promotion Record must contain api, route, and worker")
    for role, component in manifest.components.items():
        promoted = record["components"][role]
        if promoted.get("tag") != component.tag:
            raise ValueError(f"Promotion Record {role} tag mismatch")
        if promoted.get("source_digest") != component.digest:
            raise ValueError(f"Promotion Record {role} source digest mismatch")
        if promoted.get("target_digest") != component.digest:
            raise ValueError(f"Promotion Record {role} target digest mismatch")
    registry_id = str(record.get("registry_id") or "").strip()
    region = str(record.get("region") or "").strip()
    if len(registry_id) != 12 or not registry_id.isdigit() or not region:
        raise ValueError("Promotion Record registry_id/region is invalid")
    return {
        "release_id": manifest.release_id,
        "registry_id": registry_id,
        "region": region,
        "repository": record["target_repository"],
    }


def validate_preproduction_publish(
    manifest_path: str | Path,
    record_path: str | Path,
) -> dict[str, Any]:
    manifest = read_manifest(Path(manifest_path))
    record = read_preproduction_publish_record(Path(record_path))
    validate_preproduction_publish_record(manifest, record)
    return {
        "release_id": manifest.release_id,
        "registry_id": record.registry_id,
        "region": record.region,
        "repository": record.target_repository,
    }


def _sha256(path: str | Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_preproduction_acceptance(
    manifest_path: str | Path,
    publish_record_path: str | Path,
    evidence_path: str | Path,
) -> dict[str, Any]:
    publish = validate_preproduction_publish(manifest_path, publish_record_path)
    manifest = read_manifest(Path(manifest_path))
    evidence = _read_json(evidence_path)
    if evidence.get("schema_version") != "automation-ecs-deploy-evidence-v1":
        raise ValueError("invalid Preproduction deploy evidence schema")
    if evidence.get("status") != "complete" or evidence.get("environment") != "preproduction":
        raise ValueError("Preproduction deploy evidence is not complete")
    if evidence.get("release_id") != manifest.release_id or evidence.get("git_commit") != manifest.git_commit:
        raise ValueError("Preproduction deploy evidence release identity mismatch")
    prompt = evidence.get("prompt_release") or {}
    if prompt.get("release_id") != manifest.prompt_release_id:
        raise ValueError("Preproduction deploy evidence Prompt Release mismatch")
    registry = evidence.get("registry") or {}
    if (
        str(registry.get("id") or "") != publish["registry_id"]
        or registry.get("repository") != "supportportal/preproduction"
    ):
        raise ValueError("Preproduction deploy evidence registry mismatch")
    components = evidence.get("components") or {}
    if set(components) != {"api", "route", "worker"}:
        raise ValueError("Preproduction deploy evidence must contain all roles")
    for role, component in manifest.components.items():
        observed = components[role]
        if observed.get("expected_digest") != component.digest or observed.get("runtime_verified") is not True:
            raise ValueError(f"Preproduction {role} runtime evidence is incomplete")
    checks = evidence.get("checks") or {}
    required_passed = {
        "terraform_zero_drift",
        "source_prompt",
        "ecr",
        "suspension_recipients",
        "heartbeats",
        "public_health",
        "cloudwatch",
        "ec2_backup",
    }
    failed = sorted(name for name in required_passed if checks.get(name) != "passed")
    if failed:
        raise ValueError(f"Preproduction deploy evidence checks failed: {failed}")
    if checks.get("schema_bootstrap") not in {"passed", "skipped_current"}:
        raise ValueError("Preproduction schema bootstrap evidence is incomplete")
    if checks.get("prompt_sync") != "active" or checks.get("prompt_activation") != "active":
        raise ValueError("Preproduction Prompt activation evidence is incomplete")
    return {
        **publish,
        "publish_record_sha256": _sha256(publish_record_path),
        "deploy_evidence_sha256": _sha256(evidence_path),
    }


def _container(task_definition: dict[str, Any], role: str) -> dict[str, Any]:
    matches = [
        item
        for item in task_definition.get("containerDefinitions") or []
        if str(item.get("name") or "") == role
    ]
    if len(matches) != 1:
        raise ValueError(f"task definition must contain exactly one {role} container")
    return matches[0]


def _environment_map(container: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name") or ""): str(item.get("value") or "")
        for item in container.get("environment") or []
    }


def _secret_names(container: dict[str, Any]) -> set[str]:
    return {
        str(item.get("name") or "")
        for item in container.get("secrets") or []
    }


def _set_environment_value(container: dict[str, Any], name: str, value: str) -> None:
    environment = container.setdefault("environment", [])
    matches = [item for item in environment if str(item.get("name") or "") == name]
    if len(matches) > 1:
        raise ValueError(f"task definition contains duplicate environment: {name}")
    if matches:
        matches[0]["value"] = value
        return
    environment.append({"name": name, "value": value})


def _set_secret_reference(container: dict[str, Any], name: str, value_from: str) -> None:
    secrets = container.setdefault("secrets", [])
    matches = [item for item in secrets if str(item.get("name") or "") == name]
    if len(matches) > 1:
        raise ValueError(f"task definition contains duplicate secret: {name}")
    if matches:
        matches[0]["valueFrom"] = value_from
        return
    secrets.append({"name": name, "valueFrom": value_from})


def _remove_secret_references(container: dict[str, Any], names: set[str]) -> None:
    container["secrets"] = [
        item
        for item in container.get("secrets") or []
        if str(item.get("name") or "") not in names
    ]


def _remove_environment_values(container: dict[str, Any], names: set[str]) -> None:
    container["environment"] = [
        item
        for item in container.get("environment") or []
        if str(item.get("name") or "") not in names
    ]


def _registrable_task_definition(task_definition: dict[str, Any]) -> dict[str, Any]:
    return {
        key: json.loads(json.dumps(value))
        for key, value in task_definition.items()
        if key in REGISTER_TASK_DEFINITION_FIELDS
    }


def _apply_preproduction_knowledge_configuration(
    container: dict[str, Any],
    *,
    environment: str,
    parameter_prefix_arn: str,
    knowledge_source_allowlist: str | None = None,
    knowledge_stage_config: dict[str, Any] | None = None,
) -> None:
    """Render the stage-6 knowledge configuration on API and Worker only.

    The master switch and all governance switches come from the validated
    knowledge stage config (all-off by default — the first Preproduction
    release). The AgentMemory and Slack values are SSM references so no
    secret or environment-specific value is copied into the
    task-definition JSON.
    """
    if environment != "preproduction":
        return
    stage = knowledge_stage_config or default_knowledge_stage_config()
    _set_environment_value(container, KNOWLEDGE_MASTER_SWITCH_ENV, stage["master"])
    for name in KNOWLEDGE_GOVERNANCE_SWITCHES:
        _set_environment_value(container, name, stage["switches"][name])
    if knowledge_stage_config is not None:
        allowlist = str(knowledge_stage_config.get("source_allowlist") or "")
    else:
        allowlist = (
            knowledge_source_allowlist
            if knowledge_source_allowlist is not None
            else os.getenv("KNOWLEDGE_SOURCE_ALLOWLIST", "")
        )
    _set_environment_value(container, "KNOWLEDGE_SOURCE_ALLOWLIST", str(allowlist or ""))
    _set_environment_value(
        container,
        "KNOWLEDGE_SLACK_REVIEW_LEASE_SECONDS",
        KNOWLEDGE_SLACK_REVIEW_LEASE_SECONDS,
    )
    names = set(KNOWLEDGE_PREPRODUCTION_SECRET_SUFFIXES)
    _remove_environment_values(container, names)
    _remove_secret_references(container, names)
    for name, suffix in sorted(KNOWLEDGE_PREPRODUCTION_SECRET_SUFFIXES.items()):
        _set_secret_reference(container, name, _parameter_arn(parameter_prefix_arn, suffix))


def _apply_preproduction_weknora_client_configuration(
    container: dict[str, Any],
    *,
    environment: str,
    parameter_prefix_arn: str,
) -> None:
    """Render the WeKnora dual-write client configuration (Worker only).

    Stage 6 (p2-197): the worker carries the fork client credentials, the
    pinned API contract, the knowledge base id, and the X-API-Key auth
    shape on every rendered Preproduction revision. Observed definitions
    are normalized first (every known WeKnora name, including the retired
    memory identity, is stripped) so repeat renders stay idempotent. The
    retired p2-181 auto-promotion path (WEKNORA_PROMOTION_ENABLED) stays
    explicitly off — the KNOWLEDGE_WEKNORA_DELIVERY_ENABLED switch in the
    knowledge stage config is the only way WeKnora writes turn on.
    """
    if environment != "preproduction":
        return
    _remove_secret_references(container, set(WEKNORA_SECRET_SUFFIXES))
    _remove_environment_values(container, set(WEKNORA_SECRET_SUFFIXES) | set(WEKNORA_CLIENT_ENV))
    for name, suffix in sorted(WEKNORA_CLIENT_SECRET_SUFFIXES.items()):
        _set_secret_reference(container, name, _parameter_arn(parameter_prefix_arn, suffix))
    for name, value in WEKNORA_CLIENT_ENV.items():
        _set_environment_value(container, name, value)
    _set_environment_value(container, "WEKNORA_PROMOTION_ENABLED", "0")


def _parameter_arn(prefix_arn: str, name: str) -> str:
    return f"{prefix_arn.rstrip('/')}/{name}"


def _parameter_prefix_arn(container: dict[str, Any], *, environment: str) -> str:
    references = [
        str(item.get("valueFrom") or "")
        for item in container.get("secrets") or []
        if str(item.get("name") or "") == "AUTOMATION_DB_DSN"
    ]
    if len(references) != 1:
        raise ValueError("task definition requires one AUTOMATION_DB_DSN secret")
    reference = references[0]
    suffix = f"/supportportal/{environment}/automation-db-dsn"
    if ":parameter/" not in reference or not reference.endswith(suffix):
        raise ValueError("AUTOMATION_DB_DSN must use the environment SSM parameter prefix")
    return reference[: -len("/automation-db-dsn")]


def _base_environment(
    *,
    manifest: Any,
    role: str,
    environment: str,
    hermes_case_workflow_mode: str,
    automation_case_engine: str = "legacy",
    enablement_workflow_mode: str = "manual",
    knowledge_stage_config: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    values = {
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "0",
        "RUNTIME_SCHEMA_MODE": "check",
        "TICKET_DB_SCHEMA": f"supportportal_{environment}",
        "AUTOMATION_BASE_PATH": f"/automation/{environment}",
        "AUTOMATION_DB_RESOURCE_ID": f"supportportal-{environment}",
        "APP_BUILD_REF": manifest.git_commit,
        "AUTOMATION_ENVIRONMENT": environment,
        "AUTOMATION_JOB_NAMESPACE": f"supportportal-{environment}",
        "AUTOMATION_RELEASE_ID": manifest.release_id,
        "AUTOMATION_DB_SCHEMA": f"supportportal_{environment}",
        "PROMPT_RELEASE_REQUIRED": "true",
        "APP_BUILD_TIME": manifest.build_time.isoformat().replace("+00:00", "Z"),
        "AUTOMATION_IMAGE_DIGEST": manifest.components[role].digest,
        "PROMPT_RELEASE_ID": manifest.prompt_release_id,
    }
    if role in {"api", "worker"}:
        values.update(
            {
                "ENGINEER_SLACK_CHANNEL_ID": "C0BS0N61D1R",
                "ENGINEER_SLACK_TEAM_ID": "T1CBEDLJY",
                # Production keeps credentials wired but stops posting to the
                # engineer channel (p2-150); preproduction stays on so the
                # hermes review summons keeps working.
                "ENGINEER_SLACK_OUTBOUND_ENABLED": "0" if environment == "production" else "1",
                "ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS": "300",
                "HERMES_CASE_WORKFLOW_MODE": hermes_case_workflow_mode,
                "ENABLEMENT_WORKFLOW_MODE": enablement_workflow_mode,
            }
        )
    if role in {"api", "worker"} and environment == "preproduction":
        # Knowledge governance master switch: the stage-6 knowledge stage
        # config owns the value (all-off by default); runtime readiness
        # (real Hermes mode + configured gateway) alone never re-enables
        # governance. The knowledge configuration call later in the render
        # flow re-applies the same validated value, so this seed exists only
        # for roles/paths that never reach it.
        values["HERMES_KNOWLEDGE_WORKFLOW_ENABLED"] = str(
            (knowledge_stage_config or default_knowledge_stage_config())["master"]
        )
        if role == "worker":
            # The retired promotion path stays explicitly off, never derived
            # from the Hermes workflow mode.
            values["WEKNORA_PROMOTION_ENABLED"] = "0"
    if role in {"route", "worker"}:
        values.update(
            {
                "TICKET_DB_APPLICATION_NAME": f"supportportal-automation-ecs-{role}-{environment}",
                "PROMPT_RUNTIME_SERVICE": f"automation-ecs-{role}-{environment}",
            }
        )
        if role == "route":
            values["AUTOMATION_CASE_ENGINE"] = automation_case_engine
    if role == "api":
        # The hermes engine executes business tools on the API role; without
        # this flag every action is silently blocked and reports a fake
        # success (ticket 13567). Mirrors the worker value.
        values["AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED"] = "1"
        # Graph mail config for tool escalation alerts (ticket 13580) and
        # the suspension/fraud internal email execution chain (13922).
        values.update(API_GRAPH_MAIL_ENV)
    if role == "worker":
        values.update(
            {
                "ACCOUNT_REPLY_POLLER_ENABLED": "false",
                "ACCOUNT_REPLY_LEGACY_POLLER_ENABLED": "true",
                "AUTOMATION_ECS_ACCOUNT_ONLY": "1",
                "AUTOMATION_REPLY_POLL_ENABLED": "true",
                "AUTOMATION_REPLY_POLL_INTERVAL_SECONDS": "300",
                "AUTOMATION_REPLY_POLL_MAX_MESSAGES": "25",
                "AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED": "1",
                "ACCOUNT_DEFAULT_PROCESSING_PROFILE": environment,
                "INTERNAL_EMAIL_SUBJECT_NAMESPACE": f"[{environment}-automation]",
                "CLIENT_RAG_SERVICE_TIMEOUT_SECONDS": "180",
                "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE": "/app/.msgraph/billing-automation-token.json",
                "BILLING_AUTOMATION_REPLY_RECORD_PATH": "/app/.msgraph/billing-request-replies.jsonl",
                "BILLING_AUTOMATION_GRAPH_TENANT_ID": "60275374-3eaa-49c2-83c3-cc189d126981",
                "BILLING_AUTOMATION_GRAPH_CLIENT_ID": "cb5aaefe-2ee2-4ac9-a3ee-5490ddf70d80",
                "BILLING_AUTOMATION_GRAPH_USERNAME": "ai-support-agent@agora.io",
            }
        )
    return [{"name": name, "value": values[name]} for name in sorted(values)]


def _ensure_graph_efs_volume(
    task_definition: dict[str, Any],
    container: dict[str, Any],
    *,
    file_system_id: str,
    access_point_id: str,
) -> None:
    """Attach the shared Graph token-cache EFS volume and mount (13922).

    Idempotent: an existing graph-token-cache volume or mount point is
    left untouched so repeat renders never duplicate or reset live IDs.
    """
    volume = {
        "name": "graph-token-cache",
        "efsVolumeConfiguration": {
            "fileSystemId": file_system_id,
            "rootDirectory": "/",
            "transitEncryption": "ENABLED",
            "authorizationConfig": {
                "accessPointId": access_point_id,
                "iam": "ENABLED",
            },
        },
    }
    volumes = task_definition.setdefault("volumes", [])
    if not any(str(item.get("name") or "") == "graph-token-cache" for item in volumes):
        volumes.append(volume)
    mount_points = container.setdefault("mountPoints", [])
    if not any(
        str(item.get("sourceVolume") or "") == "graph-token-cache"
        for item in mount_points
    ):
        mount_points.append(
            {
                "sourceVolume": "graph-token-cache",
                "containerPath": "/app/.msgraph",
                "readOnly": False,
            }
        )


def render_initial_task_definition(
    *,
    role: str,
    manifest_path: str | Path,
    registry_id: str,
    region: str,
    environment: str,
    repository: str,
    execution_role_arn: str,
    task_role_arn: str,
    log_group_name: str,
    parameter_prefix_arn: str,
    hermes_case_workflow_mode: str = "disabled",
    hermes_persona_enabled: bool = False,
    automation_case_engine: str = "legacy",
    hermes_agent_enabled: bool = False,
    enablement_workflow_mode: str = "manual",
    graph_efs_file_system_id: str | None = None,
    graph_efs_access_point_id: str | None = None,
    agent_model: str | None = None,
    knowledge_source_allowlist: str | None = None,
    knowledge_stage_config: str | None = None,
) -> dict[str, Any]:
    if role not in {"api", "route", "worker"}:
        raise ValueError("role must be api, route, or worker")
    if environment != "preproduction":
        raise ValueError("initial task definitions may only target preproduction")
    if repository != "supportportal/preproduction":
        raise ValueError("initial Preproduction repository must be supportportal/preproduction")
    if knowledge_stage_config and knowledge_source_allowlist is not None:
        raise ValueError(
            "knowledge stage config and the standalone source allowlist are "
            "mutually exclusive; put source_allowlist in the stage config"
        )
    stage_config = load_knowledge_stage_config(knowledge_stage_config)
    normalized_agent_model = " ".join(str(agent_model or "").split()).strip()
    if not normalized_agent_model:
        raise ValueError(
            "agent_model is required for initial preproduction renders: the "
            "SSM agent-model parameter is missing or empty"
        )
    if hermes_case_workflow_mode not in HERMES_CASE_WORKFLOW_MODES:
        raise ValueError("Hermes Case Workflow mode must be disabled, mock, or real")
    if automation_case_engine not in AUTOMATION_CASE_ENGINES:
        raise ValueError("automation case engine must be legacy or hermes")
    if enablement_workflow_mode not in ENABLEMENT_WORKFLOW_MODES:
        raise ValueError("enablement workflow mode must be manual or archer")
    if automation_case_engine == "hermes" and not hermes_agent_enabled:
        raise ValueError("the hermes engine requires hermes agent credentials")
    if role in {"api", "worker"} and not (graph_efs_file_system_id and graph_efs_access_point_id):
        raise ValueError(f"{role} initial task definition requires Graph EFS inputs")

    manifest = read_manifest(Path(manifest_path))
    component = manifest.components[role]
    commands = {
        "api": [
            "python",
            "-m",
            "uvicorn",
            "backend.automation_ecs_api:create_app",
            "--factory",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        "route": ["python", "-m", "backend.automation_ecs_route_worker"],
        "worker": ["python", "-m", "backend.automation_ecs_worker"],
    }
    secret_names = {
        "api": {
            "AUTOMATION_DB_DSN": "automation-db-dsn",
            "AUTOMATION_INTAKE_SHARED_TOKEN": "automation-intake-shared-token",
            "AUTOMATION_DASHBOARD_SESSION_SECRET": "dashboard-session-secret",
            "TICKET_DB_DSN": "automation-db-dsn",
            "n8n_request_token": "n8n-request-token",
            # The dashboard hermes-review approve path resolves the current
            # Zendesk comments revision when no sync row exists yet, which
            # needs the Zendesk credentials and configured assignee email.
            "zendesk_basic_auth": "zendesk-basic-auth",
            "ZENDESK_AI_ASSIGNEE_EMAIL": "zendesk-ai-assignee-email",
            # Hermes-agent tool executions on the API role need the LLM
            # credential (enablement grounded field extraction).
            "OPENAI_API_KEY": "openai-api-key",
            # Graph mail credentials for the failure-alert email sent by
            # hermes tool escalation (the tool runs on the api role).
            "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET": "billing-graph-client-secret",
            # Internal email recipients for the Hermes automation execution
            # (the tool sends the suspension/fraud handoff email from the
            # api container, not the worker).
            "FRAUD_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": "fraud-internal-email-recipients",
            "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": "account-suspension-internal-email-recipients",
        },
        "route": {
            "AUTOMATION_DB_DSN": "automation-db-dsn",
            "OPENAI_API_KEY": "openai-api-key",
            "TICKET_DB_DSN": "automation-db-dsn",
        },
        "worker": {
            "AUTOMATION_DB_DSN": "automation-db-dsn",
            "TICKET_DB_DSN": "automation-db-dsn",
            "zendesk_basic_auth": "zendesk-basic-auth",
            "OPENAI_API_KEY": "openai-api-key",
            "n8n_request_token": "n8n-request-token",
            "RAGFLOW_BASE_URL": "rag-service-url",
            "RAGFLOW_API_KEY": "rag-service-shared-token",
            "ZENDESK_AI_ASSIGNEE_EMAIL": "zendesk-ai-assignee-email",
            "ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID": "zendesk-fraud-review-assignee-id",
            "ACCOUNT_SLACK_N8N_WEBHOOK_URL": "account-slack-n8n-webhook-url",
            "ACCOUNT_SLACK_N8N_STATUS_URL": "account-slack-n8n-status-url",
            "ENGINEER_SLACK_ACCESS_TOKEN": "engineer-slack-access-token",
            "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET": "billing-graph-client-secret",
            "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": "enablement-internal-email-recipients",
            "FRAUD_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": "fraud-internal-email-recipients",
            "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON": "account-suspension-internal-email-recipients",
        },
    }
    if role in {"api", "worker"} and (
        hermes_persona_enabled or hermes_case_workflow_mode != "disabled"
    ):
        secret_names[role].update(
            {
                "ENGINEER_INVESTIGATION_REPLY_BASE_URL": "hermes-base-url",
                "ENGINEER_INVESTIGATION_REPLY_API_KEY": "hermes-api-server-key",
            }
        )
    if role == "api" and hermes_case_workflow_mode != "disabled":
        secret_names[role]["HERMES_CALLBACK_TOKEN"] = "hermes-callback-token"
        if environment == "preproduction":
            secret_names[role]["ENGINEER_SLACK_ACCESS_TOKEN"] = "engineer-slack-access-token"
    if hermes_agent_enabled and role in {"api", "worker"}:
        secret_names[role].update(
            {
                "HERMES_AGENT_BASE_URL": "hermes-agent-base-url",
                "HERMES_AGENT_API_TOKEN": "hermes-api-server-key",
            }
        )
    if role == "worker":
        secret_names[role].update(AGENTRELAY_SECRET_SUFFIXES)
    container: dict[str, Any] = {
        "name": role,
        "image": (
            f"{registry_id}.dkr.ecr.{region}.amazonaws.com/"
            f"{repository}@{component.digest}"
        ),
        "cpu": {"api": 512, "route": 256, "worker": 512}[role],
        "essential": True,
        "command": commands[role],
        "environment": _base_environment(
            manifest=manifest,
            role=role,
            environment=environment,
            hermes_case_workflow_mode=hermes_case_workflow_mode,
            automation_case_engine=automation_case_engine,
            enablement_workflow_mode=enablement_workflow_mode,
            knowledge_stage_config=stage_config,
        ),
        "secrets": [
            {"name": name, "valueFrom": _parameter_arn(parameter_prefix_arn, suffix)}
            for name, suffix in sorted(secret_names[role].items())
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group_name,
                "awslogs-region": region,
                "awslogs-stream-prefix": role,
            },
        },
    }
    if role == "api":
        container["portMappings"] = [
            {
                "name": "api-8000-tcp",
                "containerPort": 8000,
                "hostPort": 8000,
                "protocol": "tcp",
                "appProtocol": "http",
            }
        ]
        container["healthCheck"] = {
            "command": [
                "CMD-SHELL",
                "python -c \"import urllib.request; "
                "urllib.request.urlopen('http://127.0.0.1:8000/automation/preproduction/health/live', timeout=3)\" "
                "|| exit 1",
            ],
            "interval": 30,
            "timeout": 5,
            "retries": 3,
            "startPeriod": 60,
        }
    volumes: list[dict[str, Any]] = []
    rendered = {
        "family": f"supportportal-{environment}-{role}",
        "taskRoleArn": task_role_arn,
        "executionRoleArn": execution_role_arn,
        "networkMode": "awsvpc",
        "containerDefinitions": [container],
        "volumes": volumes,
        "requiresCompatibilities": ["FARGATE"],
        "cpu": {"api": "512", "route": "256", "worker": "512"}[role],
        "memory": {"api": "1024", "route": "512", "worker": "1024"}[role],
        "runtimePlatform": {
            "cpuArchitecture": "X86_64",
            "operatingSystemFamily": "LINUX",
        },
    }
    _set_environment_value(container, AGENT_MODEL_ENV_NAME, normalized_agent_model)
    if role in {"api", "worker"}:
        # Graph EFS requirement validated above; attach the shared volume
        # and mount for both mail-sending roles (13922).
        _ensure_graph_efs_volume(
            rendered,
            container,
            file_system_id=str(graph_efs_file_system_id),
            access_point_id=str(graph_efs_access_point_id),
        )
    if role in {"api", "worker"}:
        _apply_preproduction_knowledge_configuration(
            container,
            environment=environment,
            parameter_prefix_arn=parameter_prefix_arn,
            knowledge_source_allowlist=knowledge_source_allowlist,
            knowledge_stage_config=stage_config,
        )
    if role == "worker":
        _apply_preproduction_weknora_client_configuration(
            container,
            environment=environment,
            parameter_prefix_arn=parameter_prefix_arn,
        )
        validate_worker_contract(rendered)
    return rendered


def _enablement_mode_from_environment(environment: dict[str, str]) -> str:
    value = str(environment.get("ENABLEMENT_WORKFLOW_MODE") or "").strip().lower() or "manual"
    if value not in ENABLEMENT_WORKFLOW_MODES:
        raise ValueError(f"ENABLEMENT_WORKFLOW_MODE must be manual or archer, got {value!r}")
    return value


def validate_worker_contract(task_definition: dict[str, Any]) -> None:
    container = _container(task_definition, "worker")
    environment = _environment_map(container)
    forbidden_names = {"PILOT_BIN", "XDG_CONFIG_HOME", "PILOT_HOME"}
    if forbidden_names & set(environment):
        raise ValueError("Worker task definition contains forbidden Pilot environment")
    if any("pilot" in str(value).lower() for value in environment.values()):
        raise ValueError("Worker task definition contains a Pilot environment value")
    if any("pilot" in str(item.get("sourceVolume") or "").lower() for item in container.get("mountPoints") or []):
        raise ValueError("Worker task definition contains a pilot-creds mount")
    if any("pilot" in str(item.get("name") or "").lower() for item in task_definition.get("volumes") or []):
        raise ValueError("Worker task definition contains a pilot-creds volume")
    if "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON" not in _secret_names(container):
        raise ValueError("Worker task definition is missing the Suspension recipients secret")
    _enablement_mode_from_environment(environment)
    if RETIRED_WORKER_SECRET_NAMES & _secret_names(container):
        raise ValueError(
            "Worker task definition contains retired Archer secrets "
            "(the ECS enablement runtime holds no Archer credentials; the auto "
            "workflow dispatches AgentRelay tasks instead)"
        )


def _strip_retired_worker_secrets(task_definition: dict[str, Any]) -> dict[str, Any]:
    """Drop retired Enablement secrets from an observed Worker definition.

    Formal upgrades render from the live task definition, so an old revision
    that still injects ARCHER_OAUTH_COOKIE would otherwise carry the secret
    into every new revision.  Manual-mode renders strip before validation so
    observed legacy definitions upgrade cleanly while the rendered output
    stays fail-closed; archer-mode renders keep the credential (the mode
    contract below then requires it).
    """

    container = _container(task_definition, "worker")
    _remove_secret_references(container, RETIRED_WORKER_SECRET_NAMES)
    _remove_environment_values(container, RETIRED_WORKER_SECRET_NAMES)
    return task_definition


def render_task_definition(
    *,
    role: str,
    current_path: str | Path,
    manifest_path: str | Path,
    registry_id: str,
    region: str,
    environment: str = "production",
    repository: str = "supportportal/production",
    hermes_case_workflow_mode: str | None = None,
    hermes_persona_enabled: bool | None = None,
    investigation_case_files_enabled: bool | None = None,
    automation_case_engine: str | None = None,
    hermes_agent_enabled: bool | None = None,
    enablement_workflow_mode: str | None = None,
    agent_model: str | None = None,
    graph_efs_file_system_id: str | None = None,
    graph_efs_access_point_id: str | None = None,
    knowledge_source_allowlist: str | None = None,
    knowledge_stage_config: str | None = None,
) -> dict[str, Any]:
    if investigation_case_files_enabled is True and environment != "preproduction":
        raise ValueError("Investigation case files may only be enabled in Preproduction")
    if role not in {"api", "route", "worker"}:
        raise ValueError("role must be api, route, or worker")
    if environment not in {"preproduction", "production"}:
        raise ValueError("environment must be preproduction or production")
    expected_repository = f"supportportal/{environment}"
    if repository != expected_repository:
        raise ValueError(f"repository must be {expected_repository}")
    if knowledge_stage_config and environment != "preproduction":
        raise ValueError("the knowledge stage config is a Preproduction-only entry point")
    if knowledge_stage_config and knowledge_source_allowlist is not None:
        raise ValueError(
            "knowledge stage config and the standalone source allowlist are "
            "mutually exclusive; put source_allowlist in the stage config"
        )
    stage_config = load_knowledge_stage_config(knowledge_stage_config)
    normalized_agent_model = " ".join(str(agent_model or "").split()).strip()
    if environment == "preproduction":
        if not normalized_agent_model:
            raise ValueError(
                "agent_model is required for preproduction renders: the SSM "
                "agent-model parameter is missing or empty"
            )
    elif normalized_agent_model:
        raise ValueError("agent_model must not be pinned on production renders")
    if (
        enablement_workflow_mode is not None
        and enablement_workflow_mode not in ENABLEMENT_WORKFLOW_MODES
    ):
        raise ValueError("enablement workflow mode must be manual or archer")
    if bool(graph_efs_file_system_id) != bool(graph_efs_access_point_id):
        raise ValueError("api Graph EFS render requires both the file system and access point IDs")
    if (graph_efs_file_system_id or graph_efs_access_point_id) and role != "api":
        raise ValueError("Graph EFS inputs are only valid for api renders")
    source = _read_json(current_path)
    task_definition = source.get("taskDefinition") if "taskDefinition" in source else source
    if not isinstance(task_definition, dict):
        raise ValueError("taskDefinition object is required")
    if role == "worker":
        worker_container = _container(task_definition, "worker")
        effective_enablement_mode = enablement_workflow_mode or _enablement_mode_from_environment(
            _environment_map(worker_container)
        )
        # Normalize the mode on every rendered revision; the ECS runtime holds
        # no Archer credentials in either mode (p2-163).
        _set_environment_value(
            worker_container,
            "ENABLEMENT_WORKFLOW_MODE",
            effective_enablement_mode,
        )
        _strip_retired_worker_secrets(task_definition)
        validate_worker_contract(task_definition)
    if (
        hermes_case_workflow_mode is not None
        and hermes_case_workflow_mode not in HERMES_CASE_WORKFLOW_MODES
    ):
        raise ValueError("Hermes Case Workflow mode must be disabled, mock, or real")
    if automation_case_engine is not None:
        if automation_case_engine not in AUTOMATION_CASE_ENGINES:
            raise ValueError("automation case engine must be legacy or hermes")
        if environment == "production" and automation_case_engine == "hermes":
            raise ValueError("the hermes case engine is not allowed in Production")
    if automation_case_engine == "hermes" and hermes_agent_enabled is False:
        raise ValueError("the hermes engine requires hermes agent credentials")
    manifest = read_manifest(Path(manifest_path))
    component = manifest.components[role]
    rendered = _registrable_task_definition(task_definition)
    container = _container(rendered, role)
    environment_values = _environment_map(container)
    if environment_values.get("AUTOMATION_ENVIRONMENT") != environment:
        raise ValueError("task definition environment does not match deployment target")
    if environment not in environment_values.get("AUTOMATION_DB_SCHEMA", "").lower():
        raise ValueError("task definition DB schema does not match deployment target")
    if environment not in environment_values.get("AUTOMATION_JOB_NAMESPACE", "").lower():
        raise ValueError("task definition job namespace does not match deployment target")
    container["image"] = (
        f"{registry_id}.dkr.ecr.{region}.amazonaws.com/"
        f"{repository}@{component.digest}"
    )
    replacements = {
        "AUTOMATION_RELEASE_ID": manifest.release_id,
        "AUTOMATION_IMAGE_DIGEST": component.digest,
        "APP_BUILD_REF": manifest.git_commit,
        "APP_BUILD_TIME": manifest.build_time.isoformat().replace("+00:00", "Z"),
        "PROMPT_RELEASE_ID": manifest.prompt_release_id,
    }
    environment_items = container.get("environment") or []
    existing_names = {str(item.get("name") or "") for item in environment_items}
    if not PROVENANCE_ENV_NAMES <= existing_names:
        missing = sorted(PROVENANCE_ENV_NAMES - existing_names)
        raise ValueError(f"task definition is missing provenance fields: {missing}")
    for item in environment_items:
        name = str(item.get("name") or "")
        if name in replacements:
            item["value"] = replacements[name]
    if role in {"api", "worker"}:
        if investigation_case_files_enabled is not None:
            _set_environment_value(container, "INVESTIGATION_CASE_FILES_ENABLED", "1" if investigation_case_files_enabled else "0")
        elif "INVESTIGATION_CASE_FILES_ENABLED" not in existing_names:
            _set_environment_value(container, "INVESTIGATION_CASE_FILES_ENABLED", "0")
    if role in {"api", "worker"} and (
        hermes_case_workflow_mode is not None or hermes_persona_enabled is not None
    ):
        effective_mode = (
            hermes_case_workflow_mode
            if hermes_case_workflow_mode is not None
            else environment_values.get("HERMES_CASE_WORKFLOW_MODE", "disabled")
        )
        if hermes_case_workflow_mode is not None:
            _set_environment_value(
                container,
                "HERMES_CASE_WORKFLOW_MODE",
                hermes_case_workflow_mode,
            )
        required_names: set[str] = set()
        if hermes_persona_enabled is True or effective_mode != "disabled":
            required_names.update(HERMES_OUTBOUND_SECRET_NAMES)
        if role == "api" and effective_mode != "disabled":
            required_names.add("HERMES_CALLBACK_TOKEN")
        _remove_environment_values(container, HERMES_SECRET_NAMES)
        _remove_secret_references(container, HERMES_SECRET_NAMES)
        if required_names:
            prefix_arn = _parameter_prefix_arn(container, environment=environment)
            for name in sorted(required_names):
                _set_secret_reference(
                    container,
                    name,
                    _parameter_arn(prefix_arn, HERMES_SECRET_SUFFIXES[name]),
                )
    if automation_case_engine is not None or hermes_agent_enabled is not None:
        if role == "route" and automation_case_engine is not None:
            _set_environment_value(
                container,
                "AUTOMATION_CASE_ENGINE",
                automation_case_engine,
            )
        effective_agent_enabled = (
            hermes_agent_enabled
            if hermes_agent_enabled is not None
            else environment_values.get("AUTOMATION_CASE_ENGINE") == "hermes"
        )
        agent_required: set[str] = set()
        # The knowledge-source intake endpoints queue standalone Summary
        # tasks on the API role, and that queue gate
        # (knowledge_workflow_active) checks the agent gateway settings from
        # the API's own environment — attach them to BOTH roles, not only
        # the worker (r20261005-a4dcc29 lost them on the worker and the
        # backfill receipts silently carried no summary task).
        if effective_agent_enabled and role in {"api", "worker"}:
            agent_required.update({"HERMES_AGENT_BASE_URL", "HERMES_AGENT_API_TOKEN"})
        _remove_secret_references(container, HERMES_AGENT_SECRET_NAMES)
        if agent_required:
            prefix_arn = _parameter_prefix_arn(container, environment=environment)
            for name in sorted(agent_required):
                _set_secret_reference(
                    container,
                    name,
                    _parameter_arn(prefix_arn, HERMES_AGENT_SECRET_SUFFIXES[name]),
                )
    if role == "api":
        # The dashboard hermes-review approve path resolves the current Zendesk
        # comments revision on the API role when no comment-sync row exists yet
        # (read_ticket_ownership_snapshot); ensure the readback credentials are
        # present on every rendered revision instead of relying on the current
        # task definition already carrying them. Skip when the current
        # definition's SSM prefix cannot be derived so its own validation
        # errors surface unchanged.
        try:
            prefix_arn = _parameter_prefix_arn(container, environment=environment)
        except ValueError:
            prefix_arn = None
        if prefix_arn:
            required_api_secrets = {
                **API_ZENDESK_READBACK_SECRET_SUFFIXES,
                **API_RUNTIME_LLM_SECRET_SUFFIXES,
            }
            if environment == "preproduction" and (hermes_case_workflow_mode or environment_values.get("HERMES_CASE_WORKFLOW_MODE", "disabled")) != "disabled":
                required_api_secrets["ENGINEER_SLACK_ACCESS_TOKEN"] = "engineer-slack-access-token"
            for name, suffix in sorted(required_api_secrets.items()):
                _set_secret_reference(container, name, _parameter_arn(prefix_arn, suffix))
        if graph_efs_file_system_id and graph_efs_access_point_id:
            # Email execution chain (13922): render the Graph mail config
            # and the shared token-cache EFS volume onto every API
            # revision the pipeline produces — the observed definition
            # predates the API mount and must not gate the injection.
            for name, value in API_GRAPH_MAIL_ENV.items():
                _set_environment_value(container, name, value)
            if prefix_arn:
                for name, suffix in sorted(API_GRAPH_MAIL_SECRET_SUFFIXES.items()):
                    _set_secret_reference(
                        container, name, _parameter_arn(prefix_arn, suffix)
                    )
            _ensure_graph_efs_volume(
                rendered,
                container,
                file_system_id=graph_efs_file_system_id,
                access_point_id=graph_efs_access_point_id,
            )
    if role == "worker":
        # Ensure the AgentRelay transport identity is present on every rendered
        # worker revision (p2-163), in both enablement modes so late relay
        # results stay receivable after a manual-mode switch. Skip when the
        # SSM prefix cannot be derived so the source validation surfaces.
        try:
            relay_prefix_arn = _parameter_prefix_arn(container, environment=environment)
        except ValueError:
            relay_prefix_arn = None
        if relay_prefix_arn:
            for name, suffix in sorted(AGENTRELAY_SECRET_SUFFIXES.items()):
                _set_secret_reference(
                    container, name, _parameter_arn(relay_prefix_arn, suffix)
                )
        if environment == "preproduction":
            # Stage-6 dual-write (p2-197): the worker renders the WeKnora
            # client configuration (credentials, pinned contract, knowledge
            # base id, X-API-Key auth shape) from the environment SSM
            # prefix. Observed definitions are normalized first so repeat
            # renders never resurrect retired references; the retired
            # p2-181 auto-promotion flag stays explicitly off.
            weknora_prefix_arn = _parameter_prefix_arn(container, environment=environment)
            _apply_preproduction_weknora_client_configuration(
                container,
                environment=environment,
                parameter_prefix_arn=weknora_prefix_arn,
            )
    if role in {"api", "worker"}:
        # Ensure the Zendesk side-effects gate is enabled on every rendered
        # api/worker revision (p2-163, ticket 13567): the hermes engine's
        # business tools must never be silently blocked.
        _set_environment_value(container, "AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED", "1")
    if role in {"api", "worker"} and environment == "preproduction":
        # Stage-6 knowledge governance: the master switch and all six
        # switches come from the validated knowledge stage config (all-off
        # by default) on every rendered Preproduction api/worker revision,
        # regardless of what the observed definition carried. The SSM
        # references are rendered alongside so each stage change is a
        # task-definition revision, never a hidden runtime default.
        prefix_arn = _parameter_prefix_arn(container, environment=environment)
        _apply_preproduction_knowledge_configuration(
            container,
            environment=environment,
            parameter_prefix_arn=prefix_arn,
            knowledge_source_allowlist=knowledge_source_allowlist,
            knowledge_stage_config=stage_config,
        )
    if role in {"api", "worker"}:
        # Ensure the outbound Slack kill switch reflects the target environment
        # on every rendered revision (p2-150): off in Production, on in
        # Preproduction, regardless of what the observed definition carried.
        _set_environment_value(
            container,
            "ENGINEER_SLACK_OUTBOUND_ENABLED",
            "0" if environment == "production" else "1",
        )
    if role in {"api", "worker"} and enablement_workflow_mode is not None:
        # Carry the requested enablement workflow mode (p2-152) onto every
        # rendered api/worker revision; the worker contract above already
        # normalized the mode and credential injection before validation.
        _set_environment_value(container, "ENABLEMENT_WORKFLOW_MODE", enablement_workflow_mode)
    if role in {"api", "route", "worker"}:
        # Deployment-pinned single-model policy: Preproduction renders pin
        # AGENT_MODEL_ID from the SSM agent-model parameter read once per
        # release (the release flow re-verifies the parameter version before
        # activation). The retired p2-160 per-scenario luna/astra overrides
        # are stripped in BOTH environments so an observed definition cannot
        # carry them across a policy boundary, and Production renders also
        # strip the pin itself — the policy must never leak outside
        # Preproduction.
        _remove_environment_values(container, set(PREPRODUCTION_LLM_ENV_OVERRIDES))
        if environment == "preproduction":
            _set_environment_value(container, AGENT_MODEL_ENV_NAME, normalized_agent_model)
        else:
            _remove_environment_values(container, {AGENT_MODEL_ENV_NAME})
    return rendered


def render_production_hermes_disabled_task_definition(
    *,
    role: str,
    current_path: str | Path,
) -> tuple[dict[str, Any], bool]:
    if role not in {"api", "worker"}:
        raise ValueError("Production Hermes disable supports only api or worker")
    source = _read_json(current_path)
    task_definition = source.get("taskDefinition") if "taskDefinition" in source else source
    if not isinstance(task_definition, dict):
        raise ValueError("taskDefinition object is required")
    rendered = _registrable_task_definition(task_definition)
    container = _container(rendered, role)
    environment = _environment_map(container)
    if environment.get("AUTOMATION_ENVIRONMENT") != "production":
        raise ValueError("Hermes disable requires a Production task definition")
    if "production" not in environment.get("AUTOMATION_DB_SCHEMA", "").lower():
        raise ValueError("Production DB schema marker is missing")
    if "production" not in environment.get("AUTOMATION_JOB_NAMESPACE", "").lower():
        raise ValueError("Production job namespace marker is missing")
    image = str(container.get("image") or "")
    if "/supportportal/production@sha256:" not in image:
        raise ValueError("Production task definition must use a digest-pinned Production image")

    _remove_environment_values(container, HERMES_SECRET_NAMES)
    _remove_secret_references(container, HERMES_SECRET_NAMES)
    _remove_secret_references(container, HERMES_AGENT_SECRET_NAMES)
    _remove_secret_references(container, RETIRED_WORKER_SECRET_NAMES)
    _remove_environment_values(container, RETIRED_WORKER_SECRET_NAMES)
    _set_environment_value(container, "HERMES_CASE_WORKFLOW_MODE", "disabled")
    if role == "worker":
        validate_worker_contract(rendered)
    return rendered, rendered != _registrable_task_definition(task_definition)


def render_schema_bootstrap_task_definition(
    *,
    current_path: str | Path,
    manifest_path: str | Path,
    registry_id: str,
    region: str,
    migration_secret_reference: str,
    environment: str = "production",
    repository: str = "supportportal/production",
    agent_model: str | None = None,
) -> dict[str, Any]:
    if ":parameter/" not in migration_secret_reference:
        raise ValueError("schema bootstrap migration secret must be an SSM parameter ARN")
    rendered = render_task_definition(
        role="api",
        current_path=current_path,
        manifest_path=manifest_path,
        registry_id=registry_id,
        region=region,
        environment=environment,
        repository=repository,
        agent_model=agent_model,
    )
    rendered["family"] = f"supportportal-{environment}-schema-bootstrap"
    container = _container(rendered, "api")
    container["command"] = [
        "python",
        "-m",
        "backend.scripts.automation_ecs_bootstrap",
        "bootstrap",
    ]
    container.pop("healthCheck", None)
    container["portMappings"] = []
    for name in ("AUTOMATION_DB_MIGRATION_DSN", "TICKET_DB_MIGRATION_DSN"):
        _set_secret_reference(container, name, migration_secret_reference)
    return rendered


def verify_knowledge_stage_config(
    *,
    task_definition_path: str | Path,
    stage_config_path: str | Path | None,
    role: str,
    environment: str = "preproduction",
) -> dict[str, Any]:
    """Post-render drift check for the stage-6 knowledge configuration.

    The rendered api/worker task definition must carry EXACTLY the
    validated stage config: the master switch, all six governance switches,
    and the source allowlist verbatim. The worker must additionally carry
    the canonical WeKnora client references with their EXACT SSM valueFrom
    paths (the environment prefix's weknora-* parameters — full-path
    comparison, never mere presence), must never carry the retired memory
    identity in secret OR environment form, and must pin the retired
    auto-promotion flag off; the api must carry none of the WeKnora client
    config. A mismatch is a hard error — registration of a definition that
    silently drifted from the recorded stage config must never proceed.
    """
    if role not in {"api", "worker"}:
        raise ValueError("knowledge stage verification supports only api or worker")
    if environment != "preproduction":
        raise ValueError("knowledge stage verification is Preproduction-only")
    stage = load_knowledge_stage_config(
        str(stage_config_path) if stage_config_path is not None else None
    )
    source = _read_json(task_definition_path)
    task_definition = source.get("taskDefinition") if "taskDefinition" in source else source
    if not isinstance(task_definition, dict):
        raise ValueError("taskDefinition object is required")
    container = _container(task_definition, role)
    environment_values = _environment_map(container)
    problems: list[str] = []
    expected_env = {
        KNOWLEDGE_MASTER_SWITCH_ENV: stage["master"],
        **stage["switches"],
        "KNOWLEDGE_SOURCE_ALLOWLIST": stage["source_allowlist"],
    }
    for name, expected in expected_env.items():
        actual = environment_values.get(name)
        if actual is None:
            problems.append(f"missing environment {name} (expected {expected!r})")
        elif actual != expected:
            problems.append(f"environment {name} is {actual!r}, expected {expected!r}")
    secret_names = _secret_names(container)
    secret_references = {
        str(item.get("name") or ""): str(item.get("valueFrom") or "")
        for item in container.get("secrets") or []
    }
    if role == "worker":
        # B1: every WeKnora client secret must point at the environment SSM
        # prefix's weknora-* parameter — exact full-path comparison, so a
        # wrong prefix (production/foreign), a wrong suffix, or a relative
        # reference can never pass as "present".
        prefix_arn = _parameter_prefix_arn(container, environment=environment)
        for name, suffix in sorted(WEKNORA_CLIENT_SECRET_SUFFIXES.items()):
            expected_reference = _parameter_arn(prefix_arn, suffix)
            actual_reference = secret_references.get(name)
            if actual_reference is None:
                problems.append(f"worker is missing the WeKnora client secret {name}")
            elif actual_reference != expected_reference:
                problems.append(
                    f"worker WeKnora client secret {name} must reference "
                    f"{expected_reference}, got {actual_reference}"
                )
        # Only the seven canonical client references may point at weknora-*
        # parameters; anything else reaching for one is drift.
        for name, reference in sorted(secret_references.items()):
            if "/weknora-" in reference and name not in WEKNORA_CLIENT_SECRET_SUFFIXES:
                problems.append(
                    f"worker secret {name} unexpectedly references a WeKnora "
                    f"parameter: {reference}"
                )
        if "WEKNORA_MEMORY_IDENTITY" in secret_names:
            problems.append("worker carries the retired WEKNORA_MEMORY_IDENTITY secret")
        if "WEKNORA_MEMORY_IDENTITY" in environment_values:
            problems.append(
                "worker carries the retired WEKNORA_MEMORY_IDENTITY environment "
                "variable (the pinned contract has no memory operations; the "
                "identity must stay unset and fail closed)"
            )
        if environment_values.get("WEKNORA_PROMOTION_ENABLED") != "0":
            problems.append("worker must pin WEKNORA_PROMOTION_ENABLED=0")
        for name, expected in WEKNORA_CLIENT_ENV.items():
            actual = environment_values.get(name)
            if actual != expected:
                problems.append(
                    f"worker environment {name} is {actual!r}, expected {expected!r}"
                )
    else:
        leaked = (set(WEKNORA_SECRET_SUFFIXES) | set(WEKNORA_CLIENT_ENV)) & (
            secret_names | set(environment_values)
        )
        if leaked:
            problems.append(f"api carries WeKnora client config that belongs on the worker: {sorted(leaked)}")
    if problems:
        raise ValueError(
            "rendered task definition does not match the knowledge stage "
            f"config: {'; '.join(problems)}"
        )
    return {
        "status": "ok",
        "role": role,
        "master": stage["master"],
        "switches": dict(stage["switches"]),
        "source_allowlist": stage["source_allowlist"],
        "weknora_client": role == "worker",
    }


def verify_heartbeats(
    *,
    manifest_path: str | Path,
    task_definition_path: str | Path,
    max_age_seconds: float,
    environment: str = "production",
) -> dict[str, Any]:
    dsn = str(os.getenv("AUTOMATION_HEARTBEAT_DSN") or "").strip()
    if not dsn:
        raise ValueError("AUTOMATION_HEARTBEAT_DSN is required")
    source = _read_json(task_definition_path)
    task_definition = source.get("taskDefinition") if "taskDefinition" in source else source
    worker = _container(task_definition, "worker")
    environment_values = _environment_map(worker)
    schema = environment_values.get("AUTOMATION_DB_SCHEMA", "")
    namespace = environment_values.get("AUTOMATION_JOB_NAMESPACE", "")
    if not schema or not namespace:
        raise ValueError("Worker task definition is missing DB schema or job namespace")
    manifest = read_manifest(Path(manifest_path))
    expected_common = {
        "environment": environment,
        "release_id": manifest.release_id,
        "git_commit": manifest.git_commit,
        "build_time": manifest.build_time.isoformat().replace("+00:00", "Z"),
        "prompt_release_id": manifest.prompt_release_id,
        "db_schema": schema,
        "job_namespace": namespace,
    }
    newest: dict[str, dict[str, Any]] = {}
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT role,provenance,last_seen_at,clock_timestamp() FROM {} "
                "WHERE namespace=%s AND role IN ('route','worker')"
            ).format(sql.Identifier(schema, "automation_worker_heartbeats")),
            (namespace,),
        )
        for role, provenance, last_seen_at, observed_at in cursor.fetchall():
            normalized_role = str(role)
            current = newest.get(normalized_role)
            if current is None or last_seen_at > current["last_seen_at"]:
                newest[normalized_role] = {
                    "provenance": provenance,
                    "last_seen_at": last_seen_at,
                    "observed_at": observed_at,
                }
    for role in ("route", "worker"):
        row = newest.get(role)
        if row is None:
            raise ValueError(f"latest {role} heartbeat is missing")
        age = (row["observed_at"] - row["last_seen_at"]).total_seconds()
        if age < 0 or age > max_age_seconds:
            raise ValueError(f"latest {role} heartbeat is stale")
        expected = {
            **expected_common,
            "service_role": role,
            "image_digest": manifest.components[role].digest,
        }
        provenance = row["provenance"] if isinstance(row["provenance"], dict) else {}
        mismatches = sorted(
            key for key, value in expected.items() if provenance.get(key) != value
        )
        if mismatches:
            raise ValueError(f"latest {role} heartbeat provenance mismatch: {mismatches}")
    return {"status": "ok", "roles": ["route", "worker"]}


def validate_suspension_recipients() -> dict[str, Any]:
    recipients = resolve_account_internal_email_recipients(
        "account_suspension",
        require_json=True,
    )
    return {
        "status": "ok",
        "to_count": len(recipients.to),
        "cc_count": len(recipients.cc),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    promotion = subparsers.add_parser("validate-promotion")
    promotion.add_argument("--manifest", required=True)
    promotion.add_argument("--promotion-record", required=True)
    publish = subparsers.add_parser("validate-preproduction-publish")
    publish.add_argument("--manifest", required=True)
    publish.add_argument("--publish-record", required=True)
    acceptance = subparsers.add_parser("validate-preproduction-acceptance")
    acceptance.add_argument("--manifest", required=True)
    acceptance.add_argument("--publish-record", required=True)
    acceptance.add_argument("--deploy-evidence", required=True)
    render = subparsers.add_parser("render-task-definition")
    render.add_argument("--role", choices=("api", "route", "worker"), required=True)
    render.add_argument("--current", required=True)
    render.add_argument("--manifest", required=True)
    render.add_argument("--registry-id", required=True)
    render.add_argument("--region", required=True)
    render.add_argument("--environment", choices=("preproduction", "production"), default="production")
    render.add_argument("--repository", default="supportportal/production")
    render.add_argument(
        "--hermes-case-workflow-mode",
        choices=sorted(HERMES_CASE_WORKFLOW_MODES),
    )
    file_switch = render.add_mutually_exclusive_group()
    file_switch.add_argument("--investigation-case-files-enabled", dest="investigation_case_files_enabled", action="store_const", const=True, default=None)
    file_switch.add_argument("--investigation-case-files-disabled", dest="investigation_case_files_enabled", action="store_const", const=False)
    render.add_argument(
        "--hermes-persona-enabled",
        action="store_const",
        const=True,
        default=None,
    )
    render.add_argument(
        "--automation-case-engine",
        choices=sorted(AUTOMATION_CASE_ENGINES),
    )
    render.add_argument(
        "--hermes-agent-enabled",
        action="store_const",
        const=True,
        default=None,
    )
    render.add_argument(
        "--enablement-workflow-mode",
        choices=sorted(ENABLEMENT_WORKFLOW_MODES),
    )
    render.add_argument("--agent-model", default=None)
    render.add_argument("--graph-efs-file-system-id", default=None)
    render.add_argument("--graph-efs-access-point-id", default=None)
    render.add_argument("--knowledge-source-allowlist", default=None)
    render.add_argument("--knowledge-stage-config", default=None)
    render.add_argument("--output", required=True)
    disable_hermes = subparsers.add_parser(
        "render-production-hermes-disabled-task-definition"
    )
    disable_hermes.add_argument("--role", choices=("api", "worker"), required=True)
    disable_hermes.add_argument("--current", required=True)
    disable_hermes.add_argument("--output", required=True)
    initial = subparsers.add_parser("render-initial-task-definition")
    initial.add_argument("--role", choices=("api", "route", "worker"), required=True)
    initial.add_argument("--manifest", required=True)
    initial.add_argument("--registry-id", required=True)
    initial.add_argument("--region", required=True)
    initial.add_argument("--environment", choices=("preproduction",), required=True)
    initial.add_argument("--repository", required=True)
    initial.add_argument("--execution-role-arn", required=True)
    initial.add_argument("--task-role-arn", required=True)
    initial.add_argument("--log-group-name", required=True)
    initial.add_argument("--parameter-prefix-arn", required=True)
    initial.add_argument(
        "--hermes-case-workflow-mode",
        choices=sorted(HERMES_CASE_WORKFLOW_MODES),
        default="disabled",
    )
    initial.add_argument("--hermes-persona-enabled", action="store_true")
    initial.add_argument(
        "--automation-case-engine",
        choices=sorted(AUTOMATION_CASE_ENGINES),
        default="legacy",
    )
    initial.add_argument("--hermes-agent-enabled", action="store_true")
    initial.add_argument(
        "--enablement-workflow-mode",
        choices=sorted(ENABLEMENT_WORKFLOW_MODES),
        default="manual",
    )
    initial.add_argument("--graph-efs-file-system-id")
    initial.add_argument("--graph-efs-access-point-id")
    initial.add_argument("--agent-model", default=None)
    initial.add_argument("--knowledge-source-allowlist", default=None)
    initial.add_argument("--knowledge-stage-config", default=None)
    initial.add_argument("--output", required=True)
    verify_knowledge = subparsers.add_parser("verify-knowledge-stage-config")
    verify_knowledge.add_argument("--task-definition", required=True)
    verify_knowledge.add_argument("--stage-config", default=None)
    verify_knowledge.add_argument("--role", choices=("api", "worker"), required=True)
    verify_knowledge.add_argument(
        "--environment", choices=("preproduction",), default="preproduction"
    )
    bootstrap = subparsers.add_parser("render-schema-bootstrap-task-definition")
    bootstrap.add_argument("--current", required=True)
    bootstrap.add_argument("--manifest", required=True)
    bootstrap.add_argument("--registry-id", required=True)
    bootstrap.add_argument("--region", required=True)
    bootstrap.add_argument("--migration-secret-reference", required=True)
    bootstrap.add_argument("--environment", choices=("preproduction", "production"), default="production")
    bootstrap.add_argument("--repository", default="supportportal/production")
    bootstrap.add_argument("--agent-model", default=None)
    bootstrap.add_argument("--output", required=True)
    heartbeat = subparsers.add_parser("verify-heartbeats")
    heartbeat.add_argument("--manifest", required=True)
    heartbeat.add_argument("--task-definition", required=True)
    heartbeat.add_argument("--max-age-seconds", type=float, default=90.0)
    heartbeat.add_argument("--environment", choices=("preproduction", "production"), default="production")
    subparsers.add_parser("validate-suspension-recipients")
    return parser


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if args.command == "validate-promotion":
        return validate_promotion(args.manifest, args.promotion_record)
    if args.command == "validate-preproduction-publish":
        return validate_preproduction_publish(args.manifest, args.publish_record)
    if args.command == "validate-preproduction-acceptance":
        return validate_preproduction_acceptance(
            args.manifest,
            args.publish_record,
            args.deploy_evidence,
        )
    if args.command == "render-task-definition":
        result = render_task_definition(
            role=args.role,
            current_path=args.current,
            manifest_path=args.manifest,
            registry_id=args.registry_id,
            region=args.region,
            environment=args.environment,
            repository=args.repository,
            hermes_case_workflow_mode=args.hermes_case_workflow_mode,
            hermes_persona_enabled=args.hermes_persona_enabled,
            investigation_case_files_enabled=args.investigation_case_files_enabled,
            automation_case_engine=args.automation_case_engine,
            hermes_agent_enabled=args.hermes_agent_enabled,
            enablement_workflow_mode=args.enablement_workflow_mode,
            agent_model=args.agent_model,
            graph_efs_file_system_id=args.graph_efs_file_system_id,
            graph_efs_access_point_id=args.graph_efs_access_point_id,
            knowledge_source_allowlist=args.knowledge_source_allowlist,
            knowledge_stage_config=args.knowledge_stage_config,
        )
        Path(args.output).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "output": args.output}
    if args.command == "render-production-hermes-disabled-task-definition":
        result, changed = render_production_hermes_disabled_task_definition(
            role=args.role,
            current_path=args.current,
        )
        Path(args.output).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "output": args.output, "changed": changed}
    if args.command == "render-initial-task-definition":
        result = render_initial_task_definition(
            role=args.role,
            manifest_path=args.manifest,
            registry_id=args.registry_id,
            region=args.region,
            environment=args.environment,
            repository=args.repository,
            execution_role_arn=args.execution_role_arn,
            task_role_arn=args.task_role_arn,
            log_group_name=args.log_group_name,
            parameter_prefix_arn=args.parameter_prefix_arn,
            hermes_case_workflow_mode=args.hermes_case_workflow_mode,
            hermes_persona_enabled=args.hermes_persona_enabled,
            automation_case_engine=args.automation_case_engine,
            hermes_agent_enabled=args.hermes_agent_enabled,
            enablement_workflow_mode=args.enablement_workflow_mode,
            graph_efs_file_system_id=args.graph_efs_file_system_id,
            graph_efs_access_point_id=args.graph_efs_access_point_id,
            agent_model=args.agent_model,
            knowledge_source_allowlist=args.knowledge_source_allowlist,
            knowledge_stage_config=args.knowledge_stage_config,
        )
        Path(args.output).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "output": args.output}
    if args.command == "verify-knowledge-stage-config":
        return verify_knowledge_stage_config(
            task_definition_path=args.task_definition,
            stage_config_path=args.stage_config,
            role=args.role,
            environment=args.environment,
        )
    if args.command == "render-schema-bootstrap-task-definition":
        result = render_schema_bootstrap_task_definition(
            current_path=args.current,
            manifest_path=args.manifest,
            registry_id=args.registry_id,
            region=args.region,
            migration_secret_reference=args.migration_secret_reference,
            environment=args.environment,
            repository=args.repository,
            agent_model=args.agent_model,
        )
        Path(args.output).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"status": "ok", "output": args.output}
    if args.command == "verify-heartbeats":
        return verify_heartbeats(
            manifest_path=args.manifest,
            task_definition_path=args.task_definition,
            max_age_seconds=args.max_age_seconds,
            environment=args.environment,
        )
    if args.command == "validate-suspension-recipients":
        return validate_suspension_recipients()
    raise ValueError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    try:
        print(json.dumps(run(argv), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
