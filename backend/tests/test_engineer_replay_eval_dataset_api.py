"""API tests for engineer replay eval dataset endpoints."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository


class EngineerReplayEvalDatasetApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        main.ticket_repository = self.original_repository

    def _seed_item(self, eval_item_id: str, dataset_status: str = "candidate") -> None:
        self.repository.record_engineer_replay_eval_item(
            {
                "eval_item_id": eval_item_id,
                "client_ticket_id": "TK-TEST",
                "engineer_case_id": "EC-TEST-1",
                "source_summary_packet_id": "summary_EC-TEST-1",
                "source_summary_packet_version": "engineer-summary-packet-v1",
                "review_decision": "ready_for_engineer",
                "review_trace": {"decisions": []},
                "replan_notes": [],
                "engineer_revise_feedback": [],
                "approved_reply": "Please upgrade to SDK 4.2.2.",
                "guardrail_final": {
                    "guardrail_id": "GRD-test",
                    "decision": "approved_for_final_engineer_review",
                },
                "expected_outcome": "resolved_with_customer_reply",
                "replay_input": {"summary_packet_id": "summary_EC-TEST-1"},
                "reference_output": {"approved_reply": "Please upgrade to SDK 4.2.2."},
                "dataset_status": dataset_status,
                "schema_version": "engineer-replay-eval-dataset-v1",
                "data_quality_warnings": [],
                "created_at": "2026-06-01T09:00:00Z",
                "updated_at": "2026-06-01T09:00:00Z",
            }
        )

    def test_list_returns_items(self) -> None:
        self._seed_item("ereplay_EC-TEST-1")
        self._seed_item("ereplay_EC-TEST-2")

        response = self.client.get("/api/engineer/replay-eval-dataset")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual(len(payload["items"]), 2)

    def test_list_filters_by_status(self) -> None:
        self._seed_item("ereplay_EC-1", dataset_status="candidate")
        self._seed_item("ereplay_EC-2", dataset_status="active")

        response = self.client.get("/api/engineer/replay-eval-dataset?status=active")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["eval_item_id"], "ereplay_EC-2")

    def test_list_empty_returns_empty(self) -> None:
        response = self.client.get("/api/engineer/replay-eval-dataset")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["items"], [])

    def test_export_returns_jsonl(self) -> None:
        self._seed_item("ereplay_EC-TEST-1")
        self._seed_item("ereplay_EC-TEST-2")

        response = self.client.get("/api/engineer/replay-eval-dataset/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-type"), "application/x-ndjson")

        lines = response.text.strip().split("\n")
        self.assertEqual(len(lines), 2)
        for line in lines:
            item = json.loads(line)
            self.assertIn("eval_item_id", item)
            self.assertEqual(item["dataset_status"], "candidate")

    def test_export_empty_returns_empty(self) -> None:
        response = self.client.get("/api/engineer/replay-eval-dataset/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.strip(), "")


if __name__ == "__main__":
    unittest.main()
