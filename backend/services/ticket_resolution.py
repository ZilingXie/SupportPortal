from __future__ import annotations

import re
from typing import Any

from backend.services.investigation_flow import RESOLVED_STATUS, normalize_ticket_status

WORKFLOW_ACTION_ANSWER_CUSTOMER = "answer_customer"
ASSISTANT_MESSAGE_SOURCE_ENGINEER_GUIDANCE = "engineer_guidance"
RESOLVED_CONFIRMATION_MAX_CHARS = 160
RESOLVED_CONFIRMATION_POSITIVE_MARKERS = (
    "got it",
    "understood",
    "that helps",
    "helpful",
    "solved",
    "resolved",
    "it worked",
    "works now",
    "working now",
    "fixed",
    "thanks",
    "thank you",
    "thx",
    "明白了",
    "知道了",
    "了解了",
    "已经解决",
    "解决了",
    "谢谢",
    "感谢",
    "收到",
    "可以了",
    "好了",
)
RESOLVED_CONFIRMATION_NEGATIVE_MARKERS = (
    "?",
    "？",
    "still",
    "but",
    "however",
    "another question",
    "one more",
    "issue",
    "problem",
    "error",
    "not resolved",
    "not working",
    "doesn't work",
    "didn't work",
    "failed",
    "fail",
    "unresolved",
    "again",
    "black screen again",
    "未解决",
    "没解决",
    "还有问题",
    "还有个问题",
    "还有一个问题",
    "仍然",
    "还是",
    "但是",
    "不过",
    "错误",
    "报错",
    "不行",
    "不好用",
)
NON_SUBSTANTIVE_ANSWER_REASONS = {
    "rag_insufficient_evidence",
    "rag_service_error",
    "rag_unavailable",
    "rag_processing_timeout",
    "rag_post_check_insufficient",
    "rag_post_check_error",
    "deadline_exhausted",
    "route_timeout",
}
_RESOLVED_CONFIRMATION_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def is_cjk_text(value: str) -> bool:
    return bool(_RESOLVED_CONFIRMATION_CJK_RE.search(str(value or "")))


def matched_resolution_markers(message: str) -> list[str]:
    cleaned = clean_text(message)
    if not cleaned:
        return []
    lowered = cleaned.lower()
    matches: list[str] = []
    for marker in RESOLVED_CONFIRMATION_POSITIVE_MARKERS:
        if marker in lowered and marker not in matches:
            matches.append(marker)
    return matches


def has_resolution_negative_marker(message: str) -> bool:
    lowered = clean_text(message).lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in RESOLVED_CONFIRMATION_NEGATIVE_MARKERS)


def is_explicit_resolved_confirmation(message: str) -> bool:
    cleaned = clean_text(message)
    if not cleaned or len(cleaned) > RESOLVED_CONFIRMATION_MAX_CHARS:
        return False
    if not matched_resolution_markers(cleaned):
        return False
    if has_resolution_negative_marker(cleaned):
        return False
    return True


def latest_assistant_reply_supports_resolution(message: dict[str, Any] | None) -> bool:
    if not isinstance(message, dict):
        return False
    if clean_text(message.get("role")).lower() != "assistant":
        return False
    if not clean_text(message.get("content")):
        return False
    if bool(message.get("supports_customer_resolution")):
        return True
    if clean_text(message.get("assistant_message_source")).lower() == ASSISTANT_MESSAGE_SOURCE_ENGINEER_GUIDANCE:
        return True
    if clean_text(message.get("workflow_action")) != WORKFLOW_ACTION_ANSWER_CUSTOMER:
        return False
    if clean_text(message.get("answer_route")).lower() == "refuse":
        return False
    if clean_text(message.get("route_reason")).lower() in NON_SUBSTANTIVE_ANSWER_REASONS:
        return False
    return True


def is_customer_resolved_confirmation_candidate(
    message: str,
    *,
    latest_assistant_message: dict[str, Any] | None,
    current_ticket_status: str | None,
) -> bool:
    if normalize_ticket_status(current_ticket_status) == RESOLVED_STATUS:
        return False
    if not latest_assistant_reply_supports_resolution(latest_assistant_message):
        return False
    return is_explicit_resolved_confirmation(message)


def build_resolved_confirmation_reply(message: str) -> str:
    if is_cjk_text(message):
        return (
            "感谢你的回复，很高兴这些信息对你有帮助。"
            "我会将这个工单标记为已解决。"
            "如果你后续还有其他问题，欢迎再创建一个新工单。"
        )
    return (
        "Thanks for your response. I'm glad to hear the information provided was helpful. "
        "I'll mark this case as resolved. If you have any further questions, please create a new ticket."
    )
