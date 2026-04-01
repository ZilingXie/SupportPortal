from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend.services.rag_sufficiency_prompt import (
    build_rag_sufficiency_system_prompt,
    build_rag_sufficiency_user_payload,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_RAG_SUFFICIENCY_MODEL = "gpt-5.4-mini"
DEFAULT_RAG_SUFFICIENCY_TIMEOUT_SECONDS = 8.0
DEFAULT_RAG_SUFFICIENCY_REASONING_EFFORT = "low"
DEFAULT_RAG_SUFFICIENCY_TEMPERATURE = 0.0


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


class RagSufficiencyJudgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RagSufficiencyJudgeResult:
    decision: str
    reason: str
    confidence: float


def _extract_response_text(response_payload: dict[str, Any]) -> str:
    output_text = str(response_payload.get("output_text") or "").strip()
    if output_text:
        return output_text
    output_items = response_payload.get("output") if isinstance(response_payload.get("output"), list) else []
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


def _call_responses_api(*, api_key: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
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
    except urllib.error.HTTPError as exc:
        if "temperature" in payload:
            error_payload = _read_http_error_payload(exc).lower()
            if exc.code in {400, 422} and "temperature" in error_payload:
                retry_payload = dict(payload)
                retry_payload.pop("temperature", None)
                return _call_responses_api(
                    api_key=api_key,
                    payload=retry_payload,
                    timeout_seconds=timeout_seconds,
                )
        else:
            try:
                exc.close()
            except Exception:
                pass
        raise RagSufficiencyJudgeError(f"sufficiency_judge_request_failed: {exc}") from exc
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RagSufficiencyJudgeError(f"sufficiency_judge_request_failed: {exc}") from exc
    return raw_payload if isinstance(raw_payload, dict) else {}


def judge_rag_answer_sufficiency(
    *,
    message: str,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    route_summary: dict[str, Any] | None,
    rag_answer: str,
    sources: list[str] | None,
    citations: list[dict[str, str]] | None,
    evidence_summary: dict[str, Any] | None,
) -> RagSufficiencyJudgeResult:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RagSufficiencyJudgeError("sufficiency_judge_missing_api_key")

    model = (os.getenv("RAG_SUFFICIENCY_JUDGE_MODEL") or DEFAULT_RAG_SUFFICIENCY_MODEL).strip()
    reasoning_effort = (
        os.getenv("RAG_SUFFICIENCY_JUDGE_REASONING_EFFORT") or DEFAULT_RAG_SUFFICIENCY_REASONING_EFFORT
    ).strip() or DEFAULT_RAG_SUFFICIENCY_REASONING_EFFORT
    timeout_seconds = _safe_float_env(
        "RAG_SUFFICIENCY_JUDGE_TIMEOUT_SECONDS",
        DEFAULT_RAG_SUFFICIENCY_TIMEOUT_SECONDS,
    )
    temperature = _safe_float_env(
        "RAG_SUFFICIENCY_JUDGE_TEMPERATURE",
        DEFAULT_RAG_SUFFICIENCY_TEMPERATURE,
    )
    payload = {
        "model": model,
        "reasoning": {"effort": reasoning_effort},
        "temperature": temperature,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": build_rag_sufficiency_system_prompt()}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": build_rag_sufficiency_user_payload(
                            message=message,
                            ticket_subject=ticket_subject,
                            ticket_context=ticket_context,
                            route_summary=route_summary,
                            rag_answer=rag_answer,
                            sources=sources,
                            citations=citations,
                            evidence_summary=evidence_summary,
                        ),
                    }
                ],
            },
        ],
    }
    response_payload = _call_responses_api(
        api_key=api_key,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    raw_text = _extract_response_text(response_payload)
    if not raw_text:
        raise RagSufficiencyJudgeError("sufficiency_judge_empty_response")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        LOGGER.warning("RAG sufficiency judge returned invalid JSON: %s", raw_text)
        raise RagSufficiencyJudgeError("sufficiency_judge_invalid_json") from exc

    decision = str(parsed.get("decision") or "").strip().lower()
    if decision not in {"answer", "investigate"}:
        raise RagSufficiencyJudgeError("sufficiency_judge_invalid_decision")
    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError) as exc:
        raise RagSufficiencyJudgeError("sufficiency_judge_invalid_confidence") from exc
    reason = str(parsed.get("reason") or "").strip() or (
        "sufficient_grounded_answer" if decision == "answer" else "insufficient_grounding"
    )
    return RagSufficiencyJudgeResult(
        decision=decision,
        reason=reason,
        confidence=max(0.0, min(1.0, confidence)),
    )
