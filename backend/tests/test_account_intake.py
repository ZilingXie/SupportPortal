from __future__ import annotations

import asyncio
import json
import os
import unittest
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import psycopg

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from fastapi.testclient import TestClient

import backend.main as main
import backend.worker as worker
from backend.services import billing_automation as billing_automation_service
from backend.services.account_reply_jobs import create_account_reply_job
from backend.services.account_reply_rag_fallback import RagFallbackOutcome
from backend.repositories.ticket_repository import (
    ACCOUNT_RERUN_RESET_AI_ONLY,
    ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
    InMemoryTicketRepository,
)
from backend.services.account_admin import AccountPersonaUnavailableError
from backend.services.account_automation_ownership import OwnershipGateResult
from backend.services.billing_response_flow import hash_billing_response_token
from backend.services.enablement_field_extractor import EnablementFieldExtraction
from backend.services.account_verification_field_extractor import AccountVerificationFieldExtraction
from backend.services.account_suspension_field_extractor import AccountSuspensionFieldExtraction
from backend.services.detailed_invoice_field_extractor import DetailedInvoiceFieldExtraction
from backend.services.account_route_pipeline import AccountRouteResult, AccountRouteStageAttempt
from backend.services.account_full_reroute import AccountFullRerouteResult
from backend.services.account_ai_execution import AccountProcessingFailure
from backend.services.automation_persona import AutomationPersonaError, AutomationPersonaResult
from backend.services.llm_factory import LlmInvocationError
from backend.services.quota_field_extractor import QuotaFieldExtraction
from backend.services.support_router import SupportResolution, SupportRouteDecision, _LlmRouteAttempt
from backend.services.workspace_auth import WorkspacePrincipal


def _successful_account_rerun_preflight() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        reason="",
        as_dict=lambda: {
            "ok": True,
            "reason": "",
            "checks": {
                "postgresql": {"status": "passed"},
                "prompt_runtime": {"status": "passed"},
                "account_model": {"status": "passed"},
            },
        },
    )


def _failed_account_rerun_preflight(reason: str = "preflight_account_model_failed") -> SimpleNamespace:
    return SimpleNamespace(
        ok=False,
        reason=reason,
        as_dict=lambda: {
            "ok": False,
            "reason": reason,
            "checks": {
                "postgresql": {"status": "passed"},
                "prompt_runtime": {"status": "passed"},
                "account_model": {"status": "failed", "reason": "model_unavailable"},
            },
        },
    )


def _fraud_account_route_result() -> AccountRouteResult:
    decision = SupportRouteDecision(
        scope_label="fraud_account",
        route="fraud_account",
        route_family="automated",
        execution_action="fraud_account",
        confidence=0.97,
        reason="registered_fraud_account",
        semantic_intent="automation.fraud_account_review",
        automation_eligibility="eligible",
        tooling_profile="deterministic_billing_intake",
        router_source="account_layered_llm",
    )
    classification = {
        "pipeline_version": "account-layered-router-v3",
        "intent_class": "agora",
        "agora_route": "automation",
        "automation_subcategory": "fraud_account",
        "route_target": "automation",
        "route_reason_code": "registered_fraud_account",
        "stage_confidences": {"intent_classifier": 0.99, "agora_router": 0.98, "automation_router": 0.97},
        "stage_reason_codes": {
            "intent_classifier": "agora_case",
            "agora_router": "explicit_backend_operation",
            "automation_router": "registered_fraud_account",
        },
        "handler_binding_status": "active",
        "primary_label": "Agora",
        "secondary_label": "Account & Billing / Fraud Account",
    }
    return AccountRouteResult(
        decision=decision,
        classification=classification,
        primary_label="Agora",
        secondary_label="Account & Billing / Fraud Account",
    )


def _account_suspension_route_result() -> AccountRouteResult:
    decision = SupportRouteDecision(
        scope_label="account_billing",
        route="human_review_required",
        route_family="human_review",
        execution_action="human_review_required",
        confidence=0.96,
        reason="registered_account_suspension",
        semantic_intent="account_billing.account_suspension",
        automation_eligibility="not_eligible",
        router_source="account_layered_llm",
    )
    classification = {
        "pipeline_version": "account-layered-router-v4",
        "intent_class": "agora",
        "agora_route": "account_billing",
        "account_billing_subcategory": "account_suspension",
        "automation_subcategory": None,
        "route_target": "human_review",
        "route_reason_code": "registered_account_suspension",
        "stage_confidences": {
            "intent_classifier": 0.99,
            "agora_router": 0.98,
            "account_billing_router": 0.96,
        },
        "stage_reason_codes": {
            "intent_classifier": "agora_case",
            "agora_router": "account_billing_request",
            "account_billing_router": "registered_account_suspension",
        },
        "handler_binding_status": None,
        "primary_label": "Agora",
        "secondary_label": "Account & Billing / Account Suspension",
    }
    return AccountRouteResult(
        decision=decision,
        classification=classification,
        primary_label="Agora",
        secondary_label="Account & Billing / Account Suspension",
    )


def _active_account_suspension_route_result() -> AccountRouteResult:
    decision = SupportRouteDecision(
        scope_label="account_billing",
        route="account_suspension",
        route_family="automated",
        execution_action="account_suspension",
        confidence=0.96,
        reason="registered_account_suspension",
        semantic_intent="account_billing.account_suspension",
        automation_eligibility="eligible",
        tooling_profile="deterministic_billing_intake",
        router_source="account_layered_llm",
    )
    classification = {
        "pipeline_version": "account-layered-router-v9",
        "intent_class": "agora",
        "agora_route": "account_billing",
        "account_billing_subcategory": "account_suspension",
        "automation_subcategory": None,
        "route_target": "automation",
        "route_reason_code": "registered_account_suspension",
        "stage_reason_codes": {
            "intent_classifier": "agora_case",
            "agora_router": "account_billing_request",
            "account_billing_router": "registered_account_suspension",
        },
        "handler_binding_status": "active",
        "primary_label": "Agora",
        "secondary_label": "Account & Billing / Account Suspension",
    }
    return AccountRouteResult(
        decision=decision,
        classification=classification,
        primary_label="Agora",
        secondary_label="Account & Billing / Account Suspension",
    )


def _fake_enablement_field_extraction(**kwargs: object) -> EnablementFieldExtraction:
    messages = kwargs.get("customer_messages")
    text = "\n".join(
        str(message.get("content") or "")
        for message in (messages if isinstance(messages, list) else [])
        if isinstance(message, dict)
    )
    existing = kwargs.get("existing_fields")
    fields = dict(existing) if isinstance(existing, dict) else {}
    for candidate in (
        "7da36383d624411698e5c0bc1fda6324",
        "project.prod/eu-west#alpha",
    ):
        if candidate in text:
            fields["app_id"] = candidate
    fields.setdefault("requested_feature", "media_relay")
    fields.setdefault("requested_feature_label", "Media Relay")
    if fields.get("app_id"):
        return EnablementFieldExtraction(
            status="complete",
            collected_fields={str(key): str(value) for key, value in fields.items()},
            grounding_status="passed",
        )
    return EnablementFieldExtraction(
        status="missing",
        collected_fields={str(key): str(value) for key, value in fields.items()},
        missing_fields=["app_id"],
        follow_up="Could you share the App ID for this Media Relay request?",
        grounding_status="passed",
    )


def _fake_account_verification_field_extraction(**kwargs: object) -> AccountVerificationFieldExtraction:
    messages = kwargs.get("customer_messages")
    text = "\n".join(
        str(message.get("content") or "")
        for message in (messages if isinstance(messages, list) else [])
        if isinstance(message, dict)
    ).lower()
    existing = kwargs.get("existing_fields")
    fields = dict(existing) if isinstance(existing, dict) else {}
    if "enterprise" in text or "account type" in text:
        fields["account_type"] = "Enterprise"
    if "my name" in text or "i am " in text:
        fields["name"] = "Customer Name"
    if "office" in text or "address" in text:
        fields["office_address"] = "Customer office address"
    if "phone" in text or "contact number" in text:
        fields["contact_number"] = "+86 123 4567 8900"
    if "email" in text:
        fields["contact_email"] = "customer@example.com"
    if "use case" in text or "we use agora" in text:
        fields["use_case_description"] = "Customer-described Agora use case"
    if "console" in text or "configuration" in text or "setup" in text:
        fields["console_configuration"] = "Last known console setup"
    missing = [
        group
        for group in (
            "account_type",
            "name",
            "office_address",
            "contact_number",
            "contact_email",
            "use_case_description",
            "console_configuration",
        )
        if not fields.get(group)
    ]
    return AccountVerificationFieldExtraction(
        status="missing" if missing else "complete",
        collected_fields={str(key): str(value) for key, value in fields.items()},
        missing_fields=missing,
        grounding_status="passed",
    )


def _fake_detailed_invoice_field_extraction(**kwargs: object) -> DetailedInvoiceFieldExtraction:
    message = str(kwargs.get("message") or "")
    fields = billing_automation_service._extract_fields(
        message,
        billing_automation_service._FIELD_ALIASES["detailed_invoice"],
    )
    missing = [
        field_name
        for field_name in ("issue_date", "transaction_id", "amount")
        if not fields.get(field_name)
    ]
    return DetailedInvoiceFieldExtraction(
        status="missing" if missing else "complete",
        collected_fields=fields,
        missing_fields=missing,
        reason="deterministic test fixture",
        prompt_snapshot={"system_prompt": "test", "user_prompt": "test"},
    )


def _fake_account_suspension_field_extraction(**kwargs: object) -> AccountSuspensionFieldExtraction:
    messages = kwargs.get("customer_messages")
    text = "\n".join(
        str(message.get("content") or "")
        for message in (messages if isinstance(messages, list) else [])
        if isinstance(message, dict)
    )
    fields = dict(kwargs.get("existing_fields") or {})
    if "suspend" in text.lower() or "disabled" in text.lower():
        fields.setdefault("suspension_status_or_error", "account suspended")
    return AccountSuspensionFieldExtraction(
        status="partial" if fields else "empty",
        collected_fields=fields,
        reason="deterministic test fixture",
        grounding_status="passed",
        prompt_snapshot={"system_prompt": "test", "user_prompt": "test"},
    )


def _fake_automation_resolution_facts(**kwargs: object) -> dict[str, object]:
    behavior = str(kwargs.get("behavior") or "billing")
    source_text = str(kwargs.get("source_text") or "")
    lowered = source_text.lower()
    if "billing address" in lowered:
        return {
            "status": "customer_action_required",
            "customer_shareable_facts": [],
            "customer_action": "Please confirm the billing address for this invoice.",
            "next_step": "We will continue after receiving the billing address.",
        }
    sent = any(marker in lowered for marker in ("sent", "发送", "已发送"))
    fact = (
        "The detailed invoice has been sent to the email address on file."
        if behavior == "detailed_invoice" and sent
        else f"The {behavior.replace('_', ' ')} request has been completed."
    )
    return {
        "status": "completed",
        "customer_shareable_facts": [fact],
        "customer_action": None,
        "next_step": "Please let us know if you need any further help.",
    }


def _fake_render_automation_reply(**kwargs: object) -> AutomationPersonaResult:
    facts = dict(kwargs.get("reply_facts") or {})
    first_name = str(facts.get("customer_first_name") or "Customer")
    intent = str(facts.get("reply_intent") or "")
    behavior = str(facts.get("behavior") or "request").replace("_", " ")
    if intent == "request_missing_information":
        missing_labels = {
            "account_type": "Account type",
            "name": "Name",
            "office_address": "Office address",
            "contact_number": "Official contact number",
            "contact_email": "Official contact email",
            "use_case_description": "Use-case description",
            "console_configuration": "Last known console configuration",
            "app_id": "App ID",
        }
        missing = [
            missing_labels.get(str(item), str(item).replace("_", " "))
            for item in facts.get("missing_information", [])
        ]
        if len(missing) <= 2:
            if len(missing) == 2:
                missing_request = f"{missing[0]} and {missing[1]}"
            else:
                missing_request = missing[0] if missing else "the missing information"
            body = (
                f"Could you share {missing_request}? I will continue coordinating the request "
                "after receiving the missing information."
            )
        else:
            body = (
                "Could you share the following details?\n\n"
                + "\n".join(f"- {item}" for item in missing)
                + "\n\nI will continue coordinating the request after receiving the missing information."
            )
    elif intent == "submission_confirmation":
        if behavior == "enablement":
            body = (
                "We are reviewing your request with our internal team. Activation may take up to 24 hours, "
                "and changes are handled Monday-Friday. We will keep you posted."
            )
        else:
            body = (
                f"Your {behavior} request has been submitted for review. We are reviewing it with our internal "
                "team and will keep you posted when there is an update."
            )
    elif intent == "fraud_handoff_confirmation":
        body = "The relevant team will contact you within 24 hours."
    elif intent == "account_suspension_contact_confirmation_request":
        body = (
            "Which email is most convenient for you, and should we use the email on this ticket? "
            "The relevant team will contact you within 24 hours. This ticket will close after contact "
            "confirmation and handoff; if nobody contacts you within 24 hours, you can reopen it."
        )
    elif intent == "account_suspension_handoff_and_close":
        body = (
            "The relevant team will contact you within 24 hours. This ticket is closing after the handoff; "
            "if nobody contacts you within 24 hours, you can reopen it."
        )
    elif intent == "enablement_completed_and_close":
        body = "The feature is enabled, and this ticket is closing."
    else:
        source_facts = [str(item) for item in facts.get("source_facts", []) if str(item).strip()]
        customer_action = str((facts.get("known_information") or {}).get("customer_action") or "").strip()
        body = " ".join([*source_facts, customer_action]).strip()
        if not body:
            body = f"Your {behavior} request has been completed."
    return AutomationPersonaResult(
        content=f"Hi {first_name},\n\n{body}",
        model="test-persona",
        prompt_version="test-persona-v1",
    )


def _fake_account_route_stage(
    *,
    stage_name: str,
    payload: dict[str, object],
    **_: object,
) -> AccountRouteStageAttempt:
    """Deterministic layered-router substitute for Account API tests."""
    text = str(payload.get("message") or payload.get("ticket_subject") or "").lower()
    if stage_name == "intent_classifier":
        is_conversation = any(marker in text for marker in ("thank you", "thanks", "it works now"))
        intent = "conversation" if is_conversation else "agora"
        conversation_action = (
            "resolve" if "it works now" in text else "follow_up"
        ) if is_conversation else None
        result = {
            "intent_class": intent,
            "conversation_action": conversation_action,
            "intent_confidence": 0.99,
            "action_confidence": 0.99 if is_conversation else None,
            "reason_code": "conversation_resolution" if conversation_action == "resolve" else (
                "conversation_follow_up" if is_conversation else "agora_case"
            ),
            "evidence_spans": [],
        }
    elif stage_name == "agora_router":
        is_backend = (
            "from your end" in text
            or "concurrency" in text
            or "quota" in text
            or "capacity" in text
        ) and not any(marker in text for marker in ("how do i", "how is", "why does", "fails"))
        is_billing = any(
            marker in text
            for marker in (
                "detailed invoice", "invoice", "billing", "payment", "charge", "suspended",
                "suspicious", "fraud", "verify our account", "account verification",
            )
        )
        route = "backend_operation" if is_backend else "account_billing" if is_billing else (
            "uncategorized" if any(marker in text for marker in ("agora products", "agora's ceo", "general support question"))
            else "technical"
        )
        backend_operation = None
        if route == "backend_operation":
            target = "quota" if any(marker in text for marker in ("concurrency", "quota", "capacity")) else "media_relay"
            backend_operation = {
                "action": "review_and_increase" if target == "quota" else "enable",
                "target": target,
                "evidence": "customer requested an Account-side operation",
            }
        result = {
            "agora_route": route,
            "confidence": 0.99,
            "reason_code": {
                "backend_operation": "explicit_backend_operation",
                "account_billing": "account_billing_request",
                "technical": "technical_request",
                "uncategorized": "no_matching_category",
            }[route],
            "additional_intents": [],
            "selection_reason": "deterministic test route",
            "backend_operation": backend_operation,
            "evidence_spans": [],
        }
    elif stage_name == "account_billing_router":
        if any(marker in text for marker in ("suspicious", "fraud", "verify our account", "account verification")):
            subcategory, reason = "fraud_account", "registered_fraud_account"
        elif "suspend" in text or "stopped" in text:
            subcategory, reason = "account_suspension", "registered_account_suspension"
        elif "detailed invoice" in text or "invoice" in text:
            subcategory, reason = "detailed_invoice", "detailed_invoice_requested"
        else:
            subcategory, reason = "other", "account_billing_other"
        result = {
            "account_billing_subcategory": subcategory,
            "confidence": 0.99,
            "reason_code": reason,
        }
    elif stage_name == "backend_operation_router":
        result = {
            "backend_operation_subcategory": "quota"
            if any(marker in text for marker in ("concurrency", "quota", "capacity"))
            else "enablement",
            "confidence": 0.99,
        }
    else:
        result = {
            "automation_subcategory": "unregistered",
            "confidence": 0.99,
        }
    return AccountRouteStageAttempt(
        payload=result,
        attempted=True,
        system_prompt=f"test:{stage_name}",
        user_prompt=str(payload.get("message") or ""),
        attempt_count=1,
    )


class AccountAskedFieldKeysTest(unittest.TestCase):
    def test_top_level_postgres_message_fields_count_as_already_asked(self) -> None:
        ticket = {
            "messages": [
                {
                    "role": "assistant",
                    "asked_field_keys": [" Account_Type ", "name"],
                    "meta": {"asked_field_keys": ["name", "office_address"]},
                }
            ]
        }

        self.assertEqual(
            main._account_asked_field_keys(ticket),
            {"account_type", "name", "office_address"},
        )


class AccountIntakeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        self.original_worker_repository = worker.ticket_repository
        self.original_dependency_overrides = dict(main.app.dependency_overrides)
        main.ticket_repository = self.repository
        worker.ticket_repository = self.repository
        main.app.dependency_overrides[main.require_workspace_admin] = lambda: WorkspacePrincipal(
            account_id="account-intake-test-admin",
            role="admin",
            display_name="Account Intake Test Admin",
            expires_at=4_102_444_800,
        )
        self.client = TestClient(main.app)
        # Keep API tests deterministic even when the local .env contains live routing credentials.
        self._llm_patcher = patch(
            "backend.services.support_router._llm_route_decision",
            return_value=_LlmRouteAttempt(decision=None, attempted=True),
        )
        self._account_stage_patcher = patch(
            "backend.services.account_route_pipeline._invoke_stage",
            side_effect=_fake_account_route_stage,
        )
        self._title_model_patcher = patch(
            "backend.services.ticket_title._invoke_title_model",
            side_effect=LlmInvocationError("disabled in account intake unit tests"),
        )
        self._enablement_extractor_patcher = patch(
            "backend.main.extract_enablement_fields",
            side_effect=_fake_enablement_field_extraction,
        )
        self._account_verification_extractor_patcher = patch(
            "backend.services.account_verification_automation.extract_account_verification_fields",
            side_effect=_fake_account_verification_field_extraction,
        )
        self._account_verification_follow_up_patcher = patch(
            "backend.services.account_verification_automation.compose_account_verification_follow_up",
            side_effect=lambda **kwargs: (
                "Could you share your account type, name, office address, official contact number, "
                "official contact email, use case description, and your last known console "
                "configuration or setup?",
                {"prompt_version": "test"},
            ),
        )
        self._detailed_invoice_extractor_patcher = patch(
            "backend.services.billing_automation.extract_detailed_invoice_fields",
            side_effect=_fake_detailed_invoice_field_extraction,
        )
        self._account_suspension_extractor_patcher = patch(
            "backend.main.extract_account_suspension_fields",
            side_effect=_fake_account_suspension_field_extraction,
        )
        self._worker_persona_patcher = patch(
            "backend.worker.render_automation_reply",
            side_effect=_fake_render_automation_reply,
        )
        self._main_persona_patcher = patch(
            "backend.main.render_automation_reply",
            side_effect=_fake_render_automation_reply,
        )
        self._resolution_extractor_patcher = patch(
            "backend.main.extract_automation_resolution_facts",
            side_effect=_fake_automation_resolution_facts,
        )
        # Legacy silent-reply tests below describe the pre-fallback behavior;
        # RAG-fallback tests opt back in explicitly per test.
        self._rag_fallback_env_patcher = patch.dict(
            os.environ, {"ACCOUNT_REPLY_RAG_FALLBACK_ENABLED": "false"}
        )
        self._rag_fallback_env_patcher.start()
        self._rerun_preflight_patcher = patch(
            "backend.main.run_account_rerun_preflight",
            side_effect=_successful_account_rerun_preflight,
        )
        self._llm_patcher.start()
        self._account_stage_patcher.start()
        self._title_model_patcher.start()
        self._enablement_extractor_patcher.start()
        self._account_verification_extractor_patcher.start()
        self._account_verification_follow_up_patcher.start()
        self._detailed_invoice_extractor_patcher.start()
        self._account_suspension_extractor_patcher.start()
        self._worker_persona_patcher.start()
        self._main_persona_patcher.start()
        self._resolution_extractor_patcher.start()
        self._rerun_preflight_patcher.start()

    def test_ownership_gate_event_preserves_policy_diagnostics(self) -> None:
        repository = Mock()
        account_case = {
            "account_case_id": "AC-12875",
            "client_ticket_id": "PRD-12875",
        }
        result = OwnershipGateResult(
            eligible=True,
            state="human_replied",
            assignee_id="31116634341396",
            group_id="27216254064148",
            failure_code="zendesk_human_reply_blocks_automation",
            failure_category="policy",
            zendesk_status_code=200,
            blocking_comment_id="52708200000000",
            updated_at="2026-08-20T07:04:00Z",
        )

        with patch.object(main, "ticket_repository", repository), patch.object(
            main, "ownership_gate_eligible", return_value=True
        ), patch.object(
            main, "ensure_production_automation_ownership", return_value=result
        ):
            allowed = main._apply_production_ownership_gate(
                account_case,
                "2026-08-20T07:04:00Z",
            )

        self.assertFalse(allowed)
        event = repository.record_event.call_args.args[2]
        self.assertEqual(event["failure_category"], "policy")
        self.assertEqual(event["zendesk_status_code"], 200)
        self.assertEqual(event["blocking_comment_id"], "52708200000000")
        repository.cancel_pending_account_reply_jobs.assert_called_once_with(
            "PRD-12875",
            updated_at="2026-08-20T07:04:00Z",
        )

    def test_account_case_view_exposes_route_failure_diagnostics(self) -> None:
        view = main._build_account_ticket_view_model(
            {
                "account_case_id": "AC-12572",
                "billing_ticket_id": "AC-12572",
                "client_ticket_id": "12572",
                "route_family": "human_review",
                "execution_action": "human_review_required",
                "route_classification": {
                    "route_reason_code": "intent_classifier_invalid_json",
                    "stage_failure_types": {"intent_classifier": "invalid_json"},
                    "stage_failure_sources": {"intent_classifier": "intent_classifier"},
                    "stage_attempt_counts": {"intent_classifier": 2},
                    "stage_recovered": {"intent_classifier": False},
                    "route_failure_family": "invalid_intent_output",
                },
            },
            correction=None,
        )

        self.assertEqual(view["route_failure_family"], "invalid_intent_output")
        self.assertEqual(view["stage_failure_types"], {"intent_classifier": "invalid_json"})
        self.assertEqual(view["stage_attempt_counts"], {"intent_classifier": 2})
        self.assertEqual(view["stage_recovered"], {"intent_classifier": False})

    def test_account_intake_processing_failure_is_human_review_and_idempotent(self) -> None:
        failure = AccountProcessingFailure(
            "account_route_invocation_exhausted",
            "OpenAI Responses API unavailable after retries",
            stage="intent_classifier",
        )
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main, "decide_account_route", side_effect=failure
        ), patch.object(
            main,
            "notify_account_failure",
            return_value={"status": "sent", "incident_id": "account-processing:AC-TK-FAIL-001:intent_classifier:account_route_invocation_exhausted"},
        ) as alert:
            request = {
                "external_id": "TK-FAIL-001",
                "title": "Account request",
                "question": "Please investigate this account issue.",
                "customer_email": "customer@example.com",
            }
            first = self.client.post("/account", json=request)
            second = self.client.post("/account", json=request)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["status"], "human_review_required")
        self.assertEqual(first.json()["automation_status"], "human_review_required")
        self.assertEqual(first.json()["failure_attempt_count"], 4)
        self.assertEqual(first.json()["failure_stage"], "intent_classifier")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(second.json()["failure_incident_id"], first.json()["failure_incident_id"])
        alert.assert_called_once()
        case = self.repository.get_account_case(first.json()["account_case_id"])
        assert case is not None
        self.assertEqual(case["automation_status"], "human_review_required")
        self.assertIsNone(case["customer_reply"])
        self.assertIsNone(self.repository.get_latest_account_reply_job(first.json()["ticket_id"]))

    def test_default_processing_profile_env_routes_intake_to_production_profile(self) -> None:
        with patch.dict(os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"}), patch.object(
            main, "dispatch_event", AsyncMock()
        ):
            created = self.client.post(
                "/account",
                json={
                    "external_id": "99887766",
                    "title": "Production environment intake",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(created.status_code, 200, created.text)
        case = self.repository.get_account_case(created.json()["account_case_id"])
        assert case is not None
        self.assertEqual(case["processing_profile"], "production")
        self.assertEqual(case["zendesk_ticket_id"], "99887766")

    def test_intake_defaults_to_staging_without_profile_env(self) -> None:
        with patch.dict(os.environ), patch.object(main, "dispatch_event", AsyncMock()):
            os.environ.pop("ACCOUNT_DEFAULT_PROCESSING_PROFILE", None)
            created = self.client.post(
                "/account",
                json={
                    "external_id": "99887767",
                    "title": "Staging default intake",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(created.status_code, 200, created.text)
        case = self.repository.get_account_case(created.json()["account_case_id"])
        assert case is not None
        self.assertEqual(case["processing_profile"], "staging")
        self.assertIsNone(case["zendesk_ticket_id"])

    def test_account_case_list_default_profile_follows_env(self) -> None:
        with patch.dict(os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"}), patch.object(
            main, "dispatch_event", AsyncMock()
        ):
            created = self.client.post(
                "/account",
                json={
                    "external_id": "99887768",
                    "title": "Production list default intake",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            )
        self.assertEqual(created.status_code, 200, created.text)

        with patch.dict(os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"}):
            listed_production = self.client.get("/api/account/cases")
        self.assertEqual(listed_production.status_code, 200, listed_production.text)
        production_ids = {item["account_case_id"] for item in listed_production.json()["tickets"]}
        self.assertIn(created.json()["account_case_id"], production_ids)

        with patch.dict(os.environ):
            os.environ.pop("ACCOUNT_DEFAULT_PROCESSING_PROFILE", None)
            listed_staging = self.client.get("/api/account/cases")
        self.assertEqual(listed_staging.status_code, 200, listed_staging.text)
        staging_ids = {item["account_case_id"] for item in listed_staging.json()["tickets"]}
        self.assertNotIn(created.json()["account_case_id"], staging_ids)

    def test_account_reply_processing_failure_cancels_pending_reply_and_alerts_once(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            ).json()
        job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert job is not None
        failure = AccountProcessingFailure(
            "account_ai_structured_output_exhausted",
            "invalid JSON after retries",
            stage="enablement_field_extractor",
        )
        with patch.object(main, "_build_enablement_internal_email_attempt", side_effect=failure), patch.object(
            main, "notify_account_failure", return_value={"status": "sent"}
        ) as alert:
            response = self.client.post(
                f"/api/account/cases/{created['account_case_id']}/reply",
                json={"message": "My App ID is project.prod/eu-west#alpha."},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "human_review_required")
        self.assertEqual(payload["automation_status"], "human_review_required")
        self.assertEqual(payload["failure_stage"], "enablement_field_extractor")
        stored_job = self.repository.get_account_reply_job(job["job_id"])
        assert stored_job is not None
        self.assertEqual(stored_job["status"], "cancelled")
        alert.assert_called_once()

    def test_account_intake_reply_job_creation_failure_is_case_failure_and_alerted(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main,
            "_create_account_reply_job",
            side_effect=RuntimeError("reply job persistence failed"),
        ), patch.object(
            main,
            "notify_account_failure",
            return_value={"status": "sent", "incident_id": "test-incident"},
        ) as alert:
            response = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "human_review_required")
        self.assertEqual(payload["route_status"], "not_automated")
        self.assertEqual(payload["failure_stage"], "reply_job")
        self.assertEqual(payload["failure_code"], "account_reply_job_creation_failed")
        self.assertEqual(payload["execution_reason_code"], "account_reply_job_creation_failed")
        self.assertIsNone(payload["ai_reply_status"])
        alert.assert_called_once()
        stored = self.repository.get_account_case(payload["account_case_id"])
        assert stored is not None
        self.assertEqual(stored["route"], "enablement")
        self.assertEqual(stored["route_family"], "automated")
        self.assertEqual(stored["automation_status"], "human_review_required")
        self.assertIsNone(self.repository.get_latest_account_reply_job(payload["ticket_id"]))

    def test_account_reply_job_creation_failure_keeps_automation_route_and_alerts(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            ).json()

        with patch.object(
            main,
            "_create_account_reply_job",
            side_effect=RuntimeError("reply job persistence failed"),
        ), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ), patch.object(
            main,
            "notify_account_failure",
            return_value={"status": "sent", "incident_id": "test-incident"},
        ) as alert:
            response = self.client.post(
                f"/api/account/cases/{created['account_case_id']}/reply",
                json={"message": "My App ID is project.prod/eu-west#alpha."},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["automation_status"], "human_review_required")
        self.assertEqual(payload["route_status"], "not_automated")
        self.assertEqual(payload["failure_stage"], "reply_job")
        self.assertEqual(payload["failure_code"], "account_reply_job_creation_failed")
        self.assertIsNone(payload["ai_reply_status"])
        alert.assert_called_once()
        stored = self.repository.get_account_case(created["account_case_id"])
        assert stored is not None
        self.assertEqual(stored["route"], "enablement")
        self.assertEqual(stored["route_status"], "not_automated")
        self.assertEqual(stored["automation_status"], "human_review_required")

    def test_account_case_view_preserves_account_billing_automation_category(self) -> None:
        view = main._build_account_ticket_view_model(
            {
                "account_case_id": "AC-12710",
                "billing_ticket_id": "AC-12710",
                "client_ticket_id": "12710",
                "category": "account_billing",
                "subcategory": "detailed_invoice",
                "route": "detailed_invoice",
                "execution_action": "detailed_invoice",
                "route_family": "automated",
                "route_status": "automated",
                "automation_handler": "billing",
                "route_classification": {
                    "pipeline_version": "account-layered-router-v7",
                    "intent_class": "agora",
                    "agora_route": "account_billing",
                    "account_billing_subcategory": "detailed_invoice",
                },
            },
            correction=None,
        )

        self.assertEqual(view["category"], "account_billing")
        self.assertEqual(view["subcategory"], "detailed_invoice")
        self.assertEqual(view["route_status"], "automated")
        self.assertEqual(view["automation_handler"], "billing")
        self.assertEqual(view["secondary_label"], "Account & Billing / Detailed Invoice")

    def test_account_case_storage_preserves_account_billing_automation_category(self) -> None:
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12710",
                "billing_ticket_id": "AC-12710",
                "client_ticket_id": "12710",
                "category": "account_billing",
                "subcategory": "detailed_invoice",
                "route": "detailed_invoice",
                "execution_action": "detailed_invoice",
                "route_family": "automated",
                "route_status": "automated",
                "automation_handler": "billing",
                "route_classification": {
                    "pipeline_version": "account-layered-router-v7",
                    "intent_class": "agora",
                    "agora_route": "account_billing",
                    "account_billing_subcategory": "detailed_invoice",
                },
            }
        )

        stored = self.repository.get_account_case("AC-12710")
        assert stored is not None
        self.assertEqual(stored["category"], "account_billing")
        self.assertEqual(stored["subcategory"], "detailed_invoice")
        self.assertEqual(stored["route_status"], "automated")
        self.assertEqual(stored["automation_handler"], "billing")

        stored.update(
            route="human_review_required",
            execution_action="human_review_required",
            route_family="human_review",
            automation_status="not_automated",
            category="human_review",
            subcategory="human_review_required",
            route_status="not_automated",
            automation_handler=None,
            route_classification={
                **stored["route_classification"],
                "agora_route": "uncategorized",
                "account_billing_subcategory": None,
            },
        )
        self.repository.save_account_case(stored)
        downgraded = self.repository.get_account_case("AC-12710")
        assert downgraded is not None
        self.assertEqual(downgraded["route_family"], "human_review")
        self.assertEqual(downgraded["route_status"], "not_automated")
        self.assertIsNone(downgraded["automation_handler"])

    def tearDown(self) -> None:
        self._resolution_extractor_patcher.stop()
        self._rag_fallback_env_patcher.stop()
        self._rerun_preflight_patcher.stop()
        self._main_persona_patcher.stop()
        self._worker_persona_patcher.stop()
        self._account_suspension_extractor_patcher.stop()
        self._detailed_invoice_extractor_patcher.stop()
        self._account_verification_follow_up_patcher.stop()
        self._account_verification_extractor_patcher.stop()
        self._enablement_extractor_patcher.stop()
        self._title_model_patcher.stop()
        self._account_stage_patcher.stop()
        self._llm_patcher.stop()
        self.client.close()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(self.original_dependency_overrides)
        main.ticket_repository = self.original_repository
        worker.ticket_repository = self.original_worker_repository

    def _publish_latest_account_reply(self, ticket_id: str) -> dict[str, object]:
        job = self.repository.get_latest_account_reply_job(ticket_id)
        self.assertIsNotNone(job)
        assert job is not None
        job["status"] = (
            worker.ACCOUNT_REPLY_PERSONA_PUBLISHING
            if (job.get("payload") or {}).get("reply_pipeline") == worker.ACCOUNT_REPLY_PERSONA_PIPELINE
            else "publishing"
        )
        self.repository.save_account_reply_job(job)
        worker._publish_account_reply_job(job)
        published = self.repository.get_account_reply_job(str(job["job_id"]))
        self.assertIsNotNone(published)
        assert published is not None
        return published

    def _admit_account_reroute_job(self, job: dict[str, object]) -> dict[str, object]:
        result = self.repository.claim_account_case_rerun(
            job,
            active_after="2000-01-01T00:00:00+00:00",
            request_scope="test:account-reroute-worker",
        )
        self.assertEqual(result["status"], "created")
        return dict(result["job"])

    def _create_invoice_ticket_with_response_token(self) -> tuple[dict[str, object], str]:
        ticket_id = f"TK-HIST-INVOICE-{len(self.repository.list_tickets()) + 1:03d}"
        account_case_id = f"AC-{ticket_id}"
        created_at = "2026-07-18T00:00:00+00:00"
        question = (
            "Please send detailed invoice. Issue date: 6 May 2026. "
            "Transaction ID: 1104245232004173824. Amount: USD 705.97."
        )
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "customer@example.com",
                "requester": "customer@example.com",
                "subject": "Detailed invoice request",
                "status": "open",
                "source": "manual",
                "created_at": created_at,
                "updated_at": created_at,
                "messages": [
                    {
                        "role": "customer",
                        "content": question,
                        "created_at": created_at,
                        "content_format": "plaintext",
                        "source": "manual",
                    }
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": account_case_id,
                "billing_ticket_id": account_case_id,
                "client_ticket_id": ticket_id,
                "source": "manual",
                "title": "Detailed invoice request",
                "question": question,
                "route": "detailed_invoice",
                "scope_label": "account_billing",
                "route_family": "automated",
                "execution_action": "detailed_invoice",
                "tooling_profile": "deterministic_billing_intake",
                "automation_status": "automation",
                "route_status": "automated",
                "automation_handler": "billing",
                "category": "account_billing",
                "subcategory": "detailed_invoice",
                "customer_name": "Customer",
                "collected_fields": {
                    "issue_date": "6 May 2026",
                    "transaction_id": "1104245232004173824",
                    "amount": "USD 705.97",
                },
                "missing_fields": [],
                "internal_email_send_status": "sent",
                "internal_email_send_reason": "",
                "internal_email_payload": {
                    "delivery_key": f"billing:{account_case_id}:v1",
                    "subject": "[Billing Request] Detailed invoice request",
                    "body": "Please reply directly to this email in Outlook.",
                },
                "route_classification": {
                    "pipeline_version": "account-layered-router-v9",
                    "intent_class": "agora",
                    "agora_route": "account_billing",
                    "account_billing_subcategory": "detailed_invoice",
                    "handler_binding_status": "completed",
                    "primary_label": "Agora",
                    "secondary_label": "Account & Billing / Detailed Invoice",
                },
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
        self.repository.resolve_account_persona(ticket_id)
        payload = {
            "ticket_id": ticket_id,
            "account_case_id": account_case_id,
            "billing_ticket_id": account_case_id,
            "route": "detailed_invoice",
            "category": "account_billing",
            "subcategory": "detailed_invoice",
            "route_family": "automated",
            "route_status": "automated",
            "automation_status": "automation",
        }
        raw_token = f"legacy-response-token-{payload['ticket_id']}"
        self.repository.save_billing_response_token(
            {
                "token_hash": hash_billing_response_token(raw_token),
                "billing_ticket_id": payload["billing_ticket_id"],
                "created_at": "2026-07-18T00:00:00+00:00",
                "used_at": None,
            }
        )
        return payload, raw_token

    def test_account_full_reroute_job_is_persisted_and_duplicate_active_run_is_rejected(self) -> None:
        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as runner:
            response = self.client.post("/api/account/rerun-jobs")

            self.assertEqual(response.status_code, 202, response.text)
            created = response.json()
            self.assertEqual(created["status"], "queued")
            self.assertEqual(created["mode"], "fresh_case_rerun")
            self.assertEqual(created["reset_mode"], ACCOUNT_RERUN_RESET_AI_ONLY)
            self.assertEqual(created["persona_assignments_deleted"], 0)
            self.assertTrue(created["job_id"].startswith("account-rerun-"))
            runner.assert_awaited_once()
            self.assertEqual(runner.await_args.args[0], created["job_id"])
            self.assertTrue(str(runner.await_args.args[1]))

            latest = self.client.get("/api/account/rerun-jobs/latest")
            self.assertEqual(latest.status_code, 200, latest.text)
            latest_payload = latest.json()
            self.assertEqual(latest_payload["job_id"], created["job_id"])
            self.assertEqual(latest_payload["status"], "running")

            detail = self.client.get(f"/api/account/rerun-jobs/{created['job_id']}")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["job_id"], created["job_id"])
            self.assertEqual(detail.json()["status"], "running")

            duplicate = self.client.post("/api/account/rerun-jobs")
            self.assertEqual(duplicate.status_code, 409, duplicate.text)

    def test_full_reroute_admission_allows_unknown_automation_delivery(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12799",
                "customer_id": "customer@example.com",
                "subject": "Unknown delivery evidence",
                "status": "open",
                "messages": [],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12799",
                "client_ticket_id": "12799",
                # Legacy automated cases may only carry the old route family.
                "route_family": "billing_automation",
                "execution_action": "enablement",
                "internal_email_send_status": "sending",
                "internal_email_payload": {"delivery_key": "enablement:AC-12799:v1"},
            }
        )
        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as runner:
            response = self.client.post("/api/account/rerun-jobs")
        self.assertEqual(response.status_code, 202, response.text)
        runner.assert_awaited_once()
        self.assertEqual(len(self.repository.list_account_reroute_jobs()), 1)

    def test_account_rerun_admission_gate_blocks_full_and_single_jobs_in_both_directions(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12568",
                "customer_id": "customer@example.com",
                "subject": "Shared rerun admission gate",
                "status": "open",
                "messages": [],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12568",
                "client_ticket_id": "12568",
                "route_status": "automated",
                "route_family": "automated",
                "execution_action": "enablement",
                "internal_email_send_status": "not_ready",
            }
        )
        headers = {"Idempotency-Key": "shared-gate-single-12568"}

        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as runner:
            full_job = self.client.post("/api/account/rerun-jobs")
            blocked_single = self.client.post("/api/account/cases/12568/rerun", headers=headers)

            self.assertEqual(full_job.status_code, 202, full_job.text)
            self.assertEqual(blocked_single.status_code, 409, blocked_single.text)

            completed_at = main.now_iso()
            lease_token = str(runner.await_args.args[1])
            main._save_account_full_reroute_job(
                {
                    **full_job.json(),
                    "status": "completed",
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                },
                lease_token=lease_token,
            )
            single_job = self.client.post("/api/account/cases/12568/rerun", headers=headers)
            blocked_full = self.client.post("/api/account/rerun-jobs")

        self.assertEqual(single_job.status_code, 202, single_job.text)
        self.assertEqual(blocked_full.status_code, 409, blocked_full.text)
        self.assertEqual(runner.await_count, 2)

    def test_account_single_case_rerun_targets_only_requested_case(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12562",
                "customer_id": "customer@example.com",
                "subject": "Third-party compliance complaint",
                "status": "open",
                "messages": [{
                    "role": "customer",
                    "content": "A third-party fraud complaint asks Agora to extract server logs as evidence.",
                    "created_at": "2026-08-04T00:00:00+00:00",
                }],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12562",
                "client_ticket_id": "12562",
                "route_status": "not_automated",
                "route_family": "human_review",
            }
        )

        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as runner:
            response = self.client.post(
                "/api/account/cases/12562/rerun",
                headers={"Idempotency-Key": "single-case-12562-first"},
            )

        self.assertEqual(response.status_code, 202, response.text)
        payload = response.json()
        self.assertEqual(payload["scope"], "single_case")
        self.assertEqual(payload["target_case_ids"], ["AC-12562"])
        self.assertEqual(
            payload["reset_mode"],
            ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
        )
        self.assertEqual(payload["audit_actor_id"], "account_ui")
        self.assertEqual(payload["persona_assignments_deleted"], 0)
        runner.assert_awaited_once()
        self.assertEqual(runner.await_args.args[0], payload["job_id"])
        self.assertTrue(str(runner.await_args.args[1]))

        duplicate = self.client.post(
            "/api/account/cases/AC-12562/rerun",
            headers={"Idempotency-Key": "single-case-12562-second"},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

    def test_account_single_case_rerun_replays_same_job_and_schedules_worker_once(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12564",
                "customer_id": "customer@example.com",
                "subject": "Replay-safe Account Case",
                "status": "open",
                "messages": [],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12564",
                "client_ticket_id": "12564",
                "route_status": "automated",
                "route_family": "automated",
                "execution_action": "enablement",
            }
        )
        headers = {"Idempotency-Key": "single-case-12564-replay"}

        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as runner:
            first = self.client.post("/api/account/cases/12564/rerun", headers=headers)
            replay = self.client.post("/api/account/cases/AC-12564/rerun", headers=headers)

        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(replay.status_code, 202, replay.text)
        first_payload = first.json()
        replay_payload = replay.json()
        self.assertEqual(first_payload["status"], "queued")
        self.assertEqual(replay_payload["job_id"], first_payload["job_id"])
        self.assertEqual(replay_payload["status"], "running")
        runner.assert_awaited_once()
        self.assertEqual(runner.await_args.args[0], first_payload["job_id"])
        self.assertTrue(str(runner.await_args.args[1]))
        jobs = self.repository.list_account_reroute_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_id"], first_payload["job_id"])
        self.assertEqual(jobs[0]["status"], "running")
        self.assertEqual(
            self.repository.list_ticket_events(main.ACCOUNT_FULL_REROUTE_JOB_TICKET_ID),
            [],
        )

    def test_account_single_case_rerun_rejects_same_key_for_another_case(self) -> None:
        for ticket_id in ("12565", "12566"):
            self.repository.save_ticket(
                {
                    "ticket_id": ticket_id,
                    "customer_id": "customer@example.com",
                    "subject": "Scoped Account Case",
                    "status": "open",
                    "messages": [],
                }
            )
            self.repository.save_account_case(
                {
                    "account_case_id": f"AC-{ticket_id}",
                    "client_ticket_id": ticket_id,
                    "route_status": "automated",
                    "route_family": "automated",
                    "execution_action": "enablement",
                }
            )
        headers = {"Idempotency-Key": "single-case-cross-case-conflict"}

        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as runner:
            first = self.client.post("/api/account/cases/12565/rerun", headers=headers)
            conflict = self.client.post("/api/account/cases/12566/rerun", headers=headers)

        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["detail"]["code"], "idempotency_scope_conflict")
        runner.assert_awaited_once()

    def test_account_single_case_rerun_allows_no_key_but_rejects_a_malformed_key(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12567",
                "customer_id": "customer@example.com",
                "subject": "Validated Account Case",
                "status": "open",
                "messages": [],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12567",
                "client_ticket_id": "12567",
                "route_status": "automated",
                "route_family": "automated",
                "execution_action": "enablement",
            }
        )

        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as runner:
            missing = self.client.post("/api/account/cases/12567/rerun")
            malformed = self.client.post(
                "/api/account/cases/12567/rerun",
                headers={"Idempotency-Key": "contains spaces"},
            )
            empty = self.client.post(
                "/api/account/cases/12567/rerun",
                headers={"Idempotency-Key": ""},
            )

        self.assertEqual(missing.status_code, 202, missing.text)
        payload = missing.json()
        self.assertEqual(payload["scope"], "single_case")
        self.assertEqual(payload["target_case_ids"], ["AC-12567"])
        self.assertEqual(malformed.status_code, 422, malformed.text)
        self.assertEqual(malformed.json()["detail"]["code"], "invalid_idempotency_key")
        self.assertEqual(empty.status_code, 422, empty.text)
        self.assertEqual(empty.json()["detail"]["code"], "invalid_idempotency_key")
        runner.assert_awaited_once()
        stored = self.repository.get_account_reroute_job(payload["job_id"])
        assert stored is not None
        self.assertIsNone(stored.get("idempotency_scope"))
        self.assertIsNone(stored.get("idempotency_key"))

    def test_single_case_rerun_worker_does_not_process_other_cases(self) -> None:
        for ticket_id, case_id in (("12562", "AC-12562"), ("12563", "AC-12563")):
            self.repository.save_ticket(
                {
                    "ticket_id": ticket_id,
                    "customer_id": "customer@example.com",
                    "subject": "Account Case",
                    "status": "open",
                    "messages": [
                        {
                            "role": "customer",
                            "content": "A third-party compliance complaint asks Agora to extract logs.",
                            "created_at": "2026-08-04T00:00:00+00:00",
                        },
                        {
                            "role": "engineer",
                            "content": f"Private manual note for {ticket_id}",
                            "created_at": "2026-08-04T00:01:00+00:00",
                        },
                    ],
                }
            )
            self.repository.save_account_case(
                {
                    "account_case_id": case_id,
                    "client_ticket_id": ticket_id,
                    "route": "detailed_invoice",
                    "scope_label": "billing",
                    "execution_action": "detailed_invoice",
                    "route_status": "not_automated",
                    "route_family": "human_review",
                    "secondary_label": "Agora / Uncategorized",
                }
            )
        self.repository.resolve_account_persona("12562")

        result = SimpleNamespace(
            account_case={
                "account_case_id": "AC-12562",
                "client_ticket_id": "12562",
                "route": "human_review_required",
                "scope_label": "uncategorized",
                "route_family": "human_review",
                "execution_action": "human_review_required",
                "route_status": "not_automated",
                "category": "human_review",
                "subcategory": "human_review_required",
                "automation_handler": None,
                "route_classification": {
                    "intent_class": "agora",
                    "agora_route": "uncategorized",
                    "primary_label": "Agora",
                    "secondary_label": "Agora / Uncategorized",
                },
            },
            route_execution={"ticket_id": "12562", "classification": {}},
            changed=True,
            handler_status="not_automated",
            internal_email_to_send=None,
            email_handler=None,
            customer_reply="",
            reply_kind=None,
            asked_field_keys=(),
        )
        job = asyncio.run(
            main._enqueue_account_rerun_job(
                SimpleNamespace(add_task=lambda *args: None),
                target_case_ids=["AC-12562"],
            )
        )
        with patch.object(main, "reprocess_account_case", return_value=result) as reprocess:
            asyncio.run(main._run_account_full_reroute_job(job["job_id"]))

        reprocess.assert_called_once()
        self.assertEqual(reprocess.call_args.args[0]["account_case_id"], "AC-12562")
        self.assertEqual(reprocess.call_args.args[0]["route"], "detailed_invoice")
        self.assertEqual(reprocess.call_args.args[0]["scope_label"], "billing")
        self.assertEqual(reprocess.call_args.args[0]["execution_action"], "detailed_invoice")
        latest = main._account_full_reroute_job(job["job_id"])
        assert latest is not None
        self.assertEqual(latest["scope"], "single_case")
        self.assertEqual(latest["total"], 1)
        self.assertEqual(latest["processed"], 1)
        self.assertEqual(latest["persona_assignments_deleted"], 1)
        self.assertEqual(latest["route_counts"], {"Human Review / Uncategorized": 1})
        self.assertIsNone(self.repository.get_account_persona_assignment("12562"))
        self.assertEqual(
            self.repository.get_account_case("AC-12563")["secondary_label"],
            "Agora / Uncategorized",
        )
        execution = self.repository.list_account_route_executions("12562")[-1]
        self.assertEqual(execution["trigger"], "single_case_rerun")
        self.assertEqual(
            [message["role"] for message in self.repository.get_ticket("12562")["messages"]],
            ["customer", "engineer"],
        )
        self.assertEqual(
            [message["role"] for message in self.repository.get_ticket("12563")["messages"]],
            ["customer", "engineer"],
        )
        audit_events = self.repository.list_workspace_audit_events()
        self.assertEqual([event["event_type"] for event in audit_events], ["account_case_full_rerun_completed", "account_case_rerun_committed"])
        self.assertEqual(audit_events[0]["payload"]["persona_assignments_deleted"], 1)
        self.assertEqual(audit_events[1]["payload"]["persona_assignments_deleted"], 1)
        self.assertNotIn("Private manual note", json.dumps(audit_events))

    def test_account_case_lookup_uses_exact_ticket_number(self) -> None:
        for ticket_id in ("12572", "125720"):
            self.repository.save_ticket(
                {
                    "ticket_id": ticket_id,
                    "messages": [{"role": "customer", "content": f"Request {ticket_id}"}],
                }
            )
            self.repository.save_account_case(
                {
                    "account_case_id": f"AC-{ticket_id}",
                    "client_ticket_id": ticket_id,
                    "route_status": "not_automated",
                    "route_family": "human_review",
                }
            )

        exact = self.client.get("/api/account/cases/12572")
        prefix = self.client.get("/api/account/cases/1257")
        longer = self.client.get("/api/account/cases/1257200")

        self.assertEqual(exact.status_code, 200, exact.text)
        self.assertEqual(exact.json()["client_ticket_id"], "12572")
        self.assertEqual(prefix.status_code, 404, prefix.text)
        self.assertEqual(longer.status_code, 404, longer.text)

    def test_single_case_rerun_records_sanitized_failed_audit(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12574",
                "customer_id": "private-customer@example.com",
                "messages": [
                    {"role": "customer", "content": "Private customer message"},
                    {"role": "manual", "content": "Private manual message"},
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12574",
                "client_ticket_id": "12574",
                "route_status": "not_automated",
                "route_family": "human_review",
            }
        )
        job = asyncio.run(
            main._enqueue_account_rerun_job(
                SimpleNamespace(add_task=lambda *args: None),
                target_case_ids=["AC-12574"],
            )
        )

        with patch.object(
            main,
            "reprocess_account_case",
            side_effect=RuntimeError("private-customer@example.com could not route"),
        ):
            asyncio.run(main._run_account_full_reroute_job(job["job_id"]))

        latest = main._account_full_reroute_job(job["job_id"])
        assert latest is not None
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["stop_reason"], "case_degraded")
        self.assertEqual(latest["failed_case_id"], "AC-12574")
        audit_events = self.repository.list_workspace_audit_events()
        self.assertEqual(audit_events[0]["event_type"], "account_case_full_rerun_failed")
        audit_text = json.dumps(audit_events).lower()
        self.assertNotIn("private customer message", audit_text)
        self.assertNotIn("private manual message", audit_text)
        self.assertNotIn("private-customer@example.com", audit_text)

    def test_full_rerun_stops_before_processing_next_case_after_prepare_failure(self) -> None:
        for ticket_id in ("12576", "12577"):
            self.repository.save_ticket(
                {
                    "ticket_id": ticket_id,
                    "messages": [
                        {
                            "role": "customer",
                            "content": f"Route Case {ticket_id}",
                            "created_at": "2026-08-04T00:00:00+00:00",
                        }
                    ],
                }
            )
            self.repository.save_account_case(
                {
                    "account_case_id": f"AC-{ticket_id}",
                    "client_ticket_id": ticket_id,
                    "route_status": "not_automated",
                    "route_family": "human_review",
                }
            )
        job = asyncio.run(
            main._enqueue_account_rerun_job(
                SimpleNamespace(add_task=lambda *args: None),
            )
        )
        calls: list[str] = []

        def fail_first(account_case: dict[str, object], **_kwargs: object):
            calls.append(str(account_case.get("account_case_id") or ""))
            raise RuntimeError("first case failed")

        with patch.object(main, "reprocess_account_case", side_effect=fail_first):
            asyncio.run(main._run_account_full_reroute_job(job["job_id"]))

        latest = main._account_full_reroute_job(job["job_id"])
        assert latest is not None
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["stop_reason"], "case_degraded")
        self.assertEqual(latest["processed"], 1)
        self.assertEqual(latest["failed"], 1)
        self.assertEqual(latest["succeeded"], 0)
        self.assertEqual(latest["remaining"], 1)
        self.assertEqual(latest["remaining_case_ids"], [
            "AC-12576" if latest["failed_case_id"] == "AC-12577" else "AC-12577"
        ])
        self.assertEqual(latest["failed_stage"], "prepare")
        self.assertEqual(latest["retry_mode"], "prepare")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls, [latest["failed_case_id"]])
        self.assertIn(calls[0], {"AC-12576", "AC-12577"})
        self.assertEqual(latest["emails_sent"], 0)
        self.assertEqual(latest["replies_scheduled"], 0)

    def test_single_case_rerun_does_not_report_success_without_completion_audit(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12575",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Route this request",
                        "created_at": "2026-08-04T00:00:00+00:00",
                    }
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12575",
                "client_ticket_id": "12575",
                "route_status": "not_automated",
                "route_family": "human_review",
            }
        )
        job = asyncio.run(
            main._enqueue_account_rerun_job(
                SimpleNamespace(add_task=lambda *args: None),
                target_case_ids=["AC-12575"],
            )
        )
        result = SimpleNamespace(
            account_case={
                "account_case_id": "AC-12575",
                "client_ticket_id": "12575",
                "route": "human_review_required",
                "scope_label": "human_review",
                "route_family": "human_review",
                "execution_action": "human_review_required",
                "route_status": "not_automated",
                "route_classification": {
                    "primary_label": "Agora",
                    "secondary_label": "Agora / Uncategorized",
                },
            },
            route_execution={"ticket_id": "12575", "classification": {}},
            changed=True,
            handler_status="not_automated",
            internal_email_to_send=None,
            email_handler=None,
            customer_reply="",
            reply_kind=None,
            asked_field_keys=(),
        )
        original_record = self.repository.record_workspace_audit_event

        def record_with_completion_failure(event_type, **kwargs):
            if event_type == "account_case_full_rerun_completed":
                raise RuntimeError("completion audit unavailable")
            return original_record(event_type, **kwargs)

        with patch.object(main, "reprocess_account_case", return_value=result), patch.object(
            self.repository,
            "record_workspace_audit_event",
            side_effect=record_with_completion_failure,
        ), patch.object(main, "_ACCOUNT_RERUN_STORAGE_RETRY_DELAYS", (0.0,)):
            asyncio.run(main._run_account_full_reroute_job(job["job_id"]))

        latest = main._account_full_reroute_job(job["job_id"])
        assert latest is not None
        self.assertEqual(latest["status"], "failed")
        self.assertNotEqual(latest["status"], "completed")
        self.assertEqual(
            self.repository.list_workspace_audit_events()[0]["event_type"],
            "account_case_full_rerun_failed",
        )

    def test_rerun_preflight_failure_stops_before_case_side_effects(self) -> None:
        ticket_id = "preflight-stop-ticket"
        case_id = "AC-PREFLIGHT-STOP"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "customer@example.com",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please process this Account Case.",
                        "created_at": "2026-08-10T01:00:00+00:00",
                    }
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": case_id,
                "client_ticket_id": ticket_id,
                "route_status": "automated",
                "route_family": "automated",
                "automation_handler": "enablement",
            }
        )
        job = asyncio.run(
            main._enqueue_account_rerun_job(
                SimpleNamespace(add_task=lambda *args: None),
                target_case_ids=[case_id],
            )
        )
        with (
            patch.object(main, "run_account_rerun_preflight", return_value=_failed_account_rerun_preflight()),
            patch.object(self.repository, "reset_account_rerun_state") as reset_state,
            patch.object(main, "reprocess_account_case") as reprocess,
            patch.object(main, "_send_enablement_internal_email_attempt", AsyncMock()) as sender,
        ):
            asyncio.run(main._run_account_full_reroute_job(job["job_id"]))

        latest = main._account_full_reroute_job(job["job_id"])
        assert latest is not None
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["phase"], "Preflight")
        self.assertEqual(latest["error"], "preflight_account_model_failed")
        self.assertEqual(latest["total"], 1)
        self.assertEqual(latest["remaining"], 1)
        self.assertEqual(latest["remaining_case_ids"], ["AC-PREFLIGHT-STOP"])
        self.assertEqual(latest["processed"], 0)
        self.assertEqual(latest["succeeded"], 0)
        self.assertEqual(latest["failed"], 0)
        self.assertEqual(latest["emails_sent"], 0)
        self.assertEqual(latest["replies_scheduled"], 0)
        reset_state.assert_not_called()
        reprocess.assert_not_called()
        sender.assert_not_awaited()
        self.assertIsNone(self.repository.get_latest_account_reply_job(ticket_id))

    def test_account_full_reroute_returns_retryable_503_when_storage_is_unavailable(self) -> None:
        with patch.object(main, "_ACCOUNT_RERUN_STORAGE_RETRY_DELAYS", (0.0, 0.0, 0.0, 0.0)), patch.object(
            self.repository,
            "claim_account_case_rerun",
            side_effect=psycopg.OperationalError("ticket db pool acquire budget exhausted"),
        ), patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as runner:
            response = self.client.post("/api/account/rerun-jobs")

        self.assertEqual(response.status_code, 503, response.text)
        payload = response.json()
        self.assertEqual(payload["detail"]["code"], "account_storage_temporarily_unavailable")
        self.assertTrue(payload["detail"]["retryable"])
        self.assertIn("Retry-After", response.headers)
        runner.assert_not_awaited()

    def test_account_full_reroute_does_not_schedule_when_job_persistence_fails(self) -> None:
        with patch.object(main, "_ACCOUNT_RERUN_STORAGE_RETRY_DELAYS", (0.0, 0.0, 0.0, 0.0)), patch.object(
            self.repository,
            "claim_account_case_rerun",
            side_effect=psycopg.OperationalError("ticket db pool acquire budget exhausted"),
        ), patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as runner:
            response = self.client.post("/api/account/rerun-jobs")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "account_storage_temporarily_unavailable")
        runner.assert_not_awaited()

    def test_full_reroute_runner_saves_extraction_sends_once_and_schedules_confirmation(self) -> None:
        ticket = {
            "ticket_id": "12513",
            "customer_id": "customer@example.com",
            "requester": "customer@example.com",
            "subject": "Enable media relay",
            "status": "open",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable media relay for alpha.",
                    "created_at": "2026-07-31T08:30:00+00:00",
                }
            ],
        }
        account_case = {
            "account_case_id": "AC-12513",
            "billing_ticket_id": "AC-12513",
            "client_ticket_id": "12513",
            "route_status": "automated",
            "automation_handler": "enablement",
            "customer_reply": "Old Account reply",
            "internal_email_send_status": "sent",
            "internal_email_payload": {"delivery_key": "enablement:AC-12513:v1"},
        }
        self.repository.save_ticket(
            {
                **ticket,
                "messages": [
                    *ticket["messages"],
                    {
                        "role": "assistant",
                        "content": "Old Account reply",
                        "source": "account_ai",
                        "meta": {"account_reply_job_id": "old-account-reply"},
                        "created_at": "2026-07-31T08:31:00+00:00",
                    },
                ],
            }
        )
        self.repository.save_account_case(account_case)
        self.repository.save_account_reply_job(
            {
                "job_id": "old-account-reply",
                "ticket_id": "12513",
                "trigger_message_created_at": "2026-07-31T08:30:00+00:00",
                "status": "published",
                "scheduled_for": "2026-07-31T08:36:00+00:00",
                "payload": {},
            }
        )
        self.repository.save_account_reply_execution(
            {
                "execution_id": "reply-old-account-reply",
                "ticket_id": "12513",
                "payload": {"content": "Old Account reply"},
            }
        )
        with patch(
            "backend.repositories.ticket_repository._utc_now",
            return_value="2026-07-31T08:32:00+00:00",
        ), patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=lambda candidates: next(
                candidate for candidate in candidates if candidate["persona_key"] == "sid-bright"
            ),
        ):
            old_assignment = self.repository.resolve_account_persona("12513")
        created_at = main.now_iso()
        self._admit_account_reroute_job(
            {
                "job_id": "account-reroute-test",
                "scope": "all_cases",
                "status": "queued",
                "total": 0,
                "processed": 0,
                "succeeded": 0,
                "failed": 0,
                "changed": 0,
                "route_counts": {},
                "handler_counts": {},
                "emails_sent": 0,
                "emails_skipped": 0,
                "emails_failed": 0,
                "replies_scheduled": 0,
                "failures": [],
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
        updated_case = {
            **account_case,
            "route": "enablement",
            "execution_action": "enablement",
            "route_family": "automated",
            "route_status": "automated",
            "category": "automation",
            "subcategory": "enablement",
            "automation_handler": "enablement",
            "customer_reply": None,
            "internal_email_send_status": "pending",
            "internal_email_payload": {"delivery_key": "delivery-12513"},
            "collected_fields": {
                "app_id": "alpha",
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
            "route_classification": {
                "primary_label": "Agora",
                "secondary_label": "Backend Operation / Enablement",
            },
        }
        result = AccountFullRerouteResult(
            account_case=updated_case,
            route_execution={"ticket_id": "12513", "classification": updated_case["route_classification"]},
            changed=True,
            handler_status="completed",
            internal_email_to_send={
                "to": "internal@example.com",
                "subject": "Enablement",
                "body": "Request",
                "delivery_key": "delivery-12513",
            },
            email_handler="enablement",
            customer_reply="",
            reply_kind="submission_confirmation",
            asked_field_keys=(),
        )

        with patch.object(main, "reprocess_account_case", return_value=result), patch.object(
            main,
            "_send_enablement_internal_email_attempt",
            AsyncMock(return_value=("sent", "")),
        ) as sender, patch.object(
            main,
            "_wait_for_account_rerun_reply_preparation",
            AsyncMock(),
        ), patch(
            "backend.repositories.ticket_repository._utc_now",
            return_value="2026-08-01T00:00:00+00:00",
        ), patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=lambda candidates: next(
                candidate
                for candidate in candidates
                if candidate["persona_key"] == old_assignment["persona_key"]
            ),
        ) as chooser:
            asyncio.run(main._run_account_full_reroute_job("account-reroute-test"))

        sender.assert_awaited_once()
        self.assertEqual(chooser.call_count, 0)
        stored = self.repository.get_account_case("AC-12513")
        assert stored is not None
        self.assertEqual(stored["internal_email_send_status"], "sent")
        self.assertEqual(
            stored["internal_email_payload"]["delivery_key"],
            "delivery-12513:rerun:account-reroute-test",
        )
        reply_job = self.repository.get_latest_account_reply_job("12513")
        assert reply_job is not None
        self.assertEqual(reply_job["status"], "persona_v8_queued")
        self.assertEqual(reply_job["payload"]["rerun_job_id"], "account-reroute-test")
        self.assertEqual(
            reply_job["trigger_message_created_at"],
            "2026-07-31T08:30:00+00:00",
        )
        self.assertIsNone(self.repository.get_account_persona_assignment("12513"))
        self.assertEqual(self.repository.list_account_route_executions("12513")[-1]["rerun_mode"], "fresh_case_rerun")
        latest_job = main._account_full_reroute_job("account-reroute-test")
        assert latest_job is not None
        self.assertEqual(latest_job["status"], "completed")
        self.assertEqual(latest_job["emails_sent"], 1)
        self.assertEqual(latest_job["replies_scheduled"], 1)
        self.assertEqual(latest_job["reply_jobs_deleted"], 0)
        self.assertEqual(latest_job["reply_executions_deleted"], 0)
        self.assertEqual(latest_job["persona_assignments_deleted"], 1)
        self.assertIsNotNone(self.repository.get_account_reply_job("old-account-reply"))
        self.assertEqual(len(self.repository.list_account_reply_executions("12513")), 1)
        stored_ticket = self.repository.get_ticket("12513")
        assert stored_ticket is not None
        self.assertEqual(
            [message["role"] for message in stored_ticket["messages"]],
            ["customer"],
        )

    def test_rerun_wait_marks_cancelled_reply_as_publish_failure(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12513-CANCELLED",
                "customer_id": "customer@example.com",
                "subject": "Stale confirmation",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please handle this request.",
                        "created_at": "2026-08-01T00:00:00+00:00",
                    }
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12513-CANCELLED",
                "billing_ticket_id": "AC-12513-CANCELLED",
                "client_ticket_id": "12513-CANCELLED",
                "route_status": "automated",
                "route_family": "automated",
                "automation_handler": "enablement",
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "account-reply-cancelled",
                "ticket_id": "12513-CANCELLED",
                "trigger_message_created_at": "2026-07-31T00:00:00+00:00",
                "status": "cancelled",
                "scheduled_for": "2026-08-01T00:06:00+00:00",
                "payload": {
                    "rerun_job_id": "account-rerun-cancelled",
                    "cancel_reason": "stale_customer_revision",
                },
            }
        )
        job = {
            "job_id": "account-rerun-cancelled",
            "reply_job_ids": ["account-reply-cancelled"],
            "wait_for_replies": True,
            "failures": [],
            "failed_stage": None,
            "failed_case_id": None,
            "stop_reason": None,
            "stop_error": None,
        }
        with patch.object(main, "_save_account_full_reroute_job_with_retry", AsyncMock()):
            finished = asyncio.run(
                main._wait_for_account_rerun_replies(job, lease_token="lease")
            )

        self.assertTrue(finished)
        self.assertEqual(job["reply_jobs_cancelled"], 1)
        self.assertEqual(job["reply_cancelled_case_ids"], ["AC-12513-CANCELLED"])
        self.assertEqual(job["failed_case_id"], "AC-12513-CANCELLED")
        self.assertEqual(job["failed_stage"], "reply_publish")
        self.assertEqual(job["stop_reason"], "reply_publish_failed")
        self.assertEqual(job["stop_error"], "stale_customer_revision")

    def test_full_reroute_automated_internal_email_without_customer_reply_does_not_pin_persona(self) -> None:
        ticket_id = "12513-NO-REPLY"
        account_case_id = "AC-12513-NO-REPLY"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "customer@example.com",
                "requester": "customer@example.com",
                "subject": "Enablement without confirmation",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Process this internally without a customer reply.",
                        "created_at": "2026-07-31T09:00:00+00:00",
                    }
                ],
            }
        )
        account_case = {
            "account_case_id": account_case_id,
            "billing_ticket_id": account_case_id,
            "client_ticket_id": ticket_id,
            "route": "enablement",
            "scope_label": "automation",
            "route_family": "automated",
            "execution_action": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "automation_status": "automation",
            "internal_email_send_status": "not_ready",
        }
        self.repository.save_account_case(account_case)
        self.repository.resolve_account_persona(ticket_id)
        job = asyncio.run(
            main._enqueue_account_rerun_job(SimpleNamespace(add_task=lambda *args: None))
        )
        updated_case = {
            **account_case,
            "category": "automation",
            "subcategory": "enablement",
            "internal_email_send_status": "pending",
            "internal_email_payload": {"delivery_key": "delivery-no-reply"},
            "route_classification": {
                "primary_label": "Agora",
                "secondary_label": "Backend Operation / Enablement",
            },
        }
        result = AccountFullRerouteResult(
            account_case=updated_case,
            route_execution={"ticket_id": ticket_id, "classification": updated_case["route_classification"]},
            changed=True,
            handler_status="completed",
            internal_email_to_send={
                "to": "internal@example.com",
                "subject": "Enablement",
                "body": "Request",
                "delivery_key": "delivery-no-reply",
            },
            email_handler="enablement",
            customer_reply="",
            reply_kind=None,
            asked_field_keys=(),
        )
        original_resolve = self.repository.resolve_account_persona

        with patch.object(main, "reprocess_account_case", return_value=result), patch.object(
            main,
            "_send_enablement_internal_email_attempt",
            AsyncMock(return_value=("sent", "")),
        ) as sender, patch.object(
            self.repository,
            "resolve_account_persona",
            wraps=original_resolve,
        ) as resolve:
            asyncio.run(main._run_account_full_reroute_job(job["job_id"]))

        sender.assert_awaited_once()
        resolve.assert_not_called()
        self.assertIsNone(self.repository.get_account_persona_assignment(ticket_id))
        self.assertIsNone(self.repository.get_latest_account_reply_job(ticket_id))
        latest_job = main._account_full_reroute_job(job["job_id"])
        assert latest_job is not None
        self.assertEqual(latest_job["status"], "completed")
        self.assertEqual(latest_job["persona_assignments_deleted"], 1)
        self.assertEqual(latest_job["replies_scheduled"], 0)

    def test_full_reroute_defers_persona_resolution_until_worker_after_email(self) -> None:
        ticket = {
            "ticket_id": "12514",
            "customer_id": "customer@example.com",
            "requester": "customer@example.com",
            "subject": "Enable media relay",
            "status": "open",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable media relay for alpha.",
                    "created_at": "2026-07-31T08:30:00+00:00",
                }
            ],
        }
        account_case = {
            "account_case_id": "AC-12514",
            "billing_ticket_id": "AC-12514",
            "client_ticket_id": "12514",
            "route_status": "automated",
            "automation_handler": "enablement",
            "internal_email_send_status": "pending",
            "internal_email_payload": {"delivery_key": "enablement:AC-12514:v1"},
        }
        self.repository.save_ticket(ticket)
        self.repository.save_account_case(account_case)
        self.repository.resolve_account_persona("12514")
        created_at = main.now_iso()
        self._admit_account_reroute_job(
            {
                "job_id": "account-reroute-persona-unavailable",
                "status": "queued",
                "total": 0,
                "processed": 0,
                "succeeded": 0,
                "failed": 0,
                "changed": 0,
                "route_counts": {},
                "handler_counts": {},
                "emails_sent": 0,
                "emails_skipped": 0,
                "emails_failed": 0,
                "replies_scheduled": 0,
                "failures": [],
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
        updated_case = {
            **account_case,
            "route": "enablement",
            "execution_action": "enablement",
            "route_family": "automated",
            "route_status": "automated",
            "category": "automation",
            "subcategory": "enablement",
            "automation_handler": "enablement",
            "customer_reply": None,
            "internal_email_send_status": "pending",
            "internal_email_payload": {"delivery_key": "delivery-12514"},
            "collected_fields": {
                "app_id": "alpha",
                "requested_feature": "media_relay",
            },
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "backend_operation",
                "account_billing_subcategory": None,
                "backend_operation_subcategory": "enablement",
                "automation_subcategory": None,
                "primary_label": "Agora",
                "secondary_label": "Backend Operation / Enablement",
                "route_target": "automation",
            },
        }
        result = AccountFullRerouteResult(
            account_case=updated_case,
            route_execution={"ticket_id": "12514", "classification": updated_case["route_classification"]},
            changed=True,
            handler_status="completed",
            internal_email_to_send={
                "to": "internal@example.com",
                "subject": "Enablement",
                "body": "Request",
                "delivery_key": "delivery-12514",
            },
            email_handler="enablement",
            customer_reply="",
            reply_kind="submission_confirmation",
            asked_field_keys=(),
        )
        call_order: list[str] = []

        def unavailable_persona(_ticket_id: str) -> dict[str, object]:
            call_order.append("persona")
            raise AccountPersonaUnavailableError("no enabled published persona")

        async def unexpected_email(_attempt: dict[str, object]) -> tuple[str, str]:
            call_order.append("email")
            return "sent", ""

        with patch.object(main, "reprocess_account_case", return_value=result), patch.object(
            self.repository,
            "resolve_account_persona",
            side_effect=unavailable_persona,
        ), patch.object(
            main,
            "_send_enablement_internal_email_attempt",
            AsyncMock(side_effect=unexpected_email),
        ) as sender, patch.object(
            main,
            "_wait_for_account_rerun_reply_preparation",
            AsyncMock(),
        ):
            asyncio.run(main._run_account_full_reroute_job("account-reroute-persona-unavailable"))

        self.assertEqual(call_order, ["email"])
        sender.assert_awaited_once()
        stored = self.repository.get_account_case("AC-12514")
        assert stored is not None
        self.assertEqual(stored["route"], "enablement")
        self.assertEqual(stored["automation_status"], "automation")
        self.assertEqual(stored["route_family"], "automated")
        self.assertEqual(stored["route_status"], "automated")
        self.assertEqual(stored["automation_handler"], "enablement")
        self.assertNotEqual(stored.get("execution_reason_code"), "enablement_persona_unavailable")
        self.assertEqual(stored["route_classification"]["primary_label"], "Agora")
        self.assertEqual(
            stored["route_classification"]["secondary_label"],
            "Backend Operation / Enablement",
        )
        reply_job = self.repository.get_latest_account_reply_job("12514")
        assert reply_job is not None
        self.assertEqual(reply_job["status"], "persona_v8_queued")
        self.assertEqual(reply_job["payload"]["rerun_job_id"], "account-reroute-persona-unavailable")
        executions = self.repository.list_account_route_executions("12514")
        self.assertEqual(len(executions), 1)
        self.assertNotIn("execution_reason_code", executions[0]["classification"])
        rerun_job = main._account_full_reroute_job("account-reroute-persona-unavailable")
        assert rerun_job is not None
        self.assertEqual(rerun_job["status"], "completed")
        self.assertEqual(rerun_job["succeeded"], 1)
        self.assertEqual(rerun_job["failed"], 0)
        self.assertEqual(rerun_job["persona_assignments_deleted"], 1)
        self.assertIsNone(self.repository.get_account_persona_assignment("12514"))

    def test_account_rerun_storage_call_retries_transient_pool_failure(self) -> None:
        attempts = 0

        def flaky_operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise psycopg.OperationalError("ticket db pool acquire budget exhausted")
            return "ok"

        with patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
            result = asyncio.run(main._account_rerun_storage_call(flaky_operation))

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)
        sleep.assert_awaited_once_with(1.0)

    def test_account_rerun_cancellation_is_scoped_to_rerun_job(self) -> None:
        for job_id, payload in (
            ("reply-current-message", {}),
            ("reply-rerun-message", {"rerun_job_id": "account-rerun-scope-test"}),
        ):
            self.repository.save_account_reply_job(
                {
                    "job_id": job_id,
                    "ticket_id": "12555",
                    "trigger_message_created_at": "2026-08-01T09:19:38+00:00",
                    "status": "scheduled",
                    "scheduled_for": "2026-08-04T10:16:52+00:00",
                    "payload": payload,
                }
            )

        cancelled = self.repository.cancel_pending_account_reply_jobs(
            "12555",
            updated_at="2026-08-04T10:17:00+00:00",
            rerun_job_id="account-rerun-scope-test",
        )

        self.assertEqual(cancelled, 1)
        self.assertEqual(self.repository.get_account_reply_job("reply-current-message")["status"], "scheduled")
        self.assertEqual(self.repository.get_account_reply_job("reply-rerun-message")["status"], "cancelled")

    def test_account_rerun_reconciles_case_that_finished_after_final_save_failure(self) -> None:
        job_id = "account-rerun-reconcile-test"
        self.repository.save_ticket(
            {
                "ticket_id": "12555",
                "customer_id": "customer@example.com",
                "requester": "customer@example.com",
                "subject": "Enable media relay",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable media relay.",
                        "created_at": "2026-08-01T09:19:38+00:00",
                    }
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12555",
                "billing_ticket_id": "AC-12555",
                "client_ticket_id": "12555",
                "route_family": "automated",
                "route_status": "automated",
                "category": "automation",
                "subcategory": "enablement",
                "automation_handler": "enablement",
                "internal_email_send_status": "sent",
                "internal_email_payload": {
                    "delivery_key": f"enablement:AC-12555:v1:rerun:{job_id}",
                },
            }
        )
        self.repository.save_account_route_execution(
            {
                "ticket_id": "12555",
                "rerun_job_id": job_id,
                "route": "enablement",
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "account-reply-reconcile-test",
                "ticket_id": "12555",
                "trigger_message_created_at": "2026-08-01T09:19:38+00:00",
                "status": "published",
                "scheduled_for": "2026-08-04T10:16:52+00:00",
                "payload": {"rerun_job_id": job_id},
            }
        )
        job = {
            "job_id": job_id,
            "failed": 1,
            "succeeded": 43,
            "recovered": 0,
            "changed": 43,
            "route_counts": {},
            "handler_counts": {},
            "reply_job_ids": ["account-reply-reconcile-test"],
            "failures": [
                {
                    "account_case_id": "AC-12555",
                    "client_ticket_id": "12555",
                    "error": "ticket db pool acquire budget exhausted after 20.00 sec",
                    "retryable": True,
                    "stage": "finalizing",
                    "reply_expected": True,
                    "email_expected": True,
                    "reply_job_id": "account-reply-reconcile-test",
                    "changed": True,
                    "route_key": "Agora / Backend Operation / Enablement",
                    "handler_status": "active",
                }
            ],
        }

        asyncio.run(main._reconcile_account_rerun_failures(job))

        self.assertEqual(job["failed"], 0)
        self.assertEqual(job["succeeded"], 44)
        self.assertEqual(job["recovered"], 1)
        self.assertEqual(job["changed"], 44)
        self.assertEqual(job["recovered_cases"], ["AC-12555"])
        self.assertTrue(job["failures"][0]["recovered"])

    def _save_billing_ticket(
        self,
        *,
        ticket_id: str,
        automation_status: str,
        automation_subcategory: str = "enablement",
        route_confidence: float = 0.95,
        secondary_label: str | None = None,
        intent_class: str | None = None,
        conversation_action: str | None = None,
        persist_route_labels: bool = True,
    ) -> None:
        route_classification: dict[str, Any] = {}
        if secondary_label:
            normalized_intent = intent_class or (
                "uncertain" if secondary_label == "Human Review" else "agora"
            )
            route_classification = {
                "intent_class": normalized_intent,
                "agora_route": {
                    "Agora Technical": "technical",
                    "Agora Non-technical": "non_technical",
                    "Account & Billing": "account_billing",
                    "Agora / Uncategorized": "uncategorized",
                }.get(secondary_label, "uncategorized"),
                "conversation_action": conversation_action,
            }
            if persist_route_labels:
                route_classification.update(
                    primary_label=(
                        "Uncertain"
                        if normalized_intent == "uncertain"
                        else "Conversation"
                        if normalized_intent == "conversation"
                        else "Agora"
                    ),
                    secondary_label=secondary_label,
                )
        normalized_subcategory = (
            automation_subcategory.strip().lower()
            if automation_status == "automation"
            else ""
        )
        if normalized_subcategory not in {"fraud_account", "enablement", "detailed_invoice", "quota"}:
            normalized_subcategory = "enablement" if automation_status == "automation" else ""
        if automation_status == "automation":
            if normalized_subcategory == "fraud_account":
                scope_label = "account_billing"
                tooling_profile = "deterministic_billing_intake"
            elif normalized_subcategory in {"detailed_invoice", "quota"}:
                scope_label = "account_billing" if normalized_subcategory == "detailed_invoice" else "backend_operation"
                tooling_profile = "deterministic_billing_intake"
            else:
                scope_label = "backend_operation"
                tooling_profile = "deterministic_enablement_intake"
            route = normalized_subcategory
            route_family = "automated"
            execution_action = normalized_subcategory
        else:
            scope_label = "agora_non_technical"
            tooling_profile = "official_web_search"
            route = "web_search"
            route_family = "web_company_info"
            execution_action = "web_search"
        if automation_status == "automation" and normalized_subcategory in {"fraud_account", "enablement"}:
            route_classification.update(
                {
                    "intent_class": "agora",
                    "agora_route": "account_billing" if normalized_subcategory == "fraud_account" else "backend_operation",
                    "account_billing_subcategory": normalized_subcategory if normalized_subcategory == "fraud_account" else None,
                    "backend_operation_subcategory": normalized_subcategory if normalized_subcategory == "enablement" else None,
                }
            )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": f"BT-{ticket_id}",
                "client_ticket_id": ticket_id,
                "source": "manual",
                "title": f"Ticket {ticket_id}",
                "question": "q",
                "automation_status": automation_status,
                "route": route,
                "scope_label": scope_label,
                "route_family": route_family,
                "execution_action": execution_action,
                "tooling_profile": tooling_profile,
                "route_confidence": route_confidence,
                "route_classification": route_classification,
            }
        )

    def test_account_intake_classifies_detailed_invoice_without_automation(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_email:
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send the detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["route"], "human_review_required")
        self.assertTrue(payload["account_case_id"].startswith("AC-"))
        self.assertEqual(payload["billing_ticket_id"], payload["account_case_id"])
        self.assertEqual(payload["route_family"], "human_review")
        self.assertEqual(payload["category"], "account_billing")
        self.assertEqual(payload["subcategory"], "detailed_invoice")
        self.assertEqual(payload["route_status"], "not_automated")
        self.assertIsNone(payload["automation_handler"])
        self.assertEqual(payload["secondary_label"], "Account & Billing / Detailed Invoice")
        self.assertEqual(payload["route_classification"]["route_target"], "human_review")
        self.assertTrue(str(payload["ticket_id"] or "").startswith("TK-ACC-"))
        self.assertEqual(payload["missing_fields"], [])
        self.assertEqual(payload["customer_reply"], "")
        self.assertIsNone(payload["ai_reply_status"])
        self.assertEqual(payload["internal_email_send_status"], "not_applicable")
        send_email.assert_not_called()

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["subject"], "Detailed invoice request")
        self.assertEqual(ticket["requester"], "customer@example.com")
        self.assertEqual(ticket["customer_id"], "customer@example.com")
        self.assertEqual(ticket["source"], "manual")
        self.assertEqual([message["role"] for message in ticket["messages"]], ["customer"])
        self.assertEqual(len(ticket["messages"]), 1)

        event_payloads = [
            item["payload"]
            for item in self.repository.list_ticket_events(payload["ticket_id"])
            if item["event_type"] == "ticket_created"
        ]
        self.assertTrue(event_payloads)
        self.assertEqual(event_payloads[0]["source"], "manual")
        self.assertEqual(event_payloads[0]["execution_action"], "human_review_required")
        self.assertEqual(event_payloads[0]["account_intake_status"], "not_automated")
        executions = self.repository.list_account_route_executions(payload["ticket_id"])
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0]["final_route"], "human_review_required")
        self.assertEqual(executions[0]["router_prompt_version"], "account-layered-router-v10")
        self.assertEqual(executions[0]["classification"]["intent_class"], "agora")
        self.assertTrue(executions[0]["prompt_snapshot_available"])
        self.assertIn(
            "Detailed invoice request",
            executions[0]["prompt_snapshots"]["intent_classifier"]["user_prompt"],
        )
        assignment = self.repository.get_account_persona_assignment(payload["ticket_id"])
        self.assertIsNone(assignment)

    def test_account_intake_routes_enablement_and_sends_internal_request(self) -> None:
        question = (
            "My App ID is : 7da36383d624411698e5c0bc1fda6324. We enabled co-host token authentication "
            "but PK view does not show, so please enable Media Relay from your end."
        )
        with patch.dict(os.environ, {"ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": "enablement@example.com"}, clear=False), patch.object(
            main, "dispatch_event", AsyncMock()
        ), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_email:
            response = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": question,
                    "customer_email": "customer@example.com",
                    "customer_name": "Jack Gold",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["category"], "backend_operation")
        self.assertEqual(payload["subcategory"], "enablement")
        self.assertEqual(payload["route_family"], "automated")
        self.assertEqual(payload["route_status"], "automated")
        self.assertEqual(payload["automation_handler"], "enablement")
        self.assertEqual(payload["customer_name"], "Jack Gold")
        self.assertEqual(payload["semantic_intent"], "backend_operation.enablement")
        self.assertEqual(payload["collected_fields"]["app_id"], "7da36383d624411698e5c0bc1fda6324")
        self.assertEqual(payload["collected_fields"]["requested_feature"], "media_relay")
        self.assertEqual(payload["internal_email_send_status"], "sent")
        self.assertEqual(payload["ai_reply_status"], "queued")
        reply_job = self.repository.get_latest_account_reply_job(payload["ticket_id"])
        self.assertIsNotNone(reply_job)
        assert reply_job is not None
        self.assertEqual(reply_job["status"], worker.ACCOUNT_REPLY_PERSONA_V8_QUEUED)
        self.assertEqual(reply_job["payload"]["reply_pipeline"], worker.ACCOUNT_REPLY_PERSONA_PIPELINE)
        self.assertEqual(reply_job["payload"]["reply_facts"]["customer_first_name"], "Jack")
        self.assertEqual(reply_job["payload"]["reply_facts"]["performed_actions"], [])
        self.assertEqual(
            reply_job["payload"]["reply_facts"]["resolution_status"],
            "internal_review_in_progress",
        )
        self.assertEqual(
            reply_job["payload"]["reply_facts"]["ownership_state"],
            "support_owned_internal_review",
        )
        send_email.assert_called_once()
        email_payload = send_email.call_args.args[0]
        self.assertEqual(email_payload["to"], "")
        self.assertEqual(email_payload["recipient_config_key"], "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL")
        self.assertIn("[Enablement Request]", email_payload["subject"])
        self.assertIn(payload["ticket_id"], email_payload["body"])

    def test_account_intake_persona_unavailable_persists_human_review_without_automation_side_effects(self) -> None:
        unhandled_client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            self.repository,
            "resolve_account_persona",
            side_effect=AccountPersonaUnavailableError("no enabled published persona"),
        ), patch.object(
            main,
            "_send_enablement_internal_email_attempt",
            AsyncMock(),
        ) as sender:
            response = unhandled_client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": (
                        "My App ID is : 7da36383d624411698e5c0bc1fda6324. "
                        "Please enable Media Relay from your end."
                    ),
                    "customer_email": "customer@example.com",
                },
            )
        unhandled_client.close()

        self.assertEqual(response.status_code, 200, response.text)
        sender.assert_not_awaited()
        payload = response.json()
        self.assertEqual(payload["status"], "human_review_required")
        self.assertEqual(payload["route"], "enablement")
        self.assertEqual(payload["route_family"], "automated")
        self.assertEqual(payload["route_status"], "automated")
        self.assertEqual(payload["category"], "backend_operation")
        self.assertEqual(payload["subcategory"], "enablement")
        self.assertEqual(payload["automation_handler"], "enablement")
        self.assertEqual(payload["execution_reason_code"], "enablement_persona_unavailable")
        self.assertIsNone(self.repository.get_latest_account_reply_job(payload["ticket_id"]))
        account_case = self.repository.get_account_case(payload["account_case_id"])
        assert account_case is not None
        self.assertEqual(account_case["route_classification"]["handler_binding_status"], "human_review")
        self.assertEqual(account_case["route_classification"]["primary_label"], "Agora")
        self.assertEqual(
            account_case["route_classification"]["secondary_label"],
            "Backend Operation / Enablement",
        )

    def test_support_message_persona_unavailable_returns_human_review_without_rendering(self) -> None:
        resolution = SupportResolution(
            answer="",
            confidence=0.9,
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="enablement",
            scope_label="automation",
            route_reason="registered_enablement",
            route_confidence=0.9,
            search_used=False,
            route_family="automated",
            execution_action="enablement",
            evidence_summary={
                "enablement_missing_fields": ["app_id"],
                "enablement_collected_fields": {},
                "enablement_internal_email_send_status": "not_ready",
                "enablement_requires_human_review": False,
            },
        )
        with patch.object(main, "resolve_support_route_message", return_value=resolution), patch.object(
            self.repository,
            "resolve_account_persona",
            side_effect=AccountPersonaUnavailableError("no enabled published persona"),
        ), patch.object(main, "render_automation_reply") as render:
            rendered = main.resolve_support_message(
                "Please enable media relay.",
                ticket_id="12515",
            )

        self.assertEqual(rendered.answer, "")
        self.assertEqual(rendered.route_family, "human_review")
        self.assertEqual(rendered.route_reason, "no enabled published persona")
        self.assertIs(rendered.evidence_summary["account_persona_unavailable"], True)
        render.assert_not_called()

    def test_support_message_persona_render_failure_does_not_mark_persona_unavailable(self) -> None:
        resolution = SupportResolution(
            answer="",
            confidence=0.9,
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="enablement",
            scope_label="automation",
            route_reason="registered_enablement",
            route_confidence=0.9,
            search_used=False,
            route_family="automated",
            execution_action="enablement",
            evidence_summary={
                "enablement_missing_fields": ["app_id"],
                "enablement_collected_fields": {},
                "enablement_internal_email_send_status": "not_ready",
                "enablement_requires_human_review": False,
            },
        )
        with patch.object(main, "resolve_support_route_message", return_value=resolution), patch.object(
            self.repository,
            "get_account_case_by_ticket_id",
            return_value={"customer_name": "Alice"},
        ), patch.object(
            self.repository,
            "resolve_account_persona",
            return_value={"persona_key": "helpful", "version": 1, "content": {}},
        ), patch.object(
            main,
            "render_automation_reply",
            side_effect=AutomationPersonaError("persona render failed"),
        ):
            rendered = main.resolve_support_message(
                "Please enable media relay.",
                ticket_id="12515",
            )

        self.assertEqual(rendered.answer, "")
        self.assertEqual(rendered.route_family, "human_review")
        self.assertEqual(rendered.route_reason, "persona_render_failed")
        self.assertNotIn("account_persona_unavailable", rendered.evidence_summary)

    def test_billing_resolution_persona_unavailable_stops_customer_copy(self) -> None:
        customer_message_created_at = "2026-08-08T00:00:00+00:00"
        billing_ticket = {
            "billing_ticket_id": "AC-12516",
            "account_case_id": "AC-12516",
            "client_ticket_id": "12516",
            "execution_action": "detailed_invoice",
            "route": "detailed_invoice",
            "route_family": "automated",
            "category": "account_billing",
            "subcategory": "detailed_invoice",
            "route_status": "automated",
            "automation_handler": "billing",
            "tooling_profile": "deterministic_billing_intake",
            "automation_status": "automation",
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "account_billing",
                "account_billing_subcategory": "detailed_invoice",
                "backend_operation_subcategory": None,
                "automation_subcategory": None,
                "route_target": "automation",
                "handler_binding_status": "active",
                "primary_label": "Agora",
                "secondary_label": "Account & Billing / Detailed Invoice",
            },
            "customer_name": "Alice",
        }
        self.repository.save_ticket(
            {
                "ticket_id": "12516",
                "customer_id": "alice@example.com",
                "requester": "alice@example.com",
                "subject": "Invoice request",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please send the invoice.",
                        "created_at": customer_message_created_at,
                    }
                ],
            }
        )
        self.repository.save_billing_ticket(billing_ticket)
        self.repository.save_account_reply_job(
            {
                "job_id": "account-reply-billing-unavailable",
                "ticket_id": "12516",
                "trigger_message_created_at": customer_message_created_at,
                "status": "persona_scheduled",
                "scheduled_for": "2026-08-08T00:01:00+00:00",
                "payload": {
                    "generated_content": "This delayed reply must not be sent.",
                    "effective_prompt": {"instruction": "Pinned prompt"},
                    "persona_key": "default-support",
                    "persona_version": 1,
                },
                "created_at": "2026-08-08T00:00:30+00:00",
            }
        )
        with patch.object(
            self.repository,
            "resolve_account_persona",
            side_effect=AccountPersonaUnavailableError("no enabled published persona"),
        ), patch.object(main, "render_automation_reply") as render:
            reply = main._render_billing_resolution_customer_reply(
                billing_ticket=billing_ticket,
                note="The detailed invoice is ready.",
                customer_message="Please send the invoice.",
                title="Invoice request",
                ticket_id="12516",
            )

        original_publish = self.repository.publish_account_reply
        with patch.object(
            self.repository,
            "publish_account_reply",
            wraps=original_publish,
        ) as publish:
            claimed = self.repository.claim_account_reply_jobs(
                from_status="persona_scheduled",
                to_status="persona_publishing",
                now_value="2026-08-08T00:02:00+00:00",
                limit=10,
                due_only=False,
            )
            for claimed_job in claimed:
                worker._publish_account_reply_job(claimed_job)

        self.assertEqual(reply, "")
        render.assert_not_called()
        stored = self.repository.get_billing_ticket("AC-12516")
        stored_job = self.repository.get_account_reply_job(
            "account-reply-billing-unavailable"
        )
        assert stored is not None
        assert stored_job is not None
        self.assertEqual(stored_job["status"], "cancelled")
        self.assertEqual(claimed, [])
        publish.assert_not_called()
        messages = self.repository.get_ticket("12516")["messages"]
        self.assertFalse(
            any(
                str(message.get("source") or "") == "account_ai"
                for message in messages
                if isinstance(message, dict)
            )
        )
        self.assertEqual(stored["route"], "detailed_invoice")
        self.assertEqual(stored["automation_status"], "human_review_required")
        self.assertEqual(stored["route_family"], "automated")
        self.assertEqual(stored["category"], "account_billing")
        self.assertEqual(stored["subcategory"], "detailed_invoice")
        self.assertEqual(stored["route_status"], "automated")
        self.assertEqual(stored["automation_handler"], "billing")
        self.assertEqual(stored["execution_reason_code"], "billing_persona_unavailable")
        self.assertEqual(stored["route_classification"]["handler_binding_status"], "human_review")

    def test_claimed_delayed_reply_cannot_publish_after_case_moves_to_human_review(self) -> None:
        ticket_id = "12516-CLAIMED"
        job_id = "account-reply-billing-claimed"
        trigger_created_at = "2026-08-08T00:10:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "claimed@example.com",
                "requester": "claimed@example.com",
                "subject": "Claimed invoice reply",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please send the invoice.",
                        "created_at": trigger_created_at,
                    }
                ],
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": f"AC-{ticket_id}",
                "account_case_id": f"AC-{ticket_id}",
                "client_ticket_id": ticket_id,
                "route": "human_review_required",
                "scope_label": "human_review",
                "route_family": "human_review",
                "execution_action": "human_review_required",
                "category": "human_review",
                "subcategory": "human_review_required",
                "route_status": "not_automated",
                "automation_handler": None,
                "automation_status": "not_automated",
                "policy_decision": "account_persona_unavailable_human_review",
                "not_automated_reason": "no enabled published persona",
                "route_reason": "no enabled published persona",
            }
        )
        job = self.repository.save_account_reply_job(
            {
                "job_id": job_id,
                "ticket_id": ticket_id,
                "trigger_message_created_at": trigger_created_at,
                "status": "persona_publishing",
                "scheduled_for": "2026-08-08T00:11:00+00:00",
                "payload": {
                    "generated_content": "This claimed reply must not be sent.",
                    "effective_prompt": {"instruction": "Pinned prompt"},
                    "persona_key": "default-support",
                    "persona_version": 1,
                },
                "claimed_at": "2026-08-08T00:11:00+00:00",
                "created_at": "2026-08-08T00:10:30+00:00",
            }
        )

        worker._publish_account_reply_job(job)

        stored_job = self.repository.get_account_reply_job(job_id)
        stored_ticket = self.repository.get_ticket(ticket_id)
        stored_case = self.repository.get_billing_ticket(f"AC-{ticket_id}")
        assert stored_job is not None
        assert stored_ticket is not None
        assert stored_case is not None
        self.assertEqual(stored_job["status"], "manual_attention")
        self.assertEqual(stored_job["payload"]["persona_render_status"], "human_review")
        self.assertEqual(
            stored_job["payload"]["error"],
            "no enabled published persona",
        )
        self.assertEqual(
            [
                message
                for message in stored_ticket["messages"]
                if str(message.get("source") or "") == "account_ai"
            ],
            [],
        )
        self.assertFalse(stored_case.get("customer_reply"))
        self.assertEqual(self.repository.list_account_reply_executions(ticket_id), [])

    def test_reset_deleted_claimed_job_cannot_be_recreated_by_stale_publish(self) -> None:
        ticket_id = "12516-RESET-FIRST"
        job_id = "account-reply-reset-first"
        trigger_created_at = "2026-08-08T00:20:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "reset-first@example.com",
                "requester": "reset-first@example.com",
                "subject": "Reset claimed reply",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable this feature.",
                        "created_at": trigger_created_at,
                    }
                ],
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": f"AC-{ticket_id}",
                "account_case_id": f"AC-{ticket_id}",
                "client_ticket_id": ticket_id,
                "route": "enablement",
                "scope_label": "automation",
                "route_family": "automated",
                "execution_action": "enablement",
                "category": "automation",
                "subcategory": "enablement",
                "route_status": "automated",
                "automation_handler": "enablement",
                "automation_status": "internal_processing",
            }
        )
        stale_job = self.repository.save_account_reply_job(
            {
                "job_id": job_id,
                "ticket_id": ticket_id,
                "trigger_message_created_at": trigger_created_at,
                "status": "persona_publishing",
                "scheduled_for": "2026-08-08T00:21:00+00:00",
                "payload": {
                    "generated_content": "This stale reply must not be sent.",
                    "persona_key": "default-support",
                    "persona_version": 1,
                },
                "claimed_at": "2026-08-08T00:21:00+00:00",
                "created_at": "2026-08-08T00:20:30+00:00",
            }
        )

        reset_result = self.repository.reset_account_rerun_state(
            ticket_id,
            reset_at="2026-08-08T00:22:00+00:00",
            rerun_job_id="account-rerun-reset-first",
            clear_persona_assignment=True,
        )
        with self.assertRaises(KeyError):
            self.repository.publish_account_reply(
                stale_job,
                content="This stale reply must not be sent.",
                payload=dict(stale_job["payload"]),
                published_at="2026-08-08T00:23:00+00:00",
                reply_execution={
                    "execution_id": f"reply-{job_id}",
                    "ticket_id": ticket_id,
                    "reply_kind": "enablement",
                },
            )

        stored_ticket = self.repository.get_ticket(ticket_id)
        stored_case = self.repository.get_billing_ticket(f"AC-{ticket_id}")
        assert stored_ticket is not None
        assert stored_case is not None
        self.assertEqual(reset_result["reply_jobs_deleted"], 1)
        self.assertIsNone(self.repository.get_account_reply_job(job_id))
        self.assertEqual(self.repository.list_account_reply_executions(ticket_id), [])
        self.assertFalse(stored_case.get("customer_reply"))
        self.assertEqual(
            [
                message
                for message in stored_ticket["messages"]
                if str(message.get("source") or "") == "account_ai"
            ],
            [],
        )

    def test_billing_resolution_persona_render_failure_uses_generic_human_review(self) -> None:
        billing_ticket = {
            "billing_ticket_id": "AC-12517",
            "account_case_id": "AC-12517",
            "client_ticket_id": "12517",
            "execution_action": "detailed_invoice",
            "route": "detailed_invoice",
            "route_family": "automated",
            "category": "account_billing",
            "subcategory": "detailed_invoice",
            "route_status": "automated",
            "automation_handler": "billing",
            "tooling_profile": "deterministic_billing_intake",
            "automation_status": "automation",
            "route_classification": {
                "intent_class": "agora",
                "agora_route": "account_billing",
                "account_billing_subcategory": "detailed_invoice",
                "backend_operation_subcategory": None,
                "route_target": "automation",
                "automation_subcategory": None,
                "handler_binding_status": "active",
                "primary_label": "Agora",
                "secondary_label": "Account & Billing / Detailed Invoice",
            },
            "customer_name": "Alice",
        }
        with patch.object(
            self.repository,
            "resolve_account_persona",
            return_value={"persona_key": "sid-warm", "version": 1, "content": {}},
        ), patch.object(
            main,
            "extract_automation_resolution_facts",
            return_value={
                "customer_shareable_facts": ["The detailed invoice is ready."],
                "customer_action": None,
                "next_step": None,
                "status": "completed",
            },
        ), patch.object(
            main,
            "render_automation_reply",
            side_effect=AutomationPersonaError("persona render failed"),
        ):
            reply = main._render_billing_resolution_customer_reply(
                billing_ticket=billing_ticket,
                note="The detailed invoice is ready.",
                customer_message="Please send the invoice.",
                title="Invoice request",
                ticket_id="12517",
            )

        self.assertEqual(reply, "")
        stored = self.repository.get_billing_ticket("AC-12517")
        assert stored is not None
        self.assertEqual(stored["policy_decision"], "account_processing_failure_human_review")
        self.assertEqual(stored["route"], "detailed_invoice")
        self.assertEqual(stored["automation_status"], "human_review_required")
        self.assertEqual(stored["route_family"], "automated")
        self.assertEqual(stored["category"], "account_billing")
        self.assertEqual(stored["subcategory"], "detailed_invoice")
        self.assertEqual(stored["route_status"], "automated")
        self.assertEqual(stored["automation_handler"], "billing")
        self.assertEqual(stored["execution_reason_code"], "account_processing_persona_render_failed")
        self.assertEqual(stored["route_classification"]["handler_binding_status"], "human_review")

    def test_quota_intake_remains_compatibility_only(self) -> None:
        decision = SupportRouteDecision(
            scope_label="quota",
            route="quota",
            route_family="automated",
            execution_action="quota",
            tooling_profile="deterministic_quota_intake",
            semantic_intent="quota.capacity_request",
            reason="registered_quota",
            confidence=0.98,
        )
        classification = {
            "intent_class": "agora",
            "agora_route": "automation",
            "automation_subcategory": "quota",
            "route_target": "automation",
            "route_reason_code": "registered_quota",
            "handler_binding_status": "active",
            "primary_label": "Agora",
            "secondary_label": "Backend Operation / Quota",
            "stage_confidences": {"intent_classifier": 0.99, "agora_router": 0.98, "automation_router": 0.98},
            "stage_reason_codes": {
                "intent_classifier": "agora_case",
                "agora_router": "explicit_backend_operation",
                "automation_router": "registered_quota",
            },
        }
        route_result = SimpleNamespace(
            decision=decision,
            classification=classification,
            prompt_snapshots={},
        )
        with patch.object(main, "decide_account_route", return_value=route_result), patch.object(
            main, "dispatch_event", AsyncMock()
        ):
            created = self.client.post(
                "/account",
                json={
                    "title": "Increase RTC, RTM, and Chat concurrency",
                    "question": "Please review and increase our concurrency limits before launch.",
                    "customer_email": "customer@example.com",
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            created_payload = created.json()
            self.assertEqual(created_payload["subcategory"], "quota")
            self.assertIsNone(created_payload["automation_handler"])
            self.assertEqual(created_payload["route_status"], "not_automated")
            self.assertEqual(created_payload["status"], "not_automated")
            self.assertEqual(created_payload["missing_fields"], [])
            self.assertIsNone(self.repository.get_latest_account_reply_job(created_payload["ticket_id"]))

            continued = self.client.post(
                f"/api/account/cases/{created_payload['account_case_id']}/reply",
                json={"message": "Those are all the details currently available."},
            )

        self.assertEqual(continued.status_code, 200, continued.text)
        continued_payload = continued.json()
        self.assertEqual(continued_payload["internal_email_send_status"], "not_applicable")
        self.assertEqual(continued_payload["route_status"], "not_automated")
        self.assertEqual(continued_payload["automation_status"], "not_automated")

    def test_enablement_does_not_claim_submission_when_internal_email_is_retrying(self) -> None:
        question = "Please enable Media Relay from your end. My App ID is project.prod/eu-west#alpha."
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "retry", "reason": "missing to"},
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay",
                    "question": question,
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["internal_email_send_status"], "retry")
        self.assertEqual(payload["execution_reason_code"], "enablement_internal_email_retry")
        self.assertIsNone(payload["ai_reply_status"])
        self.assertIsNone(self.repository.get_latest_account_reply_job(payload["ticket_id"]))

    def test_uncertain_enablement_fields_fail_closed_to_human_review(self) -> None:
        uncertain = EnablementFieldExtraction(
            status="uncertain",
            collected_fields={"requested_feature": "media_relay", "requested_feature_label": "Media Relay"},
            reason="App ID could not be grounded",
            grounding_status="failed",
            failure_type="grounding_failed",
        )
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.extract_enablement_fields",
            return_value=uncertain,
        ), patch("backend.main.send_enablement_internal_email") as send_email:
            response = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay",
                    "question": "Please enable Media Relay from your end for one of our applications.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["route_status"], "not_automated")
        self.assertEqual(payload["route_family"], "automated")
        self.assertEqual(payload["category"], "backend_operation")
        self.assertEqual(payload["subcategory"], "enablement")
        self.assertEqual(payload["automation_handler"], "enablement")
        self.assertEqual(payload["primary_label"], "Agora")
        self.assertEqual(payload["secondary_label"], "Backend Operation / Enablement")
        self.assertEqual(payload["automation_status"], "human_review_required")
        self.assertEqual(payload["execution_reason_code"], "enablement_field_extraction_uncertain")
        self.assertEqual(payload["missing_fields"], [])
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["route_classification"]["field_extraction"]["status"], "uncertain")
        self.assertEqual(payload["route_classification"]["backend_operation_subcategory"], "enablement")
        stored = self.repository.get_account_case(payload["account_case_id"])
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["execution_reason_code"], "enablement_field_extraction_uncertain")
        self.assertIsNone(self.repository.get_latest_account_reply_job(payload["ticket_id"]))
        send_email.assert_not_called()

    def test_enablement_followup_collects_app_id_and_sends_only_once(self) -> None:
        with patch.dict(os.environ, {"ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": "enablement@example.com"}, clear=False), patch.object(
            main, "dispatch_event", AsyncMock()
        ), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_email:
            created = self.client.post(
                "/account",
                json={
                    "title": "Cross Channel Media Relay Activation",
                    "question": "Please enable Cross Channel Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            created_payload = created.json()
            self.assertEqual(created_payload["missing_fields"], ["app_id"])
            send_email.assert_not_called()

            completed = self.client.post(
                f"/api/account/cases/{created_payload['account_case_id']}/reply",
                json={"message": "App ID: 7da36383d624411698e5c0bc1fda6324"},
            )
            self.assertEqual(completed.status_code, 200, completed.text)
            self.assertEqual(completed.json()["missing_fields"], [])
            self.assertEqual(completed.json()["internal_email_send_status"], "sent")
            self.assertTrue(
                completed.json()["internal_email_payload"]["customer_confirmation_queued"]
            )
            send_email.assert_called_once()

            repeated = self.client.post(
                f"/api/account/cases/{created_payload['account_case_id']}/reply",
                json={"message": "Thank you."},
            )
            self.assertEqual(repeated.status_code, 200, repeated.text)
            send_email.assert_called_once()

    def test_uncertain_enablement_followup_fails_closed_to_human_review(self) -> None:
        uncertain = EnablementFieldExtraction(
            status="uncertain",
            collected_fields={"requested_feature": "media_relay", "requested_feature_label": "Media Relay"},
            reason="App ID could not be grounded",
            grounding_status="failed",
            failure_type="grounding_failed",
        )
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_enablement_internal_email"
        ) as send_email:
            created = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            created_payload = created.json()
            self.assertEqual(created_payload["missing_fields"], ["app_id"])
            initial_reply_job = self.repository.get_latest_account_reply_job(created_payload["ticket_id"])
            self.assertIsNotNone(initial_reply_job)
            assert initial_reply_job is not None

            with patch("backend.main.extract_enablement_fields", return_value=uncertain):
                reviewed = self.client.post(
                    f"/api/account/cases/{created_payload['account_case_id']}/reply",
                    json={"message": "It belongs to our production application."},
                )

        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        payload = reviewed.json()
        self.assertEqual(payload["route_status"], "automated")
        self.assertEqual(payload["route_family"], "automated")
        self.assertEqual(payload["category"], "backend_operation")
        self.assertEqual(payload["subcategory"], "enablement")
        self.assertEqual(payload["automation_handler"], "enablement")
        self.assertEqual(payload["primary_label"], "Agora")
        self.assertEqual(payload["secondary_label"], "Backend Operation / Enablement")
        self.assertEqual(payload["automation_status"], "human_review_required")
        self.assertEqual(payload["execution_reason_code"], "enablement_field_extraction_uncertain")
        self.assertEqual(payload["missing_fields"], [])
        self.assertEqual(payload["route_classification"]["field_extraction"]["status"], "uncertain")
        self.assertEqual(payload["route_classification"]["backend_operation_subcategory"], "enablement")
        latest_reply_job = self.repository.get_latest_account_reply_job(payload["ticket_id"])
        self.assertIsNotNone(latest_reply_job)
        assert latest_reply_job is not None
        self.assertEqual(latest_reply_job["job_id"], initial_reply_job["job_id"])
        self.assertEqual(latest_reply_job["status"], "cancelled")
        send_email.assert_not_called()

    def test_account_intake_preserves_non_automated_ticket_without_email(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "skipped_config_missing", "reason": "missing BILLING_AUTOMATION_SMTP_PASSWORD"},
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "General support question",
                    "question": "Can someone tell me more about Agora products?",
                    "customer_email": "customer@example.com",
                    "source": "manual",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["route"], "human_review_required")
        self.assertTrue(str(payload["ticket_id"] or "").startswith("TK-ACC-"))
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["missing_fields"], [])
        self.assertEqual(payload["internal_email_send_status"], "not_applicable")

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["subject"], "General support question")
        self.assertEqual(ticket["source"], "manual")
        self.assertEqual([message["role"] for message in ticket["messages"]], ["customer"])

    def test_account_intake_requires_question(self) -> None:
        response = self.client.post("/account", json={"title": "", "question": ""})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "question is required")
        self.assertEqual(self.repository.list_tickets(), [])

    def test_account_intake_uses_trimmed_external_id_as_canonical_ticket_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "external_id": " 11830 ",
                    "title": "Zendesk support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "https://agoraio.zendesk.com/agent/tickets/11830",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "11830")
        self.assertEqual(payload["account_case_id"], "AC-11830")
        self.assertEqual(payload["billing_ticket_id"], "AC-11830")
        self.assertIsNotNone(self.repository.get_ticket("11830"))
        billing_ticket = self.repository.get_account_case("AC-11830")
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["client_ticket_id"], "11830")
        self.assertEqual(billing_ticket["external_id"], "11830")

    def test_account_intake_external_id_takes_precedence_over_ticket_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "external_id": "zendesk-42",
                    "ticket_id": "TK-UPSTREAM-42",
                    "title": "Zendesk support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "api",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ticket_id"], "zendesk-42")
        self.assertIsNotNone(self.repository.get_ticket("zendesk-42"))
        self.assertIsNone(self.repository.get_ticket("TK-UPSTREAM-42"))

    def test_account_intake_uses_zendesk_ticket_id_from_source_url(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Zendesk support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": {"Link": "https://agoraio.zendesk.com/api/v2/tickets/11831.json"},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "11831")
        self.assertEqual(payload["account_case_id"], "AC-11831")
        self.assertIsNotNone(self.repository.get_ticket("11831"))
        account_case = self.repository.get_account_case("AC-11831")
        self.assertIsNotNone(account_case)
        assert account_case is not None
        self.assertEqual(account_case["external_id"], "11831")
        self.assertEqual(
            account_case["source"],
            '{"Link": "https://agoraio.zendesk.com/agent/tickets/11831"}',
        )

    def test_account_intake_ticket_id_precedes_zendesk_source_fallback(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "ticket_id": "TK-UPSTREAM-11832",
                    "title": "Compatible upstream request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "https://agoraio.zendesk.com/agent/tickets/11832",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ticket_id"], "TK-UPSTREAM-11832")
        self.assertIsNotNone(self.repository.get_ticket("TK-UPSTREAM-11832"))

    def test_account_intake_falls_back_to_ticket_id_then_generated_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            provided_response = self.client.post(
                "/account",
                json={
                    "ticket_id": " TK-COMPAT-001 ",
                    "title": "Compatible support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "manual",
                },
            )
            generated_response = self.client.post(
                "/account",
                json={
                    "title": "Manual support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "manual",
                },
            )

        self.assertEqual(provided_response.status_code, 200, provided_response.text)
        self.assertEqual(provided_response.json()["ticket_id"], "TK-COMPAT-001")
        self.assertEqual(generated_response.status_code, 200, generated_response.text)
        self.assertTrue(generated_response.json()["ticket_id"].startswith("TK-ACC-"))

    def test_account_intake_external_id_collision_does_not_overwrite_existing_ticket(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "zendesk-existing-1",
                "customer_id": "legacy-customer",
                "requester": "legacy@example.com",
                "subject": "Existing ticket",
                "status": "open",
            }
        )

        response = self.client.post(
            "/account",
            json={
                "external_id": "zendesk-existing-1",
                "title": "Replacement ticket",
                "question": "Can someone tell me more about Agora products?",
                "source": "api",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "ticket_id already exists")
        existing = self.repository.get_ticket("zendesk-existing-1")
        self.assertIsNotNone(existing)
        assert existing is not None
        self.assertEqual(existing["subject"], "Existing ticket")
        self.assertEqual(len(self.repository.list_tickets()), 1)

    def test_account_intake_empty_title_derives_from_question(self) -> None:
        """N8n sends title: \"\" — backend should derive title from question body."""
        source_url = "https://agoraio.zendesk.com/api/v2/tickets/11830.json"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "",
                    "question": "Can someone tell me more about Agora products?",
                    "customer_email": "n8n@example.com",
                    "source": source_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "11830")

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        # Title should have been derived from question, not left empty.
        self.assertTrue(ticket["subject"])
        self.assertNotEqual(ticket["subject"], "")
        # The derived title should be a reasonable short phrase, not the full question.
        self.assertLess(len(ticket["subject"]), len("Can someone tell me more about Agora products?"))

        billing_ticket = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["title"], ticket["subject"])
        self.assertIn("https://agoraio.zendesk.com/agent/tickets/11830", billing_ticket["source"])

    def test_account_get_serves_ui_and_post_serves_json_api(self) -> None:
        page_response = self.client.get("/account/")
        self.assertEqual(page_response.status_code, 200, page_response.text)
        self.assertIn("Account Intake", page_response.text)

        with patch.object(main, "dispatch_event", AsyncMock()):
            api_response = self.client.post(
                "/account",
                json={
                    "title": "General support question",
                    "question": "Can someone tell me more about Agora products?",
                },
            )

        self.assertEqual(api_response.status_code, 200, api_response.text)
        self.assertEqual(api_response.headers["content-type"].split(";")[0], "application/json")
        self.assertEqual(api_response.json()["status"], "not_automated")

    def test_account_intake_returns_billing_ticket_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send the detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(str(payload["ticket_id"] or "").startswith("TK-ACC-"))
        self.assertTrue(str(payload["account_case_id"] or "").startswith("AC-TK-ACC-"))
        self.assertEqual(payload["billing_ticket_id"], payload["account_case_id"])
        self.assertNotIn("support_ticket_id", payload)

    def test_billing_internal_email_uses_outlook_reply_without_response_link(self) -> None:
        payload, _ = self._create_invoice_ticket_with_response_token()
        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        stored_payload = bt["internal_email_payload"]
        self.assertIsInstance(stored_payload, dict)
        stored_body = stored_payload["body"]
        self.assertIn("reply directly to this email in Outlook", stored_body)
        self.assertNotIn("/response?token=", stored_body)

    def test_billing_outlook_reply_email_failure_records_failure_without_response_link(self) -> None:
        payload, _ = self._create_invoice_ticket_with_response_token()
        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["internal_email_send_status"], "sent")
        self.assertEqual(bt["route_status"], "automated")
        self.assertEqual(bt["automation_status"], "automation")
        self.assertNotIn("/response?token=", bt["internal_email_payload"]["body"])

    def test_billing_response_lookup_returns_context_for_valid_token(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.get(f"/api/billing-response?token={raw_token}")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["billing_ticket_id"], create_payload["billing_ticket_id"])
        self.assertEqual(payload["customer_email"], "customer@example.com")
        self.assertFalse(payload["submitted"])
        self.assertEqual(payload["title"], "Detailed invoice request")
        self.assertIn("detailed invoice", payload["question"].lower())
        self.assertIsInstance(payload["collected_fields"], dict)
        self.assertNotIn("ticket_id", payload)
        self.assertNotIn("client_ticket_id", payload)

    def test_billing_response_lookup_reports_submitted_for_used_token(self) -> None:
        _, raw_token = self._create_invoice_ticket_with_response_token()
        token_hash = hash_billing_response_token(raw_token)
        self.assertTrue(self.repository.mark_billing_response_token_used(token_hash, "2026-06-19T00:00:00+00:00"))

        response = self.client.get(f"/api/billing-response?token={raw_token}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["submitted"])

    def test_billing_response_submit_records_event_and_customer_reply(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={"token": raw_token, "result": "completed", "notify_customer": True, "note": ""},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["submitted"])
        self.assertTrue(payload["customer_notified"])
        self.assertEqual(payload["billing_ticket_id"], create_payload["billing_ticket_id"])
        self.assertEqual(payload["automation_status"], "customer_notified")

        ticket_id = str(create_payload["ticket_id"])
        event_types = [
            item["event_type"]
            for item in reversed(self.repository.list_ticket_events(ticket_id))
            if item["event_type"]
            in {"billing_internal_resolution_submitted", "billing_customer_followup_generated"}
        ]
        self.assertEqual(
            event_types,
            ["billing_internal_resolution_submitted", "billing_customer_followup_generated"],
        )
        followup_events = [
            item["payload"]
            for item in self.repository.list_ticket_events(ticket_id)
            if item["event_type"] == "billing_customer_followup_generated"
        ]
        self.assertTrue(followup_events)
        self.assertEqual(followup_events[-1]["resolution_result"], "completed")
        self.assertEqual(followup_events[-1]["source"], "billing_response_ai")
        ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")

        billing_ticket = self.repository.get_billing_ticket(str(create_payload["billing_ticket_id"]))
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["automation_status"], "customer_notified")
        self.assertEqual(billing_ticket["route_status"], "automated")

    def test_billing_response_submit_generates_customer_reply_from_internal_details(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        captured_source: list[str] = []

        def fake_extract(**kwargs: object) -> dict[str, object]:
            captured_source.append(str(kwargs.get("source_text") or ""))
            return {
                "status": "completed",
                "customer_shareable_facts": ["The detailed invoice was sent."],
                "customer_action": None,
                "next_step": "Please let us know if you need anything else.",
            }

        rendered = AutomationPersonaResult(
            content="We sent the detailed invoice to the email address on file. Please let us know if you need anything else.",
            model="test-persona",
        )
        with patch(
            "backend.main.extract_automation_resolution_facts",
            side_effect=fake_extract,
        ) as extract_mock, patch(
            "backend.main.render_automation_reply",
            return_value=rendered,
        ) as render_mock:
            response = self.client.post(
                "/api/billing-response/submit",
                json={
                    "token": raw_token,
                    "result": "completed",
                    "notify_customer": True,
                    "note": "已经通过邮件发送给客户",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertEqual(
            payload["customer_reply"],
            "We sent the detailed invoice to the email address on file. Please let us know if you need anything else.",
        )
        self.assertNotEqual(payload["customer_reply"], "已经通过邮件发送给客户")
        extract_mock.assert_called_once()
        render_mock.assert_called_once()
        self.assertEqual(captured_source, ["已经通过邮件发送给客户"])

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["source"], "billing_response_ai")
        self.assertEqual(last_message["content"], payload["customer_reply"])

    def test_billing_response_submit_does_not_notify_customer_with_internal_status_note(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "completed",
                "notify_customer": True,
                "note": "已通过邮件发送给客户",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertIn("detailed invoice", payload["customer_reply"].lower())
        self.assertIn("sent", payload["customer_reply"].lower())
        self.assertNotEqual(payload["customer_reply"], "已通过邮件发送给客户")

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")
        self.assertEqual(last_message["content"], payload["customer_reply"])

    def test_billing_response_submit_generates_invoice_reply_from_short_sent_note(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "completed",
                "notify_customer": True,
                "note": "已发送",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertIn("detailed invoice", payload["customer_reply"].lower())
        self.assertIn("sent", payload["customer_reply"].lower())
        self.assertNotEqual(payload["customer_reply"], "Your billing request has been processed.")
        self.assertNotEqual(payload["customer_reply"], "已发送")

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")
        self.assertEqual(last_message["content"], payload["customer_reply"])

    def test_billing_response_submit_rejects_fragmentary_customer_note(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "completed",
                "notify_customer": True,
                "note": "以及通过邮件发送",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertIn("detailed invoice", payload["customer_reply"].lower())
        self.assertIn("sent", payload["customer_reply"].lower())
        self.assertNotEqual(payload["customer_reply"], "以及通过邮件发送")

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")
        self.assertEqual(last_message["content"], payload["customer_reply"])

    def test_billing_response_submit_uses_original_question_for_resolution_context(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "completed",
                "notify_customer": True,
                "note": "已发送",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("detailed invoice", payload["customer_reply"].lower())
        self.assertIn("sent", payload["customer_reply"].lower())
        self.assertNotEqual(payload["customer_reply"], "Your billing request has been processed.")

    def test_billing_response_submit_no_notify_records_event_without_customer_reply(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        ticket_id = str(create_payload["ticket_id"])
        before_ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(before_ticket)
        assert before_ticket is not None
        before_billing_response_messages = [
            message
            for message in before_ticket["messages"]
            if message.get("role") == "assistant" and message.get("source") == "billing_response_ai"
        ]

        response = self.client.post(
            "/api/billing-response/submit",
            json={"token": raw_token, "result": "completed", "notify_customer": False, "note": ""},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["submitted"])
        self.assertFalse(payload["customer_notified"])
        self.assertEqual(payload["automation_status"], "resolved_without_customer_notification")
        event_types = [item["event_type"] for item in self.repository.list_ticket_events(ticket_id)]
        self.assertIn("billing_internal_resolution_submitted", event_types)
        self.assertNotIn("billing_customer_followup_generated", event_types)

        ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        after_billing_response_messages = [
            message
            for message in ticket["messages"]
            if message.get("role") == "assistant" and message.get("source") == "billing_response_ai"
        ]
        self.assertEqual(after_billing_response_messages, before_billing_response_messages)
        billing_ticket = self.repository.get_billing_ticket(str(create_payload["billing_ticket_id"]))
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(
            billing_ticket["automation_status"],
            "resolved_without_customer_notification",
        )

    def test_billing_response_submit_no_notify_rejects_token_reset_before_commit(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        ticket_id = str(create_payload["ticket_id"])
        original_builder = main.build_billing_internal_resolution_event

        def reset_then_build(**kwargs):
            self.repository.reset_account_rerun_state(
                ticket_id,
                reset_at="2026-07-18T00:01:00+00:00",
                rerun_job_id="account-rerun-token-reset-no-notify",
                reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
                clear_persona_assignment=True,
            )
            return original_builder(**kwargs)

        with patch.object(
            main,
            "build_billing_internal_resolution_event",
            side_effect=reset_then_build,
        ):
            response = self.client.post(
                "/api/billing-response/submit",
                json={
                    "token": raw_token,
                    "result": "completed",
                    "notify_customer": False,
                    "note": "",
                },
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            self.client.get(f"/api/billing-response?token={raw_token}").status_code,
            404,
        )
        self.assertNotIn(
            "billing_internal_resolution_submitted",
            [item["event_type"] for item in self.repository.list_ticket_events(ticket_id)],
        )

    def test_billing_response_submit_notify_rejects_token_reset_during_render(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        ticket_id = str(create_payload["ticket_id"])

        def reset_then_render(**_kwargs):
            self.repository.reset_account_rerun_state(
                ticket_id,
                reset_at="2026-07-18T00:01:00+00:00",
                rerun_job_id="account-rerun-token-reset-notify",
                reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
                clear_persona_assignment=True,
            )
            return "Hi Customer,\n\nThe invoice is ready.\n\nBest Regards,\nSid"

        with patch.object(
            main,
            "_render_billing_resolution_customer_reply",
            side_effect=reset_then_render,
        ):
            response = self.client.post(
                "/api/billing-response/submit",
                json={
                    "token": raw_token,
                    "result": "completed",
                    "notify_customer": True,
                    "note": "",
                },
            )

        self.assertEqual(response.status_code, 409, response.text)
        stored_ticket = self.repository.get_ticket(ticket_id)
        assert stored_ticket is not None
        self.assertFalse(
            any(
                message.get("source") == "billing_response_ai"
                for message in stored_ticket["messages"]
            )
        )
        self.assertNotIn(
            "billing_customer_followup_generated",
            [item["event_type"] for item in self.repository.list_ticket_events(ticket_id)],
        )

    def test_billing_response_submit_rejects_second_submit(self) -> None:
        _, raw_token = self._create_invoice_ticket_with_response_token()
        first_response = self.client.post(
            "/api/billing-response/submit",
            json={"token": raw_token, "result": "completed", "notify_customer": False, "note": ""},
        )
        self.assertEqual(first_response.status_code, 200, first_response.text)

        second_response = self.client.post(
            "/api/billing-response/submit",
            json={"token": raw_token, "result": "completed", "notify_customer": False, "note": ""},
        )

        self.assertEqual(second_response.status_code, 409, second_response.text)

    def test_billing_response_submit_requires_note_for_refused_and_customer_action(self) -> None:
        _, refused_token = self._create_invoice_ticket_with_response_token()

        refused_response = self.client.post(
            "/api/billing-response/submit",
            json={"token": refused_token, "result": "refused", "notify_customer": False, "note": ""},
        )

        self.assertEqual(refused_response.status_code, 400, refused_response.text)
        lookup_response = self.client.get(f"/api/billing-response?token={refused_token}")
        self.assertEqual(lookup_response.status_code, 200, lookup_response.text)
        self.assertFalse(lookup_response.json()["submitted"])

        create_payload, customer_action_token = self._create_invoice_ticket_with_response_token()
        customer_action_response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": customer_action_token,
                "result": "customer_action_required",
                "notify_customer": True,
                "note": "",
            },
        )
        self.assertEqual(customer_action_response.status_code, 400, customer_action_response.text)

        valid_response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": customer_action_token,
                "result": "customer_action_required",
                "notify_customer": False,
                "note": "Please ask the customer for their billing address.",
            },
        )
        self.assertEqual(valid_response.status_code, 200, valid_response.text)
        self.assertEqual(valid_response.json()["billing_ticket_id"], create_payload["billing_ticket_id"])

    def test_billing_response_submit_customer_action_status(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "customer_action_required",
                "notify_customer": True,
                "note": "Please confirm the billing address for this invoice.",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertEqual(payload["automation_status"], "waiting_customer_action")

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")

        billing_ticket = self.repository.get_billing_ticket(str(create_payload["billing_ticket_id"]))
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["automation_status"], "waiting_customer_action")

    def test_billing_response_invalid_token_returns_404(self) -> None:
        lookup_response = self.client.get("/api/billing-response?token=not-a-real-token")
        self.assertEqual(lookup_response.status_code, 404, lookup_response.text)

        submit_response = self.client.post(
            "/api/billing-response/submit",
            json={"token": "not-a-real-token", "result": "completed", "notify_customer": False, "note": ""},
        )
        self.assertEqual(submit_response.status_code, 404, submit_response.text)

    def test_billing_response_missing_or_blank_token_returns_404(self) -> None:
        lookup_missing = self.client.get("/api/billing-response")
        self.assertEqual(lookup_missing.status_code, 404, lookup_missing.text)

        lookup_blank = self.client.get("/api/billing-response?token=")
        self.assertEqual(lookup_blank.status_code, 404, lookup_blank.text)

        submit_missing = self.client.post(
            "/api/billing-response/submit",
            json={"result": "completed", "notify_customer": False, "note": ""},
        )
        self.assertEqual(submit_missing.status_code, 404, submit_missing.text)

        submit_blank = self.client.post(
            "/api/billing-response/submit",
            json={"token": "", "result": "completed", "notify_customer": False, "note": ""},
        )
        self.assertEqual(submit_blank.status_code, 404, submit_blank.text)

    def test_billing_response_submit_persists_status_before_internal_event_dispatch(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        billing_ticket_id = str(create_payload["billing_ticket_id"])
        status_seen_during_dispatch: list[str | None] = []

        async def capture_dispatch(channels: list[str], payload: dict[str, object]) -> None:
            if payload.get("event") == "billing_internal_resolution_submitted":
                billing_ticket = self.repository.get_billing_ticket(billing_ticket_id)
                status_seen_during_dispatch.append(
                    str(billing_ticket.get("automation_status") or "") if billing_ticket else None
                )

        with patch.object(main, "dispatch_event", side_effect=capture_dispatch):
            response = self.client.post(
                "/api/billing-response/submit",
                json={"token": raw_token, "result": "completed", "notify_customer": False, "note": ""},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(status_seen_during_dispatch, ["resolved_without_customer_notification"])

    def test_billing_response_submit_persona_failure_commits_human_review_atomically(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        before_ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(before_ticket)
        assert before_ticket is not None
        before_response_messages = [
            message
            for message in before_ticket["messages"]
            if message.get("role") == "assistant" and message.get("source") == "billing_response_ai"
        ]

        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.render_automation_reply",
            side_effect=AutomationPersonaError("automation_persona_failed"),
        ):
            response = self.client.post(
                "/api/billing-response/submit",
                json={"token": raw_token, "result": "completed", "notify_customer": True, "note": ""},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["customer_notified"])
        self.assertTrue(response.json()["human_review_required"])

        billing_ticket = self.repository.get_billing_ticket(str(create_payload["billing_ticket_id"]))
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["automation_status"], "human_review_required")
        self.assertEqual(billing_ticket["route"], "detailed_invoice")
        self.assertEqual(billing_ticket["route_family"], "automated")
        self.assertEqual(billing_ticket["category"], "account_billing")
        self.assertEqual(billing_ticket["subcategory"], "detailed_invoice")
        self.assertEqual(billing_ticket["route_status"], "automated")
        self.assertEqual(billing_ticket["automation_handler"], "billing")
        self.assertEqual(
            billing_ticket["policy_decision"],
            "account_processing_failure_human_review",
        )
        self.assertEqual(billing_ticket["execution_reason_code"], "account_processing_automation_persona_failed")
        classification = billing_ticket["route_classification"]
        self.assertEqual(classification["account_billing_subcategory"], "detailed_invoice")
        self.assertEqual(classification["handler_binding_status"], "human_review")

        after_ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(after_ticket)
        assert after_ticket is not None
        after_response_messages = [
            message
            for message in after_ticket["messages"]
            if message.get("role") == "assistant" and message.get("source") == "billing_response_ai"
        ]
        self.assertEqual(after_response_messages, before_response_messages)
        lookup_response = self.client.get(f"/api/billing-response?token={raw_token}")
        self.assertEqual(lookup_response.status_code, 200, lookup_response.text)
        self.assertTrue(lookup_response.json()["submitted"])
        self.assertEqual(
            [
                item["event_type"]
                for item in self.repository.list_ticket_events(str(create_payload["ticket_id"]))
                if item["event_type"]
                in {
                    "billing_internal_resolution_submitted",
                    "billing_customer_followup_generated",
                }
            ],
            ["billing_internal_resolution_submitted"],
        )

    def test_billing_response_submit_unexpected_render_failure_leaves_token_and_state_unmodified(
        self,
    ) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        billing_ticket_id = str(create_payload["billing_ticket_id"])
        ticket_id = str(create_payload["ticket_id"])
        before_billing_ticket = self.repository.get_billing_ticket(billing_ticket_id)
        before_ticket = self.repository.get_ticket(ticket_id)
        before_events = self.repository.list_ticket_events(ticket_id)
        self.assertIsNotNone(before_billing_ticket)
        self.assertIsNotNone(before_ticket)

        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main,
            "_render_billing_resolution_customer_reply",
            side_effect=RuntimeError("followup failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "followup failed"):
                self.client.post(
                    "/api/billing-response/submit",
                    json={
                        "token": raw_token,
                        "result": "completed",
                        "notify_customer": True,
                        "note": "",
                    },
                )

        self.assertEqual(
            self.repository.get_billing_ticket(billing_ticket_id),
            before_billing_ticket,
        )
        self.assertEqual(self.repository.get_ticket(ticket_id), before_ticket)
        self.assertEqual(self.repository.list_ticket_events(ticket_id), before_events)
        lookup_response = self.client.get(f"/api/billing-response?token={raw_token}")
        self.assertEqual(lookup_response.status_code, 200, lookup_response.text)
        self.assertFalse(lookup_response.json()["submitted"])

    def test_detailed_invoice_classification_does_not_create_response_token(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            self.repository, "save_billing_response_token", wraps=self.repository.save_billing_response_token
        ) as save_token_mock, patch("backend.main.send_billing_internal_email") as send_email:
            response = self.client.post(
                "/account",
                json={"title": "Detailed invoice request", "question": "Please send detailed invoice.", "customer_email": "customer@example.com"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["route"], "human_review_required")
        self.assertEqual(payload["category"], "account_billing")
        self.assertEqual(payload["subcategory"], "detailed_invoice")
        self.assertEqual(payload["route_status"], "not_automated")
        self.assertEqual(payload["internal_email_send_status"], "not_applicable")
        self.assertEqual(payload["missing_fields"], [])
        self.assertIsNone(payload["ai_reply_status"])
        save_token_mock.assert_not_called()
        send_email.assert_not_called()
        self.assertIsNone(
            self.repository.get_billing_response_token(hash_billing_response_token("unused-token"))
        )

    def test_account_intake_saves_classification_only_detailed_invoice(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "skipped_config_missing", "reason": "missing BILLING_AUTOMATION_SMTP_PASSWORD"},
        ) as send_email:
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send the detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                    "external_id": "ext-123",
                    "created_by": "tester",
                },
            )

        payload = response.json()
        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["client_ticket_id"], payload["ticket_id"])
        self.assertEqual(bt["automation_status"], "not_automated")
        self.assertEqual(bt["route"], "human_review_required")
        self.assertEqual(bt["route_family"], "human_review")
        self.assertEqual(bt["category"], "account_billing")
        self.assertEqual(bt["subcategory"], "detailed_invoice")
        self.assertIsNone(bt["automation_handler"])
        self.assertEqual(bt["source"], "manual")
        self.assertEqual(bt["external_id"], "ext-123")
        self.assertEqual(bt["created_by"], "tester")
        self.assertEqual(bt["title"], "Detailed invoice request")
        self.assertIsNotNone(bt["route_reason"])
        self.assertIsNotNone(bt["route_confidence"])
        self.assertIsNone(bt["customer_reply"])
        self.assertEqual(bt["internal_email_send_status"], "not_applicable")
        self.assertIsNone(bt["execution_reason_code"])
        send_email.assert_not_called()

    def test_account_intake_saves_non_automated_billing_ticket(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "General question",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "manual",
                },
            )

        payload = response.json()
        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["automation_status"], "not_automated")
        self.assertEqual(bt["route"], "human_review_required")
        self.assertEqual(bt["source"], "manual")
        self.assertEqual(bt["customer_reply"], None)

    def test_staging_not_automated_account_tickets_do_not_create_engineer_cases(self) -> None:
        responses = []
        with patch.dict(
            os.environ,
            {"ACCOUNT_NOT_AUTOMATED_ENGINEER_ROLLOUT_PERCENT": "10"},
        ), patch.object(main, "dispatch_event", AsyncMock()):
            for index in range(1, 11):
                response = self.client.post(
                    "/account",
                    json={
                        "ticket_id": f"TK-ROLLOUT-{index:03d}",
                        "external_id": f"zendesk-{index}",
                        "title": "General support question",
                        "question": "Can an engineer help me understand this product behavior?",
                        "source": "api",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                responses.append(response.json())

        self.assertTrue(all(item["status"] == "not_automated" for item in responses))
        self.assertTrue(all(item["engineer_case_id"] is None for item in responses))
        self.assertTrue(all(item["rollout_position"] is None for item in responses))
        self.assertTrue(all(item["rollout_selected"] is False for item in responses))

    def test_production_not_automated_intake_creates_one_case_dispatch_and_root_event(self) -> None:
        assignment = Mock()
        assignment.dispatch_case.return_value = {"engineer_case_id": "12874-1"}
        request = {
            "ticket_id": "TK-ENGINEER-SLACK-001",
            "external_id": "12874",
            "title": "General support question",
            "question": "Can an engineer help me understand this product behavior?",
            "source": "api",
        }
        with patch.dict(
            os.environ,
            {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"},
            clear=False,
        ), patch.object(main, "_engineer_assignment_service", return_value=assignment), patch.object(
            main, "dispatch_event", AsyncMock()
        ):
            first = self.client.post("/account", json=request)
            second = self.client.post("/account", json=request)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(first.json()["engineer_case_id"], second.json()["engineer_case_id"])
        engineer_case_id = first.json()["engineer_case_id"]
        self.assertEqual(engineer_case_id, "12874-1")
        self.assertTrue(first.json()["rollout_selected"])
        cases = self.repository.list_ticket_engineer_cases("12874")
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["active_investigation"]["id"], "12874-1-round-1")
        assignment.dispatch_case.assert_called_once_with("12874-1", reason="round_robin")
        events = self.repository.list_engineer_slack_events(statuses=("queued",))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["event_type"], "engineer_case_opened")

    def test_account_external_id_replay_does_not_recount_or_duplicate_case(self) -> None:
        request = {
            "ticket_id": "TK-IDEMPOTENT-001",
            "external_id": "zendesk-idempotent-1",
            "title": "General support question",
            "question": "Can an engineer help me understand this product behavior?",
            "source": "api",
        }
        with patch.dict(
            os.environ,
            {"ACCOUNT_NOT_AUTOMATED_ENGINEER_ROLLOUT_PERCENT": "100"},
        ), patch.object(main, "dispatch_event", AsyncMock()):
            first = self.client.post("/account", json=request)
            second = self.client.post("/account", json=request)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(first.json().get("idempotent_replay", False))
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(first.json()["ticket_id"], second.json()["ticket_id"])
        self.assertEqual(first.json()["ticket_id"], "zendesk-idempotent-1")
        self.assertIsNone(first.json()["rollout_position"])
        self.assertIsNone(second.json()["rollout_position"])
        self.assertEqual(len(self.repository.list_ticket_engineer_cases("zendesk-idempotent-1")), 0)

    def test_account_intake_billing_review_stays_not_automated(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="human_review_required",
            confidence=0.91,
            reason="billing_account_suspension",
            matched_signals=["account suspended"],
            response_language="en",
            semantic_intent="billing.account_suspension",
            automation_eligibility="not_eligible",
            policy_decision="policy_gate",
            not_automated_reason="human_review_required",
            risk_flags=["account_access_restore"],
            evidence_spans=["account has been suspended"],
            router_source="llm_semantic",
        )

        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main,
            "decide_account_route",
            return_value=_account_suspension_route_result(),
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "Our account has been suspended due to balance.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["route"], "human_review_required")
        self.assertEqual(payload["route_family"], "human_review")
        self.assertEqual(payload["primary_label"], "Agora")
        self.assertEqual(payload["secondary_label"], "Account & Billing / Account Suspension")
        self.assertEqual(payload["automation_eligibility"], "not_eligible")
        self.assertIsNone(payload["policy_decision"])
        self.assertIsNone(payload["not_automated_reason"])

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["automation_status"], "not_automated")
        self.assertEqual(bt["route"], "human_review_required")
        self.assertEqual(bt["semantic_intent"], "account_billing.account_suspension")
        self.assertIsNone(bt["policy_decision"])

        events = self.repository.list_ticket_events(payload["ticket_id"])
        self.assertEqual(events[0]["payload"]["account_intake_status"], "not_automated")
        self.assertEqual(events[0]["payload"]["execution_action"], "human_review_required")

    def test_account_intake_persists_route_result_fields_for_automation(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "skipped_config_missing", "reason": "missing BILLING_AUTOMATION_SMTP_PASSWORD"},
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": (
                        "My App ID is project.prod/eu-west#alpha. "
                        "Please enable Media Relay from your end."
                    ),
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "human_review_required")
        self.assertEqual(payload["route"], "enablement")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["scope_label"], "enablement")
        self.assertEqual(bt["route_family"], "automated")
        self.assertEqual(bt["account_case_id"], payload["account_case_id"])
        self.assertEqual(bt["category"], "backend_operation")
        self.assertEqual(bt["subcategory"], "enablement")
        self.assertEqual(bt["route_status"], "not_automated")
        self.assertEqual(bt["automation_handler"], "enablement")
        self.assertEqual(bt["execution_action"], "enablement")
        self.assertEqual(bt["route"], "enablement")

        # Detail API surfaces the route result fields.
        legacy_detail = self.client.get(
            f"/api/account/billing-tickets/{payload['billing_ticket_id']}"
        )
        self.assertEqual(legacy_detail.status_code, 200, legacy_detail.text)
        detail = legacy_detail.json()
        self.assertEqual(detail["scope_label"], "enablement")
        self.assertEqual(detail["route_family"], "automated")
        self.assertEqual(detail["execution_action"], "enablement")
        self.assertEqual(detail["route"], "enablement")

        canonical_detail = self.client.get(f"/api/account/cases/{payload['account_case_id']}")
        self.assertEqual(canonical_detail.status_code, 200, canonical_detail.text)
        self.assertEqual(canonical_detail.json()["account_case_id"], payload["account_case_id"])
        self.assertEqual(canonical_detail.json(), detail)
        canonical_list = self.client.get("/api/account/cases?route_status=automated")
        self.assertEqual(canonical_list.status_code, 200, canonical_list.text)
        self.assertIn("cases", canonical_list.json())

    def test_account_intake_persists_route_result_fields_for_non_automated(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "General support question",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "manual",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["route"], "human_review_required")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["route"], "human_review_required")
        # scope_label/route_family are persisted even for non-automated routes.
        self.assertTrue(bt["scope_label"])
        self.assertTrue(bt["route_family"])
        self.assertEqual(bt["execution_action"], "human_review_required")

    def test_billing_ticket_detail_returns_route_for_legacy_ticket_without_route_result_fields(self) -> None:
        # Historical ticket persisted before scope_label/route_family/execution_action existed.
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LEGACY-ROUTE-001",
                "client_ticket_id": "TK-LEGACY-ROUTE-001",
                "source": "manual",
                "title": "Legacy route ticket",
                "question": "legacy question",
                "automation_status": "automation",
                "route": "detailed_invoice",
            }
        )

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-LEGACY-ROUTE-001")
        self.assertEqual(detail_response.status_code, 200, detail_response.text)
        detail = detail_response.json()
        # Legacy ticket still returns the original route; missing route result fields do not error.
        self.assertEqual(detail["route"], "detailed_invoice")
        self.assertIsNone(detail.get("scope_label"))
        self.assertIsNone(detail.get("route_family"))
        self.assertIsNone(detail.get("execution_action"))

    def test_billing_tickets_list_api(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            for i in range(3):
                self.client.post(
                    "/account",
                    json={
                        "title": f"Ticket {i}",
                        "question": f"Question {i}",
                    },
                )

        response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["billing_tickets"]), 3)
        self.assertIn("tickets", data)
        self.assertEqual(len(data["tickets"]), 3)
        for item in data["tickets"]:
            self.assertTrue(str(item["ticket_id"] or "").startswith("TK-ACC-"))
            self.assertNotIn("support_ticket_id", item)
            self.assertIn("status", item)
        for item in data["billing_tickets"]:
            self.assertIn("billing_ticket_id", item)
            self.assertIn("client_ticket_id", item)
            self.assertIn("title", item)
            self.assertIn("route", item)
            self.assertIn("automation_status", item)
            self.assertIn("created_at", item)
            self.assertIn("route_review_status", item)
            self.assertEqual(item["route_review_status"], "pending")
            self.assertNotIn("question", item)
            self.assertNotIn("internal_email_payload", item)
            self.assertNotIn("evidence_spans", item)

    def test_billing_tickets_list_api_paginates_by_filter(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            for i in range(35):
                self.client.post(
                    "/account",
                    json={
                        "title": f"Paged ticket {i:02d}",
                        "question": f"Question {i}",
                    },
                )

        first_page = self.client.get(
            "/api/account/billing-tickets?page=1&page_size=30&review_status=pending"
        )
        self.assertEqual(first_page.status_code, 200, first_page.text)
        first_payload = first_page.json()
        self.assertEqual(first_payload["count"], 30)
        self.assertEqual(first_payload["page"], 1)
        self.assertEqual(first_payload["page_size"], 30)
        self.assertEqual(first_payload["total"], 35)
        self.assertEqual(first_payload["total_pages"], 2)
        self.assertTrue(first_payload["has_more"])
        self.assertEqual(len(first_payload["tickets"]), 30)

        second_page = self.client.get(
            "/api/account/billing-tickets?page=2&page_size=30&review_status=pending"
        )
        self.assertEqual(second_page.status_code, 200, second_page.text)
        second_payload = second_page.json()
        self.assertEqual(second_payload["count"], 5)
        self.assertEqual(second_payload["page"], 2)
        self.assertEqual(second_payload["page_size"], 30)
        self.assertEqual(second_payload["total"], 35)
        self.assertEqual(second_payload["total_pages"], 2)
        self.assertFalse(second_payload["has_more"])
        self.assertEqual(len(second_payload["tickets"]), 5)

        first_ids = {item["billing_ticket_id"] for item in first_payload["tickets"]}
        second_ids = {item["billing_ticket_id"] for item in second_payload["tickets"]}
        self.assertFalse(first_ids & second_ids)

    def test_billing_tickets_list_api_keeps_empty_results_on_page_one(self) -> None:
        response = self.client.get(
            "/api/account/billing-tickets?page=99&page_size=30&review_status=reviewed"
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["tickets"], [])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["total_pages"], 1)
        self.assertFalse(payload["has_more"])

    def test_billing_tickets_list_api_paginates_status_filters(self) -> None:
        for i in range(33):
            self._save_billing_ticket(ticket_id=f"TK-AUTO-{i:02d}", automation_status="automation")
        for i in range(32):
            self._save_billing_ticket(
                ticket_id=f"TK-MANUAL-{i:02d}",
                automation_status="not_automated",
                route_confidence=0.4 if i < 31 else 0.95,
            )

        automation_page = self.client.get(
            "/api/account/billing-tickets?page=2&page_size=30&automation_status=automation"
        )
        self.assertEqual(automation_page.status_code, 200, automation_page.text)
        automation_payload = automation_page.json()
        self.assertEqual(automation_payload["count"], 3)
        self.assertEqual(automation_payload["page"], 2)
        self.assertEqual(automation_payload["page_size"], 30)
        self.assertEqual(automation_payload["total"], 33)
        self.assertEqual(automation_payload["total_pages"], 2)
        self.assertTrue(
            all(item["automation_status"] == "automation" for item in automation_payload["tickets"])
        )

        manual_page = self.client.get(
            "/api/account/billing-tickets?page=2&page_size=30&automation_status=not_automated"
        )
        self.assertEqual(manual_page.status_code, 200, manual_page.text)
        manual_payload = manual_page.json()
        self.assertEqual(manual_payload["count"], 2)
        self.assertEqual(manual_payload["page"], 2)
        self.assertEqual(manual_payload["total"], 32)
        self.assertEqual(manual_payload["total_pages"], 2)
        self.assertTrue(
            all(item["automation_status"] == "not_automated" for item in manual_payload["tickets"])
        )

        route_error_page = self.client.get(
            "/api/account/billing-tickets?page=2&page_size=30&route_errors=true"
        )
        self.assertEqual(route_error_page.status_code, 200, route_error_page.text)
        route_error_payload = route_error_page.json()
        self.assertEqual(route_error_payload["count"], 1)
        self.assertEqual(route_error_payload["page"], 2)
        self.assertEqual(route_error_payload["total"], 31)
        self.assertEqual(route_error_payload["total_pages"], 2)
        self.assertTrue(all(item["route_error"] for item in route_error_payload["tickets"]))

        clamped_page = self.client.get(
            "/api/account/billing-tickets?page=99&page_size=30&automation_status=automation"
        )
        self.assertEqual(clamped_page.status_code, 200, clamped_page.text)
        clamped_payload = clamped_page.json()
        self.assertEqual(clamped_payload["page"], 2)
        self.assertEqual(clamped_payload["count"], 3)

    def test_account_cases_list_api_filters_route_labels_before_pagination(self) -> None:
        route_filters = {
            "agora_technical": "Agora Technical",
            "agora_non_technical": "Agora Non-technical",
            "account_billing": "Account & Billing",
        }
        for route_filter, secondary_label in route_filters.items():
            self._save_billing_ticket(
                ticket_id=f"TK-{route_filter}",
                automation_status="not_automated",
                secondary_label=secondary_label,
                intent_class="agora",
                persist_route_labels=False,
            )
        self._save_billing_ticket(
            ticket_id="TK-human-review-agora",
            automation_status="not_automated",
            secondary_label="Agora / Uncategorized",
            intent_class="agora",
            persist_route_labels=False,
        )
        self._save_billing_ticket(
            ticket_id="TK-human-review-uncertain",
            automation_status="not_automated",
            secondary_label="Human Review",
            intent_class="uncertain",
            persist_route_labels=False,
        )
        self._save_billing_ticket(
            ticket_id="TK-conversation",
            automation_status="not_automated",
            secondary_label="Follow-up",
            intent_class="conversation",
            conversation_action="follow_up",
            persist_route_labels=False,
        )
        self._save_billing_ticket(
            ticket_id="TK-legacy-automation",
            automation_status="automation",
        )

        for route_filter, secondary_label in route_filters.items():
            response = self.client.get(
                f"/api/account/cases?page=1&page_size=1&route_label={route_filter}"
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(
                payload["total"],
                1,
            )
            self.assertEqual(payload["count"], 1)
            if route_filter == "account_billing":
                self.assertIn(
                    payload["cases"][0]["secondary_label"],
                    {"Account & Billing / Other", "Account & Billing / Detailed Invoice"},
                )
            else:
                self.assertEqual(payload["cases"][0]["secondary_label"], secondary_label)

        human_review = self.client.get(
            "/api/account/cases?page=1&page_size=10&route_label=human_review"
        )
        self.assertEqual(human_review.status_code, 200, human_review.text)
        human_payload = human_review.json()
        self.assertEqual(human_payload["total"], 2)
        self.assertEqual(
            {item["secondary_label"] for item in human_payload["cases"]},
            {"Uncategorized", "Uncertain"},
        )

        conversation = self.client.get(
            "/api/account/cases?page=1&page_size=10&route_label=conversation"
        )
        self.assertEqual(conversation.status_code, 200, conversation.text)
        conversation_payload = conversation.json()
        self.assertEqual(conversation_payload["total"], 1)
        self.assertEqual(conversation_payload["cases"][0]["primary_label"], "Conversation")
        self.assertEqual(conversation_payload["cases"][0]["secondary_label"], "Follow-up")

        invalid = self.client.get("/api/account/cases?route_label=unsupported")
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_account_cases_route_filter_counts_cover_groups_and_leaves(self) -> None:
        fixtures = [
            ("fraud", "automated", "automation", "fraud_account", "fraud_account"),
            ("invoice", "not_automated", "account_billing", "detailed_invoice", "detailed_invoice"),
            ("enablement", "automated", "automation", "enablement", "enablement"),
            ("quota", "automated", "automation", "quota", "quota"),
            ("suspension", "not_automated", "account_billing", "account_suspension", "account_suspension"),
            ("billing-other", "not_automated", "account_billing", "other", "other"),
            ("technical", "not_automated", "agora_technical", None, None),
            ("follow-up", "not_automated", "conversation", "follow_up", "follow_up"),
            ("uncertain", "not_automated", "human_review", "uncertain", "uncertain"),
        ]
        for ticket_id, status, group, leaf, action in fixtures:
            classification = {
                "intent_class": "conversation" if group == "conversation" else "uncertain" if group == "human_review" else "agora",
                "agora_route": "technical" if group == "agora_technical" else group if group == "account_billing" else "automation" if group == "automation" else None,
                "conversation_action": leaf if group == "conversation" else None,
                "automation_subcategory": leaf if group == "automation" else None,
                "account_billing_subcategory": leaf if group == "account_billing" else None,
            }
            self.repository.save_billing_ticket(
                {
                    "billing_ticket_id": f"BT-{ticket_id}",
                    "client_ticket_id": f"TK-{ticket_id}",
                    "title": ticket_id,
                    "question": "q",
                    "automation_status": status,
                    "route_status": "automated" if status == "automated" else "not_automated",
                    "route_family": "automated" if status == "automated" else "human_review",
                    "route": action or "human_review_required",
                    "execution_action": action or "human_review_required",
                    "scope_label": group,
                    "route_classification": classification,
                }
            )

        response = self.client.get("/api/account/cases?page=2&page_size=3")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            [item["label"] for item in payload["filter_definitions"]],
            [
                "All",
                "Automated",
                "Backend Operation",
                "Account & Billing",
                "Tech",
                "Security & Compliance",
                "Conversation",
                "Human Review",
            ],
        )
        automation_definition = next(
            group for group in payload["filter_definitions"] if group["id"] == "automation"
        )
        self.assertEqual(
            {child["id"] for child in automation_definition["children"]},
            {"fraud_account", "enablement"},
        )
        counts = payload["filter_counts"]
        self.assertEqual(payload["total"], len(fixtures))
        self.assertEqual(payload["page"], 2)
        self.assertEqual(payload["count"], 3)
        self.assertEqual(counts["all"], len(fixtures))
        self.assertEqual(counts["automation"], 3)
        self.assertEqual(counts["automation:fraud_account"], 1)
        self.assertNotIn("automation:detailed_invoice", counts)
        self.assertEqual(counts["automation:enablement"], 1)
        self.assertEqual(counts["automation:quota"], 1)
        self.assertEqual(counts["backend_operation"], 2)
        self.assertEqual(counts["backend_operation:enablement"], 1)
        self.assertEqual(counts["backend_operation:quota"], 1)
        self.assertEqual(counts["account_billing"], 4)
        self.assertEqual(counts["account_billing:fraud_account"], 1)
        self.assertEqual(counts["account_billing:detailed_invoice"], 1)
        self.assertEqual(counts["account_billing:account_suspension"], 1)
        self.assertEqual(counts["account_billing:other"], 1)
        self.assertEqual(counts["conversation"], 1)
        self.assertEqual(counts["human_review"], 1)
        self.assertEqual(counts["human_review:other"], 0)
        self.assertEqual(counts["human_review:uncertain"], 1)
        self.assertNotIn(
            "unregistered",
            {
                child["id"]
                for child in next(
                    group for group in payload["filter_definitions"] if group["id"] == "human_review"
                )["children"]
            },
        )

        filtered = self.client.get(
            "/api/account/cases?page_size=10&route_group=automation&route_subcategory=enablement"
        )
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual(filtered.json()["total"], 1)
        self.assertEqual(filtered.json()["filter_counts"], counts)

        self.assertEqual(
            self.client.get(
                "/api/account/cases?route_group=agora_technical&route_subcategory=enablement"
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                "/api/account/cases?route_group=all&route_subcategory=enablement"
            ).status_code,
            422,
        )

    def test_account_case_filter_membership_overlaps_business_and_execution_views(self) -> None:
        fixtures = [
            (
                "BT-FRAUD-MEMBERSHIP",
                "account_billing",
                "fraud_account",
                "automated",
                {
                    "intent_class": "agora",
                    "agora_route": "account_billing",
                    "account_billing_subcategory": "fraud_account",
                    "pipeline_version": "account-layered-router-v7",
                },
            ),
            (
                "BT-INVOICE-MEMBERSHIP",
                "account_billing",
                "detailed_invoice",
                "automated",
                {
                    "intent_class": "agora",
                    "agora_route": "account_billing",
                    "account_billing_subcategory": "detailed_invoice",
                    "pipeline_version": "account-layered-router-v7",
                },
            ),
            (
                "BT-ENABLEMENT-MEMBERSHIP",
                "backend_operation",
                "enablement",
                "automated",
                {
                    "intent_class": "agora",
                    "agora_route": "backend_operation",
                    "backend_operation_subcategory": "enablement",
                    "pipeline_version": "account-layered-router-v7",
                },
            ),
            (
                "BT-UNREGISTERED-MEMBERSHIP",
                "backend_operation",
                "unregistered",
                "not_automated",
                {
                    "intent_class": "agora",
                    "agora_route": "backend_operation",
                    "backend_operation_subcategory": "unregistered",
                    "pipeline_version": "account-layered-router-v7",
                },
            ),
            (
                "BT-SUSPENSION-MEMBERSHIP",
                "account_billing",
                "account_suspension",
                "not_automated",
                {
                    "intent_class": "agora",
                    "agora_route": "account_billing",
                    "account_billing_subcategory": "account_suspension",
                    "pipeline_version": "account-layered-router-v7",
                },
            ),
        ]
        for billing_ticket_id, scope_label, action, route_status, classification in fixtures:
            self.repository.save_billing_ticket(
                {
                    "billing_ticket_id": billing_ticket_id,
                    "client_ticket_id": billing_ticket_id.replace("BT-", "TK-"),
                    "title": billing_ticket_id,
                    "question": "q",
                    "scope_label": scope_label,
                    "route": action,
                    "execution_action": action,
                    "route_family": "automated" if route_status == "automated" else "human_review",
                    "route_status": route_status,
                    "category": "account_billing" if scope_label == "account_billing" else None,
                    "subcategory": action,
                    "route_classification": classification,
                }
            )

        payload = self.client.get("/api/account/cases?page_size=10").json()
        counts = payload["filter_counts"]
        self.assertEqual(counts["all"], 5)
        self.assertEqual(counts["automation"], 2)
        self.assertEqual(counts["automation:fraud_account"], 1)
        self.assertNotIn("automation:detailed_invoice", counts)
        self.assertEqual(counts["automation:enablement"], 1)
        self.assertEqual(counts["account_billing"], 3)
        self.assertEqual(counts["account_billing:fraud_account"], 1)
        self.assertEqual(counts["account_billing:detailed_invoice"], 1)
        self.assertEqual(counts["account_billing:account_suspension"], 1)
        self.assertEqual(counts["human_review"], 0)
        self.assertNotIn("human_review:unregistered", counts)
        self.assertEqual(counts["human_review:other"], 0)

        automation = self.client.get(
            "/api/account/cases?route_group=automation&page_size=10"
        ).json()
        self.assertEqual(automation["total"], 2)
        account_billing = self.client.get(
            "/api/account/cases?route_group=account_billing&page_size=10"
        ).json()
        self.assertEqual(account_billing["total"], 3)
        human_review = self.client.get(
            "/api/account/cases?route_group=human_review&page_size=10"
        ).json()
        self.assertEqual(human_review["total"], 0)
        unregistered = self.client.get(
            "/api/account/cases?route_group=human_review&route_subcategory=unregistered&page_size=10"
        ).json()
        self.assertEqual(unregistered["total"], 1)
        legacy_automation = self.client.get(
            "/api/account/cases?route_label=automation:enablement&page_size=10"
        )
        self.assertEqual(legacy_automation.status_code, 200, legacy_automation.text)
        self.assertEqual(legacy_automation.json()["total"], 1)

    def test_account_cases_list_fetches_latest_reply_jobs_in_one_batch(self) -> None:
        for index in range(2):
            ticket_id = f"TK-BATCH-{index}"
            self._save_billing_ticket(ticket_id=ticket_id, automation_status="automation")
            self.repository.save_account_reply_job(
                {
                    "job_id": f"JOB-{index}",
                    "ticket_id": ticket_id,
                    "status": "scheduled",
                    "created_at": f"2026-07-28T00:0{index}:00+00:00",
                }
            )
            self.repository.resolve_account_persona(ticket_id)

        original_page = self.repository.list_account_case_page_with_filter_counts
        original_batch = self.repository.get_latest_account_reply_jobs
        with patch.object(
            self.repository,
            "list_account_case_page_with_filter_counts",
            wraps=original_page,
        ) as page_lookup, patch.object(
            self.repository,
            "count_account_cases",
            side_effect=AssertionError("list API must get total from the page query"),
        ), patch.object(
            self.repository,
            "list_account_cases",
            side_effect=AssertionError("list API must use the lightweight page query"),
        ), patch.object(
            self.repository,
            "get_latest_account_reply_jobs",
            wraps=original_batch,
        ) as batch_lookup, patch.object(
            self.repository,
            "get_latest_account_reply_job",
            side_effect=AssertionError("list API must not query reply jobs one ticket at a time"),
        ), patch.object(
            self.repository,
            "get_account_persona_assignment",
            side_effect=AssertionError("list API must not query Persona assignments one ticket at a time"),
        ):
            response = self.client.get("/api/account/cases?page_size=30&route_status=automation")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(page_lookup.call_count, 1)
        self.assertEqual(batch_lookup.call_count, 1)
        requested_ids = batch_lookup.call_args.args[0]
        self.assertEqual(set(requested_ids), {"TK-BATCH-0", "TK-BATCH-1"})
        statuses = {
            item["client_ticket_id"]: item["ai_reply_status"] for item in response.json()["cases"]
        }
        self.assertEqual(statuses, {"TK-BATCH-0": "scheduled", "TK-BATCH-1": "scheduled"})

    def test_account_case_summary_is_no_store_and_excludes_customer_detail_fields(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-SAFE-SUMMARY",
                "client_ticket_id": "TK-SAFE-SUMMARY",
                "title": "Safe summary",
                "question": "private customer message",
                "customer_email": "private@example.com",
                "collected_fields": {"transaction_id": "secret"},
                "internal_email_payload": {"body": "private body"},
                "automation_status": "not_automated",
                "route": "human_review_required",
                "scope_label": "account_billing",
                "route_family": "billing_review",
                "execution_action": "human_review_required",
            }
        )

        response = self.client.get("/api/account/cases?page=1&page_size=10")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store, max-age=0")
        self.assertEqual(response.headers["pragma"], "no-cache")
        summary = response.json()["cases"][0]
        self.assertEqual(summary["client_ticket_id"], "TK-SAFE-SUMMARY")
        self.assertTrue(summary["detail_revision"])
        for sensitive_field in (
            "question",
            "messages",
            "customer_email",
            "customer_id",
            "requester",
            "collected_fields",
            "internal_email_payload",
        ):
            self.assertNotIn(sensitive_field, summary)

    def test_account_case_batch_details_limits_size_and_reports_unknown_ids(self) -> None:
        self._save_billing_ticket(
            ticket_id="TK-BATCH-DETAIL",
            automation_status="not_automated",
        )

        response = self.client.post(
            "/api/account/cases/batch-details",
            json={"case_ids": ["BT-TK-BATCH-DETAIL", "BT-UNKNOWN"]},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store, max-age=0")
        payload = response.json()
        self.assertEqual(len(payload["details"]), 1)
        self.assertEqual(payload["details"][0]["billing_ticket_id"], "BT-TK-BATCH-DETAIL")
        self.assertEqual(payload["missing_case_ids"], ["BT-UNKNOWN"])

        too_many = self.client.post(
            "/api/account/cases/batch-details",
            json={"case_ids": [f"BT-{index}" for index in range(11)]},
        )
        self.assertEqual(too_many.status_code, 422, too_many.text)

        too_long = self.client.post(
            "/api/account/cases/batch-details",
            json={"case_ids": ["A" * 129]},
        )
        self.assertEqual(too_long.status_code, 422, too_long.text)

    def test_account_case_batch_and_single_detail_are_consistent(self) -> None:
        self._save_billing_ticket(
            ticket_id="TK-DETAIL-CONSISTENCY",
            automation_status="automation",
        )
        case_id = "BT-TK-DETAIL-CONSISTENCY"

        single = self.client.get(f"/api/account/cases/{case_id}")
        batch = self.client.post(
            "/api/account/cases/batch-details",
            json={"case_ids": [case_id]},
        )

        self.assertEqual(single.status_code, 200, single.text)
        self.assertEqual(batch.status_code, 200, batch.text)
        single_payload = single.json()
        batch_payload = batch.json()["details"][0]
        self.assertEqual(
            {key: value for key, value in batch_payload.items() if key not in {"zendesk_comments", "zendesk_comments_included"}},
            {key: value for key, value in single_payload.items() if key not in {"zendesk_comments", "zendesk_comments_included"}},
        )
        self.assertFalse(batch_payload["zendesk_comments_included"])
        self.assertTrue(single_payload["zendesk_comments_included"])

    def test_account_case_revision_tracks_persona_assignment_identity(self) -> None:
        ticket_id = "TK-PERSONA-REVISION"
        case_id = f"BT-{ticket_id}"
        self._save_billing_ticket(ticket_id=ticket_id, automation_status="automation")

        def revisions() -> tuple[str, str, str]:
            page = self.client.get("/api/account/cases?page=1&page_size=10")
            single = self.client.get(f"/api/account/cases/{case_id}")
            batch = self.client.post(
                "/api/account/cases/batch-details",
                json={"case_ids": [case_id]},
            )
            self.assertEqual(page.status_code, 200, page.text)
            self.assertEqual(single.status_code, 200, single.text)
            self.assertEqual(batch.status_code, 200, batch.text)
            return (
                page.json()["cases"][0]["detail_revision"],
                single.json()["detail_revision"],
                batch.json()["details"][0]["detail_revision"],
            )

        unassigned = revisions()
        self.assertEqual(len(set(unassigned)), 1)
        with self.repository._assignment_lock:
            self.repository._account_persona_assignments[ticket_id] = {
                "ticket_id": ticket_id,
                "persona_key": "sid-precise",
                "version": 1,
                "assigned_at": "2026-08-09T01:00:00+00:00",
            }
        assigned = revisions()

        self.assertEqual(len(set(assigned)), 1)
        self.assertNotEqual(assigned[0], unassigned[0])

        stable = revisions()
        self.assertEqual(stable, assigned)

        with self.repository._assignment_lock:
            self.repository._account_persona_assignments[ticket_id]["persona_key"] = "sid-bright"
        reassigned = revisions()
        self.assertEqual(len(set(reassigned)), 1)
        self.assertNotEqual(reassigned[0], assigned[0])

        with self.repository._assignment_lock:
            self.repository._account_persona_assignments[ticket_id]["version"] = 2
        new_version = revisions()
        self.assertEqual(len(set(new_version)), 1)
        self.assertNotEqual(new_version[0], reassigned[0])

        with self.repository._assignment_lock:
            self.repository._account_persona_assignments[ticket_id]["assigned_at"] = (
                "2026-08-09T01:01:00+00:00"
            )
        reassigned_at = revisions()
        self.assertEqual(len(set(reassigned_at)), 1)
        self.assertNotEqual(reassigned_at[0], new_version[0])

        with self.repository._assignment_lock:
            self.repository._account_personas["sid-bright"]["display_name"] = "Sid Brighter"
        renamed = revisions()
        self.assertEqual(len(set(renamed)), 1)
        self.assertNotEqual(renamed[0], reassigned_at[0])

        with self.repository._assignment_lock:
            self.repository._account_persona_assignments.pop(ticket_id)
        cleared = revisions()
        self.assertEqual(len(set(cleared)), 1)
        self.assertNotEqual(cleared[0], renamed[0])
        self.assertEqual(cleared, unassigned)

    def test_account_case_details_include_persisted_persona_assignment_without_prompt_content(self) -> None:
        ticket_id = "TK-PERSONA-DETAIL"
        self._save_billing_ticket(ticket_id=ticket_id, automation_status="automation")
        draft = self.repository.create_account_persona_draft(
            "sid-bright",
            content={
                "instruction": "SECRET PERSONA PROMPT",
                "opener": "Hello",
            },
            change_note="Persona detail contract",
            based_on_version=1,
            actor_id="admin",
            created_at="2026-08-09T01:00:00+00:00",
        )
        self.repository.publish_account_persona_version(
            "sid-bright",
            draft["version"],
            actor_id="admin",
            published_at="2026-08-09T01:01:00+00:00",
        )
        self.repository.set_account_persona_enabled("sid-precise", False)
        self.repository.set_account_persona_enabled("default-support", False)
        assigned = self.repository.resolve_account_persona(ticket_id)
        self.assertEqual((assigned["persona_key"], assigned["version"]), ("sid-bright", 2))
        superseding_draft = self.repository.create_account_persona_draft(
            "sid-bright",
            content={
                "instruction": "New published voice",
                "opener": "Hi",
            },
            change_note="Supersede assigned version",
            based_on_version=2,
            actor_id="admin",
            created_at="2026-08-09T01:02:00+00:00",
        )
        self.repository.publish_account_persona_version(
            "sid-bright",
            superseding_draft["version"],
            actor_id="admin",
            published_at="2026-08-09T01:03:00+00:00",
        )
        self.repository.set_account_persona_enabled("sid-precise", True)
        self.repository.set_account_persona_enabled("sid-bright", False)

        with patch.object(
            self.repository,
            "get_account_persona_assignment",
            side_effect=AssertionError("detail API must use the batched detail bundle"),
        ):
            single = self.client.get("/api/account/cases/BT-TK-PERSONA-DETAIL")
            batch = self.client.post(
                "/api/account/cases/batch-details",
                json={"case_ids": ["BT-TK-PERSONA-DETAIL"]},
            )

        self.assertEqual(single.status_code, 200, single.text)
        self.assertEqual(batch.status_code, 200, batch.text)
        single_assignment = single.json()["persona_assignment"]
        self.assertEqual(batch.json()["details"][0]["persona_assignment"], single_assignment)
        self.assertEqual(
            single_assignment,
            {
                "persona_key": "sid-bright",
                "version": 2,
                "assigned_at": assigned["assigned_at"],
                "display_name": "Sid Bright",
            },
        )
        self.assertNotIn("content", single_assignment)
        self.assertNotIn("enabled", single_assignment)
        self.assertNotIn("SECRET PERSONA PROMPT", single.text)

    def test_account_case_detail_returns_null_when_persona_is_not_assigned(self) -> None:
        self._save_billing_ticket(
            ticket_id="TK-PERSONA-UNASSIGNED",
            automation_status="automation",
        )

        response = self.client.get("/api/account/cases/BT-TK-PERSONA-UNASSIGNED")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["persona_assignment"])

    def test_delete_all_billing_tickets_is_not_allowed_and_preserves_account_list(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            for i in range(2):
                response = self.client.post(
                    "/account",
                    json={
                        "title": f"Ticket {i}",
                        "question": f"Question {i}",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)

        before = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(before.status_code, 200)
        before_payload = before.json()
        self.assertEqual(before_payload["count"], 2)
        before_ids = [item["billing_ticket_id"] for item in before_payload["tickets"]]

        response = self.client.delete("/api/account/billing-tickets")
        self.assertEqual(response.status_code, 405, response.text)

        after = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(after.status_code, 200)
        after_payload = after.json()
        self.assertEqual(after_payload["count"], 2)
        self.assertEqual(
            [item["billing_ticket_id"] for item in after_payload["tickets"]],
            before_ids,
        )

    def test_delete_all_billing_tickets_is_not_allowed_when_empty(self) -> None:
        response = self.client.delete("/api/account/billing-tickets")

        self.assertEqual(response.status_code, 405, response.text)

    def test_billing_tickets_detail_api(self) -> None:
        create_payload, _ = self._create_invoice_ticket_with_response_token()
        bt_id = create_payload["billing_ticket_id"]

        response = self.client.get(f"/api/account/billing-tickets/{bt_id}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["billing_ticket_id"], bt_id)
        self.assertEqual(detail["ticket_id"], detail.get("client_ticket_id"))
        self.assertNotIn("support_ticket_id", detail)
        self.assertEqual(detail["automation_status"], "automation")
        self.assertEqual(detail["status"], "automation")
        self.assertEqual(detail["route"], "detailed_invoice")
        self.assertEqual(detail.get("missing_fields"), [])
        # Detail now includes canonical ticket messages.
        self.assertIn("messages", detail)
        self.assertIsInstance(detail["messages"], list)
        self.assertTrue(len(detail["messages"]) >= 1)
        self.assertIn("customer_id", detail)
        self.assertIn("requester", detail)
        self.assertIn("support_ticket_status", detail)

    def test_route_correction_updates_active_tuple_and_records_event_only(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()) as dispatch_mock, patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as email_mock:
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send the detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200, create_response.text)
        billing_ticket_id = create_response.json()["billing_ticket_id"]
        pre_correction_email_calls = email_mock.call_count

        with patch.object(main, "dispatch_event", AsyncMock()) as correction_dispatch, patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as correction_email:
            response = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
                json={
                    "scope_label": "billing",
                    "execution_action": "human_review_required",
                    "corrector": "operator",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["route_corrected"])
        self.assertTrue(payload["route_error"])
        self.assertEqual(payload["route"], "human_review_required")
        self.assertEqual(payload["scope_label"], "billing")
        self.assertEqual(payload["route_family"], "billing_review")
        self.assertEqual(payload["execution_action"], "human_review_required")
        self.assertEqual(payload["tooling_profile"], "deterministic_billing_intake")
        self.assertEqual(payload["primary_label"], "Agora")
        self.assertEqual(payload["secondary_label"], "Account & Billing / Other")
        self.assertEqual(payload["automation_status"], "not_automated")
        self.assertIn(
            payload["route_correction"]["original_execution_action"],
            {"detailed_invoice", "human_review_required"},
        )
        self.assertEqual(payload["route_correction"]["corrected_execution_action"], "human_review_required")
        self.assertEqual(payload["route_correction"]["first_corrected_execution_action"], "human_review_required")
        self.assertEqual(payload["route_correction"]["correction_count"], 1)
        correction_email.assert_not_called()
        self.assertGreaterEqual(pre_correction_email_calls, 0)
        events = self.repository.list_ticket_events(payload["ticket_id"])
        route_events = [item for item in events if item["event_type"] == "route_corrected"]
        self.assertEqual(len(route_events), 1)
        event_payload = route_events[0]["payload"]
        self.assertIn(
            event_payload["original_execution_action"],
            {"detailed_invoice", "human_review_required"},
        )
        self.assertEqual(event_payload["corrected_execution_action"], "human_review_required")
        dispatched_events = [call.args[1]["event"] for call in correction_dispatch.await_args_list]
        self.assertEqual(dispatched_events, ["route_corrected"])

        # Sanity: account creation dispatched through the normal path, while correction did not replay it.
        self.assertTrue(dispatch_mock.await_args_list)

    def test_route_correction_rejects_invalid_tuple_before_mutating_ticket(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "Detailed invoice request", "question": "Please send detailed invoice."},
            )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        billing_ticket_id = create_response.json()["billing_ticket_id"]
        before = self.repository.get_billing_ticket(billing_ticket_id)
        assert before is not None

        with patch.object(main, "dispatch_event", AsyncMock()) as dispatch_mock:
            response = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
                json={"scope_label": "billing", "execution_action": "rag"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        after = self.repository.get_billing_ticket(billing_ticket_id)
        self.assertEqual(after, before)
        self.assertIsNone(self.repository.get_billing_route_correction(billing_ticket_id))
        dispatch_mock.assert_not_called()

    def test_canonical_route_correction_accepts_automation_subcategory(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "General question", "question": "Tell me about Agora products."},
            )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        account_case_id = create_response.json()["account_case_id"]

        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                f"/api/account/cases/{account_case_id}/route-correction",
                json={"category": "automation", "subcategory": "fraud_account"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["category"], "account_billing")
        self.assertEqual(payload["subcategory"], "fraud_account")
        self.assertEqual(payload["route_family"], "automated")
        self.assertEqual(payload["route_status"], "automated")
        self.assertEqual(payload["automation_handler"], "billing")
        self.assertEqual(payload["automation_status"], "automation")
        self.assertEqual(payload["primary_label"], "Agora")
        self.assertEqual(payload["secondary_label"], "Account & Billing / Fraud Account")
        stored = self.repository.get_account_case(account_case_id)
        assert stored is not None
        self.assertEqual(stored["route_classification"]["agora_route"], "account_billing")
        self.assertEqual(
            stored["route_classification"]["account_billing_subcategory"],
            "fraud_account",
        )
        self.assertEqual(stored["route_classification"]["classification_source"], "operator_correction")

    def test_route_correction_uses_new_automation_and_human_review_taxonomy(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "Correction taxonomy", "question": "General Agora question."},
            )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        account_case_id = create_response.json()["account_case_id"]

        enablement = self.client.post(
            f"/api/account/cases/{account_case_id}/route-correction",
            json={"category": "automation", "subcategory": "enablement"},
        )
        self.assertEqual(enablement.status_code, 200, enablement.text)
        enablement_payload = enablement.json()
        self.assertEqual(enablement_payload["category"], "backend_operation")
        self.assertEqual(enablement_payload["subcategory"], "enablement")
        self.assertEqual(enablement_payload["route_status"], "automated")
        self.assertEqual(enablement_payload["primary_label"], "Agora")
        self.assertEqual(enablement_payload["secondary_label"], "Backend Operation / Enablement")

        unregistered = self.client.post(
            f"/api/account/cases/{account_case_id}/route-correction",
            json={"category": "human_review", "subcategory": "unregistered"},
        )
        self.assertEqual(unregistered.status_code, 200, unregistered.text)
        unregistered_payload = unregistered.json()
        self.assertEqual(unregistered_payload["category"], "backend_operation")
        self.assertEqual(unregistered_payload["subcategory"], "unregistered")
        self.assertEqual(unregistered_payload["route_status"], "not_automated")
        self.assertEqual(unregistered_payload["primary_label"], "Agora")
        self.assertEqual(unregistered_payload["secondary_label"], "Backend Operation / Unregistered")

    def test_route_correction_missing_ticket_returns_404(self) -> None:
        response = self.client.post(
            "/api/account/billing-tickets/BT-MISSING/route-correction",
            json={"scope_label": "billing", "execution_action": "human_review_required"},
        )

        self.assertEqual(response.status_code, 404, response.text)

    def test_route_correction_recorrrection_preserves_original_and_first_corrected(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "Detailed invoice request", "question": "Please send detailed invoice."},
            )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        billing_ticket_id = create_response.json()["billing_ticket_id"]

        first_response = self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
            json={"scope_label": "billing", "execution_action": "human_review_required"},
        )
        self.assertEqual(first_response.status_code, 200, first_response.text)
        second_response = self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
            json={"scope_label": "billing", "execution_action": "refuse"},
        )
        self.assertEqual(second_response.status_code, 200, second_response.text)
        correction = second_response.json()["route_correction"]
        self.assertIn(
            correction["original_execution_action"],
            {"detailed_invoice", "human_review_required"},
        )
        self.assertEqual(correction["first_corrected_execution_action"], "human_review_required")
        self.assertEqual(correction["corrected_execution_action"], "refuse")
        self.assertEqual(correction["correction_count"], 2)
        saved = self.repository.get_billing_ticket(billing_ticket_id)
        assert saved is not None
        self.assertEqual(saved["route"], "refuse")
        self.assertEqual(saved["route_family"], "fallback_or_refuse")

    def test_route_correction_flags_list_detail_and_summary(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-CORRECTED-001",
                "client_ticket_id": "TK-CORRECTED-001",
                "source": "manual",
                "title": "Corrected",
                "question": "q",
                "automation_status": "automation",
                "route": "detailed_invoice",
                "scope_label": "billing",
                "route_family": "automated",
                "execution_action": "detailed_invoice",
                "tooling_profile": "deterministic_billing_intake",
                "route_reason": "invoice",
                "route_confidence": 0.95,
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LOWCONF-001",
                "client_ticket_id": "TK-LOWCONF-001",
                "source": "manual",
                "title": "Low confidence",
                "question": "q",
                "automation_status": "not_automated",
                "route": "web_search",
                "scope_label": "agora_non_technical",
                "route_family": "web_company_info",
                "execution_action": "web_search",
                "tooling_profile": "official_web_search",
                "route_confidence": 0.2,
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-CLEAN-001",
                "client_ticket_id": "TK-CLEAN-001",
                "source": "manual",
                "title": "Clean",
                "question": "q",
                "automation_status": "not_automated",
                "route": "web_search",
                "scope_label": "agora_non_technical",
                "route_family": "web_company_info",
                "execution_action": "web_search",
                "tooling_profile": "official_web_search",
                "route_confidence": 0.99,
            }
        )
        correction_response = self.client.post(
            "/api/account/billing-tickets/BT-TK-CORRECTED-001/route-correction",
            json={"scope_label": "billing", "execution_action": "human_review_required"},
        )
        self.assertEqual(correction_response.status_code, 200, correction_response.text)

        list_payload = self.client.get("/api/account/billing-tickets?limit=10").json()
        by_id = {item["billing_ticket_id"]: item for item in list_payload["tickets"]}
        self.assertTrue(by_id["BT-TK-CORRECTED-001"]["route_corrected"])
        self.assertTrue(by_id["BT-TK-CORRECTED-001"]["route_error"])
        self.assertFalse(by_id["BT-TK-LOWCONF-001"]["route_corrected"])
        self.assertTrue(by_id["BT-TK-LOWCONF-001"]["route_error"])
        self.assertFalse(by_id["BT-TK-CLEAN-001"]["route_corrected"])
        self.assertFalse(by_id["BT-TK-CLEAN-001"]["route_error"])

        detail = self.client.get("/api/account/billing-tickets/TK-CORRECTED-001").json()
        self.assertTrue(detail["route_corrected"])
        self.assertTrue(detail["route_error"])
        self.assertEqual(detail["route_correction"]["corrected_execution_action"], "human_review_required")

        summary = self.client.get("/api/account/route-errors/summary?limit=10").json()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["corrected_count"], 1)
        self.assertEqual(summary["low_confidence_count"], 1)
        transitions = {item["transition"]: item["count"] for item in summary["transitions"]}
        self.assertEqual(transitions["detailed_invoice -> human_review_required"], 1)

    def test_route_correction_api_uses_atomic_repository_method_and_persisted_count(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "Detailed invoice request", "question": "Please send detailed invoice."},
            )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        billing_ticket_id = create_response.json()["billing_ticket_id"]
        original_apply = self.repository.apply_billing_route_correction
        calls: list[dict[str, object]] = []

        def fake_apply(*, billing_ticket_id: str, active_route: dict[str, object], correction: dict[str, object]):
            calls.append(
                {
                    "billing_ticket_id": billing_ticket_id,
                    "active_route": dict(active_route),
                    "correction": dict(correction),
                }
            )
            saved = original_apply(
                billing_ticket_id=billing_ticket_id,
                active_route=active_route,
                correction=correction,
            )
            saved["correction_count"] = 7
            self.repository._billing_route_corrections[billing_ticket_id] = dict(saved)
            return saved

        with patch.object(self.repository, "apply_billing_route_correction", side_effect=fake_apply), patch.object(
            self.repository,
            "save_billing_ticket",
            side_effect=AssertionError("API must not save active route separately"),
        ), patch.object(self.repository, "save_billing_route_correction", side_effect=AssertionError("API must use atomic apply")):
            response = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
                json={"scope_label": "billing", "execution_action": "human_review_required"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(response.json()["route_correction"]["correction_count"], 7)

    def test_route_error_summary_fetches_corrections_in_one_batch(self) -> None:
        for i in range(3):
            billing_ticket_id = f"BT-TK-SUMMARY-{i}"
            self.repository.save_billing_ticket(
                {
                    "billing_ticket_id": billing_ticket_id,
                    "client_ticket_id": f"TK-SUMMARY-{i}",
                    "source": "manual",
                    "title": f"Summary {i}",
                    "question": "q",
                    "automation_status": "automation",
                    "route": "human_review_required",
                    "scope_label": "billing",
                    "route_family": "billing_review",
                    "execution_action": "human_review_required",
                    "tooling_profile": "deterministic_billing_intake",
                    "route_confidence": 0.95,
                    "created_at": f"2026-06-19T00:0{i}:00+00:00",
                }
            )
            self.repository.save_billing_route_correction(
                {
                    "billing_ticket_id": billing_ticket_id,
                    "client_ticket_id": f"TK-SUMMARY-{i}",
                    "original_execution_action": "detailed_invoice",
                    "corrected_scope_label": "billing",
                    "corrected_route_family": "billing_review",
                    "corrected_execution_action": "human_review_required",
                    "corrected_tooling_profile": "deterministic_billing_intake",
                    "first_corrected_scope_label": "billing",
                    "first_corrected_route_family": "billing_review",
                    "first_corrected_execution_action": "human_review_required",
                    "first_corrected_tooling_profile": "deterministic_billing_intake",
                    "updated_at": f"2026-06-19T00:0{i}:00+00:00",
                }
            )

        original_batch = self.repository.get_billing_route_corrections_for_tickets
        with patch.object(
            self.repository,
            "get_billing_route_corrections_for_tickets",
            wraps=original_batch,
        ) as batch_lookup, patch.object(
            self.repository,
            "get_billing_route_correction",
            side_effect=AssertionError("summary API must not query corrections one ticket at a time"),
        ):
            summary = self.client.get("/api/account/route-errors/summary?limit=2").json()

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["corrected_count"], 2)
        self.assertEqual(batch_lookup.call_count, 1)
        self.assertEqual(
            set(batch_lookup.call_args.args[0]),
            {"BT-TK-SUMMARY-1", "BT-TK-SUMMARY-2"},
        )
        transitions = {item["transition"]: item["count"] for item in summary["transitions"]}
        self.assertEqual(transitions["detailed_invoice -> human_review_required"], 2)

    def test_route_review_marks_ticket_and_filters_list(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            for i in range(3):
                response = self.client.post(
                    "/account",
                    json={"title": f"Review ticket {i}", "question": f"Question {i}"},
                )
                self.assertEqual(response.status_code, 200, response.text)
        billing_ticket_id = None
        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        for item in list_response.json()["tickets"]:
            self.assertEqual(item["route_review_status"], "pending")
            if billing_ticket_id is None:
                billing_ticket_id = item["billing_ticket_id"]

        with patch.object(main, "dispatch_event", AsyncMock()) as review_dispatch:
            review_response = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-review",
                json={"review_status": "reviewed", "reviewer": "operator"},
            )
        self.assertEqual(review_response.status_code, 200, review_response.text)
        reviewed_payload = review_response.json()
        self.assertEqual(reviewed_payload["route_review_status"], "reviewed")

        events = self.repository.list_ticket_events(reviewed_payload["ticket_id"])
        review_events = [item for item in events if item["event_type"] == "route_reviewed"]
        self.assertEqual(len(review_events), 1)
        self.assertEqual(review_events[0]["payload"]["review_status"], "reviewed")
        dispatched_events = [call.args[1]["event"] for call in review_dispatch.await_args_list]
        self.assertIn("route_reviewed", dispatched_events)

        unreviewed_response = self.client.get(
            "/api/account/billing-tickets?limit=30&review_status=pending"
        )
        self.assertEqual(unreviewed_response.status_code, 200)
        unreviewed_items = unreviewed_response.json()["tickets"]
        self.assertEqual(len(unreviewed_items), 2)
        for item in unreviewed_items:
            self.assertEqual(item["route_review_status"], "pending")

        reviewed_response = self.client.get(
            "/api/account/billing-tickets?limit=30&review_status=reviewed"
        )
        self.assertEqual(reviewed_response.status_code, 200)
        reviewed_items = reviewed_response.json()["tickets"]
        self.assertEqual(len(reviewed_items), 1)
        self.assertEqual(reviewed_items[0]["billing_ticket_id"], billing_ticket_id)
        self.assertEqual(reviewed_items[0]["route_review_status"], "reviewed")

        with patch.object(main, "dispatch_event", AsyncMock()):
            unreview_revert = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-review",
                json={"review_status": "pending", "reviewer": "operator"},
            )
        self.assertEqual(unreview_revert.status_code, 200)
        self.assertEqual(unreview_revert.json()["route_review_status"], "pending")

    def test_route_review_rejects_invalid_status(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "Bad review", "question": "Question"},
            )
        self.assertEqual(create_response.status_code, 200)
        billing_ticket_id = create_response.json()["billing_ticket_id"]

        response = self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-review",
            json={"review_status": "invalid_status"},
        )
        self.assertEqual(response.status_code, 400)

    def test_route_review_missing_ticket_returns_404(self) -> None:
        response = self.client.post(
            "/api/account/billing-tickets/BT-MISSING/route-review",
            json={"review_status": "reviewed"},
        )
        self.assertEqual(response.status_code, 404)

    def test_billing_ticket_view_model_normalizes_legacy_api_source(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LEGACY-001",
                "client_ticket_id": "TK-LEGACY-001",
                "source": "/account-http",
                "title": "Legacy API ticket",
                "question": "legacy question",
                "automation_status": "not_automated",
            }
        )

        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        self.assertEqual(list_payload["tickets"][0]["ticket_id"], "TK-LEGACY-001")
        self.assertEqual(list_payload["tickets"][0]["source"], "api")

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-LEGACY-001")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["ticket_id"], "TK-LEGACY-001")
        self.assertNotIn("support_ticket_id", detail)
        self.assertEqual(detail["source"], "api")

    def test_account_intake_suspension_route_is_account_billing_classification_only(self) -> None:
        extraction = AccountSuspensionFieldExtraction(
            status="partial",
            collected_fields={"suspension_status_or_error": "account suspended"},
            grounding_status="passed",
        )
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main, "decide_account_route", return_value=_account_suspension_route_result()
        ), patch.object(main, "extract_account_suspension_fields", return_value=extraction):
            response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended. I cannot log in.",
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["route"], "human_review_required")
        self.assertEqual(payload["category"], "account_billing")
        self.assertEqual(payload["subcategory"], "account_suspension")
        self.assertEqual(payload["secondary_label"], "Account & Billing / Account Suspension")
        self.assertEqual(payload["customer_reply"], "")
        self.assertIsNone(payload["ai_reply_status"])
        self.assertEqual(payload["internal_email_send_status"], "not_applicable")
        self.assertEqual(payload["internal_email_send_reason"], "account_billing_classification_only")
        self.assertIsNone(payload["automation_handler"])
        self.assertNotIn("support_ticket_id", payload)

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["automation_status"], "not_automated")
        self.assertEqual(bt["route"], "human_review_required")
        self.assertEqual(bt["execution_action"], "human_review_required")
        self.assertEqual(bt["subcategory"], "account_suspension")
        self.assertEqual(
            bt["collected_fields"],
            {"suspension_status_or_error": "account suspended"},
        )
        self.assertEqual(bt["automation_context"], {})

    def test_account_suspension_customer_reply_reextracts_without_automation_side_effects(self) -> None:
        extractions = [
            AccountSuspensionFieldExtraction(
                status="partial",
                collected_fields={"suspension_status_or_error": "account suspended"},
                grounding_status="passed",
            ),
            AccountSuspensionFieldExtraction(
                status="partial",
                collected_fields={
                    "suspension_status_or_error": "account suspended",
                    "known_reason": "balance",
                    "customer_actions_taken": "topped up",
                },
                grounding_status="passed",
            ),
        ]
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main, "decide_account_route", return_value=_account_suspension_route_result()
        ), patch.object(
            main, "extract_account_suspension_fields", side_effect=extractions
        ) as extractor:
            created = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "Our account is suspended.",
                    "customer_email": "customer@example.com",
                },
            ).json()
            response = self.client.post(
                f"/api/account/cases/{created['account_case_id']}/reply",
                json={"message": "The balance was exhausted, and we topped up."},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(extractor.call_count, 2)
        self.assertEqual(payload["category"], "account_billing")
        self.assertEqual(payload["subcategory"], "account_suspension")
        self.assertEqual(payload["route_status"], "not_automated")
        self.assertEqual(payload["automation_status"], "not_automated")
        self.assertEqual(payload["collected_fields"]["known_reason"], "balance")
        self.assertEqual(payload["collected_fields"]["customer_actions_taken"], "topped up")
        self.assertEqual(payload["internal_email_send_status"], "not_applicable")
        self.assertIsNone(payload["automation_handler"])
        self.assertIsNone(payload["ai_reply_status"])

    def test_account_suspension_automation_requires_confirmation_before_handoff_and_closes_after_reply(self) -> None:
        extraction = AccountSuspensionFieldExtraction(
            status="partial",
            collected_fields={"suspension_status_or_error": "account suspended"},
            grounding_status="passed",
        )
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main, "decide_account_route", return_value=_active_account_suspension_route_result()
        ), patch.object(
            main, "extract_account_suspension_fields", return_value=extraction
        ), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_email:
            created = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended and I cannot log in.",
                    "customer_email": "customer@example.com",
                },
            ).json()

            self.assertEqual(created["status"], "automation")
            self.assertEqual(created["category"], "account_billing")
            self.assertEqual(created["subcategory"], "account_suspension")
            self.assertEqual(created["route_status"], "automated")
            self.assertEqual(created["automation_handler"], "account_suspension")
            self.assertEqual(created["internal_email_send_status"], "not_applicable")

            first_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
            self.assertIsNotNone(first_job)
            assert first_job is not None
            self.assertEqual(
                first_job["payload"]["reply_intent"],
                "account_suspension_contact_confirmation_request",
            )
            self.assertNotIn("close_after_publish", first_job["payload"])
            self._publish_latest_account_reply(created["ticket_id"])
            first_reply = self.repository.get_ticket(created["ticket_id"])["messages"][-1]["content"]
            self.assertIn("which email", first_reply.lower())
            self.assertIn("ticket", first_reply.lower())
            self.assertIn("24 hours", first_reply.lower())
            self.assertIn("close", first_reply.lower())
            self.assertIn("reopen", first_reply.lower())
            self.assertNotIn("Support Engineer", first_reply)

            confirmed = self.client.post(
                f"/api/account/cases/{created['account_case_id']}/reply",
                json={"message": "Yes, please use the email address on this ticket."},
            )
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            confirmed_payload = confirmed.json()
            self.assertEqual(confirmed_payload["internal_email_send_status"], "sent")
            self.assertEqual(confirmed_payload["support_ticket_status"], "open")
            send_email.assert_called_once()

            closing_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
            self.assertIsNotNone(closing_job)
            assert closing_job is not None
            self.assertEqual(
                closing_job["payload"]["reply_intent"],
                "account_suspension_handoff_and_close",
            )
            self.assertTrue(closing_job["payload"]["close_after_publish"])
            workflow = self.repository.get_account_case(created["account_case_id"])[
                "automation_context"
            ]["account_suspension_contact_workflow"]
            self.assertEqual(workflow["state"], "closing_reply_pending")

            self._publish_latest_account_reply(created["ticket_id"])
            ticket = self.repository.get_ticket(created["ticket_id"])
            self.assertEqual(ticket["status"], "resolved")
            closing_reply = ticket["messages"][-1]["content"]
            self.assertIn("24 hours", closing_reply.lower())
            self.assertIn("closing", closing_reply.lower())
            self.assertIn("reopen", closing_reply.lower())
            self.assertNotIn("Support Engineer", closing_reply)
            workflow = self.repository.get_account_case(created["account_case_id"])[
                "automation_context"
            ]["account_suspension_contact_workflow"]
            self.assertEqual(workflow["state"], "closed")

    def test_billing_tickets_detail_by_canonical_ticket_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Canonical lookup test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                },
            )
        ticket_id = create_response.json()["ticket_id"]

        response = self.client.get(f"/api/account/billing-tickets/{ticket_id}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["ticket_id"], ticket_id)
        self.assertEqual(detail["title"], "Canonical lookup test")

    def test_billing_tickets_detail_by_canonical_ticket_id_is_not_limited_to_recent_items(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-OLD-001",
                "client_ticket_id": "TK-OLD-001",
                "source": "manual",
                "title": "Old canonical ticket",
                "question": "old question",
                "automation_status": "not_automated",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
        for i in range(205):
            self.repository.save_billing_ticket(
                {
                    "billing_ticket_id": f"BT-TK-NEW-{i:03d}",
                    "client_ticket_id": f"TK-NEW-{i:03d}",
                    "source": "manual",
                    "title": f"New ticket {i}",
                    "question": "new question",
                    "automation_status": "not_automated",
                    "created_at": f"2026-02-01T00:{i % 60:02d}:00+00:00",
                }
            )

        response = self.client.get("/api/account/billing-tickets/TK-OLD-001")
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertEqual(detail["ticket_id"], "TK-OLD-001")
        self.assertEqual(detail["title"], "Old canonical ticket")

    def test_billing_tickets_detail_api_404(self) -> None:
        response = self.client.get("/api/account/billing-tickets/BT-nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_account_intake_http_link_source_creates_ticket_with_api_normalization(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "HTTP link test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"Link": "https://example.com/case/1"},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        ticket_id = payload["ticket_id"]

        ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        event_payloads = [
            item["payload"]
            for item in self.repository.list_ticket_events(ticket_id)
            if item["event_type"] == "ticket_created"
        ]
        self.assertTrue(event_payloads)
        self.assertEqual(event_payloads[0]["source"], "api")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertIn("Link", bt["source"])
        self.assertEqual(bt["source"], '{"Link": "https://example.com/case/1"}')

    def test_http_link_source_detail_returns_object(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LINK-001",
                "client_ticket_id": "TK-LINK-001",
                "source": '{"Link": "https://example.com/case/1"}',
                "title": "Link source ticket",
                "question": "link question",
                "automation_status": "automation",
            }
        )

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-LINK-001")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], "https://example.com/case/1")

        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        link_items = [t for t in list_payload["tickets"] if t.get("billing_ticket_id") == "BT-TK-LINK-001"]
        self.assertEqual(len(link_items), 1)
        self.assertIsInstance(link_items[0]["source"], dict)
        self.assertEqual(link_items[0]["source"]["Link"], "https://example.com/case/1")

    def test_manual_source_still_returns_manual_string(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Manual test",
                    "question": "A question",
                    "source": "manual",
                },
            )

        self.assertEqual(response.status_code, 200)
        bt_id = response.json()["billing_ticket_id"]
        bt = self.repository.get_billing_ticket(bt_id)
        self.assertEqual(bt["source"], "manual")

        detail = self.client.get(f"/api/account/billing-tickets/{bt_id}").json()
        self.assertEqual(detail["source"], "manual")

    def test_default_source_returns_manual(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Default source test",
                    "question": "A question",
                },
            )

        self.assertEqual(response.status_code, 200)
        bt_id = response.json()["billing_ticket_id"]
        bt = self.repository.get_billing_ticket(bt_id)
        self.assertEqual(bt["source"], "manual")

    def test_http_link_source_strips_extra_source_fields_from_view_model(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LINK-EXTRA-001",
                "client_ticket_id": "TK-LINK-EXTRA-001",
                "source": '{"Link": "https://example.com/case/1", "token": "secret"}',
                "title": "Link source ticket",
                "question": "link question",
                "automation_status": "automation",
            }
        )

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-LINK-EXTRA-001")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["source"], {"Link": "https://example.com/case/1"})

    def test_non_http_link_source_is_not_saved_as_clickable_source(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Unsafe link test",
                    "question": "A question",
                    "source": {"Link": "javascript:alert(1)"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "manual")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], "manual")

    # --- New tests for billing automation reply flow ---

    def test_account_intake_schedules_customer_reply_before_publishing_assistant_message(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "automation")
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["ai_reply_status"], "queued")

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        messages = ticket["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "customer")
        self._publish_latest_account_reply(payload["ticket_id"])
        ticket = self.repository.get_ticket(payload["ticket_id"])
        assert ticket is not None
        self.assertEqual(ticket["messages"][1]["role"], "assistant")
        self.assertEqual(ticket["messages"][1]["source"], "account_ai")
        self.assertIn("app id", ticket["messages"][1]["content"].lower())

    def test_account_intake_sends_internal_email_via_async_to_thread(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="account_verification",
            confidence=0.93,
            reason="account verification",
            matched_signals=["company verification"],
            response_language="en",
            semantic_intent="billing.account_verification",
            automation_eligibility="eligible",
            policy_decision="policy_gate",
            risk_flags=[],
            evidence_spans=[],
            router_source="llm_semantic",
        )
        threaded_functions = []

        async def fake_async_to_thread(func, *args, **kwargs):
            threaded_functions.append(func)
            return func(*args, **kwargs)

        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main,
            "decide_support_route",
            return_value=decision,
        ), patch.object(
            main,
            "async_to_thread",
            side_effect=fake_async_to_thread,
        ), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_mock:
            response = self.client.post(
                "/account",
                json={
                    "title": "Account verification",
                    "question": (
                        "Account type: Enterprise. My name is Taylor. "
                        "Office address: 1 Example Street, Singapore. "
                        "Contact number: +65-1234-5678. Contact email: taylor@example.com. "
                        "Use case: internal video calls. Console configuration: RTC project."
                    ),
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["internal_email_send_status"], "sent")
        send_mock.assert_called_once()
        self.assertIn(send_mock, threaded_functions)

    def test_account_intake_missing_fields_persisted(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended. I cannot log in.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "automation")
        self.assertEqual(payload["subcategory"], "account_suspension")
        self.assertEqual(payload["route_status"], "automated")
        self.assertEqual(payload["automation_handler"], "account_suspension")
        self.assertEqual(payload["missing_fields"], [])
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["ai_reply_status"], "queued")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["missing_fields"], [])
        self.assertIsNone(bt["customer_reply"])
        job = self.repository.get_latest_account_reply_job(payload["ticket_id"])
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(
            job["payload"]["reply_intent"],
            "account_suspension_contact_confirmation_request",
        )
        workflow = bt["automation_context"]["account_suspension_contact_workflow"]
        self.assertEqual(workflow["state"], "awaiting_contact_confirmation")

    def test_account_verification_missing_use_case_uses_customer_name_email_style(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="account_verification",
            confidence=0.93,
            reason="account verification",
            matched_signals=["company verification"],
            response_language="en",
            semantic_intent="billing.account_verification",
            automation_eligibility="eligible",
            policy_decision="policy_gate",
            risk_flags=[],
            evidence_spans=[],
            router_source="llm_semantic",
        )

        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main,
            "decide_support_route",
            return_value=decision,
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Account verification",
                    "question": (
                        "Account type: Enterprise. My name is Taylor. "
                        "Office address: 1 Example Street, Singapore. "
                        "Contact number: +65-1234-5678. Contact email: taylor@example.com. "
                        "Console configuration: basic RTC project."
                    ),
                    "customer_email": "taylor@example.com",
                    "customer_name": "Taylor",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["route"], "fraud_account")
        self.assertEqual(payload["missing_fields"], ["use_case_description"])
        job = self.repository.get_latest_account_reply_job(payload["ticket_id"])
        assert job is not None
        reply_facts = job["payload"]["reply_facts"]
        self.assertEqual(reply_facts["customer_first_name"], "Taylor")
        self.assertEqual(reply_facts["missing_information"], ["use_case_description"])
        self._publish_latest_account_reply(payload["ticket_id"])
        ticket = self.repository.get_ticket(payload["ticket_id"])
        assert ticket is not None
        draft = ticket["messages"][-1]["content"]
        self.assertTrue(draft.startswith("Hi Taylor,"))
        self.assertIn("use-case description", draft.lower())

    def test_account_verification_second_incomplete_reply_sends_internal_email_without_reasking(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="account_verification",
            confidence=0.93,
            reason="account verification",
            matched_signals=["company verification"],
            response_language="en",
            semantic_intent="billing.account_verification",
            automation_eligibility="eligible",
            policy_decision="policy_gate",
            risk_flags=[],
            evidence_spans=[],
            router_source="llm_semantic",
        )
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main,
            "decide_account_route",
            return_value=_fraud_account_route_result(),
        ), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_mock:
            created = self.client.post(
                "/account",
                json={
                    "title": "Account verification",
                    "question": "Please help verify our account.",
                    "customer_email": "Taylor",
                },
            ).json()
            first_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
            assert first_job is not None
            created_case = self.repository.get_account_case(created["account_case_id"])
            assert created_case is not None
            self.assertEqual(created_case["automation_context"]["follow_up_count"], 0)
            self.assertTrue(created_case["automation_context"]["follow_up_scheduled"])
            self.assertEqual(
                set(first_job["payload"]["asked_field_keys"]),
                {"account_type", "name", "office_address", "contact_number", "contact_email", "use_case_description", "console_configuration"},
            )
            self._publish_latest_account_reply(created["ticket_id"])
            ticket = self.repository.get_ticket(created["ticket_id"])
            assert ticket is not None
            first_reply = ticket["messages"][-1]
            first_reply_meta = first_reply.get("meta")
            assert isinstance(first_reply_meta, dict)
            first_reply["asked_field_keys"] = first_reply_meta.pop("asked_field_keys")
            self.repository.save_ticket(ticket)

            response = self.client.post(
                f"/api/account/cases/{created['account_case_id']}/reply",
                json={"message": "I do not have any more information to add."},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["internal_email_send_status"], "sent")
        self.assertEqual(payload["route_status"], "automated")
        self.assertEqual(payload["automation_context"]["follow_up_count"], 1)
        self.assertTrue(payload["automation_context"]["proceed_with_missing_fields"])
        send_mock.assert_called_once()
        email_body = send_mock.call_args.args[0]["body"]
        self.assertIn("Missing after one follow-up", email_body)
        latest_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert latest_job is not None
        self.assertNotEqual(latest_job["job_id"], first_job["job_id"])
        self.assertEqual(latest_job["payload"]["asked_field_keys"], [])

    def test_billing_automation_reply_recomputes_fields(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main, "decide_account_route", return_value=_fraud_account_route_result()
        ):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Suspicious activity verification",
                    "question": (
                        "The fraud review asks us to submit company, contact, use case, and payment "
                        "information to verify and restore our account."
                    ),
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200)
        create_payload = create_response.json()
        bt_id = create_payload["billing_ticket_id"]

        # Initial state: missing fields exist and the Persona reply is queued for preparation.
        self.assertTrue(len(create_payload["missing_fields"]) > 0)
        self.assertEqual(create_payload["ai_reply_status"], "queued")

        ticket = self.repository.get_ticket(create_payload["ticket_id"])
        self.assertEqual(len(ticket["messages"]), 1)

        saved_new_message_counts: list[int] = []
        original_save_ticket = self.repository.save_ticket

        def save_ticket_spy(ticket_data, new_messages=None):
            saved_new_message_counts.append(len(new_messages or []))
            return original_save_ticket(ticket_data, new_messages=new_messages)

        self.repository.save_ticket = save_ticket_spy  # type: ignore[method-assign]

        # Reply with field info.
        with patch.object(
            main,
            "_send_billing_internal_email_attempt",
            AsyncMock(return_value=("skipped_config_missing", "mail disabled in unit test")),
        ):
            reply_response = self.client.post(
                f"/api/account/billing-tickets/{bt_id}/reply",
                json={
                    "message": (
                        "Account type: Enterprise. My name is Taylor. "
                        "Office address: 1 Main Street, Singapore. "
                        "Contact number: +65-12345678. Email: taylor@example.com. "
                        "Use case: live streaming. Console configuration: RTC project setup."
                    ),
                },
            )

        self.assertEqual(reply_response.status_code, 200, reply_response.text)
        reply_payload = reply_response.json()
        self.assertEqual(reply_payload["status"], "human_review_required")
        self.assertEqual(reply_payload["automation_status"], "human_review_required")
        self.assertEqual(reply_payload["route_status"], "not_automated")
        self.assertEqual(reply_payload["missing_fields"], [])
        self.assertEqual(reply_payload["customer_reply"], None)
        self.assertIsNone(reply_payload["ai_reply_status"])

        # Only the customer message is visible until the scheduled publication.
        self.assertEqual(saved_new_message_counts, [1])

        ticket = self.repository.get_ticket(create_payload["ticket_id"])
        self.assertEqual(len(ticket["messages"]), 2)
        self.assertEqual(ticket["messages"][1]["role"], "customer")
        # Check billing ticket was updated.
        bt = self.repository.get_billing_ticket(bt_id)
        self.assertEqual(bt["missing_fields"], [])
        self.assertIn("account_type", bt["collected_fields"])

    def test_fraud_followup_with_missing_fields_queues_missing_info_reply(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main, "decide_account_route", return_value=_fraud_account_route_result()
        ):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Suspicious activity verification",
                    "question": (
                        "The fraud review asks us to submit company, contact, use case, and payment "
                        "information to verify and restore our account."
                    ),
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200)
        create_payload = create_response.json()
        bt_id = create_payload["billing_ticket_id"]
        self.assertTrue(len(create_payload["missing_fields"]) > 0)

        # Partial answer: company only, so fraud fields are still incomplete.
        def _gate_with_ownership(account_case, timestamp):
            context = dict(account_case.get("automation_context") or {})
            context["zendesk_ownership"] = {"state": "assigned", "assignee_id": "48557297720084"}
            account_case["automation_context"] = context
            return True

        with patch.object(
            main, "decide_account_route", return_value=_fraud_account_route_result()
        ), patch.object(
            main, "_apply_production_ownership_gate", side_effect=_gate_with_ownership
        ):
            reply_response = self.client.post(
                f"/api/account/billing-tickets/{bt_id}/reply",
                json={"message": "Company name: Acme Corp."},
            )

        self.assertEqual(reply_response.status_code, 200, reply_response.text)
        reply_payload = reply_response.json()
        # The ownership gate result must survive the automation attempt merge.
        stored_case = self.repository.get_account_case(bt_id)
        assert stored_case is not None
        self.assertEqual(
            (stored_case.get("automation_context") or {}).get("zendesk_ownership", {}).get("state"),
            "assigned",
        )
        # The missing-information ask must queue instead of failing on the
        # fraud handoff intent conflict.
        self.assertEqual(reply_payload["automation_status"], "automation")
        self.assertEqual(reply_payload["ai_reply_status"], "queued")
        self.assertTrue(len(reply_payload["missing_fields"]) > 0)
        job = self.repository.get_latest_account_reply_job(create_payload["ticket_id"])
        assert job is not None
        facts = job["payload"].get("reply_facts") or {}
        self.assertEqual(facts.get("reply_intent"), "request_missing_information")
        # The canonical intent is the missing-information ask, not the fraud
        # handoff confirmation, so no intent conflict and no close flag.
        self.assertEqual(job["payload"].get("reply_intent"), "request_missing_information")
        self.assertNotIn("close_after_publish", job["payload"])
        self.assertTrue(len(job["payload"]["asked_field_keys"]) > 0)

    def test_billing_automation_reply_sends_internal_email_when_fields_complete(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main, "decide_account_route", return_value=_fraud_account_route_result()
        ):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Suspicious activity verification",
                    "question": (
                        "The fraud review asks us to submit company, contact, use case, and payment "
                        "information to verify and restore our account."
                    ),
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200, create_response.text)
        create_payload = create_response.json()
        bt_id = create_payload["billing_ticket_id"]

        captured_payloads: list[dict[str, str]] = []

        def fake_send(payload: dict[str, str]) -> dict[str, str]:
            captured_payloads.append(payload)
            return {"status": "sent", "reason": ""}

        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            side_effect=fake_send,
        ):
            reply_response = self.client.post(
                f"/api/account/billing-tickets/{bt_id}/reply",
                json={
                    "message": (
                        "Account type: Enterprise. My name is Taylor. "
                        "Office address: 1 Main Street, Singapore. "
                        "Contact number: +65-12345678. Email: taylor@example.com. "
                        "Use case: live streaming. Console configuration: RTC project setup."
                    ),
                },
            )

        self.assertEqual(reply_response.status_code, 200, reply_response.text)
        reply_payload = reply_response.json()
        self.assertEqual(reply_payload["missing_fields"], [])
        self.assertEqual(reply_payload["internal_email_send_status"], "sent")
        self.assertEqual(reply_payload["internal_email_send_reason"], "")
        self.assertTrue(captured_payloads)
        self.assertEqual(captured_payloads[0]["to"], "xieziling@agora.io")
        self.assertIn("reply directly to this email", captured_payloads[0]["body"])
        self.assertNotIn("/response?token=", captured_payloads[0]["body"])

        bt = self.repository.get_billing_ticket(bt_id)
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["internal_email_send_status"], "sent")
        self.assertNotIn("/response?token=", bt["internal_email_payload"]["body"])

    def test_billing_automation_reply_records_outlook_email_failure_without_response_token(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main, "decide_account_route", return_value=_fraud_account_route_result()
        ):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Suspicious activity verification",
                    "question": (
                        "The fraud review asks us to submit company, contact, use case, and payment "
                        "information to verify and restore our account."
                    ),
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200, create_response.text)
        bt_id = create_response.json()["billing_ticket_id"]
        captured_payloads: list[dict[str, str]] = []

        def fake_send(payload: dict[str, str]) -> dict[str, str]:
            captured_payloads.append(payload)
            return {"status": "failed", "reason": "smtp down"}

        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            side_effect=fake_send,
        ):
            reply_response = self.client.post(
                f"/api/account/billing-tickets/{bt_id}/reply",
                json={
                    "message": (
                        "Account type: Enterprise. My name is Taylor. "
                        "Office address: 1 Main Street, Singapore. "
                        "Contact number: +65-12345678. Email: taylor@example.com. "
                        "Use case: live streaming. Console configuration: RTC project setup."
                    ),
                },
            )

        self.assertEqual(reply_response.status_code, 200, reply_response.text)
        reply_payload = reply_response.json()
        self.assertEqual(reply_payload["internal_email_send_status"], "failed")
        self.assertEqual(reply_payload["internal_email_send_reason"], "smtp down")
        self.assertTrue(captured_payloads)
        self.assertIn("reply directly to this email", captured_payloads[0]["body"])
        self.assertNotIn("/response?token=", captured_payloads[0]["body"])

        bt = self.repository.get_billing_ticket(bt_id)
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["internal_email_send_status"], "failed")
        self.assertEqual(bt["internal_email_send_reason"], "smtp down")
        self.assertNotIn("/response?token=", bt["internal_email_payload"]["body"])

    def test_billing_automation_reply_404(self) -> None:
        response = self.client.post(
            "/api/account/billing-tickets/BT-nonexistent/reply",
            json={"message": "Hello"},
        )
        self.assertEqual(response.status_code, 404)

    def test_billing_detail_includes_canonical_messages(self) -> None:
        create_payload, _ = self._create_invoice_ticket_with_response_token()
        bt_id = create_payload["billing_ticket_id"]
        detail = self.client.get(f"/api/account/billing-tickets/{bt_id}").json()
        self.assertIn("messages", detail)
        self.assertEqual(len(detail["messages"]), 1)
        self.assertEqual(detail["messages"][0]["role"], "customer")
        self.assertEqual(detail["customer_id"], "customer@example.com")
        self.assertEqual(detail["requester"], "customer@example.com")
        self.assertIn("support_ticket_status", detail)
        self.assertEqual(detail["collected_fields"]["transaction_id"], "1104245232004173824")

    def test_non_automated_ticket_remains_not_automated(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "General FAQ question",
                    "question": "What is Agora?",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200)
        payload = create_response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["missing_fields"], [])

    # --- N8n-style source link tests ---

    def test_n8n_plain_zendesk_url_source_normalizes_and_saves_link(self) -> None:
        zendesk_url = "https://xxx.zendesk.com/agent/tickets/123"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n plain URL test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": zendesk_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        ticket_id = payload["ticket_id"]

        # Canonical ticket source must be "api".
        ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        # Event source must be "api".
        event_payloads = [
            item["payload"]
            for item in self.repository.list_ticket_events(ticket_id)
            if item["event_type"] == "ticket_created"
        ]
        self.assertTrue(event_payloads)
        self.assertEqual(event_payloads[0]["source"], "api")

        # Billing ticket source must be saved as JSON with Link.
        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertIn("Link", bt["source"])
        self.assertIn(zendesk_url, bt["source"])

        # List API returns source as object with Link.
        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_data = list_response.json()
        match = [t for t in list_data["tickets"] if t.get("billing_ticket_id") == payload["billing_ticket_id"]]
        self.assertEqual(len(match), 1)
        self.assertIsInstance(match[0]["source"], dict)
        self.assertEqual(match[0]["source"]["Link"], zendesk_url)

        # Detail API returns source as object with Link.
        detail_response = self.client.get(f"/api/account/billing-tickets/{payload['billing_ticket_id']}")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], zendesk_url)

        # Detail API returns customer_id/requester == customer_email.
        self.assertEqual(detail["customer_id"], "customer@example.com")
        self.assertEqual(detail["requester"], "customer@example.com")

    def test_n8n_source_dict_with_link_key_saves_and_returns_link(self) -> None:
        zendesk_url = "https://xxx.zendesk.com/agent/tickets/456"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n dict link test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"link": zendesk_url},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertIn("Link", bt["source"])
        self.assertIn(zendesk_url, bt["source"])

        detail = self.client.get(f"/api/account/billing-tickets/{payload['billing_ticket_id']}").json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], zendesk_url)

    def test_n8n_source_dict_with_url_key_saves_and_returns_link(self) -> None:
        zendesk_url = "https://xxx.zendesk.com/agent/tickets/789"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n dict url test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"url": zendesk_url},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertIn(zendesk_url, bt["source"])

        detail = self.client.get(f"/api/account/billing-tickets/{payload['billing_ticket_id']}").json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], zendesk_url)


    def test_legacy_raw_url_billing_ticket_source_returns_link_object(self) -> None:
        zendesk_url = "https://xxx.zendesk.com/agent/tickets/999"
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-RAW-URL-001",
                "client_ticket_id": "TK-RAW-URL-001",
                "source": zendesk_url,
                "title": "Raw URL source ticket",
                "question": "raw url question",
                "automation_status": "automation",
            }
        )

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-RAW-URL-001")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["source"], {"Link": zendesk_url})

        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        link_items = [t for t in list_payload["tickets"] if t.get("billing_ticket_id") == "BT-TK-RAW-URL-001"]
        self.assertEqual(len(link_items), 1)
        self.assertEqual(link_items[0]["source"], {"Link": zendesk_url})

    def test_n8n_javascript_url_source_is_not_saved_as_clickable(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n unsafe URL test",
                    "question": "A question",
                    "source": "javascript:alert(1)",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "manual")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], "manual")

    def test_n8n_empty_string_source_still_manual(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n empty source test",
                    "question": "A question",
                    "source": "",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "manual")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], "manual")

    def test_n8n_overlong_url_source_is_not_saved_as_clickable(self) -> None:
        long_url = "https://example.com/" + ("x" * 2000)
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n long URL test",
                    "question": "A question",
                    "source": long_url,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "manual")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], "manual")

    # --- Zendesk API URL normalization tests ---

    def test_zendesk_api_url_normalized_to_agent_url_on_create(self) -> None:
        """N8n plain source with /api/v2/tickets/{n}.json → persisted as /agent/tickets/{n}."""
        api_url = "https://agoraio.zendesk.com/api/v2/tickets/11816.json"
        expected = "https://agoraio.zendesk.com/agent/tickets/11816"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Zendesk API URL test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": api_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], '{"Link": "https://agoraio.zendesk.com/agent/tickets/11816"}')

        detail = self.client.get(f"/api/account/billing-tickets/{payload['billing_ticket_id']}").json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], expected)

    def test_zendesk_api_url_dict_source_normalized_to_agent_url(self) -> None:
        """N8n dict source with /api/v2/tickets/{n}.json → persisted as /agent/tickets/{n}."""
        api_url = "https://subdomain.zendesk.com/api/v2/tickets/42.json"
        expected = "https://subdomain.zendesk.com/agent/tickets/42"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Zendesk dict API URL test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"link": api_url},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], '{"Link": "https://subdomain.zendesk.com/agent/tickets/42"}')

        # Also test url key variant.
        with patch.object(main, "dispatch_event", AsyncMock()):
            response2 = self.client.post(
                "/account",
                json={
                    "title": "Zendesk dict url key test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"url": api_url},
                },
            )

        self.assertEqual(response2.status_code, 200)
        bt2 = self.repository.get_billing_ticket(response2.json()["billing_ticket_id"])
        self.assertIsNotNone(bt2)
        assert bt2 is not None
        self.assertEqual(bt2["source"], '{"Link": "https://subdomain.zendesk.com/agent/tickets/42"}')

    def test_zendesk_agent_url_preserved_as_is(self) -> None:
        """Already /agent/tickets/{n} URLs are kept unchanged."""
        agent_url = "https://agoraio.zendesk.com/agent/tickets/11816"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Zendesk agent URL test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": agent_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], '{"Link": "https://agoraio.zendesk.com/agent/tickets/11816"}')

    def test_legacy_zendesk_api_url_normalized_in_view_model(self) -> None:
        """Historical billing ticket with raw API URL → list/detail returns agent URL."""
        api_url = "https://agoraio.zendesk.com/api/v2/tickets/11816.json"
        expected = "https://agoraio.zendesk.com/agent/tickets/11816"
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-ZEN-LEGACY-001",
                "client_ticket_id": "TK-ZEN-LEGACY-001",
                "source": '{"Link": "https://agoraio.zendesk.com/api/v2/tickets/11816.json"}',
                "title": "Legacy Zendesk API URL ticket",
                "question": "legacy question",
                "automation_status": "automation",
            }
        )

        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        match = [t for t in list_payload["tickets"] if t.get("billing_ticket_id") == "BT-TK-ZEN-LEGACY-001"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["source"]["Link"], expected)

        detail = self.client.get("/api/account/billing-tickets/BT-TK-ZEN-LEGACY-001").json()
        self.assertEqual(detail["source"]["Link"], expected)

    def test_non_zendesk_url_unchanged(self) -> None:
        """Non-Zendesk safe URLs are returned as-is."""
        non_zendesk_url = "https://example.com/case/42"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Non-Zendesk URL test",
                    "question": "A question",
                    "customer_email": "customer@example.com",
                    "source": non_zendesk_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], '{"Link": "https://example.com/case/42"}')

    def test_staging_account_reply_job_is_immediately_due(self) -> None:
        with patch.dict(os.environ):
            os.environ.pop("ACCOUNT_DEFAULT_PROCESSING_PROFILE", None)
            with patch.object(main, "dispatch_event", AsyncMock()):
                response = self.client.post(
                    "/account",
                    json={
                        "title": "Enable Media Relay Feature",
                        "question": "Please enable Media Relay from your end.",
                        "customer_email": "customer@example.com",
                    },
                )

        payload = response.json()
        job = self.repository.get_latest_account_reply_job(payload["ticket_id"])
        assert job is not None
        created_at = datetime.fromisoformat(job["created_at"])
        scheduled = datetime.fromisoformat(job["scheduled_for"])
        self.assertEqual((scheduled - created_at).total_seconds(), 0)
        self.assertEqual(payload["ai_reply_scheduled_for"], job["scheduled_for"])

    def test_production_account_reply_delay_is_sampled_once(self) -> None:
        with patch.dict(os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"}), patch.object(
            main, "dispatch_event", AsyncMock()
        ), patch.object(
            main, "_apply_production_ownership_gate", return_value=True
        ), patch.object(
            main, "account_reply_delay_seconds_for_profile", return_value=417
        ) as delay:
            response = self.client.post(
                "/account",
                json={
                    "external_id": "99887769",
                    "title": "Enable Media Relay Feature",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            )

        payload = response.json()
        job = self.repository.get_latest_account_reply_job(payload["ticket_id"])
        assert job is not None
        created_at = datetime.fromisoformat(job["created_at"])
        scheduled = datetime.fromisoformat(job["scheduled_for"])
        self.assertEqual((scheduled - created_at).total_seconds(), 417)
        self.assertEqual(payload["ai_reply_scheduled_for"], job["scheduled_for"])
        delay.assert_called_once_with("production")

    def test_account_intake_identity_uses_name_value_and_source_from_same_field(self) -> None:
        for field_name in ("customer_name", "cx_name", "cx name", "cxName", "requester_name"):
            with self.subTest(field_name=field_name):
                payload = {
                    "title": "Enablement",
                    "question": "Please enable the feature.",
                    field_name: "Jack Gold",
                }
                request = main.AccountIntakeRequest.model_validate(payload)
                identity = main._account_intake_identity(request, payload=payload, ticket_id="TK-NAME")
                self.assertEqual(identity.customer_name, "Jack Gold")
                self.assertEqual(identity.customer_name_source, field_name)

        payload = {
            "title": "Enablement",
            "question": "Please enable the feature.",
            "customer_name": "",
            "cx_name": "Alias Winner",
            "requester": {"name": "Nested Loser"},
        }
        request = main.AccountIntakeRequest.model_validate(payload)
        identity = main._account_intake_identity(request, payload=payload, ticket_id="TK-PRECEDENCE")
        self.assertEqual(identity.customer_name, "Alias Winner")
        self.assertEqual(identity.customer_name_source, "cx_name")

        for parent_key in ("requester", "customer"):
            with self.subTest(parent_key=parent_key):
                nested_payload = {
                    "title": "Enablement",
                    "question": "Please enable the feature.",
                    parent_key: {"display_name": "Nested Name"},
                }
                nested_request = main.AccountIntakeRequest.model_validate(nested_payload)
                nested_identity = main._account_intake_identity(
                    nested_request,
                    payload=nested_payload,
                    ticket_id="TK-NESTED",
                )
                self.assertEqual(nested_identity.customer_name, "Nested Name")
                self.assertEqual(nested_identity.customer_name_source, f"{parent_key}.display_name")

    def test_account_intake_normalizes_current_n8n_identity_and_redacts_audit_values(self) -> None:
        source_url = "https://agoraio.zendesk.com/api/v2/tickets/12598.json"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "General support question",
                    "question": "Can someone explain this product behavior?",
                    "customer_email": " Customer@Example.COM ",
                    "source": source_url,
                    "customer_name": "Jack Gold",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "12598")
        self.assertEqual(payload["customer_name"], "Jack Gold")
        ticket = self.repository.get_ticket("12598")
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["customer_id"], "customer@example.com")
        self.assertEqual(ticket["requester"], "customer@example.com")
        self.assertEqual(ticket["source"], "api")
        account_case = self.repository.get_account_case("AC-12598")
        self.assertIsNotNone(account_case)
        assert account_case is not None
        self.assertEqual(account_case["customer_name"], "Jack Gold")

        event = next(
            item["payload"]
            for item in self.repository.list_ticket_events("12598")
            if item["event_type"] == "ticket_created"
        )
        self.assertTrue(event["customer_name_present"])
        self.assertEqual(event["customer_name_source"], "customer_name")
        self.assertTrue(event["customer_email_present"])
        self.assertEqual(event["customer_email_source"], "customer_email")
        self.assertEqual(event["customer_email_status"], "valid")
        serialized_event = json.dumps(event)
        self.assertNotIn("Jack Gold", serialized_event)
        self.assertNotIn("customer@example.com", serialized_event)

    def test_account_intake_invalid_or_missing_email_uses_ticket_scoped_anonymous_identity(self) -> None:
        identities: list[str] = []
        for ticket_id, customer_email, expected_status in (
            ("TK-INVALID-EMAIL", "not an email", "invalid"),
            ("TK-MISSING-EMAIL", "", "missing"),
        ):
            with self.subTest(ticket_id=ticket_id), patch.object(main, "dispatch_event", AsyncMock()):
                response = self.client.post(
                    "/account",
                    json={
                        "ticket_id": ticket_id,
                        "title": "General support question",
                        "question": "Can someone explain this product behavior?",
                        "customer_email": customer_email,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                ticket = self.repository.get_ticket(ticket_id)
                self.assertIsNotNone(ticket)
                assert ticket is not None
                identities.append(str(ticket["customer_id"]))
                self.assertTrue(str(ticket["customer_id"]).startswith("account-intake:"))
                self.assertEqual(ticket["requester"], ticket["customer_id"])
                event = next(
                    item["payload"]
                    for item in self.repository.list_ticket_events(ticket_id)
                    if item["event_type"] == "ticket_created"
                )
                self.assertFalse(event["customer_email_present"])
                self.assertEqual(
                    event["customer_email_source"],
                    "customer_email" if expected_status == "invalid" else None,
                )
                self.assertEqual(event["customer_email_status"], expected_status)
                if customer_email:
                    self.assertNotIn(customer_email, json.dumps(event))

        self.assertNotEqual(identities[0], identities[1])

    def test_account_intake_rejects_overlong_nested_customer_name(self) -> None:
        response = self.client.post(
            "/account",
            json={
                "ticket_id": "TK-OVERLONG-NAME",
                "title": "General support question",
                "question": "Can someone explain this product behavior?",
                "requester": {"name": "x" * 161},
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"], "customer name must not exceed 160 characters")
        self.assertIsNone(self.repository.get_ticket("TK-OVERLONG-NAME"))

    def test_missing_fields_are_asked_only_once_per_ticket(self) -> None:
        cases = (
            (
                "Suspicious activity verification",
                (
                    "Our account has been suspended and we need help restoring it. "
                    "We are reaching out to resolve this."
                ),
                {
                    "account_type",
                    "name",
                    "office_address",
                    "contact_number",
                    "contact_email",
                    "use_case_description",
                    "console_configuration",
                },
            ),
            (
                "Feature enablement",
                "Please enable Media Relay from your end.",
                {"app_id"},
            ),
        )
        for title, question, expected_asked_fields in cases:
            route_patch = (
                patch.object(main, "decide_account_route", return_value=_fraud_account_route_result())
                if title == "Suspicious activity verification"
                else nullcontext()
            )
            email_patch = (
                patch(
                    "backend.main.send_billing_internal_email",
                    return_value={"status": "sent", "reason": ""},
                )
                if title == "Suspicious activity verification"
                else nullcontext()
            )
            with self.subTest(title=title), patch.object(main, "dispatch_event", AsyncMock()), route_patch, email_patch:
                created = self.client.post(
                    "/account",
                    json={
                        "title": title,
                        "question": question,
                        "customer_email": "customer@example.com",
                    },
                ).json()

                first_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
                assert first_job is not None
                first_job_id = first_job["job_id"]
                self.assertEqual(set(first_job["payload"]["asked_field_keys"]), expected_asked_fields)
                self._publish_latest_account_reply(created["ticket_id"])

                response = self.client.post(
                    f"/api/account/billing-tickets/{created['billing_ticket_id']}/reply",
                    json={"message": "Thank you for checking."},
                )
                self.assertEqual(response.status_code, 200, response.text)
                second_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
                assert second_job is not None
                if title == "Suspicious activity verification":
                    self.assertNotEqual(second_job["job_id"], first_job_id)
                    self.assertEqual(second_job["payload"]["asked_field_keys"], [])
                    self.assertEqual(response.json()["internal_email_send_status"], "sent")
                    self.assertEqual(response.json()["primary_label"], "Agora")
                    self.assertEqual(response.json()["secondary_label"], "Account & Billing / Fraud Account")
                else:
                    self.assertEqual(second_job["job_id"], first_job_id)
                    self.assertEqual(response.json()["primary_label"], "Conversation")
                    self.assertEqual(response.json()["secondary_label"], "Follow-up")

    def _create_enablement_case_with_pending_ask(self) -> dict[str, Any]:
        created = self.client.post(
            "/account",
            json={
                "title": "Feature enablement",
                "question": "Please enable Media Relay from your end.",
                "customer_email": "customer@example.com",
            },
        ).json()
        self._publish_latest_account_reply(created["ticket_id"])
        return created

    def test_unexpected_reply_answers_from_rag_fallback(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ):
            created = self._create_enablement_case_with_pending_ask()
            with patch.dict(os.environ, {"ACCOUNT_REPLY_RAG_FALLBACK_ENABLED": "true"}), patch(
                "backend.main.try_rag_fallback_answer",
                return_value=RagFallbackOutcome(
                    kind="answer",
                    answer="An App ID identifies your Agora project.",
                    references=("https://docs.agora.io/en/get-started",),
                ),
            ) as fallback:
                response = self.client.post(
                    f"/api/account/billing-tickets/{created['billing_ticket_id']}/reply",
                    json={"message": "what is appid?"},
                )
        self.assertEqual(response.status_code, 200, response.text)
        fallback.assert_called_once()
        self.assertIn("what is appid?", fallback.call_args.kwargs["question"])
        job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert job is not None
        # The RAG answer enters the persona pipeline as provided_answer facts;
        # the persona render voices the customer reply and the worker appends
        # the reference links deterministically before publication.
        facts = job["payload"]["reply_facts"]
        self.assertEqual(facts["provided_answer"], "An App ID identifies your Agora project.")
        self.assertEqual(facts["reply_intent"], "rag_fallback_answer")
        self.assertEqual(facts["behavior"], "rag_fallback_answer")
        self.assertEqual(facts["references"], ["https://docs.agora.io/en/get-started"])
        self.assertEqual(job["payload"]["reply_intent"], "rag_fallback_answer")
        self.assertEqual(job["payload"]["reply_pipeline"], "automation_persona_v8")

    def test_unexpected_reply_escalates_to_human_when_rag_cannot_answer(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ):
            created = self._create_enablement_case_with_pending_ask()
            with patch.dict(os.environ, {"ACCOUNT_REPLY_RAG_FALLBACK_ENABLED": "true"}), patch(
                "backend.main.try_rag_fallback_answer",
                return_value=RagFallbackOutcome(kind="escalate", reason="insufficient_evidence"),
            ):
                response = self.client.post(
                    f"/api/account/billing-tickets/{created['billing_ticket_id']}/reply",
                    json={"message": "what is appid?"},
                )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["automation_status"], "human_review_required")
        self.assertIn("reply_rag_fallback_escalation", payload["not_automated_reason"])

    def test_rag_fallback_publication_creates_delivery_ledger_for_rerouted_case(self) -> None:
        ticket_id = "TK-RAG-LEDGER"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "messages": [
                    {
                        "role": "customer",
                        "content": "what is appid?",
                        "created_at": "2026-08-23T00:00:00+00:00",
                    }
                ],
                "created_at": "2026-08-23T00:00:00+00:00",
                "updated_at": "2026-08-23T00:00:00+00:00",
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-RAG-LEDGER",
                "billing_ticket_id": "AC-RAG-LEDGER",
                "client_ticket_id": ticket_id,
                "processing_profile": "production",
                "zendesk_ticket_id": "12999",
                "route_family": "rag_product_support",
                "execution_action": "rag",
                "automation_status": "not_automated",
                "created_at": "2026-08-23T00:00:00+00:00",
            }
        )
        job = create_account_reply_job(
            self.repository,
            ticket_id=ticket_id,
            trigger_message_created_at="2026-08-23T00:00:00+00:00",
            created_at="2026-08-23T00:00:00+00:00",
            delay_seconds=0,
            draft_content="You can find the App ID on the Projects page in Agora Console.",
            reply_intent="rag_fallback_answer",
        )
        job["status"] = "publishing"
        self.repository.save_account_reply_job(job)
        with patch.object(worker, "_deliver_production_account_reply_to_zendesk"):
            worker._publish_account_reply_job(job)
        messages = self.repository.get_ticket(ticket_id)["messages"]
        assistant = [m for m in messages if m.get("role") == "assistant"]
        self.assertEqual(len(assistant), 1)
        claim = self.repository.claim_account_zendesk_comment_delivery(
            account_case_id="AC-RAG-LEDGER",
            message_id=str(assistant[0].get("message_id") or ""),
            claimed_at="2026-08-23T00:01:00+00:00",
        )
        self.assertNotEqual(claim.get("status"), "missing")

    def test_unexpected_reply_rag_fallback_disabled_keeps_legacy_silence(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ):
            created = self._create_enablement_case_with_pending_ask()
            first_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
            assert first_job is not None
            with patch.dict(os.environ, {"ACCOUNT_REPLY_RAG_FALLBACK_ENABLED": "false"}), patch(
                "backend.main.try_rag_fallback_answer"
            ) as fallback:
                response = self.client.post(
                    f"/api/account/billing-tickets/{created['billing_ticket_id']}/reply",
                    json={"message": "Thank you for checking."},
                )
        self.assertEqual(response.status_code, 200, response.text)
        fallback.assert_not_called()
        second_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert second_job is not None
        self.assertEqual(second_job["job_id"], first_job["job_id"])

    def test_new_customer_message_cancels_pending_reply(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ):
            created = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            ).json()
        first_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert first_job is not None

        with patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ):
            response = self.client.post(
                f"/api/account/billing-tickets/{created['billing_ticket_id']}/reply",
                json={"message": "My App ID is project.prod/eu-west#alpha."},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.repository.get_account_reply_job(first_job["job_id"])["status"], "cancelled")
        latest = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert latest is not None
        self.assertNotEqual(latest["job_id"], first_job["job_id"])

    def test_new_support_question_supersedes_active_automation_handler(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            ).json()

        response = self.client.post(
            f"/api/account/cases/{created['account_case_id']}/reply",
            json={"message": "How do I generate an Agora RTC token?"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        # A fresh technical request supersedes the prior billing handler.
        self.assertEqual(payload["primary_label"], "Agora")
        self.assertEqual(payload["secondary_label"], "Agora Technical")
        self.assertEqual(payload["automation_status"], "not_automated")
        self.assertIsNone(payload["ai_reply_status"])
        classification = payload["route_classification"]
        self.assertEqual(classification["superseded_automation_handler"], "enablement")
        self.assertEqual(classification["previous_handler_binding_status"], "superseded")

    def test_non_automated_ticket_only_records_route_labels(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Product question",
                    "question": "What Agora products are available?",
                    "customer_email": "customer@example.com",
                },
            ).json()
        job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        self.assertIsNone(job)
        self.assertEqual(created["primary_label"], "Human Review")
        self.assertEqual(created["secondary_label"], "Uncategorized")
        self.assertIsNone(created["ai_reply_status"])

    def test_account_reply_publication_is_idempotent_after_partial_worker_recovery(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            ).json()

        published = self._publish_latest_account_reply(created["ticket_id"])
        ticket = self.repository.get_ticket(created["ticket_id"])
        assert ticket is not None
        self.assertEqual(len(ticket["messages"]), 2)
        self.assertEqual(len(self.repository.list_account_reply_executions(created["ticket_id"])), 1)

        published["status"] = "publishing"
        self.repository.save_account_reply_job(published)
        worker._publish_account_reply_job(published)
        ticket = self.repository.get_ticket(created["ticket_id"])
        assert ticket is not None
        self.assertEqual(len(ticket["messages"]), 2)
        self.assertEqual(len(self.repository.list_account_reply_executions(created["ticket_id"])), 1)

    def test_account_reply_job_claims_once_after_due_time(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": "Please enable Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            ).json()
        job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert job is not None

        persona_pipeline = (job.get("payload") or {}).get("reply_pipeline") == worker.ACCOUNT_REPLY_PERSONA_PIPELINE
        from_status = worker.ACCOUNT_REPLY_PERSONA_V8_QUEUED if persona_pipeline else "scheduled"
        to_status = worker.ACCOUNT_REPLY_PERSONA_V8_PREPARING if persona_pipeline else "publishing"
        not_due = self.repository.claim_account_reply_jobs(
            from_status=from_status,
            to_status=to_status,
            now_value="2000-01-01T00:00:00+00:00",
            due_only=not persona_pipeline,
        )
        self.assertEqual(
            [item["job_id"] for item in not_due],
            [job["job_id"]] if persona_pipeline else [],
        )
        claimed = self.repository.claim_account_reply_jobs(
            from_status=from_status,
            to_status=to_status,
            now_value="2999-01-01T00:00:00+00:00",
            due_only=not persona_pipeline,
        )
        self.assertEqual(
            [item["job_id"] for item in claimed],
            [] if persona_pipeline else [job["job_id"]],
        )
        claimed_again = self.repository.claim_account_reply_jobs(
            from_status=from_status,
            to_status=to_status,
            now_value="2999-01-01T00:00:00+00:00",
            due_only=not persona_pipeline,
        )
        self.assertEqual(claimed_again, [])

    def test_account_reply_job_claims_are_isolated_by_pipeline_status(self) -> None:
        legacy_job = {
            "job_id": "account-reply-legacy-claim",
            "ticket_id": "TK-LEGACY-CLAIM",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": "queued",
            "scheduled_for": "2026-03-22T00:00:00+00:00",
            "payload": {"draft_content": "legacy"},
            "attempt_count": 0,
            "created_at": "2026-03-22T00:00:00+00:00",
            "updated_at": "2026-03-22T00:00:00+00:00",
        }
        persona_job = {
            "job_id": "account-reply-persona-claim",
            "ticket_id": "TK-PERSONA-CLAIM",
            "trigger_message_created_at": "2026-03-22T00:00:00+00:00",
            "status": worker.ACCOUNT_REPLY_PERSONA_QUEUED,
            "scheduled_for": "2026-03-22T00:00:00+00:00",
            "payload": {
                "draft_content": "",
                "reply_pipeline": worker.ACCOUNT_REPLY_PERSONA_PIPELINE,
            },
            "attempt_count": 0,
            "created_at": "2026-03-22T00:00:01+00:00",
            "updated_at": "2026-03-22T00:00:01+00:00",
        }
        self.repository.save_account_reply_job(legacy_job)
        self.repository.save_account_reply_job(persona_job)

        legacy_claim = self.repository.claim_account_reply_jobs(
            from_status="queued",
            to_status="preparing",
            now_value="2026-03-22T00:01:00+00:00",
        )
        self.assertEqual([item["job_id"] for item in legacy_claim], [legacy_job["job_id"]])

        persona_claim = self.repository.claim_account_reply_jobs(
            from_status=worker.ACCOUNT_REPLY_PERSONA_QUEUED,
            to_status=worker.ACCOUNT_REPLY_PERSONA_PREPARING,
            now_value="2026-03-22T00:01:00+00:00",
        )
        self.assertEqual([item["job_id"] for item in persona_claim], [persona_job["job_id"]])

        self.assertEqual(
            self.repository.claim_account_reply_jobs(
                from_status="queued",
                to_status="preparing",
                now_value="2026-03-22T00:01:00+00:00",
            ),
            [],
        )
        self.assertEqual(
            self.repository.claim_account_reply_jobs(
                from_status=worker.ACCOUNT_REPLY_PERSONA_QUEUED,
                to_status=worker.ACCOUNT_REPLY_PERSONA_PREPARING,
                now_value="2026-03-22T00:01:00+00:00",
            ),
            [],
        )
