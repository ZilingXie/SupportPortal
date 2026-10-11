from types import SimpleNamespace

import pytest

from backend.services.llm_pricing_discovery import PricingDiscoveryError, discover_pricing, parse_pricing_result


def test_discovery_requires_exact_model_and_source_url():
    result = parse_pricing_result('{"provider":"openai","model":"gpt-x","input_usd_per_1m":1,"output_usd_per_1m":2,"source_url":"https://openai.com/pricing"}', provider="openai", model="gpt-x")
    assert result["pricing_source"] == "assistant_found"
    with pytest.raises(PricingDiscoveryError, match="model mismatch"):
        parse_pricing_result('{"provider":"openai","model":"gpt-y","input_usd_per_1m":1,"source_url":"https://openai.com"}', provider="openai", model="gpt-x")
    with pytest.raises(PricingDiscoveryError, match="source_url"):
        parse_pricing_result('{"provider":"openai","model":"gpt-x","input_usd_per_1m":1}', provider="openai", model="gpt-x")


def test_discovery_reuses_existing_search_boundary():
    calls = []
    def search(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return SimpleNamespace(answer='{"provider":"openai","model":"gpt-x","input_usd_per_1m":1,"output_usd_per_1m":2,"source_url":"https://openai.com/pricing"}')
    result = discover_pricing("openai", "gpt-x", search=search)
    assert result["pricing_source"] == "assistant_found"
    assert calls and "exact model" in calls[0][0]
