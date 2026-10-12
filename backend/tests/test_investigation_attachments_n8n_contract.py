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
        # Execute the attachment branch represented by the published graph.
        # The resolver response is injected at the resolver boundary, while
        # the next node and both output edges come from the real snapshot.
        # This catches a miswired gate even though no n8n API is called.
        def run_attachment_branch(resolver_result: dict[str, object]) -> tuple[list[str], int, int]:
            resolver_calls = 0
            hermes_calls = 0
            visited: list[str] = []
            node = "Validate Hermes Attachment Mention"
            while node:
                visited.append(node)
                if node == "Validate Hermes Attachment Mention":
                    node = self.connections[node]["main"][0][0]["node"]
                elif node == "Resolve Hermes Attachment Binding":
                    resolver_calls += 1
                    node = self.connections[node]["main"][0][0]["node"]
                elif node == "Hermes Attachment Bound":
                    bound = (
                        resolver_result.get("status") == "bound"
                        and bool(resolver_result.get("zendesk_ticket_id"))
                    )
                    output_index = 0 if bound else 1
                    node = self.connections[node]["main"][output_index][0]["node"]
                elif node == "Send Hermes Message":
                    hermes_calls += 1
                    node = ""
                elif node == "Ignore Non-Investigation Attachment":
                    node = ""
                else:
                    self.fail(f"unexpected attachment branch node: {node}")
            return visited, resolver_calls, hermes_calls

        scenarios = {
            "investigation-bound": {"status": "bound", "zendesk_ticket_id": "13923"},
            "production-bound": {"status": "ignored_production", "zendesk_ticket_id": None},
            "unbound": {"status": "ignored_unbound", "zendesk_ticket_id": None},
        }
        for name, resolver_result in scenarios.items():
            visited, resolver_calls, hermes_calls = run_attachment_branch(resolver_result)
            self.assertEqual(resolver_calls, 1, name)
            if name == "investigation-bound":
                self.assertEqual(hermes_calls, 1, visited)
                self.assertIn("Send Hermes Message", visited)
                self.assertNotIn("Ignore Non-Investigation Attachment", visited)
            else:
                self.assertEqual(hermes_calls, 0, visited)
                self.assertIn("Ignore Non-Investigation Attachment", visited)
                self.assertNotIn("Send Hermes Message", visited)

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
