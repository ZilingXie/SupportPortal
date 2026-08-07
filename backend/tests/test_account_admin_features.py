from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.repositories.ticket_repository import (
    ACCOUNT_RERUN_RESET_AI_ONLY,
    ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
    InMemoryTicketRepository,
)
from backend.services.account_admin import (
    ACCOUNT_PERSONA_PRESETS,
    ACCOUNT_PERSONA_PRESET_VERSION,
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

    def test_account_rerun_reset_deletes_ai_state_but_keeps_customer_messages(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "TK-RESET",
                "updated_at": "2026-08-03T00:00:00+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Keep this request",
                        "created_at": "2026-08-03T00:00:00+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "Delete this Account reply",
                        "source": "account_ai",
                        "meta": {"account_reply_job_id": "old-job"},
                        "created_at": "2026-08-03T00:01:00+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "Keep this engineer note",
                        "source": "engineer",
                        "created_at": "2026-08-03T00:02:00+00:00",
                    },
                ],
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "AC-RESET",
                "account_case_id": "AC-RESET",
                "client_ticket_id": "TK-RESET",
                "title": "Request",
                "question": "Keep this request",
                "automation_status": "automation",
                "customer_reply": "Delete this Account reply",
                "internal_email_payload": {"delivery_key": "old-delivery"},
                "internal_email_send_status": "sent",
                "internal_email_send_reason": "sent",
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "old-job",
                "ticket_id": "TK-RESET",
                "trigger_message_created_at": "2026-08-03T00:00:00+00:00",
                "status": "published",
                "scheduled_for": "2026-08-03T00:01:00+00:00",
                "payload": {},
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "old-job-2",
                "ticket_id": "TK-RESET",
                "trigger_message_created_at": "2026-08-03T00:00:00+00:00",
                "status": "failed",
                "scheduled_for": "2026-08-03T00:01:00+00:00",
                "payload": {},
            }
        )
        self.repository.save_account_reply_execution(
            {
                "execution_id": "reply-old-job",
                "ticket_id": "TK-RESET",
                "payload": {"content": "Delete this Account reply"},
            }
        )

        counts = self.repository.reset_account_rerun_state(
            "TK-RESET",
            reset_at="2026-08-04T00:00:00+00:00",
            rerun_job_id="rerun-1",
            reset_mode=ACCOUNT_RERUN_RESET_AI_ONLY,
        )

        self.assertEqual(
            counts,
            {
                "ai_messages_deleted": 1,
                "reply_jobs_deleted": 2,
                "reply_executions_deleted": 1,
                "customer_replies_cleared": 1,
            },
        )
        ticket = self.repository.get_ticket("TK-RESET")
        assert ticket is not None
        self.assertEqual([message["content"] for message in ticket["messages"]], [
            "Keep this request",
            "Keep this engineer note",
        ])
        self.assertEqual(self.repository.list_account_reply_executions("TK-RESET"), [])
        self.assertIsNone(self.repository.get_account_reply_job("old-job"))
        self.assertIsNone(self.repository.get_account_reply_job("old-job-2"))
        case = self.repository.get_billing_ticket("AC-RESET")
        assert case is not None
        self.assertIsNone(case["customer_reply"])
        self.assertIsNone(case["internal_email_payload"])
        self.assertIsNone(case["internal_email_send_status"])

    def test_single_case_reset_keeps_only_customer_messages_and_records_sanitized_audit(self) -> None:
        customer_messages = [
            {
                "message_id": "customer-1",
                "role": "customer",
                "content": "Private request from customer@example.com",
                "created_at": "2026-08-03T00:00:00+00:00",
                "meta": {"source": "zendesk", "sequence": 1},
            },
            {
                "message_id": "customer-2",
                "role": "user",
                "content": "Additional private details",
                "created_at": "2026-08-03T00:03:00+00:00",
                "meta": {"source": "zendesk", "sequence": 2},
            },
        ]
        self.repository.save_ticket(
            {
                "ticket_id": "12572",
                "subject": "Private subject",
                "updated_at": "2026-08-03T00:00:00+00:00",
                "messages": [
                    customer_messages[0],
                    {"role": "assistant", "content": "AI reply", "source": "account_ai"},
                    {"role": "engineer", "content": "Manual engineer reply"},
                    {"role": "internal", "content": "Internal note"},
                    {"role": "mystery", "content": "Unknown role"},
                    customer_messages[1],
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12572",
                "billing_ticket_id": "AC-12572",
                "client_ticket_id": "12572",
                "title": "Private subject",
                "route_review_status": "reviewed",
                "route": "detailed_invoice",
                "scope_label": "billing",
                "route_family": "automated",
                "execution_action": "detailed_invoice",
                "tooling_profile": "deterministic_billing_intake",
                "category": "automation",
                "subcategory": "detailed_invoice",
                "route_status": "automated",
                "automation_status": "automation",
                "automation_handler": "billing",
                "route_classification": {"route_target": "automation"},
                "automation_context": {"follow_up_count": 1},
                "missing_fields": ["amount"],
                "collected_fields": {"transaction_id": "private-value"},
                "customer_reply": "Old reply",
                "internal_email_payload": {"delivery_key": "old-delivery"},
                "internal_email_send_status": "sent",
                "internal_email_send_reason": "sent",
            }
        )
        self.repository.apply_billing_route_correction(
            billing_ticket_id="AC-12572",
            active_route={
                "route": "detailed_invoice",
                "scope_label": "billing",
                "route_family": "automated",
                "execution_action": "detailed_invoice",
                "tooling_profile": "deterministic_billing_intake",
                "category": "automation",
                "subcategory": "detailed_invoice",
                "route_status": "automated",
                "automation_handler": "billing",
                "route_classification": {},
            },
            correction={
                "client_ticket_id": "12572",
                "original_scope_label": "uncertain",
                "original_route_family": "human_review",
                "original_execution_action": "human_review_required",
                "original_tooling_profile": "manual",
                "corrected_scope_label": "billing",
                "corrected_route_family": "automated",
                "corrected_execution_action": "detailed_invoice",
                "corrected_tooling_profile": "deterministic_billing_intake",
                "first_corrected_scope_label": "billing",
                "first_corrected_route_family": "automated",
                "first_corrected_execution_action": "detailed_invoice",
                "first_corrected_tooling_profile": "deterministic_billing_intake",
                "corrector": "private-operator@example.com",
                "created_at": "2026-08-03T00:02:00+00:00",
                "updated_at": "2026-08-03T00:02:00+00:00",
            },
        )

        counts = self.repository.reset_account_rerun_state(
            "12572",
            reset_at="2026-08-04T00:00:00+00:00",
            rerun_job_id="account-rerun-test",
            reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
            audit_context={
                "actor_id": "account_ui",
                "account_case_id": "AC-12572",
                "ticket_number": "12572",
                "requested_at": "2026-08-04T00:00:00+00:00",
                "build_ref": "test-build",
            },
        )

        self.assertEqual(self.repository.get_ticket("12572")["messages"], customer_messages)
        self.assertEqual(counts["customer_messages_retained"], 2)
        self.assertEqual(counts["messages_deleted"], 4)
        self.assertEqual(
            counts["deleted_messages_by_role"],
            {"assistant": 1, "engineer": 1, "internal": 1, "unknown": 1},
        )
        account_case = self.repository.get_account_case("AC-12572")
        assert account_case is not None
        self.assertEqual(account_case["route_review_status"], "pending")
        for cleared_field in (
            "route",
            "scope_label",
            "route_family",
            "execution_action",
            "tooling_profile",
            "category",
            "subcategory",
            "automation_handler",
        ):
            self.assertIsNone(account_case[cleared_field])
        self.assertEqual(account_case["route_status"], "not_automated")
        self.assertEqual(account_case["automation_status"], "not_automated")
        self.assertEqual(account_case["route_classification"], {})
        self.assertEqual(account_case["automation_context"], {})
        self.assertEqual(account_case["missing_fields"], [])
        self.assertEqual(account_case["collected_fields"], {})
        self.assertIsNone(self.repository.get_billing_route_correction("AC-12572"))
        audit = self.repository.list_workspace_audit_events()[0]
        self.assertEqual(audit["event_type"], "account_case_full_rerun_reset")
        self.assertEqual(audit["actor_id"], "account_ui")
        self.assertEqual(audit["target_id"], "AC-12572")
        self.assertEqual(audit["payload"]["messages_deleted"], 4)
        self.assertTrue(audit["payload"]["route_correction_cleared"])
        audit_text = str(audit).lower()
        for private_value in (
            "private request",
            "additional private details",
            "private subject",
            "customer@example.com",
            "private-operator@example.com",
        ):
            self.assertNotIn(private_value, audit_text)

    def test_single_case_reset_rolls_back_when_audit_write_fails(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12573",
                "messages": [
                    {"role": "customer", "content": "Keep"},
                    {"role": "engineer", "content": "Do not delete without audit"},
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12573",
                "billing_ticket_id": "AC-12573",
                "client_ticket_id": "12573",
                "route_review_status": "reviewed",
                "customer_reply": "Existing reply",
            }
        )

        with patch.object(
            self.repository,
            "record_workspace_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.repository.reset_account_rerun_state(
                    "12573",
                    reset_at="2026-08-04T00:00:00+00:00",
                    rerun_job_id="account-rerun-test",
                    reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
                    audit_context={
                        "actor_id": "account_ui",
                        "account_case_id": "AC-12573",
                        "ticket_number": "12573",
                    },
                )

        self.assertEqual(
            [message["role"] for message in self.repository.get_ticket("12573")["messages"]],
            ["customer", "engineer"],
        )
        account_case = self.repository.get_account_case("AC-12573")
        assert account_case is not None
        self.assertEqual(account_case["route_review_status"], "reviewed")
        self.assertEqual(account_case["customer_reply"], "Existing reply")

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

    def test_route_execution_records_stage_failure_attempt_metadata(self) -> None:
        decision = SupportRouteDecision(
            scope_label="uncertain",
            route="human_review_required",
            confidence=0.0,
            reason="intent_classifier_invalid_json",
            route_family="human_review",
            execution_action="human_review_required",
            router_source="account_layered_llm",
        )
        execution = route_execution_from_decision(
            ticket_id="TK-12572",
            decision=decision,
            system_prompt=None,
            user_prompt=None,
            classification={
                "pipeline_version": ROUTER_PROMPT_VERSION,
                "route_target": "human_review",
                "route_reason_code": "intent_classifier_invalid_json",
                "stage_confidences": {"intent_classifier": 0.0},
                "stage_reason_codes": {"intent_classifier": "intent_classifier_invalid_json"},
            },
            stage_attempts={
                "intent_classifier": SimpleNamespace(
                    failure_type="invalid_json",
                    failure_source="intent_classifier",
                    attempt_count=2,
                    recovered=False,
                    model_name="gpt-test",
                    provider_name="openai",
                    raw_output_length=8,
                    raw_output_sha256="a" * 64,
                    sanitized_output_excerpt="not json",
                    attempt_failures=({"attempt": 1, "failure_type": "invalid_json"},),
                )
            },
        )

        stage = execution["stages"][0]
        self.assertEqual(stage["status"], "failed")
        self.assertEqual(stage["failure_type"], "invalid_json")
        self.assertEqual(stage["attempt_count"], 2)
        self.assertEqual(stage["output_sha256"], "a" * 64)
        self.assertEqual(stage["output_excerpt"], "not json")

    def test_routing_config_describes_stages_and_lists_supported_categories(self) -> None:
        payload = routing_config_payload()

        self.assertEqual(payload["stages"], [stage["name"] for stage in payload["stage_details"]])
        self.assertTrue(all(stage["name"] and stage["description"] for stage in payload["stage_details"]))
        self.assertEqual(
            [category["name"] for category in payload["route_categories"]],
            ["conversation", "intent", "agora", "account_billing", "automation"],
        )
        account_billing = next(
            category
            for category in payload["route_categories"]
            if category["name"] == "account_billing"
        )
        self.assertEqual(account_billing["subcategories"], ["account_suspension", "other"])
        self.assertEqual(account_billing["handler_modes"]["account_suspension"], "classification_only")
        automation = next(category for category in payload["route_categories"] if category["name"] == "automation")
        self.assertEqual(
            automation["subcategories"],
            ["fraud_account", "detailed_invoice", "enablement", "quota", "unregistered"],
        )
        self.assertIn("Intent Classifier", payload["system_prompt"])
        self.assertIn("Account & Billing Router", payload["system_prompt"])
        self.assertIn("Automation Router", payload["system_prompt"])

    def test_persona_seed_catalog_contains_the_three_published_presets(self) -> None:
        expected = {
            "sid-precise": {
                "display_name": "Sid Precise",
                "instruction": (
                    "Use a precise, composed, and professional support voice. State the current "
                    "status clearly, then explain any information the customer needs to provide "
                    "or the next step. Prefer concise, complete sentences and unambiguous wording. "
                    "Avoid casual chatter, decorative language, vague reassurance, and promises "
                    "not supported by the provided facts. Remain courteous and human; do not sound "
                    "legalistic, cold, or robotic."
                ),
            },
            "sid-bright": {
                "display_name": "Sid Bright",
                "instruction": (
                    "Use a professional, upbeat, and energetic support voice. Keep the writing "
                    "natural and concise, with positive momentum and varied sentence rhythm. "
                    "Friendly contractions are acceptable when they sound natural, but do not use "
                    "emoji, slang, exaggerated enthusiasm, excessive exclamation marks, or overly "
                    "casual language. For sensitive or serious matters, reduce the energy and use "
                    "a calm, respectful tone."
                ),
            },
            "default-support": {
                "display_name": "Sid Warm",
                "instruction": (
                    "Use a warm, considerate, and reassuring support voice. Acknowledge the "
                    "customer's request or patience naturally when supported by the provided "
                    "facts, and explain the current status and next step in a personal, caring way. "
                    "Avoid canned pleasantries, repetitive thanks or apologies, false empathy, and "
                    "promises beyond the provided facts. Remain concise and professional, "
                    "especially for sensitive matters."
                ),
            },
        }

        catalog = {preset.persona_key: preset for preset in ACCOUNT_PERSONA_PRESETS}
        personas = {persona["persona_key"]: persona for persona in self.repository.list_account_personas()}

        self.assertEqual(ACCOUNT_PERSONA_PRESET_VERSION, "automation-persona-presets-v1")
        self.assertEqual(set(catalog), set(expected))
        self.assertEqual(set(personas), set(expected))
        for key, expected_preset in expected.items():
            self.assertEqual(catalog[key].display_name, expected_preset["display_name"])
            self.assertEqual(catalog[key].content["instruction"], expected_preset["instruction"])
            self.assertEqual(catalog[key].content["opener"], "")
            self.assertEqual(catalog[key].content["signature"], "Best,\nSid\nSupport Engineer 2")
            self.assertEqual(catalog[key].seed_marker, f"Seeded {expected_preset['display_name']} preset v1")
            self.assertTrue(personas[key]["enabled"])
            self.assertEqual(personas[key]["published_version"], 1)
            self.assertEqual(len(personas[key]["versions"]), 1)
            self.assertEqual(personas[key]["versions"][0]["status"], "published")
            self.assertEqual(personas[key]["versions"][0]["created_by"], "system")
            self.assertEqual(personas[key]["versions"][0]["change_note"], catalog[key].seed_marker)
            self.assertEqual(personas[key]["versions"][0]["content"], catalog[key].content)

    def test_persona_preset_catalog_is_immutable_and_content_isolated(self) -> None:
        preset = ACCOUNT_PERSONA_PRESETS[0]
        original_content = preset.content

        for attribute, replacement in (
            ("persona_key", "changed-key"),
            ("display_name", "Changed name"),
            ("seed_marker", "Changed marker"),
            ("instruction", "Changed instruction"),
        ):
            with self.subTest(attribute=attribute):
                with self.assertRaises(FrozenInstanceError):
                    setattr(preset, attribute, replacement)

        self.assertEqual(set(original_content), {"instruction", "opener", "signature"})
        original_content["instruction"] = "Mutated instruction"
        self.assertNotEqual(preset.content["instruction"], "Mutated instruction")

        reseeded = {item["persona_key"]: item for item in InMemoryTicketRepository().list_account_personas()}
        self.assertEqual(
            reseeded[preset.persona_key]["versions"][0]["content"],
            preset.content,
        )

    def test_persona_draft_publish_assignment_and_rollback_are_versioned(self) -> None:
        personas = {item["persona_key"]: item for item in self.repository.list_account_personas()}
        default_persona = personas[DEFAULT_PERSONA_KEY]
        self.assertEqual(default_persona["display_name"], "Sid Warm")
        self.assertEqual(default_persona["published_version"], 1)
        self.assertEqual(default_persona["versions"][0]["content"], DEFAULT_PERSONA_CONTENT)
        self.assertEqual(
            default_persona["versions"][0]["content"]["signature"],
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
        assigned_before_publish = self.repository.resolve_account_persona("TK-1")

        published = self.repository.publish_account_persona_version(
            DEFAULT_PERSONA_KEY, draft["version"], actor_id="admin-1", published_at="2026-07-21T02:00:00+00:00"
        )
        self.assertEqual(published["status"], "published")
        personas_after_publish = {
            item["persona_key"]: item for item in self.repository.list_account_personas()
        }
        self.assertEqual(personas_after_publish[DEFAULT_PERSONA_KEY]["published_version"], 2)
        self.assertEqual(
            [item["status"] for item in personas_after_publish[DEFAULT_PERSONA_KEY]["versions"]],
            ["superseded", "published"],
        )
        self.assertEqual(self.repository.resolve_account_persona("TK-1"), assigned_before_publish)
        self.assertEqual(
            {
                key: item["published_version"]
                for key, item in personas_after_publish.items()
                if key != DEFAULT_PERSONA_KEY
            },
            {"sid-bright": 1, "sid-precise": 1},
        )

        rollback = self.repository.rollback_account_persona_version(
            DEFAULT_PERSONA_KEY, 1, actor_id="admin-1", published_at="2026-07-21T03:00:00+00:00"
        )
        self.assertEqual(rollback["version"], 3)
        self.assertEqual(rollback["status"], "published")
        personas_after_rollback = {
            item["persona_key"]: item for item in self.repository.list_account_personas()
        }
        self.assertEqual(
            [item["version"] for item in personas_after_rollback[DEFAULT_PERSONA_KEY]["versions"]],
            [1, 2, 3],
        )
        self.assertEqual(self.repository.resolve_account_persona("TK-1"), assigned_before_publish)

    def test_last_enabled_persona_cannot_be_disabled(self) -> None:
        self.repository.set_account_persona_enabled("sid-bright", False)
        self.repository.set_account_persona_enabled("sid-precise", False)
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
