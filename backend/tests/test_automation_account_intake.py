"""Unit tests for the /automation/production parity intake (p2-109 Phase B)."""

import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import backend.services.automation_account_intake as intake_module
from backend.services.automation_account_intake import run_production_account_intake
from backend.services.enablement_archer_executor import ArcherEnablementResult


class _FakeRepository:
    def __init__(self) -> None:
        self.saved_tickets: list[dict] = []
        self.saved_cases: list[dict] = []
        self.saved_route_executions: list[dict] = []
        self.saved_engineer_cases: list[dict] = []
        self.engineer_case_saves: list[tuple] = []
        self.comment_sync_baselines: list[dict] = []
        self.events: list[tuple] = []

    def save_ticket(self, ticket, new_messages=None):
        self.saved_tickets.append(dict(ticket))

    def resolve_account_persona(self, ticket_id):
        return None

    def save_account_case(self, case):
        self.saved_cases.append(dict(case))

    def save_account_route_execution(self, execution):
        self.saved_route_executions.append(dict(execution))

    def save_engineer_case(self, engineer_case, new_messages=None, slack_events=None):
        self.saved_engineer_cases.append(dict(engineer_case))
        self.engineer_case_saves.append((list(new_messages or []), list(slack_events or [])))

    def sync_account_case_comments(self, *, ticket_id, account_case_id, snapshot, synced_at):
        self.comment_sync_baselines.append(
            {
                "ticket_id": ticket_id,
                "account_case_id": account_case_id,
                "comments_revision": str(getattr(snapshot, "comments_revision", "") or ""),
                "synced_at": synced_at,
            }
        )

    def record_event(self, ticket_id, event_type, payload):
        self.events.append((ticket_id, event_type, payload))


def _extraction(*, missing=None, collected=None, requires_human_review=False, status="ok"):
    return NS(
        missing_fields=list(missing or []),
        collected_fields=dict(collected or {}),
        requires_human_review=requires_human_review,
        status=status,
        prompt_snapshot={},
        audit_payload=lambda: {"status": status},
        follow_up=None,
    )


def _automation_result(
    *,
    missing=None,
    collected=None,
    email=None,
    requires_human_review=False,
    extraction=None,
    follow_up_count=0,
    proceed_with_missing_fields=False,
):
    resolved = extraction or _extraction(missing=missing, collected=collected)
    return NS(
        missing_fields=list(missing or []),
        collected_fields=dict(collected or {}),
        internal_email=dict(email) if email else None,
        requires_human_review=requires_human_review,
        field_extraction=resolved,
        extraction=resolved,
        customer_reply="",
        prompt_snapshots={},
        follow_up_count=follow_up_count,
        proceed_with_missing_fields=proceed_with_missing_fields,
    )


DECISION = {
    "scope_label": "account",
    "route_family": "automated",
    "execution_action": "fraud_account",
    "reason": "matched",
    "confidence": 0.9,
    "router_source": "llm",
    "semantic_intent": None,
    "not_automated_reason": None,
    "policy_decision": None,
}


class AutomationAccountIntakeTest(unittest.TestCase):
    def _run(self, repository, **kwargs):
        kwargs.setdefault("repository", repository)
        kwargs.setdefault("subject", "Fraud report")
        kwargs.setdefault("question", "please verify")
        kwargs.setdefault("ticket_id", "123")
        kwargs.setdefault("zendesk_ticket_id", "123")
        kwargs.setdefault("customer_email", "c@example.com")
        kwargs.setdefault("customer_name", "Customer")
        kwargs.setdefault("source", None)
        kwargs.setdefault("route_decision", dict(DECISION))
        kwargs.setdefault("route_classification", {"automation_handler": "billing"})
        return asyncio.run(run_production_account_intake(**kwargs))

    def _base_patches(self, **overrides):
        patches = {
            "build_billing_automation_result": lambda **kw: _automation_result(missing=["account_type"], collected={"contact_email": "c@example.com"}),
            "build_account_verification_automation_result": lambda **kw: _automation_result(missing=["account_type"], collected={"contact_email": "c@example.com"}),
            "extract_enablement_fields": lambda **kw: _extraction(collected={"project_id": "p1"}),
            "build_enablement_automation_result_from_fields": lambda **kw: _automation_result(collected={"project_id": "p1"}, email={"subject": "Enablement"}),
            "extract_account_suspension_fields": lambda **kw: _extraction(collected={"reason": "x"}),
            "ensure_production_automation_ownership": lambda *a, **kw: NS(
                fail_closed=False, state="assigned", assignee_id="a1", group_id="g1",
                failure_code=None, failure_category=None, zendesk_status_code=None,
                failure_detail=None, blocking_comment_id=None,
            ),
            "ownership_gate_eligible": lambda case: True,
            "escalate_account_case_to_human_review": lambda **kw: NS(status="escalated"),
            "notify_account_failure": lambda **kw: {"status": "alerted"},
            "deliver_account_internal_email_async": None,
            "account_reply_delay_seconds_for_profile": lambda profile: 0,
            "create_account_reply_job": lambda repository, **kw: {"job_id": "job-1", **kw},
            "send_billing_internal_email": lambda payload: {"status": "sent", "reason": ""},
            "send_enablement_internal_email": lambda payload: {"status": "sent", "reason": ""},
            "build_automation_reply_facts": lambda **kw: {"behavior": kw.get("behavior")},
            "build_account_automation_reply_facts": lambda **kw: {"handler": kw.get("handler")},
            "route_execution_from_decision": lambda **kw: {"ticket_id": kw.get("ticket_id")},
            "build_new_engineer_case": lambda ticket, **kw: {"engineer_case_id": kw.get("engineer_case_id")},
            "derive_engineer_case_title": lambda ticket: "Engineer title",
            "build_engineer_case_opened_event": lambda **kw: {"event": "opened"},
            "EngineerAssignmentService": lambda repository: NS(dispatch_case=lambda case_id, reason: None),
        }
        patches.update(overrides)

        async def _fake_deliver(repository, *, account_case_id, payload, sender, **kw):
            status, reason = await sender(payload)
            return NS(succeeded=status == "sent", status=status, reason=reason, payload=payload)

        if patches["deliver_account_internal_email_async"] is None:
            patches["deliver_account_internal_email_async"] = _fake_deliver

        class _Ctx:
            def __enter__(self):
                self._patchers = [patch.object(intake_module, name, value) for name, value in patches.items()]
                for p in self._patchers:
                    p.start()
                return self

            def __exit__(self, *exc):
                for p in self._patchers:
                    p.stop()

        return _Ctx()

    def test_fraud_missing_fields_creates_follow_up_reply_job_only(self):
        repository = _FakeRepository()
        with self._base_patches():
            outcome = self._run(repository)
        self.assertEqual(outcome["response_status"], "automation")
        self.assertIsNotNone(outcome["reply_job"])
        self.assertEqual(outcome["reply_job"]["asked_field_keys"], ["account_type"])
        self.assertEqual(outcome["internal_email_send_status"], "not_ready")
        self.assertEqual(repository.saved_engineer_cases, [])

    def test_fraud_missing_fields_persists_follow_up_context(self):
        repository = _FakeRepository()
        incomplete = _automation_result(
            missing=["office_address", "contact_number"],
            collected={"account_type": "company"},
            follow_up_count=1,
        )
        with self._base_patches(
            build_account_verification_automation_result=lambda **kw: incomplete,
        ):
            outcome = self._run(repository)

        self.assertEqual(
            outcome["account_case"]["automation_context"],
            {
                "handler": "fraud_account",
                "extractor_version": None,
                "extraction_status": "ok",
                "follow_up_count": 0,
                "follow_up_scheduled": True,
                "proceed_with_missing_fields": False,
            },
        )

    def test_fraud_complete_fields_sends_email_and_confirmation_job(self):
        repository = _FakeRepository()
        complete = _automation_result(collected={"account_type": "company"}, email={"subject": "Fraud handoff", "delivery_key": "dk-1"})
        with self._base_patches(
            build_billing_automation_result=lambda **kw: complete,
            build_account_verification_automation_result=lambda **kw: complete,
        ):
            outcome = self._run(repository)
        self.assertEqual(outcome["response_status"], "automation")
        self.assertEqual(outcome["internal_email_send_status"], "sent")
        self.assertEqual(outcome["reply_job"]["reply_intent"], "fraud_handoff_confirmation")
        self.assertEqual(outcome["reply_job"]["automation_delivery_key"], "dk-1")
        self.assertFalse(outcome["reply_job"]["close_after_publish"])

    def test_production_suspension_persists_key_before_email_claim_and_single_handoff_job(self):
        repository = _FakeRepository()
        order = []

        async def deliver(repository, *, account_case_id, payload, sender, **_kwargs):
            persisted = repository.saved_cases[-1]
            self.assertEqual(
                persisted["internal_email_payload"]["delivery_key"],
                payload["delivery_key"],
            )
            order.append("claim")
            status, reason = await sender(payload)
            return NS(
                succeeded=status == "sent",
                status=status,
                reason=reason,
                payload=payload,
            )

        def send(payload):
            order.append("email")
            return {"status": "sent", "reason": ""}

        def create_job(repository, **kwargs):
            order.append("job")
            return {"job_id": "job-suspension", **kwargs}

        with self._base_patches(
            deliver_account_internal_email_async=deliver,
            send_billing_internal_email=send,
            create_account_reply_job=create_job,
        ):
            outcome = self._run(
                repository,
                route_decision={**DECISION, "execution_action": "account_suspension"},
                route_classification={"automation_handler": "account_suspension"},
            )
        self.assertEqual(outcome["response_status"], "automation")
        self.assertEqual(order, ["claim", "email", "job"])
        self.assertEqual(
            outcome["reply_job"]["reply_intent"],
            "account_suspension_handoff_and_close",
        )
        self.assertEqual(
            outcome["reply_job"]["reply_facts"]["reply_intent"],
            "account_suspension_handoff_and_close",
        )
        self.assertFalse(outcome["reply_job"]["close_after_publish"])
        self.assertEqual(outcome["internal_email_send_status"], "sent")
        workflow = outcome["account_case"]["automation_context"][
            "account_suspension_contact_workflow"
        ]
        self.assertEqual(workflow["state"], "closing_reply_pending")
        self.assertEqual(workflow["intake_mode"], "direct_handoff")
        self.assertEqual(workflow["confirmed_email"], "c@example.com")
        self.assertEqual(workflow["confirmed_email_source"], "ticket_email")
        self.assertEqual(workflow["closing_reply_job_id"], "job-suspension")
        self.assertTrue(workflow["handoff_delivery_key"])

    def test_preproduction_suspension_keeps_contact_confirmation_stage(self):
        repository = _FakeRepository()
        with self._base_patches():
            outcome = self._run(
                repository,
                processing_profile="preproduction",
                route_decision={**DECISION, "execution_action": "account_suspension"},
                route_classification={"automation_handler": "account_suspension"},
            )
        self.assertEqual(outcome["response_status"], "automation")
        self.assertEqual(
            outcome["reply_job"]["reply_intent"],
            "account_suspension_contact_confirmation_request",
        )
        self.assertEqual(outcome["internal_email_send_status"], "not_applicable")
        workflow = outcome["account_case"]["automation_context"][
            "account_suspension_contact_workflow"
        ]
        self.assertEqual(workflow["state"], "awaiting_contact_confirmation")
        self.assertNotIn("intake_mode", workflow)

    def test_production_suspension_invalid_email_fails_closed_without_side_effects(self):
        repository = _FakeRepository()
        sent = []
        jobs = []
        with self._base_patches(
            send_billing_internal_email=lambda payload: sent.append(payload),
            create_account_reply_job=lambda repository, **kwargs: jobs.append(kwargs),
        ):
            outcome = self._run(
                repository,
                customer_email="not-an-email",
                route_decision={**DECISION, "execution_action": "account_suspension"},
                route_classification={"automation_handler": "account_suspension"},
            )
        self.assertEqual(outcome["response_status"], "human_review_required")
        self.assertEqual(outcome["execution_reason_code"], "suspension_missing_customer_email")
        self.assertIsNone(outcome["reply_job"])
        self.assertEqual(sent, [])
        self.assertEqual(jobs, [])
        workflow = outcome["account_case"]["automation_context"][
            "account_suspension_contact_workflow"
        ]
        self.assertEqual(workflow["state"], "human_review_required")

    def test_production_suspension_email_failure_or_unknown_creates_no_reply_job(self):
        for status in ("failed", "outcome_unknown"):
            with self.subTest(status=status):
                repository = _FakeRepository()
                jobs = []
                with self._base_patches(
                    send_billing_internal_email=lambda payload, status=status: {
                        "status": status,
                        "reason": "provider unavailable",
                    },
                    create_account_reply_job=lambda repository, **kwargs: jobs.append(kwargs),
                ):
                    outcome = self._run(
                        repository,
                        route_decision={**DECISION, "execution_action": "account_suspension"},
                        route_classification={"automation_handler": "account_suspension"},
                    )
                self.assertEqual(outcome["response_status"], "human_review_required")
                self.assertIsNone(outcome["reply_job"])
                self.assertEqual(jobs, [])
                workflow = outcome["account_case"]["automation_context"][
                    "account_suspension_contact_workflow"
                ]
                self.assertEqual(workflow["state"], "human_review_required")
                self.assertEqual(workflow["failure_reason"], "provider unavailable")

    def test_production_suspension_reply_job_failure_marks_workflow_for_review(self):
        repository = _FakeRepository()

        def fail_job(repository, **kwargs):
            raise RuntimeError("queue unavailable")

        with self._base_patches(create_account_reply_job=fail_job):
            outcome = self._run(
                repository,
                route_decision={**DECISION, "execution_action": "account_suspension"},
                route_classification={"automation_handler": "account_suspension"},
            )
        self.assertEqual(outcome["response_status"], "human_review_required")
        self.assertIsNone(outcome["reply_job"])
        workflow = outcome["account_case"]["automation_context"][
            "account_suspension_contact_workflow"
        ]
        self.assertEqual(workflow["state"], "human_review_required")
        self.assertEqual(
            workflow["failure_reason"],
            "account_suspension_closing_reply_job_failed",
        )

    def _run_complete_enablement(self, repository, *, archer_result, **patches):
        app_id = "0123456789abcdef0123456789abcdef"
        complete = _automation_result(
            collected={
                "app_id": app_id,
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
            email={
                "subject": "Enablement",
                "delivery_key": "enablement:AC-123:v1",
                "body": "Enablement request",
                "body_html": "<p>Enablement request</p>",
            },
        )
        enablement_patches = {
            "extract_enablement_fields": lambda **kw: complete.extraction,
            "build_enablement_automation_result_from_fields": lambda **kw: complete,
            "execute_enablement_archer": lambda value: archer_result,
        }
        enablement_patches.update(patches)
        with self._base_patches(**enablement_patches):
            return self._run(
                repository,
                subject="Enable Media Relay",
                question=f"Please enable Media Relay for {app_id}",
                route_decision={**DECISION, "execution_action": "enablement"},
                route_classification={"automation_handler": "enablement"},
            )

    def test_enablement_archer_enabled_closes_via_persona_without_email(self):
        repository = _FakeRepository()
        sent = []
        outcome = self._run_complete_enablement(
            repository,
            archer_result=ArcherEnablementResult("enabled", "开启结果：成功"),
            send_enablement_internal_email=lambda payload: sent.append(payload),
        )
        self.assertEqual(outcome["reply_job"]["reply_intent"], "enablement_archer_enabled")
        self.assertTrue(outcome["reply_job"]["close_after_publish"])
        self.assertEqual(outcome["internal_email_send_status"], "not_applicable")
        self.assertEqual(sent, [])
        self.assertIsNone(outcome["account_case"]["internal_email_payload"])
        event = next(payload for _, event, payload in repository.events if event == "enablement_archer_result")
        self.assertNotIn("0123456789abcdef0123456789abcdef", str(event))

    def test_enablement_archer_recoverable_outcomes_clear_rejected_appid(self):
        for archer_outcome, expected_intent in (
            ("appid_invalid", "enablement_appid_invalid"),
            ("project_not_found", "enablement_appid_not_found"),
        ):
            with self.subTest(archer_outcome=archer_outcome):
                repository = _FakeRepository()
                outcome = self._run_complete_enablement(
                    repository,
                    archer_result=ArcherEnablementResult(archer_outcome, archer_outcome),
                )
                self.assertEqual(outcome["reply_job"]["reply_intent"], expected_intent)
                self.assertFalse(outcome["reply_job"]["close_after_publish"])
                self.assertEqual(outcome["reply_job"]["asked_field_keys"], [])
                self.assertEqual(outcome["account_case"]["missing_fields"], ["app_id"])
                self.assertNotIn("app_id", outcome["account_case"]["collected_fields"])
                self.assertEqual(
                    outcome["account_case"]["route_classification"]["handler_binding_status"],
                    "active",
                )

    def test_enablement_archer_failure_escalates_before_fallback_email(self):
        repository = _FakeRepository()
        order = []

        def escalate(**kwargs):
            order.append("escalate")
            kwargs["account_case"]["automation_status"] = "human_review_required"
            return NS(status="completed")

        def send(payload):
            order.append("email")
            self.assertIn("sanitized Archer failure", payload["body"])
            return {"status": "outcome_unknown", "reason": "provider timed out"}

        outcome = self._run_complete_enablement(
            repository,
            archer_result=ArcherEnablementResult("enable_failed", "sanitized Archer failure"),
            escalate_account_case_to_human_review=escalate,
            send_enablement_internal_email=send,
        )
        self.assertEqual(order, ["escalate", "email"])
        self.assertIsNone(outcome["reply_job"])
        self.assertEqual(outcome["account_case"]["automation_status"], "human_review_required")
        self.assertEqual(outcome["internal_email_send_status"], "delivery_unknown")

    def test_enablement_ownership_failure_never_calls_archer(self):
        repository = _FakeRepository()
        called = []
        outcome = self._run_complete_enablement(
            repository,
            archer_result=ArcherEnablementResult("enabled", "success"),
            ensure_production_automation_ownership=lambda *a, **kw: NS(
                fail_closed=True,
                state="blocked",
                assignee_id=None,
                group_id=None,
                failure_code="ownership_conflict",
                failure_category="conflict",
                zendesk_status_code=200,
                failure_detail="conflict",
                blocking_comment_id=None,
            ),
            execute_enablement_archer=lambda app_id: called.append(app_id),
        )
        self.assertEqual(called, [])
        self.assertEqual(outcome["response_status"], "human_review_required")

    def test_extraction_failure_escalates_to_human_queue(self):
        from backend.services.account_verification_field_extractor import AccountVerificationFieldExtraction

        repository = _FakeRepository()
        escalated = []
        failing = AccountVerificationFieldExtraction(status="ambiguous", collected_fields={"contact_email": "c@example.com"})
        with self._base_patches(
            build_account_verification_automation_result=lambda **kw: _automation_result(
                requires_human_review=True,
                extraction=failing,
            ),
            escalate_account_case_to_human_review=lambda **kw: escalated.append(kw) or NS(status="escalated"),
        ):
            outcome = self._run(repository)
        self.assertEqual(outcome["response_status"], "human_review_required")
        self.assertTrue(escalated)
        self.assertEqual(escalated[0]["failure_stage"], "field_extraction")

    def test_not_automated_creates_engineer_case_and_dispatch(self):
        repository = _FakeRepository()
        with self._base_patches():
            outcome = self._run(
                repository,
                route_decision={**DECISION, "route_family": "billing_review", "execution_action": "human_review_required", "not_automated_reason": "outside_scope"},
                route_classification={},
            )
        self.assertEqual(outcome["response_status"], "not_automated")
        self.assertEqual(outcome["engineer_case_id"], "123-1")
        self.assertEqual(len(repository.saved_engineer_cases), 1)
        self.assertEqual(repository.saved_engineer_cases[0]["thread_id"], "123-1-round-1")

    def test_not_automated_opening_round_persists_messages_and_thread_event(self):
        repository = _FakeRepository()
        with self._base_patches():
            outcome = self._run(
                repository,
                route_decision={**DECISION, "route_family": "billing_review", "execution_action": "human_review_required", "not_automated_reason": "outside_scope"},
                route_classification={},
            )
        self.assertEqual(outcome["response_status"], "not_automated")
        self.assertEqual(len(repository.engineer_case_saves), 1)
        new_messages, slack_events = repository.engineer_case_saves[0]
        self.assertTrue(
            any(str(message.get("role") or "") == "engineer_ai" for message in new_messages)
        )
        event_types = [str(event.get("event_type") or "") for event in slack_events]
        self.assertTrue(any(event.get("event") == "opened" for event in slack_events))
        self.assertIn("engineer_ai_response", event_types)
        opening_events = [event for event in slack_events if str(event.get("event_type")) == "engineer_ai_response"]
        self.assertTrue(str(opening_events[0].get("message_text") or "").strip())
        self.assertEqual(len(repository.comment_sync_baselines), 1)
        self.assertTrue(str(repository.comment_sync_baselines[0]["comments_revision"] or "").strip())

    def test_ownership_gate_fail_closed_escalates_and_skips_reply(self):
        repository = _FakeRepository()
        escalated = []
        with self._base_patches(
            ensure_production_automation_ownership=lambda *a, **kw: NS(
                fail_closed=True, state="blocked", assignee_id=None, group_id=None,
                failure_code="ownership_conflict", failure_category="conflict", zendesk_status_code=200,
                failure_detail="agent conflict", blocking_comment_id=None,
            ),
            escalate_account_case_to_human_review=lambda **kw: escalated.append(kw) or NS(status="escalated"),
        ):
            outcome = self._run(repository)
        self.assertEqual(outcome["response_status"], "human_review_required")
        self.assertEqual(outcome["execution_reason_code"], "zendesk_ownership_gate_failed")
        self.assertIsNone(outcome["reply_job"])
        self.assertTrue(escalated)
        self.assertEqual(escalated[-1]["failure_stage"], "ownership_gate")

    def test_ticket_created_accepts_ecs_route_stage_attempt_list(self):
        from backend.services.account_admin import route_execution_from_decision

        repository = _FakeRepository()
        route_decision = {
            **DECISION,
            "stage_attempts": ["intent_classifier"],
        }
        classification = {
            "automation_handler": "billing",
            "pipeline_version": "account-layered-router-v10",
            "route_target": "fraud_account",
            "route_reason_code": "matched",
            "stage_confidences": {"intent_classifier": 0.9},
            "stage_reason_codes": {"intent_classifier": "matched"},
            "stage_attempt_counts": {"intent_classifier": 1},
        }
        with self._base_patches(route_execution_from_decision=route_execution_from_decision):
            outcome = self._run(
                repository,
                route_decision=route_decision,
                route_classification=classification,
            )

        self.assertEqual(outcome["response_status"], "automation")
        self.assertEqual(repository.saved_route_executions[0]["stages"][0]["name"], "intent_classifier")


if __name__ == "__main__":
    unittest.main()
