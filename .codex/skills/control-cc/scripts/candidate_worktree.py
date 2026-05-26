#!/usr/bin/env python3
"""Create, export, and clean control-cc detached candidate worktrees."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def run_text(command: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True).stdout


def git_changed_files(worktree: Path) -> list[str]:
    output = run_text(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=worktree)
    files: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return sorted(files)


def git_untracked_files(worktree: Path) -> list[str]:
    output = run_text(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=worktree)
    return [line[3:] for line in output.splitlines() if line.startswith("?? ") and len(line) >= 4]


def export_patch(worktree: Path) -> str:
    untracked = git_untracked_files(worktree)
    if untracked:
        subprocess.run(["git", "add", "-N", "--", *untracked], cwd=worktree, check=True)
    try:
        return run_text(["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--"], cwd=worktree)
    finally:
        if untracked:
            subprocess.run(["git", "reset", "-q", "--", *untracked], cwd=worktree, check=True)


def command_create(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    worktree = run_dir / "worktree"
    run_dir.mkdir(parents=True, exist_ok=False)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), args.base_ref],
        check=True,
        capture_output=True,
        text=True,
    )
    head = run_text(["git", "rev-parse", "HEAD"], cwd=worktree).strip()
    branch = run_text(["git", "branch", "--show-current"], cwd=worktree).strip()
    result = {
        "run_dir": str(run_dir),
        "worktree_path": str(worktree),
        "base_ref": args.base_ref,
        "head": head,
        "detached": branch == "",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_export_patch(args: argparse.Namespace) -> int:
    worktree = args.worktree.resolve()
    patch_file = args.patch_file.resolve()
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    changed_files = git_changed_files(worktree)
    patch = export_patch(worktree)
    patch_file.write_text(patch, encoding="utf-8")
    result = {
        "worktree_path": str(worktree),
        "patch_file": str(patch_file),
        "changed_files": changed_files,
        "has_patch": bool(patch.strip()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_cleanup(args: argparse.Namespace) -> int:
    worktree = args.worktree.resolve()
    existed = worktree.exists()
    if existed:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], check=True)
    result = {
        "worktree_path": str(worktree),
        "removed": existed and not worktree.exists(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a detached candidate worktree")
    create.add_argument("--run-dir", type=Path, required=True)
    create.add_argument("--base-ref", default="HEAD")
    create.set_defaults(func=command_create)

    export = subparsers.add_parser("export-patch", help="Export a binary patch from a candidate worktree")
    export.add_argument("--worktree", type=Path, required=True)
    export.add_argument("--patch-file", type=Path, required=True)
    export.set_defaults(func=command_export_patch)

    cleanup = subparsers.add_parser("cleanup", help="Remove a candidate worktree")
    cleanup.add_argument("--worktree", type=Path, required=True)
    cleanup.set_defaults(func=command_cleanup)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
