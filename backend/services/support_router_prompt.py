from __future__ import annotations

import re
from typing import Any

from backend.services.prompts.router import (
    build_router_system_prompt as build_router_system_prompt_v2,
    build_router_user_prompt as build_router_user_prompt_v2,
)
from backend.services.support_products import get_support_product_label
from backend.services.ticket_resolution import (
    has_resolution_negative_marker,
    latest_assistant_reply_supports_resolution,
    matched_resolution_markers,
)

SYSTEM_TERMS = (
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
SMALL_TALK_TERMS = (
    "今天天气",
    "天气怎么样",
    "weather",
    "hello",
    "hi",
    "hey",
    "你好",
    "早上好",
    "晚上好",
)
PUBLIC_INFO_TERMS = (
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
TECHNICAL_TERMS = (
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
    "communication",
    "live broadcasting",
    "live_broadcasting",
    "host",
    "audience",
    "viewer",
    "viewer count",
    "viewer analytics",
    "notifications",
    "notification",
    "signaling",
    "individual recording",
    "composite",
    "transcoding",
    "transcodingconfig",
    "authorization header",
    "authorization",
    "auth",
    "parameter mismatch",
    "docs-based rag",
    "benchmark",
    "test set",
    "automated test set",
    "auth benchmark",
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
    "crash",
    "crashed",
    "disconnect",
    "disconnected",
    "network quality",
    "lag",
    "laggy",
)
AGORA_SIGNALS = (
    "agora",
    "agora.io",
    "声网",
    "convoai",
)
FOLLOW_UP_TERMS = (
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
QUESTION_PREFIX_RE = re.compile(r"^(what|why|how|where|when|who|can|could|would|is|are|do|does|did)\b", re.IGNORECASE)
CJK_RE = re.compile(r"[\u3400-\u9fff]")
JOIN_CHANNEL_RE = re.compile(r"\b(join|leave|create|publish|subscribe)\b.{0,24}\bchannel\b", re.IGNORECASE)
COMPARISON_RE = re.compile(r"\b(difference|compare|comparison|versus|vs\.?)\b", re.IGNORECASE)
CHOICE_RE = re.compile(r"\b(should i use|right fit|which .* use|choose|avoid|better than)\b", re.IGNORECASE)

ROUTE_FEW_SHOT_EXAMPLES = (
    {
        "message": "What's the real difference between COMMUNICATION and LIVE_BROADCASTING?",
        "hints": {"technical": ["communication", "live broadcasting"], "flags": ["comparison_pattern", "looks_like_question"]},
        "output": {
            "scope_label": "agora_technical",
            "confidence": 0.95,
            "reason": "product_mode_comparison",
            "matched_signals": ["communication", "live broadcasting", "comparison_pattern"],
        },
    },
    {
        "message": "Would Notifications help me build viewer analytics?",
        "hints": {"technical": ["notifications", "viewer analytics"], "flags": ["looks_like_question"]},
        "output": {
            "scope_label": "agora_technical",
            "confidence": 0.93,
            "reason": "feature_selection_support",
            "matched_signals": ["notifications", "viewer analytics"],
        },
    },
    {
        "message": "If compliance requires one file per participant, should I avoid composite?",
        "hints": {"technical": ["compliance", "composite", "individual recording"], "flags": ["choice_pattern", "looks_like_question"]},
        "output": {
            "scope_label": "agora_technical",
            "confidence": 0.94,
            "reason": "recording_strategy_support",
            "matched_signals": ["composite", "individual recording", "choice_pattern"],
        },
    },
    {
        "message": "Why are parameter mismatch questions good for testing a docs-based RAG?",
        "hints": {"technical": ["parameter mismatch", "docs-based rag", "auth benchmark"], "flags": ["docs_eval_anchor", "looks_like_question"]},
        "output": {
            "scope_label": "agora_technical",
            "confidence": 0.91,
            "reason": "docs_eval_auth_reasoning",
            "matched_signals": ["parameter mismatch", "docs-based rag", "docs_eval_anchor"],
        },
    },
    {
        "message": "Who's the CEO of Agora?",
        "hints": {"agora": ["agora"], "public_info": ["ceo"]},
        "output": {
            "scope_label": "agora_non_technical",
            "confidence": 0.98,
            "reason": "agora_company_info",
            "matched_signals": ["agora", "ceo"],
        },
    },
    {
        "message": "今天天气怎么样",
        "hints": {"small_talk": ["天气怎么样"]},
        "output": {
            "scope_label": "small_talk",
            "confidence": 0.99,
            "reason": "small_talk_detected",
            "matched_signals": ["天气怎么样"],
        },
    },
    {
        "message": "got it, thanks",
        "hints": {
            "resolution": {
                "message_signals": ["got it", "thanks"],
                "latest_reply_supports_resolution": True,
                "has_active_engineer_case": False,
                "current_ticket_status": "communicating",
            }
        },
        "output": {
            "scope_label": "ticket_resolution",
            "confidence": 0.99,
            "reason": "customer_confirmed_resolved",
            "matched_signals": ["got it", "thanks", "latest_support_reply"],
        },
    },
    {
        "message": "I got black screen issue, what should I do?",
        "hints": {"technical": ["black screen"], "flags": ["looks_like_question"]},
        "output": {
            "scope_label": "agora_technical",
            "confidence": 0.86,
            "reason": "technical_troubleshooting_symptom",
            "matched_signals": ["black screen"],
        },
    },
    {
        "message": "My computer blue-screened. What should I do?",
        "hints": {"system": ["blue screen"]},
        "output": {
            "scope_label": "non_agora",
            "confidence": 0.97,
            "reason": "general_it_support",
            "matched_signals": ["blue screen"],
        },
    },
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _contains_any(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = _normalize_text(text).lower()
    normalized_text = lowered.replace("_", " ")
    matches: list[str] = []
    for term in terms:
        normalized_term = _normalize_text(term).lower()
        if not normalized_term:
            continue
        if CJK_RE.search(normalized_term):
            matched = normalized_term in lowered or normalized_term in normalized_text
        else:
            escaped = re.escape(normalized_term).replace(r"\ ", r"\s+")
            matched = (
                re.search(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])", lowered, re.IGNORECASE) is not None
                or re.search(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])", normalized_text, re.IGNORECASE) is not None
            )
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
    return "?" in compact or bool(QUESTION_PREFIX_RE.match(compact))


def build_route_prompt_hints(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    has_active_engineer_case: bool = False,
) -> dict[str, Any]:
    normalized_message = _normalize_text(message)
    context_text = _context_text(ticket_subject, ticket_context)
    resolution_signals = matched_resolution_markers(normalized_message)
    message_matches = {
        "agora": _contains_any(normalized_message, AGORA_SIGNALS),
        "technical": _contains_any(normalized_message, TECHNICAL_TERMS),
        "public_info": _contains_any(normalized_message, PUBLIC_INFO_TERMS),
        "follow_up": _contains_any(normalized_message, FOLLOW_UP_TERMS),
        "small_talk": _contains_any(normalized_message, SMALL_TALK_TERMS),
        "system": _contains_any(normalized_message, SYSTEM_TERMS),
    }
    context_matches = {
        "agora": _contains_any(context_text, AGORA_SIGNALS),
        "technical": _contains_any(context_text, TECHNICAL_TERMS),
        "public_info": _contains_any(context_text, PUBLIC_INFO_TERMS),
        "follow_up": _contains_any(context_text, FOLLOW_UP_TERMS),
        "small_talk": _contains_any(context_text, SMALL_TALK_TERMS),
        "system": _contains_any(context_text, SYSTEM_TERMS),
    }
    docs_eval_anchor = bool(
        set(message_matches["technical"] + context_matches["technical"])
        & {"parameter mismatch", "docs-based rag", "benchmark", "test set", "automated test set", "auth benchmark", "auth", "token"}
    )
    flags = {
        "looks_like_question": _looks_like_question(normalized_message),
        "join_channel_pattern": bool(JOIN_CHANNEL_RE.search(normalized_message)),
        "comparison_pattern": bool(COMPARISON_RE.search(normalized_message)),
        "choice_pattern": bool(CHOICE_RE.search(normalized_message)),
        "docs_eval_anchor": docs_eval_anchor,
        "has_agora_brand": bool(message_matches["agora"] or context_matches["agora"]),
        "customer_resolution_candidate": bool(resolution_signals) and not has_resolution_negative_marker(normalized_message),
        "latest_reply_supports_resolution": latest_assistant_reply_supports_resolution(latest_assistant_message),
        "has_active_engineer_case": bool(has_active_engineer_case),
    }
    return {
        "message_matches": message_matches,
        "context_matches": context_matches,
        "flags": flags,
        "resolution": {
            "message_signals": resolution_signals,
            "latest_reply_supports_resolution": bool(flags["latest_reply_supports_resolution"]),
            "has_active_engineer_case": bool(has_active_engineer_case),
            "current_ticket_status": _normalize_text(current_ticket_status).lower() or None,
        },
    }


def build_route_user_payload(
    message: str,
    *,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    response_language: str,
    product: str | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    has_active_engineer_case: bool = False,
) -> str:
    selected_product = get_support_product_label(product)
    payload = {
        "message": _normalize_text(message),
        "ticket_subject": _normalize_text(ticket_subject),
        "selected_product": selected_product or "Generic Agora Support",
        "ticket_context": list(ticket_context or [])[-6:],
        "response_language": response_language,
        "hints": build_route_prompt_hints(
            message,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            latest_assistant_message=latest_assistant_message,
            current_ticket_status=current_ticket_status,
            has_active_engineer_case=has_active_engineer_case,
        ),
    }
    return build_router_user_prompt_v2(payload=payload)


def build_route_system_prompt() -> str:
    return build_router_system_prompt_v2(route_examples=list(ROUTE_FEW_SHOT_EXAMPLES))
