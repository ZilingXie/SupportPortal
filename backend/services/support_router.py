from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import INTENT_ROUTER_SCENARIO, WEB_SEARCH_SCENARIO, resolve_model_profile
from backend.services.prompts.web_search import build_web_search_system_prompt, build_web_search_user_prompt
from backend.services.support_router_prompt import build_route_system_prompt, build_route_user_payload

LOGGER = logging.getLogger(__name__)

DEFAULT_INTENT_ROUTER_TIMEOUT_SECONDS = 8.0
DEFAULT_INTENT_ROUTER_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_OPENAI_WEB_SEARCH_TIMEOUT_SECONDS = 30.0
OFFICIAL_AGORA_DOMAINS = [
    "agora.io",
    "www.agora.io",
    "docs.agora.io",
    "docs-preview.agora.io",
    "investor.agora.io",
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

    def __post_init__(self) -> None:
        route_family, execution_action, tooling_profile = _route_contract_for_scope(
            scope_label=self.scope_label,
            action=self.execution_action or self.route,
            reason=self.reason,
        )
        object.__setattr__(self, "route_family", self.route_family or route_family)
        object.__setattr__(self, "execution_action", self.execution_action or execution_action)
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
        }


def _route_contract_for_scope(*, scope_label: str, action: str, reason: str) -> tuple[str, str, str]:
    clean_scope = _normalize_text(scope_label).lower()
    normalized_action = _normalize_text(action).lower()

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
        return "general_chat", "refuse", "no_agora_docs_refusal"
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
    sanitized = _normalize_text(text)
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
    return _normalize_text(sanitized)


def _response_language(message: str) -> str:
    return "zh" if _CJK_RE.search(str(message or "")) else "en"


def _looks_like_question(message: str) -> bool:
    text = _normalize_text(message)
    if not text:
        return False
    return bool(_QUESTION_PREFIX_RE.search(text) or text.endswith("?"))


def _heuristic_route_decision(
    message: str,
    *,
    response_language: str,
) -> SupportRouteDecision | None:
    text = _normalize_text(message)
    if not text:
        return None

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


def _llm_route_decision(
    message: str,
    *,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    response_language: str,
) -> SupportRouteDecision | None:
    profile = resolve_model_profile(INTENT_ROUTER_SCENARIO)
    if not profile.api_key:
        return None
    system_prompt = build_route_system_prompt()
    user_prompt = build_route_user_payload(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        response_language=response_language,
    )
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except LlmInvocationError as exc:
        LOGGER.warning("Intent router responses call failed: %s", exc)
        return None
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError:
        LOGGER.warning("Intent router response did not return valid JSON")
        return None
    scope_label = _normalize_text(payload.get("scope_label")).lower()
    if scope_label not in {"small_talk", "non_agora", "agora_non_technical", "agora_technical"}:
        return None
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    matched_signals = _sanitize_matched_signals(payload.get("matched_signals"))
    return _build_route_decision(
        scope_label=scope_label,
        action="",
        confidence=max(0.0, min(1.0, confidence)),
        reason=_normalize_text(payload.get("reason")) or "llm_fallback",
        matched_signals=matched_signals,
        response_language=response_language,
    )


def decide_support_route(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
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

    heuristic_decision = _heuristic_route_decision(
        text,
        response_language=response_language,
    )
    if heuristic_decision is not None:
        return heuristic_decision

    llm_decision = _llm_route_decision(
        text,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        response_language=response_language,
    )
    threshold = _safe_float_env(
        "INTENT_ROUTER_CONFIDENCE_THRESHOLD",
        DEFAULT_INTENT_ROUTER_CONFIDENCE_THRESHOLD,
    )
    if llm_decision and llm_decision.confidence >= threshold:
        return llm_decision

    return _build_route_decision(
        scope_label="agora_technical",
        action="rag",
        confidence=0.75,
        reason="conservative_agora_technical_fallback",
        matched_signals=[],
        response_language=response_language,
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
    system_prompt = build_web_search_system_prompt(
        response_language=response_language,
        official_only=bool(allowed_domains),
    )
    user_prompt = build_web_search_user_prompt(question=question)
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


def search_agora_public_info(question: str, *, response_language: str) -> WebSearchAnswer:
    primary = _openai_web_search(
        question,
        response_language=response_language,
        allowed_domains=list(OFFICIAL_AGORA_DOMAINS),
    )
    if primary and primary.answer.strip() and "INSUFFICIENT" not in primary.answer.upper():
        return primary
    secondary = _openai_web_search(
        question,
        response_language=response_language,
        allowed_domains=None,
    )
    if secondary and secondary.answer.strip():
        return secondary
    return WebSearchAnswer(
        answer=_search_fallback_answer(response_language),
        sources=[],
        citations=[],
        search_used=False,
    )


def resolve_support_message(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    rag_answerer: Callable[[str], tuple[str, float, list[str], list[dict[str, str]], bool]] | None = None,
    decision: SupportRouteDecision | None = None,
) -> SupportResolution:
    decision = decision or decide_support_route(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
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
        )
    if decision.execution_action == "web_search":
        search_result = search_agora_public_info(message, response_language=decision.response_language)
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
    )
