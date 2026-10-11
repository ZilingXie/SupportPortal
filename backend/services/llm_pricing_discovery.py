"""Strict model-price discovery using the existing OpenAI Web Search profile."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from backend.services.support_router import _openai_web_search


class PricingDiscoveryError(ValueError):
    pass


def parse_pricing_result(text: str, *, provider: str, model: str, source_url: str | None = None) -> dict[str, Any]:
    match = re.search(r"\{.*\}", str(text or ""), flags=re.DOTALL)
    if not match:
        raise PricingDiscoveryError("search result is not JSON")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise PricingDiscoveryError("invalid pricing JSON") from exc
    if str(payload.get("provider") or "").strip().lower() != str(provider).strip().lower():
        raise PricingDiscoveryError("provider mismatch")
    if str(payload.get("model") or "").strip() != str(model).strip():
        raise PricingDiscoveryError("model mismatch")
    url = str(payload.get("source_url") or source_url or "").strip()
    if not url.startswith(("https://", "http://")):
        raise PricingDiscoveryError("source_url is required")
    rates = {key: payload.get(key) for key in ("input_usd_per_1m", "cached_input_usd_per_1m", "output_usd_per_1m", "embedding_usd_per_1m")}
    if not any(value is not None for value in rates.values()):
        raise PricingDiscoveryError("no pricing rate found")
    try:
        rates = {key: (None if value is None else float(value)) for key, value in rates.items()}
    except (TypeError, ValueError) as exc:
        raise PricingDiscoveryError("pricing rates must be numeric") from exc
    if any(value is not None and value < 0 for value in rates.values()):
        raise PricingDiscoveryError("pricing rates must be non-negative")
    now = datetime.now(timezone.utc).isoformat()
    return {"provider": str(provider).strip().lower(), "model": str(model).strip(), **rates, "pricing_source": "assistant_found", "source_url": url, "observed_at": now, "last_checked_at": now, "last_error": None}


def discover_pricing(provider: str, model: str, *, search: Callable[..., Any] | None = None) -> dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model or "").strip()
    if not normalized_provider or not normalized_model:
        raise PricingDiscoveryError("provider and model are required")
    search_fn = search or _openai_web_search
    result = search_fn(
        f"Find the official API pricing for the exact model {normalized_provider}:{normalized_model}. Return JSON only with provider, model, input_usd_per_1m, cached_input_usd_per_1m, output_usd_per_1m, embedding_usd_per_1m, source_url. Reject aliases, tiers, regions, and uncertain values.",
        response_language="en",
        allowed_domains=None,
        route_reason="llm_pricing_discovery",
    )
    if result is None:
        raise PricingDiscoveryError("web search unavailable")
    return parse_pricing_result(getattr(result, "answer", result), provider=normalized_provider, model=normalized_model)
