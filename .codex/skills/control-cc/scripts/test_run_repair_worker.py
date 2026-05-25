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
SCORE_SCRIPT = Path(__file__).with_name("score_packet.py")
REVIEW_SCRIPT = Path(__file__).with_name("review_worker_result.py")


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


def write_score_file(root: Path, score: int, decision: str = "split_required") -> Path:
    score_file = root.parent / f"{root.name}-score.json"
    score_file.write_text(
        json.dumps({"score": score, "decision": decision, "reasons": ["test"]}),
        encoding="utf-8",
    )
    return score_file


def valid_worker_result(result_body: str) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result_body,
        "total_cost_usd": 0.01,
        "modelUsage": {"opus": {"inputTokens": 1}},
        "permission_denials": [],
    }


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
    assert report["worker_call_report"]["success"] is False
    assert report["worker_call_report"]["optimization"]
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
    assert report["saved_partial_patch"]
    assert Path(str(report["saved_partial_patch"])).read_text(encoding="utf-8").find("partial worker edit") != -1
    assert report["worker_call_report"]["success"] is False
    assert report["worker_call_report"]["optimization"]
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_success_returns_worker_report_without_restoring() -> None:
    root = make_repo()
    payload = write_payload(root)
    worker_result = valid_worker_result(
        "## Result\nFixed\n\n## Files Changed\n- none\n\n## What Changed\n- none\n\n## Verification\n- Command: git status --short --branch\n- Result: clean\n\n## Risk / Uncertainty\n- none\n\n## Needs Codex Review\n- none\n"
    )
    fake_bin = fake_claude(root, f"#!/usr/bin/env bash\nprintf '%s\\n' {json.dumps(json.dumps(worker_result))}\n")

    result = run_worker(root, payload, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 0
    assert report["worker_status"] == "succeeded"
    assert report["failure_reason"] is None
    assert report["worker_result"]["total_cost_usd"] == 0.01
    assert report["worker_call_report"]["success"] is True
    assert report["worker_call_report"]["quality_score"] >= 7
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_success_normalizes_report_with_preamble() -> None:
    root = make_repo()
    payload = write_payload(root)
    worker_result = valid_worker_result(
        "The verification command ran successfully.\n\n## Result\nFixed\n\n## Files Changed\n- none\n\n## What Changed\n- none\n\n## Verification\n- Command: git status --short --branch\n- Result: clean\n\n## Risk / Uncertainty\n- none\n\n## Needs Codex Review\n- none\n"
    )
    fake_bin = fake_claude(root, f"#!/usr/bin/env bash\nprintf '%s\\n' {json.dumps(json.dumps(worker_result))}\n")

    result = run_worker(root, payload, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 0
    assert report["worker_status"] == "succeeded"
    assert report["normalized_worker_result"] is True
    assert report["worker_result"]["result"].startswith("## Result")
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_success_normalizes_result_status_punctuation() -> None:
    root = make_repo()
    payload = write_payload(root)
    worker_result = valid_worker_result(
        "## Result\nFixed.\n\n## Files Changed\n- none\n\n## What Changed\n- none\n\n## Verification\n- Command: git status --short --branch\n- Result: clean\n\n## Risk / Uncertainty\n- none\n\n## Needs Codex Review\n- none\n"
    )
    fake_bin = fake_claude(root, f"#!/usr/bin/env bash\nprintf '%s\\n' {json.dumps(json.dumps(worker_result))}\n")

    result = run_worker(root, payload, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 0
    assert report["worker_status"] == "succeeded"
    assert report["normalized_worker_result"] is True
    assert report["worker_result"]["result"].startswith("## Result\nFixed\n")
    assert report["result_status_found"] == "Fixed"


def test_invalid_result_status_reports_diagnostic_and_optimization() -> None:
    root = make_repo()
    payload = write_payload(root)
    worker_result = valid_worker_result(
        "## Result\nSuccess\n\n## Files Changed\n- none\n\n## What Changed\n- none\n\n## Verification\n- Command: git status --short --branch\n- Result: clean\n\n## Risk / Uncertainty\n- none\n\n## Needs Codex Review\n- none\n"
    )
    fake_bin = fake_claude(root, f"#!/usr/bin/env bash\nprintf '%s\\n' {json.dumps(json.dumps(worker_result))}\n")

    result = run_worker(root, payload, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["worker_status"] == "failed"
    assert report["failure_reason"] == "invalid_result_status"
    assert report["result_status_found"] == "Success"
    assert "expected one of Fixed, Not fixed, Blocked" in report["validation_failure_detail"]
    assert report["worker_call_report"]["optimization"]


def test_empty_result_after_edit_reports_specific_diagnostic_and_restores_diff() -> None:
    root = make_repo()
    payload = write_payload(root)
    worker_result = valid_worker_result("")
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "printf '\\npartial worker edit\\n' >> CLAUDE.md\n"
        f"printf '%s\\n' {json.dumps(json.dumps(worker_result))}\n",
    )

    result = run_worker(root, payload, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["worker_status"] == "failed"
    assert report["failure_reason"] == "empty_result_after_edit"
    assert report["exit_code"] == 0
    assert report["timed_out"] is False
    assert report["result_empty"] is True
    assert "CLAUDE.md" in report["partial_diff_stat"]
    assert report["saved_partial_patch"]
    assert report["restored_partial_diff"] is True
    assert "returned an empty result" in report["validation_failure_detail"]
    assert "partial diff" in report["worker_call_report"]["summary"]
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_compact_output_writes_full_report_file() -> None:
    root = make_repo()
    payload = write_payload(root)
    report_file = root.parent / f"{root.name}-full-report.json"
    worker_result = valid_worker_result(
        "## Result\nFixed\n\n## Files Changed\n- none\n\n## What Changed\n- none\n\n## Verification\n- Command: git status --short --branch\n- Result: clean\n\n## Risk / Uncertainty\n- none\n\n## Needs Codex Review\n- none\n"
    )
    fake_bin = fake_claude(root, f"#!/usr/bin/env bash\nprintf '%s\\n' {json.dumps(json.dumps(worker_result))}\n")

    result = run_worker(root, payload, fake_bin, "--compact-output", "--report-file", str(report_file))
    compact = parse_stdout(result)
    full = json.loads(report_file.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert compact["worker_status"] == "succeeded"
    assert compact["worker_call_report"]["success"] is True
    assert compact["full_report_path"] == str(report_file)
    assert "worker_result" not in compact
    assert full["worker_result"]["result"].startswith("## Result")


def test_atomic_packet_requires_write_scope_before_calling_worker() -> None:
    root = make_repo()
    payload = write_payload(root)
    fake_bin = fake_claude(root, "#!/usr/bin/env bash\nexit 99\n")

    result = run_worker(root, payload, fake_bin, "--packet-type", "atomic writing packet")
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "missing_write_scope"
    assert report["write_scope"] == []


def test_packet_score_blocks_oversized_writing_packet() -> None:
    root = make_repo()
    payload = write_payload(root)
    score_file = write_score_file(root, 7)
    fake_bin = fake_claude(root, "#!/usr/bin/env bash\nexit 99\n")

    result = run_worker(
        root,
        payload,
        fake_bin,
        "--packet-type",
        "atomic writing packet",
        "--write-scope",
        "CLAUDE.md",
        "--packet-score-file",
        str(score_file),
    )
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "packet_score_blocked"
    assert report["packet_score"] == 7


def test_read_only_probe_rejects_edit_tools_before_calling_worker() -> None:
    root = make_repo()
    payload = write_payload(root)
    fake_bin = fake_claude(root, "#!/usr/bin/env bash\nexit 99\n")

    result = run_worker(root, payload, fake_bin, "--packet-type", "read-only probe")
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "read_only_tools_violation"


def test_read_only_probe_modification_fails_and_restores() -> None:
    root = make_repo()
    payload = write_payload(root)
    worker_result = valid_worker_result(
        "## Result\nFixed\n\n## Files Changed\n- CLAUDE.md\n\n## What Changed\n- edited\n\n## Verification\n- Command: git status --short --branch\n- Result: clean\n\n## Risk / Uncertainty\n- none\n\n## Needs Codex Review\n- none\n"
    )
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "printf '\\nread-only violation\\n' >> CLAUDE.md\n"
        f"printf '%s\\n' {json.dumps(json.dumps(worker_result))}\n",
    )

    result = run_worker(root, payload, fake_bin, "--packet-type", "read-only probe", "--tools", "Read,Bash")
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "read_only_probe_modified_files"
    assert report["restored_partial_diff"] is True
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_write_scope_violation_fails_and_restores() -> None:
    root = make_repo()
    payload = write_payload(root)
    worker_result = valid_worker_result(
        "## Result\nFixed\n\n## Files Changed\n- CLAUDE.md\n\n## What Changed\n- edited\n\n## Verification\n- Command: git status --short --branch\n- Result: clean\n\n## Risk / Uncertainty\n- none\n\n## Needs Codex Review\n- none\n"
    )
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "printf '\\nout of scope\\n' >> CLAUDE.md\n"
        f"printf '%s\\n' {json.dumps(json.dumps(worker_result))}\n",
    )

    result = run_worker(
        root,
        payload,
        fake_bin,
        "--packet-type",
        "atomic writing packet",
        "--write-scope",
        "src/allowed.py",
    )
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "write_scope_violation"
    assert report["write_scope_matched"] is False
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_unsafe_repo_local_task_plan_path_is_rejected() -> None:
    root = make_repo()
    payload = write_payload(root)
    task_plan = root / "task-plan.md"
    task_plan.write_text("packet_type:\natomic writing packet\n", encoding="utf-8")
    fake_bin = fake_claude(root, "#!/usr/bin/env bash\nexit 99\n")

    result = run_worker(root, payload, fake_bin, "--task-plan-file", str(task_plan))
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "unsafe_task_plan_path"


def test_score_packet_blocks_high_complexity_writing_packet() -> None:
    root = make_repo()
    task_plan = root.parent / f"{root.name}-plan.md"
    task_plan.write_text(
        textwrap.dedent(
            """\
            packet_type:
            atomic writing packet

            write_scope:
            backend/services/rag_qa.py

            multi_stage_flow:
            true

            runtime_state:
            true
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCORE_SCRIPT), "--task-plan-file", str(task_plan)],
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads(result.stdout)

    assert report["score"] == 5
    assert report["decision"] == "split_required"
    assert report["allowed_packet_types"] == ["read-only probe"]


def test_score_packet_allows_small_atomic_packet() -> None:
    root = make_repo()
    task_plan = root.parent / f"{root.name}-plan.md"
    task_plan.write_text(
        textwrap.dedent(
            """\
            packet_type:
            atomic writing packet

            write_scope:
            backend/services/small.py
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCORE_SCRIPT), "--task-plan-file", str(task_plan)],
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads(result.stdout)

    assert report["score"] == 0
    assert report["decision"] == "writing_allowed"


def test_review_worker_result_rejects_out_of_scope_change() -> None:
    root = make_repo()
    (root / "CLAUDE.md").write_text("# changed\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(REVIEW_SCRIPT),
            "--write-scope",
            "src/allowed.py",
            "--fail-on-reject",
        ],
        cwd=root,
        text=True,
        capture_output=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 2
    assert report["accepted"] is False
    assert "changed_file_out_of_scope" in report["reasons"]


def test_review_worker_result_accepts_scoped_change() -> None:
    root = make_repo()
    (root / "CLAUDE.md").write_text("# changed\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REVIEW_SCRIPT), "--write-scope", "CLAUDE.md", "--fail-on-reject"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 0
    assert report["accepted"] is True


if __name__ == "__main__":
    test_timeout_restores_partial_diff_and_reports_worker_failure()
    test_invalid_json_restores_partial_diff_and_reports_worker_failure()
    test_success_returns_worker_report_without_restoring()
    test_success_normalizes_report_with_preamble()
    test_success_normalizes_result_status_punctuation()
    test_invalid_result_status_reports_diagnostic_and_optimization()
    test_empty_result_after_edit_reports_specific_diagnostic_and_restores_diff()
    test_compact_output_writes_full_report_file()
    test_atomic_packet_requires_write_scope_before_calling_worker()
    test_packet_score_blocks_oversized_writing_packet()
    test_read_only_probe_rejects_edit_tools_before_calling_worker()
    test_read_only_probe_modification_fails_and_restores()
    test_write_scope_violation_fails_and_restores()
    test_unsafe_repo_local_task_plan_path_is_rejected()
    test_score_packet_blocks_high_complexity_writing_packet()
    test_score_packet_allows_small_atomic_packet()
    test_review_worker_result_rejects_out_of_scope_change()
    test_review_worker_result_accepts_scoped_change()
    print("run_repair_worker tests passed")
