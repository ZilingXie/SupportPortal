import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "deployment/hermes-case-files/gateway_fargate_probe.py"


def load():
    spec = importlib.util.spec_from_file_location("gateway_fargate_probe", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_derive_probe_definition_keeps_only_hermes_and_its_mounts():
    mod = load()
    definition = {
        "family": "hermes",
        "taskRoleArn": "role",
        "executionRoleArn": "exec",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "1024",
        "memory": "6144",
        "volumes": [
            {"name": "hermes-home", "efsVolumeConfiguration": {"fileSystemId": "fs"}},
            {"name": "unused", "host": {}},
        ],
        "containerDefinitions": [
            {"name": "sidecar", "image": "sidecar"},
            {"name": "hermes", "image": "hermes", "mountPoints": [{"sourceVolume": "hermes-home"}],
             "secrets": [{"name": "API_SERVER_KEY", "valueFrom": "ssm"}], "logConfiguration": {"logDriver": "awslogs"}},
        ],
        "status": "ACTIVE",
    }
    result = mod.derive_probe_definition(definition)
    assert result["family"] == "hermes-probe"
    assert [c["name"] for c in result["containerDefinitions"]] == ["hermes"]
    assert [v["name"] for v in result["volumes"]] == ["hermes-home"]
    assert "status" not in result
    assert result["containerDefinitions"][0]["secrets"] == definition["containerDefinitions"][1]["secrets"]


@pytest.mark.parametrize("lines,error", [
    (["GATEWAY_PROBE_COMPLETE=true"], "marker_missing:GATEWAY_HEALTH_JSON"),
    (["GATEWAY_HEALTH_JSON={}", "GATEWAY_HEALTH_JSON={}", "GATEWAY_TOOLSETS_JSON=[]", "GATEWAY_PROBE_COMPLETE=true"], "marker_duplicate:GATEWAY_HEALTH_JSON"),
    (["GATEWAY_HEALTH_JSON={}", "GATEWAY_TOOLSETS_JSON=[]"], "completion_marker_missing"),
])
def test_validate_logs_requires_unique_markers_and_completion(lines, error):
    mod = load()
    with pytest.raises(mod.ProbeError, match=error):
        mod.validate_logs(lines)


def test_validate_logs_returns_both_payloads():
    mod = load()
    lines = [
        'GATEWAY_HEALTH_JSON={"status":"ok"}',
        'GATEWAY_TOOLSETS_JSON=[{"name":"skills"}]',
        "GATEWAY_PROBE_COMPLETE=true",
    ]
    result = mod.validate_logs(lines)
    assert result[mod.HEALTH_MARKER]["status"] == "ok"
    assert result[mod.TOOLSETS_MARKER][0]["name"] == "skills"


def test_probe_command_has_no_business_write_and_emits_completion_marker():
    mod = load()
    command = " ".join(mod.probe_command())
    assert "gateway run" in command
    assert "GATEWAY_PROBE_COMPLETE=true" in command
    assert "support_case_files_publish" not in command
    assert "slack" not in command.lower()
