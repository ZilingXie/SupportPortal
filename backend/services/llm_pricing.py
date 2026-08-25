"""USD pricing table and estimation for per-case LLM token usage.

Prices are USD per 1M tokens and default to None (unpriced). A model without
a full set of prices is reported as unpriced rather than silently costing 0,
matching the repository's unknown-cost marker convention (see
docs/prompt_change_log.md "gpt54-token-only-observability-v1").
"""

from __future__ import annotations

from typing import Any

LLM_PRICING_USD_PER_1M: dict[str, dict[str, float | None]] = {
    # gpt-5.6-luna rates from the official pricing page
    # (developers.openai.com/api/docs/models/gpt-5.6-luna). Legacy models stay
    # unpriced rather than guessing rates; cached_input is optional; when None
    # it falls back to the input price.
    "openai:gpt-5.4": {"input": None, "output": None, "cached_input": None},
    "openai:gpt-5.4-mini": {"input": None, "output": None, "cached_input": None},
    "openai:gpt-5.4-nano": {"input": None, "output": None, "cached_input": None},
    "openai:gpt-5.6-luna": {"input": 0.2, "output": 1.2, "cached_input": 0.02},
    "deepseek:deepseek-v4-pro": {"input": None, "output": None, "cached_input": None},
    "siliconflow:BAAI/bge-m3": {"embedding": None},
}

_PRICING_DIMENSIONS = ("input", "output", "cached_input", "embedding")


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _pricing_key(provider: Any, model: Any) -> str:
    normalized_provider = " ".join(str(provider or "").split()).strip().lower()
    normalized_model = " ".join(str(model or "").split()).strip()
    return f"{normalized_provider}:{normalized_model}"


def _model_cost_usd(prices: dict[str, float | None], model_usage: dict[str, Any]) -> float | None:
    embedding_tokens = _safe_int(model_usage.get("embedding_tokens"))
    has_text_usage = any(_safe_int(model_usage.get(key)) for key in ("input_tokens", "output_tokens"))
    input_price = prices.get("input")
    output_price = prices.get("output")
    embedding_price = prices.get("embedding")
    if has_text_usage and (input_price is None or output_price is None):
        return None
    if embedding_tokens and embedding_price is None:
        return None
    cost = 0.0
    if has_text_usage and input_price is not None and output_price is not None:
        input_tokens = _safe_int(model_usage.get("input_tokens"))
        cached_tokens = min(_safe_int(model_usage.get("cached_input_tokens")), input_tokens)
        cached_price = prices.get("cached_input")
        if cached_price is None:
            cached_price = input_price
        # Billing semantics: input_tokens already includes cached tokens, so
        # price the uncached remainder at full input price.
        uncached_tokens = input_tokens - cached_tokens
        cost += uncached_tokens * input_price / 1_000_000
        cost += cached_tokens * cached_price / 1_000_000
        cost += _safe_int(model_usage.get("output_tokens")) * output_price / 1_000_000
    if embedding_tokens and embedding_price is not None:
        cost += embedding_tokens * embedding_price / 1_000_000
    return cost


def estimate_token_usage_cost_usd(token_usage: dict[str, Any] | None) -> dict[str, Any]:
    """Estimate USD cost for an aggregated token_usage payload.

    Returns {available, total_usd, by_model: [{provider, model, usd}]}. The
    estimate is available only when every model with usage carries full
    prices; otherwise total_usd is None and unpriced models report usd=None.
    """
    payload = dict(token_usage or {})
    models = [dict(item) for item in payload.get("token_by_model") or [] if isinstance(item, dict)]
    if not models:
        return {"available": True, "total_usd": 0.0, "by_model": []}
    by_model: list[dict[str, Any]] = []
    all_priced = True
    total = 0.0
    for model_usage in models:
        provider = model_usage.get("provider")
        model = model_usage.get("model")
        prices = LLM_PRICING_USD_PER_1M.get(_pricing_key(provider, model))
        if prices is None:
            by_model.append({"provider": provider, "model": model, "usd": None})
            all_priced = False
            continue
        usd = _model_cost_usd(prices, model_usage)
        by_model.append({"provider": provider, "model": model, "usd": usd})
        if usd is None:
            all_priced = False
        else:
            total += usd
    return {
        "available": all_priced,
        "total_usd": round(total, 6) if all_priced else None,
        "by_model": by_model,
    }


def model_pricing_payload() -> list[dict[str, Any]]:
    """Describe the pricing table for display, one entry per known model.

    Each entry carries the per-1M USD rates it has; models without any rate
    keep priced=False so the UI can show the explicit unpriced marker.
    """
    entries: list[dict[str, Any]] = []
    for key, prices in LLM_PRICING_USD_PER_1M.items():
        provider, _, model = key.partition(":")
        entries.append(
            {
                "provider": provider,
                "model": model,
                "input_usd_per_1m": prices.get("input"),
                "cached_input_usd_per_1m": prices.get("cached_input"),
                "output_usd_per_1m": prices.get("output"),
                "embedding_usd_per_1m": prices.get("embedding"),
                "priced": any(value is not None for value in prices.values()),
            }
        )
    return entries
