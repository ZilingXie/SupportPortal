from __future__ import annotations

import unittest

from backend.services.token_usage import (
    aggregate_usage_ledger,
    build_usage_ledger_entry,
    resolve_ticket_family_identity,
)


class TokenUsageTests(unittest.TestCase):
    def test_openai_gpt_5_4_usage_entry_records_token_fields(self) -> None:
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
        self.assertEqual(entry["prompt_tokens"], 1200)
        self.assertEqual(entry["completion_tokens"], 300)

    def test_siliconflow_usage_entry_preserves_provider_model_breakout(self) -> None:
        entry = build_usage_ledger_entry(
            provider="siliconflow",
            model="deepseek-ai/DeepSeek-V3.2",
            stage="benchmark_judge",
            input_tokens=1000,
            output_tokens=500,
        )

        self.assertEqual(entry["provider"], "siliconflow")
        self.assertEqual(entry["model"], "deepseek-ai/DeepSeek-V3.2")
        self.assertEqual(entry["input_tokens"], 1000)
        self.assertEqual(entry["output_tokens"], 500)

    def test_unknown_usage_fields_are_preserved(self) -> None:
        entry = build_usage_ledger_entry(
            provider="siliconflow",
            model="unknown/model",
            stage="benchmark_judge",
            input_tokens=1000,
            output_tokens=500,
            unknown_usage_fields=["cached_input_tokens"],
        )

        self.assertEqual(entry["unknown_usage_fields"], ["cached_input_tokens"])

    def test_aggregate_usage_ledger_rolls_up_totals_and_token_breakdown(self) -> None:
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
        self.assertEqual(summary["total_prompt_tokens"], 1400)
        self.assertEqual(summary["total_completion_tokens"], 350)
        self.assertEqual(len(summary["token_by_model"]), 2)

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
