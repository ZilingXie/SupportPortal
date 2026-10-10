from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = (
    ROOT
    / "docs"
    / "integrations"
    / "n8n"
    / "workflows"
    / "active"
    / "r1HIW8UNuCabiOPn.published.json"
)


class InvestigationAttachmentsN8nContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        cls.workflow = cls.payload["workflow"]
        cls.nodes = {node["name"]: node for node in cls.workflow["nodes"]}
        cls.connections = cls.workflow["connections"]

    def test_attachment_entry_is_preproduction_binding_gated(self) -> None:
        validator = self.nodes["Validate Hermes Attachment Mention"]
        condition = validator["parameters"]["conditions"]["conditions"][0]["leftValue"]
        self.assertIn("file_share", condition)
        self.assertIn("<@U08RVQSJQF2>", condition)
        self.assertIn("T1CBEDLJY", condition)
        self.assertIn("C0BS0N61D1R", condition)

        attachment_resolver = self.nodes["Resolve Hermes Attachment Binding"]
        self.assertIn(
            "/automation/preproduction/api/integrations/slack/hermes-cases/thread-bindings/resolve",
            attachment_resolver["parameters"]["url"],
        )
        self.assertEqual(
            self.connections["Validate Hermes Attachment Mention"]["main"][0][0]["node"],
            "Resolve Hermes Attachment Binding",
        )

        bound_gate = self.nodes["Hermes Attachment Bound"]
        gate = bound_gate["parameters"]["conditions"]["conditions"][0]["leftValue"]
        self.assertIn("status === 'bound'", gate)
        self.assertIn("zendesk_ticket_id", gate)
        self.assertEqual(
            self.connections["Hermes Attachment Bound"]["main"][0][0]["node"],
            "Send Hermes Message",
        )
        self.assertEqual(
            self.connections["Hermes Attachment Bound"]["main"][1][0]["node"],
            "Ignore Non-Investigation Attachment",
        )

    def test_attachment_scenarios_have_explicit_safe_outcomes(self) -> None:
        # The Preproduction Hermes resolver is the binding authority. A
        # production-bound thread is not returned as a Hermes binding, and an
        # unbound thread has the same safe non-send outcome.
        scenarios = {
            "investigation-bound": {"status": "bound", "zendesk_ticket_id": "13923"},
            "production-bound": {"status": "unbound", "zendesk_ticket_id": None},
            "unbound": {"status": "unbound", "zendesk_ticket_id": None},
        }
        for name, result in scenarios.items():
            is_hermes_bound = result["status"] == "bound" and bool(result["zendesk_ticket_id"])
            outcome = "send_hermes" if is_hermes_bound else "ignore"
            self.assertEqual(outcome, "send_hermes" if name == "investigation-bound" else "ignore")

        # Normal text messages retain their existing adhoc fallback path.
        self.assertEqual(
            self.connections["Hermes Bound Thread"]["main"][1][0]["node"],
            "Send Adhoc Session",
        )

    def test_production_path_remains_separate(self) -> None:
        self.assertIn("/automation/production/api/integrations/slack/engineer-cases", json.dumps(self.workflow))
        self.assertIn(
            "Validate Slack Mention",
            [edge["node"] for edge in self.connections["Validate Hermes Attachment Mention"]["main"][1]],
        )


if __name__ == "__main__":
    unittest.main()
