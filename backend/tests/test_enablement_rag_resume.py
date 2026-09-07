"""Multi-turn ECS regression, with no provider or customer-side effects."""

import asyncio
from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import Mock, patch

from backend.services import automation_account_reply_sync as reply
from backend.services import automation_account_intake as intake
from backend.services.account_ai_execution import AccountProcessingFailure
from backend.services.account_failure_alerts import notify_account_failure
from backend.services.enablement_field_extractor import EnablementFieldExtraction


def route(action):
    return {
        "route": action, "execution_action": action,
        "route_family": "rag_product_support" if action == "rag" else "automated",
        "scope_label": "support", "reason": "test", "confidence": 1.0,
        "matched_signals": [], "semantic_intent": None,
        "automation_eligibility": "eligible", "policy_decision": "test",
        "not_automated_reason": None, "risk_flags": [], "evidence_spans": [],
        "router_source": "test", "classification": {"intent_class": "agora"},
        "intent_router_confidence_threshold": 0.7,
    }


class EnablementRagResumeTest(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fields = {"requested_feature": "media_relay", "requested_feature_label": "Media Relay"}
        self.case = {
            "account_case_id": "test-case", "billing_ticket_id": "test-case",
            "client_ticket_id": "123", "zendesk_ticket_id": "123",
            "processing_profile": "production", "automation_status": "automation",
            "automation_handler": "enablement", "execution_action": "enablement",
            "route": "enablement", "route_family": "automated",
            "route_classification": {"handler_binding_status": "active"},
            "collected_fields": dict(self.fields), "missing_fields": ["app_id"],
            "internal_email_send_status": "not_ready",
            "automation_context": {"zendesk_ownership": {
                "state": "assigned", "source_group_id": "original-group",
            }},
        }
        self.ticket = {"ticket_id": "123", "status": "open", "subject": "Enable Media Relay",
                       "customer_id": "synthetic@example.com", "messages": [
                           {"role": "customer", "content": "Enable Media Relay", "created_at": "initial"},
                           {"role": "assistant", "content": "Please share your App ID.",
                            "meta": {"asked_field_keys": ["app_id"]}},
                       ]}
        self.ledger = {}
        self.repo = Mock()
        self.repo.get_account_case.side_effect = lambda _: deepcopy(self.case)
        self.repo.get_ticket.side_effect = lambda _: deepcopy(self.ticket)
        self.repo.save_account_case.side_effect = self.save_case
        self.repo.save_ticket.side_effect = self.save_ticket
        self.repo.begin_idempotent_request.side_effect = self.claim
        self.repo.complete_idempotent_request.side_effect = self.complete
        self.repo.fail_idempotent_request.side_effect = self.complete
        self.mail = Mock()
        self.stack.enter_context(patch.object(intake, "notify_account_failure", side_effect=lambda **kw:
                                             notify_account_failure(**kw, mail_sender=self.mail)))
        self.stack.enter_context(patch.object(reply, "_apply_ownership_gate", return_value=True))
        self.extract = self.stack.enter_context(patch.object(intake, "extract_enablement_fields", side_effect=self.extract_fields))
        self.archer = self.stack.enter_context(patch.object(intake, "execute_enablement_archer", side_effect=lambda app_id:
            NS(outcome="enabled" if len(app_id) == 32 else "appid_invalid", detail="synthetic")))
        self.rag = self.stack.enter_context(patch.object(reply, "try_rag_fallback_answer",
            return_value=NS(kind="answer", answer="Read the project settings.", references=("https://docs.agora.io",))))
        self.create = self.stack.enter_context(patch.object(reply, "_create_reply_job", side_effect=self.create_job))
        self.stack.enter_context(patch.object(intake, "_create_reply_job", side_effect=self.create_job))
        self.note = self.stack.enter_context(patch("backend.services.account_human_review_escalation._deliver_internal_note",
            return_value=("sent", "test-note", None)))
        self.queue = self.stack.enter_context(patch("backend.services.account_human_review_escalation.route_ticket_back_to_queue",
            return_value=NS(status="queued")))
        self.jobs = []

    def save_case(self, case):
        self.case = deepcopy(case)

    def save_ticket(self, ticket, **kwargs):
        self.ticket = deepcopy(ticket)

    def claim(self, scope, key, **kwargs):
        if (scope, key) in self.ledger:
            return {"created": False, "response_payload": self.ledger[scope, key]}
        self.ledger[scope, key] = {}
        return {"created": True}

    def complete(self, scope, key, response_payload, **kwargs):
        self.ledger[scope, key] = response_payload

    def create_job(self, **kwargs):
        self.jobs.append(kwargs)
        return {"job_id": f"job-{len(self.jobs)}", "status": "queued", "payload": {}}

    def extract_fields(self, **kwargs):
        current = kwargs["customer_messages"][-1]
        fields = dict(kwargs["existing_fields"])
        if current["content"].startswith("try: "):
            fields["app_id"] = current["content"].split(": ", 1)[1]
        return EnablementFieldExtraction(
            status="complete" if fields.get("app_id") else "missing",
            collected_fields=fields, missing_fields=[] if fields.get("app_id") else ["app_id"],
            follow_up="Please share the App ID.", grounding_status="passed",
            source_message_ids={"app_id": current["created_at"]} if fields.get("app_id") else {},
            source_quotes={"app_id": current["content"]}, reason="do not persist free text",
        )

    def turn(self, message, action="enablement", comment_id=None):
        return asyncio.run(reply._process_account_customer_reply_impl(
            repository=self.repo, billing_ticket_id="test-case", message=message,
            source="test", message_source_id=comment_id or f"c-{len(self.ticket['messages'])}",
            precomputed_route=route(action), persona_assignment={"persona_key": "test"},
        ))

    def test_full_question_invalid_question_corrected_sequence(self):
        self.turn("What is App ID?", "rag")
        self.assertEqual(self.case["execution_action"], "enablement")
        self.assertEqual(self.case["collected_fields"], self.fields)
        self.turn("try: " + "a" * 33)
        self.assertEqual(self.case["automation_context"]["enablement_archer"]["outcome"], "appid_invalid")
        for question in ("Where can I find it?", "Which project settings?"):
            self.turn(question, "rag")
            self.assertEqual(self.case["route_classification"]["handler_binding_status"], "active")
            self.assertEqual(self.case["automation_context"]["zendesk_ownership"]["source_group_id"], "original-group")
        self.turn("try: " + "b" * 32, comment_id="corrected")
        self.assertEqual([c.args[0] for c in self.archer.call_args_list], ["a" * 33, "b" * 32])
        context = self.extract.call_args.kwargs["automation_context"]
        self.assertEqual(context["evidence_message_ids"], [context["current_message_id"]])
        self.assertEqual(len(context["conversation"]), 7)
        self.assertNotIn("app_id", self.extract.call_args.kwargs["existing_fields"])
        self.assertEqual(self.case["route_classification"]["handler_binding_status"], "completed")
        self.assertEqual(self.rag.call_count, 3)
        self.turn("try: " + "b" * 32, comment_id="corrected")
        self.assertEqual(self.archer.call_count, 2)
        self.mail.assert_not_called()
        self.assertEqual(self.jobs[0]["reply_intent"], "rag_fallback_answer")
        diagnostic = self.case["route_classification"]["field_extraction"]
        self.assertNotIn("source_quotes", diagnostic)
        self.assertNotIn("reason", diagnostic)
        self.assertNotIn("b" * 32, str(diagnostic))
        self.assertTrue(any(c.args[0]["final_route"] == "rag" and
                            c.args[0]["classification"].get("retained_automation_handler") == "enablement"
                            for c in self.repo.save_account_route_execution.call_args_list))

    def test_full_sequence_with_real_extractor_and_grounding(self):
        from backend.services.enablement_field_extractor import extract_enablement_fields

        def real_extraction(**kwargs):
            context = kwargs["automation_context"]
            current = context["conversation"][-1]
            fields = {"requested_feature": {
                "value": "media_relay", "original_label": "Media Relay",
                "source_message_id": "old-source", "source_quote": "Media Relay", "confidence": 0.99,
            }}
            if current["content"].startswith("try: "):
                value = current["content"].split(": ", 1)[1]
                fields["app_id"] = {"value": value, "source_message_id": current["message_id"],
                                    "source_quote": value, "confidence": 0.99}
            payload = {"status": "complete" if "app_id" in fields else "missing", "fields": fields,
                       "missing_fields": [] if "app_id" in fields else ["app_id"],
                       "follow_up": "Please provide the App ID."}
            result = extract_enablement_fields(**kwargs, invoke=lambda **_: deepcopy(payload))
            self.assertFalse(result.requires_human_review, result.audit_payload())
            return result

        self.extract.side_effect = real_extraction
        self.test_full_question_invalid_question_corrected_sequence()

    def test_extraction_failure_never_falls_through_to_rag(self):
        self.extract.side_effect = None
        self.extract.return_value = EnablementFieldExtraction(
            status="ambiguous", collected_fields={}, ambiguous_fields=["app_id"],
            failure_type="grounding_failed", grounding_reason_code="value_mismatch",
            reason="secret diagnostic", source_quotes={"app_id": "sensitive value"})
        result = self.turn("try: conflicting candidates")
        self.assertEqual(result["execution_reason_code"], "enablement_field_extraction_ambiguous")
        self.assertEqual(result["automation_status"], "human_review_required")
        self.assertEqual(result["alert_status"], "sent")
        self.rag.assert_not_called()
        self.archer.assert_not_called()
        self.assertEqual(self.jobs, [])
        self.assertNotIn("secret diagnostic", str(self.repo.record_event.call_args_list))
        self.mail.assert_called_once()
        self.queue.assert_called_once()
        self.turn("another comment")
        self.mail.assert_called_once()

    def test_model_failure_preserves_code_without_rag(self):
        self.extract.side_effect = AccountProcessingFailure("account_ai_invocation_exhausted", "private response")
        result = self.turn("try: a new ID")
        self.assertEqual(result["execution_reason_code"], "enablement_field_extraction_uncertain")
        self.assertEqual(result["route_classification"]["field_extraction"]["failure_type"], "account_ai_invocation_exhausted")
        self.assertNotIn("private response", str(result))
        self.rag.assert_not_called()
        self.archer.assert_not_called()

    def test_rag_escalation_alert_and_original_queue(self):
        self.rag.return_value = NS(kind="escalate", reason="insufficient_evidence")
        result = self.turn("Where is it?", "rag")
        self.assertEqual(result["execution_reason_code"], "reply_rag_fallback_escalation")
        self.mail.assert_called_once()
        self.queue.assert_called_once_with(ticket_id="123", source_group_id="original-group")
        self.assertEqual(self.jobs, [])
        self.assertIn("Customer context: Where is it?", self.note.call_args.kwargs["body"])
        self.assertEqual(self.note.call_args.kwargs["incident_id"], result["failure_incident_id"])

    def test_alert_failure_does_not_undo_human_handoff(self):
        self.mail.side_effect = RuntimeError("synthetic delivery failure")
        self.rag.return_value = NS(kind="escalate", reason="insufficient_evidence")
        result = self.turn("Where is it?", "rag")
        self.assertEqual(result["alert_status"], "delivery_failed")
        self.assertEqual(result["automation_status"], "human_review_required")
        self.assertEqual(result["automation_context"]["human_review_escalation"]["handoff_status"], "queued")
        self.assertEqual(self.jobs, [])

    def test_solved_case_is_inert(self):
        self.ticket["status"] = "solved"
        self.turn("try: " + "b" * 32)
        self.extract.assert_not_called()
        self.archer.assert_not_called()
        self.rag.assert_not_called()
        self.mail.assert_not_called()

    def test_uncertain_grounding_preserves_non_content_diagnostics(self):
        self.extract.side_effect = None
        self.extract.return_value = EnablementFieldExtraction(
            status="uncertain", collected_fields={}, failure_type="grounding_failed",
            grounding_reason_code="quote_mismatch", grounding_status="failed")
        result = self.turn("try: replacement")
        self.assertEqual(result["execution_reason_code"], "enablement_field_extraction_uncertain")
        self.assertEqual(result["route_classification"]["field_extraction"]["grounding_reason_code"], "quote_mismatch")
        self.rag.assert_not_called()
        self.archer.assert_not_called()
        self.assertEqual(self.jobs, [])

    def test_project_not_found_then_direct_replacement(self):
        self.archer.side_effect = [NS(outcome="project_not_found", detail="not found"),
                                   NS(outcome="enabled", detail="enabled")]
        self.turn("try: " + "a" * 32)
        self.turn("try: " + "b" * 32)
        self.assertEqual(self.archer.call_count, 2)
        context = self.extract.call_args.kwargs["automation_context"]
        self.assertEqual(context["evidence_message_ids"], [context["current_message_id"]])
        self.assertEqual(len(context["conversation"]), 4)
        self.assertEqual(self.case["route_classification"]["handler_binding_status"], "completed")
        self.rag.assert_not_called()

    def test_replacement_can_still_be_invalid(self):
        self.turn("try: " + "a" * 33)
        self.turn("Where is it?", "rag")
        self.turn("try: " + "b" * 33)
        self.assertEqual(self.case["automation_context"]["enablement_archer"]["outcome"], "appid_invalid")
        self.assertEqual(self.case["missing_fields"], ["app_id"])
        self.assertEqual(self.archer.call_count, 2)
        self.mail.assert_not_called()

    def test_completed_enablement_is_not_retained_by_rag(self):
        self.case["route_classification"]["handler_binding_status"] = "completed"
        self.turn("Where is it?", "rag")
        self.assertEqual(self.case["execution_action"], "rag")
        self.extract.assert_not_called()
        self.archer.assert_not_called()

    def test_lost_ownership_does_not_resume_enablement(self):
        with patch.object(reply, "_apply_ownership_gate", return_value=False):
            self.turn("try: " + "b" * 32)
        self.extract.assert_not_called()
        self.archer.assert_not_called()
        self.assertEqual(self.jobs, [])

    def test_same_failure_incident_does_not_send_twice(self):
        for _ in range(2):
            self.case = asyncio.run(intake._record_execution_failure(
                repository=self.repo, account_case=self.case, ticket_id="123", handler="enablement",
                stage="reply_rag_fallback", reason_code="reply_rag_fallback_escalation",
                detail="insufficient_evidence"))
        self.mail.assert_called_once()
        self.queue.assert_called_once()
        self.note.assert_called_once()

    def test_unknown_handoff_does_not_requeue_or_resume(self):
        self.queue.return_value = NS(status="outcome_unknown")
        self.rag.return_value = NS(kind="escalate", reason="insufficient_evidence")
        self.turn("Where is it?", "rag")
        self.turn("try: " + "b" * 32)
        self.queue.assert_called_once()
        self.archer.assert_not_called()
        self.mail.assert_called_once()
