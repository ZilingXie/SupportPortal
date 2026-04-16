from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from backend.services.client_ticket_agent_runtime import TicketExecutionResult
from backend.services.customer_reply_composer import (
    compose_customer_reply_email,
    detect_customer_reply_language,
)
from backend.services.investigation_flow import COMMUNICATING_STATUS
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import PRODUCT_SELECTION_SCENARIO, resolve_model_profile
from backend.services.prompts.product_selection import (
    build_product_selection_system_prompt,
    build_product_selection_user_prompt,
)
from backend.services.support_products import (
    SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING,
    SUPPORT_PRODUCT_CLOUD_RECORDING,
    get_support_product_label,
    list_support_products,
)
from backend.services.support_router import SupportRouteDecision, decide_support_route

LOGGER = logging.getLogger(__name__)

PRODUCT_SELECTION_STATE_AWAITING_CONFIRMATION = "awaiting_product_confirmation"
WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_PRODUCT = "clarify_customer_for_product"
PRODUCT_SELECTION_ROUTE_FAMILY = "product_selection"
PRODUCT_SELECTION_TOOLING_PROFILE = "product_selection_agent"
PRODUCT_SELECTION_REASON_AWAITING_CONFIRMATION = "awaiting_product_confirmation"
_NORMALIZED_AWAITING_CONFIRMATION = "awaiting product confirmation"

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")

_EXPLICIT_PRODUCT_ALIASES: dict[str, tuple[str, ...]] = {
    SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING: (
        "audio/video calling",
        "audio video calling",
        "rtc",
        "rtc sdk",
        "rtc issue",
        "real time calling",
        "real-time calling",
    ),
    SUPPORT_PRODUCT_CLOUD_RECORDING: (
        "cloud recording",
        "cloud-recording",
        "cloud recording sdk",
    ),
}

_SHORT_CONFIRMATION_ALIASES: dict[str, tuple[str, ...]] = {
    SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING: (
        "rtc",
        "calling",
        "call sdk",
        "video calling",
        "audio calling",
    ),
    SUPPORT_PRODUCT_CLOUD_RECORDING: (
        "cloud recording",
        "recording",
        "recording sdk",
    ),
}

_PRODUCT_SIGNAL_RULES: dict[str, tuple[tuple[str, int], ...]] = {
    SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING: (
        ("join channel", 3),
        ("publish", 2),
        ("subscribe", 2),
        ("remote video", 3),
        ("remote audio", 3),
        ("no video", 2),
        ("no audio", 2),
        ("black screen", 2),
        ("token renewal", 2),
        ("setclientrole", 3),
        ("channel profile", 2),
        ("uid", 1),
    ),
    SUPPORT_PRODUCT_CLOUD_RECORDING: (
        ("sid", 4),
        ("resource id", 4),
        ("acquire", 3),
        ("start recording", 3),
        ("stop recording", 3),
        ("query recording", 3),
        ("update layout", 3),
        ("web page recording", 4),
        ("recording mode", 3),
        ("composite", 2),
        ("individual", 2),
        ("recording file", 2),
        ("transcoding", 2),
        ("layout", 2),
    ),
}

_CORRECTION_MARKERS = (
    "actually",
    "not rtc",
    "not audio",
    "not video",
    "not cloud",
    "instead",
    "should be",
    "this is",
    "it's",
    "it is",
    "for ",
)


@dataclass(frozen=True)
class SupportProductDecision:
    product: str | None
    confidence: float
    reason: str
    matched_signals: list[str]


@dataclass(frozen=True)
class ResolvedSupportProductContext:
    effective_message: str
    product: str | None
    product_selection_state: dict[str, Any] | None
    preflight_execution: TicketExecutionResult | None = None
    route_decision: SupportRouteDecision | None = None
    product_decision: SupportProductDecision | None = None
    product_changed: bool = False


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_matching_text(value: Any) -> str:
    normalized = _clean_text(value).lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", normalized).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_text = _normalize_matching_text(text)
    normalized_phrase = _normalize_matching_text(phrase)
    if not normalized_text or not normalized_phrase:
        return False
    if _CJK_RE.search(normalized_phrase):
        return normalized_phrase in normalized_text
    escaped = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", normalized_text) is not None


def _looks_short_confirmation(message: str) -> bool:
    return len(_ASCII_TOKEN_RE.findall(_normalize_matching_text(message))) <= 6


def _normalize_alias_product(value: Any) -> str | None:
    normalized = _normalize_matching_text(value)
    if not normalized:
        return None
    if normalized == SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING:
        return SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING
    if normalized == SUPPORT_PRODUCT_CLOUD_RECORDING:
        return SUPPORT_PRODUCT_CLOUD_RECORDING
    for product, aliases in _EXPLICIT_PRODUCT_ALIASES.items():
        if any(_contains_phrase(normalized, alias) for alias in aliases):
            return product
    for product, aliases in _SHORT_CONFIRMATION_ALIASES.items():
        if any(normalized == _normalize_matching_text(alias) for alias in aliases):
            return product
    return None


def _explicit_product_mentions(message: str, *, confirmation_mode: bool = False) -> list[str]:
    text = _normalize_matching_text(message)
    if not text:
        return []
    mentions: list[str] = []
    for product, aliases in _EXPLICIT_PRODUCT_ALIASES.items():
        if any(_contains_phrase(text, alias) for alias in aliases):
            mentions.append(product)
    if confirmation_mode and _looks_short_confirmation(text):
        for product, aliases in _SHORT_CONFIRMATION_ALIASES.items():
            if any(text == _normalize_matching_text(alias) for alias in aliases) and product not in mentions:
                mentions.append(product)
    return mentions


def normalize_product_selection_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    phase = _normalize_matching_text(value.get("phase"))
    if phase != _NORMALIZED_AWAITING_CONFIRMATION:
        return None
    pending_customer_message = _clean_text(value.get("pending_customer_message"))
    return {
        "phase": PRODUCT_SELECTION_STATE_AWAITING_CONFIRMATION,
        "pending_customer_message": pending_customer_message or None,
        "pending_message_created_at": _clean_text(value.get("pending_message_created_at")) or None,
        "last_confirmation_requested_at": _clean_text(value.get("last_confirmation_requested_at")) or None,
        "last_updated_at": _clean_text(value.get("last_updated_at")) or None,
    }


def build_product_selection_state(
    *,
    pending_customer_message: str,
    pending_message_created_at: str | None,
    now_value: str | None,
) -> dict[str, Any]:
    return {
        "phase": PRODUCT_SELECTION_STATE_AWAITING_CONFIRMATION,
        "pending_customer_message": _clean_text(pending_customer_message) or None,
        "pending_message_created_at": _clean_text(pending_message_created_at) or None,
        "last_confirmation_requested_at": _clean_text(now_value) or None,
        "last_updated_at": _clean_text(now_value) or None,
    }


def combine_product_follow_up_message(pending_customer_message: str, follow_up_message: str) -> str:
    pending_text = _clean_text(pending_customer_message)
    follow_up_text = _clean_text(follow_up_message)
    if not pending_text:
        return follow_up_text
    if not follow_up_text or _normalize_matching_text(follow_up_text) == _normalize_matching_text(pending_text):
        return pending_text
    return (
        f"{pending_text}\n\n"
        f"Customer follow-up after product confirmation request: {follow_up_text}"
    )


def detect_explicit_support_product(message: str, *, confirmation_mode: bool = False) -> str | None:
    text = _normalize_matching_text(message)
    if not text:
        return None
    if confirmation_mode and _looks_short_confirmation(text):
        matches = [
            product
            for product, aliases in _SHORT_CONFIRMATION_ALIASES.items()
            if any(text == _normalize_matching_text(alias) for alias in aliases)
        ]
        return matches[0] if len(matches) == 1 else None
    explicit_mentions = _explicit_product_mentions(text, confirmation_mode=False)
    return explicit_mentions[0] if len(explicit_mentions) == 1 else None


def detect_support_product_correction(message: str, current_product: str | None) -> str | None:
    if not current_product:
        return None
    normalized_message = _normalize_matching_text(message)
    mentions = _explicit_product_mentions(message, confirmation_mode=True)
    if not mentions:
        return None
    alternatives = [product for product in mentions if product != current_product]
    if not alternatives:
        return None
    if len(alternatives) > 1:
        return None
    detected = alternatives[0]
    if _looks_short_confirmation(normalized_message):
        return detected
    if any(marker in normalized_message for marker in _CORRECTION_MARKERS):
        return detected
    current_label = _normalize_matching_text(get_support_product_label(current_product) or current_product)
    if current_label and current_label in normalized_message:
        return detected
    return None


def infer_support_product_deterministically(message: str) -> SupportProductDecision | None:
    text = _normalize_matching_text(message)
    if not text:
        return None
    explicit = detect_explicit_support_product(text, confirmation_mode=False)
    if explicit is not None:
        return SupportProductDecision(
            product=explicit,
            confidence=0.99,
            reason="explicit_product_mention",
            matched_signals=[get_support_product_label(explicit) or explicit],
        )

    scores: dict[str, int] = {
        SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING: 0,
        SUPPORT_PRODUCT_CLOUD_RECORDING: 0,
    }
    matched_signals: dict[str, list[str]] = {
        SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING: [],
        SUPPORT_PRODUCT_CLOUD_RECORDING: [],
    }
    for product, rules in _PRODUCT_SIGNAL_RULES.items():
        for phrase, weight in rules:
            if _contains_phrase(text, phrase):
                scores[product] += weight
                matched_signals[product].append(phrase)

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_product, best_score = ordered[0]
    second_score = ordered[1][1]
    if best_score < 4 or best_score - second_score < 2:
        return None
    return SupportProductDecision(
        product=best_product,
        confidence=min(0.92, 0.6 + best_score * 0.05),
        reason="deterministic_signal_match",
        matched_signals=matched_signals[best_product][:4],
    )


def decide_support_product(
    *,
    message: str,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, Any]] | None = None,
    current_product: str | None = None,
    awaiting_confirmation: bool = False,
) -> SupportProductDecision:
    deterministic = infer_support_product_deterministically(message)
    if deterministic is not None:
        return deterministic

    profile = resolve_model_profile(PRODUCT_SELECTION_SCENARIO)
    if not profile.api_key:
        return SupportProductDecision(
            product=None,
            confidence=0.0,
            reason="product_agent_missing_api_key",
            matched_signals=[],
        )

    allowed_products = [
        {"value": profile.value, "label": profile.label}
        for profile in list_support_products()
    ]
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=build_product_selection_system_prompt(),
            user_prompt=build_product_selection_user_prompt(
                latest_customer_message=message,
                ticket_subject=ticket_subject,
                ticket_context=ticket_context,
                current_product=current_product,
                awaiting_confirmation=awaiting_confirmation,
                allowed_products=allowed_products,
            ),
        )
    except LlmInvocationError as exc:
        LOGGER.warning("Product selection agent failed: %s", exc)
        return SupportProductDecision(
            product=None,
            confidence=0.0,
            reason="product_agent_error",
            matched_signals=[],
        )
    except Exception as exc:
        LOGGER.warning("Product selection agent failed unexpectedly: %s", exc)
        return SupportProductDecision(
            product=None,
            confidence=0.0,
            reason="product_agent_error",
            matched_signals=[],
        )

    try:
        payload = json.loads(str(response.text or "").strip() or "{}")
    except json.JSONDecodeError:
        return SupportProductDecision(
            product=None,
            confidence=0.0,
            reason="product_agent_invalid_json",
            matched_signals=[],
        )

    selected_product = _normalize_alias_product(payload.get("product"))
    if str(payload.get("product") or "").strip().lower() == "unknown":
        selected_product = None
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    matched_signals = [
        _clean_text(item)
        for item in list(payload.get("matched_signals") or [])
        if _clean_text(item)
    ][:4]
    return SupportProductDecision(
        product=selected_product,
        confidence=max(0.0, min(1.0, confidence)),
        reason=_clean_text(payload.get("reason")) or ("product_inferred" if selected_product else "product_unknown"),
        matched_signals=matched_signals,
    )


def build_product_confirmation_reply(
    *,
    message: str,
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    language = detect_customer_reply_language(message)
    body = (
        "为了把你转到正确的支持流程，能否确认这是 Audio/Video Calling (RTC) 还是 Cloud Recording 的问题？"
        if language == "zh"
        else "To route this correctly, could you confirm whether this is for Audio/Video Calling (RTC) or Cloud Recording?"
    )
    opener = "感谢你提供这些信息。" if language == "zh" else "Thanks for the details."
    return compose_customer_reply_email(
        reply_kind="clarification",
        body=body,
        requester=requester,
        customer_id=customer_id,
        language=language,
        opener=opener,
    )


def build_product_confirmation_execution(
    *,
    message: str,
    requester: str | None = None,
    customer_id: str | None = None,
    reason: str = PRODUCT_SELECTION_REASON_AWAITING_CONFIRMATION,
    matched_signals: list[str] | None = None,
) -> TicketExecutionResult:
    reply = build_product_confirmation_reply(
        message=message,
        requester=requester,
        customer_id=customer_id,
    )
    return TicketExecutionResult(
        answer=reply,
        confidence=1.0,
        sources=[],
        citations=[],
        evidence_summary={"diagnostics": {"product_selection": True, "reason": reason}},
        packed_evidence=None,
        needs_investigating=False,
        next_status=COMMUNICATING_STATUS,
        answer_route="workflow",
        scope_label="agora_technical",
        route_family=PRODUCT_SELECTION_ROUTE_FAMILY,
        execution_action=WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_PRODUCT,
        tooling_profile=PRODUCT_SELECTION_TOOLING_PROFILE,
        route_reason=reason,
        route_confidence=1.0,
        search_used=False,
        matched_signals=list(matched_signals or [reason]),
        investigation_reason=None,
        workflow_action=WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_PRODUCT,
        client_intake_state=None,
    )


def resolve_support_product_context(
    *,
    message: str,
    ticket_subject: str | None,
    ticket_context: list[dict[str, Any]] | None,
    product: str | None,
    product_selection_state: dict[str, Any] | None,
    latest_assistant_message: dict[str, Any] | None,
    current_ticket_status: str | None,
    requester: str | None = None,
    customer_id: str | None = None,
    message_created_at: str | None = None,
    route_agent: Callable[..., SupportRouteDecision] | None = None,
    product_agent: Callable[..., SupportProductDecision] | None = None,
) -> ResolvedSupportProductContext:
    resolved_route_agent = route_agent or decide_support_route
    resolved_product_agent = product_agent or (
        lambda **kwargs: decide_support_product(
            message=str(kwargs.get("message") or ""),
            ticket_subject=kwargs.get("ticket_subject"),
            ticket_context=kwargs.get("ticket_context"),
            current_product=kwargs.get("current_product"),
            awaiting_confirmation=bool(kwargs.get("awaiting_confirmation")),
        )
    )
    normalized_state = normalize_product_selection_state(product_selection_state)
    normalized_product = _normalize_alias_product(product)
    base_message = _clean_text(message)

    corrected_product = detect_support_product_correction(base_message, normalized_product)
    if corrected_product is not None:
        return ResolvedSupportProductContext(
            effective_message=base_message,
            product=corrected_product,
            product_selection_state=None,
            product_changed=corrected_product != normalized_product,
            product_decision=SupportProductDecision(
                product=corrected_product,
                confidence=1.0,
                reason="product_corrected_by_customer",
                matched_signals=[get_support_product_label(corrected_product) or corrected_product],
            ),
        )

    if normalized_state is not None:
        pending_message = _clean_text(normalized_state.get("pending_customer_message") or "") or base_message
        combined_message = combine_product_follow_up_message(pending_message, base_message)
        explicit_product = detect_explicit_support_product(base_message, confirmation_mode=True)
        decision = (
            SupportProductDecision(
                product=explicit_product,
                confidence=1.0,
                reason="product_confirmed_by_customer",
                matched_signals=[get_support_product_label(explicit_product) or explicit_product],
            )
            if explicit_product is not None
            else resolved_product_agent(
                message=combined_message,
                ticket_subject=ticket_subject,
                ticket_context=ticket_context,
                current_product=None,
                awaiting_confirmation=True,
            )
        )
        if decision.product is not None:
            return ResolvedSupportProductContext(
                effective_message=combined_message,
                product=decision.product,
                product_selection_state=None,
                product_decision=decision,
            )
        return ResolvedSupportProductContext(
            effective_message=combined_message,
            product=None,
            product_selection_state=build_product_selection_state(
                pending_customer_message=combined_message,
                pending_message_created_at=normalized_state.get("pending_message_created_at") or message_created_at,
                now_value=message_created_at,
            ),
            preflight_execution=build_product_confirmation_execution(
                message=combined_message,
                requester=requester,
                customer_id=customer_id,
                reason=PRODUCT_SELECTION_REASON_AWAITING_CONFIRMATION,
                matched_signals=decision.matched_signals or [PRODUCT_SELECTION_REASON_AWAITING_CONFIRMATION],
            ),
            product_decision=decision,
        )

    if normalized_product is not None:
        return ResolvedSupportProductContext(
            effective_message=base_message,
            product=normalized_product,
            product_selection_state=None,
        )

    route_decision = resolved_route_agent(
        message=base_message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=None,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=False,
    )
    if str(route_decision.scope_label or "").strip().lower() != "agora_technical":
        return ResolvedSupportProductContext(
            effective_message=base_message,
            product=None,
            product_selection_state=None,
            route_decision=route_decision,
        )

    decision = resolved_product_agent(
        message=base_message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        current_product=None,
        awaiting_confirmation=False,
    )
    if decision.product is not None:
        return ResolvedSupportProductContext(
            effective_message=base_message,
            product=decision.product,
            product_selection_state=None,
            route_decision=route_decision,
            product_decision=decision,
        )

    return ResolvedSupportProductContext(
        effective_message=base_message,
        product=None,
        product_selection_state=build_product_selection_state(
            pending_customer_message=base_message,
            pending_message_created_at=message_created_at,
            now_value=message_created_at,
        ),
        preflight_execution=build_product_confirmation_execution(
            message=base_message,
            requester=requester,
            customer_id=customer_id,
            reason=PRODUCT_SELECTION_REASON_AWAITING_CONFIRMATION,
            matched_signals=decision.matched_signals or [PRODUCT_SELECTION_REASON_AWAITING_CONFIRMATION],
        ),
        route_decision=route_decision,
        product_decision=decision,
    )
