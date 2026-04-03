from __future__ import annotations

import unittest

from backend.services.token_usage import (
    aggregate_usage_ledger,
    build_usage_ledger_entry,
    resolve_ticket_family_identity,
)


class TokenUsageTests(unittest.TestCase):
    def test_openai_gpt_5_4_usage_entry_calculates_known_cost(self) -> None:
        entry = build_usage_ledger_entry(
            provider="openai",
            model="gpt-5.4",
            stage="rag_answer",
            input_tokens=1200,
            output_tokens=300,
            prompt_tokens=1200,
            completion_tokens=300,
        )

        self.assertEqual(entry["provider"], "openai")
        self.assertEqual(entry["model"], "gpt-5.4")
        self.assertEqual(entry["input_tokens"], 1200)
        self.assertEqual(entry["output_tokens"], 300)
        self.assertGreater(float(entry["known_cost"] or 0.0), 0.0)
        self.assertFalse(entry["unknown_cost"])

    def test_siliconflow_usage_entry_uses_provider_qualified_pricing(self) -> None:
        entry = build_usage_ledger_entry(
            provider="siliconflow",
            model="deepseek-ai/DeepSeek-V3.2",
            stage="benchmark_judge",
            input_tokens=1000,
            output_tokens=500,
        )

        self.assertEqual(entry["provider"], "siliconflow")
        self.assertEqual(entry["model"], "deepseek-ai/DeepSeek-V3.2")
        self.assertGreater(float(entry["known_cost"] or 0.0), 0.0)
        self.assertFalse(entry["unknown_cost"])

    def test_unknown_pricing_marks_usage_as_unknown_instead_of_zero(self) -> None:
        entry = build_usage_ledger_entry(
            provider="siliconflow",
            model="unknown/model",
            stage="benchmark_judge",
            input_tokens=1000,
            output_tokens=500,
        )

        self.assertIsNone(entry["known_cost"])
        self.assertTrue(entry["unknown_cost"])

    def test_aggregate_usage_ledger_rolls_up_totals_and_provider_breakdown(self) -> None:
        summary = aggregate_usage_ledger(
            [
                build_usage_ledger_entry(
                    provider="openai",
                    model="gpt-5.4",
                    stage="rag_answer",
                    input_tokens=1200,
                    output_tokens=300,
                    prompt_tokens=1200,
                    completion_tokens=300,
                ),
                build_usage_ledger_entry(
                    provider="openai",
                    model="gpt-5.4-mini",
                    stage="query_expansion",
                    input_tokens=200,
                    output_tokens=50,
                    prompt_tokens=200,
                    completion_tokens=50,
                ),
            ]
        )

        self.assertEqual(summary["total_input_tokens"], 1400)
        self.assertEqual(summary["total_output_tokens"], 350)
        self.assertFalse(summary["unknown_cost_present"])
        self.assertEqual(len(summary["cost_by_model"]), 2)

    def test_resolve_ticket_family_identity_prefers_client_ticket_reference(self) -> None:
        summary = resolve_ticket_family_identity(
            {
                "ticket_id": "TK-040-1",
                "client_ticket_id": "TK-040",
                "client_ticket_ref": {"ticket_id": "TK-040"},
            },
            related_ticket_ids=["TK-040-1", "TK-040-2"],
        )

        self.assertEqual(summary["canonical_ticket_id"], "TK-040")
        self.assertEqual(summary["related_ticket_ids"], ["TK-040-1", "TK-040-2"])


if __name__ == "__main__":
    unittest.main()
