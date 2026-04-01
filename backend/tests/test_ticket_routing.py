from __future__ import annotations

import unittest
from pathlib import Path


class TicketRoutingContractTests(unittest.TestCase):
    def test_main_uses_shared_ticket_orchestrator_and_exposes_route_metadata(self) -> None:
        source = Path("backend/main.py").read_text(encoding="utf-8")
        orchestrator_source = Path("backend/services/ticket_orchestrator.py").read_text(encoding="utf-8")

        self.assertIn("orchestrate_ticket_execution", source)
        self.assertIn('"answer_route":', source)
        self.assertIn('"scope_label":', source)
        self.assertIn('"route_reason":', source)
        self.assertIn('"route_confidence":', source)
        self.assertIn('"search_used":', source)
        self.assertIn("class TicketExecutionResult", orchestrator_source)
        self.assertIn("class AgenticExecutionPlan", orchestrator_source)
        self.assertIn("class SkillExecutionResult", orchestrator_source)
        self.assertIn("class SufficiencyAssessment", orchestrator_source)
        self.assertIn("route_family", orchestrator_source)
        self.assertIn("execution_action", orchestrator_source)
        self.assertIn("tooling_profile", orchestrator_source)
        self.assertIn("needs_investigating", orchestrator_source)
        self.assertIn("next_status", orchestrator_source)
        self.assertIn("investigation_reason", orchestrator_source)

    def test_worker_uses_shared_ticket_orchestrator_and_persists_route_metadata(self) -> None:
        source = Path("backend/worker.py").read_text(encoding="utf-8")

        self.assertIn("orchestrate_ticket_execution", source)
        self.assertIn('assistant_message["answer_route"]', source)
        self.assertIn('assistant_message["scope_label"]', source)
        self.assertIn('"answer_route": execution.answer_route', source)
        self.assertIn('"scope_label": execution.scope_label', source)


if __name__ == "__main__":
    unittest.main()
