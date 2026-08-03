from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_admin import (
    DEFAULT_PERSONA_CONTENT,
    DEFAULT_PERSONA_KEY,
    ROUTER_PROMPT_VERSION,
    apply_persona_to_customer_reply,
    account_automation_payload,
    environment_config_entries,
    environment_config_names,
    route_execution_from_decision,
    routing_config_payload,
)
from backend.services.support_router import SupportRouteDecision


class AccountAdminFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()

    def test_automation_ratio_uses_all_account_tickets(self) -> None:
        for ticket_id, status in (("BT-1", "automation"), ("BT-2", "not_automated"), ("BT-3", "unknown")):
            self.repository.save_billing_ticket(
                {
                    "billing_ticket_id": ticket_id,
                    "client_ticket_id": ticket_id.removeprefix("BT-"),
                    "title": ticket_id,
                    "question": "question",
                    "automation_status": status,
                    "route_family": "automated" if status == "automation" else "web_company_info",
                    "execution_action": "detailed_invoice" if status == "automation" else "web_search",
                    "created_at": f"2026-07-2{ticket_id[-1]}T00:00:00+00:00",
                }
            )

        payload = account_automation_payload(self.repository, page=1, page_size=2)

        self.assertEqual(payload["metrics"], {
            "total_account_cases": 3,
            "automated_cases": 1,
            "not_automated_cases": 2,
            "automation_rate": 1 / 3,
        })
        self.assertEqual(len(payload["cases"]), 2)

        filtered = account_automation_payload(self.repository, page=1, page_size=20, route_status="automated")
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["metrics"]["total_account_cases"], 3)
        self.assertEqual(filtered["cases"][0]["category"], "automation")
        self.assertEqual(filtered["cases"][0]["subcategory"], "detailed_invoice")

    def test_account_reply_supersede_marks_old_account_message(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "TK-RERUN",
                "messages": [
                    {"role": "customer", "content": "Request", "created_at": "2026-08-03T00:00:00+00:00"},
                    {
                        "role": "assistant",
                        "content": "Old reply",
                        "source": "account_ai",
                        "meta": {"account_reply_job_id": "old-job"},
                        "created_at": "2026-08-03T00:01:00+00:00",
                    },
                ],
            }
        )

        self.assertEqual(
            self.repository.supersede_account_ai_messages(
                "TK-RERUN",
                except_job_id="new-job",
                superseded_at="2026-08-03T00:02:00+00:00",
            ),
            1,
        )
        message = self.repository.get_ticket("TK-RERUN")["messages"][1]
        self.assertTrue(message["meta"]["superseded"])
        self.assertEqual(message["meta"]["superseded_by_job_id"], "new-job")

    def test_environment_config_returns_names_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("# comment\nSAFE_NAME=secret\nexport OTHER_KEY='hidden'\nBAD-NAME=nope\nSAFE_NAME=second\n", encoding="utf-8")
            self.assertEqual(environment_config_names(env_path), ["OTHER_KEY", "SAFE_NAME"])
        self.assertEqual(environment_config_names(Path(directory) / ".env"), [])

    def test_environment_config_can_require_a_readable_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / ".env"
            with self.assertRaises(OSError):
                environment_config_names(missing_path, required=True)

            directory_path = Path(directory) / "config-dir"
            directory_path.mkdir()
            with self.assertRaises(OSError):
                environment_config_names(directory_path, required=True)

    def test_environment_config_entries_describe_every_name_without_reading_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=do-not-return-this-value\n"
                "TICKET_DB_DSN=postgresql://hidden\n"
                "CUSTOM_RUNTIME_SWITCH=another-secret\n",
                encoding="utf-8",
            )

            entries = environment_config_entries(env_path)

        self.assertEqual([entry["name"] for entry in entries], [
            "CUSTOM_RUNTIME_SWITCH",
            "OPENAI_API_KEY",
            "TICKET_DB_DSN",
        ])
        self.assertTrue(all(str(entry["description"]).strip() for entry in entries))
        self.assertIn("OpenAI", entries[1]["description"])
        self.assertIn("PostgreSQL", entries[2]["description"])
        serialized = repr(entries)
        self.assertNotIn("do-not-return-this-value", serialized)
        self.assertNotIn("postgresql://hidden", serialized)
        self.assertNotIn("another-secret", serialized)

    def test_environment_config_example_has_descriptions_for_every_key(self) -> None:
        entries = environment_config_entries(Path(".env.example"))

        self.assertGreater(len(entries), 100)
        self.assertEqual(len(entries), len(environment_config_names(Path(".env.example"))))
        self.assertTrue(all(entry["description"].strip() for entry in entries))
        self.assertEqual(len({entry["name"] for entry in entries}), len(entries))

    def test_route_execution_preserves_exact_prompt_snapshot(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="detailed_invoice",
            confidence=0.91,
            reason="billing_request",
            automation_eligibility="eligible",
            policy_decision="allow",
            router_source="intent_router",
            intent_router_attempted=True,
            intent_router_confidence_threshold=0.8,
            intent_router_model_confidence=0.91,
        )
        execution = route_execution_from_decision(
            ticket_id="TK-1",
            decision=decision,
            system_prompt="system snapshot",
            user_prompt="user snapshot",
        )
        self.assertEqual(execution["router_prompt_version"], ROUTER_PROMPT_VERSION)
        self.assertEqual(execution["system_prompt"], "system snapshot")
        self.assertEqual(execution["user_prompt"], "user snapshot")
        self.assertTrue(execution["prompt_snapshot_available"])
        self.assertGreaterEqual(len(execution["stages"]), 3)

    def test_routing_config_describes_stages_and_lists_supported_categories(self) -> None:
        payload = routing_config_payload()

        self.assertEqual(payload["stages"], [stage["name"] for stage in payload["stage_details"]])
        self.assertTrue(all(stage["name"] and stage["description"] for stage in payload["stage_details"]))
        self.assertEqual(
            [category["name"] for category in payload["route_categories"]],
            ["conversation", "intent", "agora", "automation"],
        )
        automation = next(category for category in payload["route_categories"] if category["name"] == "automation")
        self.assertEqual(
            automation["subcategories"],
            ["account_verification", "account_suspension", "detailed_invoice", "enablement", "quota", "unregistered"],
        )
        self.assertIn("Intent Classifier", payload["system_prompt"])
        self.assertIn("Automation Router", payload["system_prompt"])

    def test_persona_draft_publish_assignment_and_rollback_are_versioned(self) -> None:
        personas = self.repository.list_account_personas()
        self.assertEqual(personas[0]["persona_key"], DEFAULT_PERSONA_KEY)
        self.assertEqual(personas[0]["published_version"], 1)
        default_instruction = personas[0]["versions"][0]["content"]["instruction"]
        self.assertEqual(personas[0]["versions"][0]["content"], DEFAULT_PERSONA_CONTENT)
        self.assertIn("friendly and helpful support agent", default_instruction)
        self.assertIn("Match the customer's language", default_instruction)
        self.assertIn("You are Sid", default_instruction)
        self.assertEqual(
            personas[0]["versions"][0]["content"]["signature"],
            "Best,\nSid\nSupport Engineer 2",
        )

        draft = self.repository.create_account_persona_draft(
            DEFAULT_PERSONA_KEY,
            content={"instruction": "Calm and concise", "signoff_name": "Sid"},
            change_note="Calmer reply",
            based_on_version=1,
            actor_id="admin-1",
            created_at="2026-07-21T01:00:00+00:00",
        )
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(self.repository.resolve_account_persona("TK-1")["version"], 1)

        published = self.repository.publish_account_persona_version(
            DEFAULT_PERSONA_KEY, draft["version"], actor_id="admin-1", published_at="2026-07-21T02:00:00+00:00"
        )
        self.assertEqual(published["status"], "published")
        first = self.repository.resolve_account_persona("TK-STABLE")
        self.assertEqual(first["version"], 2)

        rollback = self.repository.rollback_account_persona_version(
            DEFAULT_PERSONA_KEY, 1, actor_id="admin-1", published_at="2026-07-21T03:00:00+00:00"
        )
        self.assertEqual(rollback["version"], 3)
        self.assertEqual(rollback["status"], "published")
        self.assertEqual(self.repository.resolve_account_persona("TK-STABLE")["version"], 2)

    def test_last_enabled_persona_cannot_be_disabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "last enabled persona"):
            self.repository.set_account_persona_enabled(DEFAULT_PERSONA_KEY, False)

    def test_persona_opener_and_reply_execution_are_auditable(self) -> None:
        persona = {
            "persona_key": "concise",
            "version": 4,
            "content": {
                "instruction": "Be concise",
                "opener": "Thanks for contacting the billing team.",
                "signature": "Best,\nMaya\nSupport Engineer 1",
            },
        }
        rendered = apply_persona_to_customer_reply("Hi Taylor,\n\nPlease send the transaction ID.\n\nBest Regards,\nSid", persona)
        self.assertIn("Thanks for contacting the billing team.", rendered)
        self.assertTrue(rendered.endswith("Best,\nMaya\nSupport Engineer 1"))

        saved = self.repository.save_account_reply_execution({
            "execution_id": "reply-1",
            "ticket_id": "TK-1",
            "reply_kind": "missing_fields",
            "persona_key": "concise",
            "persona_version": 4,
            "effective_prompt": persona["content"],
            "created_at": "2026-07-21T00:00:00+00:00",
        })
        self.assertEqual(saved["persona_version"], 4)
        self.assertEqual(self.repository.list_account_reply_executions("TK-1"), [saved])


if __name__ == "__main__":
    unittest.main()
