from __future__ import annotations

import unittest
from pathlib import Path


class TicketRoutingContractTests(unittest.TestCase):
    def test_main_uses_main_agent_runtime_and_exposes_route_metadata(self) -> None:
        source = Path("backend/main.py").read_text(encoding="utf-8")
        runtime_source = Path("backend/services/client_ticket_agent_runtime.py").read_text(encoding="utf-8")

        self.assertIn("execute_client_ticket_agent_runtime", source)
        self.assertNotIn("orchestrate_ticket_execution", source)
        self.assertIn('"answer_route":', source)
        self.assertIn('"scope_label":', source)
        self.assertIn('"route_reason":', source)
        self.assertIn('"route_confidence":', source)
        self.assertIn('"search_used":', source)
        self.assertIn('assistant_message["retrieval_plan_snapshot"]', source)
        self.assertIn('route_payload.get("retrieval_plan_snapshot")', source)
        self.assertIn("class TicketExecutionResult", runtime_source)
        self.assertIn("AGENT_NAME_MAIN", runtime_source)
        self.assertIn("AGENT_NAME_ROUTE", runtime_source)
        self.assertIn("AGENT_NAME_RAG", runtime_source)
        self.assertIn("AGENT_NAME_REVIEW", runtime_source)
        self.assertIn("workflow_action", runtime_source)
        self.assertIn("route_family", runtime_source)
        self.assertIn("execution_action", runtime_source)
        self.assertIn("tooling_profile", runtime_source)
        self.assertIn("needs_investigating", runtime_source)
        self.assertIn("next_status", runtime_source)
        self.assertIn("investigation_reason", runtime_source)

    def test_worker_uses_main_agent_runtime_and_persists_route_metadata(self) -> None:
        source = Path("backend/worker.py").read_text(encoding="utf-8")

        self.assertIn("execute_client_ticket_agent_runtime", source)
        self.assertNotIn("orchestrate_ticket_execution", source)
        self.assertIn('assistant_message["answer_route"]', source)
        self.assertIn('assistant_message["scope_label"]', source)
        self.assertIn('assistant_message["retrieval_plan_snapshot"]', source)
        self.assertIn('"answer_route": execution.answer_route', source)
        self.assertIn('"scope_label": execution.scope_label', source)


if __name__ == "__main__":
    unittest.main()
