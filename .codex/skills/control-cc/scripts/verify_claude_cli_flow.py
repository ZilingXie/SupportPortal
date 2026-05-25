#!/usr/bin/env python3
"""Verify the project-local control-cc Claude CLI handoff."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
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
    "tables, alternate headings, or wrapper title. The body under ## Result must be exactly "
    "one of Fixed, Not fixed, or Blocked with no punctuation."
)


def run_text(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


def git_status() -> str:
    return run_text(["git", "status", "--short", "--branch"])


def without_fenced_code(markdown: str) -> str:
    return re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)


def build_payload() -> str:
    return """/repair-worker

mode:
correction

problem:
Verify that the project-local control-cc flow can call Claude Code CLI non-interactively and receive the strict worker result format.

must_keep:
- Use the project-local repair-worker skill.
- Do not modify any files.
- Keep this as a read-only verification probe.

must_change:
Run the verification command and return the result using exactly the six required H2 headings in order.

verification:
git status --short --branch

acceptance:
- The final answer starts with ## Result.
- The final answer has exactly the six required H2 headings in order.
- Result body is exactly Fixed, Not fixed, or Blocked.
- No files are modified by this verification round.

final_output_contract:
- Final answer starts exactly with `## Result`.
- Under `## Result`, write exactly one of `Fixed`, `Not fixed`, or `Blocked`.
- Do not write `Fixed.`, `Success`, `Implemented`, bullets, or code formatting on the result line.
- Use exactly these six H2 headings in order: `## Result`, `## Files Changed`, `## What Changed`, `## Verification`, `## Risk / Uncertainty`, `## Needs Codex Review`.
"""


def validate_worker_result(payload: dict, before_status: str, after_status: str, max_budget: float | None) -> None:
    if payload.get("is_error"):
        raise AssertionError(f"Claude CLI returned is_error=true: {payload}")
    if payload.get("permission_denials"):
        raise AssertionError(f"Claude CLI reported permission denials: {payload['permission_denials']}")
    cost = float(payload.get("total_cost_usd") or 0)
    if max_budget is not None and cost > max_budget:
        raise AssertionError(f"Claude CLI cost {cost:.6f} exceeded budget {max_budget:.6f}")
    if before_status != after_status:
        raise AssertionError(
            "Claude CLI verification modified git status.\n"
            f"Before:\n{before_status}\nAfter:\n{after_status}"
        )

    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        raise AssertionError("Claude CLI JSON result did not include non-empty result text")
    result = result.strip()
    if not result.startswith("## Result"):
        raise AssertionError(f"Worker result does not start with ## Result:\n{result}")

    headings = re.findall(r"^## .+$", without_fenced_code(result), flags=re.MULTILINE)
    if headings != EXPECTED_HEADINGS:
        raise AssertionError(f"Unexpected worker headings: {headings}")

    first_section = result.split("\n", 2)
    if len(first_section) < 2:
        raise AssertionError("Worker result missing Result body")
    result_body_match = re.search(r"^## Result\s*\n\s*(Fixed|Not fixed|Blocked)\s*$", result, flags=re.MULTILINE)
    if not result_body_match:
        raise AssertionError("Worker Result body is not exactly Fixed, Not fixed, or Blocked")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="opus")
    parser.add_argument("--effort", default="max", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-budget-usd", type=float, default=None, help="Optional smoke-test safety cap")
    parser.add_argument("--timeout-sec", type=float, default=1200)
    args = parser.parse_args()

    if shutil.which("claude") is None:
        print("claude CLI not found on PATH", file=sys.stderr)
        return 1

    runner = Path(__file__).with_name("run_repair_worker.py")
    if not runner.exists():
        print(f"runner not found: {runner}", file=sys.stderr)
        return 1

    payload_file = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".md")
    try:
        payload_file.write(build_payload())
        payload_file.close()
        before_status = git_status()
        command = [
            sys.executable,
            str(runner),
            "--payload-file",
            payload_file.name,
            "--timeout-sec",
            str(args.timeout_sec),
            "--tools",
            "Read,Bash",
            "--restore-on-failure",
            "--model",
            args.model,
            "--effort",
            args.effort,
        ]
        if args.max_budget_usd is not None:
            command.extend(["--max-budget-usd", str(args.max_budget_usd)])
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise AssertionError(
                "Runner reported worker failure.\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"Runner did not return JSON: {completed.stdout}") from exc
    finally:
        Path(payload_file.name).unlink(missing_ok=True)

    if response.get("worker_status") != "succeeded":
        raise AssertionError(f"Runner reported worker failure: {response}")
    worker_result = response.get("worker_result")
    if not isinstance(worker_result, dict):
        raise AssertionError(f"Runner did not include worker_result: {response}")

    command = [
        "git",
        "status",
        "--short",
        "--branch",
    ]
    after_status = run_text(command)
    validate_worker_result(worker_result, before_status, after_status, args.max_budget_usd)
    print(
        "Claude CLI flow verified: "
        f"model={args.model}, effort={args.effort}, "
        f"cost=${float(response.get('total_cost_usd') or 0):.6f}, "
        f"session={worker_result.get('session_id', '')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
