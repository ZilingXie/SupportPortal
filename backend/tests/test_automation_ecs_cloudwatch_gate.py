"""Exercise the deployed shell gate/CLI, replacing only the external AWS boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(os.environ.get("CLOUDWATCH_GATE_TEST_SCRIPT", str(ROOT / "deployment/deploy_automation_ecs_release.sh")))
ROLES = ("api", "route", "worker")


def _task(role: str, digit: int, *, group: str | None = None, revision: int = 2,
          status: str = "RUNNING", exit_code: int | None = None,
          stop_code: str | None = None) -> dict:
    return {
        "taskArn": f"arn:aws:ecs:us-east-1:123:task/test/{digit:032x}",
        "group": group if group is not None else f"service:test-{role}",
        "taskDefinitionArn": f"arn:aws:ecs:us-east-1:123:task-definition/{role}:{revision}",
        "lastStatus": status, "stopCode": stop_code,
        "stoppedAt": datetime.now(timezone.utc).isoformat() if status == "STOPPED" else None,
        "containers": [{"name": role, "exitCode": exit_code}],
    }


def _event(role: str, task: dict, i: int = 1) -> dict:
    return {
        "logStreamName": f"{role}/{role}/{task['taskArn'].rsplit('/', 1)[-1]}",
        "eventId": str(i), "timestamp": int(time.time() * 1000) - 1000,
        "message": "ERROR customer text and sensitive details must not persist",
    }


@pytest.fixture
def gate(tmp_path: Path):
    data = {role: {"tasks": [_task(role, i)], "events": []}
            for i, role in enumerate(ROLES, 1)}
    state = tmp_path / "state"
    state.mkdir()
    for role in ROLES:
        (state / f"{role}.new-arn").write_text(data[role]["tasks"][0]["taskDefinitionArn"])
        (state / f"{role}.current.json").write_text(json.dumps({"taskDefinition": {
            "containerDefinitions": [{"name": role, "logConfiguration": {
                "options": {"awslogs-group": role}}}]}}))
    aws = tmp_path / "aws"
    aws.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
data = json.loads(Path(os.environ["AWS_FIXTURE"]).read_text())
with open(os.environ["AWS_CALLS"], "a") as out:
    out.write(json.dumps(args) + "\\n")
def arg(name):
    return args[args.index(name) + 1]
assert arg("--output") == "json" and "--query" not in args
assert "AWS_ACCESS_KEY_ID" not in os.environ
assert "AWS_SECRET_ACCESS_KEY" not in os.environ
assert "AWS_SESSION_TOKEN" not in os.environ
if "--profile" not in args:
    assert os.environ["AWS_PROFILE"] == "gate-test-provider"
assert os.environ["AWS_CONFIG_FILE"].endswith("original-config")
if args[:2] == ["ecs", "list-tasks"]:
    role = arg("--service-name").removeprefix("test-")
    if data[role].get("aws_failure"):
        sys.exit(9)
    desired = arg("--desired-status")
    tasks = [t for t in data[role]["tasks"]
             if t.get("group") == "service:test-" + role
             and (t["lastStatus"] == "STOPPED") == (desired == "STOPPED")]
    print(json.dumps({"taskArns": [t["taskArn"] for t in tasks]}))
elif args[:2] == ["ecs", "describe-tasks"]:
    refs = args[args.index("--tasks") + 1:args.index("--region")]
    assert 0 < len(refs) <= 100
    tasks = [t for role in data.values() for t in role["tasks"]
             if t["taskArn"] in refs or t["taskArn"].rsplit("/", 1)[-1] in refs]
    missing = [r for r in refs if not any(t["taskArn"] == r or
               t["taskArn"].rsplit("/", 1)[-1] == r for t in tasks)]
    print(json.dumps({"tasks": tasks, "failures": [{"reason": "MISSING"} for r in missing]}))
elif args[:2] == ["logs", "filter-log-events"]:
    role = arg("--log-group-name")
    assert arg("--log-stream-name-prefix") == role + "/" + role + "/"
    # The former shell did not send end-time; permit that for reverse validation.
    if "--end-time" in args:
        assert int(arg("--start-time")) < int(arg("--end-time"))
    assert arg("--filter-pattern") == "?ERROR ?Traceback ?Exception"
    print(json.dumps({"events": data[role]["events"]}))
else:
    raise AssertionError(args)
''')
    aws.chmod(0o700)
    calls = tmp_path / "calls.jsonl"

    def run(*, age_seconds: int = 60, python_bin: str = sys.executable):
        fixture = tmp_path / "fixture.json"
        fixture.write_text(json.dumps(data))
        env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
               "AWS_FIXTURE": str(fixture), "AWS_CALLS": str(calls),
               "AWS_ACCESS_KEY_ID": "test-static", "AWS_SECRET_ACCESS_KEY": "test-static",
               "AWS_SESSION_TOKEN": "test-static", "AWS_PROFILE": "terraform-profile",
               "AUTOMATION_AWS_PROFILE": "gate-test-provider",
               "AWS_CONFIG_FILE": str(tmp_path / "original-config")}
        start = int(time.time() * 1000) - age_seconds * 1000
        result = subprocess.run([
            "bash", "-c", '''
source "$1"
TEMP_DIR="$2"
PYTHON_BIN="$3"
CLUSTER=test
REGION=us-east-1
API_SERVICE=test-api
ROUTE_SERVICE=test-route
WORKER_SERVICE=test-worker
verify_cloudwatch "$4"
''', "bash", str(SCRIPT), str(state), python_bin, str(start),
        ], cwd=ROOT, env=env, text=True, capture_output=True)
        reports = {role: json.loads((state / f"{role}.cloudwatch-count.json").read_text())
                   for role in ROLES}
        return result, reports

    return data, run, calls


def test_old_revision_and_same_revision_probe_are_excluded_with_evidence(gate):
    data, run, _ = gate
    old = _task("worker", 10, revision=1, status="STOPPED", exit_code=1)
    probe = _task("worker", 11, group="family:worker")
    data["worker"]["tasks"] += [old, probe]
    data["worker"]["events"] = [_event("worker", old, i) for i in range(272)] + [
        _event("worker", probe, 273)]
    result, reports = run()
    assert result.returncode == 0, result.stderr
    report = reports["worker"]
    assert report["status"] == "passed" and report["error_count"] == 0
    assert report["excluded_counts"] == {"old_revision": 272, "non_service": 1}
    assert len(report["events"]) == 273
    assert {t["category"] for t in report["tasks"]} == {
        "release_service", "old_revision", "non_service"}
    assert "customer text" not in json.dumps(reports)


@pytest.mark.parametrize("status", ["RUNNING", "STOPPED"])
def test_release_errors_including_stopped_tasks_block_and_collect_other_roles(gate, status):
    data, run, _ = gate
    task = _task("api", 12, status=status, exit_code=0 if status == "STOPPED" else None)
    data["api"]["tasks"].append(task)
    data["api"]["events"] = [_event("api", task)]
    result, reports = run()
    assert result.returncode != 0
    assert reports["api"]["error_count"] == 1
    assert "release_service_errors" in reports["api"]["blocking_reasons"]
    assert reports["route"]["status"] == reports["worker"]["status"] == "passed"


@pytest.mark.parametrize("stop_code,exit_code,reason", [
    ("TaskFailedToStart", None, "release_task_failed"),
    ("EssentialContainerExited", 1, "release_task_failed"),
    ("ServiceSchedulerInitiated", None, "release_task_exit_unknown"),
])
def test_failed_release_task_without_logs_blocks(gate, stop_code, exit_code, reason):
    data, run, _ = gate
    data["worker"]["tasks"].append(_task(
        "worker", 13, status="STOPPED", stop_code=stop_code, exit_code=exit_code))
    result, reports = run()
    assert result.returncode != 0
    assert reports["worker"]["error_count"] == 0
    assert reason in reports["worker"]["blocking_reasons"]


@pytest.mark.parametrize("failure,reason", [
    ("unknown", "task_identity_unavailable"),
    ("incomplete", "task_identity_incomplete"),
    ("bad_stream", "unresolved_log_stream"),
    ("aws", "collection_failed"),
    ("no_service", "no_release_service_tasks"),
])
def test_identity_and_collection_gaps_fail_closed(gate, failure, reason):
    data, run, _ = gate
    if failure == "unknown":
        data["api"]["events"] = [_event("api", _task("api", 999))]
    elif failure == "incomplete":
        data["api"]["tasks"][0]["taskDefinitionArn"] = ""
    elif failure == "bad_stream":
        data["api"]["events"] = [{"logStreamName": "api/api/unrecognized"}]
    elif failure == "aws":
        data["api"]["aws_failure"] = True
    else:
        data["api"]["tasks"] = []
    result, reports = run()
    assert result.returncode != 0
    assert reason in reports["api"]["blocking_reasons"]
    assert reports["route"]["status"] == reports["worker"]["status"] == "passed"


def test_expired_resume_window_cannot_certify_missing_stopped_tasks(gate):
    _, run, calls = gate
    result, reports = run(age_seconds=56 * 60)
    assert result.returncode != 0
    assert not calls.exists()
    assert all("task_history_window_expired" in r["blocking_reasons"] for r in reports.values())


def test_resume_existing_task_errors_and_batched_describe_are_not_lost(gate):
    data, run, calls = gate
    # Reused running task predates this deployment; attribution does not filter createdAt.
    existing = data["worker"]["tasks"][0]
    existing["createdAt"] = "2020-01-01T00:00:00Z"
    data["worker"]["events"] = [_event("worker", existing)]
    data["worker"]["tasks"] += [
        _task("worker", i, status="STOPPED", exit_code=0) for i in range(20, 225)]
    result, reports = run()
    assert result.returncode != 0
    assert reports["worker"]["error_count"] == 1
    assert len(reports["worker"]["tasks"]) == 206
    requests = [json.loads(line) for line in calls.read_text().splitlines()]
    batches = [args[args.index("--tasks") + 1:args.index("--region")]
               for args in requests if args[:2] == ["ecs", "describe-tasks"]]
    assert sorted(map(len, batches)) == [1, 1, 6, 100, 100]


def test_reused_revision_failure_before_window_is_retained_but_not_gated(gate):
    data, run, _ = gate
    old = _task("worker", 14, status="STOPPED", exit_code=1)
    old["stoppedAt"] = "2020-01-01T00:00:00Z"
    data["worker"]["tasks"].append(old)
    result, reports = run()
    assert result.returncode == 0, result.stderr
    assert len(reports["worker"]["tasks"]) == 2


def test_stop_time_missing_is_not_certified(gate):
    data, run, _ = gate
    stopped = _task("worker", 14, status="STOPPED", exit_code=0)
    del stopped["stoppedAt"]
    data["worker"]["tasks"].append(stopped)
    result, reports = run()
    assert result.returncode != 0
    assert "release_task_stop_time_unknown" in reports["worker"]["blocking_reasons"]


@pytest.mark.parametrize("failure,reason", [
    ("missing_group", "missing_scope"), ("missing_python", "collector_unavailable"),
])
def test_collection_setup_failure_does_not_reuse_passed_reports(gate, failure, reason):
    _, run, calls = gate
    state = calls.parent / "state"
    for role in ROLES:
        (state / f"{role}.cloudwatch-count.json").write_text(json.dumps({
            "role": role, "status": "passed", "error_count": 0}))
    if failure == "missing_group":
        (state / "api.current.json").unlink()
    result, reports = run(python_bin="/nonexistent-python" if failure == "missing_python" else sys.executable)
    assert result.returncode != 0
    assert reports["api"]["status"] == "failed"
    assert reason in reports["api"]["blocking_reasons"]
    if failure == "missing_group":
        assert reports["route"]["status"] == reports["worker"]["status"] == "passed"
