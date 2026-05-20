from __future__ import annotations

import unittest

from backend.services.rag_deadline import RagDeadline


class RagDeadlineTests(unittest.TestCase):
    def test_remaining_seconds_uses_total_budget_and_stage_cap(self) -> None:
        now = 10.0

        def clock() -> float:
            return now

        deadline = RagDeadline(
            started_at=10.0,
            total_seconds=5.0,
            stage_timeout_seconds={"query_understanding": 1.5},
            clock=clock,
        )

        self.assertEqual(deadline.remaining_seconds("query_understanding"), 1.5)
        self.assertEqual(deadline.remaining_seconds("answer_generation"), 5.0)

    def test_mark_timeout_preserves_first_timeout_stage(self) -> None:
        deadline = RagDeadline(started_at=10.0, total_seconds=5.0, clock=lambda: 10.0)

        deadline.mark_timeout("query_understanding")
        deadline.mark_timeout("warm_original_bm25")

        self.assertEqual(deadline.timeout_stage, "query_understanding")
        self.assertFalse(deadline.is_exhausted())

    def test_is_exhausted_uses_total_deadline(self) -> None:
        now = 16.0
        deadline = RagDeadline(started_at=10.0, total_seconds=5.0, clock=lambda: now)

        self.assertTrue(deadline.is_exhausted())
        self.assertEqual(deadline.remaining_seconds("answer_generation"), 0.0)


if __name__ == "__main__":
    unittest.main()
