from __future__ import annotations

import unittest

from backend.services.agent_config import build_agent_config_payload
from backend.services.engineer_plan_agent import ENGINEER_PLAN_SKILLS


class AgentConfigTests(unittest.TestCase):
    @staticmethod
    def _navigation_node(node: dict, key: str) -> dict:
        if node["key"] == key:
            return node
        for child in node.get("children", []):
            try:
                return AgentConfigTests._navigation_node(child, key)
            except KeyError:
                continue
        raise KeyError(key)

    def test_catalog_groups_real_agents_and_places_personas_on_automation_router(self) -> None:
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
        self.assertNotIn("related_services", payload)
        automation = self._navigation_node(payload["route_navigation"], "automation-router")
        self.assertEqual(automation["persona_scope"], "account-automation")
        self.assertEqual(payload["automation_personas"][0]["persona_key"], "default-support")
        self.assertEqual(payload["automation_personas"][0]["published_version"], 1)

    def test_catalog_exposes_prompt_skill_and_empty_mcp_contracts(self) -> None:
        payload = build_agent_config_payload([])
        agents = {agent["key"]: agent for agent in payload["agents"]}

        route_prompts = {item["key"]: item for item in agents["route-agent"]["prompts"]}
        self.assertEqual(route_prompts["account-intent-classifier-system"]["version"], "account-intent-v2")
        self.assertEqual(route_prompts["account-agora-router-system"]["version"], "account-agora-v6")
        self.assertEqual(route_prompts["account-automation-router-system"]["version"], "account-automation-v7")
        self.assertEqual(route_prompts["account-account-billing-router-system"]["version"], "account-billing-v1")
        self.assertEqual(route_prompts["account-agora-router-system"]["metadata"]["scope"], "/account")
        self.assertEqual(route_prompts["account-automation-router-system"]["metadata"]["managed"], False)
        self.assertEqual(route_prompts["account-enablement-field-extractor-system"]["metadata"]["managed"], True)
        self.assertEqual(route_prompts["account-enablement-field-extractor-system"]["version"], "account-enablement-fields-v3")
        self.assertEqual(route_prompts["account-quota-field-extractor-system"]["version"], "account-quota-fields-v1")
        self.assertEqual(
            route_prompts["account-verification-field-extractor-system"]["version"],
            "fraud-account-fields-v2",
        )
        self.assertEqual(
            route_prompts["account-suspension-field-extractor-system"]["version"],
            "account-suspension-fields-v2",
        )
        self.assertEqual(
            route_prompts["account-detailed-invoice-field-extractor-system"]["metadata"]["managed"],
            True,
        )
        self.assertEqual(
            route_prompts["account-detailed-invoice-field-extractor-system"]["version"],
            "detailed-invoice-fields-v2",
        )
        self.assertEqual(route_prompts["route-system"]["version"], "account-router-v2")
        component_keys = {item["key"] for item in agents["route-agent"]["components"]}
        self.assertTrue(
            {
                "account-intent-classifier",
                "account-agora-router",
                "account-automation-router",
                "account-enablement-field-extractor",
                "account-quota-field-extractor",
                "fraud-account-handler",
                "account-suspension-field-extractor",
                "account-verification-field-extractor",
                "account-verification-payment-safety",
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
        self.assertTrue(all(not item["mcp_servers"] for item in payload["agents"]))

        route_navigation = payload["route_navigation"]
        all_nodes: list[dict] = []

        def collect_nodes(node: dict) -> None:
            all_nodes.append(node)
            for child in node["children"]:
                collect_nodes(child)

        collect_nodes(route_navigation)
        self.assertTrue(all(isinstance(node["is_agent"], bool) for node in all_nodes))
        self.assertEqual(
            [node["key"] for node in all_nodes if node["is_agent"]],
            ["route-agent", "agora-router", "account-billing-router", "automation-router"],
        )
        self.assertTrue(all(node["is_agent"] for node in all_nodes if node["kind"] in {"agent", "router"}))
        self.assertTrue(all(not node["is_agent"] for node in all_nodes if node["kind"] in {"outcome", "handoff", "automation", "fallback"}))
        self.assertEqual(
            [item["key"] for item in route_navigation["children"]],
            ["conversation-action", "agora-router", "intent-uncertain"],
        )
        agora = self._navigation_node(route_navigation, "agora-router")
        self.assertEqual(agora["prompt_keys"], ["account-agora-router-system"])
        automation = self._navigation_node(route_navigation, "automation-router")
        self.assertEqual(
            [item["key"] for item in automation["children"]],
            ["fraud-account", "detailed-invoice", "enablement", "quota", "unregistered"],
        )
        account_billing = self._navigation_node(route_navigation, "account-billing-router")
        self.assertEqual(
            [item["key"] for item in account_billing["children"]],
            ["account-suspension", "account-billing-other"],
        )
        self.assertEqual(
            account_billing["prompt_keys"],
            ["account-account-billing-router-system"],
        )
        self.assertEqual(automation["prompt_keys"], ["account-automation-router-system"])
        self.assertFalse(any("persona" in child for child in automation["children"]))
        fraud = self._navigation_node(route_navigation, "fraud-account")
        self.assertEqual(fraud["prompt_keys"], ["account-verification-field-extractor-system"])
        self.assertTrue(payload["route_runtime"]["router_prompt_version"])
        self.assertTrue(payload["route_runtime"]["stage_details"])

        serialized = str(payload)
        self.assertNotIn("OPENAI_API_KEY", serialized)
        self.assertNotIn("customer_message", serialized)
        self.assertNotIn("ticket_context", serialized)


if __name__ == "__main__":
    unittest.main()
