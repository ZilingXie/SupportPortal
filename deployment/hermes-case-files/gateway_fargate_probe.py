"""One-shot Fargate probe for Hermes Gateway readback.

The probe avoids ECS Exec. It derives a temporary single-container task
definition, runs the existing Gateway, writes markers to CloudWatch, and uses
ECS STOPPED plus container exitCode=0 as the transport completion proof.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HEALTH_MARKER = "GATEWAY_HEALTH_JSON"
TOOLSETS_MARKER = "GATEWAY_TOOLSETS_JSON"
COMPLETE_MARKER = "GATEWAY_PROBE_COMPLETE=true"
TASK_KEYS = {
    "family", "taskRoleArn", "executionRoleArn", "networkMode",
    "containerDefinitions", "volumes", "placementConstraints",
    "requiresCompatibilities", "cpu", "memory", "ipcMode", "pidMode",
    "proxyConfiguration", "inferenceAccelerators", "ephemeralStorage",
    "runtimePlatform",
}
CONTAINER_KEYS = {
    "name", "image", "cpu", "memory", "memoryReservation", "portMappings",
    "essential", "entryPoint", "command", "environment", "environmentFiles",
    "mountPoints", "volumesFrom", "linuxParameters", "secrets", "dependsOn",
    "startTimeout", "stopTimeout", "user", "workingDirectory",
    "readonlyRootFilesystem", "interactive", "pseudoTerminal", "ulimits",
    "logConfiguration", "healthCheck", "systemControls",
}


class ProbeError(RuntimeError):
    pass


def _call(argv: Sequence[str], *, runner: Callable[..., Any]) -> Any:
    result = runner(argv, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ProbeError(f"command_failed:{argv[0]}:{result.returncode}")
    return result


def _json_call(argv: Sequence[str], *, runner: Callable[..., Any]) -> Mapping[str, Any]:
    return json.loads(_call(tuple(argv) + ("--output", "json"), runner=runner).stdout)


def derive_probe_definition(task_definition: Mapping[str, Any]) -> dict[str, Any]:
    containers = task_definition.get("containerDefinitions") or []
    hermes = next((c for c in containers if c.get("name") == "hermes"), None)
    if hermes is None:
        raise ProbeError("hermes_container_missing")
    mounted = {m.get("sourceVolume") for m in hermes.get("mountPoints", [])}
    result = {k: task_definition[k] for k in TASK_KEYS if k in task_definition}
    result["family"] = f"{task_definition['family']}-probe"
    probe_container = {k: v for k, v in hermes.items() if k in CONTAINER_KEYS}
    probe_container["mountPoints"] = [
        {**mount, "readOnly": True} for mount in hermes.get("mountPoints", [])
    ]
    result["containerDefinitions"] = [probe_container]
    result["volumes"] = [
        v for v in task_definition.get("volumes", []) if v.get("name") in mounted
    ]
    return result


def probe_command() -> str:
    # Keep the command in the image's existing shell; no business API is called.
    script = (
        "set -eu; gateway run >/tmp/gateway-probe.log 2>&1 & pid=$!; "
        "trap 'kill $pid 2>/dev/null || true; wait $pid 2>/dev/null || true' EXIT; "
        "python3 - <<'PY'\n"
        "import json,os,time,urllib.request\n"
        "h={'Authorization':'Bearer '+os.environ['API_SERVER_KEY']}\n"
        "def get(path):\n"
        "  last=None\n"
        "  for _ in range(30):\n"
        "    try:\n"
        "      with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8642'+path,headers=h),timeout=5) as r: return json.load(r)\n"
        "    except Exception as e: last=e; time.sleep(2)\n"
        "  raise last\n"
        "health=get('/v1/health'); assert health.get('status')=='ok'; print('GATEWAY_HEALTH_JSON='+json.dumps(health,separators=(',',':')),flush=True)\n"
        "data=get('/v1/toolsets'); selected=[x for x in data.get('data',[]) if x.get('name') in ('skills','supportportal_files')]; assert len(selected)==2 and all(x.get('enabled') and x.get('configured') for x in selected); print('GATEWAY_TOOLSETS_JSON='+json.dumps(selected,separators=(',',':')),flush=True)\n"
        "print('GATEWAY_PROBE_COMPLETE=true',flush=True)\n"
        "PY\n"
        "exit 0"
    )
    return script


def validate_logs(lines: Sequence[str]) -> Mapping[str, Any]:
    completed = [line for line in lines if line == COMPLETE_MARKER]
    if len(completed) != 1:
        raise ProbeError("completion_marker_missing" if not completed else "completion_marker_duplicate")
    values: dict[str, Any] = {}
    for marker in (HEALTH_MARKER, TOOLSETS_MARKER):
        found = [x.split("=", 1)[1] for x in lines if x.startswith(marker + "=")]
        if len(found) != 1:
            raise ProbeError(f"marker_{'missing' if not found else 'duplicate'}:{marker}")
        try:
            values[marker] = json.loads(found[0])
        except json.JSONDecodeError as exc:
            raise ProbeError(f"marker_invalid_json:{marker}") from exc
    return values


def run_probe(*, cluster: str, task_definition_arn: str, region: str,
              output: Path, runner: Callable[..., Any] = subprocess.run,
              sleep: Callable[[float], None] = time.sleep,
              network_service: str = "supportportal-preproduction-hermes",
              clock: Callable[[], float] = time.monotonic,
              stream_timeout_seconds: float = 60) -> Mapping[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    td_result = _json_call(("aws", "ecs", "describe-task-definition", "--region", region,
                            "--task-definition", task_definition_arn), runner=runner)
    definition = derive_probe_definition(td_result["taskDefinition"])
    definition["containerDefinitions"][0]["entryPoint"] = ["sh", "-c"]
    definition["containerDefinitions"][0]["command"] = [probe_command()]
    log_options = definition["containerDefinitions"][0].get("logConfiguration", {}).get("options", {})
    log_group = log_options.get("awslogs-group")
    log_prefix = log_options.get("awslogs-stream-prefix")
    if not log_group or not log_prefix:
        raise ProbeError("hermes_cloudwatch_identity_missing")
    definition_path = None
    network_path = None
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(definition, handle)
        definition_path = handle.name
    registered = None
    task_arn = None
    try:
        registered = _json_call(("aws", "ecs", "register-task-definition", "--region", region,
                                 "--cli-input-json", f"file://{definition_path}"), runner=runner)
        registered_arn = registered["taskDefinition"]["taskDefinitionArn"]
        net = _json_call(("aws", "ecs", "describe-services", "--region", region,
                          "--cluster", cluster, "--services", network_service,
                          "--query", "services[0].networkConfiguration"), runner=runner)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(net, handle)
            network_path = handle.name
        started = _json_call(("aws", "ecs", "run-task", "--region", region, "--cluster", cluster,
                              "--launch-type", "FARGATE", "--task-definition", registered_arn,
                              "--network-configuration", f"file://{network_path}"), runner=runner)
        task_arn = started["tasks"][0]["taskArn"]
        _call(("aws", "ecs", "wait", "tasks-stopped", "--region", region,
               "--cluster", cluster, "--tasks", task_arn), runner=runner)
        observed = _json_call(("aws", "ecs", "describe-tasks", "--region", region,
                               "--cluster", cluster, "--tasks", task_arn), runner=runner)
        task = observed["tasks"][0]
        container = task["containers"][0]
        if task.get("lastStatus") != "STOPPED" or container.get("exitCode") != 0:
            raise ProbeError(f"probe_exit:{container.get('exitCode')}:{task.get('stoppedReason','unknown')}")
        log_stream = f"{log_prefix}/hermes/{task_arn.rsplit('/', 1)[-1]}"
        logs = None
        deadline = clock() + stream_timeout_seconds
        while logs is None:
            try:
                logs = _json_call(("aws", "logs", "get-log-events", "--region", region,
                                   "--log-group-name", log_group,
                                   "--log-stream-name", log_stream,
                                   "--start-from-head"), runner=runner)
            except ProbeError:
                if clock() >= deadline:
                    raise ProbeError("cloudwatch_stream_unavailable")
                sleep(2)
        lines = [e.get("message", "").rstrip("\n") for e in logs.get("events", [])]
        token = logs.get("nextForwardToken")
        while token:
            page = _json_call(("aws", "logs", "get-log-events", "--region", region,
                               "--log-group-name", log_group,
                               "--log-stream-name", log_stream,
                               "--start-from-head", "--next-token", token), runner=runner)
            lines.extend(e.get("message", "").rstrip("\n") for e in page.get("events", []))
            next_token = page.get("nextForwardToken")
            if not next_token or next_token == token:
                break
            token = next_token
        payload = validate_logs(lines)
        evidence = {"task_definition": registered_arn, "task_arn": task_arn,
                    "exit_code": container.get("exitCode"), "markers": payload,
                    "completed_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        (output / "probe.json").write_text(json.dumps(evidence, indent=2) + "\n")
        return evidence
    finally:
        if registered:
            arn = registered.get("taskDefinition", {}).get("taskDefinitionArn")
            if arn:
                _call(("aws", "ecs", "deregister-task-definition", "--region", region,
                       "--task-definition", arn), runner=runner)
        for path in (definition_path, network_path):
            if path:
                Path(path).unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--task-definition", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_probe(cluster=args.cluster, task_definition_arn=args.task_definition,
                           region=args.region, output=args.output)
    except Exception as exc:
        print("GATEWAY_FARGATE_PROBE_FAILED=" + json.dumps({"error": str(exc)}))
        return 1
    print("GATEWAY_FARGATE_PROBE_PASSED=" + json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
