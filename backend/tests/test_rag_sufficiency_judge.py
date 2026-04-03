from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from backend.services.rag_sufficiency_judge import judge_rag_answer_sufficiency


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class RagSufficiencyJudgeTests(unittest.TestCase):
    def test_judge_rag_answer_sufficiency_retries_without_temperature_when_model_rejects_it(self) -> None:
        calls: list[dict[str, object]] = []
        payload = {
            "output_text": json.dumps(
                {
                    "decision": "answer",
                    "reason": "supported_after_retry",
                    "confidence": 0.91,
                }
            )
        }

        def _capture(request, timeout=None):
            calls.append(json.loads(request.data.decode("utf-8")))
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    url="https://api.openai.com/v1/responses",
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(
                        b'{"error":{"message":"Unsupported parameter: \'temperature\' is not supported with this model."}}'
                    ),
                )
            return _FakeResponse(payload)

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "RAG_SUFFICIENCY_JUDGE_MODEL": "gpt-5.4",
                "RAG_SUFFICIENCY_JUDGE_REASONING_EFFORT": "low",
                "RAG_SUFFICIENCY_JUDGE_TEMPERATURE": "0.0",
            },
            clear=True,
        ), patch("urllib.request.urlopen", side_effect=_capture):
            result = judge_rag_answer_sufficiency(
                message="how to join channel",
                ticket_subject="how to join channel",
                ticket_context=[{"role": "customer", "content": "how to join channel"}],
                route_summary={
                    "scope_label": "agora_technical",
                    "route_family": "agora_docs_rag",
                    "execution_action": "rag",
                    "tooling_profile": "docs_grounded_rag",
                    "reason": "rtc_join_channel_help",
                    "confidence": 0.98,
                    "matched_signals": ["join channel", "channel"],
                },
                rag_answer="Call the join method with the required channel options for your SDK.",
                sources=["Quickstart > Implement Broadcast Streaming > Join a channel"],
                citations=[
                    {
                        "title": "Quickstart > Implement Broadcast Streaming > Join a channel",
                        "source": "docs",
                    }
                ],
                packed_evidence={
                    "packed_context_text": "[chunk-1] official/join.md | Join a channel\nUse joinChannel with the same channel name.",
                    "packed_chunk_ids": ["chunk-1"],
                    "selected_contexts": [
                        {
                            "chunk_id": "chunk-1",
                            "source_path": "official/join.md",
                            "heading": "Join a channel",
                            "text_excerpt": "Use joinChannel with the same channel name.",
                        }
                    ],
                },
                evidence_summary={"top_k": 3},
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["temperature"], 0.0)
        self.assertNotIn("temperature", calls[1])
        self.assertEqual(calls[1]["model"], "gpt-5.4")
        self.assertEqual(calls[1]["reasoning"]["effort"], "low")
        self.assertIn("## Packed Evidence", calls[1]["input"][1]["content"][0]["text"])
        self.assertIn("packed_context_text", calls[1]["input"][1]["content"][0]["text"])
        self.assertEqual(result.decision, "answer")
        self.assertEqual(result.reason, "supported_after_retry")
        self.assertEqual(result.confidence, 0.91)


if __name__ == "__main__":
    unittest.main()
