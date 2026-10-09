"""Structured judgment of the fraud_account customer-reply style contract.

Evaluates a customer-facing reply draft against the Production-aligned
contract (C1-C8) established in the plan "Production fraud_account customer
reply style alignment v1". This module is evaluation tooling only — it is
deliberately NOT wired into the runtime reply pipeline (no keyword
post-processing, no silent rewriting); failures surface as evidence for a
human decision.

Usage:
    from scripts.testing.fraud_reply_style import judge_fraud_reply_style
    verdict = judge_fraud_reply_style(reply, missing_fields=[...],
                                      collected_fields=[...])
"""
from __future__ import annotations

import re
from typing import Any

# Accepted surface labels per canonical fraud field (case-insensitive
# substring anchors; Production samples vary between bare and "official"
# phrasing).
# Word-boundary regex anchors (Production labels vary between bare and
# "official" phrasing).
FRAUD_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "account_type": (r"\baccount type\b",),
    "name": (r"\bname\b",),
    "office_address": (r"\boffice address\b",),
    "contact_number": (r"\b(?:official )?contact number\b",),
    "contact_email": (r"\b(?:official )?contact email\b",),
    "use_case_description": (r"\buse case\b",),
    "console_configuration": (r"\bconsole configuration\b",),
}

_PAYMENT_ASK_RE = re.compile(
    r"(?i)payment\s+(information|method|details)|credit\s+card|billing\s+information"
)
_TWENTY_FOUR_HOURS_RE = re.compile(r"(?i)\b24\s*[- ]?\s*hours?\b|\b24h\b")
_HANDOFF_RE = re.compile(
    r"(?i)\b(?:handed|passed|escalated|forwarded|relevant team|reviewing team)\b"
)
_COORDINATE_RE = re.compile(r"(?i)\bcoordinat\w*\b")
_FORBIDDEN_META_RE = re.compile(
    r"(?i)weren'?t included in your message|wasn'?t included in your message"
)
_FORBIDDEN_VAGUE_RE = re.compile(r"(?i)move\b[^.!?]{0,50}forward|we can continue review\w*")
_FLAT_APOLOGY_RE = re.compile(
    r"(?i)\b(?:i'?m sorry|we'?re sorry|i apologize)[^.!?]*(?:blocked|suspended)[^.!?]*[.!?]"
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LIST_ITEM_RE = re.compile(r"^\s*-\s+\S")


def _label_present(text: str, field: str) -> bool:
    return any(
        re.search(anchor, str(text or ""), re.IGNORECASE)
        for anchor in FRAUD_FIELD_LABELS.get(field, ())
    )


def _list_items(reply: str) -> list[str]:
    return [line.strip() for line in str(reply or "").splitlines() if _LIST_ITEM_RE.match(line)]


def _has_24h_promise(reply: str) -> bool:
    return bool(_TWENTY_FOUR_HOURS_RE.search(reply))


def _claims_submitted(reply: str) -> bool:
    """Affirmative "the review has been submitted" claim (negations pass)."""
    text = str(reply or "")
    scrubbed = re.sub(
        r"(?i)(has not yet been|hasn'?t been|not yet been|has not been|not been)\s+submitted",
        "",
        text,
    )
    scrubbed = re.sub(r"(?i)(has not yet|hasn'?t yet|not yet) (?:been )?submitted", "", scrubbed)
    return bool(re.search(r"(?i)\b(?:has been|already|request has been)\s+submitted", scrubbed))


def judge_fraud_reply_style(
    reply: str,
    *,
    missing_fields: list[str],
    collected_fields: list[str] | None = None,
    expected_restate_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Return a structured C1-C8 verdict for one fraud_account reply draft.

    ``missing_fields``/``collected_fields`` are canonical field keys from the
    server work result. ``expected_restate_terms`` (optional) are substrings
    the partial-info structure must restate (scenario-controlled facts).
    """
    collected = list(collected_fields or [])
    missing = list(missing_fields or [])
    text = str(reply or "")
    lowered = text.casefold()
    items = _list_items(reply)
    checks: dict[str, dict[str, Any]] = {}

    # C1 — ask exactly the missing fields (list items are asks; prose
    # restatement of collected facts is allowed and expected by C2).
    asked_in_items = [
        field for field in FRAUD_FIELD_LABELS if any(_label_present(item, field) for item in items)
    ]
    if missing:
        missing_missing = [f for f in missing if not _label_present(lowered, f)]
        checks["c1_ask_missing_fields"] = {
            "passed": not missing_missing,
            "detail": f"fields not asked: {missing_missing}" if missing_missing else "all missing fields asked",
        }
        re_asked = [f for f in collected if f in asked_in_items]
        checks["c1_no_reask"] = {
            "passed": not re_asked,
            "detail": f"collected fields re-asked as list items: {re_asked}" if re_asked else "no collected field re-asked",
        }
    else:
        stray_asks = asked_in_items or (
            [f for f in FRAUD_FIELD_LABELS if _label_present(lowered, f)]
            if re.search(r"(?i)please (share|provide|send)", lowered)
            else []
        )
        checks["c5_complete_no_ask"] = {
            "passed": not stray_asks,
            "detail": f"information requested despite complete fields: {stray_asks}" if stray_asks else "no information requested",
        }
        checks["c5_handoff_stated"] = {
            "passed": bool(_HANDOFF_RE.search(text)),
            "detail": "handoff confirmation present" if _HANDOFF_RE.search(text) else "handoff confirmation missing",
        }
        checks["c5_24h_promise"] = {
            "passed": _has_24h_promise(text),
            "detail": "24-hour contact commitment present" if _has_24h_promise(text) else "24-hour contact commitment missing",
        }

    # C2 — restate collected facts when partial.
    if collected and missing:
        terms = [str(t) for t in (expected_restate_terms or []) if str(t).strip()]
        if terms:
            absent = [t for t in terms if t.casefold() not in lowered]
            checks["c2_restate_collected"] = {
                "passed": not absent,
                "detail": f"restate terms missing: {absent}" if absent else "collected facts restated",
            }
        else:
            checks["c2_restate_collected"] = {
                "passed": True,
                "detail": "no expected restate terms supplied (skipped)",
            }

    # C3 — list format for 3+ items.
    if len(missing) >= 3:
        checks["c3_list_format"] = {
            "passed": len(items) >= len(missing),
            "detail": f"{len(items)} list items for {len(missing)} missing fields",
        }

    # C4 — style anchors. The coordinate-the-review semantics govern the
    # ask path; the complete-fields handoff confirmation states the handoff
    # itself (C5) and does not need the coordinate wording.
    coordinate = bool(_COORDINATE_RE.search(text)) or not missing
    forbidden: list[str] = []
    if _FORBIDDEN_META_RE.search(text):
        forbidden.append("meta phrasing about the message contents")
    if _FORBIDDEN_VAGUE_RE.search(text):
        forbidden.append("vague 'move the request forward' claim")
    if _FLAT_APOLOGY_RE.search(text):
        forbidden.append("flat apology about the blocked/suspended account")
    checks["c4_style"] = {
        "passed": coordinate and not forbidden,
        "detail": (
            ("coordinate-the-review semantics missing" if not coordinate else "coordinate semantics present")
            + (f"; forbidden: {forbidden}" if forbidden else "")
        ),
    }

    # C6 — payment information is never a collectible item.
    payment = bool(_PAYMENT_ASK_RE.search(text))
    checks["c6_payment_not_asked"] = {
        "passed": not payment,
        "detail": "payment information referenced" if payment else "no payment information request",
    }

    # C7 — while information is missing: no 24h promise, no submitted claim.
    if missing:
        premature_24h = _has_24h_promise(text)
        submitted = _claims_submitted(text)
        checks["c7_no_premature_promises"] = {
            "passed": not premature_24h and not submitted,
            "detail": (
                f"24h promise while info missing={premature_24h}, submitted claim={submitted}"
                if (premature_24h or submitted)
                else "no premature commitments"
            ),
        }

    # C8 — the draft itself is English (translation happens post-approval).
    # Absolute count, not a ratio: a short Chinese suffix on a long English
    # reply is still a mixed-language draft.
    cjk = len(_CJK_RE.findall(text))
    checks["c8_english_draft"] = {
        "passed": cjk <= 2,
        "detail": f"CJK character count {cjk}",
    }

    failed = [name for name, result in checks.items() if not result["passed"]]
    return {
        "passed": not failed,
        "failed": failed,
        "checks": checks,
    }
