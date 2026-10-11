"""Shared, server-owned facts contract for Hermes customer replies.

The model may choose wording, but it never owns the facts, greeting, or
publication policy.  This module keeps those values in a small versioned
record that can be carried from Work through Persona and the delivery ledger.
"""

from __future__ import annotations

import re
from typing import Any

HERMES_CUSTOMER_REPLY_BASIS_KIND = "hermes_customer_reply_basis_v1"
HERMES_CUSTOMER_REPLY_BASIS_VERSION = "hermes_customer_reply_basis_v1"
HERMES_REPLY_OWNER = "hermes_persona"

_PAYMENT_PATTERNS = (
    re.compile(r"\b(?:credit|debit|bank|card)\s+(?:card|number|details|information)\b", re.I),
    re.compile(r"\b(?:cvv|cvc|security code|password|one[- ]time code|otp)\b", re.I),
    re.compile(r"支付(?:宝|信息|凭据)|银行卡|信用卡|验证码|密码"),
)
_INTERNAL_PATTERNS = (
    re.compile(r"\b(?:prompt|workflow|work result|reply basis|route|tool call|internal ticket)\b", re.I),
    re.compile(r"\b(?:automation[- ]persona|account[- ]reply[- ]job|hermes persona)\b", re.I),
)
_HANDOFF_REASK_PATTERNS = (
    re.compile(r"\b(?:please|kindly)\s+(?:provide|send|share)\b", re.I),
    re.compile(r"请(?:提供|发送|补充|再次提供)"),
)
_GREETING_RE = re.compile(r"^\s*hi\s+[^,\n]+,\s*(?:\n\s*\n)?", re.I)


class HermesReplyBasisError(ValueError):
    """Raised when a basis or draft violates the server reply contract."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = str(code or "hermes_reply_basis_invalid")
        self.detail = str(detail or self.code)
        super().__init__(self.detail)


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def build_hermes_customer_reply_basis(
    *,
    source_turn_id: str,
    direction: str,
    route: str,
    stage: str,
    collected_facts: Any = None,
    missing_fields: Any = None,
    trusted_conclusion: Any = None,
    allowed_claims: Any = None,
    forbidden_topics: Any = None,
    publication_policy: str | None = None,
    greeting_name: str | None = None,
    draft_language: str | None = None,
    source_route: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical basis used by all Hermes customer replies."""
    turn = str(source_turn_id or "").strip()
    if not turn:
        raise HermesReplyBasisError("source_turn_required")
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in {"automation", "investigation"}:
        raise HermesReplyBasisError("direction_invalid")
    normalized_route = str(route or source_route or "").strip() or "hermes_case"
    policy = str(publication_policy or ("auto" if normalized_direction == "automation" else "manual")).strip().lower()
    if policy not in {"auto", "manual"}:
        raise HermesReplyBasisError("publication_policy_invalid")
    facts = dict(collected_facts) if isinstance(collected_facts, dict) else {}
    conclusion = dict(trusted_conclusion) if isinstance(trusted_conclusion, dict) else {}
    basis: dict[str, Any] = {
        "kind": HERMES_CUSTOMER_REPLY_BASIS_KIND,
        "basis_version": HERMES_CUSTOMER_REPLY_BASIS_VERSION,
        "reply_owner": HERMES_REPLY_OWNER,
        "source_turn_id": turn,
        "source_direction": normalized_direction,
        "direction": normalized_direction,
        "source_route": normalized_route,
        "route": normalized_route,
        "stage": str(stage or "").strip() or "reply",
        "collected_facts": facts,
        "missing_fields": _clean_list(missing_fields),
        "trusted_conclusion": conclusion,
        "allowed_claims": _clean_list(allowed_claims),
        "forbidden_topics": _clean_list(forbidden_topics),
        "publication_policy": policy,
        "greeting_name": str(greeting_name or "Customer").strip() or "Customer",
    }
    if draft_language:
        basis["draft_language"] = str(draft_language).strip()
    if extra:
        # Extra data is intentionally shallow and cannot overwrite contract
        # identity fields.
        for key, value in extra.items():
            if key not in basis and str(key).strip():
                basis[str(key)] = value
    return basis


def basis_from_work_result(
    *,
    turn: dict[str, Any],
    work_result: dict[str, Any] | None,
    stage: str = "automation_result",
) -> dict[str, Any]:
    """Normalize route Work output into the common basis shape."""
    result = work_result if isinstance(work_result, dict) else {}
    existing = result.get("reply_basis")
    if isinstance(existing, dict) and existing.get("kind") == HERMES_CUSTOMER_REPLY_BASIS_KIND:
        return dict(existing)
    route = str(result.get("route") or turn.get("route") or "").strip()
    direction = str(turn.get("direction") or "automation").strip().lower()
    collected = result.get("collected_fields") or result.get("collected_facts") or {}
    missing = result.get("missing_fields") or []
    status = str(result.get("status") or "").strip()
    conclusion = {
        key: result[key]
        for key in (
            "status", "reason", "outcome", "internal_email_send_status",
            "internal_email_send_reason", "answer", "references", "progress",
            "followup_kind", "reply_kind", "message_action_reply",
        )
        if key in result and result[key] not in (None, "")
    }
    allowed = result.get("allowed_claims") or []
    if not allowed:
        allowed = [f"state:{status}"] if status else []
    return build_hermes_customer_reply_basis(
        source_turn_id=str(turn.get("turn_id") or ""),
        direction=direction,
        route=route,
        stage=stage,
        collected_facts=collected,
        missing_fields=missing,
        trusted_conclusion=conclusion,
        allowed_claims=allowed,
        forbidden_topics=result.get("forbidden_topics") or ["payment_information", "internal_implementation"],
        publication_policy="auto" if direction == "automation" else "manual",
        greeting_name=str((turn.get("input_snapshot") or {}).get("greeting_name") or "Customer")
        if isinstance(turn.get("input_snapshot"), dict)
        else "Customer",
        draft_language=result.get("draft_language"),
        extra={"source_work_status": status} if status else None,
    )


def _basis_field_values(basis: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("collected_facts", "trusted_conclusion"):
        value = basis.get(key)
        if isinstance(value, dict):
            values.extend(str(item).strip().lower() for item in value.values() if str(item).strip())
    return values


def validate_customer_reply_draft(content: str, basis: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on safety/fact conflicts while allowing natural wording."""
    text = str(content or "").strip()
    if not text:
        raise HermesReplyBasisError("content_required")
    if not isinstance(basis, dict) or basis.get("kind") != HERMES_CUSTOMER_REPLY_BASIS_KIND:
        raise HermesReplyBasisError("basis_required")
    for pattern in _PAYMENT_PATTERNS:
        if pattern.search(text):
            raise HermesReplyBasisError("payment_information_request")
    for pattern in _INTERNAL_PATTERNS:
        if pattern.search(text):
            raise HermesReplyBasisError("internal_implementation_exposed")
    forbidden = " ".join(_clean_list(basis.get("forbidden_topics"))).lower()
    if forbidden and any(token in text.lower() for token in ("payment information", "credit card", "银行卡")):
        raise HermesReplyBasisError("forbidden_topic")
    if str(basis.get("source_direction") or basis.get("direction") or "") == "automation":
        if str(basis.get("trusted_conclusion", {}).get("status") or "") == "human_review_required":
            raise HermesReplyBasisError("human_review_reply_forbidden")
    greeting_count = len(re.findall(r"\bhi\s+[^,\n]+,", text, re.I))
    if greeting_count > 1:
        raise HermesReplyBasisError("duplicate_greeting")
    missing = {item.lower() for item in _clean_list(basis.get("missing_fields"))}
    collected_value = basis.get("collected_facts") or basis.get("collected_fields")
    collected = {
        str(key).strip().lower()
        for key in (collected_value.keys() if isinstance(collected_value, dict) else _clean_list(collected_value))
        if str(key).strip()
    }
    if missing and collected and missing.intersection(collected):
        raise HermesReplyBasisError("basis_missing_collected_conflict")
    # A handoff basis may never ask for new customer data.
    conclusion = basis.get("trusted_conclusion") if isinstance(basis.get("trusted_conclusion"), dict) else {}
    if conclusion.get("handoff") is True and any(pattern.search(text) for pattern in _HANDOFF_REASK_PATTERNS):
        raise HermesReplyBasisError("handoff_reasks_information")
    return {
        "decision": "pass",
        "reply_owner": HERMES_REPLY_OWNER,
        "basis_version": HERMES_CUSTOMER_REPLY_BASIS_VERSION,
        "basis_values": _basis_field_values(basis),
    }


def project_greeting(content: str, greeting_name: str | None) -> str:
    """Project exactly one English salutation onto a draft."""
    normalized = str(content or "").strip()
    expected = f"Hi {str(greeting_name or 'Customer').strip() or 'Customer'},"
    normalized = _GREETING_RE.sub("", normalized, count=1).strip()
    return f"{expected}\n\n{normalized}" if normalized else expected

