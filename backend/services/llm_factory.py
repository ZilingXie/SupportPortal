from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend.services import openai_agent_tracing
from backend.services.llm_profiles import (
    DEEPSEEK_CHAT_API,
    OPENAI_CHAT_API,
    OPENAI_RESPONSES_API,
    SILICONFLOW_CHAT_API,
    ModelProfile,
)


class LlmInvocationError(RuntimeError):
    def __init__(self, message: str, *, fallback_eligible: bool = False) -> None:
        super().__init__(message)
        self.fallback_eligible = bool(fallback_eligible)


LOGGER = logging.getLogger(__name__)
_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class LlmTextResult:
    text: str
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    raw_payload: dict[str, Any] | None = None
    provider_name: str = "openai"

    @property
    def provider_model_name(self) -> str:
        provider = _normalize_text(self.provider_name).lower()
        model = _normalize_text(self.model_name)
        if provider and model:
            return f"{provider}:{model}"
        return model


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _read_http_error_payload(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read()
    except Exception:
        return ""
    finally:
        try:
            error.close()
        except Exception:
            pass
    if not body:
        return ""
    return body.decode("utf-8", errors="replace")


def _responses_text(payload: dict[str, Any]) -> str:
    # Customer prose must retain paragraph and list boundaries.
    output_text = str(payload.get("output_text") or "").strip()
    if output_text:
        return output_text
    output_items = payload.get("output") if isinstance(payload.get("output"), list) else []
    for item in output_items:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content_item in item.get("content") or []:
            if not isinstance(content_item, dict):
                continue
            text = str(content_item.get("text") or "").strip()
            if text:
                return text
    return ""


def _chat_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
    if not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = _normalize_text(item.get("text"))
                if text:
                    parts.append(text)
            else:
                text = _normalize_text(item)
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return _normalize_text(content)


def _usage_details(usage: dict[str, Any]) -> tuple[int, int]:
    input_details = usage.get("input_tokens_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    prompt_details = usage.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    output_details = usage.get("output_tokens_details")
    output_details = output_details if isinstance(output_details, dict) else {}
    completion_details = usage.get("completion_tokens_details")
    completion_details = completion_details if isinstance(completion_details, dict) else {}
    cached_tokens = int(
        input_details.get("cached_tokens") or prompt_details.get("cached_tokens") or 0
    )
    reasoning_tokens = int(
        output_details.get("reasoning_tokens")
        or completion_details.get("reasoning_tokens")
        or 0
    )
    return cached_tokens, reasoning_tokens


def _responses_usage(payload: dict[str, Any]) -> tuple[int, int, int, int]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    cached_tokens, reasoning_tokens = _usage_details(usage)
    return prompt_tokens, completion_tokens, cached_tokens, reasoning_tokens


def _chat_usage(payload: dict[str, Any]) -> tuple[int, int, int, int]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cached_tokens, reasoning_tokens = _usage_details(usage)
    return prompt_tokens, completion_tokens, cached_tokens, reasoning_tokens


def _is_model_unavailable(error_payload: str) -> bool:
    lowered = error_payload.lower()
    return "model_not_found" in lowered or "does not exist" in lowered or "not available" in lowered


def _retry_budget(profile: ModelProfile) -> int:
    return max(0, int(profile.max_retries))


def _should_retry_http_error(code: int) -> bool:
    return code in _RETRYABLE_HTTP_STATUS_CODES or code >= 500


def _trace_model_name(profile: ModelProfile, model_name: str) -> str:
    if profile.provider == "openai":
        return model_name
    return f"{profile.provider}:{model_name}"


def _reasoning_effort_for_deepseek(reasoning_effort: str | None) -> dict[str, Any]:
    normalized = _normalize_text(reasoning_effort).lower()
    if normalized in {"none", "off", "disabled"}:
        return {"thinking": {"type": "disabled"}}
    if not normalized:
        return {}
    effort = "max" if normalized in {"max", "xhigh"} else "high"
    return {"thinking": {"type": "enabled"}, "reasoning_effort": effort}


def _responses_extra_payload_to_chat_payload(extra_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not extra_payload:
        return {}
    allowed_keys = {"max_output_tokens", "text"}
    if any(key not in allowed_keys for key in extra_payload):
        return None
    payload: dict[str, Any] = {}
    if extra_payload.get("max_output_tokens") is not None:
        payload["max_tokens"] = extra_payload.get("max_output_tokens")
    text_options = extra_payload.get("text")
    if text_options is None:
        return payload
    if not isinstance(text_options, dict):
        return None
    format_options = text_options.get("format")
    if format_options is None:
        return payload
    if not isinstance(format_options, dict):
        return None
    format_type = _normalize_text(format_options.get("type")).lower()
    if format_type in {"json_schema", "json_object"}:
        payload["response_format"] = {"type": "json_object"}
        return payload
    if format_type in {"", "text"}:
        return payload
    return None


def _fallback_profiles(profile: ModelProfile) -> list[ModelProfile]:
    return [fallback for fallback in profile.fallback_profiles if _normalize_text(fallback.api_key)]


def _raise_with_fallback_failures(primary_error: LlmInvocationError, fallback_errors: list[str]) -> None:
    if not fallback_errors:
        raise primary_error
    fallback_summary = "; ".join(_normalize_text(item) for item in fallback_errors if _normalize_text(item))
    raise LlmInvocationError(
        f"{primary_error}; provider_fallback_failed: {fallback_summary}",
        fallback_eligible=primary_error.fallback_eligible,
    ) from primary_error


def _log_retry(
    *,
    profile: ModelProfile,
    model_name: str,
    attempt_number: int,
    error: BaseException,
) -> None:
    total_attempts = _retry_budget(profile) + 1
    LOGGER.warning(
        "Retrying LLM request for scenario=%s model=%s after failed attempt %s/%s: %s",
        profile.scenario,
        model_name,
        attempt_number,
        total_attempts,
        error,
    )


def _responses_request(
    *,
    profile: ModelProfile,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    extra_payload: dict[str, Any] | None,
    temperature: float | None,
) -> urllib.request.Request:
    payload: dict[str, Any] = {
        "model": model_name,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
    }
    if profile.reasoning_effort:
        payload["reasoning"] = {"effort": profile.reasoning_effort}
    if temperature is not None:
        payload["temperature"] = temperature
    if extra_payload:
        payload.update(extra_payload)
    base_url = (profile.base_url or "https://api.openai.com/v1").rstrip("/")
    return urllib.request.Request(
        f"{base_url}/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        },
    )


def _invoke_responses_text_once(
    *,
    profile: ModelProfile,
    system_prompt: str,
    user_prompt: str,
    extra_payload: dict[str, Any] | None = None,
) -> LlmTextResult:
    if not profile.api_key:
        raise LlmInvocationError(f"{profile.scenario}_missing_api_key", fallback_eligible=True)

    candidate_models = profile.candidate_models()
    last_error: LlmInvocationError | None = None

    for candidate_index, model_name in enumerate(candidate_models):
        has_next_model = candidate_index < len(candidate_models) - 1
        temperature = profile.temperature
        retry_attempts = 0
        while True:
            request = _responses_request(
                profile=profile,
                model_name=model_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                extra_payload=extra_payload,
                temperature=temperature,
            )
            try:
                with urllib.request.urlopen(request, timeout=profile.timeout_seconds) as response:
                    raw_payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
            except urllib.error.HTTPError as exc:
                error_payload = _read_http_error_payload(exc)
                lowered = error_payload.lower()
                if temperature is not None and exc.code in {400, 422} and "temperature" in lowered:
                    temperature = None
                    continue
                if _is_model_unavailable(lowered):
                    last_error = LlmInvocationError(
                        f"{profile.scenario}_model_unavailable: {model_name}",
                        fallback_eligible=True,
                    )
                    break
                if _should_retry_http_error(exc.code) and retry_attempts < _retry_budget(profile):
                    retry_attempts += 1
                    _log_retry(
                        profile=profile,
                        model_name=model_name,
                        attempt_number=retry_attempts,
                        error=exc,
                    )
                    continue
                current_error = LlmInvocationError(
                    f"{profile.scenario}_request_failed: {exc}",
                    fallback_eligible=_should_retry_http_error(exc.code),
                )
                if has_next_model and _should_retry_http_error(exc.code):
                    last_error = current_error
                    break
                raise current_error from exc
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                if retry_attempts < _retry_budget(profile):
                    retry_attempts += 1
                    _log_retry(
                        profile=profile,
                        model_name=model_name,
                        attempt_number=retry_attempts,
                        error=exc,
                    )
                    continue
                current_error = LlmInvocationError(
                    f"{profile.scenario}_request_failed: {exc}",
                    fallback_eligible=True,
                )
                if has_next_model:
                    last_error = current_error
                    break
                raise current_error from exc

            payload = raw_payload if isinstance(raw_payload, dict) else {}
            text = _responses_text(payload)
            prompt_tokens, completion_tokens, cached_input_tokens, reasoning_tokens = _responses_usage(payload)
            if openai_agent_tracing.current_trace_ref() is not None:
                openai_agent_tracing.record_generation_span(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_text=text,
                    model_name=_trace_model_name(profile, model_name),
                    reasoning_effort=profile.reasoning_effort,
                    temperature=temperature,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            return LlmTextResult(
                text=text,
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                raw_payload=payload,
                provider_name=profile.provider,
            )

    if last_error is not None:
        raise last_error
    raise LlmInvocationError(f"{profile.scenario}_no_available_model", fallback_eligible=True)


def invoke_responses_text(
    *,
    profile: ModelProfile,
    system_prompt: str,
    user_prompt: str,
    extra_payload: dict[str, Any] | None = None,
) -> LlmTextResult:
    if profile.api_mode != OPENAI_RESPONSES_API:
        raise ValueError(f"profile {profile.scenario} does not use responses API")

    primary_error: LlmInvocationError | None = None
    if profile.api_key:
        try:
            return _invoke_responses_text_once(
                profile=profile,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                extra_payload=extra_payload,
            )
        except LlmInvocationError as exc:
            if not exc.fallback_eligible:
                raise
            primary_error = exc
    else:
        primary_error = LlmInvocationError(f"{profile.scenario}_missing_api_key", fallback_eligible=True)

    chat_payload = _responses_extra_payload_to_chat_payload(extra_payload)
    return _invoke_provider_fallbacks(
        profile=profile,
        primary_error=primary_error,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        chat_extra_payload=chat_payload,
    )


def _chat_request(
    *,
    profile: ModelProfile,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float | None,
    extra_payload: dict[str, Any] | None = None,
) -> urllib.request.Request:
    base_url = (profile.base_url or "https://api.openai.com/v1").rstrip("/")
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if profile.api_mode == DEEPSEEK_CHAT_API:
        payload.update(_reasoning_effort_for_deepseek(profile.reasoning_effort))
    if extra_payload:
        payload.update(extra_payload)
    return urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        },
    )


def _invoke_chat_text_once(
    *,
    profile: ModelProfile,
    system_prompt: str,
    user_prompt: str,
    extra_payload: dict[str, Any] | None = None,
) -> LlmTextResult:
    if not profile.api_key:
        raise LlmInvocationError(f"{profile.scenario}_missing_api_key", fallback_eligible=True)

    last_error: LlmInvocationError | None = None
    for model_name in profile.candidate_models():
        temperature = profile.temperature
        retry_attempts = 0
        while True:
            request = _chat_request(
                profile=profile,
                model_name=model_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                extra_payload=extra_payload,
            )
            try:
                with urllib.request.urlopen(request, timeout=profile.timeout_seconds) as response:
                    raw_payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
            except urllib.error.HTTPError as exc:
                error_payload = _read_http_error_payload(exc)
                lowered = error_payload.lower()
                if temperature is not None and exc.code in {400, 422} and "temperature" in lowered:
                    temperature = None
                    continue
                if _is_model_unavailable(lowered):
                    last_error = LlmInvocationError(
                        f"{profile.scenario}_model_unavailable: {model_name}",
                        fallback_eligible=True,
                    )
                    break
                if _should_retry_http_error(exc.code) and retry_attempts < _retry_budget(profile):
                    retry_attempts += 1
                    _log_retry(
                        profile=profile,
                        model_name=model_name,
                        attempt_number=retry_attempts,
                        error=exc,
                    )
                    continue
                raise LlmInvocationError(
                    f"{profile.scenario}_request_failed: {exc}",
                    fallback_eligible=_should_retry_http_error(exc.code),
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                if retry_attempts < _retry_budget(profile):
                    retry_attempts += 1
                    _log_retry(
                        profile=profile,
                        model_name=model_name,
                        attempt_number=retry_attempts,
                        error=exc,
                    )
                    continue
                raise LlmInvocationError(
                    f"{profile.scenario}_request_failed: {exc}",
                    fallback_eligible=True,
                ) from exc

            payload = raw_payload if isinstance(raw_payload, dict) else {}
            text = _chat_text(payload)
            prompt_tokens, completion_tokens, cached_input_tokens, reasoning_tokens = _chat_usage(payload)
            return LlmTextResult(
                text=text,
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                raw_payload=payload,
                provider_name=profile.provider,
            )

    if last_error is not None:
        raise last_error
    raise LlmInvocationError(f"{profile.scenario}_no_available_model", fallback_eligible=True)


def _invoke_provider_fallbacks(
    *,
    profile: ModelProfile,
    primary_error: LlmInvocationError,
    system_prompt: str,
    user_prompt: str,
    chat_extra_payload: dict[str, Any] | None,
) -> LlmTextResult:
    if chat_extra_payload is None:
        raise primary_error

    fallback_errors: list[str] = []
    for fallback in _fallback_profiles(profile):
        if fallback.api_mode not in {OPENAI_CHAT_API, SILICONFLOW_CHAT_API, DEEPSEEK_CHAT_API}:
            continue
        LOGGER.warning(
            "LLM request for scenario=%s provider=%s model=%s failed; trying provider fallback %s:%s.",
            profile.scenario,
            profile.provider,
            profile.model,
            fallback.provider,
            fallback.model,
        )
        try:
            return _invoke_chat_text_once(
                profile=fallback,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                extra_payload=chat_extra_payload,
            )
        except LlmInvocationError as exc:
            fallback_errors.append(str(exc))
            continue

    _raise_with_fallback_failures(primary_error, fallback_errors)


def invoke_chat_text(
    *,
    profile: ModelProfile,
    system_prompt: str,
    user_prompt: str,
) -> LlmTextResult:
    if profile.api_mode not in {OPENAI_CHAT_API, SILICONFLOW_CHAT_API, DEEPSEEK_CHAT_API}:
        raise ValueError(f"profile {profile.scenario} does not use chat completions API")

    primary_error: LlmInvocationError | None = None
    if profile.api_key:
        try:
            return _invoke_chat_text_once(
                profile=profile,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except LlmInvocationError as exc:
            if not exc.fallback_eligible:
                raise
            primary_error = exc
    else:
        primary_error = LlmInvocationError(f"{profile.scenario}_missing_api_key", fallback_eligible=True)

    return _invoke_provider_fallbacks(
        profile=profile,
        primary_error=primary_error,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        chat_extra_payload={},
    )
