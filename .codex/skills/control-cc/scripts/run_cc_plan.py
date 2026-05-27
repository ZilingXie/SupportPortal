#!/usr/bin/env python3
"""Run a control-cc implementation plan through Claude Code with light guards."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path


OUTPUT_GUARD = (
    "For /control-cc-worker implementation plans, execute the plan using your judgment, "
    "run the requested verification when possible, and return a concise completion report. "
    "Include changed files, verification evidence, remaining risks, and anything Codex "
    "should review. Do not commit, push, edit global skill directories, or hide test "
    "failures."
)

DEBUG_MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|debugger|pdb\.set_trace|console\.log|print\()", re.IGNORECASE)
ARTIFACT_SUFFIXES = (".log", ".tmp", ".temp", ".bak", ".orig", ".patch", ".rej", ".swp")
ARTIFACT_NAMES = {".DS_Store"}
ARTIFACT_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
PROMPT_CHANGE_PREFIXES = (
    ".codex/skills/",
    ".claude/skills/",
    "backend/services/prompts/",
)
PROMPT_CHANGE_FILES = {
    ".codex/skills/control-cc/agents/openai.yaml",
}
RAG_CHANGE_HINTS = (
    "rag",
    "retrieval",
    "embedding",
    "vector",
)


def run_text(command: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True).stdout


def git_status() -> str:
    return run_text(["git", "status", "--short", "--branch"])


def git_porcelain() -> str:
    return run_text(["git", "status", "--porcelain=v1", "--untracked-files=all"])


def git_changed_files() -> list[str]:
    files: list[str] = []
    for line in git_porcelain().splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return files


def git_untracked_files() -> list[str]:
    files: list[str] = []
    for line in git_porcelain().splitlines():
        if line.startswith("?? ") and len(line) >= 4:
            files.append(line[3:])
    return files


def git_diff_with_untracked(command: list[str]) -> str:
    untracked = git_untracked_files()
    if untracked:
        subprocess.run(["git", "add", "-N", "--", *untracked], check=True)
    try:
        return run_text(command)
    finally:
        if untracked:
            subprocess.run(["git", "reset", "-q", "--", *untracked], check=True)


def git_diff_stat() -> str:
    return git_diff_with_untracked(["git", "diff", "--stat"])


def git_diff_patch() -> str:
    return git_diff_with_untracked(["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--"])


def restore_worktree() -> None:
    subprocess.run(["git", "restore", "--staged", "--worktree", "--", "."], check=True)
    subprocess.run(["git", "clean", "-fd"], check=True, capture_output=True, text=True)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def write_json_artifact(prefix: str, suffix: str, payload: dict[str, object], path: Path | None = None) -> str:
    if path is None:
        path = make_temp_path(prefix, suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def write_temp_artifact(prefix: str, suffix: str, text: str) -> str | None:
    if not text:
        return None
    artifact = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix=prefix, suffix=suffix)
    with artifact:
        artifact.write(text)
    return artifact.name


def make_temp_path(prefix: str, suffix: str) -> Path:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return Path(path)


def save_current_patch(prefix: str) -> str | None:
    return write_temp_artifact(prefix, ".patch", git_diff_patch())


def added_lines_from_patch(patch: str) -> list[str]:
    lines: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        lines.append(line[1:])
    return lines


def is_artifact_or_temp_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    parts = set(normalized.split("/"))
    return name in ARTIFACT_NAMES or normalized.endswith(ARTIFACT_SUFFIXES) or bool(parts & ARTIFACT_PARTS)


def is_prompt_related(path: str) -> bool:
    return path in PROMPT_CHANGE_FILES or path.startswith(PROMPT_CHANGE_PREFIXES)


def is_rag_related(path: str) -> bool:
    lower = path.lower()
    return lower.startswith(("backend/", "docs/", "scripts/")) and any(hint in lower for hint in RAG_CHANGE_HINTS)


def git_status_for_path(path: Path) -> dict[str, object]:
    result = subprocess.run(
        ["git", "-C", str(path), "status", "--short", "--branch"],
        capture_output=True,
        text=True,
    )
    status = result.stdout.strip()
    dirty_lines = [line for line in status.splitlines() if line and not line.startswith("##")]
    return {
        "path": str(path),
        "ok": result.returncode == 0,
        "status": status,
        "dirty": bool(dirty_lines),
    }


def build_review_packet(
    report: dict[str, object],
    *,
    diff_stat: str,
    patch: str,
    changed_files: list[str],
    root_workspace: Path | None,
) -> dict[str, object]:
    added_lines = added_lines_from_patch(patch)
    non_ascii_lines = [line for line in added_lines if any(ord(char) > 127 for char in line)]
    debug_lines = [line for line in added_lines if DEBUG_MARKER_RE.search(line)]
    artifact_files = [path for path in changed_files if is_artifact_or_temp_file(path)]
    prompt_change_log_required = any(is_prompt_related(path) for path in changed_files)
    prompt_change_log_touched = "docs/prompt_change_log.md" in changed_files
    rag_change_log_required = any(is_rag_related(path) for path in changed_files)
    rag_change_log_touched = "docs/rag_change_log.md" in changed_files
    missing_required_changelog = (
        (prompt_change_log_required and not prompt_change_log_touched)
        or (rag_change_log_required and not rag_change_log_touched)
    )
    root_workspace_status = git_status_for_path(root_workspace) if root_workspace else None
    worker_result = report.get("worker_result")
    worker_result_excerpt = ""
    if isinstance(worker_result, dict):
        worker_result_excerpt = truncate(str(worker_result.get("result") or ""), 1200)

    return {
        "schema_version": "control-cc-review-packet-v1",
        "plan_status": report.get("plan_status"),
        "failure_reason": report.get("failure_reason"),
        "availability_failure_reason": report.get("availability_failure_reason"),
        "worker_workspace": report.get("worker_workspace"),
        "plan_file": report.get("plan_file"),
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "diff_stat": diff_stat,
        "artifact_or_temp_files": artifact_files,
        "added_non_ascii_samples": [truncate(line, 240) for line in non_ascii_lines[:5]],
        "debug_or_todo_samples": [truncate(line, 240) for line in debug_lines[:5]],
        "prompt_change_log_required": prompt_change_log_required,
        "prompt_change_log_touched": prompt_change_log_touched,
        "rag_change_log_required": rag_change_log_required,
        "rag_change_log_touched": rag_change_log_touched,
        "missing_required_changelog": missing_required_changelog,
        "root_workspace_status": root_workspace_status,
        "worker_result_excerpt": worker_result_excerpt,
        "flags": {
            "added_non_ascii": bool(non_ascii_lines),
            "debug_or_todo_markers": bool(debug_lines),
            "artifact_or_temp_files": bool(artifact_files),
            "missing_required_changelog": missing_required_changelog,
            "root_workspace_dirty": bool(root_workspace_status and root_workspace_status.get("dirty")),
        },
        "codex_review_hint": (
            "Read this packet before opening long logs or full diffs. Expand only changed "
            "files or flagged risks unless the task is high risk."
        ),
    }


def write_review_packet(
    report: dict[str, object],
    *,
    diff_stat: str,
    patch: str,
    changed_files: list[str],
    review_packet_file: Path | None,
    root_workspace: Path | None,
) -> str:
    packet = build_review_packet(
        report,
        diff_stat=diff_stat,
        patch=patch,
        changed_files=changed_files,
        root_workspace=root_workspace,
    )
    report["review_packet"] = packet
    report["review_flags"] = packet["flags"]
    return write_json_artifact("control-cc-review-packet-", ".json", packet, review_packet_file)


def build_command(args: argparse.Namespace, plan: str) -> list[str]:
    command = [
        args.claude_bin,
        "--bare",
        "-p",
        plan,
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


def run_worker(
    command: list[str],
    *,
    timeout_sec: float,
    heartbeat_sec: float,
    attempt: int,
) -> dict[str, object]:
    start = time.monotonic()
    heartbeats: list[dict[str, object]] = []
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return {
            "attempt": attempt,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "timed_out": False,
            "startup_error": True,
            "duration_sec": round(time.monotonic() - start, 3),
            "heartbeats": heartbeats,
        }

    heartbeat_interval = max(0.01, heartbeat_sec)
    stdout = ""
    stderr = ""
    while True:
        elapsed = time.monotonic() - start
        remaining = timeout_sec - elapsed
        if remaining <= 0:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            return {
                "attempt": attempt,
                "exit_code": None,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "timed_out": True,
                "startup_error": False,
                "duration_sec": round(time.monotonic() - start, 3),
                "heartbeats": heartbeats,
            }
        try:
            stdout, stderr = process.communicate(timeout=min(heartbeat_interval, remaining))
            return {
                "attempt": attempt,
                "exit_code": process.returncode,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "timed_out": False,
                "startup_error": False,
                "duration_sec": round(time.monotonic() - start, 3),
                "heartbeats": heartbeats,
            }
        except subprocess.TimeoutExpired:
            heartbeats.append(
                {
                    "attempt": attempt,
                    "elapsed_sec": round(time.monotonic() - start, 3),
                    "pid": process.pid,
                    "running": process.poll() is None,
                }
            )


def validation_failure(payload: dict[str, object]) -> str | None:
    if payload.get("is_error"):
        return "worker_error"
    if payload.get("permission_denials"):
        return "permission_denied"
    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        return "missing_result"
    return None


def availability_failure_reason(failure_reason: str | None, changed_files: list[str]) -> str | None:
    if failure_reason in {"startup_error", "nonzero_exit", "invalid_json"}:
        return failure_reason
    if failure_reason == "missing_result" and not changed_files:
        return failure_reason
    return None


def claude_call_report(report: dict[str, object]) -> dict[str, object]:
    succeeded = report.get("plan_status") == "succeeded"
    if succeeded:
        return {
            "success": True,
            "status": "succeeded",
            "summary": "Claude Code completed the implementation plan; Codex must review the diff and verification evidence.",
            "failure_reason": None,
        }
    reason = report.get("failure_reason") or "unknown"
    return {
        "success": False,
        "status": "failed",
        "summary": f"Claude Code plan run failed: {reason}.",
        "failure_reason": reason,
        "availability_failure_reason": report.get("availability_failure_reason"),
        "cleanup": "restored" if report.get("restored_partial_diff") else "not_restored",
    }


def base_report(args: argparse.Namespace, before_status: str) -> dict[str, object]:
    return {
        "plan_status": "failed",
        "failure_reason": None,
        "timed_out": False,
        "exit_code": None,
        "duration_sec": None,
        "model": args.model,
        "effort": args.effort,
        "tools": args.tools,
        "plan_file": str(args.plan_file),
        "worker_workspace": str(Path.cwd()),
        "attempt_count": 0,
        "retry_count": 0,
        "max_unavailable_retries": args.max_unavailable_retries,
        "availability_failure_reason": None,
        "attempts": [],
        "heartbeats": [],
        "before_status": before_status,
        "after_status": None,
        "stdout": "",
        "stdout_path": None,
        "stderr": "",
        "stderr_path": None,
        "worker_result": None,
        "total_cost_usd": None,
        "modelUsage": None,
        "permission_denials": None,
        "partial_diff_stat": "",
        "changed_files": [],
        "success_patch": None,
        "saved_partial_patch": None,
        "restored_partial_diff": False,
        "claude_call_report": None,
        "review_packet_path": None,
        "review_packet": None,
        "review_flags": None,
    }


def add_heartbeat_summary(report: dict[str, object]) -> None:
    heartbeats = report.get("heartbeats")
    if isinstance(heartbeats, list):
        report["heartbeat_count"] = len(heartbeats)
        report["last_heartbeat"] = heartbeats[-1] if heartbeats else None


def compact_report(report: dict[str, object]) -> dict[str, object]:
    keys = [
        "plan_status",
        "failure_reason",
        "duration_sec",
        "model",
        "effort",
        "tools",
        "partial_diff_stat",
        "changed_files",
        "success_patch",
        "saved_partial_patch",
        "restored_partial_diff",
        "stdout_path",
        "stderr_path",
        "full_report_path",
        "total_cost_usd",
        "exit_code",
        "timed_out",
        "attempt_count",
        "retry_count",
        "max_unavailable_retries",
        "availability_failure_reason",
        "heartbeat_count",
        "last_heartbeat",
        "plan_file",
        "worker_workspace",
        "claude_call_report",
        "review_packet_path",
        "review_flags",
    ]
    return {key: report.get(key) for key in keys if key in report}


def emit(
    report: dict[str, object],
    exit_code: int,
    *,
    compact_output: bool,
    report_file: Path | None,
) -> int:
    if report_file is None and compact_output:
        report_file = make_temp_path("control-cc-plan-report-", ".json")
    add_heartbeat_summary(report)
    if report_file is not None:
        report["full_report_path"] = str(report_file)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    output = compact_report(report) if compact_output else report
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-file", type=Path, required=True)
    parser.add_argument("--timeout-sec", type=float, default=1200)
    parser.add_argument("--heartbeat-sec", type=float, default=60)
    parser.add_argument("--retry-interval-sec", type=float, default=10)
    parser.add_argument("--max-unavailable-retries", type=int, default=3)
    parser.add_argument("--model", default="opus")
    parser.add_argument("--effort", default="max", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--tools", default="Read,Edit,Bash")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--max-budget-usd", type=float, default=None)
    parser.add_argument("--restore-on-failure", action="store_true")
    parser.add_argument("--allow-dirty-baseline", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--report-file", type=Path, default=None)
    parser.add_argument("--review-packet-file", type=Path, default=None)
    parser.add_argument("--root-workspace", type=Path, default=None)
    parser.add_argument("--inline-log-chars", type=int, default=2000)
    args = parser.parse_args()

    plan = args.plan_file.read_text(encoding="utf-8")
    before_status = git_status()
    before_porcelain = git_porcelain()
    report = base_report(args, before_status)

    if before_porcelain and not args.allow_dirty_baseline:
        report["failure_reason"] = "dirty_baseline"
        report["changed_files"] = git_changed_files()
        report["after_status"] = before_status
        report["claude_call_report"] = claude_call_report(report)
        return emit(report, 2, compact_output=args.compact_output, report_file=args.report_file)

    command = build_command(args, plan)
    max_attempts = max(1, args.max_unavailable_retries + 1)
    final_failure_reason: str | None = None

    for attempt in range(1, max_attempts + 1):
        attempt_result = run_worker(
            command,
            timeout_sec=args.timeout_sec,
            heartbeat_sec=args.heartbeat_sec,
            attempt=attempt,
        )
        stdout = str(attempt_result["stdout"])
        stderr = str(attempt_result["stderr"])
        timed_out = bool(attempt_result["timed_out"])
        exit_code = attempt_result["exit_code"]
        stdout_path = write_temp_artifact(f"control-cc-plan-attempt-{attempt}-stdout-", ".log", stdout)
        stderr_path = write_temp_artifact(f"control-cc-plan-attempt-{attempt}-stderr-", ".log", stderr)
        changed_files = git_changed_files()
        diff_stat = git_diff_stat()

        parsed: dict[str, object] | None = None
        failure_reason: str | None = None
        if attempt_result.get("startup_error"):
            failure_reason = "startup_error"
        elif timed_out:
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
            failure_reason = validation_failure(parsed)

        attempt_summary = {
            "attempt": attempt,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "startup_error": bool(attempt_result.get("startup_error")),
            "duration_sec": attempt_result["duration_sec"],
            "failure_reason": failure_reason,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "changed_files": changed_files,
            "partial_diff_stat": diff_stat,
            "heartbeats": attempt_result["heartbeats"],
        }
        report["attempts"].append(attempt_summary)
        report["heartbeats"].extend(attempt_result["heartbeats"])
        report.update(
            {
                "attempt_count": attempt,
                "retry_count": attempt - 1,
                "timed_out": timed_out,
                "exit_code": exit_code,
                "duration_sec": round(sum(float(item["duration_sec"]) for item in report["attempts"]), 3),
                "stdout": truncate(stdout, args.inline_log_chars),
                "stdout_path": stdout_path,
                "stderr": truncate(stderr, args.inline_log_chars),
                "stderr_path": stderr_path,
                "partial_diff_stat": diff_stat,
                "changed_files": changed_files,
            }
        )

        if failure_reason is None:
            report["plan_status"] = "succeeded"
            success_patch = git_diff_patch()
            report["success_patch"] = write_temp_artifact("control-cc-plan-success-", ".patch", success_patch)
            report["review_packet_path"] = write_review_packet(
                report,
                diff_stat=diff_stat,
                patch=success_patch,
                changed_files=changed_files,
                review_packet_file=args.review_packet_file,
                root_workspace=args.root_workspace,
            )
            report["after_status"] = git_status()
            report["claude_call_report"] = claude_call_report(report)
            return emit(report, 0, compact_output=args.compact_output, report_file=args.report_file)

        availability_reason = availability_failure_reason(failure_reason, changed_files)
        if availability_reason and attempt < max_attempts:
            attempt_summary["saved_partial_patch"] = save_current_patch(f"control-cc-plan-attempt-{attempt}-partial-")
            if args.restore_on_failure:
                restore_worktree()
            time.sleep(max(0, args.retry_interval_sec))
            final_failure_reason = availability_reason
            continue

        if availability_reason and attempt == max_attempts:
            final_failure_reason = availability_reason
            report["failure_reason"] = "claude_unavailable"
            report["availability_failure_reason"] = availability_reason
        else:
            final_failure_reason = failure_reason
            report["failure_reason"] = failure_reason
        break

    partial_patch = git_diff_patch()
    report["saved_partial_patch"] = write_temp_artifact("control-cc-plan-partial-", ".patch", partial_patch)
    report["review_packet_path"] = write_review_packet(
        report,
        diff_stat=str(report.get("partial_diff_stat") or ""),
        patch=partial_patch,
        changed_files=list(report.get("changed_files") or []),
        review_packet_file=args.review_packet_file,
        root_workspace=args.root_workspace,
    )
    if args.restore_on_failure:
        restore_worktree()
        report["restored_partial_diff"] = True
    report["after_status"] = git_status()
    if report.get("availability_failure_reason") is None:
        report["availability_failure_reason"] = final_failure_reason if report["failure_reason"] == "claude_unavailable" else None
    report["claude_call_report"] = claude_call_report(report)
    return emit(report, 2, compact_output=args.compact_output, report_file=args.report_file)


if __name__ == "__main__":
    raise SystemExit(main())
