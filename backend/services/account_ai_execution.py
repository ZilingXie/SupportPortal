from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Any, Callable

from backend.services.llm_factory import LlmInvocationError, LlmTextResult, invoke_responses_text
from backend.services.llm_profiles import ModelProfile
from backend.services.llm_usage_capture import record_llm_invocation


LOGGER = logging.getLogger(__name__)
ACCOUNT_MAX_RETRIES = 3


class AccountProcessingFailure(RuntimeError, ValueError):
    """A system failure that must stop Account automation and reach human review."""

    def __init__(
        self,
        code: str,
        detail: Any = "",
        *,
        stage: str = "account",
        attempt_count: int = 1,
    ) -> None:
        normalized_code = "_".join(str(code or "account_processing_failed").strip().lower().split())
        self.code = re.sub(r"[^a-z0-9_.-]+", "_", normalized_code)[:160].strip("_") or "account_processing_failed"
        self.detail = " ".join(str(detail or "").split())[:500]
        self.stage = " ".join(str(stage or "account").split())[:120]
        self.attempt_count = max(0, int(attempt_count))
        message = self.code if not self.detail else f"{self.code}: {self.detail}"
        super().__init__(message)


class AccountRerunDegradedError(AccountProcessingFailure):
    """Terminal Account rerun degradation that must not commit a Case."""

    def __init__(
        self,
        code: str,
        detail: Any = "",
        *,
        stage: str = "account",
        source: str | None = None,
    ) -> None:
        super().__init__(code, detail, stage=stage)
        self.degradation_reason_code = self.code
        self.degradation_stage = self.stage
        self.degradation_source = " ".join(str(source or stage).split())[:120] or self.stage


def account_profile(profile: ModelProfile) -> ModelProfile:
    """Pin Account calls to their configured provider and a single model profile."""
    if not isinstance(profile, ModelProfile):
        # Unit-test doubles and repository adapters may expose a profile-shaped object.
        return profile
    return replace(profile, max_retries=0, fallback_models=(), fallback_profiles=())


def account_profile_has_primary_credentials(profile: ModelProfile) -> bool:
    raw_api_key = getattr(profile, "api_key", None)
    api_key = raw_api_key.strip() if isinstance(raw_api_key, str) else ""
    if api_key:
        return True
    checker = getattr(profile, "has_invocation_credentials", None)
    return bool(checker()) if callable(checker) and not isinstance(profile, ModelProfile) else False


def _failure_detail(exc: Exception) -> str:
    if isinstance(exc, (LlmInvocationError, json.JSONDecodeError, TypeError, ValueError)):
        return str(exc)
    return type(exc).__name__


def invoke_account_responses_text(
    *,
    profile: ModelProfile,
    system_prompt: str,
    user_prompt: str,
    extra_payload: dict[str, Any] | None = None,
    stage: str = "account",
    validate_response: Callable[[LlmTextResult], None] | None = None,
) -> LlmTextResult:
    """Invoke and validate an Account response within one four-call budget."""
    pinned = account_profile(profile)
    last_error: Exception | None = None
    for attempt in range(ACCOUNT_MAX_RETRIES + 1):
        try:
            response = invoke_responses_text(
                profile=pinned,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                extra_payload=extra_payload,
            )
            record_llm_invocation(response, stage=stage)
            if validate_response is not None:
                validate_response(response)
            return response
        except Exception as exc:
            last_error = exc
            if attempt >= ACCOUNT_MAX_RETRIES:
                break
            LOGGER.warning(
                "Account AI stage %s failed; retrying attempt %s/%s: %s",
                stage,
                attempt + 1,
                ACCOUNT_MAX_RETRIES + 1,
                exc,
            )
    if isinstance(last_error, AccountProcessingFailure):
        last_error.attempt_count = ACCOUNT_MAX_RETRIES + 1
        raise last_error
    raise AccountProcessingFailure(
        "account_ai_invocation_exhausted",
        _failure_detail(last_error) if last_error else "unknown model invocation failure",
        stage=stage,
        attempt_count=ACCOUNT_MAX_RETRIES + 1,
    ) from last_error


def invoke_account_json_payload(
    *,
    profile: ModelProfile,
    system_prompt: str,
    user_prompt: str,
    extra_payload: dict[str, Any] | None = None,
    stage: str,
) -> dict[str, Any]:
    """Invoke an Account model and require a JSON object on every attempt."""
    pinned = account_profile(profile)
    if not account_profile_has_primary_credentials(pinned):
        raise AccountProcessingFailure(
            "account_ai_missing_credentials",
            "the configured OpenAI Account profile has no primary API key",
            stage=stage,
            attempt_count=0,
        )
    last_error: Exception | None = None
    for attempt in range(ACCOUNT_MAX_RETRIES + 1):
        try:
            response = invoke_responses_text(
                profile=pinned,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                extra_payload=extra_payload,
            )
            record_llm_invocation(response, stage=stage)
            payload = json.loads(str(response.text or ""))
            if not isinstance(payload, dict):
                raise ValueError("Account model returned a non-object JSON payload")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt >= ACCOUNT_MAX_RETRIES:
                break
            LOGGER.warning(
                "Account AI JSON stage %s failed; retrying attempt %s/%s: %s",
                stage,
                attempt + 1,
                ACCOUNT_MAX_RETRIES + 1,
                exc,
            )
    raise AccountProcessingFailure(
        "account_ai_structured_output_exhausted",
        _failure_detail(last_error) if last_error else "unknown structured output failure",
        stage=stage,
        attempt_count=ACCOUNT_MAX_RETRIES + 1,
    ) from last_error


def is_account_processing_failure(exc: BaseException) -> bool:
    return isinstance(exc, AccountProcessingFailure)
