"""Bounded reverse checks for p2-192; restore task source in finally.

Run from the task worktree with its test interpreter, with no other test process
using these files. External APIs remain mocked by the selected tests. Each
mutation must produce assertion failures, never collection/harness errors.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
TEST = "backend/tests/test_investigation_attachment_followup_repair.py"
MUTATIONS = [
    ("default_ua", "backend/services/investigation_attachments.py",
     'ATTACHMENT_USER_AGENT = "SupportPortal-AttachmentSync/1.0"',
     'ATTACHMENT_USER_AGENT = "Python-urllib/3.13"', "authenticated_comment_redirect", 1),
    ("old_recovery_direction", "backend/services/automation_native_notifications.py",
     'fixed = binding.get("case_task")', 'fixed = None',
     "drain_fixed and valid and not invalid and not postgres", 1),
    ("original_customer_display", "backend/services/automation_native_notifications.py",
     "display = _to_english_display(payload['body'], failure_text=TRANSLATION_UNAVAILABLE, preserve_layout=True)",
     "display = payload['body']", "processor and False and not postgres", 1),
]


def main():
    for name, file, before, after, selection, expected in MUTATIONS:
        path = ROOT / file
        original = path.read_text()
        if original.count(before) != 1:
            raise RuntimeError(f"mutation anchor invalid: {name}")
        try:
            path.write_text(original.replace(before, after))
            with tempfile.TemporaryDirectory(prefix="p2192-reverse-") as directory:
                report = Path(directory) / "junit.xml"
                result = subprocess.run([sys.executable, "-m", "pytest", "-q", TEST,
                    "-k", selection, "--tb=short", f"--junitxml={report}"], cwd=ROOT,
                    capture_output=True, text=True)
                if not report.exists():
                    raise RuntimeError(f"test carrier failed: {name}")
                cases = ET.parse(report).findall(".//testcase")
                failures = [c for c in cases if c.find("failure") is not None]
                errors = [c for c in cases if c.find("error") is not None]
                if result.returncode != 1 or len(failures) != expected or errors:
                    print(result.stdout)
                    print(result.stderr)
                    raise RuntimeError(f"reverse check did not detect intended defect: {name}")
                for case in failures:
                    message = case.find("failure").get("message", "")
                    if "assert" not in message.lower():
                        raise RuntimeError(f"non-assertion failure: {name}")
                print(f"{name}: detected {len(failures)} assertion failure(s), exit=1")
        finally:
            path.write_text(original)


if __name__ == "__main__":
    main()
