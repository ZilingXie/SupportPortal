"""Real deploy CLI: explicit zero preserves an absent, fail-closed switch."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLAG = "INVESTIGATION_CASE_FILES_ENABLED"


def _definition(role, value):
    return {"containerDefinitions": [{"name": role, "environment": (
        [] if value is None else [{"name": FLAG, "value": value}]
    ), "secrets": []}]}


def _cli(tmp_path, role, before, after):
    current, rendered = tmp_path / "current.json", tmp_path / "rendered.json"
    current.write_text(json.dumps(before))
    rendered.write_text(json.dumps(after))
    return subprocess.run([
        sys.executable, "-m", "backend.scripts.automation_ecs_deploy",
        "verify-knowledge-shared-configuration", "--current", str(current),
        "--rendered", str(rendered), "--role", role,
    ], cwd=ROOT, capture_output=True, text=True)


@pytest.mark.parametrize("role", ["api", "route", "worker"])
@pytest.mark.parametrize("before,after,allowed", [
    (None, None, True), (None, "0", True), ("0", None, True),
    ("0", "0", True), (None, "1", False), ("0", "1", False),
    ("1", "0", False), ("1", None, False), ("1", "1", True),
])
def test_real_cli_file_switch_transitions(tmp_path, role, before, after, allowed):
    result = _cli(tmp_path, role, _definition(role, before), _definition(role, after))
    assert result.returncode == (0 if allowed else 1), result.stdout + result.stderr
    payload = json.loads(result.stdout)
    if allowed:
        assert payload["status"] == "ok"
    else:
        assert FLAG in payload["error"]


@pytest.mark.parametrize("role", ["api", "route", "worker"])
@pytest.mark.parametrize("field,name", [
    ("environment", "AGENT_MODEL_ID"),
    ("secrets", "HERMES_AGENT_API_TOKEN"),
    ("environment", "CUSTOM_RUNTIME_FLAG"),
])
def test_real_cli_normalization_keeps_other_configuration_guarded(tmp_path, role, field, name):
    before, after = _definition(role, None), _definition(role, "0")
    value_key = "valueFrom" if field == "secrets" else "value"
    before["containerDefinitions"][0][field].append({"name": name, value_key: "original"})
    after["containerDefinitions"][0][field].append({"name": name, value_key: "unapproved-sensitive-value"})
    result = _cli(tmp_path, role, before, after)
    assert result.returncode == 1
    assert name in json.loads(result.stdout)["error"]
    assert "unapproved-sensitive-value" not in result.stdout + result.stderr


@pytest.mark.parametrize("role", ["api", "worker"])
def test_real_renderer_explicit_disabled_passes_shared_guard(tmp_path, role):
    from backend.scripts.automation_ecs_deploy import render_task_definition
    from backend.tests.test_automation_ecs_deploy import _task_definition, _as_preproduction, _manifest

    current = _task_definition(tmp_path, role)
    _as_preproduction(current)
    kwargs = dict(role=role, manifest_path=_manifest(tmp_path), registry_id="123456789012",
                  region="us-east-1", environment="preproduction", repository="supportportal/preproduction",
                  automation_case_engine="hermes", hermes_agent_enabled=True, hermes_persona_enabled=True,
                  hermes_case_workflow_mode="disabled", enablement_workflow_mode="manual", agent_model="gpt-6.1-sol")
    before = render_task_definition(current_path=current, **kwargs)
    # The deployed pre-R2 definition predates the file switch entirely.
    container = before["containerDefinitions"][0]
    container["environment"] = [e for e in container["environment"] if e["name"] != FLAG]
    assert not any(e["name"] == FLAG for e in before["containerDefinitions"][0]["environment"])
    current.write_text(json.dumps(before))
    after = render_task_definition(current_path=current, investigation_case_files_enabled=False, **kwargs)
    values = {e["name"]: e["value"] for e in after["containerDefinitions"][0]["environment"]}
    assert values[FLAG] == "0"
    result = _cli(tmp_path, role, before, after)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "ok"
