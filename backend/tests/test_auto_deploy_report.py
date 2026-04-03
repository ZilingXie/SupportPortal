from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.llm_factory import LlmTextResult


class AutoDeployReportTests(unittest.TestCase):
    def _context(self, *, status: str = "success") -> dict[str, object]:
        return {
            "status": status,
            "execution_mode": "health-only",
            "host": "ip-10-0-0-1",
            "branch": "main",
            "local_commit": "abc1234",
            "remote_commit": "abc1234",
            "failed_step": "none" if status == "success" else "Internal health check",
            "domain": "support.stellarix.space",
            "started_at_utc": "2026-04-03T18:59:00Z",
            "ended_at_utc": "2026-04-03T19:00:00Z",
            "duration_seconds": 60,
            "internal_health_status": "ok" if status == "success" else "failed",
            "internal_health_detail": '{"status":"ok"}' if status == "success" else "curl: (22) 502",
            "external_health_status": "ok",
            "external_health_detail": '{"status":"ok"}',
            "run_log_tail": "recent run tail",
            "report_timezone": "Asia/Shanghai",
        }

    def _diagnostics(self) -> dict[str, str]:
        return {
            "service_status_text": "NAME\\napi up",
            "service_logs_text": "api | INFO startup complete\\nworker | WARNING queue depth high",
            "suspicious_excerpt_text": "worker | WARNING queue depth high",
        }

    def test_build_report_email_payload_uses_daily_subject_and_ai_summary(self) -> None:
        from backend.services.auto_deploy_report import build_report_email_payload

        payload = build_report_email_payload(
            context=self._context(),
            diagnostics=self._diagnostics(),
            ai_analysis="风险等级：低\\n建议：继续观察。",
            from_address="alerts@example.com",
            to_addresses=["ops@example.com"],
        )

        self.assertEqual(payload["Content"]["Simple"]["Subject"]["Data"], "SupportPortal Report 4/4")
        body = payload["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertIn("运行摘要", body)
        self.assertIn("AI 日志分析", body)
        self.assertIn("风险等级：低", body)
        self.assertIn("服务状态", body)

    def test_failed_report_subject_uses_failed_prefix(self) -> None:
        from backend.services.auto_deploy_report import build_report_email_payload

        payload = build_report_email_payload(
            context=self._context(status="failed"),
            diagnostics=self._diagnostics(),
            ai_analysis="风险等级：高",
            from_address="alerts@example.com",
            to_addresses=["ops@example.com"],
        )

        self.assertEqual(payload["Content"]["Simple"]["Subject"]["Data"], "[Failed] SupportPortal Report 4/4")

    def test_ai_analysis_reports_unavailable_when_openai_key_missing(self) -> None:
        from backend.services.auto_deploy_report import build_ai_analysis

        with patch.dict(os.environ, {}, clear=True):
            analysis = build_ai_analysis(context=self._context(), diagnostics=self._diagnostics())

        self.assertIn("AI analysis unavailable", analysis)
        self.assertIn("OPENAI_API_KEY", analysis)

    def test_ai_analysis_uses_llm_when_available(self) -> None:
        from backend.services.auto_deploy_report import build_ai_analysis

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with patch(
                "backend.services.auto_deploy_report.invoke_responses_text",
                return_value=LlmTextResult(text="风险等级：中\\n建议：关注 worker 日志。", model_name="gpt-5.4-mini"),
            ):
                analysis = build_ai_analysis(context=self._context(), diagnostics=self._diagnostics())

        self.assertIn("风险等级：中", analysis)
        self.assertIn("关注 worker 日志", analysis)

    def test_sanitize_log_text_truncates_and_strips_binary_characters(self) -> None:
        from backend.services.auto_deploy_report import sanitize_log_text

        sanitized = sanitize_log_text("ok\x00oops\n" + ("a" * 200), max_chars=20)

        self.assertNotIn("\x00", sanitized)
        self.assertLessEqual(len(sanitized), 40)
        self.assertIn("[truncated]", sanitized)


if __name__ == "__main__":
    unittest.main()
