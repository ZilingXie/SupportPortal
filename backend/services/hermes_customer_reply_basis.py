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
    re.compile(
        r"\b(?:payment\s+method|billing\s+address|credit|debit|bank|card)"
        r"(?:\s+(?:card|number|details|information|method|address))?\b",
        re.I,
    ),
    re.compile(r"\b(?:cvv|cvc|security code|password|one[- ]time code|otp)\b", re.I),
    re.compile(r"支付(?:宝|信息|凭据)|银行卡|信用卡|验证码|密码"),
)
_INTERNAL_PATTERNS = (
    re.compile(r"\b(?:prompt|workflow|work result|reply basis|tool call|internal ticket)\b", re.I),
    re.compile(r"\b(?:automation[- ]persona|account[- ]reply[- ]job|hermes persona)\b", re.I),
)
_HANDOFF_REASK_PATTERNS = (
    re.compile(r"\b(?:please|kindly)\s+(?:provide|send|share)\b", re.I),
    re.compile(r"请(?:提供|发送|补充|再次提供)"),
)
_GREETING_RE = re.compile(r"^\s*hi\s+[^,\n]+,\s*(?:\n\s*\n)?", re.I)
_REQUEST_VERB_RE = re.compile(
    r"\b(?:please|kindly|need|needs|required|required to|provide|send|share|"
    r"confirm|enter|submit|tell us|let us know)\b|请(?:提供|发送|补充|确认|告知)",
    re.I,
)
_NEGATED_REQUEST_RE = re.compile(
    r"\b(?:no|without|don't|do not|doesn't|does not|not required|not needed|"
    r"unnecessary|never)\b|无需|不用|不需要|无需提供",
    re.I,
)
_RECOVERY_CLAIM_RE = re.compile(
    r"\b(?:account|access|service)\b[^.?!\n]{0,40}\b(?:restored|recovered|"
    r"reactivated|reopened|enabled|activated)\b|\b(?:restored|recovered|"
    r"reactivated|reopened|enabled|activated)\s+(?:your\s+)?account\b",
    re.I,
)
_TIME_COMMITMENT_RE = re.compile(
    r"\b(?:within|in)\s+(?:the\s+next\s+)?(?:24|48|72)\s+hours?\b|24小时|48小时|72小时",
    re.I,
)
_FIELD_ALIASES: dict[str, set[str]] = {
    "name": {"name", "full name", "customer name", "姓名", "名字"},
    "payment_method": {"payment method", "付款方式", "支付方式"},
    "billing_address": {"billing address", "账单地址"},
    "office_address": {"office address", "办公地址"},
    "contact_number": {"contact number", "official contact number", "phone number", "联系电话"},
    "contact_email": {"contact email", "official contact email", "email address", "联系邮箱"},
    "tax_id": {"tax id", "tax number", "tax identification number", "vat number", "vat id", "税号", "税务登记号"},
    "passport_number": {"passport number", "passport id", "护照号"},
    "social_security_number": {"social security number", "ssn", "社保号"},
}


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
            "status", "outcome", "internal_email_send_status",
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
        extra={
            key: status if key == "source_work_status" else result.get(key)
            for key in ("source_work_status", "compatibility_account_reply_job_id")
            if (key == "source_work_status" and status)
            or (key != "source_work_status" and result.get(key) not in (None, ""))
        },
    )


def basis_for_persona_turn(
    *, turn: dict[str, Any], binding: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Return the server-owned basis that the Persona must receive.

    Work results are authoritative for automation turns. Investigation reply
    turns use the reviewed binding conclusion, while the turn linkage is
    validated by the tool entry before a draft can be saved.
    """
    direction = str(turn.get("direction") or "").strip().lower()
    turn_kind = str(turn.get("turn_kind") or "normal").strip().lower()
    work_result = turn.get("work_result") if isinstance(turn.get("work_result"), dict) else {}
    basis = None
    if direction == "automation" and work_result:
        basis = basis_from_work_result(turn=turn, work_result=work_result)
    investigation = (
        binding.get("investigation")
        if isinstance(binding, dict) and isinstance(binding.get("investigation"), dict)
        else {}
    )
    if direction == "investigation" and turn_kind != "investigation_reply":
        if str(investigation.get("recorded_turn_id") or "") != str(turn.get("turn_id") or ""):
            return None
        basis = basis_from_work_result(
            turn=turn,
            work_result={},
            stage="investigation_result",
        )
    if direction == "investigation" and turn_kind == "investigation_reply":
        source_turn_id = str(work_result.get("source_turn_id") or "").strip()
        source_turn = (
            binding.get("investigation_source_turn")
            if isinstance(binding, dict) and isinstance(binding.get("investigation_source_turn"), dict)
            else {}
        )
        if not source_turn_id or str(source_turn.get("turn_id") or "") != source_turn_id:
            return None
        source_result = source_turn.get("result") if isinstance(source_turn.get("result"), dict) else {}
        if str(source_result.get("continued_turn_id") or "") != str(turn.get("turn_id") or ""):
            return None
        if str(investigation.get("recorded_turn_id") or "") != source_turn_id:
            return None
        basis = basis_from_work_result(
            turn=turn,
            work_result={},
            stage="investigation_result",
        )
    if basis is None:
        return None
    if str(turn.get("direction") or "").strip().lower() == "investigation" and investigation:
        conclusion = dict(basis.get("trusted_conclusion") or {})
        for key in ("summary", "evidence", "known_facts", "unknowns", "next_steps"):
            if investigation.get(key) not in (None, "", [], {}):
                conclusion[key] = investigation[key]
        basis["trusted_conclusion"] = conclusion
    return basis


def _basis_field_values(basis: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "collected_facts",
        "trusted_conclusion",
        "allowed_claims",
        "allowed_commitments",
        "missing_field_labels",
        "confirmation_anchor",
        "contact_commitment_anchor",
    ):
        value = basis.get(key)
        if isinstance(value, dict):
            for item in value.values():
                if isinstance(item, (list, tuple, set)):
                    values.extend(str(entry).strip().lower() for entry in item if str(entry).strip())
                else:
                    values.extend(str(item).strip().lower() for item in (item,) if str(item).strip())
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, dict):
                    values.extend(str(entry).strip().lower() for entry in item.values() if str(entry).strip())
                elif str(item).strip():
                    values.append(str(item).strip().lower())
        elif str(value).strip():
            values.append(str(value).strip().lower())
    return values


def _authorized_claim_values(basis: dict[str, Any]) -> list[str]:
    """Collect only server-authorized claim/commitment text.

    Customer-provided collected facts are deliberately excluded. They can be
    restated, but they cannot authorize a recovery state or a time promise.
    """
    values: list[str] = []
    for key in ("allowed_claims", "allowed_commitments"):
        value = basis.get(key)
        if isinstance(value, dict):
            values.extend(str(item).strip().lower() for item in value.values() if str(item).strip())
        elif isinstance(value, (list, tuple, set)):
            values.extend(str(item).strip().lower() for item in value if str(item).strip())
        elif str(value).strip():
            values.append(str(value).strip().lower())
    conclusion = basis.get("trusted_conclusion")
    if isinstance(conclusion, dict):
        for key in (
            "status", "outcome", "reason", "reply_intent", "account_status",
            "recovery_status", "completion_status", "time_commitment",
            "contact_commitment", "handoff",
        ):
            value = conclusion.get(key)
            if isinstance(value, (list, tuple, set)):
                values.extend(str(item).strip().lower() for item in value if str(item).strip())
            elif value not in (None, ""):
                values.append(str(value).strip().lower())
    return values


def _basis_collected_keys(basis: dict[str, Any]) -> set[str]:
    value = basis.get("collected_facts") or basis.get("collected_fields")
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key).strip().lower() for key in value if str(key).strip())
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("field") or "").strip().lower()
            if label:
                keys.add(label)
    return keys


def _basis_missing_keys(basis: dict[str, Any]) -> set[str]:
    return {str(item).strip().lower() for item in _clean_list(basis.get("missing_fields")) if str(item).strip()}


def _field_key_from_text(text: str) -> str | None:
    lowered = str(text or "").strip().lower()
    for key, aliases in _FIELD_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return key
    return None


def _has_negation_before(text: str, start: int) -> bool:
    prefix = str(text or "")[max(0, start - 48):start]
    return bool(_NEGATED_REQUEST_RE.search(prefix))


def _contains_authorized_claim(text: str, basis: dict[str, Any], pattern: re.Pattern[str]) -> bool:
    match = pattern.search(text)
    if not match:
        return True
    if _has_negation_before(text, match.start()):
        return True
    normalized_values = _authorized_claim_values(basis)
    # The claim is authorized only when its semantic marker appears in a
    # server-owned conclusion/commitment.  Customer names and arbitrary
    # collected values cannot authorize a state transition.
    marker = match.group(0).lower()
    markers = {
        token
        for token in ("restored", "recovered", "reactivated", "reopened", "enabled", "activated")
        if token in marker
    }
    if pattern is _TIME_COMMITMENT_RE:
        return any(
            re.search(r"(?:within|in)\s+(?:the\s+next\s+)?(?:24|48|72)\s+hours?", value, re.I)
            or any(token in value for token in ("24", "48", "72"))
            for value in normalized_values
        )
    return any(any(token in value for token in markers) for value in normalized_values)


def validate_customer_reply_draft(content: str, basis: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on safety/fact conflicts while allowing natural wording."""
    text = str(content or "").strip()
    if not text:
        raise HermesReplyBasisError("content_required")
    if not isinstance(basis, dict) or basis.get("kind") != HERMES_CUSTOMER_REPLY_BASIS_KIND:
        raise HermesReplyBasisError("basis_required")
    for pattern in _PAYMENT_PATTERNS:
        match = pattern.search(text)
        if match and _REQUEST_VERB_RE.search(text[max(0, match.start() - 64):match.end() + 24]) and not _has_negation_before(text, match.start()):
            raise HermesReplyBasisError("payment_information_request")
    for pattern in _INTERNAL_PATTERNS:
        if pattern.search(text):
            raise HermesReplyBasisError("internal_implementation_exposed")
    forbidden = " ".join(_clean_list(basis.get("forbidden_topics"))).lower()
    if forbidden:
        for token in ("payment information", "credit card", "银行卡"):
            position = text.lower().find(token)
            if position >= 0 and _REQUEST_VERB_RE.search(text[max(0, position - 64):position + len(token) + 24]) and not _has_negation_before(text, position):
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
    conclusion = basis.get("trusted_conclusion") if isinstance(basis.get("trusted_conclusion"), dict) else {}
    handoff = conclusion.get("handoff") is True or "handoff" in str(conclusion.get("reply_intent") or "").lower()
    if handoff and any(pattern.search(text) for pattern in _HANDOFF_REASK_PATTERNS):
        raise HermesReplyBasisError("handoff_reasks_information")
    # A model may restate a collected field, but it may not ask the customer
    # to provide it again.  Use aliases so ``full name`` and ``name`` share
    # one server-owned identity.
    for field_key in _FIELD_ALIASES:
        if field_key not in collected:
            continue
        aliases = _FIELD_ALIASES[field_key]
        for alias in aliases:
            match = re.search(rf"\b{re.escape(alias)}\b", text, re.I)
            if match and _REQUEST_VERB_RE.search(text[max(0, match.start() - 56):match.end() + 20]):
                raise HermesReplyBasisError("collected_field_reask")
    missing_keys = _basis_missing_keys(basis)
    for field_key, aliases in _FIELD_ALIASES.items():
        if field_key in collected:
            continue
        for alias in aliases:
            match = re.search(rf"\b{re.escape(alias)}\b", text, re.I)
            if match and _REQUEST_VERB_RE.search(text[max(0, match.start() - 56):match.end() + 24]):
                if field_key not in missing_keys and not _has_negation_before(text, match.start()):
                    raise HermesReplyBasisError("unauthorized_field_request")
    if _contains_authorized_claim(text, basis, _RECOVERY_CLAIM_RE) is False:
        raise HermesReplyBasisError("unsupported_account_state_claim")
    if _contains_authorized_claim(text, basis, _TIME_COMMITMENT_RE) is False:
        raise HermesReplyBasisError("unsupported_time_commitment")
    # A handoff basis may never ask for new customer data.
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