#!/usr/bin/env python3
"""Legacy v2 review gate for strict control-cc worker diffs."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def run_text(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def git_changed_files() -> list[str]:
    output = run_text(["git", "status", "--porcelain=v1", "--untracked-files=all"])
    files: list[str] = []
    for line in output.splitlines():
        if len(line) >= 4:
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path)
    return files


def git_diff_text() -> str:
    return run_text(["git", "diff", "--", "."])


def split_scope(values: list[str]) -> list[str]:
    scope: list[str] = []
    for value in values:
        for item in value.replace(",", "\n").splitlines():
            item = item.strip().lstrip("-*").strip()
            if item:
                scope.append(item.rstrip("/"))
    return scope


def allowed(path: str, scope: list[str]) -> bool:
    if not scope:
        return False
    for item in scope:
        if path == item or path.startswith(f"{item}/"):
            return True
    return False


def is_doc_path(path: str) -> bool:
    return (
        path.startswith(("docs/", ".codex/skills/", ".claude/skills/"))
        or path in {"AGENTS.md", "CLAUDE.md"}
        or path.endswith((".md", ".rst", ".txt"))
    )


def has_worker_verification(report_file: Path | None) -> bool:
    if report_file is None:
        return False
    payload = json.loads(report_file.read_text(encoding="utf-8"))
    worker_result = payload.get("worker_result")
    if not isinstance(worker_result, dict):
        return False
    result = worker_result.get("result")
    if not isinstance(result, str):
        return False
    match = re.search(
        r"^## Verification\s*\n(?P<body>.*?)(?=^## Risk / Uncertainty|\Z)",
        result,
        flags=re.MULTILINE | re.DOTALL,
    )
    return bool(match and match.group("body").strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-scope", action="append", default=[])
    parser.add_argument("--report-file", type=Path, default=None)
    parser.add_argument("--forbid-docs", action="store_true")
    parser.add_argument("--require-verification", action="store_true")
    parser.add_argument("--fail-on-reject", action="store_true")
    args = parser.parse_args()

    changed_files = git_changed_files()
    scope = split_scope(args.write_scope)
    reasons: list[str] = []

    if not scope:
        reasons.append("missing_write_scope")
    out_of_scope = [path for path in changed_files if not allowed(path, scope)]
    if out_of_scope:
        reasons.append("changed_file_out_of_scope")
    doc_changes = [path for path in changed_files if is_doc_path(path)]
    if args.forbid_docs and doc_changes:
        reasons.append("docs_or_skill_change_in_worker_diff")
    diff = git_diff_text()
    if re.search(r"\b(TODO|FIXME|print\(|console\.log|debugger)\b", diff):
        reasons.append("debug_or_todo_in_diff")
    if args.require_verification and not has_worker_verification(args.report_file):
        reasons.append("missing_worker_verification")

    result = {
        "accepted": not reasons,
        "reasons": reasons,
        "changed_files": changed_files,
        "write_scope": scope,
        "out_of_scope_files": out_of_scope,
        "doc_changes": doc_changes,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if args.fail_on_reject and reasons else 0


if __name__ == "__main__":
    raise SystemExit(main())
