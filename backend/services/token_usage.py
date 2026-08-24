from __future__ import annotations

from collections import OrderedDict
from typing import Any


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_usage_ledger_entry(
    *,
    provider: str,
    model: str,
    stage: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    tool_tokens: int | None = None,
    embedding_tokens: int | None = None,
    unknown_usage_fields: list[str] | None = None,
) -> dict[str, Any]:
    normalized_provider = _clean_text(provider).lower()
    normalized_model = _clean_text(model)
    normalized_stage = _clean_text(stage)
    prompt_token_count = _safe_int(prompt_tokens if prompt_tokens is not None else input_tokens)
    completion_token_count = _safe_int(completion_tokens if completion_tokens is not None else output_tokens)
    input_token_count = _safe_int(input_tokens if input_tokens is not None else prompt_token_count)
    output_token_count = _safe_int(output_tokens if output_tokens is not None else completion_token_count)
    embedding_token_count = _safe_int(embedding_tokens)
    return {
        "provider": normalized_provider,
        "model": normalized_model,
        "stage": normalized_stage,
        "input_tokens": input_token_count,
        "output_tokens": output_token_count,
        "prompt_tokens": prompt_token_count,
        "completion_tokens": completion_token_count,
        "cached_input_tokens": _safe_int(cached_input_tokens),
        "reasoning_tokens": _safe_int(reasoning_tokens),
        "tool_tokens": _safe_int(tool_tokens),
        "embedding_tokens": embedding_token_count,
        "unknown_usage_fields": list(unknown_usage_fields or []),
    }


def aggregate_usage_ledger(entries: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None) -> dict[str, Any]:
    totals = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_cached_input_tokens": 0,
        "total_reasoning_tokens": 0,
        "total_tool_tokens": 0,
        "total_embedding_tokens": 0,
    }
    grouped: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
    ledger = [dict(item) for item in entries or [] if isinstance(item, dict)]
    for entry in ledger:
        totals["total_input_tokens"] += _safe_int(entry.get("input_tokens"))
        totals["total_output_tokens"] += _safe_int(entry.get("output_tokens"))
        totals["total_prompt_tokens"] += _safe_int(entry.get("prompt_tokens"))
        totals["total_completion_tokens"] += _safe_int(entry.get("completion_tokens"))
        totals["total_cached_input_tokens"] += _safe_int(entry.get("cached_input_tokens"))
        totals["total_reasoning_tokens"] += _safe_int(entry.get("reasoning_tokens"))
        totals["total_tool_tokens"] += _safe_int(entry.get("tool_tokens"))
        totals["total_embedding_tokens"] += _safe_int(entry.get("embedding_tokens"))
        provider = _clean_text(entry.get("provider")).lower()
        model = _clean_text(entry.get("model"))
        group = grouped.setdefault(
            (provider, model),
            {
                "provider": provider,
                "model": model,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "reasoning_tokens": 0,
                "embedding_tokens": 0,
            },
        )
        group["input_tokens"] += _safe_int(entry.get("input_tokens"))
        group["output_tokens"] += _safe_int(entry.get("output_tokens"))
        group["cached_input_tokens"] += _safe_int(entry.get("cached_input_tokens"))
        group["reasoning_tokens"] += _safe_int(entry.get("reasoning_tokens"))
        group["embedding_tokens"] += _safe_int(entry.get("embedding_tokens"))
    return {
        "entries": ledger,
        "total_input_tokens": totals["total_input_tokens"],
        "total_output_tokens": totals["total_output_tokens"],
        "total_prompt_tokens": totals["total_prompt_tokens"],
        "total_completion_tokens": totals["total_completion_tokens"],
        "total_cached_input_tokens": totals["total_cached_input_tokens"],
        "total_reasoning_tokens": totals["total_reasoning_tokens"],
        "total_tool_tokens": totals["total_tool_tokens"],
        "total_embedding_tokens": totals["total_embedding_tokens"],
        "token_by_model": [dict(item) for item in grouped.values()],
    }


def resolve_ticket_family_identity(
    ticket_payload: dict[str, Any] | None,
    *,
    related_ticket_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    payload = dict(ticket_payload or {})
    client_ticket_ref = payload.get("client_ticket_ref") if isinstance(payload.get("client_ticket_ref"), dict) else {}
    canonical_ticket_id = (
        _clean_text(payload.get("client_ticket_id"))
        or _clean_text(client_ticket_ref.get("ticket_id"))
        or _clean_text(payload.get("ticket_id"))
    )
    ordered_related: list[str] = []
    for item in related_ticket_ids or []:
        normalized = _clean_text(item)
        if normalized and normalized not in ordered_related:
            ordered_related.append(normalized)
    return {
        "canonical_ticket_id": canonical_ticket_id,
        "related_ticket_ids": ordered_related,
    }
