from __future__ import annotations

import unittest

from backend.services.account_admin import ACCOUNT_PERSONA_PRESETS
from backend.services.agent_config import build_agent_config_payload, build_managed_prompt_catalog
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
        seeded_personas = [
            {
                "persona_key": preset.persona_key,
                "display_name": preset.display_name,
                "enabled": True,
                "published_version": 1,
                "versions": [
                    {
                        "version": 1,
                        "status": "published",
                        "content": preset.content,
                        "change_note": preset.seed_marker,
                        "created_at": "2026-07-20T00:00:00+00:00",
                        "published_at": "2026-07-20T00:00:00+00:00",
                    }
                ],
            }
            for preset in ACCOUNT_PERSONA_PRESETS
        ]
        payload = build_agent_config_payload(seeded_personas)

        self.assertEqual(
            [agent["key"] for agent in payload["agents"]],
            ["route-agent", "client-agent", "engineer-agent", "guardrail-agent"],
        )
        self.assertTrue(all(agent["kind"] == "agent" for agent in payload["agents"]))
        self.assertNotIn("related_services", payload)
        automation = self._navigation_node(payload["route_navigation"], "automation-router")
        self.assertEqual(automation["persona_scope"], "account-automation")
        personas = {item["persona_key"]: item for item in payload["automation_personas"]}
        self.assertEqual(set(personas), {"default-support", "sid-bright", "sid-precise"})
        for preset in ACCOUNT_PERSONA_PRESETS:
            persona = personas[preset.persona_key]
            self.assertEqual(persona["display_name"], preset.display_name)
            self.assertTrue(persona["enabled"])
            self.assertEqual(persona["published_version"], 1)
            self.assertEqual(persona["versions"][0]["content"]["instruction"], preset.content["instruction"])
            self.assertEqual(persona["versions"][0]["content"]["opener"], "")
            self.assertEqual(set(persona["versions"][0]["content"]), {"instruction", "opener"})

    def test_detailed_invoice_is_classification_only_and_not_an_automation_workflow(self) -> None:
        payload = build_agent_config_payload([])
        route_navigation = payload["route_navigation"]

        detailed_invoice = self._navigation_node(route_navigation, "detailed-invoice")

        self.assertEqual(detailed_invoice["kind"], "classification")
        self.assertEqual(detailed_invoice["workflow"]["route_family"], "human_review")
        self.assertEqual(detailed_invoice["workflow"]["status"], "classification_only")
        self.assertIsNone(detailed_invoice["workflow"]["automation_handler"])
        self.assertNotIn(
            "detailed_invoice",
            {item["subcategory"] for item in payload["automation_workflows"]},
        )

    def test_catalog_exposes_prompt_skill_and_empty_mcp_contracts(self) -> None:
        payload = build_agent_config_payload([])
        agents = {agent["key"]: agent for agent in payload["agents"]}

        route_prompts = {item["key"]: item for item in agents["route-agent"]["prompts"]}
        self.assertEqual(route_prompts["account-intent-classifier-system"]["version"], "account-intent-v3")
        self.assertEqual(route_prompts["account-agora-router-system"]["version"], "account-agora-v10")
        self.assertEqual(route_prompts["account-automation-router-system"]["version"], "account-automation-v7")
        self.assertEqual(route_prompts["account-backend-operation-router-system"]["version"], "account-backend-operation-v1")
        self.assertEqual(route_prompts["account-account-billing-router-system"]["version"], "account-billing-v2")
        self.assertEqual(route_prompts["account-agora-router-system"]["metadata"]["scope"], "/account")
        self.assertEqual(route_prompts["account-automation-router-system"]["metadata"]["managed"], True)
        self.assertEqual(route_prompts["account-backend-operation-router-system"]["metadata"]["managed"], True)
        self.assertEqual(route_prompts["account-enablement-field-extractor-system"]["metadata"]["managed"], True)
        self.assertEqual(route_prompts["account-enablement-field-extractor-system"]["version"], "account-enablement-fields-v3")
        self.assertEqual(route_prompts["account-quota-field-extractor-system"]["version"], "account-quota-fields-v1")
        self.assertEqual(
            route_prompts["account-verification-field-extractor-system"]["version"],
            "fraud-account-fields-v4",
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
            ["enablement", "quota", "unregistered"],
        )
        self.assertIn("three registered Automation outcomes", automation["description"])
        unregistered = self._navigation_node(route_navigation, "unregistered")
        self.assertEqual(unregistered["workflow"]["status"], "fallback")
        self.assertIn("not a registered Automation or Human Review filter member", unregistered["description"])
        account_billing = self._navigation_node(route_navigation, "account-billing-router")
        self.assertEqual(
            [item["key"] for item in account_billing["children"]],
            ["account-suspension", "fraud-account", "detailed-invoice", "account-billing-other"],
        )
        self.assertEqual(
            account_billing["prompt_keys"],
            ["account-account-billing-router-system"],
        )
        self.assertEqual(
            automation["prompt_keys"],
            ["account-backend-operation-router-system", "account-automation-router-system"],
        )
        self.assertFalse(any("persona" in child for child in automation["children"]))
        fraud = self._navigation_node(route_navigation, "fraud-account")
        self.assertEqual(fraud["prompt_keys"], ["account-verification-field-extractor-system"])
        detailed_invoice = self._navigation_node(route_navigation, "detailed-invoice")
        self.assertEqual(detailed_invoice["kind"], "classification")
        self.assertEqual(detailed_invoice["workflow"]["status"], "classification_only")
        self.assertIsNone(detailed_invoice["workflow"]["automation_handler"])
        self.assertEqual(len(payload["automation_workflows"]), 4)
        self.assertEqual(
            [item["subcategory"] for item in payload["automation_workflows"]],
            ["fraud_account", "enablement", "quota", "unregistered"],
        )
        self.assertEqual(
            [item["status"] for item in payload["automation_workflows"]],
            ["registered", "registered", "registered", "fallback"],
        )
        self.assertTrue(payload["route_runtime"]["router_prompt_version"])
        self.assertTrue(payload["route_runtime"]["stage_details"])

        serialized = str(payload)
        self.assertNotIn("OPENAI_API_KEY", serialized)
        self.assertNotIn("customer_message", serialized)
        self.assertNotIn("ticket_context", serialized)

    def test_managed_catalog_uses_current_account_route_prompt_content(self) -> None:
        catalog = {
            item["prompt_key"]: item["content"]
            for item in build_managed_prompt_catalog()
        }

        self.assertIn("detailed_invoice_requested", catalog["account-account-billing-router-system"])
        self.assertIn("invoice_payment_reconciliation", catalog["account-account-billing-router-system"])
        self.assertIn("legal_enforcement_request", catalog["account-agora-router-system"])
        self.assertNotIn(
            "legal_compliance_request",
            catalog["account-agora-router-system"],
        )


if __name__ == "__main__":
    unittest.main()
