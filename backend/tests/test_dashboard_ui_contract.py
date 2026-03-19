from __future__ import annotations

import re
import unittest
from pathlib import Path


class DashboardUiContractTests(unittest.TestCase):
    def test_root_dashboard_is_ticket_operations_page(self) -> None:
        source = Path("ui/dashboard-ui/index.html").read_text(encoding="utf-8")

        for required_id in [
            'id="ticket-volume"',
            'id="resolution-rate"',
            'id="sentiment-alerts"',
            'id="event-stream"',
            'id="header-user-controls"',
            'id="ws-status"',
        ]:
            self.assertIn(required_id, source)

        self.assertIn('href="/dashboard/rag/"', source)
        self.assertNotIn('data-dashboard-tab="experiments"', source)
        self.assertNotIn('data-dashboard-tab="overview"', source)

    def test_rag_dashboard_nav_uses_task_workbench_pages(self) -> None:
        source = Path("ui/dashboard-ui/rag/index.html").read_text(encoding="utf-8")

        expected_tabs = {
            "experiments": "Experiments",
            "diagnosis": "Diagnosis",
            "knowledge-supply": "Knowledge Supply",
            "production-signals": "Production Signals",
            "review": "Review Queue",
        }
        for page_name, label in expected_tabs.items():
            self.assertIn(f'data-dashboard-tab="{page_name}"', source)
            self.assertIn(f">{label}</button>", source)

        self.assertIn('class="dashboard-tab active" data-dashboard-tab="experiments"', source)

        for legacy_page in [
            "overview",
            "ingestion",
            "chunking",
            "embedding-index",
            "retrieval",
            "generation",
            "handoff",
            "performance-cost",
            "failures",
            "reports",
        ]:
            self.assertNotIn(f'data-dashboard-tab="{legacy_page}"', source)

    def test_rag_dashboard_app_defaults_to_experiments_and_registers_new_pages(self) -> None:
        source = Path("ui/dashboard-ui/rag/app.js").read_text(encoding="utf-8")
        self.assertIn('let currentDashboardTab = "experiments";', source)

        for page_name in [
            "experiments",
            "diagnosis",
            "knowledge-supply",
            "production-signals",
            "review",
        ]:
            self.assertRegex(
                source,
                rf'["\']{re.escape(page_name)}["\']\s*:\s*\{{',
            )


if __name__ == "__main__":
    unittest.main()
