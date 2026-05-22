#!/usr/bin/env python3
"""Tests for run_repair_worker.py failure handling."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_repair_worker.py")


def run(command: list[str], cwd: Path, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, **kwargs)


def make_repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="repair-worker-test-"))
    run(["git", "init", "-q"], root, check=True)
    run(["git", "config", "user.email", "test@example.com"], root, check=True)
    run(["git", "config", "user.name", "Test User"], root, check=True)
    (root / "CLAUDE.md").write_text("# Claude Code Rules\n", encoding="utf-8")
    run(["git", "add", "CLAUDE.md"], root, check=True)
    run(["git", "commit", "-q", "-m", "baseline"], root, check=True)
    return root


def write_payload(root: Path) -> Path:
    payload = root.parent / f"{root.name}-payload.md"
    payload.write_text(
        textwrap.dedent(
            """\
            /repair-worker

            goal:
            Test worker runner behavior.

            scope_hints:
            - CLAUDE.md

            known_context:
            - This is a test.

            constraints:
            - Do not stage or commit.

            verification:
            git status --short --branch

            acceptance:
            - Return structured output.
            """
        ),
        encoding="utf-8",
    )
    return payload


def fake_claude(root: Path, body: str) -> Path:
    fake_bin = root.parent / f"{root.name}-fake-bin"
    fake_bin.mkdir()
    script = fake_bin / "claude"
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return fake_bin


def run_worker(root: Path, payload: Path, fake_bin: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--payload-file",
            str(payload),
            "--timeout-sec",
            "1",
            "--restore-on-failure",
            *extra,
        ],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
    )


def parse_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def test_timeout_restores_partial_diff_and_reports_worker_failure() -> None:
    root = make_repo()
    payload = write_payload(root)
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "printf '\\npartial worker edit\\n' >> CLAUDE.md\n"
        "sleep 30\n",
    )

    result = run_worker(root, payload, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["worker_status"] == "failed"
    assert report["failure_reason"] == "timeout"
    assert "CLAUDE.md" in report["partial_diff_stat"]
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_invalid_json_restores_partial_diff_and_reports_worker_failure() -> None:
    root = make_repo()
    payload = write_payload(root)
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "printf '\\npartial worker edit\\n' >> CLAUDE.md\n"
        "echo 'not json'\n",
    )

    result = run_worker(root, payload, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["worker_status"] == "failed"
    assert report["failure_reason"] == "invalid_json"
    assert "CLAUDE.md" in report["partial_diff_stat"]
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_success_returns_worker_report_without_restoring() -> None:
    root = make_repo()
    payload = write_payload(root)
    worker_result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "## Result\nFixed\n\n## Files Changed\n- none\n\n## What Changed\n- none\n\n## Verification\n- Command: git status --short --branch\n- Result: clean\n\n## Risk / Uncertainty\n- none\n\n## Needs Codex Review\n- none\n",
        "total_cost_usd": 0.01,
        "modelUsage": {"opus": {"inputTokens": 1}},
        "permission_denials": [],
    }
    fake_bin = fake_claude(root, f"#!/usr/bin/env bash\nprintf '%s\\n' {json.dumps(json.dumps(worker_result))}\n")

    result = run_worker(root, payload, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 0
    assert report["worker_status"] == "succeeded"
    assert report["failure_reason"] is None
    assert report["worker_result"]["total_cost_usd"] == 0.01
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


if __name__ == "__main__":
    test_timeout_restores_partial_diff_and_reports_worker_failure()
    test_invalid_json_restores_partial_diff_and_reports_worker_failure()
    test_success_returns_worker_report_without_restoring()
    print("run_repair_worker tests passed")
