#!/usr/bin/env python3
"""Tests for the lightweight control-cc plan runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_cc_plan.py")
CANDIDATE_SCRIPT = Path(__file__).with_name("candidate_worktree.py")


def run(command: list[str], cwd: Path, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, **kwargs)


def make_repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="cc-plan-test-"))
    run(["git", "init", "-q"], root, check=True)
    run(["git", "config", "user.email", "test@example.com"], root, check=True)
    run(["git", "config", "user.name", "Test User"], root, check=True)
    (root / "README.md").write_text("# baseline\n", encoding="utf-8")
    run(["git", "add", "README.md"], root, check=True)
    run(["git", "commit", "-q", "-m", "baseline"], root, check=True)
    return root


def write_plan(root: Path) -> Path:
    plan = root.parent / f"{root.name}-plan.md"
    plan.write_text(
        textwrap.dedent(
            """\
            /control-cc-worker

            goal:
            Execute this implementation plan.

            implementation_plan:
            - Make the requested change.

            verification:
            git status --short --branch
            """
        ),
        encoding="utf-8",
    )
    return plan


def fake_claude(root: Path, body: str) -> Path:
    fake_bin = root.parent / f"{root.name}-fake-bin"
    fake_bin.mkdir()
    script = fake_bin / "claude"
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return fake_bin


def run_plan(root: Path, plan: Path, fake_bin: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--plan-file",
            str(plan),
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


def success_payload(result_text: str = "Implemented the plan. Verification passed.") -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result_text,
        "total_cost_usd": 0.02,
        "modelUsage": {"opus": {"inputTokens": 1}},
        "permission_denials": [],
    }


def test_success_accepts_natural_language_json_and_saves_patch() -> None:
    root = make_repo()
    plan = write_plan(root)
    payload = success_payload("Natural language completion report with no strict headings.")
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "printf '\\nplanned edit\\n' >> README.md\n"
        f"printf '%s\\n' {json.dumps(json.dumps(payload))}\n",
    )

    result = run_plan(root, plan, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 0
    assert report["plan_status"] == "succeeded"
    assert report["failure_reason"] is None
    assert report["worker_result"]["result"].startswith("Natural language")
    assert report["success_patch"]
    assert Path(str(report["success_patch"])).read_text(encoding="utf-8").find("planned edit") != -1
    assert "README.md" in report["changed_files"]
    assert report["claude_call_report"]["success"] is True
    assert report["attempt_count"] == 1


def test_dirty_baseline_blocks_before_calling_claude() -> None:
    root = make_repo()
    plan = write_plan(root)
    marker = root / "called.txt"
    fake_bin = fake_claude(root, f"#!/usr/bin/env bash\ntouch {marker}\nexit 99\n")
    (root / "README.md").write_text("# dirty\n", encoding="utf-8")

    result = run_plan(root, plan, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["plan_status"] == "failed"
    assert report["failure_reason"] == "dirty_baseline"
    assert not marker.exists()


def test_timeout_saves_partial_patch_and_restores() -> None:
    root = make_repo()
    plan = write_plan(root)
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "printf '\\npartial timeout edit\\n' >> README.md\n"
        "sleep 30\n",
    )

    result = run_plan(root, plan, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "timeout"
    assert report["saved_partial_patch"]
    assert Path(str(report["saved_partial_patch"])).read_text(encoding="utf-8").find("partial timeout edit") != -1
    assert report["restored_partial_diff"] is True
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_long_running_worker_records_heartbeats_in_report_without_inline_logs() -> None:
    root = make_repo()
    plan = write_plan(root)
    report_file = root.parent / f"{root.name}-report.json"
    payload = success_payload("Long run finished.")
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "sleep 0.25\n"
        "printf '\\nlong run edit\\n' >> README.md\n"
        f"printf '%s\\n' {json.dumps(json.dumps(payload))}\n",
    )

    result = run_plan(
        root,
        plan,
        fake_bin,
        "--timeout-sec",
        "2",
        "--heartbeat-sec",
        "0.1",
        "--compact-output",
        "--report-file",
        str(report_file),
    )
    compact = parse_stdout(result)
    full = json.loads(report_file.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert compact["plan_status"] == "succeeded"
    assert "stdout" not in compact
    assert "heartbeats" not in compact
    assert compact["heartbeat_count"] >= 1
    assert compact["last_heartbeat"]["attempt"] == 1
    assert compact["stdout_path"]
    assert compact["review_packet_path"]
    assert full["heartbeats"]
    assert full["heartbeats"][0]["attempt"] == 1


def test_success_writes_review_packet_with_mechanical_flags() -> None:
    root = make_repo()
    plan = write_plan(root)
    payload = success_payload("Implemented with verification evidence.")
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "printf '\\nTODO: remove debug trace ✨\\n' >> README.md\n"
        "printf 'temporary log\\n' > debug.log\n"
        f"printf '%s\\n' {json.dumps(json.dumps(payload))}\n",
    )

    result = run_plan(root, plan, fake_bin)
    report = parse_stdout(result)
    packet_path = Path(str(report["review_packet_path"]))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert packet["changed_files"] == ["README.md", "debug.log"]
    assert "README.md" in packet["diff_stat"]
    assert packet["flags"]["added_non_ascii"] is True
    assert packet["flags"]["debug_or_todo_markers"] is True
    assert packet["flags"]["artifact_or_temp_files"] is True
    assert packet["artifact_or_temp_files"] == ["debug.log"]
    assert packet["prompt_change_log_required"] is False


def test_skill_prompt_change_review_packet_requires_prompt_log() -> None:
    root = make_repo()
    plan = write_plan(root)
    payload = success_payload("Changed skill instructions.")
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        "mkdir -p .codex/skills/control-cc\n"
        "printf 'skill change\\n' > .codex/skills/control-cc/SKILL.md\n"
        f"printf '%s\\n' {json.dumps(json.dumps(payload))}\n",
    )

    result = run_plan(root, plan, fake_bin)
    report = parse_stdout(result)
    packet = json.loads(Path(str(report["review_packet_path"])).read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert packet["prompt_change_log_required"] is True
    assert packet["prompt_change_log_touched"] is False
    assert packet["flags"]["missing_required_changelog"] is True


def test_invalid_json_retries_then_reports_claude_unavailable_and_restores() -> None:
    root = make_repo()
    plan = write_plan(root)
    attempts = root.parent / f"{root.name}-attempts.txt"
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        f"printf x >> {attempts}\n"
        "printf '\\npartial invalid json edit\\n' >> README.md\n"
        "echo 'not json'\n",
    )

    result = run_plan(root, plan, fake_bin, "--retry-interval-sec", "0.01", "--max-unavailable-retries", "3")
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "claude_unavailable"
    assert report["availability_failure_reason"] == "invalid_json"
    assert report["attempt_count"] == 4
    assert attempts.read_text(encoding="utf-8") == "xxxx"
    assert report["saved_partial_patch"]
    assert report["restored_partial_diff"] is True
    assert run(["git", "status", "--short"], root, check=True).stdout == ""


def test_empty_result_without_diff_retries_then_reports_claude_unavailable() -> None:
    root = make_repo()
    plan = write_plan(root)
    attempts = root.parent / f"{root.name}-attempts.txt"
    payload = success_payload("")
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        f"printf x >> {attempts}\n"
        f"printf '%s\\n' {json.dumps(json.dumps(payload))}\n",
    )

    result = run_plan(root, plan, fake_bin, "--retry-interval-sec", "0.01", "--max-unavailable-retries", "3")
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "claude_unavailable"
    assert report["availability_failure_reason"] == "missing_result"
    assert report["attempt_count"] == 4
    assert attempts.read_text(encoding="utf-8") == "xxxx"


def test_permission_denial_fails_without_strict_heading_checks() -> None:
    root = make_repo()
    plan = write_plan(root)
    payload = success_payload("The content shape is otherwise acceptable.")
    payload["permission_denials"] = ["Edit denied"]
    fake_bin = fake_claude(root, f"#!/usr/bin/env bash\nprintf '%s\\n' {json.dumps(json.dumps(payload))}\n")

    result = run_plan(root, plan, fake_bin)
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "permission_denied"
    assert report["attempt_count"] == 1
    assert report["claude_call_report"]["success"] is False


def test_nonzero_exit_retries_then_reports_claude_unavailable() -> None:
    root = make_repo()
    plan = write_plan(root)
    attempts = root.parent / f"{root.name}-attempts.txt"
    fake_bin = fake_claude(root, "#!/usr/bin/env bash\n" f"printf x >> {attempts}\n" "exit 7\n")

    result = run_plan(root, plan, fake_bin, "--retry-interval-sec", "0.01", "--max-unavailable-retries", "3")
    report = parse_stdout(result)

    assert result.returncode == 2
    assert report["failure_reason"] == "claude_unavailable"
    assert report["availability_failure_reason"] == "nonzero_exit"
    assert report["exit_code"] == 7
    assert report["attempt_count"] == 4
    assert attempts.read_text(encoding="utf-8") == "xxxx"


def test_worker_blocked_result_does_not_retry() -> None:
    root = make_repo()
    plan = write_plan(root)
    attempts = root.parent / f"{root.name}-attempts.txt"
    payload = success_payload("Blocked: the plan needs broader API changes.")
    fake_bin = fake_claude(
        root,
        "#!/usr/bin/env bash\n"
        f"printf x >> {attempts}\n"
        f"printf '%s\\n' {json.dumps(json.dumps(payload))}\n",
    )

    result = run_plan(root, plan, fake_bin, "--retry-interval-sec", "0.01", "--max-unavailable-retries", "3")
    report = parse_stdout(result)

    assert result.returncode == 0
    assert report["plan_status"] == "succeeded"
    assert report["attempt_count"] == 1
    assert attempts.read_text(encoding="utf-8") == "x"


def test_candidate_worktree_exports_patch_and_cleans_up() -> None:
    root = make_repo()
    run_dir = root.parent / f"{root.name}-candidate"
    patch_file = run_dir / "candidate.patch"

    create = subprocess.run(
        [
            sys.executable,
            str(CANDIDATE_SCRIPT),
            "create",
            "--run-dir",
            str(run_dir),
            "--base-ref",
            "HEAD",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    created = json.loads(create.stdout)
    worktree = Path(created["worktree_path"])

    (worktree / "README.md").write_text("# candidate\n", encoding="utf-8")
    (worktree / "NEW.md").write_text("new file\n", encoding="utf-8")

    export = subprocess.run(
        [
            sys.executable,
            str(CANDIDATE_SCRIPT),
            "export-patch",
            "--worktree",
            str(worktree),
            "--patch-file",
            str(patch_file),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    exported = json.loads(export.stdout)

    assert exported["changed_files"] == ["NEW.md", "README.md"]
    patch_text = patch_file.read_text(encoding="utf-8")
    assert "diff --git a/README.md b/README.md" in patch_text
    assert "diff --git a/NEW.md b/NEW.md" in patch_text

    cleanup = subprocess.run(
        [sys.executable, str(CANDIDATE_SCRIPT), "cleanup", "--worktree", str(worktree)],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    cleaned = json.loads(cleanup.stdout)
    assert cleaned["removed"] is True
    assert not worktree.exists()


if __name__ == "__main__":
    test_success_accepts_natural_language_json_and_saves_patch()
    test_dirty_baseline_blocks_before_calling_claude()
    test_timeout_saves_partial_patch_and_restores()
    test_long_running_worker_records_heartbeats_in_report_without_inline_logs()
    test_success_writes_review_packet_with_mechanical_flags()
    test_skill_prompt_change_review_packet_requires_prompt_log()
    test_invalid_json_retries_then_reports_claude_unavailable_and_restores()
    test_empty_result_without_diff_retries_then_reports_claude_unavailable()
    test_permission_denial_fails_without_strict_heading_checks()
    test_nonzero_exit_retries_then_reports_claude_unavailable()
    test_worker_blocked_result_does_not_retry()
    test_candidate_worktree_exports_patch_and_cleans_up()
    print("run_cc_plan tests passed")
