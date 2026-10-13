"""Release entry rejects implicit engines and unapproved native downgrades."""
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from backend.scripts import automation_ecs_release_pipeline as pipeline


def definition(engine):
    return {"taskDefinition": {"containerDefinitions": [{"name": "route", "environment": [
        {"name": "AUTOMATION_CASE_ENGINE", "value": engine}]}]}}


@pytest.mark.parametrize("arguments,message", [
    ([], "explicit --automation-case-engine"),
    (["--automation-case-engine", "hermes"], "requires --hermes-agent-enabled"),
])
def test_pipeline_rejects_missing_config_before_external_work(tmp_path, monkeypatch, arguments, message):
    external = Mock(side_effect=AssertionError("no external release work allowed"))
    monkeypatch.setattr(pipeline, "verify_aws_identity", external)
    monkeypatch.setattr(pipeline, "_run_stage", external)
    args = ["run", "--project-root", str(tmp_path), "--prompt-release-id", "pr-test", *arguments]
    with pytest.raises(ValueError, match=message):
        pipeline.run(args)
    external.assert_not_called()
    assert not (tmp_path / ".deployments").exists()


def test_pipeline_rejects_live_downgrade_before_build(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOMATION_CASE_ENGINE_DOWNGRADE_APPROVED", raising=False)
    monkeypatch.setattr(pipeline, "verify_aws_identity", lambda _: None)
    aws = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, json.dumps({"services": [{"taskDefinition": "route:140"}]})),
        subprocess.CompletedProcess([], 0, json.dumps(definition("hermes"))),
    ])
    monkeypatch.setattr(pipeline.subprocess, "run", aws)
    stages = Mock(side_effect=AssertionError("no build or deployment allowed"))
    monkeypatch.setattr(pipeline, "_run_stage", stages)
    with pytest.raises(ValueError, match="downgrade requires"):
        pipeline.run(["run", "--project-root", str(tmp_path), "--prompt-release-id", "pr-test",
                      "--automation-case-engine", "legacy"])
    assert aws.call_count == 2
    stages.assert_not_called()
    assert not (tmp_path / ".deployments").exists()


@pytest.mark.parametrize("current,target,approved", [
    ("legacy", "hermes", False), ("hermes", "hermes", False), ("hermes", "legacy", True),
])
def test_transition_cli_accepts_authorized_modes(tmp_path, monkeypatch, current, target, approved):
    monkeypatch.setenv("AUTOMATION_CASE_ENGINE_DOWNGRADE_APPROVED", "1" if approved else "0")
    path = tmp_path / "route.json"
    path.write_text(json.dumps(definition(current)))
    assert pipeline.run(["validate-case-engine-transition", "--task-definition", str(path),
                         "--target-engine", target])["status"] == "passed"


def test_deploy_entry_rejects_omitted_engine():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["bash", str(root / "deployment/deploy_automation_ecs_release.sh"),
                             "--environment", "preproduction"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "explicit --automation-case-engine" in result.stderr
