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
        self.repo.prepare_account_internal_email_delivery.return_value = True
        self.repo.save_ticket.side_effect = self.save_ticket
        self.repo.begin_idempotent_request.side_effect = self.claim
        self.repo.complete_idempotent_request.side_effect = self.complete
        self.repo.fail_idempotent_request.side_effect = self.complete
        # The escalation context persists this count (p2-190); a bare Mock
        # return value is not deepcopy-safe and would crash the case mirror.
        self.repo.cancel_pending_account_reply_jobs.return_value = 0
        self.mail = Mock()
        self.stack.enter_context(patch.object(intake, "notify_account_failure", side_effect=lambda **kw:
                                             notify_account_failure(**kw, mail_sender=self.mail)))
        self.stack.enter_context(patch.object(reply, "_apply_ownership_gate", return_value=True))
        self.extract = self.stack.enter_context(patch.object(intake, "extract_enablement_fields", side_effect=self.extract_fields))
        # p2-163: the manual flow must never persist a relay request (the ECS
        # runtime holds no Archer path at all).
        self.repo.create_enablement_relay_request.side_effect = AssertionError(
            "manual enablement must not dispatch relay requests"
        )
        self.relay_dispatches = 0
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
        self.assertEqual(self.case["internal_email_send_reason"], "appid_invalid_format")
        for question in ("Where can I find it?", "Which project settings?"):
            self.turn(question, "rag")
            self.assertEqual(self.case["route_classification"]["handler_binding_status"], "active")
            self.assertEqual(self.case["automation_context"]["zendesk_ownership"]["source_group_id"], "original-group")
        self.turn("try: " + "b" * 32, comment_id="corrected")
        self.repo.create_enablement_relay_request.assert_not_called()
        context = self.extract.call_args.kwargs["automation_context"]
        self.assertEqual(context["evidence_message_ids"], [context["current_message_id"]])
        self.assertEqual(len(context["conversation"]), 7)
        self.assertNotIn("app_id", self.extract.call_args.kwargs["existing_fields"])
        self.assertEqual(self.case["route_classification"]["handler_binding_status"], "active")
        self.assertEqual(self.case["internal_email_send_status"], "awaiting_public_reply")
        self.assertEqual(self.jobs[-1]["reply_intent"], "submission_confirmation")
        self.assertEqual(self.rag.call_count, 3)
        job_count = len(self.jobs)
        self.turn("try: " + "b" * 32, comment_id="corrected")
        self.assertEqual(len(self.jobs), job_count)
        self.repo.create_enablement_relay_request.assert_not_called()
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
        self.repo.create_enablement_relay_request.assert_not_called()
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
        self.repo.create_enablement_relay_request.assert_not_called()

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
        self.repo.create_enablement_relay_request.assert_not_called()
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
        self.repo.create_enablement_relay_request.assert_not_called()
        self.assertEqual(self.jobs, [])

    def test_valid_app_id_waits_for_review_without_project_lookup(self):
        self.turn("try: " + "a" * 32)
        self.repo.create_enablement_relay_request.assert_not_called()
        context = self.extract.call_args.kwargs["automation_context"]
        self.assertEqual(context["evidence_message_ids"], [context["current_message_id"]])
        self.assertEqual(len(context["conversation"]), 3)
        self.assertEqual(self.case["collected_fields"]["app_id"], "a" * 32)
        self.assertEqual(self.case["route_classification"]["handler_binding_status"], "active")
        self.assertEqual(self.case["internal_email_send_status"], "awaiting_public_reply")
        self.assertEqual(self.jobs[-1]["reply_intent"], "submission_confirmation")
        self.rag.assert_not_called()

    def test_replacement_can_still_be_invalid(self):
        self.turn("try: " + "a" * 33)
        self.turn("Where is it?", "rag")
        self.turn("try: " + "b" * 33)
        self.assertEqual(self.case["internal_email_send_reason"], "appid_invalid_format")
        self.assertEqual(self.case["missing_fields"], ["app_id"])
        self.repo.create_enablement_relay_request.assert_not_called()
        self.mail.assert_not_called()

    def test_legacy_rejected_app_id_resumes_into_manual_review(self):
        self.case["automation_context"]["enablement_archer"] = {"outcome": "project_not_found"}
        self.case["collected_fields"]["app_id"] = "a" * 32
        self.case["internal_email_send_status"] = "not_applicable"
        self.case["internal_email_send_reason"] = "archer_project_not_found"

        self.turn("try: " + "b" * 32)

        self.assertNotIn("app_id", self.extract.call_args.kwargs["existing_fields"])
        self.assertEqual(self.case["collected_fields"]["app_id"], "b" * 32)
        self.assertEqual(self.case["internal_email_send_status"], "awaiting_public_reply")
        self.assertEqual(self.jobs[-1]["reply_intent"], "submission_confirmation")
        self.repo.create_enablement_relay_request.assert_not_called()

    def test_completed_enablement_is_not_retained_by_rag(self):
        self.case["route_classification"]["handler_binding_status"] = "completed"
        self.turn("Where is it?", "rag")
        self.assertEqual(self.case["execution_action"], "rag")
        self.extract.assert_not_called()
        self.repo.create_enablement_relay_request.assert_not_called()

    def test_lost_ownership_does_not_resume_enablement(self):
        with patch.object(reply, "_apply_ownership_gate", return_value=False):
            self.turn("try: " + "b" * 32)
        self.extract.assert_not_called()
        self.repo.create_enablement_relay_request.assert_not_called()
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
        self.repo.create_enablement_relay_request.assert_not_called()
        self.mail.assert_called_once()

    def test_comment_entry_gates_internal_email_until_public_confirmation(self):
        internal_emails = []
        self.stack.enter_context(patch.object(
            intake, "send_enablement_internal_email",
            side_effect=lambda payload: internal_emails.append(payload) or {"status": "sent", "reason": ""}))

        outcome = self.turn("try: " + "c" * 32, comment_id="manual-review")

        self.mail.assert_not_called()
        self.repo.create_enablement_relay_request.assert_not_called()
        self.assertEqual(internal_emails, [])
        self.assertEqual(outcome["automation_status"], "automation")
        self.assertEqual(outcome["internal_email_send_status"], "awaiting_public_reply")
        self.assertEqual(outcome["internal_email_payload"]["delivery_key"], "enablement:test-case:v1")
        workflow = outcome["automation_context"]["enablement_manual_workflow"]
        self.assertEqual(workflow["reply_job_id"], "job-1")
        self.assertEqual(self.jobs[0]["reply_intent"], "submission_confirmation")
        prepared = self.repo.prepare_account_internal_email_delivery.call_args.kwargs
        self.assertEqual(prepared["target_status"], "awaiting_public_reply")


class InsessionEnableProgressReplyTest(EnablementRagResumeTest):
    """14152 regression: an in-session re-enable request restates the SAME
    pending request. It is answered from the bound request's actual state
    (manual review or relay request) — never superseded into the RAG
    fallback, never a second enablement application."""

    REENABLE = "Could you help me enable the permission now?"

    def _enter_pending_review_state(self, app_id="b" * 32):
        """Post-submission 14152 state: complete App ID, manual review reply
        job bound, review pending, automation still owns the case."""
        self.case["collected_fields"] = {
            "requested_feature": "media_relay",
            "requested_feature_label": "Media Relay",
        }
        if app_id:
            self.case["collected_fields"]["app_id"] = app_id
        self.case["missing_fields"] = []
        self.case["internal_email_send_status"] = "awaiting_public_reply"
        self.case["internal_email_send_reason"] = "enablement_manual_review"
        self.case["automation_context"] = {
            "zendesk_ownership": {"state": "assigned", "source_group_id": "original-group"},
            "enablement_manual_workflow": {
                "version": 1,
                "state": "awaiting_public_reply",
                "reply_job_id": "account-reply-14152",
                "delivery_key": "dk-1",
                "prepared_at": "2026-10-20T08:00:00Z",
                "updated_at": "2026-10-20T08:00:00Z",
            },
        }
        self.repo.get_account_reply_job.return_value = {
            "job_id": "account-reply-14152",
            "status": "queued",
        }

    def test_reenable_wording_answered_from_manual_review_state(self):
        self._enter_pending_review_state()
        self.turn(self.REENABLE, "rag")
        # No RAG fallback, no supersession, no second application.
        self.rag.assert_not_called()
        self.repo.create_enablement_relay_request.assert_not_called()
        classification = self.case["route_classification"]
        self.assertEqual(classification.get("handler_binding_status"), "active")
        self.assertNotEqual(
            classification.get("previous_handler_binding_status"), "superseded"
        )
        self.assertEqual(classification["handler_continuation_reason"], "insession_enable_progress")
        self.assertEqual(
            classification["server_correction_reason"],
            "insession_enable_reuse:re_enable_wording->progress_inquiry",
        )
        self.assertEqual(self.case["automation_status"], "automation")
        self.assertEqual(self.case["execution_action"], "enablement")
        self.assertEqual(len(self.jobs), 1)
        job = self.jobs[0]
        self.assertEqual(job["reply_intent"], "progress_inquiry_answer")
        facts = job["reply_facts"]
        self.assertEqual(facts["behavior"], "progress_inquiry")
        self.assertIn("already received", facts["provided_answer"])
        self.assertIn("queued for review", facts["provided_answer"])
        self.assertEqual(facts["progress"]["binding"], "manual_review")

    def test_reenable_wording_failclosed_when_review_state_unreadable(self):
        self._enter_pending_review_state()
        self.repo.get_account_reply_job.return_value = None
        self.turn(self.REENABLE, "rag")
        self.rag.assert_not_called()
        self.assertEqual(self.case["automation_status"], "human_review_required")
        self.assertEqual(
            self.case["failure_code"],
            "followup_progress_state_unavailable:manual_job_missing",
        )
        self.assertEqual(self.jobs, [])

    def test_reenable_wording_terminal_relay_request_fails_closed(self):
        self._enter_pending_review_state()
        self.case["automation_context"]["enablement_auto_workflow"] = {
            "state": "awaiting_public_reply",
            "request_id": "enr-14152-v1",
        }
        self.repo.get_enablement_relay_request.return_value = {
            "request_id": "enr-14152-v1",
            "status": "cancelled",
        }
        self.turn(self.REENABLE, "rag")
        self.rag.assert_not_called()
        self.assertEqual(
            self.case["failure_code"],
            "followup_progress_state_unavailable:request_cancelled",
        )

    def test_reenable_wording_active_relay_request_answers_from_request(self):
        self._enter_pending_review_state()
        self.case["automation_context"]["enablement_auto_workflow"] = {
            "state": "awaiting_public_reply",
            "request_id": "enr-14152-v1",
        }
        self.repo.get_enablement_relay_request.return_value = {
            "request_id": "enr-14152-v1",
            "status": "dispatched",
            "request_version": 1,
            "created_at": "2026-10-20T09:00:00Z",
        }
        self.turn(self.REENABLE, "rag")
        self.rag.assert_not_called()
        job = self.jobs[-1]
        self.assertEqual(job["reply_intent"], "progress_inquiry_answer")
        self.assertEqual(job["reply_facts"]["progress"]["binding"], "relay_request")
        self.assertEqual(
            job["reply_facts"]["progress"]["review_state"], "with the reviewing engineer"
        )

    def test_reenable_wording_incomplete_app_id_falls_through_to_rag(self):
        self._enter_pending_review_state(app_id=None)
        self.case["missing_fields"] = ["app_id"]
        self.case["collected_fields"] = {
            "requested_feature": "media_relay",
            "requested_feature_label": "Media Relay",
        }
        self.case["missing_fields"] = ["app_id"]
        self.turn(self.REENABLE, "rag")
        # Old contract preserved: field collection continues via routing.
        self.assertEqual(self.rag.call_count, 1)
        self.assertEqual(self.jobs[-1]["reply_intent"], "rag_fallback_answer")

    def test_reenable_wording_human_review_status_does_not_fire(self):
        self._enter_pending_review_state()
        self.case["automation_status"] = "human_review_required"
        self.turn(self.REENABLE, "rag")
        # The progress branch requires an automation-held case; the RAG
        # fallback gate also skips an already-escalated case. No progress
        # reply may appear either way.
        self.assertTrue(
            all(job["reply_intent"] != "progress_inquiry_answer" for job in self.jobs)
        )

    def test_reenable_wording_with_new_app_id_stays_out_of_progress_path(self):
        message = f"Could you help me enable the permission now? My App ID is {'c' * 32}"
        self.turn(message, "rag")
        # A corrected App ID riding with the re-enable wording is field
        # progress: the progress reply must stay out of the turn even when
        # extraction itself does not surface the new value.
        self.assertTrue(
            all(job["reply_intent"] != "progress_inquiry_answer" for job in self.jobs)
        )
        self.assertNotEqual(
            self.case["route_classification"].get("handler_continuation_reason"),
            "insession_enable_progress",
        )

    def test_plain_question_wording_is_not_a_reenable_request(self):
        self.turn("Where can I find the App ID?", "rag")
        self.assertEqual(self.rag.call_count, 1)
        self.assertEqual(self.jobs[-1]["reply_intent"], "rag_fallback_answer")
