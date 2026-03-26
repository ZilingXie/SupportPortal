from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
import urllib.parse
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
_SYSTEM_TERMS = (
    "windows",
    "macbook",
    "iphone",
    "电脑",
    "laptop",
    "printer",
    "office",
    "outlook",
    "excel",
    "蓝屏",
    "blue screen",
    "blue-screen",
    "wifi",
    "wi-fi",
    "battery",
    "draining fast",
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
    "profile",
    "role",
    "roles",
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
    clean_reason = _normalize_text(reason).lower()

    if clean_scope == "agora_technical":
        actual_action = normalized_action if normalized_action in {"rag", "refuse"} else "rag"
        tooling = "agora_docs_only" if actual_action == "rag" else "no_agora_docs_refusal"
        return "agora_docs_rag", actual_action, tooling
    if clean_scope == "agora_non_technical":
        actual_action = normalized_action if normalized_action in {"web_search", "controlled_response", "refuse"} else "web_search"
        if actual_action == "web_search":
            tooling = "official_web_search"
        elif actual_action == "controlled_response":
            tooling = "no_agora_docs_controlled"
        else:
            tooling = "no_agora_docs_refusal"
        return "web_company_info", actual_action, tooling
    if clean_scope == "small_talk":
        return "general_chat", "controlled_response", "no_agora_docs_controlled"
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


def _contains_any(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = _normalize_text(text).lower()
    matches: list[str] = []
    for term in terms:
        normalized_term = _normalize_text(term).lower()
        if not normalized_term:
            continue
        if _CJK_RE.search(normalized_term):
            matched = normalized_term in lowered
        else:
            escaped = re.escape(normalized_term).replace(r"\ ", r"\s+")
            matched = re.search(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])", lowered, re.IGNORECASE) is not None
        if matched and term not in matches:
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
        "- non_agora: non-technical, unrelated request that should not be answered with Agora docs\n"
        "- agora_non_technical: Agora-related public/company/policy/pricing question, not technical support\n"
        "- agora_technical: technical support / integration / troubleshooting question, including device, system, SDK, API, configuration, token, callback, networking, or debugging questions even when the message does not explicitly mention Agora\n"
        "Use brand terms, technical terms, device/system support signals, and the existing ticket context.\n"
        "If the message is technical, choose agora_technical even without an explicit Agora brand mention.\n"
        "If the message is ambiguous and non-technical with no clear Agora signal, choose non_agora.\n"
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
    return _build_route_decision(
        scope_label=scope_label,
        action=route,
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

    context_text = _context_text(ticket_subject, ticket_context)
    current_small_talk = _contains_any(text, _SMALL_TALK_TERMS)
    agora_signals = _contains_any(text, _AGORA_SIGNALS)
    technical_signals = _contains_any(text, _TECHNICAL_TERMS)
    public_info_signals = _contains_any(text, _PUBLIC_INFO_TERMS)
    follow_up_signals = _contains_any(text, _FOLLOW_UP_TERMS)
    system_signals = _contains_any(text, _SYSTEM_TERMS)
    context_agora_signals = _contains_any(context_text, _AGORA_SIGNALS + _TECHNICAL_TERMS)
    context_technical_signals = _contains_any(context_text, _TECHNICAL_TERMS + _SYSTEM_TERMS)

    if _JOIN_CHANNEL_RE.search(text):
        technical_signals.append("join channel")

    technical_triggers = list(dict.fromkeys([*technical_signals, *system_signals]))

    if technical_triggers:
        matched = list(dict.fromkeys([*agora_signals, *technical_triggers]))
        reason = "agora_technical_signals" if agora_signals or context_agora_signals else "technical_support_signals"
        return _build_route_decision(
            scope_label="agora_technical",
            action="rag",
            confidence=0.95,
            reason=reason,
            matched_signals=matched,
            response_language=response_language,
        )

    if context_technical_signals and (follow_up_signals or _looks_like_question(text)):
        matched = list(dict.fromkeys([*context_agora_signals, *context_technical_signals, *follow_up_signals]))
        reason = "agora_context_followup" if context_agora_signals else "technical_context_followup"
        return _build_route_decision(
            scope_label="agora_technical",
            action="rag",
            confidence=0.87,
            reason=reason,
            matched_signals=matched,
            response_language=response_language,
        )

    if public_info_signals and agora_signals:
        matched = list(dict.fromkeys([*agora_signals, *public_info_signals] or ["agora_public_info"]))
        return _build_route_decision(
            scope_label="agora_non_technical",
            action="web_search",
            confidence=0.93,
            reason="agora_public_info",
            matched_signals=matched,
            response_language=response_language,
        )

    if current_small_talk and not technical_triggers and not public_info_signals and not agora_signals:
        return _build_route_decision(
            scope_label="small_talk",
            action="controlled_response",
            confidence=0.98,
            reason="small_talk_detected",
            matched_signals=current_small_talk,
            response_language=response_language,
        )

    if agora_signals and not technical_triggers and _looks_like_question(text):
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

    if not technical_triggers and not public_info_signals and not current_small_talk:
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
        scope_label="non_agora",
        action="refuse",
        confidence=0.75,
        reason="conservative_non_agora_fallback",
        matched_signals=[],
        response_language=response_language,
    )


def build_refusal_answer(decision: SupportRouteDecision) -> str:
    if decision.response_language == "zh":
        return "我是 Agora 的 support agent，主要负责回答 Agora 相关的问题。这个问题不在我的支持范围内，所以我先不回答。"
    return "I'm Agora's support agent and mainly handle Agora-related questions. I can't answer that request."


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
