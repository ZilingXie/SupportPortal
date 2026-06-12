from __future__ import annotations

from pathlib import Path
import unittest


class QbrPlanContractTests(unittest.TestCase):
    def test_qbr_plan_tracks_multi_agent_lane(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "Engineer AI multi-agent",
            "Plan Agent",
            "Memory Review Agent",
            "Implement Agent",
            "Conclude Agent",
            "当前下一步：实现真实 Plan Agent",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

    def test_qbr_plan_tracks_assignment_lane(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "Ticket assignment UI demo",
            "/assignment",
            "UTC+8",
            "I'm ready to roll",
            "I'm ready for the next case",
            "read-only",
            "/assignment/admin",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)

    def test_qbr_plan_tracks_billing_lane_with_canonical_ticket_language(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "Billing intake / ticket routing",
            "canonical `TK-...` ticket id",
            "account-intake-v2",
            "automation_status = automation",
            "不调用 follow process",
            "不发送内部邮件",
            "不再展示第二个 Support ticket ID",
            "`support_billing_tickets` companion table",
            "`source` 统一为 `manual` / `api`",
            "Recent tickets",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)
        self.assertNotIn("Billing ticket routing", html_source)

    def test_qbr_plan_tracks_rag_vs_kg_lane(self) -> None:
        html_source = Path("docs/qbr_plan.html").read_text(encoding="utf-8")
        required_terms = [
            "RAG vs KG / Case Memory",
            "Case Memory Ledger",
            "Active Retrieval Memory",
            "text-first product-form pipeline",
            "SupportPortal adapter",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, html_source)


if __name__ == "__main__":
    unittest.main()
