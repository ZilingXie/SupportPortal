#!/usr/bin/env python3
"""Legacy v2 strict control-cc Claude CLI runner."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
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
EXPECTED_RESULT_STATUSES = ["Fixed", "Not fixed", "Blocked"]
STATUS_ALIASES = {
    "fixed": "Fixed",
    "not fixed": "Not fixed",
    "blocked": "Blocked",
}

OUTPUT_GUARD = (
    "For /control-cc-worker or /repair-worker tasks, the final answer must start with "
    "## Result and use exactly these H2 headings in order: ## Result, ## Files Changed, "
    "## What Changed, ## Verification, ## Risk / Uncertainty, ## Needs Codex Review. "
    "No preamble, tables, alternate headings, or wrapper title. The body under ## Result "
    "must be exactly one of Fixed, Not fixed, or Blocked with no punctuation."
)


def run_text(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def git_status() -> str:
    return run_text(["git", "status", "--short", "--branch"])


def git_porcelain() -> str:
    return run_text(["git", "status", "--porcelain=v1", "--untracked-files=all"])


def git_diff_stat() -> str:
    return run_text(["git", "diff", "--stat"])


def git_diff_patch() -> str:
    return run_text(["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--"])


def git_changed_files() -> list[str]:
    output = run_text(["git", "status", "--porcelain=v1", "--untracked-files=all"])
    files: list[str] = []
    for line in output.splitlines():
        if len(line) >= 4:
            files.append(line[3:])
    return files


def split_scope(values: list[str]) -> list[str]:
    scope: list[str] = []
    for value in values:
        for item in value.replace(",", "\n").splitlines():
            item = item.strip().lstrip("-*").strip().rstrip("/")
            if item:
                scope.append(item)
    return scope


def path_allowed(path: str, scope: list[str]) -> bool:
    return any(path == item or path.startswith(f"{item}/") for item in scope)


def paths_outside_scope(paths: list[str], scope: list[str]) -> list[str]:
    if not scope:
        return []
    return [path for path in paths if not path_allowed(path, scope)]


def load_json_file(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def task_plan_path_is_safe(path: Path | None) -> bool:
    if path is None:
        return True
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    try:
        relative = resolved.relative_to(cwd)
    except ValueError:
        return True
    return str(relative).startswith(".codex/tmp/")


def restore_worktree() -> None:
    subprocess.run(["git", "restore", "--staged", "--worktree", "--", "."], check=True)
    subprocess.run(["git", "clean", "-fd"], check=True, capture_output=True, text=True)


def without_fenced_code(markdown: str) -> str:
    return re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)


def result_headings(result: str) -> list[str]:
    return re.findall(r"^## .+$", without_fenced_code(result), flags=re.MULTILINE)


def canonical_result_status(raw: str) -> str | None:
    status = raw.strip()
    status = re.sub(r"^\s*[-*]\s+", "", status)
    status = status.strip("`'\" ").rstrip(".").strip()
    status = re.sub(r"[_-]+", " ", status)
    status = re.sub(r"\s+", " ", status).casefold()
    return STATUS_ALIASES.get(status)


def result_status_found(result: str) -> str | None:
    match = re.search(r"^## Result\s*\n\s*([^\n]+)", without_fenced_code(result), flags=re.MULTILINE)
    if not match:
        return None
    return canonical_result_status(match.group(1)) or match.group(1).strip()


def normalize_result_status(result: str) -> tuple[str, bool]:
    pattern = re.compile(r"(^## Result\s*\n)([^\n]+)(\n\s*\n## Files Changed)", flags=re.MULTILINE)
    match = pattern.search(result)
    if not match:
        return result, False
    canonical = canonical_result_status(match.group(2))
    if canonical is None or canonical == match.group(2).strip():
        return result, False
    normalized = pattern.sub(rf"\1{canonical}\3", result, count=1)
    return normalized, True


def normalize_worker_result(payload: dict[str, object]) -> bool:
    result = payload.get("result")
    if not isinstance(result, str):
        return False
    stripped = result.strip()
    marker = stripped.find("## Result")
    if marker < 0:
        return False
    changed = False
    normalized = stripped
    if marker > 0:
        normalized = stripped[marker:]
        changed = True
    normalized, status_changed = normalize_result_status(normalized)
    payload["result"] = normalized
    return changed or status_changed


def validation_detail(reason: str | None, payload: dict[str, object]) -> str | None:
    if reason is None:
        return None
    result = payload.get("result")
    if not isinstance(result, str):
        return f"{reason}: worker JSON did not include a string result"
    if reason == "empty_result_after_edit":
        return (
            "empty_result_after_edit: Claude CLI completed successfully and changed files, "
            "but returned an empty result; inspect the saved partial diff before deciding whether to retry."
        )
    if reason == "empty_result":
        return "empty_result: Claude CLI completed successfully but returned an empty result"
    if reason == "missing_report_sections":
        return f"{reason}: found headings {result_headings(result)!r}"
    if reason == "invalid_result_status":
        return (
            f"{reason}: found {result_status_found(result)!r}; expected one of "
            f"{', '.join(EXPECTED_RESULT_STATUSES)}"
        )
    return reason


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


def truncate(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def write_temp_artifact(prefix: str, suffix: str, text: str) -> str | None:
    if not text:
        return None
    artifact = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix=prefix, suffix=suffix)
    with artifact:
        artifact.write(text)
    return artifact.name


def save_partial_patch() -> str | None:
    patch = git_diff_patch()
    return write_temp_artifact("control-cc-partial-", ".patch", patch)


def make_temp_path(prefix: str, suffix: str) -> Path:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return Path(path)


def failure_optimization(reason: object) -> str:
    if reason == "empty_result_after_edit":
        return "Treat the worker round as failed, review the saved partial patch, then either send one output-contract correction payload or have Codex take over."
    if reason == "empty_result":
        return "Run the control-cc CLI flow smoke test before dispatching more workers."
    if reason == "invalid_result_status":
        return "Tighten the final_output_contract and keep runner status normalization enabled."
    if reason == "missing_report_sections":
        return "Repeat the six-heading output contract at the end of the payload."
    if reason == "invalid_json":
        return "Keep Claude CLI on JSON output and inspect the saved stdout/stderr artifacts."
    if reason == "timeout":
        return "Reduce task scope or give the worker a narrower verification command."
    if reason == "permission_denied":
        return "Check requested tools and avoid paths outside the task worktree."
    if reason == "dirty_baseline":
        return "Start the worker only from a clean task worktree."
    if reason == "unsafe_task_plan_path":
        return "Store task plans under /tmp/control-cc-tasks or ignored .codex/tmp/tasks paths."
    if reason == "missing_write_scope":
        return "Give every atomic writing packet an explicit write scope."
    if reason == "packet_score_blocked":
        return "Split the packet until score is 0-4, or dispatch a read-only probe instead."
    if reason == "read_only_tools_violation":
        return "Run read-only probes with Read,Bash tools only."
    if reason == "read_only_probe_modified_files":
        return "Treat the probe as failed; narrow the prompt and keep probes read-only."
    if reason == "write_scope_violation":
        return "Reject or correct the worker diff; it changed files outside the packet write scope."
    return "Review the saved full report and send one correction payload only if the diff is close."


def verification_summary(worker_result: object) -> str | None:
    if not isinstance(worker_result, dict):
        return None
    result = worker_result.get("result")
    if not isinstance(result, str):
        return None
    match = re.search(
        r"^## Verification\s*\n(?P<body>.*?)(?=^## Risk / Uncertainty|\Z)",
        result,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        return None
    body = " ".join(line.strip() for line in match.group("body").splitlines() if line.strip())
    return body or None


def build_worker_call_report(report: dict[str, object]) -> dict[str, object]:
    succeeded = report.get("worker_status") == "succeeded"
    if not succeeded:
        reason = report.get("failure_reason") or "unknown"
        summary = f"Claude Code failed: {reason}."
        if reason == "empty_result_after_edit":
            changed_files = report.get("partial_diff_files")
            if isinstance(changed_files, list) and changed_files:
                summary = (
                    "Claude Code failed: empty_result_after_edit; "
                    f"partial diff touched {', '.join(str(item) for item in changed_files)}."
                )
        return {
            "success": False,
            "status": "failed",
            "failure_reason": reason,
            "summary": summary,
            "quality_score": None,
            "quality_reason": None,
            "optimization": failure_optimization(reason),
        }

    score = 8
    reasons = ["structured result accepted", "Codex still needs final diff review"]
    if report.get("normalized_worker_result"):
        score -= 1
        reasons.append("minor output-format drift was normalized")
    if not verification_summary(report.get("worker_result")):
        score -= 1
        reasons.append("verification summary was missing or empty")
    changed_files = report.get("partial_diff_files")
    if isinstance(changed_files, list) and len(changed_files) > 8:
        score -= 1
        reasons.append("worker changed more than 8 files")
    score = max(1, min(10, score))
    return {
        "success": True,
        "status": "succeeded",
        "failure_reason": None,
        "summary": "Claude Code succeeded; Codex should review the diff and verification evidence.",
        "quality_score": score,
        "quality_reason": "; ".join(reasons),
        "optimization": None if score >= 7 else "Send one correction payload or have Codex take over.",
    }


def compact_report(report: dict[str, object]) -> dict[str, object]:
    keys = [
        "worker_status",
        "failure_reason",
        "duration_sec",
        "model",
        "effort",
        "tools",
        "partial_diff_stat",
        "partial_diff_files",
        "restored_partial_diff",
        "saved_partial_patch",
        "stdout_path",
        "stderr_path",
        "full_report_path",
        "total_cost_usd",
        "normalized_worker_result",
        "headings_found",
        "result_status_found",
        "validation_failure_detail",
        "worker_call_report",
        "exit_code",
        "timed_out",
        "result_empty",
        "task_plan_file",
        "packet_score",
        "packet_decision",
        "packet_type",
        "write_scope",
        "write_scope_matched",
        "worker_workspace",
    ]
    return {key: report.get(key) for key in keys if key in report}


def emit(
    report: dict[str, object],
    exit_code: int,
    *,
    compact_output: bool = False,
    report_file: Path | None = None,
) -> int:
    if report_file is None and compact_output:
        report_file = make_temp_path("control-cc-report-", ".json")
    if report_file is not None:
        report["full_report_path"] = str(report_file)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    output = compact_report(report) if compact_output else report
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
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
        "saved_partial_patch": None,
        "stdout": "",
        "stdout_path": None,
        "stderr": "",
        "stderr_path": None,
        "worker_result": None,
        "total_cost_usd": None,
        "modelUsage": None,
        "permission_denials": None,
        "result_empty": False,
        "normalized_worker_result": False,
        "headings_found": [],
        "result_status_found": None,
        "expected_result_status": EXPECTED_RESULT_STATUSES,
        "validation_failure_detail": None,
        "task_plan_file": str(args.task_plan_file) if args.task_plan_file else None,
        "packet_score_file": str(args.packet_score_file) if args.packet_score_file else None,
        "packet_score": None,
        "packet_decision": None,
        "packet_type": args.packet_type,
        "write_scope": split_scope(args.write_scope),
        "write_scope_matched": None,
        "worker_workspace": str(Path.cwd()),
        "worker_call_report": None,
        "codex_action_required": (
            "Use worker_call_report for the short user-facing Claude Code report. Review the diff "
            "and verification evidence before accepting successful worker output."
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
    parser.add_argument("--task-plan-file", type=Path, default=None)
    parser.add_argument("--packet-score-file", type=Path, default=None)
    parser.add_argument("--packet-type", choices=["atomic writing packet", "read-only probe"], default=None)
    parser.add_argument("--write-scope", action="append", default=[])
    parser.add_argument("--compact-output", action="store_true", help="Print only the Codex review summary")
    parser.add_argument("--report-file", type=Path, default=None, help="Optional path for the full JSON report")
    parser.add_argument("--inline-log-chars", type=int, default=2000)
    args = parser.parse_args()

    payload = args.payload_file.read_text(encoding="utf-8")
    before_status = git_status()
    before_porcelain = git_porcelain()
    report = base_report(args, before_status)
    packet_score = load_json_file(args.packet_score_file)
    if packet_score is not None:
        report["packet_score"] = packet_score.get("score")
        report["packet_decision"] = packet_score.get("decision")

    if not task_plan_path_is_safe(args.task_plan_file):
        report["failure_reason"] = "unsafe_task_plan_path"
        report["after_status"] = before_status
        report["worker_call_report"] = build_worker_call_report(report)
        return emit(report, 2, compact_output=args.compact_output, report_file=args.report_file)

    if args.packet_type == "atomic writing packet" and not report["write_scope"]:
        report["failure_reason"] = "missing_write_scope"
        report["after_status"] = before_status
        report["worker_call_report"] = build_worker_call_report(report)
        return emit(report, 2, compact_output=args.compact_output, report_file=args.report_file)

    if args.packet_type == "atomic writing packet" and isinstance(report["packet_score"], int) and report["packet_score"] > 4:
        report["failure_reason"] = "packet_score_blocked"
        report["after_status"] = before_status
        report["worker_call_report"] = build_worker_call_report(report)
        return emit(report, 2, compact_output=args.compact_output, report_file=args.report_file)

    if args.packet_type == "read-only probe" and re.search(r"\b(Edit|Write)\b", args.tools):
        report["failure_reason"] = "read_only_tools_violation"
        report["after_status"] = before_status
        report["worker_call_report"] = build_worker_call_report(report)
        return emit(report, 2, compact_output=args.compact_output, report_file=args.report_file)

    if before_porcelain and not args.allow_dirty_baseline:
        report["failure_reason"] = "dirty_baseline"
        report["after_status"] = before_status
        report["partial_diff_files"] = git_changed_files()
        report["worker_call_report"] = build_worker_call_report(report)
        return emit(report, 2, compact_output=args.compact_output, report_file=args.report_file)

    command = build_command(args, payload)
    exit_code, stdout, stderr, timed_out, duration = run_worker(command, args.timeout_sec)
    report.update(
        {
            "timed_out": timed_out,
            "exit_code": exit_code,
            "duration_sec": round(duration, 3),
            "stdout": truncate(stdout, args.inline_log_chars),
            "stdout_path": write_temp_artifact("control-cc-stdout-", ".log", stdout),
            "stderr": truncate(stderr, args.inline_log_chars),
            "stderr_path": write_temp_artifact("control-cc-stderr-", ".log", stderr),
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
        report["normalized_worker_result"] = normalize_worker_result(parsed)
        report["worker_result"] = parsed
        report["total_cost_usd"] = parsed.get("total_cost_usd")
        report["modelUsage"] = parsed.get("modelUsage")
        report["permission_denials"] = parsed.get("permission_denials")
        failure_reason = validate_worker_json(parsed)
        result = parsed.get("result")
        if isinstance(result, str):
            report["result_empty"] = not bool(result.strip())
            report["headings_found"] = result_headings(result)
            report["result_status_found"] = result_status_found(result)
            if failure_reason == "missing_result" and report["result_empty"]:
                failure_reason = (
                    "empty_result_after_edit"
                    if report["partial_diff_files"]
                    else "empty_result"
                )
        report["validation_failure_detail"] = validation_detail(failure_reason, parsed)

    changed_files = report.get("partial_diff_files")
    changed_file_list = changed_files if isinstance(changed_files, list) else []
    scope = split_scope(args.write_scope)
    out_of_scope = paths_outside_scope([str(item) for item in changed_file_list], scope)
    if scope:
        report["write_scope_matched"] = not out_of_scope
    if failure_reason is None and args.packet_type == "read-only probe" and changed_file_list:
        failure_reason = "read_only_probe_modified_files"
        report["validation_failure_detail"] = "read_only_probe_modified_files: read-only probes must not modify files"
    if failure_reason is None and out_of_scope:
        failure_reason = "write_scope_violation"
        report["validation_failure_detail"] = (
            "write_scope_violation: changed files outside write scope: "
            + ", ".join(str(item) for item in out_of_scope)
        )

    if failure_reason is None:
        report["worker_status"] = "succeeded"
        report["failure_reason"] = None
        report["after_status"] = git_status()
        report["worker_call_report"] = build_worker_call_report(report)
        return emit(report, 0, compact_output=args.compact_output, report_file=args.report_file)

    report["failure_reason"] = failure_reason
    report["saved_partial_patch"] = save_partial_patch()
    if args.restore_on_failure:
        restore_worktree()
        report["restored_partial_diff"] = True
    report["after_status"] = git_status()
    report["worker_call_report"] = build_worker_call_report(report)
    return emit(report, 2, compact_output=args.compact_output, report_file=args.report_file)


if __name__ == "__main__":
    raise SystemExit(main())
