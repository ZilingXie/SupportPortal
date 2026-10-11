#!/usr/bin/env python3
"""Prove the entry-point regressions reject the pre-repair shell, without editing it."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BASELINE = "bd2ce145a16672ecb38f0892598ffa4681ad61d5"
TESTS = [
    "backend/tests/test_automation_ecs_cloudwatch_gate.py::"
    "test_old_revision_and_same_revision_probe_are_excluded_with_evidence",
    "backend/tests/test_automation_ecs_cloudwatch_gate.py::"
    "test_failed_release_task_without_logs_blocks[TaskFailedToStart-None-release_task_failed]",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE)
    args = parser.parse_args()
    original = subprocess.run(
        ["git", "show", f"{args.baseline}:deployment/deploy_automation_ecs_release.sh"],
        cwd=ROOT, check=True, text=True, capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="ecs-cloudwatch-reverse-") as temp:
        old_script = Path(temp) / "deploy.sh"
        old_script.write_text(original)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *TESTS], cwd=ROOT,
            env={**os.environ, "CLOUDWATCH_GATE_TEST_SCRIPT": str(old_script)},
            text=True, capture_output=True,
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        # Assertion failures, not missing pytest, collection errors, or AWS fixture errors.
        if result.returncode != 1 or "2 failed" not in result.stdout:
            raise RuntimeError("reverse validation did not produce both behavioral failures")
        if result.stdout.count("assert result.returncode") != 2 or "ERROR at" in result.stdout:
            raise RuntimeError("reverse validation failed outside the gate assertions")
    print("PASS: old gate rejects clean release with old errors and accepts failed startup; both caught")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
