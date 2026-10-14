"""Prove the real renderer/CLI regression detects the original guard bug.

Run alone in the task worktree; the source is restored in finally.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def main():
    path = ROOT / "backend/scripts/automation_ecs_deploy.py"
    original = path.read_text()
    line = '        result.setdefault(("environment", "INVESTIGATION_CASE_FILES_ENABLED"), "0")\n'
    assert original.count(line) == 1, "invalid mutation anchor"
    try:
        path.write_text(original.replace(line, ""))
        with tempfile.TemporaryDirectory(prefix="case-files-guard-reverse-") as directory:
            report = Path(directory) / "junit.xml"
            result = subprocess.run([
                sys.executable, "-m", "pytest", "-q",
                "backend/tests/test_case_files_closed_release_guard.py",
                "-k", "real_renderer_explicit_disabled", "--tb=short",
                f"--junitxml={report}",
            ], cwd=ROOT, capture_output=True, text=True)
            assert report.exists(), "test carrier failed"
            cases = ET.parse(report).findall(".//testcase")
            assert len(cases) == 2 and result.returncode == 1, result.stdout + result.stderr
            for case in cases:
                assert case.find("error") is None, "collection/harness error"
                failure = case.find("failure")
                assert failure is not None
                assert "Knowledge release would change shared" in failure.text
                assert "INVESTIGATION_CASE_FILES_ENABLED" in failure.text
            print("Original guard detected: API/Worker renderer-to-CLI checks both failed at the intended gate")
    finally:
        path.write_text(original)


if __name__ == "__main__":
    main()
