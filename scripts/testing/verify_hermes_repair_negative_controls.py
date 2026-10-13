"""Run repair mutations in disposable source copies; never edit the task tree."""
from __future__ import annotations
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


CONTROLS = [
    ("omit_plain_text_delivery_watermark", "backend/worker.py",
     [('not_before=delivery["created_at"],', 'not_before=None,')],
     "backend/tests/test_worker_compatibility_scope.py::WorkerReadbackTests::test_real_audit_watermark_gates_plain_text_release_and_replay"),
    ("check_audit_time_only_with_attachments", "backend/services/zendesk_comments.py",
     [("if not_before is not None or attachments:", "if attachments:")],
     "backend/tests/test_worker_compatibility_scope.py::WorkerReadbackTests::test_real_audit_watermark_gates_plain_text_release_and_replay"),
    ("omit_delivery_scope_flag", "backend/services/automation_hermes_delivery.py",
     [("if relay_required_flag is not None:", "if False:")],
     "backend/tests/test_hermes_native_enablement_regression.py"),
    ("infer_relay_from_enablement_route", "backend/worker.py",
     [('relay_required = bool(marker.get("_hermes_compatibility_relay_required"))', 'relay_required = True')],
     "backend/tests/test_hermes_native_enablement_regression.py"),
    ("release_without_confirmed_public_delivery", "backend/repositories/enablement_relay_repository.py",
     [("if not self._has_delivered_public_reply(", "if False and not self._has_delivered_public_reply(")],
     "backend/tests/test_worker_compatibility_scope.py::ReplayTests::test_relay_rejects_unconfirmed_or_mismatched_identity"),
    ("omit_real_request_fixture", "backend/tests/test_worker_compatibility_scope.py",
     [("    if auto:\n        request =", "    if False:\n        request =")],
     "backend/tests/test_worker_compatibility_scope.py::ReplayTests::test_relay_release_once_with_real_repository_state"),
    ("repeat_release_transition", "backend/repositories/enablement_relay_repository.py",
     [('updated["status"] = ENABLEMENT_RELAY_REQUEST_DISPATCH_PENDING', 'updated["status"] = ENABLEMENT_RELAY_REQUEST_GATED'),
      ('state="dispatch_pending",\n                    email_status="not_applicable",',
       'state="awaiting_public_reply",\n                    email_status="awaiting_public_reply",')],
     "backend/tests/test_worker_compatibility_scope.py::ReplayTests::test_relay_release_once_with_real_repository_state"),
    ("omit_gate_failure_phase", "backend/services/automation_hermes_agent.py",
     [('failed_phase="ownership_gate",', 'failed_phase=None,')],
     "backend/tests/test_ownership_gate_alert_context.py"),
    ("omit_gate_failure_reason", "backend/services/automation_hermes_agent.py",
     [('failure_reason=(\n                str(ownership_result.failure_detail', 'failure_reason=(\n                str(""')],
     "backend/tests/test_ownership_gate_alert_context.py"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--control", action="append", help="Run only the named controls")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    archive = subprocess.check_output(["git", "archive", "HEAD"], cwd=root)
    results = []
    for name, filename, substitutions, target in CONTROLS:
        if args.control and name not in args.control:
            continue
        with tempfile.TemporaryDirectory(prefix="hermes-repair-negative-") as temporary:
            copied = Path(temporary)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(copied, filter="data")
            carrier_files = {filename, target.split("::")[0]}
            if name in {"omit_plain_text_delivery_watermark", "check_audit_time_only_with_attachments"}:
                carrier_files.update({"backend/worker.py", "backend/services/zendesk_comments.py"})
            for rel in carrier_files:
                (copied / rel).write_bytes((root / rel).read_bytes())
            path = copied / filename
            text = path.read_text()
            for old, new in substitutions:
                if text.count(old) != 1:
                    raise RuntimeError(f"mutation must match once: {name}")
                text = text.replace(old, new)
            path.write_text(text)
            env = dict(os.environ, TICKET_DB_DSN="postgresql://example.invalid/test", SENTIMENT_PROVIDER="legacy")
            outcome = subprocess.run([sys.executable, "-m", "pytest", target, "-q", "--tb=short"],
                                     cwd=copied, env=env, capture_output=True, text=True)
            # Collection/import errors cannot prove behavior detection.
            detected = (outcome.returncode == 1
                        and ("FAILED " in outcome.stdout or "SUBFAILED(" in outcome.stdout)
                        and "ERROR collecting" not in outcome.stdout)
            results.append({"control": name, "detected": detected, "exit_code": outcome.returncode,
                            "test": target, "output": outcome.stdout + outcome.stderr})
            print(name, "detected" if detected else "NOT DETECTED", flush=True)
    Path(args.output).write_text(json.dumps({"source_head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(), "controls": results}, indent=2))
    return 0 if all(item["detected"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
