from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from backend.services.api_semantics import is_api_semantics_mismatch_message
from backend.services.automation_routing import canonical_automation_subcategory
from backend.services.billing_automation import (
    BILLING_ROUTE_FAMILY,
    BILLING_SCOPE_LABEL,
    BILLING_TOOLING_PROFILE,
    build_billing_automation_result,
    detect_billing_route,
    send_billing_internal_email,
)
from backend.services.enablement_automation import (
    ENABLEMENT_ACTION,
    ENABLEMENT_SCOPE_LABEL,
    ENABLEMENT_SEMANTIC_INTENT,
    ENABLEMENT_TOOLING_PROFILE,
    build_enablement_automation_result,
    detect_enablement_route,
    send_enablement_internal_email,
)
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    INTENT_ROUTER_SCENARIO,
    WEB_SEARCH_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.prompts.web_search import build_web_search_system_prompt, build_web_search_user_prompt
from backend.services.prompt_runtime import resolve_system_prompt
from backend.services.support_router_prompt import (
    build_route_prompt_hints,
    build_route_system_prompt,
    build_route_user_payload,
    detect_product_portfolio_signals,
)
from backend.services.ticket_resolution import (
    build_resolved_confirmation_reply,
    has_resolution_negative_marker,
    is_customer_resolved_confirmation_candidate,
    matched_resolution_markers,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_INTENT_ROUTER_TIMEOUT_SECONDS = 8.0
DEFAULT_INTENT_ROUTER_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_OPENAI_WEB_SEARCH_TIMEOUT_SECONDS = 30.0
PRODUCT_PORTFOLIO_ROUTE_REASON = "agora_product_portfolio"
OFFICIAL_AGORA_DOMAINS = [
    "agora.io",
    "www.agora.io",
    "docs.agora.io",
    "docs-preview.agora.io",
    "investor.agora.io",
]
OFFICIAL_AGORA_PRODUCT_PORTFOLIO_DOMAINS = [
    "agora.io",
    "www.agora.io",
]
AUTHORITATIVE_WEB_SOURCE_TIERS = {
    "tier_1": [
        "agora.io",
        "www.agora.io",
        "investor.agora.io",
        "sec.gov",
        "www.sec.gov",
        "nasdaq.com",
        "www.nasdaq.com",
    ],
    "tier_2": [
        "reuters.com",
        "www.reuters.com",
        "bloomberg.com",
        "www.bloomberg.com",
        "finance.yahoo.com",
        "www.marketwatch.com",
    ],
}
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+[^)]*)\)", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)
_URLISH_LABEL_RE = re.compile(r"^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s]*)?$", re.IGNORECASE)
_QUESTION_PREFIX_RE = re.compile(r"^\s*(how|what|why|when|where|can|could|do|does|is|are|should)\b", re.IGNORECASE)
_JOIN_CHANNEL_RE = re.compile(r"\bjoin(?:ing)?\s+(?:a\s+|the\s+)?channel\b", re.IGNORECASE)
_GENERAL_SYSTEM_HELP_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bblue[ -]?screen(?:ed)?\b", re.IGNORECASE), "blue screen"),
    (re.compile(r"\bprinter\b", re.IGNORECASE), "printer"),
    (re.compile(r"\boutlook\b", re.IGNORECASE), "outlook"),
    (re.compile(r"\bexcel\b", re.IGNORECASE), "excel"),
    (re.compile(r"\boffice\s+(?:wi-?fi|wifi)\b", re.IGNORECASE), "office wifi"),
)
_FASTPATH_TROUBLESHOOTING_SIGNALS = frozenset(
    {
        "black screen",
        "blank screen",
        "black video",
        "no video",
        "no audio",
        "frozen video",
        "can't see remote video",
        "cannot see remote video",
        "can't hear",
        "cannot hear",
        "join failed",
        "cannot join",
        "can't join",
        "disconnect",
        "disconnected",
        "network quality",
    }
)
_SHORT_GRATITUDE_MESSAGE_MAX_WORDS = 20
_SHORT_TROUBLESHOOTING_MESSAGE_MAX_WORDS = 24
_SHORT_TROUBLESHOOTING_MESSAGE_MAX_CHARS = 180
_GRATITUDE_BILLING_EXCLUSION_RE = re.compile(
    r"\b(?:account|billing|invoice|payment|fraud|suspicious|suspended|disabled|blocked|"
    r"restore|reactivat|verification|verify|refund|dispute|charge|transaction|balance|"
    r"technical|api|sdk|rtc|channel|token|appid|app\s*id)\b",
    re.IGNORECASE,
)
_DETERMINISTIC_BILLING_RISK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:refund|chargeback|return\s+(?:my|our)\s+money)\b", re.IGNORECASE), "refund_request"),
    (
        re.compile(
            r"\b(?:dispute|wrong\s+amount|incorrect\s+amount|overcharged|charged\s+wrong|"
            r"wrong\s+charge|billing\s+error|why\s+was\s+i\s+charged)\b",
            re.IGNORECASE,
        ),
        "billing_dispute",
    ),
    (re.compile(r"\b(?:legal|lawsuit|sue|compensation|damages)\b", re.IGNORECASE), "legal_or_compensation"),
)


@dataclass(frozen=True)
class SupportRouteDecision:
    scope_label: str
    route: str
    confidence: float
    reason: str
    matched_signals: list[str] = field(default_factory=list)
    response_language: str = "en"
    route_family: str | None = None
    execution_action: str | None = None
    tooling_profile: str | None = None
    # ── Semantic routing (LLM-first architecture) ──
    semantic_intent: str | None = None
    automation_eligibility: str | None = None
    policy_decision: str | None = None
    not_automated_reason: str | None = None
    risk_flags: list[str] = field(default_factory=list)
    evidence_spans: list[str] = field(default_factory=list)
    router_source: str = "deterministic"
    # ── Router audit (observability) ──
    intent_router_attempted: bool = False
    intent_router_confidence_threshold: float | None = None
    intent_router_model_confidence: float | None = None
    intent_router_fallback_reason: str | None = None
    intent_router_failure_type: str | None = None
    intent_router_failure_source: str | None = None

    def __post_init__(self) -> None:
        route_family, execution_action, tooling_profile = _route_contract_for_scope(
            scope_label=self.scope_label,
            action=self.execution_action or self.route,
            reason=self.reason,
        )
        resolved_route_family = self.route_family or route_family
        resolved_execution_action = self.execution_action or execution_action
        if resolved_route_family == BILLING_ROUTE_FAMILY:
            resolved_execution_action = canonical_automation_subcategory(resolved_execution_action)
            object.__setattr__(self, "route", resolved_execution_action)
        object.__setattr__(self, "route_family", resolved_route_family)
        object.__setattr__(self, "execution_action", resolved_execution_action)
        object.__setattr__(self, "tooling_profile", self.tooling_profile or tooling_profile)


@dataclass(frozen=True)
class WebSearchAnswer:
    answer: str
    sources: list[str]
    citations: list[dict[str, str]]
    search_used: bool


@dataclass(frozen=True)
class SupportResolution:
    answer: str
    confidence: float
    sources: list[str]
    citations: list[dict[str, str]]
    needs_engineer_guidance: bool
    answer_route: str
    scope_label: str
    route_reason: str
    route_confidence: float
    search_used: bool
    matched_signals: list[str] = field(default_factory=list)
    route_family: str | None = None
    execution_action: str | None = None
    tooling_profile: str | None = None
    evidence_summary: dict[str, Any] | None = None
    packed_evidence: dict[str, Any] | None = None
    # ── Router audit (observability) ──
    router_source: str = "deterministic"
    intent_router_attempted: bool = False
    intent_router_confidence_threshold: float | None = None
    intent_router_model_confidence: float | None = None
    intent_router_fallback_reason: str | None = None
    intent_router_failure_type: str | None = None
    intent_router_failure_source: str | None = None

    def as_answer_tuple(self) -> tuple[str, float, list[str], list[dict[str, str]], bool]:
        return (
            self.answer,
            self.confidence,
            list(self.sources),
            [dict(item) for item in self.citations],
            self.needs_engineer_guidance,
        )

    def route_payload(self) -> dict[str, Any]:
        return {
            "answer_route": self.answer_route,
            "scope_label": self.scope_label,
            "route_family": self.route_family,
            "execution_action": self.execution_action,
            "tooling_profile": self.tooling_profile,
            "route_reason": self.route_reason,
            "route_confidence": round(float(self.route_confidence), 4),
            "search_used": bool(self.search_used),
            "matched_signals": list(self.matched_signals),
            "router_source": self.router_source,
            "intent_router_attempted": self.intent_router_attempted,
            "intent_router_confidence_threshold": self.intent_router_confidence_threshold,
            "intent_router_model_confidence": self.intent_router_model_confidence,
            "intent_router_fallback_reason": self.intent_router_fallback_reason,
            "intent_router_failure_type": self.intent_router_failure_type,
            "intent_router_failure_source": self.intent_router_failure_source,
        }


def _route_contract_for_scope(*, scope_label: str, action: str, reason: str) -> tuple[str, str, str]:
    clean_scope = _normalize_text(scope_label).lower()
    normalized_action = _normalize_text(action).lower()
    normalized_reason = _normalize_text(reason).lower()

    if clean_scope == "ticket_resolution":
        return "ticket_resolution", "resolve_ticket", "deterministic_resolution"
    if clean_scope == BILLING_SCOPE_LABEL:
        normalized_action = canonical_automation_subcategory(normalized_action)
        if normalized_action in {"account_suspension", "detailed_invoice", "account_verification"}:
            return BILLING_ROUTE_FAMILY, normalized_action, BILLING_TOOLING_PROFILE
        if normalized_action == "human_review_required":
            return "billing_review", "human_review_required", BILLING_TOOLING_PROFILE
        return "fallback_or_refuse", "refuse", "no_agora_docs_refusal"
    if clean_scope == ENABLEMENT_SCOPE_LABEL:
        if normalized_action in {ENABLEMENT_ACTION, "automation_candidate"}:
            return BILLING_ROUTE_FAMILY, ENABLEMENT_ACTION, ENABLEMENT_TOOLING_PROFILE
        return "fallback_or_refuse", "refuse", "no_agora_docs_refusal"
    if clean_scope == "agora_technical":
        return "agora_docs_rag", "rag", "agora_docs_only"
    if clean_scope == "agora_non_technical":
        actual_action = normalized_action if normalized_action in {"web_search", "refuse"} else "web_search"
        if actual_action == "web_search":
            tooling = "official_web_search"
        else:
            tooling = "no_agora_docs_refusal"
        return "web_company_info", actual_action, tooling
    if clean_scope == "small_talk":
        actual_action = (
            "controlled_response"
            if normalized_action == "controlled_response" or normalized_reason == "gratitude_acknowledgement"
            else "refuse"
        )
        tooling = "controlled_acknowledgement" if actual_action == "controlled_response" else "no_agora_docs_refusal"
        return "general_chat", actual_action, tooling
    if clean_scope == "non_agora":
        return "fallback_or_refuse", "refuse", "no_agora_docs_refusal"
    return "fallback_or_refuse", "refuse", "no_agora_docs_refusal"


def _build_route_decision(
    *,
    scope_label: str,
    action: str,
    confidence: float,
    reason: str,
    matched_signals: list[str],
    response_language: str,
    semantic_intent: str | None = None,
    automation_eligibility: str | None = None,
    policy_decision: str | None = None,
    not_automated_reason: str | None = None,
    risk_flags: list[str] | None = None,
    evidence_spans: list[str] | None = None,
    router_source: str = "deterministic",
    # Router audit fields
    intent_router_attempted: bool = False,
    intent_router_confidence_threshold: float | None = None,
    intent_router_model_confidence: float | None = None,
    intent_router_fallback_reason: str | None = None,
    intent_router_failure_type: str | None = None,
    intent_router_failure_source: str | None = None,
) -> SupportRouteDecision:
    route_family, execution_action, tooling_profile = _route_contract_for_scope(
        scope_label=scope_label,
        action=action,
        reason=reason,
    )
    return SupportRouteDecision(
        scope_label=scope_label,
        route=execution_action,
        route_family=route_family,
        execution_action=execution_action,
        tooling_profile=tooling_profile,
        confidence=confidence,
        reason=reason,
        matched_signals=matched_signals,
        response_language=response_language,
        semantic_intent=semantic_intent,
        automation_eligibility=automation_eligibility,
        policy_decision=policy_decision,
        not_automated_reason=not_automated_reason,
        risk_flags=risk_flags or [],
        evidence_spans=evidence_spans or [],
        router_source=router_source,
        intent_router_attempted=intent_router_attempted,
        intent_router_confidence_threshold=intent_router_confidence_threshold,
        intent_router_model_confidence=intent_router_model_confidence,
        intent_router_fallback_reason=intent_router_fallback_reason,
        intent_router_failure_type=intent_router_failure_type,
        intent_router_failure_source=intent_router_failure_source,
    )


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _safe_nonnegative_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _sanitize_matched_signals(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    sanitized: list[str] = []
    for value in values or []:
        clean = _normalize_text(value)
        if clean and clean not in sanitized:
            sanitized.append(clean)
    return sanitized


def _sanitize_web_search_answer_text(text: Any) -> str:
    sanitized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not sanitized:
        return ""

    def _replace_markdown_link(match: re.Match[str]) -> str:
        label = _normalize_text(match.group(1))
        return "" if _URLISH_LABEL_RE.fullmatch(label) else label

    sanitized = _MARKDOWN_LINK_RE.sub(_replace_markdown_link, sanitized)
    sanitized = _BARE_URL_RE.sub("", sanitized)

    previous = None
    while previous != sanitized:
        previous = sanitized
        sanitized = re.sub(r"\(\s*\)", "", sanitized)
        sanitized = re.sub(r"\[\s*\]", "", sanitized)

    sanitized = re.sub(r"\s+([,.;:!?])", r"\1", sanitized)
    lines = []
    for line in sanitized.split("\n"):
        normalized_line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        normalized_line = re.sub(r"\s+([,.;:!?])", r"\1", normalized_line)
        lines.append(normalized_line)
    sanitized = "\n".join(lines)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    return sanitized


def _response_language(message: str) -> str:
    return "zh" if _CJK_RE.search(str(message or "")) else "en"


def _looks_like_question(message: str) -> bool:
    text = _normalize_text(message)
    if not text:
        return False
    return bool(_QUESTION_PREFIX_RE.search(text) or text.endswith("?"))


def _match_general_system_help_signals(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    matches: list[str] = []
    for pattern, label in _GENERAL_SYSTEM_HELP_PATTERNS:
        if label in matches:
            continue
        if pattern.search(normalized):
            matches.append(label)
    return matches


def _is_short_troubleshooting_message(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if len(normalized) > _SHORT_TROUBLESHOOTING_MESSAGE_MAX_CHARS:
        return False
    return len(normalized.split()) <= _SHORT_TROUBLESHOOTING_MESSAGE_MAX_WORDS


def _is_short_gratitude_message(text: str) -> bool:
    """True when the message is short, pure gratitude without billing/account/fraud/technical signals."""
    normalized = _normalize_text(text)
    if not normalized:
        return False
    word_count = len(normalized.split())
    if word_count > _SHORT_GRATITUDE_MESSAGE_MAX_WORDS:
        return False
    if _GRATITUDE_BILLING_EXCLUSION_RE.search(normalized):
        return False
    return True


def _match_deterministic_billing_risk_flags(text: str) -> list[str]:
    flags: list[str] = []
    for pattern, flag in _DETERMINISTIC_BILLING_RISK_PATTERNS:
        if pattern.search(text):
            flags.append(flag)
    return flags


def _heuristic_route_decision(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    response_language: str,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    has_active_engineer_case: bool = False,
) -> SupportRouteDecision | None:
    text = _normalize_text(message)
    if not text:
        return None

    enablement_match = detect_enablement_route(text)
    if enablement_match is not None:
        return _build_route_decision(
            scope_label=ENABLEMENT_SCOPE_LABEL,
            action=ENABLEMENT_ACTION,
            confidence=0.99,
            reason=enablement_match.reason,
            matched_signals=enablement_match.matched_signals,
            response_language=response_language,
            semantic_intent=ENABLEMENT_SEMANTIC_INTENT,
            automation_eligibility="eligible",
            policy_decision="policy_gate",
            router_source="deterministic",
        )

    billing_match = detect_billing_route(text)
    if billing_match is not None:
        risk_flags = _match_deterministic_billing_risk_flags(text)
        if risk_flags:
            return _build_route_decision(
                scope_label=BILLING_SCOPE_LABEL,
                action="human_review_required",
                confidence=0.98,
                reason=f"{billing_match.reason}_risk_review",
                matched_signals=_sanitize_matched_signals([*billing_match.matched_signals, *risk_flags]),
                response_language=response_language,
                semantic_intent=f"billing.{billing_match.action}",
                automation_eligibility="not_eligible",
                policy_decision="policy_gate",
                not_automated_reason="human_review_required",
                risk_flags=risk_flags,
                router_source="deterministic",
            )
        return _build_route_decision(
            scope_label=BILLING_SCOPE_LABEL,
            action=billing_match.action,
            confidence=0.98,
            reason=billing_match.reason,
            matched_signals=_sanitize_matched_signals(billing_match.matched_signals),
            response_language=response_language,
            semantic_intent=f"billing.{billing_match.action}",
            router_source="deterministic",
        )

    if is_customer_resolved_confirmation_candidate(
        text,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
    ):
        matched_signals = matched_resolution_markers(text)
        if latest_assistant_message is not None:
            matched_signals.append(
                "engineer_guidance"
                if _normalize_text(latest_assistant_message.get("assistant_message_source")).lower() == "engineer_guidance"
                else "latest_support_reply"
            )
        if has_active_engineer_case:
            matched_signals.append("active_engineer_case")
        return _build_route_decision(
            scope_label="ticket_resolution",
            action="resolve_ticket",
            confidence=0.99,
            reason="customer_confirmed_resolved",
            matched_signals=_sanitize_matched_signals(matched_signals),
            response_language=response_language,
        )

    gratitude_signals = matched_resolution_markers(text)
    if gratitude_signals and not has_resolution_negative_marker(text) and not _looks_like_question(text) and _is_short_gratitude_message(text):
        return _build_route_decision(
            scope_label="small_talk",
            action="controlled_response",
            confidence=0.91,
            reason="gratitude_acknowledgement",
            matched_signals=_sanitize_matched_signals(gratitude_signals),
            response_language=response_language,
        )

    if _is_short_troubleshooting_message(text):
        hints = build_route_prompt_hints(
            text,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            latest_assistant_message=latest_assistant_message,
            current_ticket_status=current_ticket_status,
            has_active_engineer_case=has_active_engineer_case,
        )
        message_matches = hints.get("message_matches") if isinstance(hints.get("message_matches"), dict) else {}
        flags = hints.get("flags") if isinstance(hints.get("flags"), dict) else {}
        follow_up_signals = message_matches.get("follow_up") if isinstance(message_matches.get("follow_up"), list) else []
        looks_like_question = bool(flags.get("looks_like_question"))
        question_or_follow_up = looks_like_question or bool(follow_up_signals)

        system_signals = _match_general_system_help_signals(text)
        if system_signals and question_or_follow_up:
            matched_signals = list(system_signals)
            if looks_like_question:
                matched_signals.append("looks_like_question")
            return _build_route_decision(
                scope_label="non_agora",
                action="refuse",
                confidence=0.99,
                reason="general_it_support",
                matched_signals=_sanitize_matched_signals(matched_signals),
                response_language=response_language,
            )

        technical_signals = [
            signal
            for signal in list(message_matches.get("technical") or [])
            if signal in _FASTPATH_TROUBLESHOOTING_SIGNALS
        ]
        explicit_non_agora_signal = bool(
            system_signals
            or list(message_matches.get("public_info") or [])
            or list(message_matches.get("product_portfolio") or [])
        )
        if technical_signals and question_or_follow_up and not explicit_non_agora_signal:
            matched_signals = list(technical_signals)
            if looks_like_question:
                matched_signals.append("looks_like_question")
            elif follow_up_signals:
                matched_signals.append("follow_up")
            return _build_route_decision(
                scope_label="agora_technical",
                action="rag",
                confidence=0.99,
                reason="technical_troubleshooting_symptom",
                matched_signals=_sanitize_matched_signals(matched_signals),
                response_language=response_language,
            )

    if is_api_semantics_mismatch_message(text):
        matched_signals = ["docs_url", "endpoint_path"]
        if _looks_like_question(text):
            matched_signals.append("looks_like_question")
        return _build_route_decision(
            scope_label="agora_technical",
            action="rag",
            confidence=0.99,
            reason="docs_api_semantics_support",
            matched_signals=matched_signals,
            response_language=response_language,
        )

    if _JOIN_CHANNEL_RE.search(text):
        matched_signals = ["join channel", "channel"]
        if _looks_like_question(text):
            matched_signals.append("looks_like_question")
        return _build_route_decision(
            scope_label="agora_technical",
            action="rag",
            confidence=0.98,
            reason="channel_joining_support",
            matched_signals=matched_signals,
            response_language=response_language,
        )

    product_portfolio_signals = detect_product_portfolio_signals(text)
    if product_portfolio_signals:
        return _build_route_decision(
            scope_label="agora_non_technical",
            action="web_search",
            confidence=0.98,
            reason=PRODUCT_PORTFOLIO_ROUTE_REASON,
            matched_signals=_sanitize_matched_signals(product_portfolio_signals),
            response_language=response_language,
        )

    return None


def _normalized_domain(value: str) -> str:
    parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
    return parsed.netloc.lower().strip()


def authoritative_source_tier(source_url: str) -> str | None:
    normalized_domain = _normalized_domain(_normalize_text(source_url))
    if not normalized_domain:
        return None
    for tier_name, domains in AUTHORITATIVE_WEB_SOURCE_TIERS.items():
        for domain in domains:
            normalized_allowed = _normalized_domain(domain)
            if normalized_domain == normalized_allowed or normalized_domain.endswith(f".{normalized_allowed}"):
                return tier_name
    return None


def citations_use_authoritative_source(citations: list[dict[str, str]] | None = None, *, sources: list[str] | None = None) -> bool:
    for citation in citations or []:
        if authoritative_source_tier(_normalize_text(citation.get("source_url")) or _normalize_text(citation.get("source_path"))):
            return True
    for source_url in sources or []:
        if authoritative_source_tier(source_url):
            return True
    return False


@dataclass(frozen=True)
class _LlmRouteAttempt:
    """Internal result from _llm_route_decision capturing both success and failure metadata."""

    decision: SupportRouteDecision | None
    attempted: bool = True
    failure_type: str | None = None
    failure_source: str | None = None


def _llm_route_decision(
    message: str,
    *,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    response_language: str,
    product: str | None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    has_active_engineer_case: bool = False,
) -> _LlmRouteAttempt:
    profile = resolve_model_profile(INTENT_ROUTER_SCENARIO)
    if not profile_has_invocation_credentials(profile):
        return _LlmRouteAttempt(
            decision=None,
            attempted=True,
            failure_type="missing_credentials",
            failure_source="profile_check",
        )
    system_prompt = resolve_system_prompt("route-system", build_route_system_prompt())
    user_prompt = build_route_user_payload(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        response_language=response_language,
        product=product,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=has_active_engineer_case,
    )
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except LlmInvocationError as exc:
        LOGGER.warning("Intent router responses call failed: %s", exc)
        return _LlmRouteAttempt(
            decision=None,
            attempted=True,
            failure_type="llm_invocation_failed",
            failure_source="responses_api",
        )
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError:
        LOGGER.warning("Intent router response did not return valid JSON")
        return _LlmRouteAttempt(
            decision=None,
            attempted=True,
            failure_type="invalid_json",
            failure_source="responses_api",
        )
    scope_label = _normalize_text(payload.get("scope_label")).lower()
    if scope_label not in {
        "ticket_resolution",
        BILLING_SCOPE_LABEL,
        ENABLEMENT_SCOPE_LABEL,
        "small_talk",
        "non_agora",
        "agora_non_technical",
        "agora_technical",
    }:
        return _LlmRouteAttempt(
            decision=None,
            attempted=True,
            failure_type="invalid_payload",
            failure_source="scope_validation",
        )
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    matched_signals = _sanitize_matched_signals(payload.get("matched_signals"))
    action = _normalize_text(
        payload.get("action")
        or payload.get("execution_action")
        or payload.get("recommended_action")
    ).lower()
    if scope_label == BILLING_SCOPE_LABEL and action not in {"account_suspension", "detailed_invoice", "account_verification", "human_review_required", "refuse", "automation_candidate"}:
        return _LlmRouteAttempt(
            decision=None,
            attempted=True,
            failure_type="invalid_payload",
            failure_source="action_validation",
        )
    if scope_label == ENABLEMENT_SCOPE_LABEL and action not in {ENABLEMENT_ACTION, "automation_candidate"}:
        return _LlmRouteAttempt(
            decision=None,
            attempted=True,
            failure_type="invalid_payload",
            failure_source="action_validation",
        )
    return _LlmRouteAttempt(
        decision=_build_route_decision(
            scope_label=scope_label,
            action=action,
            confidence=max(0.0, min(1.0, confidence)),
            reason=_normalize_text(payload.get("reason")) or "llm_fallback",
            matched_signals=matched_signals,
            response_language=response_language,
            semantic_intent=_normalize_text(payload.get("semantic_intent")) or None,
            automation_eligibility=_normalize_text(payload.get("automation_eligibility")) or None,
            policy_decision=_normalize_text(payload.get("policy_decision")) or None,
            not_automated_reason=_normalize_text(payload.get("not_automated_reason")) or None,
            risk_flags=_sanitize_matched_signals(payload.get("risk_flags")),
            evidence_spans=_sanitize_matched_signals(payload.get("evidence_spans")),
            router_source="llm_semantic",
        ),
        attempted=True,
    )


def _apply_policy_gate(decision: SupportRouteDecision, *, message: str) -> SupportRouteDecision:
    """Apply deterministic safety policy over LLM semantic routing.

    The LLM recommends intent and eligibility; the policy gate has final say on:
    - automation_eligibility (can override LLM from 'eligible' to 'not_eligible')
    - not_automated_reason (must be set when not_eligible)
    - route_family/execution_action (maps billing_review for non-automated billing)
    """

    def _policy_route_decision(**kwargs: Any) -> SupportRouteDecision:
        return _copy_router_audit(
            _build_route_decision(**kwargs),
            source=decision,
        )

    intent = (decision.semantic_intent or "").lower()
    risk_flags_lower = [f.lower() for f in decision.risk_flags]

    # Infer intent from execution_action when semantic_intent is missing (legacy LLM response).
    if not intent and decision.scope_label == BILLING_SCOPE_LABEL:
        action = (decision.execution_action or decision.route or "").lower()
        if action == "account_suspension":
            intent = "billing.account_suspension"
        elif action == "detailed_invoice":
            intent = "billing.detailed_invoice"
    semantic_intent = decision.semantic_intent or intent or None

    if decision.scope_label == ENABLEMENT_SCOPE_LABEL:
        enablement_match = detect_enablement_route(message)
        if enablement_match is None or semantic_intent != ENABLEMENT_SEMANTIC_INTENT:
            return _policy_route_decision(
                scope_label="agora_technical",
                action="rag",
                confidence=decision.confidence,
                reason="enablement_policy_gate_failed",
                matched_signals=list(decision.matched_signals),
                response_language=decision.response_language,
                semantic_intent=None,
                automation_eligibility="not_eligible",
                policy_decision="policy_gate",
                not_automated_reason="explicit_enablement_request_required",
                risk_flags=list(decision.risk_flags),
                evidence_spans=list(decision.evidence_spans),
                router_source=decision.router_source,
            )
        return _policy_route_decision(
            scope_label=ENABLEMENT_SCOPE_LABEL,
            action=ENABLEMENT_ACTION,
            confidence=decision.confidence,
            reason=decision.reason,
            matched_signals=list(decision.matched_signals),
            response_language=decision.response_language,
            semantic_intent=ENABLEMENT_SEMANTIC_INTENT,
            automation_eligibility="eligible",
            policy_decision="policy_gate",
            risk_flags=list(decision.risk_flags),
            evidence_spans=list(decision.evidence_spans),
            router_source=decision.router_source,
        )

    # Non-billing: pass through unchanged.
    if decision.scope_label != BILLING_SCOPE_LABEL:
        return decision

    # Billing: refund/dispute -> never automation.
    if "refund_or_dispute" in intent:
        return _policy_route_decision(
            scope_label=decision.scope_label,
            action="human_review_required",
            confidence=decision.confidence,
            reason=decision.reason,
            matched_signals=list(decision.matched_signals),
            response_language=decision.response_language,
            semantic_intent=semantic_intent,
            automation_eligibility="not_eligible",
            policy_decision="policy_gate",
            not_automated_reason="human_review_required",
            risk_flags=list(decision.risk_flags),
            evidence_spans=list(decision.evidence_spans),
            router_source=decision.router_source,
        )

    # Billing: legal/compensation in risk_flags -> never automation.
    if any(
        flag in {"legal_threat", "compensation", "legal"} or "legal" in flag or "compensation" in flag
        for flag in risk_flags_lower
    ):
        return _policy_route_decision(
            scope_label=decision.scope_label,
            action="human_review_required",
            confidence=decision.confidence,
            reason=decision.reason,
            matched_signals=list(decision.matched_signals),
            response_language=decision.response_language,
            semantic_intent=semantic_intent,
            automation_eligibility="not_eligible",
            policy_decision="policy_gate",
            not_automated_reason="human_review_required",
            risk_flags=list(decision.risk_flags),
            evidence_spans=list(decision.evidence_spans),
            router_source=decision.router_source,
        )

    # Billing: account_verification -> eligible when no refund/dispute/legal risk.
    if "account_verification" in intent:
        verification_risk_signals = {
            "amount_dispute",
            "billing_error",
            "billing_logic",
            "charge_dispute",
            "dispute",
            "legal_threat",
            "metering_issue",
            "overcharge",
            "refund",
            "refund_request",
            "wrong amount",
            "wrong_amount",
        }
        if any(sig in risk_flags_lower for sig in verification_risk_signals):
            return _policy_route_decision(
                scope_label=decision.scope_label,
                action="human_review_required",
                confidence=decision.confidence,
                reason=decision.reason,
                matched_signals=list(decision.matched_signals),
                response_language=decision.response_language,
                semantic_intent=semantic_intent,
                automation_eligibility="not_eligible",
                policy_decision="policy_gate",
                not_automated_reason="human_review_required",
                risk_flags=list(decision.risk_flags),
                evidence_spans=list(decision.evidence_spans),
                router_source=decision.router_source,
            )
        return _policy_route_decision(
            scope_label=decision.scope_label,
            action="account_verification",
            confidence=decision.confidence,
            reason=decision.reason,
            matched_signals=list(decision.matched_signals),
            response_language=decision.response_language,
            semantic_intent=semantic_intent,
            automation_eligibility=decision.automation_eligibility or "eligible",
            policy_decision=decision.policy_decision or "policy_gate",
            not_automated_reason=decision.not_automated_reason,
            risk_flags=list(decision.risk_flags),
            evidence_spans=list(decision.evidence_spans),
            router_source=decision.router_source,
        )

    # Billing: account_suspension -> human review by default.
    if "account_suspension" in intent:
        return _policy_route_decision(
            scope_label=decision.scope_label,
            action="human_review_required",
            confidence=decision.confidence,
            reason=decision.reason,
            matched_signals=list(decision.matched_signals),
            response_language=decision.response_language,
            semantic_intent=semantic_intent,
            automation_eligibility="not_eligible",
            policy_decision="policy_gate",
            not_automated_reason="human_review_required",
            risk_flags=list(decision.risk_flags),
            evidence_spans=list(decision.evidence_spans),
            router_source=decision.router_source,
        )

    # Billing: detailed_invoice -> check for dispute signals, then normalize to the automation action.
    if "detailed_invoice" in intent:
        dispute_signals = {"amount_dispute", "overcharge", "refund", "dispute", "wrong amount"}
        if any(sig in risk_flags_lower for sig in dispute_signals):
            return _policy_route_decision(
                scope_label=decision.scope_label,
                action="human_review_required",
                confidence=decision.confidence,
                reason=decision.reason,
                matched_signals=list(decision.matched_signals),
                response_language=decision.response_language,
                semantic_intent=semantic_intent,
                automation_eligibility="not_eligible",
                policy_decision="policy_gate",
                not_automated_reason="human_review_required",
                risk_flags=list(decision.risk_flags),
                evidence_spans=list(decision.evidence_spans),
                router_source=decision.router_source,
            )
        return _policy_route_decision(
            scope_label=decision.scope_label,
            action="detailed_invoice",
            confidence=decision.confidence,
            reason=decision.reason,
            matched_signals=list(decision.matched_signals),
            response_language=decision.response_language,
            semantic_intent=semantic_intent,
            automation_eligibility=decision.automation_eligibility or "eligible",
            policy_decision=decision.policy_decision or "policy_gate",
            not_automated_reason=decision.not_automated_reason,
            risk_flags=list(decision.risk_flags),
            evidence_spans=list(decision.evidence_spans),
            router_source=decision.router_source,
        )

    # Billing: general or unknown intent -> human review.
    return _policy_route_decision(
        scope_label=decision.scope_label,
        action="human_review_required",
        confidence=decision.confidence,
        reason=decision.reason,
        matched_signals=list(decision.matched_signals),
        response_language=decision.response_language,
        semantic_intent=semantic_intent,
        automation_eligibility="not_eligible",
        policy_decision="policy_gate",
        not_automated_reason="human_review_required",
        risk_flags=list(decision.risk_flags),
        evidence_spans=list(decision.evidence_spans),
        router_source=decision.router_source,
    )


def _copy_router_audit(
    decision: SupportRouteDecision,
    *,
    source: SupportRouteDecision,
) -> SupportRouteDecision:
    return replace(
        decision,
        intent_router_attempted=source.intent_router_attempted,
        intent_router_confidence_threshold=source.intent_router_confidence_threshold,
        intent_router_model_confidence=source.intent_router_model_confidence,
        intent_router_fallback_reason=source.intent_router_fallback_reason,
        intent_router_failure_type=source.intent_router_failure_type,
        intent_router_failure_source=source.intent_router_failure_source,
    )


def _with_intent_router_success_audit(
    decision: SupportRouteDecision,
    *,
    threshold: float,
) -> SupportRouteDecision:
    return replace(
        decision,
        intent_router_attempted=True,
        intent_router_confidence_threshold=threshold,
        intent_router_model_confidence=decision.confidence,
        intent_router_fallback_reason=None,
        intent_router_failure_type=None,
        intent_router_failure_source=None,
    )


def _build_fallback_audit_kwargs(
    *,
    llm_attempt: _LlmRouteAttempt | None,
    threshold: float,
) -> dict[str, Any]:
    """Build audit kwargs for a fallback route decision from an LLM attempt."""
    kwargs: dict[str, Any] = {
        "intent_router_attempted": False,
        "intent_router_confidence_threshold": threshold,
        "intent_router_model_confidence": None,
        "intent_router_fallback_reason": None,
        "intent_router_failure_type": None,
        "intent_router_failure_source": None,
    }
    if llm_attempt is None:
        return kwargs
    kwargs["intent_router_attempted"] = llm_attempt.attempted
    if llm_attempt.decision is not None:
        kwargs["intent_router_model_confidence"] = llm_attempt.decision.confidence
        if llm_attempt.decision.confidence < threshold:
            kwargs["intent_router_fallback_reason"] = "below_confidence_threshold"
    elif llm_attempt.failure_type:
        kwargs["intent_router_fallback_reason"] = llm_attempt.failure_type
        kwargs["intent_router_failure_type"] = llm_attempt.failure_type
        kwargs["intent_router_failure_source"] = llm_attempt.failure_source
    return kwargs


def decide_support_route(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    has_active_engineer_case: bool = False,
    semantic_first: bool = False,
) -> SupportRouteDecision:
    text = _normalize_text(message)
    response_language = _response_language(text)
    if not text:
        return _build_route_decision(
            scope_label="non_agora",
            action="refuse",
            confidence=0.95,
            reason="empty_message",
            matched_signals=[],
            response_language=response_language,
        )

    # ── Semantic-first mode: run LLM before deterministic fast path ──
    if semantic_first:
        llm_attempt = _llm_route_decision(
            text,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            response_language=response_language,
            product=product,
            latest_assistant_message=latest_assistant_message,
            current_ticket_status=current_ticket_status,
            has_active_engineer_case=has_active_engineer_case,
        )
        threshold = _safe_float_env(
            "INTENT_ROUTER_CONFIDENCE_THRESHOLD",
            DEFAULT_INTENT_ROUTER_CONFIDENCE_THRESHOLD,
        )
        if llm_attempt.decision is not None and llm_attempt.decision.confidence >= threshold:
            return _apply_policy_gate(
                _with_intent_router_success_audit(llm_attempt.decision, threshold=threshold),
                message=text,
            )
        # Fall through to heuristic + fallback with audit tracking.
        heuristic_decision = _heuristic_route_decision(
            text,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            response_language=response_language,
            latest_assistant_message=latest_assistant_message,
            current_ticket_status=current_ticket_status,
            has_active_engineer_case=has_active_engineer_case,
        )
        if heuristic_decision is not None:
            return replace(
                heuristic_decision,
                **_build_fallback_audit_kwargs(llm_attempt=llm_attempt, threshold=threshold),
            )
        audit_kwargs = _build_fallback_audit_kwargs(llm_attempt=llm_attempt, threshold=threshold)
        return _build_route_decision(
            scope_label="agora_technical",
            action="rag",
            confidence=0.75,
            reason="conservative_agora_technical_fallback",
            matched_signals=[],
            response_language=response_language,
            router_source="conservative_fallback",
            **audit_kwargs,
        )

    heuristic_decision = _heuristic_route_decision(
        text,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        response_language=response_language,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=has_active_engineer_case,
    )
    if heuristic_decision is not None:
        return heuristic_decision

    llm_attempt = _llm_route_decision(
        text,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        response_language=response_language,
        product=product,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=has_active_engineer_case,
    )
    threshold = _safe_float_env(
        "INTENT_ROUTER_CONFIDENCE_THRESHOLD",
        DEFAULT_INTENT_ROUTER_CONFIDENCE_THRESHOLD,
    )
    if llm_attempt.decision is not None and llm_attempt.decision.confidence >= threshold:
        return _apply_policy_gate(
            _with_intent_router_success_audit(llm_attempt.decision, threshold=threshold),
            message=text,
        )

    audit_kwargs = _build_fallback_audit_kwargs(llm_attempt=llm_attempt, threshold=threshold)
    return _build_route_decision(
        scope_label="agora_technical",
        action="rag",
        confidence=0.75,
        reason="conservative_agora_technical_fallback",
        matched_signals=[],
        response_language=response_language,
        router_source="conservative_fallback",
        **audit_kwargs,
    )


def build_refusal_answer(decision: SupportRouteDecision) -> str:
    if decision.response_language == "zh":
        return (
            "我是 Agora 的 Support AI，主要回答 Agora 相关问题。"
            "这个问题不在我的支持范围内。"
            "如果你有 Agora 产品、SDK、API 或集成相关问题，我可以继续帮你。"
        )
    return (
        "I'm Agora's support AI and mainly answer Agora-related questions. "
        "This request is outside my support scope. "
        "If you have an Agora product, SDK, API, or integration question, I can help with that."
    )


def build_controlled_response(decision: SupportRouteDecision) -> str:
    if _normalize_text(decision.reason).lower() == "gratitude_acknowledgement":
        if decision.response_language == "zh":
            return "不客气。如果这个工单还有后续问题，直接在这里补充，我会继续协助。"
        return "You're welcome. If you need anything else for this ticket, send the next detail here and I'll continue helping."
    if decision.response_language == "zh":
        if decision.route_family == "general_chat":
            return (
                "我是 Agora 的 support agent，主要负责 Agora 相关支持。"
                "像天气或闲聊这类问题我就不展开了；如果你有 Agora 技术问题，我可以继续帮你。"
            )
        return (
            "我是 Agora 的 support agent，主要负责 Agora 相关支持。"
            "这个问题更像通用技术帮助，不适合使用 Agora 文档来回答；如果你遇到 Agora SDK 或 API 问题，我可以继续协助。"
        )
    if decision.route_family == "general_chat":
        return (
            "I'm Agora's support agent and mainly handle Agora-related support. "
            "I won't use Agora docs for small talk, but I can help if you have an Agora technical question."
        )
    return (
        "I'm Agora's support agent and mainly handle Agora-related support. "
        "This looks like general technical help, so I won't answer it with Agora docs. "
        "If you have an Agora SDK or API issue, I can help with that."
    )


def _search_fallback_answer(response_language: str) -> str:
    if response_language == "zh":
        return (
            "我是 Agora 的 support agent，主要负责回答 Agora 相关的问题。"
            "这个 Agora 非技术信息我暂时无法联网核实，请优先查看 Agora 官网或投资者关系页面。"
        )
    return (
        "I am Agora support agent and mainly handle Agora-related questions. "
        "I couldn't verify that Agora public information right now. Please check Agora's official site or investor relations page."
    )


def _dedupe_citations(citations: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in citations:
        source_url = _normalize_text(item.get("source_url"))
        title = _normalize_text(item.get("title") or item.get("heading"))
        key = (source_url, title)
        if not source_url or key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                "source_url": source_url,
                "heading": title or source_url,
                "source_path": source_url,
            }
        )
    return unique


def _extract_web_search_payload(response_payload: dict[str, Any]) -> tuple[str, list[dict[str, str]], list[str]]:
    output_text = _sanitize_web_search_answer_text(response_payload.get("output_text"))
    citations: list[dict[str, str]] = []
    sources: list[str] = []
    output_items = response_payload.get("output") if isinstance(response_payload.get("output"), list) else []
    for item in output_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            for source in action.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                url = _normalize_text(source.get("url"))
                title = _normalize_text(source.get("title"))
                if url and url not in sources:
                    sources.append(url)
                if url:
                    citations.append({"source_url": url, "title": title or url})
        if item.get("type") != "message":
            continue
        for content_item in item.get("content") or []:
            if not isinstance(content_item, dict):
                continue
            if content_item.get("type") == "output_text" and not output_text:
                output_text = _sanitize_web_search_answer_text(content_item.get("text"))
            for annotation in content_item.get("annotations") or []:
                if not isinstance(annotation, dict):
                    continue
                if annotation.get("type") != "url_citation":
                    continue
                url = _normalize_text(annotation.get("url"))
                title = _normalize_text(annotation.get("title"))
                if not url:
                    citation_payload = annotation.get("url_citation") if isinstance(annotation.get("url_citation"), dict) else {}
                    url = _normalize_text(citation_payload.get("url"))
                    title = title or _normalize_text(citation_payload.get("title"))
                if url:
                    citations.append({"source_url": url, "title": title or url})
                    if url not in sources:
                        sources.append(url)
    unique_citations = _dedupe_citations(citations)
    return _sanitize_web_search_answer_text(output_text), unique_citations, sources


def _openai_web_search(
    question: str,
    *,
    response_language: str,
    allowed_domains: list[str] | None,
    route_reason: str | None = None,
) -> WebSearchAnswer | None:
    profile = resolve_model_profile(WEB_SEARCH_SCENARIO)
    if not profile.api_key:
        return None
    tool: dict[str, Any] = {
        "type": "web_search",
        "external_web_access": True,
    }
    if allowed_domains:
        tool["filters"] = {"allowed_domains": allowed_domains}
    fallback_system_prompt = build_web_search_system_prompt(
        response_language=response_language,
        official_only=bool(allowed_domains),
        route_reason=route_reason,
    )
    if response_language == "en":
        prompt_key = "web-search-product-portfolio" if route_reason == PRODUCT_PORTFOLIO_ROUTE_REASON else "web-search"
        system_prompt = resolve_system_prompt(prompt_key, fallback_system_prompt)
    else:
        system_prompt = fallback_system_prompt
    user_prompt = build_web_search_user_prompt(question=question, route_reason=route_reason)
    extra_payload = {
        "tools": [tool],
        "include": ["web_search_call.action.sources"],
    }
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            extra_payload=extra_payload,
        )
    except LlmInvocationError as exc:
        LOGGER.warning("Agora public info web search failed: %s", exc)
        return None
    text, citations, sources = _extract_web_search_payload(response.raw_payload or {})
    if not text:
        return None
    return WebSearchAnswer(
        answer=text,
        sources=sources,
        citations=citations,
        search_used=True,
    )


def search_agora_public_info(
    question: str,
    *,
    response_language: str,
    route_reason: str | None = None,
) -> WebSearchAnswer:
    normalized_reason = _normalize_text(route_reason).lower()
    official_only_product_portfolio = normalized_reason == PRODUCT_PORTFOLIO_ROUTE_REASON
    allowed_domains = (
        list(OFFICIAL_AGORA_PRODUCT_PORTFOLIO_DOMAINS)
        if official_only_product_portfolio
        else list(OFFICIAL_AGORA_DOMAINS)
    )
    primary = _openai_web_search(
        question,
        response_language=response_language,
        allowed_domains=allowed_domains,
        route_reason=normalized_reason or None,
    )
    if primary and primary.answer.strip() and "INSUFFICIENT" not in primary.answer.upper():
        return primary
    if official_only_product_portfolio:
        if primary and primary.answer.strip():
            return primary
        return WebSearchAnswer(
            answer=_search_fallback_answer(response_language),
            sources=[],
            citations=[],
            search_used=False,
        )
    secondary = _openai_web_search(
        question,
        response_language=response_language,
        allowed_domains=None,
        route_reason=normalized_reason or None,
    )
    if secondary and secondary.answer.strip():
        return secondary
    return WebSearchAnswer(
        answer=_search_fallback_answer(response_language),
        sources=[],
        citations=[],
        search_used=False,
    )


def _route_audit_kwargs_from_decision(decision: SupportRouteDecision) -> dict[str, Any]:
    """Extract audit fields from a route decision for propagation into SupportResolution."""
    return {
        "router_source": decision.router_source,
        "intent_router_attempted": decision.intent_router_attempted,
        "intent_router_confidence_threshold": decision.intent_router_confidence_threshold,
        "intent_router_model_confidence": decision.intent_router_model_confidence,
        "intent_router_fallback_reason": decision.intent_router_fallback_reason,
        "intent_router_failure_type": decision.intent_router_failure_type,
        "intent_router_failure_source": decision.intent_router_failure_source,
    }


def resolve_support_message(
    message: str,
    *,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    has_active_engineer_case: bool = False,
    rag_answerer: Callable[[str], tuple[str, float, list[str], list[dict[str, str]], bool]] | None = None,
    decision: SupportRouteDecision | None = None,
) -> SupportResolution:
    decision = decision or decide_support_route(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=has_active_engineer_case,
    )
    if decision.route_family == "billing_review":
        return SupportResolution(
            answer=build_refusal_answer(decision),
            confidence=round(decision.confidence, 2),
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="human_review_required",
            scope_label=decision.scope_label,
            route_family=decision.route_family,
            execution_action=decision.execution_action,
            tooling_profile=decision.tooling_profile,
            route_reason=decision.reason,
            route_confidence=decision.confidence,
            search_used=False,
            matched_signals=list(decision.matched_signals),
            evidence_summary={
                "billing_action": str(decision.execution_action or decision.route),
                "semantic_intent": decision.semantic_intent or "",
                "not_automated_reason": decision.not_automated_reason or "",
                "risk_flags": list(decision.risk_flags),
                "evidence_spans": list(decision.evidence_spans),
            },
            **_route_audit_kwargs_from_decision(decision),
        )
    if decision.route_family == BILLING_ROUTE_FAMILY and decision.scope_label == ENABLEMENT_SCOPE_LABEL:
        enablement_result = build_enablement_automation_result(
            message=message,
            ticket_id=ticket_id or "{{ticket_id}}",
            account_case_id=f"AC-{ticket_id}" if ticket_id else "{{account_case_id}}",
            customer_email=customer_id,
            generate_customer_reply=not bool(ticket_id),
        )
        email_send_result = (
            send_enablement_internal_email(enablement_result.internal_email)
            if enablement_result.internal_email
            else {"status": "not_ready", "reason": "missing_required_fields"}
        )
        return SupportResolution(
            answer=enablement_result.customer_reply,
            confidence=round(decision.confidence, 2),
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="workflow",
            scope_label=decision.scope_label,
            route_family=decision.route_family,
            execution_action=decision.execution_action,
            tooling_profile=decision.tooling_profile,
            route_reason=decision.reason,
            route_confidence=decision.confidence,
            search_used=False,
            matched_signals=list(decision.matched_signals),
            evidence_summary={
                "enablement_missing_fields": list(enablement_result.missing_fields),
                "enablement_collected_fields": dict(enablement_result.collected_fields),
                "enablement_requires_human_review": bool(
                    getattr(enablement_result, "requires_human_review", False)
                ),
                "enablement_internal_email_send_status": str(email_send_result.get("status") or ""),
                "enablement_internal_email_send_reason": str(email_send_result.get("reason") or ""),
                **({"enablement_internal_email": dict(enablement_result.internal_email)} if enablement_result.internal_email else {}),
            },
            **_route_audit_kwargs_from_decision(decision),
        )
    if decision.route_family == BILLING_ROUTE_FAMILY:
        billing_action = str(decision.execution_action or decision.route)
        billing_result = build_billing_automation_result(
            action=billing_action,
            message=message,
            ticket_id=ticket_id,
            customer_email=customer_id,
            requester=customer_id,
            # The ticket-backed path owns the managed LLM extraction. Keep the
            # ticket-less compatibility helper deterministic for callers that
            # only need a route preview and have no publication context.
            use_llm_field_extractor=billing_action == "detailed_invoice" and bool(ticket_id),
            generate_customer_reply=not bool(ticket_id),
        )
        email_send_result = (
            send_billing_internal_email(billing_result.internal_email)
            if billing_result.internal_email
            else {"status": "not_ready", "reason": "missing_required_fields"}
        )
        return SupportResolution(
            answer=billing_result.customer_reply,
            confidence=round(decision.confidence, 2),
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="workflow",
            scope_label=decision.scope_label,
            route_family=decision.route_family,
            execution_action=decision.execution_action,
            tooling_profile=decision.tooling_profile,
            route_reason=decision.reason,
            route_confidence=decision.confidence,
            search_used=False,
            matched_signals=list(decision.matched_signals),
            evidence_summary={
                "billing_action": billing_action,
                "billing_missing_fields": list(billing_result.missing_fields),
                "billing_collected_fields": dict(billing_result.collected_fields),
                "billing_requires_human_review": bool(billing_result.requires_human_review),
                "billing_internal_email_send_status": str(email_send_result.get("status") or ""),
                "billing_internal_email_send_reason": str(email_send_result.get("reason") or ""),
                **(
                    {"billing_internal_email": dict(billing_result.internal_email)}
                    if billing_result.internal_email
                    else {}
                ),
                **(
                    {"billing_field_extraction": billing_result.field_extraction.audit_payload()}
                    if billing_result.field_extraction is not None
                    else {}
                ),
            },
            **_route_audit_kwargs_from_decision(decision),
        )
    if decision.execution_action == "resolve_ticket":
        return SupportResolution(
            answer=build_resolved_confirmation_reply(message),
            confidence=1.0,
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="workflow",
            scope_label=decision.scope_label,
            route_family=decision.route_family,
            execution_action=decision.execution_action,
            tooling_profile=decision.tooling_profile,
            route_reason=decision.reason,
            route_confidence=decision.confidence,
            search_used=False,
            matched_signals=list(decision.matched_signals),
            **_route_audit_kwargs_from_decision(decision),
        )
    if decision.execution_action == "refuse":
        answer = build_refusal_answer(decision)
        return SupportResolution(
            answer=answer,
            confidence=round(decision.confidence, 2),
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="refuse",
            scope_label=decision.scope_label,
            route_family=decision.route_family,
            execution_action=decision.execution_action,
            tooling_profile=decision.tooling_profile,
            route_reason=decision.reason,
            route_confidence=decision.confidence,
            search_used=False,
            matched_signals=list(decision.matched_signals),
            **_route_audit_kwargs_from_decision(decision),
        )
    if decision.execution_action == "controlled_response":
        return SupportResolution(
            answer=build_controlled_response(decision),
            confidence=round(decision.confidence, 2),
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="controlled_response",
            scope_label=decision.scope_label,
            route_family=decision.route_family,
            execution_action=decision.execution_action,
            tooling_profile=decision.tooling_profile,
            route_reason=decision.reason,
            route_confidence=decision.confidence,
            search_used=False,
            matched_signals=list(decision.matched_signals),
            **_route_audit_kwargs_from_decision(decision),
        )
    if decision.execution_action == "web_search":
        search_result = search_agora_public_info(
            message,
            response_language=decision.response_language,
            route_reason=decision.reason,
        )
        return SupportResolution(
            answer=search_result.answer,
            confidence=round(decision.confidence, 2),
            sources=list(search_result.sources),
            citations=[dict(item) for item in search_result.citations],
            needs_engineer_guidance=False,
            answer_route="web_search",
            scope_label=decision.scope_label,
            route_family=decision.route_family,
            execution_action=decision.execution_action,
            tooling_profile=decision.tooling_profile,
            route_reason=decision.reason,
            route_confidence=decision.confidence,
            search_used=search_result.search_used,
            matched_signals=list(decision.matched_signals),
            **_route_audit_kwargs_from_decision(decision),
        )
    if rag_answerer is None:
        raise ValueError("rag_answerer is required for Agora technical routing")
    answer, confidence, sources, citations, needs_engineer_guidance = rag_answerer(message)
    return SupportResolution(
        answer=answer,
        confidence=confidence,
        sources=list(sources),
        citations=[dict(item) for item in citations],
        needs_engineer_guidance=needs_engineer_guidance,
        answer_route="rag",
        scope_label=decision.scope_label,
        route_family=decision.route_family,
        execution_action=decision.execution_action,
        tooling_profile=decision.tooling_profile,
        route_reason=decision.reason,
        route_confidence=decision.confidence,
        search_used=False,
        matched_signals=list(decision.matched_signals),
        **_route_audit_kwargs_from_decision(decision),
    )
