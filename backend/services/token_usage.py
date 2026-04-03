from __future__ import annotations

from collections import OrderedDict
from typing import Any


_DEFAULT_PRICING: dict[tuple[str, str], dict[str, float]] = {
    ("openai", "gpt-5.4"): {
        "input_per_1k": 0.002,
        "output_per_1k": 0.008,
    },
    ("openai", "gpt-5.4-mini"): {
        "input_per_1k": 0.0004,
        "output_per_1k": 0.0016,
    },
    ("siliconflow", "Qwen/Qwen3.5-397B-A17B"): {
        "input_per_1k": 0.0006,
        "output_per_1k": 0.0006,
    },
    ("siliconflow", "deepseek-ai/DeepSeek-V3.2"): {
        "input_per_1k": 0.0008,
        "output_per_1k": 0.0008,
    },
    ("siliconflow", "BAAI/bge-m3"): {
        "embedding_per_1k": 0.0001,
    },
}


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


def _pricing_for(provider: str, model: str) -> dict[str, float] | None:
    key = (_clean_text(provider).lower(), _clean_text(model))
    pricing = _DEFAULT_PRICING.get(key)
    return dict(pricing) if pricing is not None else None


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
    pricing = _pricing_for(normalized_provider, normalized_model)

    known_cost: float | None = 0.0
    unknown_cost = False
    if pricing is None:
        if any(
            [
                input_token_count,
                output_token_count,
                prompt_token_count,
                completion_token_count,
                embedding_token_count,
            ]
        ):
            known_cost = None
            unknown_cost = True
    else:
        if input_token_count or prompt_token_count:
            rate = pricing.get("input_per_1k")
            if rate is None:
                known_cost = None
                unknown_cost = True
            elif known_cost is not None:
                known_cost += (prompt_token_count / 1000.0) * _safe_float(rate)
        if output_token_count or completion_token_count:
            rate = pricing.get("output_per_1k")
            if rate is None:
                known_cost = None
                unknown_cost = True
            elif known_cost is not None:
                known_cost += (completion_token_count / 1000.0) * _safe_float(rate)
        if embedding_token_count:
            rate = pricing.get("embedding_per_1k")
            if rate is None:
                known_cost = None
                unknown_cost = True
            elif known_cost is not None:
                known_cost += (embedding_token_count / 1000.0) * _safe_float(rate)
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
        "known_cost": None if known_cost is None else round(known_cost, 6),
        "unknown_cost": bool(unknown_cost),
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
        "known_cost_total": 0.0,
        "unknown_cost_present": False,
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
        if entry.get("unknown_cost"):
            totals["unknown_cost_present"] = True
        if entry.get("known_cost") is not None:
            totals["known_cost_total"] += _safe_float(entry.get("known_cost"))
        provider = _clean_text(entry.get("provider")).lower()
        model = _clean_text(entry.get("model"))
        group = grouped.setdefault(
            (provider, model),
            {
                "provider": provider,
                "model": model,
                "input_tokens": 0,
                "output_tokens": 0,
                "embedding_tokens": 0,
                "known_cost": 0.0,
                "unknown_cost": False,
            },
        )
        group["input_tokens"] += _safe_int(entry.get("input_tokens"))
        group["output_tokens"] += _safe_int(entry.get("output_tokens"))
        group["embedding_tokens"] += _safe_int(entry.get("embedding_tokens"))
        if entry.get("known_cost") is not None:
            group["known_cost"] += _safe_float(entry.get("known_cost"))
        if entry.get("unknown_cost"):
            group["unknown_cost"] = True
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
        "known_cost_total": round(float(totals["known_cost_total"]), 6),
        "unknown_cost_present": bool(totals["unknown_cost_present"]),
        "cost_by_model": [
            {
                **item,
                "known_cost": round(_safe_float(item.get("known_cost")), 6),
                "unknown_cost": bool(item.get("unknown_cost")),
            }
            for item in grouped.values()
        ],
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
