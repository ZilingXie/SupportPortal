from __future__ import annotations

import io
import unittest
import urllib.error
from unittest.mock import patch

from backend.services.rag_service_client import (
    RagServiceClient,
    RagServiceError,
    map_rag_payload_to_ticket_answer,
)


class RagServiceClientTests(unittest.TestCase):
    def test_map_answer_payload_to_ticket_answer(self) -> None:
        answer, confidence, sources, citations, needs_engineer = map_rag_payload_to_ticket_answer(
            {
                "decision": "answer",
                "answer": "Use the REST API endpoint.",
                "confidence": 0.88,
                "sources": ["https://docs.agora.io/en/example"],
                "citations": [
                    {
                        "chunk_id": "chunk-1",
                        "source_path": "official/agora.md",
                        "heading": "API",
                    }
                ],
            },
            insufficient_reply="INSUFFICIENT",
        )
        self.assertEqual(answer, "Use the REST API endpoint.")
        self.assertEqual(confidence, 0.88)
        self.assertEqual(sources, ["https://docs.agora.io/en/example"])
        self.assertEqual(citations[0]["chunk_id"], "chunk-1")
        self.assertFalse(needs_engineer)

    def test_map_escalate_payload_to_ticket_answer(self) -> None:
        answer, confidence, sources, citations, needs_engineer = map_rag_payload_to_ticket_answer(
            {
                "decision": "escalate",
                "confidence": 0.31,
                "reason": "insufficient_evidence",
            },
            insufficient_reply="INSUFFICIENT",
        )
        self.assertEqual(answer, "INSUFFICIENT")
        self.assertEqual(confidence, 0.31)
        self.assertEqual(sources, [])
        self.assertEqual(citations, [])
        self.assertTrue(needs_engineer)

    def test_probe_health_returns_disabled_without_base_url(self) -> None:
        client = RagServiceClient(base_url="")
        payload = client.probe_health()
        self.assertEqual(payload["status"], "disabled")
        self.assertEqual(payload["knowledge_storage"], "disabled")

    def test_request_raises_rag_service_error_for_timeout(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            with self.assertRaises(RagServiceError) as ctx:
                client.query(
                    question="How do I join a channel?",
                    request_id="rag-1",
                    ticket_id="T-001",
                    customer_id="C-001",
                )
        self.assertIsNone(ctx.exception.status_code)

    def test_request_raises_rag_service_error_for_http_500(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        error = urllib.error.HTTPError(
            url="http://rag-api.internal/internal/rag/query",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"boom"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RagServiceError) as ctx:
                client.query(
                    question="How do I join a channel?",
                    request_id="rag-2",
                    ticket_id="T-001",
                    customer_id="C-001",
                )
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.payload, {"detail": "boom"})

    def test_get_ingestion_report_uses_report_endpoint(self) -> None:
        client = RagServiceClient(base_url="http://rag-api.internal", shared_token="token")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"summary":{"ingestion_id":"KI-REPORT"}}'

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["authorization"] = request.headers.get("Authorization")
            return _FakeResponse()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            payload = client.get_ingestion_report("KI-REPORT")

        self.assertEqual(
            captured["url"],
            "http://rag-api.internal/internal/knowledge/ingestions/KI-REPORT/report",
        )
        self.assertEqual(captured["authorization"], "Bearer token")
        self.assertEqual(payload["summary"]["ingestion_id"], "KI-REPORT")


if __name__ == "__main__":
    unittest.main()
