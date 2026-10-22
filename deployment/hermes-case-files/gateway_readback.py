"""Fail-closed, split ECS Exec readback for Hermes Gateway endpoints.

Health and toolsets are deliberately separate sessions. A wrapper exit code or
one successful marker cannot make the other endpoint pass. Every attempt writes
the raw stdout/stderr and a metadata JSON record; business tools are never
called.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import shlex
import subprocess
from typing import Callable, Mapping, Sequence


class ReadbackError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


HEALTH_MARKER = "GATEWAY_HEALTH_JSON"
TOOLSETS_MARKER = "GATEWAY_TOOLSETS_JSON"


def _marker_value(output: str, marker: str) -> str:
    lines = [line.split(marker + "=", 1)[1]
             for line in output.splitlines() if line.startswith(marker + "=")]
    if not lines:
        raise ReadbackError("marker_missing")
    if len(lines) != 1:
        raise ReadbackError("marker_duplicate")
    return lines[0]


def validate_result(stdout: str, stderr: str, returncode: int, marker: str,
                    *, timed_out: bool = False) -> Mapping[str, object]:
    combined = stdout + "\n" + stderr
    # EOF is an explicit transport failure. Even a valid marker is insufficient
    # when the session transport reports EOF.
    if timed_out:
        raise ReadbackError("timeout")
    if "EOF" in combined:
        raise ReadbackError("eof")
    if returncode != 0:
        raise ReadbackError("command_nonzero")
    value = _marker_value(stdout, marker)
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ReadbackError("marker_invalid_json") from exc
    if not isinstance(payload, (dict, list)):
        raise ReadbackError("marker_invalid_json")
    return payload


def _record(path: Path, *, stdout: str, stderr: str, returncode: int,
            timed_out: bool, marker: str, status: str, error: str | None,
            payload: object = None) -> None:
    path.write_text(json.dumps({
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stdout": stdout, "stderr": stderr, "returncode": returncode,
        "timed_out": timed_out, "eof": "EOF" in (stdout + "\n" + stderr),
        "marker": marker, "status": status, "error": error,
        "payload": payload,
    }, ensure_ascii=False, indent=2) + "\n")


def run_one(name: str, marker: str, command: Sequence[str], output: Path,
            runner: Callable[..., object] = subprocess.run) -> Mapping[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    stdout = stderr = ""
    returncode = 125
    timed_out = False
    try:
        result = runner(command, capture_output=True, text=True, timeout=45)
        stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
        payload = validate_result(stdout, stderr, returncode, marker)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        payload = None
        error = "timeout"
    except ReadbackError as exc:
        payload = None
        error = exc.code
    except Exception as exc:  # carrier failure is never a success
        payload = None
        error = "runner_error:" + type(exc).__name__
    else:
        error = None
    status = "passed" if error is None else "failed"
    _record(output / (name + ".json"), stdout=stdout, stderr=stderr,
            returncode=returncode, timed_out=timed_out, marker=marker,
            status=status, error=error, payload=payload)
    if error is not None:
        raise ReadbackError(name + ":" + error)
    return payload


def run_split_readbacks(commands: Mapping[str, Sequence[str]], output: Path,
                        runner: Callable[..., object] = subprocess.run) -> Mapping[str, object]:
    """Run both independent sessions and return both payloads only on success."""
    failures = []
    results = {}
    for name, marker in (("health", HEALTH_MARKER), ("toolsets", TOOLSETS_MARKER)):
        try:
            results[name] = run_one(name, marker, commands[name], output, runner)
        except ReadbackError as exc:
            failures.append(str(exc))
    if failures:
        raise ReadbackError(";".join(failures))
    return results


def _remote_code(endpoint: str) -> str:
    if endpoint == "health":
        return ("import os,json,urllib.request; "
                "r=urllib.request.Request('http://127.0.0.1:8642/v1/health', "
                "headers={'Authorization':'Bearer '+os.environ['API_SERVER_KEY']}); "
                "d=json.load(urllib.request.urlopen(r,timeout=20)); "
                "assert d.get('status')=='ok'; print('GATEWAY_HEALTH_JSON='+json.dumps(d),flush=True)")
    return ("import os,json,urllib.request; "
            "r=urllib.request.Request('http://127.0.0.1:8642/v1/toolsets', "
            "headers={'Authorization':'Bearer '+os.environ['API_SERVER_KEY']}); "
            "d=json.load(urllib.request.urlopen(r,timeout=20)); "
            "x=[t for t in d['data'] if t.get('name') in ('skills','supportportal_files')]; "
            "assert len(x)==2 and all(t.get('enabled') and t.get('configured') for t in x); "
            "print('GATEWAY_TOOLSETS_JSON='+json.dumps(x),flush=True)")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    base = ["aws", "ecs", "execute-command", "--region", args.region,
            "--cluster", args.cluster, "--task", args.task,
            "--container", "hermes", "--interactive", "--command"]
    commands = {name: base + ["python -c " + shlex.quote(_remote_code(name))]
                for name in ("health", "toolsets")}
    try:
        run_split_readbacks(commands, args.output)
    except ReadbackError as exc:
        print("GATEWAY_READBACK_FAILED=" + json.dumps({"error": str(exc)}))
        return 1
    print("GATEWAY_READBACK_PASSED=" + json.dumps({"endpoints": ["health", "toolsets"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
