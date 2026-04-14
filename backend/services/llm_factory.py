from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend.services.llm_profiles import (
    OPENAI_CHAT_API,
    OPENAI_RESPONSES_API,
    SILICONFLOW_CHAT_API,
    ModelProfile,
)


class LlmInvocationError(RuntimeError):
    pass


LOGGER = logging.getLogger(__name__)
_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class LlmTextResult:
    text: str
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_payload: dict[str, Any] | None = None


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
    output_text = _normalize_text(payload.get("output_text"))
    if output_text:
        return output_text
    output_items = payload.get("output") if isinstance(payload.get("output"), list) else []
    for item in output_items:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content_item in item.get("content") or []:
            if not isinstance(content_item, dict):
                continue
            text = _normalize_text(content_item.get("text"))
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


def _responses_usage(payload: dict[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return prompt_tokens, completion_tokens


def _chat_usage(payload: dict[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    return prompt_tokens, completion_tokens


def _is_model_unavailable(error_payload: str) -> bool:
    lowered = error_payload.lower()
    return "model_not_found" in lowered or "does not exist" in lowered or "not available" in lowered


def _retry_budget(profile: ModelProfile) -> int:
    return max(0, int(profile.max_retries))


def _should_retry_http_error(code: int) -> bool:
    return code in _RETRYABLE_HTTP_STATUS_CODES or code >= 500


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
    return urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        },
    )


def invoke_responses_text(
    *,
    profile: ModelProfile,
    system_prompt: str,
    user_prompt: str,
    extra_payload: dict[str, Any] | None = None,
) -> LlmTextResult:
    if profile.api_mode != OPENAI_RESPONSES_API:
        raise ValueError(f"profile {profile.scenario} does not use responses API")
    if not profile.api_key:
        raise LlmInvocationError(f"{profile.scenario}_missing_api_key")

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
                current_error = LlmInvocationError(f"{profile.scenario}_request_failed: {exc}")
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
                current_error = LlmInvocationError(f"{profile.scenario}_request_failed: {exc}")
                if has_next_model:
                    last_error = current_error
                    break
                raise current_error from exc

            payload = raw_payload if isinstance(raw_payload, dict) else {}
            text = _responses_text(payload)
            prompt_tokens, completion_tokens = _responses_usage(payload)
            return LlmTextResult(
                text=text,
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                raw_payload=payload,
            )

    if last_error is not None:
        raise last_error
    raise LlmInvocationError(f"{profile.scenario}_no_available_model")


def _chat_request(
    *,
    profile: ModelProfile,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float | None,
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
    return urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        },
    )


def invoke_chat_text(
    *,
    profile: ModelProfile,
    system_prompt: str,
    user_prompt: str,
) -> LlmTextResult:
    if profile.api_mode not in {OPENAI_CHAT_API, SILICONFLOW_CHAT_API}:
        raise ValueError(f"profile {profile.scenario} does not use chat completions API")
    if not profile.api_key:
        raise LlmInvocationError(f"{profile.scenario}_missing_api_key")

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
                raise LlmInvocationError(f"{profile.scenario}_request_failed: {exc}") from exc
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
                raise LlmInvocationError(f"{profile.scenario}_request_failed: {exc}") from exc

            payload = raw_payload if isinstance(raw_payload, dict) else {}
            text = _chat_text(payload)
            prompt_tokens, completion_tokens = _chat_usage(payload)
            return LlmTextResult(
                text=text,
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                raw_payload=payload,
            )

    raise LlmInvocationError(f"{profile.scenario}_no_available_model")
