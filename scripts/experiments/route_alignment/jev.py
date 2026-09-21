"""Direct, fail-closed TypeSafe Jev adapter for the route experiment."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from backend.services.hermes_route_classifier import (
    HermesRouteClassificationError,
    normalize_hermes_route_classification,
)

from .core import CandidateResult, CaseSnapshot
from .dataset import (
    MAX_REQUEST_BYTES,
    DatasetError,
    candidate_state,
    validate_candidate_request_size,
)


TYPESAFE_SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"
QUESTIONS_VERSION = "jev-account-route-v1"
ADAPTER_VERSION = "jev-direct-v1"
NORMALIZATION_POLICY_VERSION = "jev-route-normalization-v1"
CONFIDENCE_POLICY_VERSION = "jev-confidence-v1"
JEV_CLASSIFICATION_VERSION = "jev-route-aligned-v1"
CONFIDENCE_THRESHOLD = 0.7

_ADDITIONAL_INTENT_QUESTIONS = {
    "additional_technical": "technical",
    "additional_security_compliance": "security_compliance",
    "additional_account_billing": "account_billing",
    "additional_backend_operation": "backend_operation",
}


class JevAdapterError(ValueError):
    """A controlled request, response, or normalization failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class JevInput:
    """The already-redacted input shared with experiment candidates."""

    subject: str
    messages: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _Answer:
    choice: str
    probabilities: dict[str, float]
    confidence: float

    @property
    def winning_probability(self) -> float:
        return self.probabilities[self.choice]


def jev_input_from_snapshot(snapshot: CaseSnapshot) -> JevInput:
    """Build provider input without baseline labels or ticket identity."""
    state = candidate_state(snapshot)
    return JevInput(
        subject=str(state["subject"]),
        messages=tuple(dict(item) for item in state["messages"] if isinstance(item, Mapping)),
        metadata=dict(state["metadata"]),
    )


def _customer_fragments(value: JevInput) -> dict[str, str]:
    fragments: dict[str, str] = {}
    if value.subject.strip():
        fragments["customer_000"] = value.subject.strip()
    for item in value.messages:
        if str(item.get("role") or "").lower() != "user":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            fragments[f"customer_{len(fragments):03d}"] = content
    if len(fragments) > 254:
        raise JevAdapterError("input_too_large")
    return fragments


def _choice(instructions: str, criteria: Mapping[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def build_questions(value: JevInput) -> dict[str, dict[str, Any]]:
    """Return the complete versioned question set for one independent request."""
    fragments = _customer_fragments(value)
    yes_no_unknown = {
        "yes": "The current unresolved customer request contains this intent.",
        "no": "The current unresolved customer request does not contain this intent.",
        "uncertain": "The available text is insufficient or ambiguous.",
    }
    questions: dict[str, dict[str, Any]] = {
        "intent": _choice(
            "Classify the current unresolved customer request. Treat quoted examples and resolved historical mentions as context, not new requests.",
            {
                "conversation": "A conversational acknowledgement, thanks, closure, or follow-up without a new Agora support request.",
                "agora": "A technical, security/compliance, account/billing, or explicit backend-operation request about Agora.",
                "uncertain": "The current request is outside scope or cannot be determined safely.",
            },
        ),
        "conversation_action": _choice(
            "If the primary intent is conversation, choose the appropriate handling. Do not infer a previous assistant response.",
            {
                "resolve": "The customer only confirms resolution, thanks the team, or closes the issue.",
                "follow_up": "A response to an earlier assistant message is needed but no new support intent is introduced.",
                "human_review": "Conversation context is ambiguous or requires a human.",
            },
        ),
        "agora_route": _choice(
            "If the primary intent is Agora, choose one primary route based on the requested next action.",
            {
                "technical": "Product/API/SDK usage, diagnosis, integration, or technical guidance.",
                "security_compliance": "Security, privacy, compliance, DPA, questionnaire, or audit evidence.",
                "account_billing": "Invoices, charges, payment, fraud-account handling, or account suspension.",
                "backend_operation": "An explicit request for Agora staff to change or enable backend/account state.",
                "uncategorized": "No supported Agora route can be determined.",
            },
        ),
        "billing_reason": _choice(
            "If the primary route is account_billing, choose the precise controlled reason. Missing invoices, charge disputes, and payment reconciliation are distinct from detailed invoice requests.",
            {
                "registered_account_suspension": "A non-fraud Agora account is suspended or stopped and needs restoration/review.",
                "registered_fraud_account": "The Agora account is marked or suspended for fraud/risk.",
                "detailed_invoice_requested": "The customer requests a detailed invoice or receipt; it is not missing or disputed.",
                "missing_invoice": "An expected invoice is absent and the customer asks for it.",
                "invoice_charge_dispute": "The customer disputes invoice charges, usage, amount, or correctness.",
                "invoice_payment_reconciliation": "Payment records and invoice records need reconciliation.",
                "account_billing_other": "Another billing/account request or insufficient detail for a specific reason.",
            },
        ),
        "backend_operation_kind": _choice(
            "If the primary route is backend_operation, classify the requested backend change. Asking how to use an API/SDK, requesting documentation, reporting failure, or asking to disable a feature is not an enablement request.",
            {
                "explicit_media_relay_enablement": "The customer explicitly asks Agora staff to enable or activate Media Relay/Cross-Channel Media Relay.",
                "other_feature_enablement": "The customer explicitly asks Agora staff to enable another named feature.",
                "quota_change": "The customer explicitly requests a quota/limit review, increase, decrease, or adjustment.",
                "other_backend_operation": "Another explicit backend/account-state operation with a concrete desired outcome.",
                "insufficient_evidence": "No explicit backend action and target are supported by the customer text.",
            },
        ),
        "suspension_other_billing": _choice(
            "If this is account suspension, determine whether the current unresolved request also asks for another billing outcome such as refund, payment change, invoice work, or charge investigation.",
            yes_no_unknown,
        ),
    }
    for question_id, intent in _ADDITIONAL_INTENT_QUESTIONS.items():
        questions[question_id] = _choice(
            f"Determine whether the current unresolved request also contains a separate {intent} intent in addition to its primary route. Historical mentions and examples do not count.",
            yes_no_unknown,
        )
    evidence_criteria = {
        fragment_id: f"Select only if this exact customer-authored fragment explicitly supports the backend action and target: {text}"
        for fragment_id, text in fragments.items()
    }
    evidence_criteria["none"] = "No customer-authored fragment explicitly supports both the backend action and target."
    questions["backend_evidence"] = _choice(
        "If the primary route is backend_operation, select the one submitted customer fragment that explicitly authorizes the requested action and target. Never use assistant text.",
        evidence_criteria,
    )
    return questions


def questions_sha256(questions: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(questions, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_request(value: JevInput) -> tuple[dict[str, Any], dict[str, str]]:
    questions = build_questions(value)
    state = {
        "subject": value.subject,
        "messages": list(value.messages),
        "metadata": value.metadata,
    }
    return (
        {"model": JEV_MODEL, "state": state, "questions": questions},
        {"questions_sha256": questions_sha256(questions)},
    )


def _finite_probability(value: Any, *, code: str) -> float:
    if isinstance(value, bool):
        raise JevAdapterError(code)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise JevAdapterError(code) from exc
    if not math.isfinite(number) or number < 0 or number > 1:
        raise JevAdapterError(code)
    return number


def _parse_answer(question_id: str, raw: Any, choices: set[str]) -> _Answer:
    if not isinstance(raw, Mapping):
        raise JevAdapterError(f"invalid_answer:{question_id}")
    choice = raw.get("choice")
    if not isinstance(choice, str) or choice not in choices:
        raise JevAdapterError(f"invalid_choice:{question_id}")
    raw_probabilities = raw.get("probabilities")
    if not isinstance(raw_probabilities, Mapping) or set(raw_probabilities) != choices:
        raise JevAdapterError(f"invalid_probabilities:{question_id}")
    probabilities = {
        str(key): _finite_probability(item, code=f"invalid_probabilities:{question_id}")
        for key, item in raw_probabilities.items()
    }
    if not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=0.01):
        raise JevAdapterError(f"invalid_probabilities:{question_id}")
    if probabilities[choice] < max(probabilities.values()):
        raise JevAdapterError(f"invalid_probabilities:{question_id}")
    confidence = _finite_probability(raw.get("confidence"), code=f"invalid_confidence:{question_id}")
    return _Answer(choice=choice, probabilities=probabilities, confidence=confidence)


def _required(answers: Mapping[str, _Answer], question_id: str) -> _Answer:
    try:
        return answers[question_id]
    except KeyError as exc:
        raise JevAdapterError(f"missing_answer:{question_id}") from exc


def _is_uncertain(answer: _Answer) -> bool:
    return answer.choice == "uncertain" or answer.confidence < CONFIDENCE_THRESHOLD


def _additional_intents(answers: Mapping[str, _Answer], *, primary: str) -> tuple[list[str], list[str]]:
    values: list[str] = []
    abstentions: list[str] = []
    for question_id, intent in _ADDITIONAL_INTENT_QUESTIONS.items():
        if intent == primary:
            continue
        answer = _required(answers, question_id)
        if _is_uncertain(answer):
            abstentions.append(question_id)
        elif answer.choice == "yes":
            values.append(intent)
    return values, abstentions


def _map_answers(value: JevInput, answers: Mapping[str, _Answer]) -> tuple[dict[str, Any], list[str]]:
    intent = _required(answers, "intent")
    abstentions: list[str] = []
    intent_choice = "uncertain" if _is_uncertain(intent) else intent.choice
    payload: dict[str, Any] = {
        "intent_class": intent_choice,
        "intent_confidence": intent.confidence,
        "confidence": intent.confidence,
        "reason_code": "out_of_scope_or_unknown",
    }
    if intent_choice == "uncertain":
        abstentions.append("intent")
        return payload, abstentions
    if intent_choice == "conversation":
        action = _required(answers, "conversation_action")
        payload.update(
            conversation_action=action.choice,
            action_confidence=action.confidence,
            confidence=action.confidence,
            reason_code={
                "resolve": "conversation_resolution",
                "follow_up": "conversation_follow_up",
                "human_review": "conversation_requires_review",
            }[action.choice],
        )
        return payload, abstentions

    route = _required(answers, "agora_route")
    route_choice = "uncategorized" if _is_uncertain(route) else route.choice
    if route_choice == "uncategorized" and route.choice != "uncategorized":
        abstentions.append("agora_route")
    payload.update(
        agora_route=route_choice,
        agora_confidence=route.confidence,
        confidence=route.confidence,
        reason_code={
            "technical": "technical_request",
            "security_compliance": "security_compliance_request",
            "account_billing": "account_billing_request",
            "backend_operation": "explicit_backend_operation",
            "uncategorized": "no_matching_category",
        }[route_choice],
    )
    additional, additional_abstentions = _additional_intents(answers, primary=route_choice)
    abstentions.extend(additional_abstentions)
    payload["additional_intents"] = additional

    if route_choice == "account_billing":
        billing = _required(answers, "billing_reason")
        reason = billing.choice
        if billing.confidence < CONFIDENCE_THRESHOLD:
            reason = "account_billing_other"
            abstentions.append("billing_reason")
        subtype = {
            "registered_account_suspension": "account_suspension",
            "registered_fraud_account": "fraud_account",
            "detailed_invoice_requested": "detailed_invoice",
            "missing_invoice": "other",
            "invoice_charge_dispute": "other",
            "invoice_payment_reconciliation": "other",
            "account_billing_other": "other",
        }[reason]
        payload.update(account_billing_subcategory=subtype, reason_code=reason)
        if reason == "registered_account_suspension":
            other_billing = _required(answers, "suspension_other_billing")
            if other_billing.choice == "yes" and other_billing.confidence >= CONFIDENCE_THRESHOLD:
                payload["additional_intents"].append("other_billing")
            elif _is_uncertain(other_billing):
                payload["additional_intents"].append("uncertain_additional_intent")
                abstentions.append("suspension_other_billing")
        return payload, abstentions

    if route_choice == "backend_operation":
        kind = _required(answers, "backend_operation_kind")
        fragments = _customer_fragments(value)
        if kind.confidence < CONFIDENCE_THRESHOLD or kind.choice == "insufficient_evidence":
            payload.update(backend_operation_subcategory="unregistered", backend_operation=None)
            abstentions.append("backend_operation_kind")
            return payload, abstentions
        evidence = _required(answers, "backend_evidence")
        if evidence.confidence < CONFIDENCE_THRESHOLD or evidence.choice == "none":
            payload.update(backend_operation_subcategory="unregistered", backend_operation=None)
            abstentions.append("backend_evidence")
            return payload, abstentions
        if evidence.choice not in fragments:
            raise JevAdapterError("invalid_backend_evidence")
        operation_by_kind = {
            "explicit_media_relay_enablement": ("enablement", "enable", "media_relay"),
            "other_feature_enablement": ("enablement", "enable", "unsupported_feature"),
            "quota_change": ("quota", "adjust", "quota"),
            "other_backend_operation": ("unregistered", "operate", "backend_operation"),
        }
        subcategory, action, target = operation_by_kind[kind.choice]
        payload.update(
            backend_operation_subcategory=subcategory,
            backend_operation={
                "action": action,
                "target": target,
                "evidence": fragments[evidence.choice],
            },
        )
    return payload, abstentions


def parse_response(value: JevInput, response: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(response, Mapping):
        raise JevAdapterError("response_not_object")
    if response.get("model") != JEV_MODEL:
        raise JevAdapterError("model_version_mismatch")
    raw_answers = response.get("answers")
    if not isinstance(raw_answers, Mapping):
        raise JevAdapterError("missing_answers")
    questions = build_questions(value)
    answers: dict[str, _Answer] = {}
    for question_id, raw in raw_answers.items():
        if question_id not in questions:
            raise JevAdapterError("unexpected_answer")
        answers[str(question_id)] = _parse_answer(
            str(question_id), raw, set(questions[str(question_id)]["criteria"])
        )
    mapped, abstentions = _map_answers(value, answers)
    try:
        normalized = normalize_hermes_route_classification(
            mapped,
            latest_assistant_message_present=any(
                str(item.get("role") or "").lower() == "assistant" for item in value.messages
            ),
        )
    except HermesRouteClassificationError as exc:
        raise JevAdapterError(f"normalization_error:{exc.code}") from exc
    normalized["router_source"] = "jev_direct"
    normalized["classification_version"] = JEV_CLASSIFICATION_VERSION
    raw_usage = response.get("usage")
    if raw_usage is not None and not isinstance(raw_usage, Mapping):
        raise JevAdapterError("invalid_usage")
    usage: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens"):
        if not isinstance(raw_usage, Mapping) or key not in raw_usage:
            continue
        token_count = raw_usage[key]
        if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 0:
            raise JevAdapterError("invalid_usage")
        usage[key] = token_count
    evidence = {
        "requested_model": JEV_MODEL,
        "returned_model": response["model"],
        "questions_version": QUESTIONS_VERSION,
        "questions_sha256": questions_sha256(questions),
        "adapter_version": ADAPTER_VERSION,
        "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
        "confidence_policy_version": CONFIDENCE_POLICY_VERSION,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "answers": {
            key: {
                "choice": answer.choice,
                "probabilities": answer.probabilities,
                "confidence": answer.confidence,
                "winning_probability": answer.winning_probability,
            }
            for key, answer in answers.items()
        },
        "abstentions": sorted(set(abstentions)),
        "usage": usage,
        "http_call_count": 1,
    }
    raw = {**mapped, "_jev": evidence}
    return normalized, raw


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _default_open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _validate_threshold_environment() -> None:
    configured = os.getenv("ACCOUNT_ROUTER_CONFIDENCE_THRESHOLD")
    if configured is None:
        return
    try:
        matches = math.isclose(float(configured), CONFIDENCE_THRESHOLD, rel_tol=0.0, abs_tol=1e-12)
    except ValueError:
        matches = False
    if not matches:
        raise JevAdapterError("confidence_threshold_mismatch")


def jev_direct_candidate(
    *,
    api_key: str,
    timeout: float = 30.0,
    opener: Callable[[urllib.request.Request, float], Any] | None = None,
) -> Callable[[CaseSnapshot], CandidateResult]:
    """Create a one-request-per-case Jev candidate with no retry or fallback."""
    if not api_key.strip():
        raise JevAdapterError("missing_api_key")
    if timeout <= 0:
        raise JevAdapterError("invalid_timeout")
    _validate_threshold_environment()
    open_request = opener or _default_open

    def invoke(snapshot: CaseSnapshot) -> CandidateResult:
        value = jev_input_from_snapshot(snapshot)
        try:
            _validate_threshold_environment()
            payload, _ = build_request(value)
            input_sizes = validate_candidate_request_size(snapshot, payload["questions"])
            complete_request_bytes = len(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            if complete_request_bytes > MAX_REQUEST_BYTES:
                raise JevAdapterError("input_too_large")
            input_sizes["complete_request_bytes"] = complete_request_bytes
            request = urllib.request.Request(
                TYPESAFE_SYSTEMONE_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
        except (JevAdapterError, DatasetError) as exc:
            code = "input_too_large" if str(exc).startswith("input_too_large") else str(exc)
            return CandidateResult(
                candidate="jev",
                status="error",
                error=code,
                error_code=code,
                requested_model=JEV_MODEL,
                model_version=JEV_MODEL,
                prompt_version=QUESTIONS_VERSION,
                call_count=0,
            )
        started = time.monotonic()
        try:
            with open_request(request, timeout) as http_response:
                response = json.loads(http_response.read().decode("utf-8"))
            normalized, raw = parse_response(value, response)
            raw["_jev"]["input_sizes"] = input_sizes
            return CandidateResult(
                candidate="jev",
                status="ok",
                raw=raw,
                normalized=normalized,
                latency_ms=round((time.monotonic() - started) * 1000, 2),
                model_version=JEV_MODEL,
                prompt_version=QUESTIONS_VERSION,
                requested_model=JEV_MODEL,
                returned_model=JEV_MODEL,
                call_count=1,
                usage=dict(raw["_jev"]["usage"]),
                metadata=dict(raw["_jev"]),
            )
        except urllib.error.HTTPError as exc:
            code = "authentication_error" if exc.code in {401, 403} else (
                "rate_limited" if exc.code == 429 else "http_error"
            )
        except (TimeoutError, urllib.error.URLError):
            code = "timeout_or_network_error"
        except (json.JSONDecodeError, UnicodeDecodeError):
            code = "invalid_json"
        except JevAdapterError as exc:
            code = exc.code
        return CandidateResult(
            candidate="jev",
            status="error",
            error=code,
            error_code=code,
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            model_version=JEV_MODEL,
            prompt_version=QUESTIONS_VERSION,
            requested_model=JEV_MODEL,
            call_count=1,
        )

    return invoke
