from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_admin import (
    DEFAULT_PERSONA_KEY,
    ROUTER_PROMPT_VERSION,
    apply_persona_to_customer_reply,
    account_automation_payload,
    environment_config_names,
    route_execution_from_decision,
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

        filtered = account_automation_payload(self.repository, page=1, page_size=20, route_status="automation")
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["metrics"]["total_account_cases"], 3)

    def test_environment_config_returns_names_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("# comment\nSAFE_NAME=secret\nexport OTHER_KEY='hidden'\nBAD-NAME=nope\nSAFE_NAME=second\n", encoding="utf-8")
            self.assertEqual(environment_config_names(env_path), ["OTHER_KEY", "SAFE_NAME"])
        self.assertEqual(environment_config_names(Path(directory) / ".env"), [])

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

    def test_persona_draft_publish_assignment_and_rollback_are_versioned(self) -> None:
        personas = self.repository.list_account_personas()
        self.assertEqual(personas[0]["persona_key"], DEFAULT_PERSONA_KEY)
        self.assertEqual(personas[0]["published_version"], 1)

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
                "signoff_name": "Maya",
            },
        }
        rendered = apply_persona_to_customer_reply("Hi Taylor,\n\nPlease send the transaction ID.\n\nBest Regards,\nSid", persona)
        self.assertIn("Thanks for contacting the billing team.", rendered)
        self.assertTrue(rendered.endswith("Best Regards,\nMaya"))

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
