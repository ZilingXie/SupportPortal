import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.scripts.automation_ecs_deploy import load_knowledge_stage_config


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("role", ["api", "route", "worker"])
def test_real_renderer_preserves_native_config_across_knowledge_stages(tmp_path, role):
    from backend.tests.test_automation_ecs_deploy import _task_definition, _as_preproduction, _manifest
    from backend.scripts.automation_ecs_deploy import render_task_definition, verify_knowledge_shared_configuration

    current = _task_definition(tmp_path, role)
    _as_preproduction(current)
    kwargs = dict(role=role, manifest_path=_manifest(tmp_path), registry_id="123456789012",
                  region="us-east-1", environment="preproduction", repository="supportportal/preproduction",
                  automation_case_engine="hermes", hermes_agent_enabled=True, hermes_persona_enabled=True,
                  hermes_case_workflow_mode="disabled", enablement_workflow_mode="manual", agent_model="gpt-6.1-sol")
    baseline = render_task_definition(current_path=current, **kwargs)
    current.write_text(json.dumps(baseline))
    stage = tmp_path / "stage.json"
    stage.write_text(json.dumps({"master": "1", "switches": {
        "KNOWLEDGE_SOURCE_INTAKE_ENABLED": "1", "KNOWLEDGE_SUMMARY_REVIEW_ENABLED": "1",
    }, "source_allowlist": "csd_issue:CSD-TEST-1"}))
    rendered = tmp_path / "rendered.json"
    after = render_task_definition(current_path=current, knowledge_stage_config=str(stage), **kwargs)
    rendered.write_text(json.dumps(after))
    assert verify_knowledge_shared_configuration(str(current), str(rendered), role)["status"] == "ok"
    values = {v["name"]: v["value"] for v in after["containerDefinitions"][0]["environment"]}
    if role == "route":
        assert values["AUTOMATION_CASE_ENGINE"] == "hermes"
    else:
        assert values["HERMES_CASE_WORKFLOW_MODE"] == "disabled"
        assert values["KNOWLEDGE_AI_EXECUTION_ENABLED"] == "0"


@pytest.mark.parametrize("role", ["api", "route", "worker"])
@pytest.mark.parametrize("change", [None, "AUTOMATION_CASE_ENGINE", "HERMES_CASE_WORKFLOW_MODE", "ENABLEMENT_WORKFLOW_MODE", "HERMES_AGENT_API_TOKEN", "CUSTOM_RUNTIME_FLAG"])
def test_shared_configuration_real_cli_rejects_nonknowledge_drift(tmp_path, role, change):
    container = {
        "name": role,
        "environment": [
            {"name": "AUTOMATION_CASE_ENGINE", "value": "hermes"},
            {"name": "HERMES_CASE_WORKFLOW_MODE", "value": "disabled"},
            {"name": "ENABLEMENT_WORKFLOW_MODE", "value": "manual"},
            {"name": "CUSTOM_RUNTIME_FLAG", "value": "preserve"},
            {"name": "APP_BUILD_REF", "value": "old"},
        ],
        "secrets": [{"name": "HERMES_AGENT_API_TOKEN", "valueFrom": "sensitive-reference"}],
    }
    current = tmp_path / "current.json"
    current.write_text(json.dumps({"taskDefinition": {"containerDefinitions": [container]}}))
    container["environment"][-1]["value"] = "new"
    container["environment"].append({"name": "KNOWLEDGE_AI_EXECUTION_ENABLED", "value": "0"})
    if change:
        for item in container["environment"] + container["secrets"]:
            if item["name"] == change:
                item["valueFrom" if "valueFrom" in item else "value"] = "unapproved-secret-value"
    rendered = tmp_path / "rendered.json"
    rendered.write_text(json.dumps({"containerDefinitions": [container]}))
    result = subprocess.run([
        sys.executable, "-m", "backend.scripts.automation_ecs_deploy",
        "verify-knowledge-shared-configuration", "--current", str(current),
        "--rendered", str(rendered), "--role", role,
    ], cwd=ROOT, text=True, capture_output=True)
    assert (result.returncode == 0) == (change is None)
    if change:
        diagnostics = result.stdout + result.stderr
        assert change in diagnostics
        assert "unapproved-secret-value" not in diagnostics
        assert "sensitive-reference" not in diagnostics


def test_ai_switch_normalizes_off_and_requires_task_production(tmp_path):
    stage = tmp_path / "stage.json"
    stage.write_text(json.dumps({"master": "1", "switches": {"KNOWLEDGE_SUMMARY_REVIEW_ENABLED": "1"}}))
    assert load_knowledge_stage_config(str(stage))["switches"]["KNOWLEDGE_AI_EXECUTION_ENABLED"] == "0"
    stage.write_text(json.dumps({"master": "1", "switches": {"KNOWLEDGE_AI_EXECUTION_ENABLED": "1"}}))
    with pytest.raises(ValueError, match="KNOWLEDGE_SUMMARY_REVIEW_ENABLED"):
        load_knowledge_stage_config(str(stage))


def test_shared_guard_wired_before_rollout_and_activation():
    source = (ROOT / "deployment/deploy_automation_ecs_release.sh").read_text()
    assert 'verify-knowledge-shared-configuration' in source
    assert source.count('  verify_knowledge_stage_config_rendered\n') == 2

def test_shared_guard_allows_renderer_owned_team_agent_configuration(tmp_path):
    from backend.scripts.automation_ecs_deploy import verify_knowledge_shared_configuration
    container = {"name": "api", "environment": [
        {"name": "TEAM_AGENT_MODEL", "value": "team-agent"},
    ], "secrets": [{"name": "TEAM_AGENT_BASE_URL", "valueFrom": "new-url"},
                   {"name": "TEAM_AGENT_REST_TOKEN", "valueFrom": "new-token"}]}
    current = tmp_path / "current.json"; rendered = tmp_path / "rendered.json"
    current.write_text(json.dumps({"containerDefinitions": [{"name": "api", "environment": [], "secrets": []}]}))
    rendered.write_text(json.dumps({"containerDefinitions": [container]}))
    assert verify_knowledge_shared_configuration(str(current), str(rendered), "api")["status"] == "ok"


@pytest.mark.parametrize("execution", [None, "0", "1"])
def test_formal_worker_entries_use_independent_ai_gate(monkeypatch, execution):
    from unittest.mock import MagicMock
    monkeypatch.setenv("TICKET_DB_DSN", "postgresql://example.invalid/test")
    from backend import worker
    from backend.services import knowledge_standalone_workflow as standalone

    monkeypatch.setenv("HERMES_KNOWLEDGE_WORKFLOW_ENABLED", "1")
    monkeypatch.setenv("KNOWLEDGE_SUMMARY_REVIEW_ENABLED", "1")
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "disabled")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    monkeypatch.setenv("KNOWLEDGE_DUALWRITE_WORKER_ENABLED", "1")
    if execution is None:
        monkeypatch.delenv("KNOWLEDGE_AI_EXECUTION_ENABLED", raising=False)
    else:
        monkeypatch.setenv("KNOWLEDGE_AI_EXECUTION_ENABLED", execution)
    case_drain = MagicMock(return_value=2)
    source_drain = MagicMock(return_value={"executed": 2})
    monkeypatch.setattr(worker, "drain_hermes_knowledge_tasks", case_drain)
    monkeypatch.setattr(standalone, "drain_standalone_knowledge_tasks", source_drain)
    assert worker._drain_hermes_knowledge_tasks() == (2 if execution == "1" else 0)
    assert worker._drain_standalone_knowledge_tasks() == (2 if execution == "1" else 0)
    assert case_drain.call_count == source_drain.call_count == (1 if execution == "1" else 0)
