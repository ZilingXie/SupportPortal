import importlib.util
import json
import subprocess
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
            {"name": "hermes", "image": "hermes", "mountPoints": [{"sourceVolume": "hermes-home", "readOnly": False}],
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
    assert result["containerDefinitions"][0]["mountPoints"] == [{"sourceVolume": "hermes-home", "readOnly": True}]


@pytest.mark.parametrize("lines,error", [
    (["GATEWAY_PROBE_COMPLETE=true"], "marker_missing:GATEWAY_HEALTH_JSON"),
    (["GATEWAY_HEALTH_JSON={}", "GATEWAY_HEALTH_JSON={}", "GATEWAY_TOOLSETS_JSON=[]", "GATEWAY_PROBE_COMPLETE=true"], "marker_duplicate:GATEWAY_HEALTH_JSON"),
    (["GATEWAY_HEALTH_JSON={}", "GATEWAY_TOOLSETS_JSON=[]"], "completion_marker_missing"),
    (["GATEWAY_HEALTH_JSON={}", "GATEWAY_TOOLSETS_JSON=[]", "GATEWAY_PROBE_COMPLETE=true", "GATEWAY_PROBE_COMPLETE=true"], "completion_marker_duplicate"),
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
    command = mod.probe_command()
    assert "gateway run" in command
    assert "GATEWAY_PROBE_COMPLETE=true" in command
    assert "support_case_files_publish" not in command
    assert "slack" not in command.lower()


class FakeRunner:
    def __init__(self, mod, *, exit_code=0, logs=None):
        self.mod = mod
        self.exit_code = exit_code
        self.logs = logs if logs is not None else [
            [
                'GATEWAY_HEALTH_JSON={"status":"ok"}',
                'GATEWAY_PROBE_COMPLETE=true',
            ],
            ['GATEWAY_TOOLSETS_JSON=[]'],
        ]
        self.calls = []
        self.temp_paths = []
        self.stream_failures = 0

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        for index, value in enumerate(argv):
            if value.startswith("file://") and ("--cli-input-json" in argv[max(0, index - 1):index] or
                                                 "--network-configuration" in argv[max(0, index - 1):index]):
                self.temp_paths.append(Path(value.removeprefix("file://")))
        text = " ".join(argv)
        if "describe-task-definition" in text:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"taskDefinition": {
                "family": "hermes", "taskRoleArn": "r", "executionRoleArn": "e",
                "networkMode": "awsvpc", "requiresCompatibilities": ["FARGATE"],
                "cpu": "1024", "memory": "6144", "volumes": [],
                "containerDefinitions": [{"name": "hermes", "image": "h",
                    "secrets": [], "mountPoints": [], "logConfiguration": {
                        "options": {"awslogs-group": "g", "awslogs-stream-prefix": "p"}}}]
            }}), "")
        if "register-task-definition" in text:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"taskDefinition": {"taskDefinitionArn": "arn:probe:1"}}), "")
        if "describe-services" in text:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"awsvpcConfiguration": {"subnets": ["subnet"], "securityGroups": ["sg"], "assignPublicIp": "DISABLED"}}), "")
        if "run-task" in text:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"tasks": [{"taskArn": "arn:task:1"}]}), "")
        if "describe-tasks" in text:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"tasks": [{"lastStatus": "STOPPED", "containers": [{"exitCode": self.exit_code}], "stoppedReason": "done"}]}), "")
        if "get-log-events" in text:
            if self.stream_failures:
                self.stream_failures -= 1
                return subprocess.CompletedProcess(argv, 1, "", "ResourceNotFoundException")
            page = self.logs.pop(0) if self.logs else []
            body = {"events": [{"message": line} for line in page]}
            if self.logs:
                body["nextForwardToken"] = "token-1"
            return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_run_probe_success_uses_stopped_exit_zero_paginates_and_deregisters(tmp_path):
    mod = load()
    runner = FakeRunner(mod, logs=[
        ['GATEWAY_HEALTH_JSON={"status":"ok"}'],
        ['GATEWAY_TOOLSETS_JSON=[]', 'GATEWAY_PROBE_COMPLETE=true'],
    ])
    result = mod.run_probe(cluster="c", task_definition_arn="td", region="r", output=tmp_path,
                           runner=runner, sleep=lambda _: None)
    assert result["exit_code"] == 0
    assert any("run-task" in " ".join(c) for c in runner.calls)
    assert any("deregister-task-definition" in " ".join(c) for c in runner.calls)
    assert len(runner.temp_paths) == 2
    assert all(not path.exists() for path in runner.temp_paths)


@pytest.mark.parametrize("exit_code", [1, None])
def test_run_probe_rejects_nonzero_or_unknown_exit_and_deregisters(tmp_path, exit_code):
    mod = load()
    runner = FakeRunner(mod, exit_code=exit_code if exit_code is not None else 0)
    if exit_code is None:
        original = runner
        def unknown(argv, **kwargs):
            result = original(argv, **kwargs)
            if "describe-tasks" in " ".join(argv):
                result.stdout = json.dumps({"tasks": [{"lastStatus": "STOPPED", "containers": [{}]}]})
            return result
        runner = unknown
    with pytest.raises(mod.ProbeError):
        mod.run_probe(cluster="c", task_definition_arn="td", region="r", output=tmp_path,
                      runner=runner, sleep=lambda _: None)
    calls = runner.calls if hasattr(runner, "calls") else original.calls
    assert any("deregister-task-definition" in " ".join(c) for c in calls)
    assert len(original.temp_paths) == 2
    assert all(not path.exists() for path in original.temp_paths)


def test_run_probe_rejects_cloudwatch_duplicate_and_cleans_up(tmp_path):
    mod = load()
    runner = FakeRunner(mod, logs=[
        ['GATEWAY_HEALTH_JSON={"status":"ok"}', 'GATEWAY_HEALTH_JSON={}'],
        ['GATEWAY_TOOLSETS_JSON=[]', 'GATEWAY_PROBE_COMPLETE=true'],
    ])
    with pytest.raises(mod.ProbeError, match="duplicate"):
        mod.run_probe(cluster="c", task_definition_arn="td", region="r", output=tmp_path,
                      runner=runner, sleep=lambda _: None)
    assert any("deregister-task-definition" in " ".join(c) for c in runner.calls)
    assert len(runner.temp_paths) == 2
    assert all(not path.exists() for path in runner.temp_paths)


@pytest.mark.parametrize("logs,error", [
    ([['GATEWAY_HEALTH_JSON={"status":"ok"}'], ['GATEWAY_PROBE_COMPLETE=true']], "marker_missing:GATEWAY_TOOLSETS_JSON"),
    ([['GATEWAY_HEALTH_JSON={'], ['GATEWAY_TOOLSETS_JSON=[]', 'GATEWAY_PROBE_COMPLETE=true']], "marker_invalid_json:GATEWAY_HEALTH_JSON"),
])
def test_run_probe_rejects_missing_or_invalid_cloudwatch_marker_and_cleans_up(tmp_path, logs, error):
    mod = load()
    runner = FakeRunner(mod, logs=logs)
    with pytest.raises(mod.ProbeError, match=error):
        mod.run_probe(cluster="c", task_definition_arn="td", region="r", output=tmp_path,
                      runner=runner, sleep=lambda _: None)
    assert any("deregister-task-definition" in " ".join(c) for c in runner.calls)
    assert len(runner.temp_paths) == 2
    assert all(not path.exists() for path in runner.temp_paths)


def test_run_probe_rejects_cloudwatch_stream_timeout_without_sleeping(tmp_path):
    mod = load()
    runner = FakeRunner(mod, logs=[])
    runner.stream_failures = 99
    clock_values = iter([0.0, 2.0])
    with pytest.raises(mod.ProbeError, match="cloudwatch_stream_unavailable"):
        mod.run_probe(cluster="c", task_definition_arn="td", region="r", output=tmp_path,
                      runner=runner, sleep=lambda _: None, clock=lambda: next(clock_values),
                      stream_timeout_seconds=1)
    assert any("deregister-task-definition" in " ".join(c) for c in runner.calls)
    assert len(runner.temp_paths) == 2
    assert all(not path.exists() for path in runner.temp_paths)
