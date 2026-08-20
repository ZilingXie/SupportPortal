"""LLM adjudicator for Enablement internal-reply completion confirmation.

The worker's English regex remains the fast path and the authoritative
fallback: this classifier only runs when the regex says "not completion", and
may upgrade that verdict to "completed" so non-English or mistyped internal
replies still close the loop. Any failure (disabled, missing credentials,
invocation error, invalid JSON) falls back to the regex verdict. The
classifier never raises and never downgrades a regex completion.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    ENABLEMENT_COMPLETION_CLASSIFIER_SCENARIO,
    ModelProfile,
    resolve_model_profile,
)


ENABLEMENT_COMPLETION_CLASSIFIER_PROMPT_VERSION = "enablement-completion-classifier-v1"
ENABLEMENT_COMPLETION_CLASSIFIER_ENABLED_ENV = "ENABLEMENT_COMPLETION_CLASSIFIER_ENABLED"

CLASSIFICATION_SOURCE_LLM = "llm"
CLASSIFICATION_SOURCE_REGEX_FALLBACK = "regex_fallback"

_JSON_MODE_PAYLOAD: dict[str, Any] = {"text": {"format": {"type": "json_object"}}}


@dataclass(frozen=True, slots=True)
class EnablementCompletionClassification:
    completed: bool
    source: str
    failure_reason: str | None = None


_SYSTEM_PROMPT = f"""Prompt version: {ENABLEMENT_COMPLETION_CLASSIFIER_PROMPT_VERSION}. You classify replies to an internal Enablement request email.

The internal team was asked to enable a backend feature for a customer. Decide whether the reply text explicitly confirms that the requested feature is NOW enabled, activated, provisioned, or turned on (a completed, present-tense state).

Return JSON only: {{"confirmed": true}} or {{"confirmed": false}}.

Rules:
- Confirmed (true) only for an affirmative current state, in any language. Examples: "Enabled." / "Media Relay is enabled for this app." / "已开通" / "已启用" / "已开启" / "已完成开通" / "Done, the feature is on." / common typos of the above such as "enbaled" or "已开同".
- Not confirmed (false) for: future tense ("will enable", "明天开通", "会尽快"), requests or instructions ("please enable", "请开通"), questions ("Is it enabled?"), negations ("not enabled", "未开通", "还没开"), revoked states ("was enabled but now disabled", "已关闭"), in-progress states ("trying to", "已提交申请", "在处理中", "pending"), or anything ambiguous.
- Judge only the latest explicit state in the text, mirroring how a later sentence overrides an earlier one.
"""


def _fallback(reason: str) -> EnablementCompletionClassification:
    return EnablementCompletionClassification(
        completed=False,
        source=CLASSIFICATION_SOURCE_REGEX_FALLBACK,
        failure_reason=reason,
    )


def _classifier_enabled() -> bool:
    return str(os.getenv(ENABLEMENT_COMPLETION_CLASSIFIER_ENABLED_ENV, "true")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _user_prompt(note: str, feature_label: str | None) -> str:
    context = f"Requested feature: {feature_label}.\n\n" if feature_label else ""
    return f"{context}Reply text to classify:\n{note}"


def _parse_confirmed(raw_text: str) -> bool | None:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or "confirmed" not in payload:
        return None
    confirmed = payload.get("confirmed")
    if isinstance(confirmed, bool):
        return confirmed
    return None


def classify_enablement_completion(
    note: str,
    *,
    feature_label: str | None = None,
) -> EnablementCompletionClassification:
    """Classify one internal reply; never raises, never upgrades failures."""
    normalized_note = str(note or "").strip()
    if not normalized_note:
        return _fallback("empty_note")
    if not _classifier_enabled():
        return _fallback("disabled")
    try:
        profile: ModelProfile = resolve_model_profile(ENABLEMENT_COMPLETION_CLASSIFIER_SCENARIO)
    except Exception as exc:  # classification must never raise
        return _fallback(f"profile_error:{type(exc).__name__}")
    if not str(profile.api_key or "").strip():
        return _fallback("missing_api_key")
    try:
        result = invoke_responses_text(
            profile=profile,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_user_prompt(normalized_note, feature_label),
            extra_payload=_JSON_MODE_PAYLOAD,
        )
    except LlmInvocationError as exc:
        return _fallback(f"invocation_failed:{exc}")
    confirmed = _parse_confirmed(str(result.text or ""))
    if confirmed is None:
        return _fallback("invalid_payload")
    return EnablementCompletionClassification(
        completed=confirmed,
        source=CLASSIFICATION_SOURCE_LLM,
    )
