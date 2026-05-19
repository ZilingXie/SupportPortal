from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.rag_request_body_evidence import (
    RequestBodyEvidenceQuery,
    detect_request_body_evidence_query,
    merge_request_body_evidence_chunks,
    run_request_body_evidence_skill,
)


class RequestBodyEvidenceTests(unittest.TestCase):
    def test_json_payload_triggers_detector_and_extracts_nested_paths(self) -> None:
        query = detect_request_body_evidence_query(
            """
            POST /v1/apps/app-id/cloud_recording/resourceid/sid/mode/mix/updateLayout
            {
              "clientRequest": {
                "mixedVideoLayout": 3,
                "layoutConfig": [{"uid": "2", "x_axis": 0, "y_axis": 0, "width": 1, "height": 1}]
              }
            }
            """
        )

        self.assertTrue(query.is_request_body_or_api_config)
        self.assertEqual(query.http_methods, ["POST"])
        self.assertIn("/v1/apps/app-id/cloud_recording/resourceid/sid/mode/mix/updateLayout", query.endpoint_hints)
        self.assertIn("clientRequest", query.body_keys)
        self.assertIn("clientRequest.layoutConfig[].width", query.nested_paths)
        self.assertIn("clientRequest.mixedVideoLayout", query.nested_paths)
        self.assertIn("layoutConfig schema", " ".join(query.schema_evidence_goals))

    def test_python_requests_json_payload_triggers_detector(self) -> None:
        query = detect_request_body_evidence_query(
            "requests.post('https://api.example.com/v1/start', json={'clientRequest': {'tokenName': 'abc'}})"
        )

        self.assertTrue(query.is_request_body_or_api_config)
        self.assertEqual(query.http_methods, ["POST"])
        self.assertIn("/v1/start", query.endpoint_hints)
        self.assertIn("clientRequest.tokenName", query.nested_paths)

    def test_curl_payload_triggers_detector(self) -> None:
        query = detect_request_body_evidence_query(
            "curl -X PATCH https://api.example.com/v1/config -d '{\"layoutConfig\":{\"width\":640}}'"
        )

        self.assertTrue(query.is_request_body_or_api_config)
        self.assertEqual(query.http_methods, ["PATCH"])
        self.assertIn("/v1/config", query.endpoint_hints)
        self.assertIn("layoutConfig.width", query.nested_paths)

    def test_natural_language_how_to_does_not_trigger(self) -> None:
        query = detect_request_body_evidence_query("How do I join a channel?")

        self.assertFalse(query.is_request_body_or_api_config)
        self.assertEqual(query.body_keys, [])
        self.assertEqual(query.nested_paths, [])

    def test_llm_bad_json_falls_back_to_rules(self) -> None:
        with patch("backend.services.rag_request_body_evidence.invoke_responses_text") as invoke_mock:
            invoke_mock.return_value.text = "not json"
            query = detect_request_body_evidence_query(
                "fetch('/v1/start', {method: 'POST', body: JSON.stringify({clientRequest: {recordingConfig: {channelType: 1}}})})",
                use_llm=True,
            )

        self.assertTrue(query.is_request_body_or_api_config)
        self.assertIn("/v1/start", query.endpoint_hints)
        self.assertIn("clientRequest.recordingConfig.channelType", query.nested_paths)

    def test_evidence_skill_selects_schema_chunks_and_reports_missing_fields(self) -> None:
        request = RequestBodyEvidenceQuery(
            is_request_body_or_api_config=True,
            confidence=0.92,
            endpoint_hints=["/v1/start"],
            http_methods=["POST"],
            body_keys=["clientRequest"],
            nested_paths=["clientRequest.layoutConfig.width", "clientRequest.recordingConfig.channelType"],
            field_value_hints={},
            question_need="correct_payload",
            schema_evidence_goals=["layoutConfig schema", "recordingConfig schema"],
        )

        def _fake_retrieve(search_query: str, evidence_type: str) -> list[dict[str, object]]:
            if "layoutConfig" in search_query:
                return [
                    {
                        "chunk_id": "schema-layout",
                        "text": "Request body schema: clientRequest.layoutConfig.width is a number.",
                        "source_path": "cloud-recording/api-reference.md",
                        "similarity": 0.91,
                    }
                ]
            if "recordingConfig" in search_query:
                return []
            return [
                {
                    "chunk_id": "example-start",
                    "text": "Payload example for POST /v1/start.",
                    "source_path": "cloud-recording/examples.md",
                    "similarity": 0.71,
                }
            ]

        result = run_request_body_evidence_skill(request, retrieve_chunks=_fake_retrieve)

        self.assertTrue(result.triggered)
        self.assertEqual(result.chunks[0].chunk_id, "schema-layout")
        self.assertEqual(result.chunks[0].evidence_type, "nested_schema")
        self.assertIn("clientRequest.layoutConfig.width", result.chunks[0].matched_fields)
        self.assertIn("clientRequest.recordingConfig.channelType", result.missing_evidence)

    def test_merge_preserves_schema_slots_over_overview_chunks(self) -> None:
        overview = {
            "chunk_id": "overview-1",
            "text": "Cloud Recording product overview and release notes.",
            "source_path": "cloud-recording/release-notes.md",
            "similarity": 0.98,
        }
        how_to = {
            "chunk_id": "howto-1",
            "text": "Start cloud recording.",
            "source_path": "cloud-recording/start.md",
            "similarity": 0.88,
        }
        schema = {
            "chunk_id": "schema-1",
            "text": "Request body schema: clientRequest.layoutConfig.width.",
            "source_path": "cloud-recording/api-reference.md",
            "similarity": 0.72,
            "metadata": {"request_body_evidence_type": "nested_schema"},
        }

        merged = merge_request_body_evidence_chunks(
            primary_chunks=[overview, how_to],
            supplement_chunks=[schema],
            max_chunks=2,
        )

        self.assertEqual([chunk["chunk_id"] for chunk in merged], ["schema-1", "howto-1"])

    def test_merge_preserves_high_value_technical_case_when_schema_slots_are_tight(self) -> None:
        technical_case = {
            "chunk_id": "technical-root-cause",
            "text": (
                "Issue Description: Cloud Recording records the screen share as a vertical strip. "
                "Root Cause: transcodingConfig is outside recordingConfig. "
                "Step by Step Solution: move transcodingConfig under recordingConfig."
            ),
            "source_path": "technical/mix-mode-cloud-recording-output.md",
            "similarity": 0.95,
            "metadata": {
                "source_type": "technical_article_api",
                "chunk_strategy": "technical_case_units_v1",
                "chunk_type": "troubleshooting_procedure",
            },
        }
        overview = {
            "chunk_id": "overview-1",
            "text": "Cloud Recording product overview and release notes.",
            "source_path": "cloud-recording/release-notes.md",
            "similarity": 0.99,
        }
        schemas = [
            {
                "chunk_id": f"schema-{index}",
                "text": f"Request body schema {index}: clientRequest.recordingConfig.transcodingConfig.",
                "source_path": "cloud-recording/api-reference.md",
                "similarity": 0.8 - (index * 0.01),
                "metadata": {"request_body_evidence_type": "nested_schema"},
            }
            for index in range(1, 4)
        ]

        merged = merge_request_body_evidence_chunks(
            primary_chunks=[overview, technical_case],
            supplement_chunks=schemas,
            max_chunks=3,
        )

        merged_ids = [chunk["chunk_id"] for chunk in merged]
        self.assertIn("technical-root-cause", merged_ids)
        self.assertTrue(any(chunk_id.startswith("schema-") for chunk_id in merged_ids))
        self.assertNotIn("overview-1", merged_ids)


if __name__ == "__main__":
    unittest.main()
