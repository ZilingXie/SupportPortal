from __future__ import annotations

import unittest

from backend.services.llm_pricing import LLM_PRICING_USD_PER_1M, estimate_token_usage_cost_usd


def _usage(models: list[dict]) -> dict:
    return {"token_by_model": models}


class LlmPricingTests(unittest.TestCase):
    def test_unpriced_models_report_unavailable_instead_of_zero(self) -> None:
        estimate = estimate_token_usage_cost_usd(
            _usage([{"provider": "openai", "model": "gpt-5.4", "input_tokens": 1000, "output_tokens": 100}])
        )
        self.assertFalse(estimate["available"])
        self.assertIsNone(estimate["total_usd"])
        self.assertEqual(estimate["by_model"][0]["usd"], None)

    def test_unknown_model_is_unpriced(self) -> None:
        estimate = estimate_token_usage_cost_usd(
            _usage([{"provider": "openai", "model": "gpt-unknown", "input_tokens": 5, "output_tokens": 1}])
        )
        self.assertFalse(estimate["available"])
        self.assertIsNone(estimate["total_usd"])

    def test_priced_usage_sums_across_models(self) -> None:
        original = dict(LLM_PRICING_USD_PER_1M)
        try:
            LLM_PRICING_USD_PER_1M.update(
                {
                    "openai:gpt-5.4": {"input": 1.0, "output": 2.0, "cached_input": 0.1},
                    "siliconflow:BAAI/bge-m3": {"embedding": 0.02},
                }
            )
            estimate = estimate_token_usage_cost_usd(
                _usage(
                    [
                        # 900 uncached + 100 cached in, 50 out => (900*1 + 100*0.1 + 50*2)/1M
                        {
                            "provider": "openai",
                            "model": "gpt-5.4",
                            "input_tokens": 1000,
                            "cached_input_tokens": 100,
                            "output_tokens": 50,
                        },
                        {"provider": "siliconflow", "model": "BAAI/bge-m3", "embedding_tokens": 1000},
                    ]
                )
            )
        finally:
            LLM_PRICING_USD_PER_1M.clear()
            LLM_PRICING_USD_PER_1M.update(original)
        self.assertTrue(estimate["available"])
        # (900*1 + 100*0.1 + 50*2) = 1010; 1000*0.02 = 20 => 1030 / 1M
        self.assertAlmostEqual(estimate["total_usd"], 1030 / 1_000_000)

    def test_cached_price_falls_back_to_input_price_when_unset(self) -> None:
        original = dict(LLM_PRICING_USD_PER_1M)
        try:
            LLM_PRICING_USD_PER_1M["openai:gpt-5.4-mini"] = {"input": 0.5, "output": 1.0, "cached_input": None}
            estimate = estimate_token_usage_cost_usd(
                _usage(
                    [
                        {
                            "provider": "openai",
                            "model": "gpt-5.4-mini",
                            "input_tokens": 200,
                            "cached_input_tokens": 200,
                            "output_tokens": 0,
                        }
                    ]
                )
            )
        finally:
            LLM_PRICING_USD_PER_1M.clear()
            LLM_PRICING_USD_PER_1M.update(original)
        self.assertTrue(estimate["available"])
        self.assertAlmostEqual(estimate["total_usd"], 200 * 0.5 / 1_000_000)

    def test_cached_tokens_never_exceed_input_tokens(self) -> None:
        original = dict(LLM_PRICING_USD_PER_1M)
        try:
            LLM_PRICING_USD_PER_1M["openai:gpt-5.4"] = {"input": 1.0, "output": 2.0, "cached_input": 0.0}
            estimate = estimate_token_usage_cost_usd(
                _usage(
                    [
                        {
                            "provider": "openai",
                            "model": "gpt-5.4",
                            "input_tokens": 100,
                            "cached_input_tokens": 500,  # inconsistent upstream data
                            "output_tokens": 0,
                        }
                    ]
                )
            )
        finally:
            LLM_PRICING_USD_PER_1M.clear()
            LLM_PRICING_USD_PER_1M.update(original)
        self.assertTrue(estimate["available"])
        self.assertAlmostEqual(estimate["total_usd"], 0.0)

    def test_empty_usage_costs_zero(self) -> None:
        estimate = estimate_token_usage_cost_usd(_usage([]))
        self.assertTrue(estimate["available"])
        self.assertEqual(estimate["total_usd"], 0.0)
        self.assertEqual(estimate["by_model"], [])

    def test_default_table_ships_unpriced(self) -> None:
        for prices in LLM_PRICING_USD_PER_1M.values():
            for price in prices.values():
                self.assertIsNone(price)


if __name__ == "__main__":
    unittest.main()
