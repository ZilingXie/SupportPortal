from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

DEFAULT_INTENT_ROUTER_MODEL = "gpt-4o-mini"
DEFAULT_INTENT_ROUTER_TIMEOUT_SECONDS = 3.0
DEFAULT_INTENT_ROUTER_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_OPENAI_WEB_SEARCH_MODEL = "gpt-5"
DEFAULT_OPENAI_WEB_SEARCH_TIMEOUT_SECONDS = 12.0
OFFICIAL_AGORA_DOMAINS = [
    "agora.io",
    "www.agora.io",
    "docs.agora.io",
    "docs-preview.agora.io",
    "investor.agora.io",
]
_SYSTEM_TERMS = (
    "windows",
    "macbook",
    "电脑",
    "laptop",
    "printer",
    "office",
    "outlook",
    "excel",
    "蓝屏",
    "router",
)
_SMALL_TALK_TERMS = (
    "今天天气",
    "天气怎么样",
    "weather",
    "hello",
    "hi",
    "hey",
    "thanks",
    "thank you",
    "你好",
    "早上好",
    "晚上好",
)
_PUBLIC_INFO_TERMS = (
    "ceo",
    "founder",
    "headquarters",
    "stock",
    "investor",
    "revenue",
    "company",
    "pricing",
    "price",
    "billing",
    "policy",
    "plan",
    "legal",
    "上市",
    "股价",
    "创始人",
    "总部",
    "定价",
    "价格",
    "政策",
    "公司",
)
_TECHNICAL_TERMS = (
    "token",
    "appid",
    "app id",
    "app certificate",
    "sdk",
    "api",
    "rtc",
    "rtm",
    "channel",
    "uid",
    "webhook",
    "callback",
    "recording",
    "cloud recording",
    "screen share",
    "publish",
    "subscribe",
    "join channel",
    "join a channel",
    "leave channel",
    "latency",
    "packet loss",
    "buildtoken",
    "声网",
    "频道",
    "加入频道",
    "离开频道",
    "回调",
    "录制",
    "连麦",
    "鉴权",
    "排障",
)
_AGORA_SIGNALS = (
    "agora",
    "agora.io",
    "声网",
    "convoai",
)
_FOLLOW_UP_TERMS = (
    "still",
    "again",
    "same issue",
    "same problem",
    "doesn't work",
    "does not work",
    "not work",
    "not working",
    "failed",
    "issue",
    "problem",
    "it still",
    "还是不行",
    "还是有问题",
    "还是失败",
)
_QUESTION_PREFIX_RE = re.compile(r"^(what|why|how|where|when|who|can|could|would|is|are|do|does|did)\b", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_JOIN_CHANNEL_RE = re.compile(r"\b(join|leave|create|publish|subscribe)\b.{0,24}\bchannel\b", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+[^)]*)\)", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)
_URLISH_LABEL_RE = re.compile(r"^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s]*)?$", re.IGNORECASE)


@dataclass(frozen=True)
class SupportRouteDecision:
    scope_label: str
    route: str
    confidence: float
    reason: str
    matched_signals: list[str] = field(default_factory=list)
    response_language: str = "en"


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
            "route_reason": self.route_reason,
            "route_confidence": round(float(self.route_confidence), 4),
            "search_used": bool(self.search_used),
            "matched_signals": list(self.matched_signals),
        }


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


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


def _contains_any(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    matches: list[str] = []
    for term in terms:
        if term.lower() in lowered and term not in matches:
            matches.append(term)
    return matches


def _context_text(ticket_subject: str | None, ticket_context: list[dict[str, str]] | None) -> str:
    parts: list[str] = []
    subject = _normalize_text(ticket_subject)
    if subject:
        parts.append(subject)
    for item in list(ticket_context or [])[-6:]:
        content = _normalize_text(item.get("content") if isinstance(item, dict) else "")
        if content:
            parts.append(content)
    return "\n".join(parts).strip()


def _looks_like_question(text: str) -> bool:
    compact = _normalize_text(text)
    if not compact:
        return False
    return "?" in compact or bool(_QUESTION_PREFIX_RE.match(compact))


def _llm_route_decision(
    message: str,
    *,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    response_language: str,
) -> SupportRouteDecision | None:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    model = (os.getenv("INTENT_ROUTER_MODEL") or DEFAULT_INTENT_ROUTER_MODEL).strip()
    timeout_seconds = _safe_float_env(
        "INTENT_ROUTER_TIMEOUT_SECONDS",
        DEFAULT_INTENT_ROUTER_TIMEOUT_SECONDS,
    )
    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:  # pragma: no cover - dependency failure is environment-specific
        LOGGER.warning("Intent router unavailable because langchain-openai import failed: %s", exc)
        return None

    system_prompt = (
        "Classify the user's latest support message into exactly one route.\n"
        "Labels:\n"
        "- small_talk: greeting, thanks, weather, chit-chat\n"
        "- non_agora: not related to Agora or its SDK/products\n"
        "- agora_non_technical: Agora-related public/company/policy/pricing question, not technical support\n"
        "- agora_technical: Agora SDK/API/configuration/troubleshooting/integration question\n"
        "Use brand terms, Agora-specific technical terms, and the existing ticket context.\n"
        "If the message is ambiguous and there is no clear Agora signal, choose non_agora.\n"
        "Return JSON only with keys: scope_label, route, confidence, reason, matched_signals.\n"
        "Valid routes are refuse, web_search, rag.\n"
    )
    user_prompt = json.dumps(
        {
            "message": _normalize_text(message),
            "ticket_subject": _normalize_text(ticket_subject),
            "ticket_context": list(ticket_context or [])[-6:],
            "response_language": response_language,
        },
        ensure_ascii=False,
    )
    try:
        llm = ChatOpenAI(
            model=model,
            temperature=0,
            api_key=api_key,
            timeout=timeout_seconds,
        )
        response = llm.invoke([("system", system_prompt), ("user", user_prompt)])
        raw = getattr(response, "content", "")
        if isinstance(raw, list):
            text = "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in raw
            ).strip()
        else:
            text = str(raw or "").strip()
        payload = json.loads(text)
    except Exception as exc:
        LOGGER.warning("Intent router LLM fallback failed: %s", exc)
        return None
    scope_label = _normalize_text(payload.get("scope_label")).lower()
    route = _normalize_text(payload.get("route")).lower()
    if scope_label not in {"small_talk", "non_agora", "agora_non_technical", "agora_technical"}:
        return None
    if route not in {"refuse", "web_search", "rag"}:
        return None
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    matched_signals = [str(item).strip() for item in payload.get("matched_signals") or [] if str(item).strip()]
    return SupportRouteDecision(
        scope_label=scope_label,
        route=route,
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
    lowered = text.lower()
    response_language = _response_language(text)
    if not text:
        return SupportRouteDecision(
            scope_label="non_agora",
            route="refuse",
            confidence=0.95,
            reason="empty_message",
            matched_signals=[],
            response_language=response_language,
        )

    current_small_talk = _contains_any(lowered, _SMALL_TALK_TERMS)
    if current_small_talk:
        return SupportRouteDecision(
            scope_label="small_talk",
            route="refuse",
            confidence=0.98,
            reason="small_talk_detected",
            matched_signals=current_small_talk,
            response_language=response_language,
        )

    context_text = _context_text(ticket_subject, ticket_context)
    context_lowered = context_text.lower()
    agora_signals = _contains_any(lowered, _AGORA_SIGNALS)
    technical_signals = _contains_any(lowered, _TECHNICAL_TERMS)
    public_info_signals = _contains_any(lowered, _PUBLIC_INFO_TERMS)
    follow_up_signals = _contains_any(lowered, _FOLLOW_UP_TERMS)
    system_signals = _contains_any(lowered, _SYSTEM_TERMS)
    context_agora_signals = _contains_any(context_lowered, _AGORA_SIGNALS + _TECHNICAL_TERMS)

    if _JOIN_CHANNEL_RE.search(text):
        technical_signals.append("join channel")

    if system_signals and not agora_signals and not technical_signals:
        return SupportRouteDecision(
            scope_label="non_agora",
            route="refuse",
            confidence=0.95,
            reason="non_agora_system_issue",
            matched_signals=system_signals,
            response_language=response_language,
        )

    if public_info_signals and (agora_signals or context_agora_signals or "agora" in lowered):
        matched = list(dict.fromkeys([*agora_signals, *public_info_signals] or ["agora_public_info"]))
        return SupportRouteDecision(
            scope_label="agora_non_technical",
            route="web_search",
            confidence=0.93,
            reason="agora_public_info",
            matched_signals=matched,
            response_language=response_language,
        )

    if technical_signals and (agora_signals or _JOIN_CHANNEL_RE.search(text) or context_agora_signals):
        matched = list(dict.fromkeys([*agora_signals, *technical_signals]))
        return SupportRouteDecision(
            scope_label="agora_technical",
            route="rag",
            confidence=0.94,
            reason="agora_technical_signals",
            matched_signals=matched,
            response_language=response_language,
        )

    if context_agora_signals and (follow_up_signals or _looks_like_question(text)):
        matched = list(dict.fromkeys([*context_agora_signals, *follow_up_signals]))
        return SupportRouteDecision(
            scope_label="agora_technical",
            route="rag",
            confidence=0.87,
            reason="agora_context_followup",
            matched_signals=matched,
            response_language=response_language,
        )

    if agora_signals and public_info_signals:
        matched = list(dict.fromkeys([*agora_signals, *public_info_signals]))
        return SupportRouteDecision(
            scope_label="agora_non_technical",
            route="web_search",
            confidence=0.9,
            reason="agora_public_info",
            matched_signals=matched,
            response_language=response_language,
        )

    if agora_signals and not technical_signals and _looks_like_question(text):
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

    if not agora_signals and not technical_signals:
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

    return SupportRouteDecision(
        scope_label="non_agora",
        route="refuse",
        confidence=0.75,
        reason="conservative_non_agora_fallback",
        matched_signals=[],
        response_language=response_language,
    )


def build_refusal_answer(decision: SupportRouteDecision) -> str:
    if decision.response_language == "zh":
        return "我是 Agora 的 support agent，主要负责回答 Agora 相关的问题。这个问题不在我的支持范围内，所以我先不回答。"
    return "I'm Agora's support agent and mainly handle Agora-related questions. I can't answer that request."


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
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    model = (os.getenv("OPENAI_WEB_SEARCH_MODEL") or DEFAULT_OPENAI_WEB_SEARCH_MODEL).strip()
    timeout_seconds = _safe_float_env(
        "OPENAI_WEB_SEARCH_TIMEOUT_SECONDS",
        DEFAULT_OPENAI_WEB_SEARCH_TIMEOUT_SECONDS,
    )
    tool: dict[str, Any] = {
        "type": "web_search",
        "external_web_access": True,
    }
    if allowed_domains:
        tool["filters"] = {"allowed_domains": allowed_domains}
    guidance = (
        "You are Agora's support agent handling non-technical Agora questions. "
        f"Answer in {'Chinese' if response_language == 'zh' else 'English'}. "
        "Use web search and keep the answer concise and factual. "
        "Prefer official Agora sources. "
    )
    if allowed_domains:
        guidance += "If official sources do not contain the answer, reply exactly INSUFFICIENT."
    else:
        guidance += (
            "When official Agora sources do not contain the answer, you may supplement with authoritative public sources. "
        )
    prompt = f"{guidance}\nQuestion: {question}"
    payload = {
        "model": model,
        "tools": [tool],
        "include": ["web_search_call.action.sources"],
        "input": prompt,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Agora public info web search failed: %s", exc)
        return None
    text, citations, sources = _extract_web_search_payload(raw_payload if isinstance(raw_payload, dict) else {})
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
    if decision.route == "refuse":
        answer = build_refusal_answer(decision)
        return SupportResolution(
            answer=answer,
            confidence=round(decision.confidence, 2),
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="refuse",
            scope_label=decision.scope_label,
            route_reason=decision.reason,
            route_confidence=decision.confidence,
            search_used=False,
            matched_signals=list(decision.matched_signals),
        )
    if decision.route == "web_search":
        search_result = search_agora_public_info(message, response_language=decision.response_language)
        return SupportResolution(
            answer=search_result.answer,
            confidence=round(decision.confidence, 2),
            sources=list(search_result.sources),
            citations=[dict(item) for item in search_result.citations],
            needs_engineer_guidance=False,
            answer_route="web_search",
            scope_label=decision.scope_label,
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
        route_reason=decision.reason,
        route_confidence=decision.confidence,
        search_used=False,
        matched_signals=list(decision.matched_signals),
    )
