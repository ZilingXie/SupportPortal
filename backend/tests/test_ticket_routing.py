from __future__ import annotations

import unittest
from pathlib import Path


class TicketRoutingContractTests(unittest.TestCase):
    def test_main_uses_shared_resolver_and_exposes_route_metadata(self) -> None:
        source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn("resolve_support_message", source)
        self.assertIn('"answer_route":', source)
        self.assertIn('"scope_label":', source)
        self.assertIn('"route_reason":', source)
        self.assertIn('"route_confidence":', source)
        self.assertIn('"search_used":', source)

    def test_worker_uses_shared_resolver_and_persists_route_metadata(self) -> None:
        source = Path("backend/worker.py").read_text(encoding="utf-8")

        self.assertIn("resolve_support_message", source)
        self.assertIn('assistant_message["answer_route"]', source)
        self.assertIn('assistant_message["scope_label"]', source)
        self.assertIn('"answer_route": resolution.answer_route', source)
        self.assertIn('"scope_label": resolution.scope_label', source)


if __name__ == "__main__":
    unittest.main()
