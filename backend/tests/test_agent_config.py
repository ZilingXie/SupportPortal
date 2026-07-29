from __future__ import annotations

import unittest

from backend.services.agent_config import build_agent_config_payload
from backend.services.engineer_plan_agent import ENGINEER_PLAN_SKILLS


class AgentConfigTests(unittest.TestCase):
    def test_catalog_groups_real_agents_and_labels_billing_as_a_service(self) -> None:
        payload = build_agent_config_payload(
            [
                {
                    "persona_key": "default-support",
                    "display_name": "Default Support",
                    "enabled": True,
                    "published_version": 1,
                    "versions": [
                        {
                            "version": 1,
                            "status": "published",
                            "content": {"instruction": "Calm", "signoff_name": "Sid"},
                            "change_note": "Initial",
                            "created_at": "2026-07-20T00:00:00+00:00",
                            "published_at": "2026-07-20T00:00:00+00:00",
                        }
                    ],
                }
            ]
        )

        self.assertEqual(
            [agent["key"] for agent in payload["agents"]],
            ["route-agent", "client-agent", "engineer-agent", "guardrail-agent"],
        )
        self.assertTrue(all(agent["kind"] == "agent" for agent in payload["agents"]))
        billing = payload["related_services"][0]
        self.assertEqual(billing["key"], "billing-automation")
        self.assertEqual(billing["kind"], "service")
        self.assertIn("not an autonomous Agent", billing["description"])
        self.assertEqual(billing["prompts"][0]["metadata"]["status"], "published")
        self.assertTrue(billing["prompts"][0]["metadata"]["is_published"])

    def test_catalog_exposes_prompt_skill_and_empty_mcp_contracts(self) -> None:
        payload = build_agent_config_payload([])
        agents = {agent["key"]: agent for agent in payload["agents"]}

        route_prompts = {item["key"]: item for item in agents["route-agent"]["prompts"]}
        self.assertEqual(route_prompts["account-intent-classifier-system"]["version"], "account-intent-v1")
        self.assertEqual(route_prompts["account-agora-router-system"]["metadata"]["scope"], "/account")
        self.assertEqual(route_prompts["account-automation-router-system"]["metadata"]["managed"], False)
        self.assertEqual(route_prompts["account-enablement-field-extractor-system"]["metadata"]["managed"], True)
        self.assertEqual(route_prompts["account-enablement-field-extractor-system"]["version"], "account-enablement-fields-v1")
        self.assertEqual(route_prompts["route-system"]["version"], "account-router-v2")
        component_keys = {item["key"] for item in agents["route-agent"]["components"]}
        self.assertTrue(
            {
                "account-intent-classifier",
                "account-agora-router",
                "account-automation-router",
                "account-enablement-field-extractor",
                "route-classifier",
            }.issubset(component_keys)
        )
        self.assertGreater(len(agents["client-agent"]["prompts"]), 10)
        self.assertEqual(
            [skill["key"] for skill in agents["engineer-agent"]["skills"]],
            list(ENGINEER_PLAN_SKILLS),
        )
        self.assertIn("disabled by default", agents["engineer-agent"]["description"])
        final_guardrail = next(
            component
            for component in agents["guardrail-agent"]["components"]
            if component["key"] == "engineer-final-guardrail"
        )
        self.assertIn("No prompt", final_guardrail["description"])
        self.assertTrue(
            all(
                not item["mcp_servers"]
                for item in [*payload["agents"], *payload["related_services"]]
            )
        )

        serialized = str(payload)
        self.assertNotIn("OPENAI_API_KEY", serialized)
        self.assertNotIn("customer_message", serialized)
        self.assertNotIn("ticket_context", serialized)


if __name__ == "__main__":
    unittest.main()
