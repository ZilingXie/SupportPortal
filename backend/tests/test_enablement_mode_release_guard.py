"""Explicit mode changes keep Knowledge and all unrelated runtime config guarded."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.scripts.automation_ecs_deploy import render_task_definition
from backend.tests.test_automation_ecs_deploy import _as_preproduction, _manifest, _task_definition

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deployment/deploy_automation_ecs_release.sh"
MODE = "ENABLEMENT_WORKFLOW_MODE"


def _render_pair(tmp_path, role, before="manual", after="archer"):
    stage = tmp_path / "stage.json"
    stage.write_text(json.dumps({"master": "1", "switches": {
        "KNOWLEDGE_SOURCE_INTAKE_ENABLED": "1",
        "KNOWLEDGE_SUMMARY_REVIEW_ENABLED": "1",
    }, "source_allowlist": "csd_issue:CSD-TEST-1"}))
    seed = _task_definition(tmp_path, role)
    _as_preproduction(seed)
    kwargs = dict(role=role, manifest_path=_manifest(tmp_path), registry_id="123456789012",
                  region="us-east-1", environment="preproduction", repository="supportportal/preproduction",
                  automation_case_engine="hermes", hermes_agent_enabled=True, hermes_persona_enabled=True,
                  hermes_case_workflow_mode="disabled", agent_model="gpt-6.1-sol",
                  investigation_case_files_enabled=False, knowledge_stage_config=str(stage))
    current = tmp_path / f"{role}.current.json"
    current.write_text(json.dumps(render_task_definition(
        current_path=seed, enablement_workflow_mode=before, **kwargs)))
    rendered = tmp_path / f"{role}.register.json"
    rendered.write_text(json.dumps(render_task_definition(
        current_path=current, enablement_workflow_mode=after, **kwargs)))
    return current, rendered, stage


def _cli(current, rendered, role, expected):
    args = [sys.executable, "-m", "backend.scripts.automation_ecs_deploy",
            "verify-knowledge-shared-configuration", "--current", str(current),
            "--rendered", str(rendered), "--role", role]
    if expected is not None:
        args += ["--expected-enablement-workflow-mode", expected]
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True)


@pytest.mark.parametrize("role", ["api", "worker"])
@pytest.mark.parametrize("before,after", [("manual", "archer"), ("archer", "manual")])
def test_real_renderer_and_cli_allow_only_explicit_mode(tmp_path, role, before, after):
    current, rendered, _ = _render_pair(tmp_path, role, before, after)
    rejected = _cli(current, rendered, role, None)
    assert rejected.returncode == 1
    assert MODE in json.loads(rejected.stdout)["error"]
    allowed = _cli(current, rendered, role, after)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert json.loads(allowed.stdout)["allowed_changes"] == [MODE]
    for path in (current, rendered):
        env = {e["name"]: e["value"] for e in json.loads(path.read_text())["containerDefinitions"][0]["environment"]}
        assert env["HERMES_KNOWLEDGE_WORKFLOW_ENABLED"] == "1"
        assert env["KNOWLEDGE_SUMMARY_REVIEW_ENABLED"] == "1"
        assert env["KNOWLEDGE_AI_EXECUTION_ENABLED"] == "0"
        assert env["INVESTIGATION_CASE_FILES_ENABLED"] == "0"


@pytest.mark.parametrize("role", ["api", "worker"])
@pytest.mark.parametrize("bad", [None, "manual", "invalid"])
def test_expected_mode_must_match_rendered_definition(tmp_path, role, bad):
    current, rendered, _ = _render_pair(tmp_path, role)
    definition = json.loads(rendered.read_text())
    container = definition["containerDefinitions"][0]
    container["environment"] = [e for e in container["environment"] if e["name"] != MODE]
    if bad is not None:
        container["environment"].append({"name": MODE, "value": bad})
    rendered.write_text(json.dumps(definition))
    result = _cli(current, rendered, role, "archer")
    assert result.returncode == 1
    assert "does not match the requested mode" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("role", ["api", "worker"])
@pytest.mark.parametrize("field,name", [
    ("environment", "AGENT_MODEL_ID"), ("secrets", "HERMES_AGENT_API_TOKEN"),
    ("environment", "CUSTOM_RUNTIME_FLAG"), ("environment", "INVESTIGATION_CASE_FILES_ENABLED"),
])
def test_explicit_mode_does_not_allow_other_drift(tmp_path, role, field, name):
    current, rendered, _ = _render_pair(tmp_path, role)
    definition = json.loads(rendered.read_text())
    container = definition["containerDefinitions"][0]
    container[field] = [e for e in container[field] if e["name"] != name]
    container[field].append({"name": name, "valueFrom" if field == "secrets" else "value": "unapproved-sensitive-value"})
    rendered.write_text(json.dumps(definition))
    result = _cli(current, rendered, role, "archer")
    assert result.returncode == 1
    assert name in json.loads(result.stdout)["error"]
    assert "unapproved-sensitive-value" not in result.stdout + result.stderr


def test_route_mode_drift_is_still_rejected(tmp_path):
    current, rendered, _ = _render_pair(tmp_path, "route")
    definition = json.loads(rendered.read_text())
    definition["containerDefinitions"][0]["environment"].append({"name": MODE, "value": "archer"})
    rendered.write_text(json.dumps(definition))
    result = _cli(current, rendered, "route", "archer")
    assert result.returncode == 1
    assert MODE in json.loads(result.stdout)["error"]


def _shell_functions(*names):
    source = SCRIPT.read_text()
    preamble = source[:source.index("log()")]
    return preamble + "\n" + "\n".join(
        (re.search(rf"^{name}\(\) \{{[^\n]*\}}$", source, re.M)
         or re.search(rf"^{name}\(\) \{{\n.*?^\}}", source, re.M | re.S))[0] for name in names
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_actual_shell_guard_passes_explicit_target_to_cli(tmp_path, explicit):
    # Default manual must not silently downgrade an already-Archer deployment.
    before, after = ("manual", "archer") if explicit else ("archer", "manual")
    for role in ("api", "route", "worker"):
        _, _, stage = _render_pair(tmp_path, role, before, after)
    shell = _shell_functions("fail", "log", "configure_environment_defaults", "parse_args", "verify_knowledge_stage_config_rendered")
    shell += '\nPROJECT_ROOT="$1"; PYTHON_BIN="$2"; TEMP_DIR="$3"; shift 3\nparse_args "$@"\nverify_knowledge_stage_config_rendered\n'
    shell_path = tmp_path / "guard.sh"
    shell_path.write_text(shell)
    args = ["bash", str(shell_path), str(ROOT), sys.executable, str(tmp_path),
            "--environment", "preproduction", "--automation-case-engine", "hermes", "--hermes-agent-enabled",
            "--knowledge-stage-config", str(stage)]
    if explicit:
        args += ["--enablement-workflow-mode", "archer"]
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == (0 if explicit else 1), result.stdout + result.stderr
    if not explicit:
        assert "shared api configuration" in result.stderr


@pytest.mark.parametrize("mode", ["manual", "archer"])
def test_actual_preflight_and_checkpoint_bind_mode(tmp_path, mode):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"components": {r: {"digest": "sha256:test"} for r in ("api", "route", "worker")}}))
    for role in ("api", "route", "worker"):
        _, rendered, _ = _render_pair(tmp_path, role)
        (tmp_path / f"{role}.observed.json").write_text(rendered.read_text())
        (tmp_path / f"{role}.ecr-digest").write_text("sha256:test")
        (tmp_path / f"{role}.observed-arn").write_text(f"arn:test:{role}")
    for name in ("source-prompt", "terraform-identity", "prompt-target-identity"):
        (tmp_path / f"{name}.json").write_text("{}")
    (tmp_path / "secret-metadata.json").write_text("[]")
    shell = _shell_functions("fail", "file_sha256", "checkpoint_identity", "write_preflight_context")
    shell += '\nPROJECT_ROOT="$1"; PYTHON_BIN="$2"; TEMP_DIR="$3"; ENABLEMENT_WORKFLOW_MODE="$4"\n'
    shell += 'MANIFEST_PATH="${TEMP_DIR}/manifest.json"; PUBLISH_RECORD="$MANIFEST_PATH"\ncheckpoint_identity\nwrite_preflight_context\n'
    shell_path = tmp_path / "identity.sh"
    shell_path.write_text(shell)
    result = subprocess.run(["bash", str(shell_path), str(ROOT), sys.executable, str(tmp_path), mode],
                            cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["enablement_workflow_mode"] == mode
    assert json.loads((tmp_path / "preflight-context.json").read_text())["mode"]["enablement_workflow_mode"] == mode
