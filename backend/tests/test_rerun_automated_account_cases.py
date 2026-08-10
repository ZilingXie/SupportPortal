from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from scripts.ops import rerun_automated_account_cases as runner


def _personas(content_suffix: str = "baseline") -> dict[str, object]:
    personas = []
    for key, name in (
        ("sid-precise", "Sid Precise"),
        ("sid-bright", "Sid Bright"),
        ("default-support", "Sid Warm"),
    ):
        personas.append(
            {
                "persona_key": key,
                "display_name": name,
                "enabled": True,
                "published_version": 1,
                "versions": [
                    {
                        "version": 1,
                        "status": "published",
                        "content": {"instruction": f"{name} {content_suffix}"},
                    }
                ],
            }
        )
    return {"personas": personas}


def _case(case_id: str, *, action: str = "enablement", route_status: str = "automated") -> dict[str, object]:
    ticket_id = case_id.replace("AC-", "TK-")
    return {
        "account_case_id": case_id,
        "client_ticket_id": ticket_id,
        "route_status": route_status,
        "route_family": "automated" if route_status == "automated" else "human_review",
        "execution_action": action,
        "route_review_status": "pending",
    }


class FakeRequest:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict[str, object]] = []

    def __call__(self, method: str, path: str, *, headers=None, payload=None, timeout_seconds=15.0):
        call = {
            "method": method,
            "path": path,
            "headers": dict(headers or {}),
            "payload": payload,
            "timeout_seconds": timeout_seconds,
        }
        self.calls.append(call)
        return self.handler(call)


def _discovery_request(
    summaries: list[dict[str, object]],
    *,
    build_ref: str = "build-1",
    persona_suffix: str = "baseline",
    page_size: int = 2,
) -> FakeRequest:
    details = {str(item["account_case_id"]): {**item, "persona_assignment": None} for item in summaries}

    def handler(call: dict[str, object]) -> dict[str, object]:
        method = str(call["method"])
        parsed = urlsplit(str(call["path"]))
        if method == "GET" and parsed.path == "/health":
            return {"status": "ok", "app_build": {"ref": build_ref}}
        if method == "GET" and parsed.path == "/api/workspace/admin/account-personas":
            return _personas(persona_suffix)
        if method == "GET" and parsed.path == "/api/account/cases":
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            start = (page - 1) * page_size
            items = summaries[start : start + page_size]
            return {
                "cases": items,
                "page": page,
                "page_size": page_size,
                "total": len(summaries),
                "total_pages": max(1, (len(summaries) + page_size - 1) // page_size),
                "has_more": start + page_size < len(summaries),
            }
        if method == "GET" and parsed.path.startswith("/api/account/cases/"):
            case_id = unquote(parsed.path.rsplit("/", 1)[-1])
            detail = details.get(case_id)
            if detail is None:
                raise runner.HttpError(404, {"detail": "not found"})
            return dict(detail)
        raise AssertionError(f"unexpected request: {method} {call['path']}")

    return FakeRequest(handler)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += float(seconds)


class AutomatedAccountCaseRerunTests(unittest.TestCase):
    def _dry_run(self, root: Path, case_ids=("AC-1", "AC-2")) -> tuple[Path, FakeRequest]:
        request = _discovery_request([_case(case_id) for case_id in case_ids])
        operation_dir = runner.create_dry_run(
            base_url="http://127.0.0.1:8000",
            operations_root=root,
            request_json=request,
            access_token="secret-admin-token",
        )
        return operation_dir, request

    def test_dry_run_paginates_filters_deduplicates_and_writes_private_safe_files(self) -> None:
        summaries = [
            _case("AC-1"),
            _case("AC-HUMAN", route_status="not_automated"),
            _case("AC-1"),
            _case("AC-UNREGISTERED", action="unknown"),
            _case("AC-2", action="quota"),
        ]
        request = _discovery_request(summaries)
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir = runner.create_dry_run(
                base_url="http://localhost:8000",
                operations_root=Path(temporary),
                request_json=request,
                access_token="secret-admin-token",
            )

            baseline = json.loads((operation_dir / "baseline.json").read_text(encoding="utf-8"))
            progress = json.loads((operation_dir / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(baseline["frozen_case_ids"], ["AC-1", "AC-2"])
            self.assertEqual(list(progress["items"]), ["AC-1", "AC-2"])
            self.assertEqual(stat.S_IMODE(operation_dir.stat().st_mode), 0o700)
            for name in (
                "baseline.json",
                "progress.json",
                "report.json",
                "report.md",
                "manifest.json",
                "manifest.key",
            ):
                self.assertTrue((operation_dir / name).exists(), name)
                self.assertEqual(stat.S_IMODE((operation_dir / name).stat().st_mode), 0o600)
            persisted = "\n".join(path.read_text(encoding="utf-8") for path in operation_dir.iterdir() if path.is_file())
            self.assertNotIn("secret-admin-token", persisted)
            self.assertNotIn("customer_email", persisted)
            self.assertFalse(any(call["method"] == "POST" for call in request.calls))
            persona_call = next(
                call for call in request.calls if "/account-personas" in str(call["path"])
            )
            self.assertEqual(persona_call["headers"]["Authorization"], "Bearer secret-admin-token")

    def test_internal_email_reason_never_enters_dry_run_apply_or_resume_artifacts(self) -> None:
        sensitive_reason = (
            "customer=leak-target@example.com "
            "Authorization: Bearer p1-sensitive-token "
            "smtp_password=p1-top-secret"
        )
        sensitive_case = {
            **_case("AC-SENSITIVE"),
            "internal_email_send_reason": sensitive_reason,
        }
        forbidden_artifact_text = (
            "internal_email_send_reason",
            sensitive_reason,
            "leak-target@example.com",
            "Bearer p1-sensitive-token",
            "smtp_password=p1-top-secret",
        )

        def assert_artifacts_are_safe(operation_dir: Path) -> None:
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in operation_dir.rglob("*")
                if path.is_file()
            )
            for forbidden in forbidden_artifact_text:
                self.assertNotIn(forbidden, persisted)

        with tempfile.TemporaryDirectory() as temporary:
            operation_dir = runner.create_dry_run(
                base_url="http://127.0.0.1:8000",
                operations_root=Path(temporary),
                request_json=_discovery_request([sensitive_case]),
                access_token="runner-access-token",
            )
            assert_artifacts_are_safe(operation_dir)

            clock = Clock()

            def timeout_handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if parsed.path == "/api/account/cases/AC-SENSITIVE":
                    return dict(sensitive_case)
                if call["method"] == "POST":
                    return {"job_id": "job-sensitive", "status": "queued"}
                if parsed.path == "/api/account/rerun-jobs/job-sensitive":
                    return {"job_id": "job-sensitive", "status": "running"}
                raise AssertionError(call)

            timed_out = runner.apply_operation(
                operation_dir,
                request_json=FakeRequest(timeout_handler),
                sleep=clock.sleep,
                monotonic=clock.monotonic,
                poll_timeout_seconds=2,
                poll_interval_seconds=1,
            )
            self.assertEqual(timed_out["items"]["AC-SENSITIVE"]["status"], "resumable")
            assert_artifacts_are_safe(operation_dir)

            def resume_handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if parsed.path == "/api/account/rerun-jobs/job-sensitive":
                    return {"job_id": "job-sensitive", "status": "completed"}
                if parsed.path == "/api/account/cases/AC-SENSITIVE":
                    return dict(sensitive_case)
                raise AssertionError(call)

            resume = FakeRequest(resume_handler)
            completed = runner.apply_operation(
                operation_dir,
                request_json=resume,
                sleep=lambda _value: None,
            )
            self.assertEqual(completed["items"]["AC-SENSITIVE"]["status"], "completed")
            self.assertFalse(any(call["method"] == "POST" for call in resume.calls))
            assert_artifacts_are_safe(operation_dir)

    def test_apply_requires_an_existing_dry_run_and_loopback_url(self) -> None:
        with self.assertRaises(runner.OperationError):
            runner.validate_base_url("https://support.example.com")
        self.assertEqual(runner.validate_base_url("http://[::1]:8000"), "http://[::1]:8000")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(runner.OperationError):
                runner.apply_operation(Path(temporary), request_json=FakeRequest(lambda _call: {}))

    def test_apply_posts_only_single_cases_with_stable_keys_and_waits_sequentially(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary))
            terminal = {"job-1": "completed", "job-2": "completed"}

            def handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if call["method"] == "GET" and parsed.path.startswith("/api/account/cases/"):
                    return _case(unquote(parsed.path.rsplit("/", 1)[-1]))
                if call["method"] == "POST" and parsed.path.endswith("/rerun"):
                    case_id = unquote(parsed.path.split("/")[-2])
                    return {"job_id": "job-1" if case_id == "AC-1" else "job-2", "status": "queued"}
                if call["method"] == "GET" and parsed.path.startswith("/api/account/rerun-jobs/"):
                    job_id = parsed.path.rsplit("/", 1)[-1]
                    return {"job_id": job_id, "status": terminal[job_id], "emails_sent": 1}
                raise AssertionError(call)

            request = FakeRequest(handler)
            result = runner.apply_operation(operation_dir, request_json=request, sleep=lambda _value: None)

            posts = [call for call in request.calls if call["method"] == "POST"]
            self.assertEqual(
                [urlsplit(str(call["path"])).path for call in posts],
                ["/api/account/cases/AC-1/rerun", "/api/account/cases/AC-2/rerun"],
            )
            keys = [str(call["headers"]["Idempotency-Key"]) for call in posts]
            self.assertEqual(len(set(keys)), 2)
            self.assertEqual(keys[0], runner.stable_idempotency_key(result["operation_id"], "AC-1"))
            call_paths = [urlsplit(str(call["path"])).path for call in request.calls]
            self.assertLess(call_paths.index("/api/account/rerun-jobs/job-1"), call_paths.index("/api/account/cases/AC-2/rerun"))
            self.assertFalse(any(path == "/api/account/rerun-jobs" for path in call_paths))

    def test_three_retryable_start_failures_stop_and_preserve_remaining_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary))
            sensitive_error = "customer@example.com bearer-secret-from-server"

            def handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if call["method"] == "GET" and parsed.path == "/api/account/cases/AC-1":
                    return _case("AC-1")
                if call["method"] == "POST":
                    raise runner.HttpError(
                        503,
                        {"detail": {"retryable": True, "message": sensitive_error}},
                    )
                raise AssertionError(call)

            request = FakeRequest(handler)
            progress = runner.apply_operation(operation_dir, request_json=request, sleep=lambda _value: None)

            posts = [call for call in request.calls if call["method"] == "POST"]
            self.assertEqual(len(posts), 3)
            self.assertEqual(progress["stop_reason"], "three_consecutive_retryable_start_failures")
            self.assertEqual(progress["items"]["AC-1"]["status"], "resumable")
            self.assertEqual(progress["items"]["AC-2"]["status"], "pending")
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in operation_dir.iterdir()
                if path.is_file()
            )
            self.assertNotIn(sensitive_error, persisted)

    def test_start_409_stops_as_external_active_and_nonretryable_503_is_not_retried(self) -> None:
        scenarios = (
            (
                409,
                {"detail": "an Account rerun job is already running"},
                "resumable",
                "external_active_job",
            ),
            (
                503,
                {"detail": {"code": "unrelated_failure", "retryable": False}},
                "failed",
                None,
            ),
        )
        for status_code, payload, expected_item_status, expected_stop_reason in scenarios:
            with self.subTest(status_code=status_code), tempfile.TemporaryDirectory() as temporary:
                operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))

                def handler(call: dict[str, object]) -> dict[str, object]:
                    parsed = urlsplit(str(call["path"]))
                    if parsed.path == "/health":
                        return {"app_build": {"ref": "build-1"}}
                    if parsed.path == "/api/workspace/admin/account-personas":
                        return _personas()
                    if parsed.path == "/api/account/cases/AC-1":
                        return _case("AC-1")
                    if call["method"] == "POST":
                        raise runner.HttpError(status_code, payload)
                    raise AssertionError(call)

                request = FakeRequest(handler)
                progress = runner.apply_operation(
                    operation_dir,
                    request_json=request,
                    sleep=lambda _value: None,
                )

                posts = [call for call in request.calls if call["method"] == "POST"]
                self.assertEqual(len(posts), 1)
                self.assertEqual(progress["items"]["AC-1"]["status"], expected_item_status)
                self.assertEqual(progress["stop_reason"], expected_stop_reason)
                if status_code == 409:
                    self.assertEqual(
                        progress["items"]["AC-1"]["terminal_error"],
                        "external_active_job",
                    )

    def test_completed_with_errors_and_failed_are_terminal_without_extra_polling(self) -> None:
        for job_status, expected_item_status in (
            ("completed", "completed"),
            ("completed_with_errors", "failed"),
            ("failed", "failed"),
        ):
            with self.subTest(job_status=job_status), tempfile.TemporaryDirectory() as temporary:
                operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))

                def handler(call: dict[str, object]) -> dict[str, object]:
                    parsed = urlsplit(str(call["path"]))
                    if parsed.path == "/health":
                        return {"app_build": {"ref": "build-1"}}
                    if parsed.path == "/api/workspace/admin/account-personas":
                        return _personas()
                    if parsed.path == "/api/account/cases/AC-1":
                        return _case("AC-1")
                    if call["method"] == "POST":
                        return {"job_id": "job-terminal", "status": job_status}
                    raise AssertionError(call)

                request = FakeRequest(handler)
                progress = runner.apply_operation(
                    operation_dir,
                    request_json=request,
                    sleep=lambda _value: None,
                )

                self.assertEqual(progress["items"]["AC-1"]["status"], expected_item_status)
                self.assertFalse(
                    any("/api/account/rerun-jobs/" in str(call["path"]) for call in request.calls)
                )

    def test_poll_protocol_errors_stop_resumably_and_resume_never_reposts(self) -> None:
        invalid_statuses = (None, "unexpected-transient", 42)
        for invalid_status in invalid_statuses:
            with self.subTest(status=invalid_status), tempfile.TemporaryDirectory() as temporary:
                operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))

                def first_handler(call: dict[str, object]) -> dict[str, object]:
                    parsed = urlsplit(str(call["path"]))
                    if parsed.path == "/health":
                        return {"app_build": {"ref": "build-1"}}
                    if parsed.path == "/api/workspace/admin/account-personas":
                        return _personas()
                    if parsed.path == "/api/account/cases/AC-1":
                        return _case("AC-1")
                    if call["method"] == "POST":
                        return {"job_id": "job-protocol", "status": "queued"}
                    if parsed.path == "/api/account/rerun-jobs/job-protocol":
                        response: dict[str, object] = {"job_id": "job-protocol"}
                        if invalid_status is not None:
                            response["status"] = invalid_status
                        return response
                    raise AssertionError(call)

                first = FakeRequest(first_handler)
                stopped = runner.apply_operation(
                    operation_dir,
                    request_json=first,
                    sleep=lambda _value: None,
                )
                self.assertEqual(stopped["items"]["AC-1"]["status"], "resumable")
                self.assertEqual(stopped["items"]["AC-1"]["terminal_error"], "rerun_job_protocol_error")
                self.assertEqual(stopped["stop_reason"], "rerun_job_protocol_error")
                self.assertEqual(len([call for call in first.calls if call["method"] == "POST"]), 1)

                def resume_handler(call: dict[str, object]) -> dict[str, object]:
                    parsed = urlsplit(str(call["path"]))
                    if parsed.path == "/health":
                        return {"app_build": {"ref": "build-1"}}
                    if parsed.path == "/api/workspace/admin/account-personas":
                        return _personas()
                    if parsed.path == "/api/account/rerun-jobs/job-protocol":
                        return {"job_id": "job-protocol", "status": "completed"}
                    if parsed.path == "/api/account/cases/AC-1":
                        return _case("AC-1")
                    raise AssertionError(call)

                resume = FakeRequest(resume_handler)
                completed = runner.apply_operation(
                    operation_dir,
                    request_json=resume,
                    sleep=lambda _value: None,
                )
                self.assertEqual(completed["items"]["AC-1"]["status"], "completed")
                self.assertFalse(any(call["method"] == "POST" for call in resume.calls))

    def test_needs_recovery_stops_and_resume_only_polls_the_existing_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))

            def first_handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if parsed.path == "/api/account/cases/AC-1":
                    return _case("AC-1")
                if call["method"] == "POST":
                    return {"job_id": "job-recovery", "status": "running"}
                if parsed.path == "/api/account/rerun-jobs/job-recovery":
                    return {"job_id": "job-recovery", "status": "needs_recovery"}
                raise AssertionError(call)

            first = FakeRequest(first_handler)
            stopped = runner.apply_operation(
                operation_dir,
                request_json=first,
                sleep=lambda _value: None,
            )
            self.assertEqual(stopped["items"]["AC-1"]["status"], "resumable")
            self.assertEqual(stopped["stop_reason"], "rerun_job_needs_recovery")

            def resume_handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if parsed.path == "/api/account/rerun-jobs/job-recovery":
                    return {"job_id": "job-recovery", "status": "completed"}
                if parsed.path == "/api/account/cases/AC-1":
                    return _case("AC-1")
                raise AssertionError(call)

            resume = FakeRequest(resume_handler)
            completed = runner.apply_operation(
                operation_dir,
                request_json=resume,
                sleep=lambda _value: None,
            )
            self.assertEqual(completed["items"]["AC-1"]["status"], "completed")
            self.assertFalse(any(call["method"] == "POST" for call in resume.calls))

    def test_poll_timeout_stops_and_resume_polls_same_job_without_posting_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))
            clock = Clock()

            def first_handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if parsed.path == "/api/account/cases/AC-1":
                    return _case("AC-1")
                if call["method"] == "POST":
                    return {"job_id": "job-timeout", "status": "queued"}
                if parsed.path == "/api/account/rerun-jobs/job-timeout":
                    return {"job_id": "job-timeout", "status": "running"}
                raise AssertionError(call)

            first = FakeRequest(first_handler)
            timed_out = runner.apply_operation(
                operation_dir,
                request_json=first,
                sleep=clock.sleep,
                monotonic=clock.monotonic,
                poll_timeout_seconds=2,
                poll_interval_seconds=1,
            )
            self.assertEqual(timed_out["items"]["AC-1"]["job_id"], "job-timeout")
            self.assertEqual(timed_out["items"]["AC-1"]["status"], "resumable")

            def resume_handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if parsed.path == "/api/account/rerun-jobs/job-timeout":
                    return {"job_id": "job-timeout", "status": "completed"}
                if parsed.path == "/api/account/cases/AC-1":
                    return _case("AC-1")
                raise AssertionError(call)

            resume = FakeRequest(resume_handler)
            completed = runner.apply_operation(operation_dir, request_json=resume, sleep=lambda _value: None)
            self.assertEqual(completed["items"]["AC-1"]["status"], "completed")
            self.assertFalse(any(call["method"] == "POST" for call in resume.calls))

    def test_terminal_case_failure_continues_and_revalidation_skips_changed_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1", "AC-2", "AC-3"))
            sensitive_error = "customer reply body and internal bearer-secret"

            def handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if parsed.path == "/api/account/cases/AC-3":
                    return _case("AC-3", route_status="not_automated")
                if call["method"] == "GET" and parsed.path.startswith("/api/account/cases/"):
                    return _case(unquote(parsed.path.rsplit("/", 1)[-1]))
                if call["method"] == "POST":
                    case_id = unquote(parsed.path.split("/")[-2])
                    return {"job_id": f"job-{case_id[-1]}", "status": "queued"}
                if parsed.path == "/api/account/rerun-jobs/job-1":
                    return {
                        "job_id": "job-1",
                        "status": "failed",
                        "error": sensitive_error,
                        "failures": [{"error": sensitive_error}],
                    }
                if parsed.path == "/api/account/rerun-jobs/job-2":
                    return {"job_id": "job-2", "status": "completed"}
                raise AssertionError(call)

            request = FakeRequest(handler)
            progress = runner.apply_operation(operation_dir, request_json=request, sleep=lambda _value: None)
            self.assertEqual(progress["items"]["AC-1"]["status"], "failed")
            self.assertEqual(progress["items"]["AC-2"]["status"], "completed")
            self.assertEqual(progress["items"]["AC-3"]["status"], "skipped")
            posts = [str(call["path"]) for call in request.calls if call["method"] == "POST"]
            self.assertEqual(len(posts), 2)
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in operation_dir.iterdir()
                if path.is_file()
            )
            self.assertNotIn(sensitive_error, persisted)

    def test_build_or_persona_drift_blocks_apply_before_any_case_post(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))
            for build_ref, suffix in (("build-2", "baseline"), ("build-1", "changed")):
                def handler(call: dict[str, object], build_ref=build_ref, suffix=suffix):
                    parsed = urlsplit(str(call["path"]))
                    if parsed.path == "/health":
                        return {"app_build": {"ref": build_ref}}
                    if parsed.path == "/api/workspace/admin/account-personas":
                        return _personas(suffix)
                    raise AssertionError(call)

                request = FakeRequest(handler)
                with self.assertRaises(runner.OperationError):
                    runner.apply_operation(operation_dir, request_json=request)
                self.assertFalse(any(call["method"] == "POST" for call in request.calls))

    def test_exclusive_operation_lock_rejects_a_second_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))
            with runner.operation_lock(operation_dir):
                with self.assertRaises(runner.OperationError):
                    runner.apply_operation(operation_dir, request_json=FakeRequest(lambda _call: {}))

    def test_manifest_rejects_raw_baseline_or_progress_tampering(self) -> None:
        for filename in ("baseline.json", "progress.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))
                artifact_path = operation_dir / filename
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                if filename == "baseline.json":
                    artifact["app_build_ref"] = "tampered-build"
                else:
                    artifact["items"]["AC-1"]["attempts"] = 999
                artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
                os.chmod(artifact_path, 0o600)

                with self.assertRaises(runner.OperationError):
                    runner._load_operation(operation_dir)

    def test_resigned_operation_still_requires_exact_scope_case_and_key_contracts(self) -> None:
        mutation_names = (
            "duplicate_frozen_id",
            "case_set_mismatch",
            "item_set_mismatch",
            "case_id_mismatch",
            "before_snapshot_mismatch",
            "idempotency_key_mismatch",
        )
        for mutation_name in mutation_names:
            with self.subTest(mutation=mutation_name), tempfile.TemporaryDirectory() as temporary:
                operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))
                baseline_path = operation_dir / "baseline.json"
                progress_path = operation_dir / "progress.json"
                baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
                progress = json.loads(progress_path.read_text(encoding="utf-8"))
                if mutation_name == "duplicate_frozen_id":
                    baseline["frozen_case_ids"].append("AC-1")
                elif mutation_name == "case_set_mismatch":
                    baseline["cases"]["AC-EXTRA"] = _case("AC-EXTRA")
                elif mutation_name == "item_set_mismatch":
                    progress["items"]["AC-EXTRA"] = dict(progress["items"]["AC-1"])
                elif mutation_name == "case_id_mismatch":
                    baseline["cases"]["AC-1"]["account_case_id"] = "AC-OTHER"
                elif mutation_name == "before_snapshot_mismatch":
                    progress["items"]["AC-1"]["before"]["account_case_id"] = "AC-OTHER"
                else:
                    progress["items"]["AC-1"]["idempotency_key"] = "tampered-key"
                runner._write_json(baseline_path, baseline)
                runner._write_json(progress_path, progress)
                runner._write_operation_manifest(operation_dir, baseline, progress)

                with self.assertRaises(runner.OperationError):
                    runner._load_operation(operation_dir)

    def test_operation_files_reject_symlinks_wrong_modes_and_wrong_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation_dir, _ = self._dry_run(root, case_ids=("AC-1",))
            alias = root / "operation-alias"
            alias.symlink_to(operation_dir, target_is_directory=True)
            with self.assertRaises(runner.OperationError):
                runner._load_operation(alias)

        for filename in ("baseline.json", "progress.json", "manifest.json", "manifest.key"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                operation_dir, _ = self._dry_run(root, case_ids=("AC-1",))
                artifact_path = operation_dir / filename
                outside_path = root / f"outside-{filename}"
                artifact_path.rename(outside_path)
                artifact_path.symlink_to(outside_path)
                with self.assertRaises(runner.OperationError):
                    runner._load_operation(operation_dir)

        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))
            os.chmod(operation_dir / "baseline.json", 0o644)
            with self.assertRaises(runner.OperationError):
                runner._load_operation(operation_dir)

        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))
            os.chmod(operation_dir, 0o755)
            with self.assertRaises(runner.OperationError):
                runner._load_operation(operation_dir)

        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))
            with patch.object(runner.os, "getuid", return_value=os.getuid() + 1):
                with self.assertRaises(runner.OperationError):
                    runner._load_operation(operation_dir)

    def test_cli_rejects_resume_symlink_before_constructing_an_http_requester(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation_dir, _ = self._dry_run(root, case_ids=("AC-1",))
            alias = root / "operation-alias"
            alias.symlink_to(operation_dir, target_is_directory=True)
            with (
                patch.dict(os.environ, {runner.ACCESS_TOKEN_ENV: "runner-token"}),
                patch.object(runner, "_http_requester") as requester,
            ):
                with self.assertRaises(runner.OperationError):
                    runner.main(["--resume", str(alias), "--apply"])
            requester.assert_not_called()

    def test_lock_symlink_failure_preserves_target_and_cleans_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation_dir, _ = self._dry_run(root, case_ids=("AC-1",))
            outside = root / "outside-lock-target"
            outside.write_text("must-not-change", encoding="utf-8")
            os.chmod(outside, 0o644)
            lock_path = operation_dir / "operation.lock"
            lock_path.symlink_to(outside)

            with self.assertRaises(runner.OperationError):
                with runner.operation_lock(operation_dir):
                    pass

            self.assertEqual(outside.read_text(encoding="utf-8"), "must-not-change")
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o644)
            self.assertFalse(runner._LOCAL_OPERATION_LOCKS)
            lock_path.unlink()
            with runner.operation_lock(operation_dir):
                pass
            self.assertFalse(runner._LOCAL_OPERATION_LOCKS)

    def test_unlock_failure_still_closes_and_clears_local_lock_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary), case_ids=("AC-1",))
            original_flock = runner.fcntl.flock
            unlock_descriptors: list[int] = []

            def fail_unlock(descriptor: int, operation: int) -> None:
                if operation == runner.fcntl.LOCK_UN:
                    unlock_descriptors.append(descriptor)
                    raise OSError("simulated unlock failure")
                original_flock(descriptor, operation)

            try:
                with patch.object(runner.fcntl, "flock", side_effect=fail_unlock):
                    with self.assertRaises(OSError):
                        with runner.operation_lock(operation_dir):
                            pass
                self.assertFalse(runner._LOCAL_OPERATION_LOCKS)
            finally:
                for descriptor in unlock_descriptors:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                runner._LOCAL_OPERATION_LOCKS.clear()

    def test_resume_skips_terminal_items_and_never_rediscovers_the_frozen_target_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            operation_dir, _ = self._dry_run(Path(temporary))
            progress_path = operation_dir / "progress.json"
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            progress["items"]["AC-1"].update(
                {
                    "status": "completed",
                    "job_id": "job-already-terminal",
                    "job": {"job_id": "job-already-terminal", "status": "completed"},
                }
            )
            baseline = json.loads((operation_dir / "baseline.json").read_text(encoding="utf-8"))
            runner._persist_progress(operation_dir, baseline, progress)

            def handler(call: dict[str, object]) -> dict[str, object]:
                parsed = urlsplit(str(call["path"]))
                if parsed.path == "/health":
                    return {"app_build": {"ref": "build-1"}}
                if parsed.path == "/api/workspace/admin/account-personas":
                    return _personas()
                if parsed.path == "/api/account/cases":
                    raise AssertionError("resume must not rediscover Account Cases")
                if parsed.path == "/api/account/cases/AC-2":
                    return _case("AC-2")
                if call["method"] == "POST" and parsed.path == "/api/account/cases/AC-2/rerun":
                    return {"job_id": "job-resumed", "status": "queued"}
                if parsed.path == "/api/account/rerun-jobs/job-resumed":
                    return {"job_id": "job-resumed", "status": "completed"}
                raise AssertionError(call)

            request = FakeRequest(handler)
            resumed = runner.apply_operation(operation_dir, request_json=request, sleep=lambda _value: None)

            self.assertEqual(resumed["items"]["AC-1"]["job_id"], "job-already-terminal")
            self.assertEqual(resumed["items"]["AC-2"]["status"], "completed")
            posts = [str(call["path"]) for call in request.calls if call["method"] == "POST"]
            self.assertEqual(posts, ["/api/account/cases/AC-2/rerun"])


if __name__ == "__main__":
    unittest.main()
