#!/usr/bin/env python3
"""Run the repair-worker Claude CLI path with failure reporting and cleanup."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path


EXPECTED_HEADINGS = [
    "## Result",
    "## Files Changed",
    "## What Changed",
    "## Verification",
    "## Risk / Uncertainty",
    "## Needs Codex Review",
]

OUTPUT_GUARD = (
    "For /repair-worker tasks, the final answer must start with ## Result and use exactly "
    "these H2 headings in order: ## Result, ## Files Changed, ## What Changed, "
    "## Verification, ## Risk / Uncertainty, ## Needs Codex Review. No preamble, "
    "tables, alternate headings, or wrapper title."
)


def run_text(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def git_status() -> str:
    return run_text(["git", "status", "--short", "--branch"])


def git_porcelain() -> str:
    return run_text(["git", "status", "--porcelain=v1", "--untracked-files=all"])


def git_diff_stat() -> str:
    return run_text(["git", "diff", "--stat"])


def git_changed_files() -> list[str]:
    output = run_text(["git", "status", "--porcelain=v1", "--untracked-files=all"])
    files: list[str] = []
    for line in output.splitlines():
        if len(line) >= 4:
            files.append(line[3:])
    return files


def restore_worktree() -> None:
    subprocess.run(["git", "restore", "--staged", "--worktree", "--", "."], check=True)
    subprocess.run(["git", "clean", "-fd"], check=True, capture_output=True, text=True)


def without_fenced_code(markdown: str) -> str:
    return re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)


def result_headings(result: str) -> list[str]:
    return re.findall(r"^## .+$", without_fenced_code(result), flags=re.MULTILINE)


def validate_worker_json(payload: dict[str, object]) -> str | None:
    if payload.get("is_error"):
        return "worker_error"
    if payload.get("permission_denials"):
        return "permission_denied"
    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        return "missing_result"
    result = result.strip()
    if not result.startswith("## Result"):
        return "missing_report_sections"
    if result_headings(result) != EXPECTED_HEADINGS:
        return "missing_report_sections"
    if not re.search(r"^## Result\s*\n\s*(Fixed|Not fixed|Blocked)\s*$", result, flags=re.MULTILINE):
        return "invalid_result_status"
    return None


def truncate(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def emit(report: dict[str, object], exit_code: int) -> int:
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


def build_command(args: argparse.Namespace, payload: str) -> list[str]:
    command = [
        args.claude_bin,
        "--bare",
        "-p",
        payload,
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "--tools",
        args.tools,
        "--model",
        args.model,
        "--effort",
        args.effort,
        "--append-system-prompt",
        OUTPUT_GUARD,
        "--no-session-persistence",
    ]
    if args.max_budget_usd is not None:
        command.extend(["--max-budget-usd", str(args.max_budget_usd)])
    return command


def run_worker(command: list[str], timeout_sec: float) -> tuple[int | None, str, str, bool, float]:
    start = time.monotonic()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_sec)
        return process.returncode, stdout, stderr, False, time.monotonic() - start
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return None, stdout or "", stderr or "", True, time.monotonic() - start


def base_report(args: argparse.Namespace, before_status: str) -> dict[str, object]:
    return {
        "worker_status": "failed",
        "failure_reason": None,
        "timed_out": False,
        "exit_code": None,
        "duration_sec": None,
        "model": args.model,
        "effort": args.effort,
        "tools": args.tools,
        "before_status": before_status,
        "after_status": None,
        "partial_diff_stat": "",
        "partial_diff_files": [],
        "restored_partial_diff": False,
        "stdout": "",
        "stderr": "",
        "worker_result": None,
        "total_cost_usd": None,
        "modelUsage": None,
        "permission_denials": None,
        "codex_action_required": (
            "Record the worker failure in the final report. If the worker left a partial diff, "
            "restore it before Codex takes over unless the user explicitly asks to inspect it."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-file", type=Path, required=True)
    parser.add_argument("--timeout-sec", type=float, default=1200)
    parser.add_argument("--model", default="opus")
    parser.add_argument("--effort", default="max", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--tools", default="Read,Edit,Bash")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--max-budget-usd", type=float, default=None, help="Optional smoke-test safety cap")
    parser.add_argument("--restore-on-failure", action="store_true")
    parser.add_argument("--allow-dirty-baseline", action="store_true")
    args = parser.parse_args()

    payload = args.payload_file.read_text(encoding="utf-8")
    before_status = git_status()
    before_porcelain = git_porcelain()
    report = base_report(args, before_status)

    if before_porcelain and not args.allow_dirty_baseline:
        report["failure_reason"] = "dirty_baseline"
        report["after_status"] = before_status
        report["partial_diff_files"] = git_changed_files()
        return emit(report, 2)

    command = build_command(args, payload)
    exit_code, stdout, stderr, timed_out, duration = run_worker(command, args.timeout_sec)
    report.update(
        {
            "timed_out": timed_out,
            "exit_code": exit_code,
            "duration_sec": round(duration, 3),
            "stdout": truncate(stdout),
            "stderr": truncate(stderr),
            "partial_diff_stat": git_diff_stat(),
            "partial_diff_files": git_changed_files(),
        }
    )

    parsed: dict[str, object] | None = None
    failure_reason: str | None = None
    if timed_out:
        failure_reason = "timeout"
    elif exit_code != 0:
        failure_reason = "nonzero_exit"
    else:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            failure_reason = "invalid_json"

    if parsed is not None:
        report["worker_result"] = parsed
        report["total_cost_usd"] = parsed.get("total_cost_usd")
        report["modelUsage"] = parsed.get("modelUsage")
        report["permission_denials"] = parsed.get("permission_denials")
        failure_reason = validate_worker_json(parsed)

    if failure_reason is None:
        report["worker_status"] = "succeeded"
        report["failure_reason"] = None
        report["after_status"] = git_status()
        return emit(report, 0)

    report["failure_reason"] = failure_reason
    if args.restore_on_failure:
        restore_worktree()
        report["restored_partial_diff"] = True
    report["after_status"] = git_status()
    return emit(report, 2)


if __name__ == "__main__":
    raise SystemExit(main())
