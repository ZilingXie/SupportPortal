from __future__ import annotations

import io
import json
import os
import threading
import time
import urllib.error
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import backend.services.rag_qa as rag_qa
import psycopg

from backend.services.llm_factory import LlmTextResult
from backend.services.rag_context_budget import ContextBudget, PackedEvidence
from backend.services.rag_qa import (
    INSUFFICIENT_EVIDENCE_REPLY,
    RagExecutionCancelled,
    RetrievedChunk,
    _chunk_family_key,
    _extract_metadata_hints,
    _get_rag_config,
    _raise_if_cancelled,
    _metadata_rerank,
    _merge_request_body_evidence_into_final_chunks,
    probe_customer_rag_index_readiness,
    _retrieve_bm25_chunks,
    _rerank_chunks,
    _resolve_active_vector_table,
    _rrf_merge,
    _select_diverse_chunks,
    _select_bm25_query_terms,
    _split_table_name,
    _format_context,
    run_rag_query,
)
from backend.services.rag_request_body_evidence import (
    RequestBodyEvidenceChunk,
    RequestBodyEvidenceQuery,
    RequestBodyEvidenceResult,
)
from backend.services.query_understanding import QueryUnderstandingResult, RetrievalPlan, downpush_hard_filters


class RagQaHybridTests(unittest.TestCase):
    _BAN_API_MISMATCH_MESSAGE = """Hello, Agora team.

We are using the Ban User Privileges API (POST /dev/v1/kicking-rule) to disband channels after a broadcast ends, but we have found some differences between the official documentation and the actual API behavior.

1. uid: 0 cannot be used
According to the documentation
(https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel), when targeting all users in a channel, it says to use uid: 0. However, in actual use:
"uid": 0 (number) -> Error: uid '0' must be a number, or set str_uid = true

2. Cannot create a permanent rule with time: 0
The documentation states that time: 0 means the rule is applied permanently. However, when we actually send time: 0, the API returns {"status":"success","id":0}, but no rule is created."""

    class _FakeProvider:
        provider_name = "siliconflow"
        model_id = "BAAI/bge-m3"
        vector_dim = 1024

        def count_tokens(self, text: str) -> int:
            return max(1, len(str(text or "").split()))

        def drain_request_log(self) -> list[dict[str, object]]:
            return []

    def setUp(self) -> None:
        rag_qa._RUNTIME_CAPABILITY_UNAVAILABLE_UNTIL.clear()
        rag_qa.clear_active_vector_table_cache()
        self._env_backup = {
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
            "SILICONFLOW_API_KEY": os.environ.get("SILICONFLOW_API_KEY"),
            "SILLICONFLOW_KEY": os.environ.get("SILLICONFLOW_KEY"),
            "RAG_RERANK_API_KEY": os.environ.get("RAG_RERANK_API_KEY"),
        }
        for name in self._env_backup:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        rag_qa.clear_active_vector_table_cache()
        for name, value in self._env_backup.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_build_answer_text_formats_email_style_response(self) -> None:
        answer_text = rag_qa._build_answer_text(
            "To join a channel, call the SDK join method with the required authentication token.",
            [
                "Provide the channel name.",
                "Pass a valid authentication token.",
            ],
            question="How do I join a channel?",
        )

        self.assertTrue(answer_text.startswith("Hi there,"))
        self.assertIn(
            "To join a channel, call the SDK join method with the required authentication token.",
            answer_text,
        )
        self.assertIn("1. Provide the channel name.", answer_text)
        self.assertIn("2. Pass a valid authentication token.", answer_text)
        self.assertTrue(answer_text.endswith("Best Regards,\nSid"))

    def test_build_answer_text_wraps_raw_json_payload_as_fenced_json(self) -> None:
        answer_text = rag_qa._build_answer_text(
            "\n".join(
                [
                    "Use this structure instead:",
                    "",
                    "{",
                    ' "cname": "ch",',
                    ' "uid": "12345",',
                    ' "clientRequest": {',
                    '   "recordingConfig": {',
                    '     "channelType": 1,',
                    '     "transcodingConfig": {',
                    '       "width": 1280,',
                    '       "height": 720',
                    "     }",
                    "   }",
                    " }",
                    "}",
                    "",
                    "Then start a new recording session.",
                ]
            ),
            [],
            question="How can we record the whole canvas?",
        )

        fence_start = answer_text.find("```json")
        self.assertNotEqual(-1, fence_start)
        fenced_json = answer_text[fence_start + len("```json") :].split("```", 1)[0].strip()
        payload = json.loads(fenced_json)
        self.assertEqual(payload["clientRequest"]["recordingConfig"]["transcodingConfig"]["width"], 1280)
        self.assertNotIn('Use this structure instead:\n\n{', answer_text)
        self.assertIn("Then start a new recording session.", answer_text)

    def test_build_answer_text_does_not_duplicate_existing_step_numbers(self) -> None:
        answer_text = rag_qa._build_answer_text(
            "To join a channel, call the SDK join method.",
            [
                "1. Create or fetch a token.",
                "2. Call the join method.",
            ],
            question="How do I join a channel?",
        )

        self.assertIn("1. Create or fetch a token.", answer_text)
        self.assertIn("2. Call the join method.", answer_text)
        self.assertNotIn("1. 1. Create or fetch a token.", answer_text)
        self.assertNotIn("2. 2. Call the join method.", answer_text)

    def test_how_to_answer_appends_grounded_code_example_from_selected_chunk(self) -> None:
        code_chunk = RetrievedChunk(
            chunk_id="join-code",
            text=(
                "Use joinChannel to join a channel.\n\n"
                "```javascript\n"
                "client.join(appId, channelName, token, uid);\n"
                "```"
            ),
            source_path="official/join-channel.md",
            similarity=0.95,
        )

        answer, citation_ids = rag_qa._supplement_how_to_code_example_if_missing(
            "To join a channel, call the SDK join method with your token and UID.",
            question="How do I join a channel?",
            chunks=[code_chunk],
            citation_ids=[],
        )

        self.assertIn("Reference Example:", answer)
        self.assertIn("```javascript", answer)
        self.assertIn("client.join(appId, channelName, token, uid);", answer)
        self.assertEqual(citation_ids, ["join-code"])

    def test_how_to_answer_does_not_invent_code_without_chunk_code(self) -> None:
        text_chunk = RetrievedChunk(
            chunk_id="join-text",
            text="Use the join method with a channel name, token, and uid.",
            source_path="official/join-channel.md",
            similarity=0.95,
        )

        answer, citation_ids = rag_qa._supplement_how_to_code_example_if_missing(
            "To join a channel, call the SDK join method with your token and UID.",
            question="How do I join a channel?",
            chunks=[text_chunk],
            citation_ids=["join-text"],
        )

        self.assertNotIn("```", answer)
        self.assertEqual(citation_ids, ["join-text"])

    def test_split_table_name_supports_schema_prefix(self) -> None:
        self.assertEqual(_split_table_name("public.docagent"), ("public", "docagent"))
        self.assertEqual(
            _split_table_name("docagent_chunks_bge_m3_1024"),
            ("supportportal", "docagent_chunks_bge_m3_1024"),
        )

    def test_get_rag_config_uses_hybrid_candidate_windows(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _get_rag_config(top_k=6)
        self.assertEqual(config["top_k"], 6)
        self.assertEqual(config["vector_candidate_k"], 60)
        self.assertEqual(config["bm25_candidate_k"], 60)
        self.assertEqual(config["fusion_candidate_k"], 48)
        self.assertEqual(config["rerank_top_n"], 24)
        self.assertEqual(config["bm25_k1"], 1.2)
        self.assertEqual(config["bm25_b"], 0.75)
        self.assertEqual(config["rerank_provider"], "siliconflow")
        self.assertEqual(config["rerank_model"], "BAAI/bge-reranker-v2-m3")
        self.assertEqual(config["table"], "supportportal.docagent_chunks_bge_m3_1024")
        self.assertEqual(config["embedding_provider"], "siliconflow")
        self.assertEqual(config["embedding_model"], "BAAI/bge-m3")
        self.assertEqual(config["bm25_max_query_terms"], 6)
        self.assertEqual(config["bm25_max_term_doc_freq_ratio"], 0.08)
        self.assertTrue(config["shadow_retrieval_enabled"])

    def test_get_rag_config_reads_shadow_retrieval_flag(self) -> None:
        with patch.dict(os.environ, {"RAG_SHADOW_RETRIEVAL_ENABLED": "false"}, clear=True):
            config = _get_rag_config(top_k=6)

        self.assertFalse(config["shadow_retrieval_enabled"])

    def test_request_body_evidence_merge_preserves_schema_over_release_notes(self) -> None:
        overview = RetrievedChunk(
            chunk_id="overview-1",
            text="Cloud Recording product overview and release notes.",
            source_path="official/cloud-recording/release-notes.md",
            similarity=0.98,
        )
        how_to = RetrievedChunk(
            chunk_id="howto-1",
            text="Start cloud recording with the REST API.",
            source_path="official/cloud-recording/start.md",
            similarity=0.87,
        )
        schema = RetrievedChunk(
            chunk_id="schema-layout",
            text="Request body schema: clientRequest.layoutConfig.width is a number.",
            source_path="official/cloud-recording/api-reference.md",
            similarity=0.72,
        )
        evidence = RequestBodyEvidenceResult(
            triggered=True,
            query=RequestBodyEvidenceQuery(
                is_request_body_or_api_config=True,
                body_keys=["clientRequest"],
                nested_paths=["clientRequest.layoutConfig.width"],
                schema_evidence_goals=["layoutConfig schema"],
            ),
            chunks=[
                RequestBodyEvidenceChunk(
                    chunk_id="schema-layout",
                    evidence_type="nested_schema",
                    matched_fields=["clientRequest.layoutConfig.width"],
                    source_path=schema.source_path,
                    text_excerpt=schema.text,
                    similarity=schema.similarity,
                    original_chunk=schema,
                )
            ],
            missing_evidence=[],
        )

        merged = _merge_request_body_evidence_into_final_chunks(
            [overview, how_to],
            request_body_evidence=evidence,
            max_chunks=2,
        )

        self.assertEqual([chunk.chunk_id for chunk in merged], ["schema-layout", "howto-1"])
        self.assertEqual(merged[0].metadata["request_body_evidence_type"], "nested_schema")

    def test_request_body_evidence_merge_preserves_technical_root_cause_with_tight_schema_slots(self) -> None:
        technical_case = RetrievedChunk(
            chunk_id="technical-root-cause",
            text=(
                "Issue Description: Cloud Recording records the screen share as a vertical strip. "
                "Root Cause: transcodingConfig is outside recordingConfig. "
                "Step by Step Solution: move transcodingConfig under recordingConfig."
            ),
            source_path="technical/mix-mode-cloud-recording-output.md",
            similarity=0.95,
            source_type="technical_article_api",
            chunk_strategy="technical_case_units_v1",
            metadata={
                "source_type": "technical_article_api",
                "chunk_strategy": "technical_case_units_v1",
                "chunk_type": "troubleshooting_procedure",
            },
        )
        overview = RetrievedChunk(
            chunk_id="overview-1",
            text="Cloud Recording product overview and release notes.",
            source_path="official/cloud-recording/release-notes.md",
            similarity=0.99,
        )
        schema_chunks = [
            RetrievedChunk(
                chunk_id=f"schema-{index}",
                text=f"Request body schema {index}: clientRequest.recordingConfig.transcodingConfig.",
                source_path="official/cloud-recording/restful-api.md",
                similarity=0.8 - (index * 0.01),
            )
            for index in range(1, 4)
        ]
        evidence = RequestBodyEvidenceResult(
            triggered=True,
            query=RequestBodyEvidenceQuery(
                is_request_body_or_api_config=True,
                body_keys=["clientRequest"],
                nested_paths=["clientRequest.recordingConfig.transcodingConfig"],
                schema_evidence_goals=["transcodingConfig schema"],
            ),
            chunks=[
                RequestBodyEvidenceChunk(
                    chunk_id=chunk.chunk_id,
                    evidence_type="nested_schema",
                    matched_fields=["clientRequest.recordingConfig.transcodingConfig"],
                    source_path=chunk.source_path,
                    text_excerpt=chunk.text,
                    similarity=chunk.similarity,
                    original_chunk=chunk,
                )
                for chunk in schema_chunks
            ],
            missing_evidence=[],
        )

        merged = _merge_request_body_evidence_into_final_chunks(
            [overview, technical_case],
            request_body_evidence=evidence,
            max_chunks=3,
        )

        merged_ids = [chunk.chunk_id for chunk in merged]
        self.assertIn("technical-root-cause", merged_ids)
        self.assertTrue(any(chunk_id.startswith("schema-") for chunk_id in merged_ids))
        self.assertNotIn("overview-1", merged_ids)
        selected_schema = next(chunk for chunk in merged if chunk.chunk_id.startswith("schema-"))
        self.assertEqual(selected_schema.metadata["request_body_evidence_type"], "nested_schema")

    def test_request_body_evidence_merge_can_restore_retrieved_technical_candidate_after_rerank_drop(self) -> None:
        selected_schema_context = RetrievedChunk(
            chunk_id="schema-selected",
            text="Request body schema: clientRequest.recordingConfig.transcodingConfig.",
            source_path="official/cloud-recording/restful-api.md",
            similarity=0.88,
        )
        selected_start_context = RetrievedChunk(
            chunk_id="start-endpoint",
            text="Start cloud recording after acquire.",
            source_path="official/cloud-recording/restful-api.md",
            similarity=0.82,
        )
        technical_candidate = RetrievedChunk(
            chunk_id="technical-root-cause",
            text=(
                "Issue Description: Cloud Recording records the screen share as a vertical strip. "
                "Root Cause: transcodingConfig is outside recordingConfig. "
                "Step by Step Solution: move transcodingConfig under recordingConfig."
            ),
            source_path="technical/mix-mode-cloud-recording-output.md",
            similarity=0.75,
            source_type="technical_article_api",
            chunk_strategy="technical_case_units_v1",
            metadata={
                "source_type": "technical_article_api",
                "chunk_strategy": "technical_case_units_v1",
                "chunk_type": "troubleshooting_procedure",
            },
            candidate_trace={
                "retrieval_sources": ["bm25"],
                "bm25_score": 26.6,
                "rerank_rank": 45,
            },
        )
        schema_supplement = RetrievedChunk(
            chunk_id="schema-supplement",
            text="Schema: transcodingConfig contains mixedVideoLayout and layoutConfig.",
            source_path="official/cloud-recording/restful-api.md",
            similarity=0.8,
        )
        evidence = RequestBodyEvidenceResult(
            triggered=True,
            query=RequestBodyEvidenceQuery(
                is_request_body_or_api_config=True,
                body_keys=["clientRequest"],
                nested_paths=["clientRequest.recordingConfig.transcodingConfig"],
                schema_evidence_goals=["transcodingConfig schema"],
            ),
            chunks=[
                RequestBodyEvidenceChunk(
                    chunk_id="schema-supplement",
                    evidence_type="nested_schema",
                    matched_fields=["clientRequest.recordingConfig.transcodingConfig"],
                    source_path=schema_supplement.source_path,
                    text_excerpt=schema_supplement.text,
                    similarity=schema_supplement.similarity,
                    original_chunk=schema_supplement,
                )
            ],
            missing_evidence=[],
        )

        merged = _merge_request_body_evidence_into_final_chunks(
            [selected_schema_context, selected_start_context],
            request_body_evidence=evidence,
            max_chunks=3,
            retrieved_chunks=[technical_candidate, selected_schema_context, selected_start_context],
        )

        merged_ids = [chunk.chunk_id for chunk in merged]
        self.assertIn("technical-root-cause", merged_ids)
        self.assertIn("schema-supplement", merged_ids)
        self.assertLess(merged_ids.index("technical-root-cause"), len(merged_ids))

    def test_request_body_evidence_context_adds_supplement_section(self) -> None:
        schema = RetrievedChunk(
            chunk_id="schema-layout",
            text="Request body schema: clientRequest.layoutConfig.width is a number.",
            source_path="official/cloud-recording/api-reference.md",
            similarity=0.72,
            metadata={
                "request_body_evidence_type": "nested_schema",
                "request_body_matched_fields": ["clientRequest.layoutConfig.width"],
                "request_body_missing_evidence": ["exact layoutConfig schema not found"],
            },
        )

        context = _format_context([schema])

        self.assertIn("## Request Body Evidence Supplement", context)
        self.assertIn("[schema-layout] evidence_type=nested_schema", context)
        self.assertIn("matched_fields=clientRequest.layoutConfig.width", context)
        self.assertIn("Missing evidence:", context)
        self.assertIn("exact layoutConfig schema not found", context)

    def test_get_rag_config_applies_client_accuracy_first_profile(self) -> None:
        with patch.dict(os.environ, {"RAG_SHADOW_RETRIEVAL_ENABLED": "false"}, clear=True):
            config = _get_rag_config(top_k=6, query_policy="client_accuracy_first")

        self.assertEqual(config["top_k"], 8)
        self.assertEqual(config["vector_candidate_k"], 120)
        self.assertEqual(config["bm25_candidate_k"], 120)
        self.assertEqual(config["fusion_candidate_k"], 96)
        self.assertEqual(config["rerank_top_n"], 48)
        self.assertTrue(config["shadow_retrieval_enabled"])

    def test_get_rag_config_reads_lowercase_silliconflow_key_for_reranker(self) -> None:
        with patch.dict(os.environ, {"silliconflow_key": "test-rerank-key"}, clear=True):
            config = _get_rag_config(top_k=6)
        self.assertEqual(config["rerank_api_key"], "test-rerank-key")

    def test_get_rag_config_disables_vector_and_rerank_without_provider_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMBEDDING_PROVIDER": "siliconflow",
                "RAG_RERANK_PROVIDER": "siliconflow",
            },
            clear=True,
        ):
            config = _get_rag_config(top_k=6)

        self.assertFalse(config["vector_enabled"])
        self.assertFalse(config["rerank_enabled"])

    def test_raise_if_cancelled_uses_stage_name(self) -> None:
        with self.assertRaises(RagExecutionCancelled) as ctx:
            _raise_if_cancelled("answer_generation", should_cancel=lambda: True)

        self.assertEqual(ctx.exception.stage, "answer_generation")

    def test_run_rag_query_propagates_agentic_cancellation_without_legacy_fallback(self) -> None:
        with patch.object(
            rag_qa,
            "_run_rag_query_agentic",
            side_effect=RagExecutionCancelled("answer_generation"),
        ), patch.object(
            rag_qa,
            "_run_rag_query_legacy",
            side_effect=AssertionError("legacy fallback should not run for cancellations"),
        ):
            with self.assertRaises(RagExecutionCancelled) as ctx:
                run_rag_query("how to join channel")

        self.assertEqual(ctx.exception.stage, "answer_generation")

    def test_select_bm25_query_terms_filters_overly_common_terms(self) -> None:
        selected = _select_bm25_query_terms(
            terms=["agora", "token", "recommended", "app", "id"],
            term_doc_freqs={
                "agora": 29285,
                "token": 4497,
                "recommended": 684,
                "app": 10033,
                "id": 6528,
            },
            doc_count=65890,
            max_term_doc_freq_ratio=0.08,
            max_query_terms=6,
        )

        self.assertEqual(selected, ["recommended", "token"])

    def test_select_bm25_query_terms_falls_back_to_rarest_terms_when_all_are_common(self) -> None:
        selected = _select_bm25_query_terms(
            terms=["agora", "app", "id"],
            term_doc_freqs={
                "agora": 29285,
                "app": 10033,
                "id": 6528,
            },
            doc_count=65890,
            max_term_doc_freq_ratio=0.05,
            max_query_terms=2,
        )

        self.assertEqual(selected, ["id", "app"])

    def test_select_bm25_query_terms_discards_conversational_noise(self) -> None:
        selected = _select_bm25_query_terms(
            terms=["i", "m", "getting", "error", "109", "users", "join", "mean", "token", "expired"],
            term_doc_freqs={
                "i": 1,
                "m": 2,
                "getting": 12,
                "error": 4500,
                "109": 40,
                "users": 9000,
                "join": 8000,
                "mean": 15,
                "token": 4497,
                "expired": 120,
            },
            doc_count=65890,
            max_term_doc_freq_ratio=0.08,
            max_query_terms=6,
        )

        self.assertNotIn("i", selected)
        self.assertNotIn("m", selected)
        self.assertNotIn("getting", selected)
        self.assertNotIn("mean", selected)
        self.assertIn("109", selected)
        self.assertIn("error", selected)
        self.assertIn("token", selected)
        self.assertIn("expired", selected)

    def test_metadata_rerank_prefers_disband_section_for_api_semantics_query(self) -> None:
        disband_chunk = RetrievedChunk(
            chunk_id="disband",
            text="To disband a channel, fill in cname and leave uid and ip blank. Set time to 0.",
            source_path="official/ban-user-privileges.md",
            similarity=0.61,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel",
            metadata={
                "section_path": ["Ban user privileges", "Disband a channel"],
                "keywords": ["uid", "time", "cname"],
            },
        )
        kick_chunk = RetrievedChunk(
            chunk_id="kick-user",
            text="To kick a user out of a channel, specify the target uid.",
            source_path="official/ban-user-privileges.md",
            similarity=0.88,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#kick-a-user-out-of-a-channel",
            metadata={
                "section_path": ["Ban user privileges", "Kick a user out of a channel"],
                "keywords": ["uid"],
            },
        )

        reranked, _ = rag_qa._metadata_rerank(
            query=self._BAN_API_MISMATCH_MESSAGE,
            chunks=[kick_chunk, disband_chunk],
            top_k=2,
        )

        self.assertEqual(reranked[0].chunk_id, "disband")

    def test_metadata_rerank_prefers_create_rules_request_parameters_over_wrong_endpoints(self) -> None:
        create_rules_chunk = RetrievedChunk(
            chunk_id="create-rules-request",
            text="uid: Do not set it as 0. time: If the set value is 0, the banning rule does not take effect.",
            source_path="official/create-rules.md",
            similarity=0.71,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/create-rules",
            metadata={
                "product": "broadcast-streaming",
                "chunk_type": "api_params",
                "section_path": ["Create rule", "Request parameters"],
                "keywords": ["uid", "time", "time_in_seconds"],
            },
        )
        delete_rules_chunk = RetrievedChunk(
            chunk_id="delete-rules-request",
            text="Delete a previously created rule by id.",
            source_path="official/delete-rules.md",
            similarity=0.93,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/delete-rules",
            metadata={
                "product": "broadcast-streaming",
                "section_path": ["Delete rule", "Request examples"],
            },
        )

        reranked, _ = rag_qa._metadata_rerank(
            query=self._BAN_API_MISMATCH_MESSAGE,
            chunks=[delete_rules_chunk, create_rules_chunk],
            top_k=2,
        )

        self.assertEqual(reranked[0].chunk_id, "create-rules-request")

    def test_metadata_rerank_prefers_dual_stream_enablement_chunk_over_glossary(self) -> None:
        glossary_chunk = RetrievedChunk(
            chunk_id="dual-stream-glossary",
            text=(
                "In the dual-stream mode, the Video SDK simultaneously transmits a higher-resolution "
                "video stream along with an additional low-resolution, low bitrate video stream."
            ),
            source_path="official/glossary.md",
            source_url="https://docs.agora.io/en/video-calling/reference/glossary",
            similarity=0.91,
            metadata={
                "product": "video-calling",
                "section_path": ["Glossary", "D", "Dual-stream mode"],
            },
        )
        enablement_chunk = RetrievedChunk(
            chunk_id="dual-stream-web",
            text=(
                "Call `client.enableDualStream()` before remote users switch between the high and low streams. "
                "Use media stream fallback as needed for low-stream subscription behavior."
            ),
            source_path="official/media-stream-fallback_web.md",
            source_url="https://docs.agora.io/en/video-calling/advanced-features/media-stream-fallback?platform=web",
            similarity=0.63,
            metadata={
                "product": "video-calling",
                "section_path": ["Media stream fallback", "Implement media stream fallback", "Enable dual-stream mode"],
            },
        )

        reranked, info = rag_qa._metadata_rerank(
            query="how to enable the dual stream",
            chunks=[glossary_chunk, enablement_chunk],
            top_k=2,
            product="audio_video_calling",
        )

        self.assertEqual(reranked[0].chunk_id, "dual-stream-web")
        self.assertIn("intent:dual_stream_enablement", info["candidate_reasons"]["dual-stream-web"])
        self.assertIn("intent:dual_stream_glossary_penalty", info["candidate_reasons"]["dual-stream-glossary"])

    def test_metadata_rerank_prefers_official_how_to_over_issue_summary_for_onboarding_query(self) -> None:
        issue_summary_chunk = RetrievedChunk(
            chunk_id="issue-summary",
            text="Unity/Web audio-video sync issue when users join the same channel name.",
            source_path="cases/unity-web-sync-issue.md",
            similarity=0.92,
            metadata={
                "product": "video-calling",
                "doc_subtype": "troubleshooting_case",
                "chunk_type": "troubleshooting_procedure",
                "keywords": ["channel name", "join channel"],
                "topic": ["channel lifecycle"],
            },
        )
        quickstart_chunk = RetrievedChunk(
            chunk_id="official-join",
            text="To join a channel, call joinChannel with the channel name, token, uid, and options.",
            source_path="official/get-started-sdk_android.md",
            source_url="https://docs.agora.io/en/video-calling/get-started/get-started-sdk",
            similarity=0.81,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
                "keywords": ["join channel", "channel name"],
                "topic": ["channel lifecycle"],
                "use_case": "join_channel",
            },
        )

        reranked, _ = rag_qa._metadata_rerank(
            query=(
                "Hi Team, I am new to Agora and trying to integrate Agora SDK. However, I don't know "
                "how to join the channel as requested. Could you help explain to me and guide me to "
                "join the user into the channel?"
            ),
            chunks=[issue_summary_chunk, quickstart_chunk],
            top_k=2,
            product="audio_video_calling",
            query_class="how_to_faq",
        )

        self.assertEqual(reranked[0].chunk_id, "official-join")

    def test_rrf_merge_dedupes_and_limits_results(self) -> None:
        shared = RetrievedChunk(
            chunk_id="shared",
            text="Shared answer chunk",
            source_path="official/shared.md",
            similarity=0.91,
        )
        vector_chunks = [
            shared,
            RetrievedChunk(
                chunk_id="vector-only",
                text="Vector result",
                source_path="official/vector.md",
                similarity=0.88,
            ),
        ]
        keyword_chunks = [
            RetrievedChunk(
                chunk_id="keyword-only",
                text="Keyword result",
                source_path="technical/keyword.md",
                similarity=0.74,
            ),
            shared,
        ]

        merged = _rrf_merge(vector_chunks, keyword_chunks, limit=2)

        self.assertEqual(len(merged), 2)
        merged_ids = [chunk.chunk_id for chunk in merged]
        self.assertIn("shared", merged_ids)
        self.assertEqual(len(set(merged_ids)), 2)

    def test_chunk_family_key_prefers_metadata_source_family_before_source_path_heuristic(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="auth-ios",
            text="iOS authentication workflow",
            source_path="en/ios/authentication-workflow_ios.md",
            similarity=0.98,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
            },
        )

        self.assertEqual(
            _chunk_family_key(chunk),
            "video-calling::video-calling/get-started/authentication-workflow",
        )

    def test_select_diverse_chunks_prefers_unique_family_before_backfill(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="auth-android",
                text="Android authentication workflow",
                source_path="en/android/authentication-workflow.md",
                similarity=0.99,
                metadata={"product": "video-calling"},
            ),
            RetrievedChunk(
                chunk_id="auth-ios",
                text="iOS authentication workflow",
                source_path="en/ios/authentication-workflow.md",
                similarity=0.98,
                metadata={"product": "video-calling"},
            ),
            RetrievedChunk(
                chunk_id="error-codes",
                text="Common SDK error codes",
                source_path="en/android/error-codes.md",
                similarity=0.97,
                metadata={"product": "video-calling"},
            ),
        ]

        selected = _select_diverse_chunks(chunks, limit=2)

        self.assertEqual([chunk.chunk_id for chunk in selected], ["auth-android", "error-codes"])

    def test_select_diverse_chunks_backfills_original_order_when_unique_families_run_out(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="auth-android",
                text="Android authentication workflow",
                source_path="en/android/authentication-workflow.md",
                similarity=0.99,
                metadata={"product": "video-calling"},
            ),
            RetrievedChunk(
                chunk_id="auth-ios",
                text="iOS authentication workflow",
                source_path="en/ios/authentication-workflow.md",
                similarity=0.98,
                metadata={"product": "video-calling"},
            ),
            RetrievedChunk(
                chunk_id="error-codes",
                text="Common SDK error codes",
                source_path="en/android/error-codes.md",
                similarity=0.97,
                metadata={"product": "video-calling"},
            ),
        ]

        selected = _select_diverse_chunks(chunks, limit=3)

        self.assertEqual([chunk.chunk_id for chunk in selected], ["auth-android", "error-codes", "auth-ios"])

    def test_select_diverse_chunks_dedupes_repeated_sections_before_backfill(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="wildcard-precautions-a",
                text="Set uid to 0 when you generate a wildcard token.",
                source_path="official/deploy-token-server.md",
                similarity=0.99,
                h1="Deploy a token server",
                h2="Generate wildcard tokens",
                h3="Precautions",
                metadata={
                    "product": "video-calling",
                    "section_path": ["Deploy a token server", "Generate wildcard tokens", "Precautions"],
                    "use_case": "wildcard_tokens",
                },
            ),
            RetrievedChunk(
                chunk_id="wildcard-precautions-b",
                text="Wildcard tokens should still be generated on the app server.",
                source_path="official/deploy-token-server.md",
                similarity=0.98,
                h1="Deploy a token server",
                h2="Generate wildcard tokens",
                h3="Precautions",
                metadata={
                    "product": "video-calling",
                    "section_path": ["Deploy a token server", "Generate wildcard tokens", "Precautions"],
                    "use_case": "wildcard_tokens",
                },
            ),
            RetrievedChunk(
                chunk_id="wildcard-main",
                text="Generate wildcard tokens only when you need a token that works for all users.",
                source_path="official/deploy-token-server.md",
                similarity=0.97,
                h1="Deploy a token server",
                h2="Generate wildcard tokens",
                metadata={
                    "product": "video-calling",
                    "section_path": ["Deploy a token server", "Generate wildcard tokens"],
                    "use_case": "wildcard_tokens",
                },
            ),
        ]

        selected = _select_diverse_chunks(chunks, limit=2)

        self.assertEqual([chunk.chunk_id for chunk in selected], ["wildcard-precautions-a", "wildcard-main"])

    def test_retrieval_queries_parameterize_index_role(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count('index_role: str = "primary"'), 4)
        self.assertGreaterEqual(source.count("index_role = %s"), 3)

    def test_bm25_query_uses_double_precision_score_constants(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertIn("0.5::double precision", source)
        self.assertIn("1.0::double precision", source)

    def test_bm25_query_materializes_matched_postings_and_docs_before_scoring(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertIn("matched_postings AS MATERIALIZED", source)
        self.assertIn("matched_docs AS MATERIALIZED", source)
        self.assertIn("SELECT DISTINCT chunk_id FROM matched_postings", source)

    def test_bm25_query_materializes_top_scored_candidates_before_joining_chunk_table(self) -> None:
        source = Path("backend/services/rag_qa.py").read_text(encoding="utf-8")
        self.assertIn("top_scored AS MATERIALIZED", source)
        self.assertIn("FROM top_scored", source)
        self.assertIn("LIMIT %s", source)

    def test_retrieve_bm25_chunks_binds_index_role_after_bm25_constants(self) -> None:
        class _FakeCursor:
            def __init__(self) -> None:
                self.calls: list[tuple[object, tuple[object, ...] | None]] = []

            def execute(self, query: object, params: tuple[object, ...] | None = None) -> None:
                self.calls.append((query, params))

            def fetchall(self) -> list[tuple[object, ...]]:
                if len(self.calls) == 1:
                    return [("token", 10)]
                if len(self.calls) == 3:
                    return []
                return []

            def fetchone(self) -> tuple[object, ...]:
                return (100,)

            def __enter__(self) -> "_FakeCursor":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        class _FakeConnection:
            def __init__(self, cursor: _FakeCursor) -> None:
                self._cursor = cursor

            def cursor(self) -> _FakeCursor:
                return self._cursor

            def __enter__(self) -> "_FakeConnection":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        fake_cursor = _FakeCursor()
        fake_psycopg = SimpleNamespace(
            sql=psycopg.sql,
            connect=lambda *_args, **_kwargs: _FakeConnection(fake_cursor),
        )
        config = {
            "dsn": "postgresql://example",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
            "app_schema": "supportportal",
            "bm25_candidate_k": 12,
            "bm25_k1": 1.2,
            "bm25_b": 0.75,
            "bm25_max_term_doc_freq_ratio": 0.08,
            "bm25_max_query_terms": 6,
        }

        with patch("backend.services.rag_qa._import_psycopg", return_value=fake_psycopg), patch(
            "backend.services.rag_qa.tokenize_bm25_query",
            return_value=["token"],
        ), patch(
            "backend.services.rag_qa._select_bm25_query_terms",
            return_value=["token"],
        ):
            _retrieve_bm25_chunks("token question", config, index_role="shadow")

        _, params = fake_cursor.calls[2]
        assert params is not None
        self.assertEqual(params[5], 1.2)
        self.assertEqual(params[6], 1.2)
        self.assertEqual(params[7], 0.75)
        self.assertEqual(params[8], 0.75)
        self.assertEqual(params[9], 96)
        self.assertEqual(params[10], "shadow")

    def test_extract_metadata_hints_recognizes_language_method_and_structure_intent(self) -> None:
        hints = _extract_metadata_hints("Node.js 的 BuildTokenWithUidAndPrivilege Docker parameter 是什么")

        self.assertEqual(hints.language, "nodejs")
        self.assertEqual(hints.method_name, "BuildTokenWithUidAndPrivilege")
        self.assertIn("docker", hints.intent_terms)
        self.assertIn("parameter", hints.intent_terms)

    def test_extract_metadata_hints_recognizes_technical_case_intents(self) -> None:
        hints = _extract_metadata_hints("怎么判断延迟发生在 Agora 还是客户自己的 queue？")

        self.assertIsNone(hints.language)
        self.assertIsNone(hints.method_name)
        self.assertIn("decision_logic", hints.intent_terms)

    def test_metadata_rerank_filters_exact_method_and_boosts_language_matches(self) -> None:
        node_chunk = RetrievedChunk(
            chunk_id="node-method",
            text="Node.js code sample for BuildTokenWithUidAndPrivilege",
            source_path="official/deploy-token-server.md",
            similarity=0.82,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "language": "nodejs",
                "method_name": "BuildTokenWithUidAndPrivilege",
                "chunk_type": "code",
                "section_path": ["Token generation code", "Basic authentication"],
                "topic": ["token", "authentication"],
                "use_case": "basic_authentication",
            },
        )
        java_chunk = RetrievedChunk(
            chunk_id="java-method",
            text="Java code sample for BuildTokenWithUid",
            source_path="official/deploy-token-server.md",
            similarity=0.91,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "language": "java",
                "method_name": "BuildTokenWithUid",
                "chunk_type": "code",
                "section_path": ["Token generation code", "Basic authentication"],
                "topic": ["token", "authentication"],
                "use_case": "basic_authentication",
            },
        )
        docker_chunk = RetrievedChunk(
            chunk_id="docker-guide",
            text="Docker deployment guide for the token server",
            source_path="official/deploy-token-server.md",
            similarity=0.77,
            h1="Deploy a token server",
            h2="Deploy a token server",
            h3="Deploy with Docker",
            metadata={
                "language": None,
                "method_name": None,
                "chunk_type": "howto",
                "section_path": ["Deploy a token server", "Deploy with Docker"],
                "topic": ["docker", "deployment", "token"],
                "use_case": "docker_deployment",
            },
        )
        node_params_chunk = RetrievedChunk(
            chunk_id="node-method-params",
            text="Parameters for BuildTokenWithUidAndPrivilege in Node.js",
            source_path="official/deploy-token-server.md",
            similarity=0.79,
            h1="Deploy a token server",
            h2="Reference",
            h3="`BuildTokenWithUidAndPrivilege`",
            metadata={
                "language": "nodejs",
                "method_name": "BuildTokenWithUidAndPrivilege",
                "chunk_type": "api_params",
                "section_path": ["Reference", "API Reference", "`BuildTokenWithUidAndPrivilege`"],
                "topic": ["token", "permissions", "parameter"],
                "use_case": "advanced_permissions",
            },
        )

        hints = _extract_metadata_hints("Node.js 怎么用 BuildTokenWithUidAndPrivilege 生成 token")
        reranked, info = _metadata_rerank(
            query="Node.js 怎么用 BuildTokenWithUidAndPrivilege 生成 token",
            chunks=[java_chunk, docker_chunk, node_chunk, node_params_chunk],
            top_k=3,
            hints=hints,
        )

        self.assertEqual([chunk.chunk_id for chunk in reranked[:2]], ["node-method", "node-method-params"])
        self.assertTrue(info["applied_filter"])
        self.assertEqual(info["filter_type"], "language+method")
        self.assertEqual(info["post_rerank_count"], 2)
        self.assertIn("language:nodejs", info["candidate_reasons"]["node-method"])
        self.assertIn("method_name:BuildTokenWithUidAndPrivilege", info["candidate_reasons"]["node-method"])
        self.assertEqual(reranked[0].candidate_trace.get("metadata_rank"), 1)
        self.assertEqual(reranked[1].candidate_trace.get("metadata_rank"), 2)

    def test_metadata_rerank_prefers_basic_auth_for_generic_token_generation_query(self) -> None:
        advanced_chunk = RetrievedChunk(
            chunk_id="advanced-node",
            text="Node.js sample for generating a token with advanced privileges.",
            source_path="official/deploy-token-server.md",
            similarity=0.91,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Generate a token with advanced permissions",
            metadata={
                "language": "nodejs",
                "chunk_type": "code",
                "section_path": [
                    "Deploy a token server",
                    "Token generation code",
                    "Generate a token with advanced permissions",
                ],
                "topic": ["token", "permissions"],
                "use_case": "advanced_permissions",
            },
        )
        basic_chunk = RetrievedChunk(
            chunk_id="basic-node",
            text="Node.js sample for generating a token with basic authentication.",
            source_path="official/deploy-token-server.md",
            similarity=0.84,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "language": "nodejs",
                "chunk_type": "code",
                "section_path": ["Deploy a token server", "Token generation code", "Basic authentication"],
                "topic": ["token", "authentication"],
                "use_case": "basic_authentication",
            },
        )

        hints = _extract_metadata_hints("Node.js 怎么生成 token")
        reranked, _ = _metadata_rerank(
            query="Node.js 怎么生成 token",
            chunks=[advanced_chunk, basic_chunk],
            top_k=2,
            hints=hints,
        )

        self.assertEqual([chunk.chunk_id for chunk in reranked[:2]], ["basic-node", "advanced-node"])

    def test_metadata_rerank_filters_technical_case_chunks_by_strong_intent(self) -> None:
        issue_chunk = RetrievedChunk(
            chunk_id="issue-summary",
            text="A livestream archive was missing the first 64 seconds after the Cloud Transcoder create request.",
            source_path="technical/stream-start-delay.md",
            similarity=0.88,
            h1="Livestream archive missing first 64 seconds",
            h2="Issue Summary",
            metadata={
                "doc_subtype": "troubleshooting_case",
                "source_type": "technical_article_api",
                "chunk_type": "issue_summary",
                "issue_category": "startup_delay",
                "symptoms": [
                    "missing initial content",
                    "first frame delayed",
                ],
                "keywords": ["cloud transcoder", "create request", "aws ivs", "queue delay"],
                "external_service": "AWS IVS",
                "protocol": "RTMP",
            },
        )
        procedure_chunk = RetrievedChunk(
            chunk_id="procedure",
            text="Check Agora logs, find acquire/create timestamps, locate transcoder initialization, then compare the first RTMP frame arrival time at AWS IVS.",
            source_path="technical/stream-start-delay.md",
            similarity=0.81,
            h1="Livestream archive missing first 64 seconds",
            h2="Troubleshooting Procedure",
            metadata={
                "doc_subtype": "troubleshooting_case",
                "source_type": "technical_article_api",
                "chunk_type": "troubleshooting_procedure",
                "issue_category": "startup_delay",
                "symptoms": ["first frame delayed"],
                "keywords": ["cloud transcoder", "aws ivs", "rtmp", "create request"],
                "external_service": "AWS IVS",
                "protocol": "RTMP",
            },
        )
        decision_chunk = RetrievedChunk(
            chunk_id="decision",
            text="If the delay occurs before Agora receives create, investigate the customer queue. If Agora receives create quickly but RTMP starts late, investigate transcoder initialization.",
            source_path="technical/stream-start-delay.md",
            similarity=0.79,
            h1="Livestream archive missing first 64 seconds",
            h2="Decision Logic",
            metadata={
                "doc_subtype": "troubleshooting_case",
                "source_type": "technical_article_api",
                "chunk_type": "decision_logic",
                "issue_category": "startup_delay",
                "symptoms": ["first frame delayed", "stream start timestamp mismatch"],
                "keywords": ["queue delay", "cloud transcoder", "create", "rtmp output start"],
                "external_service": "AWS IVS",
                "protocol": "RTMP",
            },
        )
        best_practice_chunk = RetrievedChunk(
            chunk_id="best-practice",
            text="Log request dispatch timestamps, monitor RTMP output start, and minimize queue scheduling latency.",
            source_path="technical/stream-start-delay.md",
            similarity=0.74,
            h1="Livestream archive missing first 64 seconds",
            h2="Best Practice",
            metadata={
                "doc_subtype": "troubleshooting_case",
                "source_type": "technical_article_api",
                "chunk_type": "best_practice",
                "issue_category": "startup_delay",
                "symptoms": ["first frame delayed"],
                "keywords": ["logging", "queue latency", "monitoring"],
                "external_service": "AWS IVS",
                "protocol": "RTMP",
            },
        )

        hints = _extract_metadata_hints("怎么判断延迟发生在 Agora 还是客户自己的 queue？")
        reranked, info = _metadata_rerank(
            query="怎么判断延迟发生在 Agora 还是客户自己的 queue？",
            chunks=[issue_chunk, procedure_chunk, decision_chunk, best_practice_chunk],
            top_k=3,
            hints=hints,
        )

        self.assertEqual(reranked[0].chunk_id, "decision")
        self.assertTrue(info["applied_filter"])
        self.assertEqual(info["filter_type"], "technical_intent")
        self.assertIn("intent:decision_logic", info["candidate_reasons"]["decision"])
        self.assertGreaterEqual(info["post_rerank_count"], 1)

    def test_run_rag_query_uses_agentic_hybrid_pipeline(self) -> None:
        vector_chunk = RetrievedChunk(
            chunk_id="vector-1",
            text="Vector chunk",
            source_path="official/vector.md",
            similarity=0.91,
        )
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-1",
            text="BM25 chunk",
            source_path="technical/bm25.md",
            similarity=0.66,
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[vector_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[bm25_chunk]):
                        with patch("backend.services.rag_qa._retrieve_fts_chunks", return_value=[]):
                            with patch("backend.services.rag_qa._metadata_rerank", return_value=([vector_chunk, bm25_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None})):
                                with patch("backend.services.rag_qa._rerank_chunks", return_value=[bm25_chunk, vector_chunk]):
                                    with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", return_value=({"answer": "Use the BM25 chunk.", "key_steps": [], "citations": ["bm25-1"], "insufficient_evidence": False}, 10, 5, "gpt-4.1")):
                                        result = run_rag_query("How do I use BM25 for channel join retrieval?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.retrieval_strategy, "agentic_multi_tool_v1")
        self.assertGreaterEqual(result.trace.bm25_candidates_count, 1)
        self.assertEqual(result.trace.selected_chunk_ids[0], "bm25-1")
        self.assertTrue(result.trace.agent_enabled)
        self.assertFalse(result.trace.deadline_exhausted)
        self.assertIsNone(result.trace.timeout_stage)
        self.assertEqual(result.trace.reranker_provider, "siliconflow")
        self.assertEqual(result.trace.reranker_model, "BAAI/bge-reranker-v2-m3")
        self.assertTrue(result.trace.retrieval_candidates)
        self.assertTrue(
            all(
                isinstance(candidate.get("candidate_trace"), dict)
                for candidate in result.trace.retrieval_candidates
            )
        )
        self.assertTrue(
            any(
                "p_bm25" in (candidate["candidate_trace"].get("retrieval_sources") or [])
                for candidate in result.trace.retrieval_candidates
            )
        )

    def test_run_rag_query_short_how_to_faq_uses_lexical_light_path_before_any_vector_recovery(self) -> None:
        def _bm25_chunk() -> RetrievedChunk:
            return RetrievedChunk(
                chunk_id="bm25-join",
                text="Call joinChannel with the same channel name on each client.",
                source_path="official/get-started.md",
                similarity=0.95,
            )
        def _fts_auth_chunk() -> RetrievedChunk:
            return RetrievedChunk(
                chunk_id="fts-auth",
                text="Generate a token from your authentication server before calling joinChannel.",
                source_path="official/authentication-workflow.md",
                similarity=0.88,
            )
        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch("backend.services.rag_qa._resolve_active_vector_table", return_value="supportportal.docagent_chunks_bge_m3_1024"), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                side_effect=AssertionError("query understanding should be skipped for short how-to FAQ light path"),
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for short how-to FAQ light path"),
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector retrieval should not run on the first pass when lexical light path is sufficient"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[_bm25_chunk()],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[_fts_auth_chunk()],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: ([_bm25_chunk(), _fts_auth_chunk()], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None}),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=AssertionError("external rerank should be skipped for lexical light path"),
            ), patch(
                "backend.services.rag_qa._fetch_generic_join_pinned_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                return_value=(
                    {
                        "answer": "Generate a token from your authentication server, then call joinChannel with the same channel name on each client.",
                        "key_steps": [],
                        "citations": ["bm25-join", "fts-auth"],
                        "insufficient_evidence": False,
                    },
                    10,
                    5,
                    "gpt-5.4",
                ),
            ):
                result = run_rag_query("how to join channel")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())
        self.assertIn("user id", result.answer.answer.lower())
        self.assertIn("join method", result.answer.answer.lower())
        self.assertIn("channel/media options", result.answer.answer.lower())
        self.assertEqual(result.trace.selected_chunk_ids[0], "bm25-join")
        self.assertIn("fts-auth", result.trace.selected_chunk_ids)
        self.assertEqual(result.trace.query_class, "usage_configuration")
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.vector_setup_skipped)
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertEqual(result.trace.generic_join_support_chunks[:2], ["bm25-join", "fts-auth"])
        self.assertFalse(result.trace.generic_join_recovery_used)
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_bm25"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_fts"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertFalse(
            any(
                timing.get("tool_name") == "p_vec"
                and timing.get("query_kind") in {"semantic", "rewrite", "context"}
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertEqual(result.trace.answer_profile_used, "generic_join_deterministic")
        self.assertFalse(result.trace.answer_profile_fallback_used)

    def test_run_rag_query_follow_up_code_example_inherits_prior_join_channel_topic(self) -> None:
        def _bm25_chunk() -> RetrievedChunk:
            return RetrievedChunk(
                chunk_id="bm25-join",
                text=(
                    "Call joinChannel with the same channel name on each client.\n\n"
                    "```cpp\nengine->joinChannel(token, channelName, uid, options);\n```"
                ),
                source_path="official/get-started.md",
                similarity=0.95,
            )

        def _fts_auth_chunk() -> RetrievedChunk:
            return RetrievedChunk(
                chunk_id="fts-auth",
                text="Generate a token from your authentication server before calling joinChannel.",
                source_path="official/authentication-workflow.md",
                similarity=0.88,
            )

        ticket_context = [
            {"role": "customer", "content": "How to join channel?"},
            {
                "role": "assistant",
                "content": (
                    "To join a channel, initialize the engine, prepare your token, "
                    "then call the SDK join method."
                ),
            },
        ]

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "fallback_models": (),
                "query_policy": "balanced",
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                side_effect=AssertionError("follow-up inherited join examples should stay on the lexical light path"),
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for inherited generic join examples"),
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector retrieval should not run for inherited generic join examples"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[_bm25_chunk()],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[_fts_auth_chunk()],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    [_bm25_chunk(), _fts_auth_chunk()],
                    {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=AssertionError("external rerank should be skipped for inherited lexical light path"),
            ), patch(
                "backend.services.rag_qa._fetch_generic_join_pinned_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=AssertionError("generic join deterministic answer should satisfy code-example follow-ups"),
            ):
                result = run_rag_query(
                    "Can you share a code example?",
                    product="audio_video_calling",
                    ticket_context=ticket_context,
                )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("reference example", result.answer.answer.lower())
        self.assertIn("joinchannel", result.answer.answer.lower())
        self.assertEqual(result.trace.query_class, "usage_configuration")
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.vector_setup_skipped)
        self.assertTrue(result.trace.follow_up_inheritance_used)
        self.assertEqual(result.trace.follow_up_inheritance_source, "prior_customer_message")
        self.assertIn("join channel", str(result.trace.effective_question).lower())
        self.assertEqual(result.trace.answer_profile_used, "generic_join_deterministic")
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertEqual(result.trace.generic_join_support_chunks[:2], ["bm25-join", "fts-auth"])

    def test_run_rag_query_exact_error_lookup_uses_light_path_fast_answer_profile_then_falls_back_to_main_model(self) -> None:
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-error-109",
            text="Error 109 means the token is expired.",
            source_path="official/error-codes.md",
            similarity=0.95,
        )
        captured_models: list[tuple[str, str]] = []

        def _capture_answer_call(*, profile, system_prompt: str, user_prompt: str, extra_payload=None):
            _ = system_prompt
            _ = user_prompt
            _ = extra_payload
            captured_models.append((str(profile.model), str(profile.reasoning_effort)))
            if len(captured_models) == 1:
                return LlmTextResult(
                    text=(
                        '{"answer":"Error 109 means the token is expired.",'
                        '"key_steps":[],"citations":[],"insufficient_evidence":false}'
                    ),
                    model_name=str(profile.model),
                    prompt_tokens=10,
                    completion_tokens=5,
                )
            return LlmTextResult(
                text=(
                    '{"answer":"Error 109 means the token is expired.",'
                    '"key_steps":[],"citations":["bm25-error-109"],"insufficient_evidence":false}'
                ),
                model_name=str(profile.model),
                prompt_tokens=12,
                completion_tokens=6,
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch("backend.services.rag_qa._resolve_active_vector_table", return_value=None):
                with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                    with patch(
                        "backend.services.rag_qa._retrieve_bm25_chunks",
                        return_value=[bm25_chunk],
                    ), patch(
                        "backend.services.rag_qa._retrieve_fts_chunks",
                        return_value=[],
                    ), patch(
                        "backend.services.rag_qa._metadata_rerank",
                        return_value=([bm25_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                    ), patch(
                        "backend.services.rag_qa._rerank_chunks",
                        side_effect=AssertionError("external rerank should be skipped for simple lexical queries"),
                    ), patch(
                        "backend.services.rag_qa.invoke_responses_text",
                        side_effect=_capture_answer_call,
                    ):
                        result = run_rag_query("what does error 109 mean")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            captured_models,
            [("gpt-5.4-mini", "low"), ("gpt-5.4", "high")],
        )
        self.assertEqual(result.trace.query_class, "lexical_exact")
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.vector_setup_skipped)
        self.assertEqual(result.trace.answer_profile_used, "gpt-5.4")
        self.assertTrue(result.trace.answer_profile_fallback_used)
        self.assertGreaterEqual(result.trace.bm25_sql_latency_ms, 0.0)
        self.assertGreaterEqual(result.trace.fts_latency_ms, 0.0)
        self.assertGreaterEqual(result.trace.retrieval_round_wall_clock_ms, 0.0)
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_bm25"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_fts"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )

    def test_run_rag_query_generic_join_channel_prefers_rtc_join_and_token_contexts_for_audio_video_calling(self) -> None:
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.96,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/stream-channel",
            },
        )
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Call joinChannelEx to join multiple channels at the same time.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.94,
            h1="Video Calling",
            h2="Join multiple channels",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced/join-multiple-channels",
            },
        )
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.87,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.86,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        captured_final_chunk_ids: list[list[str]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Request a token, keep the channel name and user ID ready, then call joinChannel.",
                    "key_steps": [],
                    "citations": [chunk.chunk_id for chunk in chunks[:2]],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-5.4",
            )

        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )

        def _vector_chunks(*args: object, **kwargs: object) -> list[RetrievedChunk]:
            _ = args
            _ = kwargs
            return [
                rag_qa._copy_chunk(stream_chunk),
                rag_qa._copy_chunk(multi_chunk),
                rag_qa._copy_chunk(auth_chunk),
                rag_qa._copy_chunk(join_chunk),
            ]

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value=None,
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=_vector_chunks,
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(set(result.trace.selected_chunk_ids[:2]), {"join-android", "auth-android"})
        self.assertNotIn("stream-join", result.trace.selected_chunk_ids[:2])
        self.assertNotIn("multi-join", result.trace.selected_chunk_ids[:2])
        self.assertEqual(set(result.trace.cited_chunk_ids), {"join-android", "auth-android"})
        self.assertEqual(result.trace.query_class, "usage_configuration")
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.vector_setup_skipped)
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())
        self.assertIn("join method", result.answer.answer.lower())

    def test_run_rag_query_generic_join_channel_retries_for_second_supporting_citation(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.91,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.9,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )

        def _vector_chunks(*args: object, **kwargs: object) -> list[RetrievedChunk]:
            _ = args
            _ = kwargs
            return [
                rag_qa._copy_chunk(join_chunk),
                rag_qa._copy_chunk(auth_chunk),
            ]

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value=None,
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=_vector_chunks,
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.selected_chunk_ids[:2], ["join-android", "auth-android"])
        self.assertEqual(set(result.trace.cited_chunk_ids), {"join-android", "auth-android"})
        self.assertEqual(result.trace.citation_count, 2)
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertTrue(result.trace.generic_join_recovery_used)
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())
        self.assertIn("join method", result.answer.answer.lower())

    def test_run_rag_query_generic_join_channel_recovers_from_wrong_family_mix(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.91,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.9,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        broadcast_auth_chunk = RetrievedChunk(
            chunk_id="auth-broadcast",
            text="Use a token to join a channel in the Web implementation.",
            source_path="official/authentication-workflow_web.md",
            similarity=0.92,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "broadcast-streaming",
                "source_family": "broadcast-streaming/token-authentication/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.88,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/core-functionality/stream-channel",
            },
        )
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Join the channel using a random user ID.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.87,
            h1="Join multiple channels",
            h2="Implementation",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced-features/join-multiple-channels",
            },
        )
        captured_calls: list[list[str]] = []
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )

        def _vector_side_effect(*args: object, **kwargs: object) -> list[RetrievedChunk]:
            _ = args
            _ = kwargs
            return [
                rag_qa._copy_chunk(stream_chunk),
                rag_qa._copy_chunk(multi_chunk),
                rag_qa._copy_chunk(broadcast_auth_chunk),
                rag_qa._copy_chunk(auth_chunk),
                rag_qa._copy_chunk(join_chunk),
            ]

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value=None,
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=_vector_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(set(result.trace.selected_chunk_ids[:2]), {"join-android", "auth-android"})
        self.assertNotIn("stream-join", result.trace.selected_chunk_ids[:2])
        self.assertNotIn("multi-join", result.trace.selected_chunk_ids[:2])
        self.assertEqual(set(result.trace.cited_chunk_ids), {"join-android", "auth-android"})
        self.assertEqual(set(result.trace.selected_chunk_ids[:2]), {"join-android", "auth-android"})
        self.assertEqual(result.trace.citation_count, 2)
        self.assertEqual(result.trace.query_class, "usage_configuration")
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.vector_setup_skipped)
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertTrue(result.trace.generic_join_recovery_used)
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())
        self.assertIn("join method", result.answer.answer.lower())

    def test_run_rag_query_short_how_to_faq_recovers_lexically_from_wrong_family_mix(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.86,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.85,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.97,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/core-functionality/stream-channel",
            },
        )
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Call joinChannelEx to join multiple channels at the same time.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.96,
            h1="Join multiple channels",
            h2="Implementation",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced-features/join-multiple-channels",
            },
        )
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )
        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [rag_qa._copy_chunk(stream_chunk), rag_qa._copy_chunk(multi_chunk)]
            if query_text == "join a channel joinChannel channelName uid token appid quickstart get started":
                return [rag_qa._copy_chunk(join_chunk)]
            if query_text == "join channel joinChannel token channel name uid basic authentication":
                return [rag_qa._copy_chunk(auth_chunk)]
            if query_text == "join channel":
                return [rag_qa._copy_chunk(join_chunk)]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [rag_qa._copy_chunk(stream_chunk)]
            return []

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for short how_to_faq light path"),
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector recovery should not be needed for lexical join-family correction"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                side_effect=_fts_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                    {"post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]), "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(set(result.trace.selected_chunk_ids[:2]), {"join-android", "auth-android"})
        self.assertNotIn("multi-join", result.trace.selected_chunk_ids[:2])
        self.assertNotIn("stream-join", result.trace.selected_chunk_ids[:2])
        self.assertEqual(set(result.trace.cited_chunk_ids), {"join-android", "auth-android"})
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertTrue(result.trace.generic_join_recovery_used)
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_bm25" and timing.get("query_kind") == "focused_join_step"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_bm25" and timing.get("query_kind") == "focused_rewrite"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertFalse(
            any(
                timing.get("tool_name") == "p_vec"
                and timing.get("query_kind") in {"semantic", "rewrite", "context"}
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )

    def test_run_rag_query_short_how_to_faq_uses_pinned_join_family_without_round_two(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.86,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.85,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.97,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/core-functionality/stream-channel",
            },
        )
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Call joinChannelEx to join multiple channels at the same time.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.96,
            h1="Join multiple channels",
            h2="Implementation",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced-features/join-multiple-channels",
            },
        )
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )
        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [rag_qa._copy_chunk(stream_chunk), rag_qa._copy_chunk(multi_chunk)]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [rag_qa._copy_chunk(stream_chunk)]
            raise AssertionError(f"unexpected fts query: {query_text!r}")

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            citation_retry: bool = False,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            _ = citation_retry
            captured_payload_chunks.append([chunk.chunk_id for chunk in chunks])
            cited = [chunk.chunk_id for chunk in chunks[:2]]
            return (
                {
                    "answer": "Request a token with the channel name and user ID, then call joinChannel.",
                    "key_steps": [],
                    "citations": cited,
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-5.4",
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for short how_to_faq light path"),
            ), patch(
                "backend.services.rag_qa._fetch_generic_join_pinned_chunks",
                return_value=[rag_qa._copy_chunk(join_chunk), rag_qa._copy_chunk(auth_chunk)],
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector recovery should not be needed when pinned FAQ chunks are available"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                side_effect=_fts_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                    {"post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]), "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.selected_chunk_ids[:2], ["join-android", "auth-android"])
        self.assertTrue(result.trace.light_path_used)
        self.assertFalse(
            any(
                timing.get("query_kind") in {"focused_join_step", "focused_rewrite", "exact_token"}
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertFalse(
            any(
                timing.get("tool_name") == "p_vec"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )

    def test_run_rag_query_short_how_to_faq_runs_focused_auth_recovery_when_original_auth_support_is_off_family(self) -> None:
        join_video_ios = RetrievedChunk(
            chunk_id="join-video-ios",
            text="Set options.clientRoleType = .broadcaster and call joinChannel(byToken: token, channelId: channelName).",
            source_path="official/get-started-sdk_ios.md",
            similarity=0.93,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "platform": "ios",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        join_voice_ios = RetrievedChunk(
            chunk_id="join-voice-ios",
            text="Set channelProfile to communication and call joinChannel with broadcaster options.",
            source_path="official/voice-calling/get-started-sdk_ios.md",
            similarity=0.95,
            h1="Quickstart",
            h2="Implement Voice Calling",
            h3="Join a channel",
            metadata={
                "product": "voice-calling",
                "platform": "ios",
                "source_family": "voice-calling/get-started/get-started-sdk",
            },
        )
        off_family_auth_chunk = RetrievedChunk(
            chunk_id="auth-unity",
            text="Get a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_unity.md",
            similarity=0.88,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "broadcast-streaming",
                "platform": "unity",
                "source_family": "broadcast-streaming/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        auth_video_ios = RetrievedChunk(
            chunk_id="auth-video-ios",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_ios.md",
            similarity=0.9,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "platform": "ios",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.97,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/core-functionality/stream-channel",
            },
        )
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Call joinChannelEx to join multiple channels at the same time.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.96,
            h1="Join multiple channels",
            h2="Implementation",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced-features/join-multiple-channels",
            },
        )
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )
        captured_payload_chunks: list[list[str]] = []

        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [
                    rag_qa._copy_chunk(stream_chunk),
                    rag_qa._copy_chunk(multi_chunk),
                    rag_qa._copy_chunk(off_family_auth_chunk),
                ]
            if query_text == "join a channel joinChannel channelName uid token appid quickstart get started":
                return [rag_qa._copy_chunk(join_voice_ios), rag_qa._copy_chunk(join_video_ios)]
            if query_text == "join channel joinChannel token channel name uid basic authentication":
                return [rag_qa._copy_chunk(auth_video_ios)]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [rag_qa._copy_chunk(stream_chunk)]
            return []

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for short how_to_faq light path"),
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                side_effect=_fts_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                    {"post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]), "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn(result.trace.selected_chunk_ids[0], {"join-video-ios", "join-voice-ios"})
        self.assertEqual(result.trace.selected_chunk_ids[1], "auth-video-ios")
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_bm25" and timing.get("query_kind") == "focused_join_step"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_bm25" and timing.get("query_kind") == "focused_rewrite"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertFalse(
            any(
                timing.get("tool_name") == "p_bm25" and timing.get("query_kind") == "exact_token"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertTrue(result.trace.generic_join_recovery_used)
        self.assertEqual(result.trace.answer_profile_used, "generic_join_deterministic")
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())
        self.assertIn("join method", result.answer.answer.lower())

    def test_run_rag_query_short_how_to_faq_uses_original_join_and_auth_without_round_two(self) -> None:
        join_broadcast_ios = RetrievedChunk(
            chunk_id="join-broadcast-ios",
            text="Set options.clientRoleType = .broadcaster and call joinChannel(byToken: token, channelId: channelName).",
            source_path="official/get-started-sdk_ios.md",
            similarity=0.94,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "platform": "ios",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        join_react_video = RetrievedChunk(
            chunk_id="join-react-video",
            text="To join a channel, use the useJoin hook with appid, channel, and token.",
            source_path="official/get-started-sdk_react-js.md",
            similarity=0.91,
            h1="Quickstart",
            h2="Join a channel",
            h3="React",
            metadata={
                "product": "video-calling",
                "platform": "react-js",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.9,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "platform": "android",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )
        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [
                    rag_qa._copy_chunk(join_broadcast_ios),
                    rag_qa._copy_chunk(auth_chunk),
                    rag_qa._copy_chunk(join_react_video),
                ]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for short how_to_faq light path"),
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector recovery should not be needed when original lexical join/auth support is coherent"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                    {"post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]), "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.selected_chunk_ids[:2], ["join-react-video", "auth-android"])
        self.assertFalse(
            any(
                timing.get("tool_name") == "p_bm25" and timing.get("query_kind") in {"exact_token", "focused_join_step", "focused_rewrite"}
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertFalse(
            any(
                timing.get("tool_name") == "p_vec"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertFalse(result.trace.generic_join_recovery_used)
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())

    def test_run_rag_query_short_how_to_faq_accepts_join_step_chunk_that_already_covers_auth_prerequisite(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-quickstart",
            text=(
                "Call the SDK join method with the channel name, token, uid, and ChannelMediaOptions "
                "to join a channel."
            ),
            source_path="official/get-started-sdk_android.md",
            similarity=0.95,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "platform": "android",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )

        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [rag_qa._copy_chunk(join_chunk)]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for short how_to_faq light path"),
            ), patch(
                "backend.services.rag_qa._fetch_generic_join_pinned_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector recovery should not be needed when the join step already covers auth prerequisites"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                    {
                        "post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                        "hints": {},
                        "applied_filter": False,
                        "filter_type": None,
                    },
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.selected_chunk_ids[:1], ["join-quickstart"])
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertEqual(result.trace.generic_join_support_chunks, ["join-quickstart"])
        self.assertFalse(result.trace.generic_join_recovery_used)
        self.assertEqual(result.trace.answer_profile_used, "generic_join_deterministic")
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())
        self.assertIn("user id", result.answer.answer.lower())
        self.assertIn("join method", result.answer.answer.lower())
        self.assertIn("channelmediaoptions", result.answer.answer.lower())

    def test_generic_join_reference_example_skips_dependency_setup_blocks(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-windows-setup",
            text=(
                "Quickstart setup text. After the setup phase, call the SDK join method "
                "with a channel name, token, and user ID to join a channel."
            ),
            source_path="official/get-started-sdk_windows.md",
            similarity=0.95,
            h1="Quickstart",
            h2="Set up your project",
            metadata={"product": "voice-calling", "platform": "windows"},
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-flutter",
            text=(
                "Use a token to join a channel.\n\n"
                "```yaml\n"
                "dependencies:\n"
                "  agora_rtc_engine: ^6.3.0\n"
                "  http: ^0.13.5\n"
                "```\n\n"
                "Then call the join method with the token, channel name, and user ID.\n\n"
                "```dart\n"
                "await _engine.joinChannel(\n"
                "  token: token,\n"
                "  channelId: channelName,\n"
                "  uid: uid,\n"
                "  options: const ChannelMediaOptions(),\n"
                ");\n"
                "```"
            ),
            source_path="official/authentication-workflow_flutter.md",
            similarity=0.9,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "platform": "flutter",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )

        answer = rag_qa._build_generic_join_grounded_answer(
            "How do I join the channel?",
            [join_chunk, auth_chunk],
            product="audio_video_calling",
        )

        self.assertIsNotNone(answer)
        assert answer is not None
        self.assertIn("Reference Example:", answer.answer)
        self.assertIn("_engine.joinChannel", answer.answer)
        self.assertNotIn("dependencies:", answer.answer)

    def test_run_rag_query_short_how_to_faq_accepts_token_auth_chunk_that_already_covers_join_flow(self) -> None:
        auth_join_chunk = RetrievedChunk(
            chunk_id="auth-join-android",
            text=(
                "Use a token to join a channel. "
                "Request a token from your app server for the channel name and user ID before joining. "
                "```kotlin\n"
                "val channelId = \"demo-room\"\n"
                "val uid = 0\n"
                "val token = getToken(channelId, uid)\n"
                "engine.joinChannel(token, channelId, uid, option)\n"
                "```"
            ),
            source_path="official/authentication-workflow_android.md",
            similarity=0.95,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "platform": "android",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )

        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [rag_qa._copy_chunk(auth_join_chunk)]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for short how_to_faq light path"),
            ), patch(
                "backend.services.rag_qa._fetch_generic_join_pinned_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector recovery should not be needed when the auth chunk already covers the join flow"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                    {
                        "post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                        "hints": {},
                        "applied_filter": False,
                        "filter_type": None,
                    },
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=AssertionError("generic_join deterministic path should not call the llm when one auth chunk already covers the join flow"),
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.selected_chunk_ids[:1], ["auth-join-android"])
        self.assertEqual(result.trace.answer_profile_used, "generic_join_deterministic")
        self.assertFalse(result.trace.needs_human)
        self.assertEqual(result.trace.handoff_reason, None)
        self.assertEqual(result.answer.citations[0]["chunk_id"], "auth-join-android")
        self.assertIn("```kotlin", result.answer.answer)
        self.assertIn("join a channel", result.answer.answer.lower())

    def test_run_rag_query_dual_stream_enable_query_returns_grounded_answer_with_citations(self) -> None:
        dual_stream_chunk = RetrievedChunk(
            chunk_id="dual-stream-web",
            text=(
                "Call `client.enableDualStream()` to enable dual-stream mode for the local video stream.\n\n"
                "```javascript\nawait client.enableDualStream();\n```\n\n"
                "After enabling dual-stream mode, configure your low-stream subscription or stream fallback logic."
            ),
            source_path="official/media-stream-fallback_web.md",
            source_url="https://docs.agora.io/en/video-calling/advanced-features/media-stream-fallback?platform=web",
            similarity=0.96,
            h1="Media stream fallback",
            h2="Implement media stream fallback",
            h3="Enable dual-stream mode",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced-features/media-stream-fallback",
            },
        )
        glossary_chunk = RetrievedChunk(
            chunk_id="dual-stream-glossary",
            text="Dual-stream mode lets a sender publish both a high-quality and a low-quality video stream.",
            source_path="official/glossary.md",
            source_url="https://docs.agora.io/en/video-calling/reference/glossary",
            similarity=0.9,
            h1="Glossary",
            h2="D",
            h3="Dual-stream mode",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/reference/glossary",
            },
        )

        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            normalized = " ".join(str(query_text or "").split()).lower()
            if normalized == "how to enable the dual stream":
                return [rag_qa._copy_chunk(glossary_chunk)]
            if "media stream fallback" in normalized or "enabledualstream" in normalized or "setdualstreammode" in normalized:
                return [rag_qa._copy_chunk(dual_stream_chunk), rag_qa._copy_chunk(glossary_chunk)]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                side_effect=AssertionError("embedding provider should not be initialized for dual-stream light path"),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                side_effect=AssertionError("query understanding should be skipped for dual-stream light path"),
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for dual-stream light path"),
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector retrieval should not run for dual-stream light path"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[rag_qa._copy_chunk(glossary_chunk)],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    [rag_qa._copy_chunk(dual_stream_chunk), rag_qa._copy_chunk(glossary_chunk)],
                    {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=AssertionError("external rerank should be skipped for dual-stream light path"),
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=AssertionError("dual-stream deterministic path should not call the llm"),
            ), patch(
                "backend.services.rag_qa._run_rag_query_legacy",
                side_effect=AssertionError("dual-stream light path should stay in agentic mode"),
            ):
                result = run_rag_query("how to enable the dual stream", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.execution_mode, "agentic")
        self.assertEqual(result.trace.query_class, "usage_configuration")
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.vector_setup_skipped)
        self.assertEqual(result.trace.answer_profile_used, "dual_stream_deterministic")
        self.assertFalse(result.trace.needs_human)
        self.assertEqual(result.trace.handoff_reason, None)
        self.assertEqual(result.answer.citations[0]["chunk_id"], "dual-stream-web")
        self.assertIn("client.enableDualStream()", result.answer.answer)
        self.assertIn("```javascript", result.answer.answer)
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_bm25"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_fts"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertFalse(
            any(
                timing.get("tool_name") == "p_vec"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )

    def test_run_rag_query_short_black_screen_guidance_uses_deterministic_answer_profile(self) -> None:
        faq_chunk = RetrievedChunk(
            chunk_id="black-screen-faq-ios",
            text=(
                "Title: Quickstart\n"
                "Knowledge Type: Official Documentation\n"
                "Platform: ios\n"
                "Product: video-calling\n"
                "Section: Frequently asked questions\n\n"
                "* [How can I fix black screen issues?](https://docs-md.agora.io/en/help/quality-issues/video_blank.md)\n"
            ),
            source_path="official/get-started-sdk_ios.md",
            source_url="https://docs.agora.io/en/video-calling/get-started/get-started-sdk?platform=ios",
            similarity=0.6227,
            h1="Quickstart",
            h2="Reference",
            h3="Frequently asked questions",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
                "chunk_type": "faq_index",
                "use_case": "faq",
            },
            index_role="primary",
        )
        faq_chunk.rerank_score = 0.2593
        release_note_chunk = RetrievedChunk(
            chunk_id="black-screen-release-note-web",
            text=(
                "Title: Release notes\n"
                "Knowledge Type: Official Documentation\n"
                "Platform: web\n"
                "Product: video-calling\n"
                "Section: Bug fixes\n\n"
                "This release fixes the following issues:\n"
                "- Black screen might occur when calling setMute and setEnable under certain conditions.\n"
            ),
            source_path="official/release-notes_web.md",
            source_url="https://docs.agora.io/en/video-calling/overview/release-notes?platform=web",
            similarity=0.7617,
            h1="Release notes",
            h2="Video SDK",
            h3="Bug fixes",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/overview/release-notes",
                "chunk_type": "concept",
            },
            index_role="primary",
        )
        release_note_chunk.rerank_score = 0.1345
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="i got black screen what should i do",
            canonical_terms=["Black screen"],
            glossary_hits=[{"canonical_term": "Black screen"}],
            dictionary_hits=[],
            retrieval_plan=RetrievalPlan(
                semantic_query="black screen troubleshooting",
                soft_signals={"symptoms": ["black screen"]},
                rule_expansions=[],
            ),
            rewritten_queries=["black screen troubleshooting"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )

        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            normalized = " ".join(str(query_text or "").split()).lower()
            if normalized == "i got black screen! what should i do?":
                return [rag_qa._copy_chunk(faq_chunk), rag_qa._copy_chunk(release_note_chunk)]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
                "shadow_retrieval_enabled": True,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                return_value=(
                    [rag_qa._copy_chunk(faq_chunk), rag_qa._copy_chunk(release_note_chunk)],
                    {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=AssertionError("black-screen deterministic path should not call the llm when faq and release-note guidance are both available"),
            ):
                result = run_rag_query("i got black screen! what should i do?", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.query_class, "troubleshooting_why")
        self.assertEqual(result.trace.answer_profile_used, "black_screen_guidance_deterministic")
        self.assertFalse(result.trace.needs_human)
        self.assertEqual(result.trace.handoff_reason, None)
        self.assertEqual(len(result.answer.citations), 2)
        self.assertTrue(result.answer.answer.startswith("Hi there,"))
        self.assertIn("release notes", result.answer.answer.lower())
        self.assertIn("black screen issues", result.answer.answer.lower())
        self.assertTrue(result.answer.answer.endswith("Best Regards,\nSid"))

    def test_run_rag_query_short_how_to_faq_recovers_when_original_support_missing_auth_chunk(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.93,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Call joinChannelEx to join multiple channels at the same time.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.97,
            h1="Join multiple channels",
            h2="Implementation",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced-features/join-multiple-channels",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.9,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )
        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [rag_qa._copy_chunk(join_chunk), rag_qa._copy_chunk(multi_chunk)]
            if query_text == "join channel joinChannel token channel name uid basic authentication":
                return [rag_qa._copy_chunk(auth_chunk)]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for short how_to_faq light path"),
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector recovery should not run when lexical auth recovery can fill the missing support"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                    {"post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]), "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.selected_chunk_ids[:2], ["join-android", "auth-android"])
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_bm25" and timing.get("query_kind") == "focused_rewrite"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertTrue(result.trace.generic_join_recovery_used)
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())

    def test_run_rag_query_short_how_to_faq_recovers_join_step_when_original_support_is_auth_only(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.89,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "platform": "android",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.93,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "platform": "android",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        query_understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="how to join channel",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            retrieval_plan=RetrievalPlan(
                semantic_query="how to join channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            rewritten_queries=["join channel token uid"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.0,
            rewrite_latency_ms=1.0,
        )
        captured_payload_chunks: list[list[str]] = []

        def _bm25_side_effect(query_text: str, *_args, **_kwargs) -> list[RetrievedChunk]:
            if query_text == "how to join channel":
                return [rag_qa._copy_chunk(auth_chunk)]
            if query_text == "join a channel joinChannel channelName uid token appid quickstart get started":
                return [rag_qa._copy_chunk(join_chunk)]
            if query_text == "join channel joinChannel token channel name uid basic authentication":
                return [rag_qa._copy_chunk(auth_chunk)]
            raise AssertionError(f"unexpected bm25 query: {query_text!r}")

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            citation_retry: bool = False,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            _ = citation_retry
            captured_payload_chunks.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Call the SDK join method with channel name, token, user ID, and media options.",
                    "key_steps": [
                        "Provide the channel name you want to join.",
                        "Request or provide a valid token for that channel and user ID.",
                        "Set the user ID and media options, then call the SDK join method.",
                    ],
                    "citations": ["join-android", "auth-android"],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-5.4",
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=query_understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                side_effect=AssertionError("planner should be skipped for short how_to_faq light path"),
            ), patch(
                "backend.services.rag_qa._fetch_generic_join_pinned_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=_bm25_side_effect,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                    {"post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]), "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda query, chunks, config, *, limit=None: chunks,
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.selected_chunk_ids[:2], ["join-android", "auth-android"])
        self.assertTrue(result.trace.light_path_used)
        self.assertTrue(result.trace.generic_join_primary_chunk_found)
        self.assertEqual(result.trace.generic_join_support_chunks, ["join-android", "auth-android"])
        self.assertTrue(result.trace.generic_join_recovery_used)
        self.assertIn("channel name", result.answer.answer.lower())
        self.assertIn("authentication token", result.answer.answer.lower())
        self.assertIn("user id", result.answer.answer.lower())
        self.assertIn("join method", result.answer.answer.lower())
        self.assertIn("channel/media options", result.answer.answer.lower())
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_bm25" and timing.get("query_kind") == "focused_join_step"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(
            any(
                timing.get("tool_name") == "p_vec"
                for timing in result.trace.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )

    def test_run_rag_query_join_multiple_channels_keeps_multi_channel_context(self) -> None:
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Call joinChannelEx to join multiple channels at the same time.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.95,
            h1="Video Calling",
            h2="Join multiple channels",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced/join-multiple-channels",
            },
        )
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.9,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        captured_final_chunk_ids: list[list[str]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Use joinChannelEx for multiple channels.",
                    "key_steps": [],
                    "citations": ["multi-join"],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-5.4",
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[multi_chunk]), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[join_chunk],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join multiple channels", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_final_chunk_ids[0][0], "multi-join")
        self.assertEqual(result.trace.selected_chunk_ids[0], "multi-join")

    def test_run_rag_query_join_stream_channel_keeps_stream_channel_context(self) -> None:
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.95,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/stream-channel",
            },
        )
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.9,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
            },
        )
        captured_final_chunk_ids: list[list[str]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Join the stream channel with the stream-channel flow.",
                    "key_steps": [],
                    "citations": ["stream-join"],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-5.4",
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[stream_chunk]), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[join_chunk],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=_capture_payload,
            ):
                result = run_rag_query("how to join a stream channel", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_final_chunk_ids[0][0], "stream-join")
        self.assertEqual(result.trace.selected_chunk_ids[0], "stream-join")

    def test_rerank_quota_failure_enters_process_cooldown(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="chunk-rerank",
            text="Call joinChannel with a token.",
            source_path="official/get-started.md",
            similarity=0.91,
        )
        config = {
            "rerank_provider": "siliconflow",
            "rerank_api_key": "test-rerank-key",
            "rerank_base_url": "https://api.siliconflow.cn/v1",
            "rerank_model": "BAAI/bge-reranker-v2-m3",
            "rerank_timeout_seconds": 10.0,
            "rerank_max_retries": 0,
            "rerank_top_n": 1,
        }

        def _http_403() -> urllib.error.HTTPError:
            return urllib.error.HTTPError(
                url="https://api.siliconflow.cn/v1/rerank",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=io.BytesIO(b'{"message":"insufficient balance"}'),
            )

        with patch.dict(rag_qa.__dict__, {"_RUNTIME_CAPABILITY_UNAVAILABLE_UNTIL": {}}, clear=False), patch(
            "backend.services.rag_qa.time.time",
            return_value=100.0,
        ), patch(
            "urllib.request.urlopen",
            side_effect=[_http_403(), _http_403()],
        ) as urlopen_mock:
            first = _rerank_chunks("how to join channel", [chunk], dict(config), limit=1)
            second = _rerank_chunks("how to join channel", [chunk], dict(config), limit=1)

        self.assertEqual(urlopen_mock.call_count, 1)
        self.assertEqual([item.chunk_id for item in first], ["chunk-rerank"])
        self.assertEqual([item.chunk_id for item in second], ["chunk-rerank"])

    def test_run_rag_query_records_keyword_fallback_as_agentic_tool(self) -> None:
        vector_chunk = RetrievedChunk(
            chunk_id="vector-1",
            text="Vector chunk",
            source_path="official/vector.md",
            similarity=0.91,
        )
        keyword_chunk = RetrievedChunk(
            chunk_id="keyword-1",
            text="Keyword fallback chunk",
            source_path="technical/keyword.md",
            similarity=0.52,
            retrieval_sources=["keyword_fallback"],
            candidate_trace={"keyword_fallback_hits": 2},
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[vector_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", side_effect=RuntimeError("bm25 offline")):
                        with patch("backend.services.rag_qa._retrieve_keyword_chunks", return_value=[keyword_chunk]):
                            with patch("backend.services.rag_qa._metadata_rerank", return_value=([vector_chunk, keyword_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None})):
                                with patch("backend.services.rag_qa._rerank_chunks", return_value=[vector_chunk, keyword_chunk]):
                                    with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", return_value=({"answer": "Use the vector chunk.", "key_steps": [], "citations": ["vector-1"], "insufficient_evidence": False}, 10, 5, "gpt-4.1")):
                                        result = run_rag_query("bm25 is down, use fallback")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.retrieval_strategy, "agentic_multi_tool_v1")
        self.assertTrue(result.trace.agent_enabled)
        self.assertTrue(
            any(
                "p_keyword" in (candidate["candidate_trace"].get("retrieval_sources") or [])
                for candidate in result.trace.retrieval_candidates
            )
        )

    def test_run_rag_query_diversifies_final_chunks_before_generation(self) -> None:
        query = "how do I handle token authentication errors?"
        auth_android = RetrievedChunk(
            chunk_id="auth-android",
            text="Android authentication workflow",
            source_path="en/android/authentication-workflow.md",
            similarity=0.99,
            metadata={"product": "video-calling"},
        )
        auth_ios = RetrievedChunk(
            chunk_id="auth-ios",
            text="iOS authentication workflow",
            source_path="en/ios/authentication-workflow.md",
            similarity=0.98,
            metadata={"product": "video-calling"},
        )
        error_codes = RetrievedChunk(
            chunk_id="error-codes",
            text="Common SDK error codes",
            source_path="en/android/error-codes.md",
            similarity=0.97,
            metadata={"product": "video-calling"},
        )
        captured_final_chunk_ids: list[list[str]] = []
        understanding = QueryUnderstandingResult(
            query_profile="test-hybrid",
            query_understanding_version="query-understanding-test",
            glossary_version="glossary-test",
            self_query_version="self-query-test",
            normalized_query=query,
            canonical_terms=[],
            glossary_hits=[],
            dictionary_hits=[],
            rewritten_queries=[],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(semantic_query=query),
            fallback_mode="none",
        )

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "Use the selected chunks.",
                    "key_steps": [],
                    "citations": ["auth-android"],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-4.1",
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[auth_android]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[auth_ios, error_codes]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=([auth_android, auth_ios, error_codes], {"post_rerank_count": 3, "hints": {}, "applied_filter": False, "filter_type": None}),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[auth_android, auth_ios, error_codes]):
                                with patch("backend.services.rag_qa.understand_rag_query", return_value=understanding):
                                    with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", side_effect=_capture_payload):
                                        result = run_rag_query(query)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_final_chunk_ids[0], ["auth-android", "error-codes"])
        self.assertEqual(result.trace.selected_chunk_ids, ["auth-android", "error-codes"])

    def test_run_rag_query_diversifies_rerank_candidates_before_external_rerank(self) -> None:
        query = "how do I handle token authentication errors?"
        auth_android = RetrievedChunk(
            chunk_id="auth-android",
            text="Android authentication workflow",
            source_path="en/android/authentication-workflow.md",
            similarity=0.99,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
            },
        )
        auth_ios = RetrievedChunk(
            chunk_id="auth-ios",
            text="iOS authentication workflow",
            source_path="en/ios/authentication-workflow.md",
            similarity=0.98,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
            },
        )
        error_codes = RetrievedChunk(
            chunk_id="error-codes",
            text="Common SDK error codes",
            source_path="en/android/error-codes.md",
            similarity=0.97,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/reference/error-codes",
            },
        )
        captured_rerank_inputs: list[list[str]] = []
        understanding = QueryUnderstandingResult(
            query_profile="test-hybrid",
            query_understanding_version="query-understanding-test",
            glossary_version="glossary-test",
            self_query_version="self-query-test",
            normalized_query=query,
            canonical_terms=[],
            glossary_hits=[],
            dictionary_hits=[],
            rewritten_queries=[],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(semantic_query=query),
            fallback_mode="none",
        )

        def _capture_rerank(
            query: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            limit: int,
        ) -> list[RetrievedChunk]:
            _ = query
            _ = config
            _ = limit
            captured_rerank_inputs.append([chunk.chunk_id for chunk in chunks])
            return list(chunks)

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 3,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[auth_android]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[auth_ios, error_codes]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=(
                                [auth_android, auth_ios, error_codes],
                                {"post_rerank_count": 3, "hints": {}, "applied_filter": False, "filter_type": None},
                            ),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", side_effect=_capture_rerank):
                                with patch("backend.services.rag_qa.understand_rag_query", return_value=understanding):
                                    with patch(
                                        "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                        return_value=(
                                            {
                                                "answer": "Use the selected chunks.",
                                                "key_steps": [],
                                                "citations": ["auth-android"],
                                                "insufficient_evidence": False,
                                            },
                                            10,
                                            5,
                                            "gpt-4.1",
                                        ),
                                    ):
                                        result = run_rag_query(query)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(captured_rerank_inputs[0], ["auth-android", "error-codes", "auth-ios"])

    def test_run_rag_query_preserves_method_coverage_for_comparison_queries(self) -> None:
        wildcard_chunk = RetrievedChunk(
            chunk_id="wildcard-token",
            text="Wildcard tokens allow all users to join the same channel.",
            source_path="official/deploy-token-server.md",
            similarity=0.96,
            h1="Deploy a token server",
            h2="Generate wildcard tokens",
            metadata={
                "product": "video-calling",
                "section_path": ["Deploy a token server", "Generate wildcard tokens"],
                "use_case": "wildcard_tokens",
            },
        )
        build_token_chunk = RetrievedChunk(
            chunk_id="build-token-with-uid",
            text="BuildTokenWithUid generates a token with appId, appCertificate, channelName, uid, role, and expiration.",
            source_path="official/deploy-token-server.md",
            similarity=0.93,
            h1="Deploy a token server",
            h2="Reference",
            h3="BuildTokenWithUid",
            metadata={
                "product": "video-calling",
                "language": "nodejs",
                "method_name": "BuildTokenWithUid",
                "section_path": ["Reference", "API Reference", "BuildTokenWithUid"],
                "use_case": "basic_authentication",
            },
        )
        privilege_chunk = RetrievedChunk(
            chunk_id="build-token-with-uid-privilege",
            text="BuildTokenWithUidAndPrivilege adds per-privilege expirations to the token payload.",
            source_path="official/deploy-token-server.md",
            similarity=0.92,
            h1="Deploy a token server",
            h2="Reference",
            h3="BuildTokenWithUidAndPrivilege",
            metadata={
                "product": "video-calling",
                "language": "nodejs",
                "method_name": "BuildTokenWithUidAndPrivilege",
                "section_path": ["Reference", "API Reference", "BuildTokenWithUidAndPrivilege"],
                "use_case": "advanced_permissions",
            },
        )
        captured_final_chunk_ids: list[list[str]] = []

        def _capture_payload(
            message: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            strict_retry: bool = False,
            packed_evidence=None,
            product: str | None = None,
            **_: object,
        ) -> tuple[dict[str, object], int, int, str]:
            _ = message
            _ = config
            _ = strict_retry
            _ = packed_evidence
            _ = product
            captured_final_chunk_ids.append([chunk.chunk_id for chunk in chunks])
            return (
                {
                    "answer": "The privilege variant supports privilege-level expirations.",
                    "key_steps": [],
                    "citations": [chunk.chunk_id for chunk in chunks[:2]],
                    "insufficient_evidence": False,
                },
                10,
                5,
                "gpt-4.1",
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[wildcard_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[build_token_chunk, privilege_chunk]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=(
                                [wildcard_chunk, build_token_chunk, privilege_chunk],
                                {"post_rerank_count": 3, "hints": {}, "applied_filter": False, "filter_type": None},
                            ),
                        ):
                            with patch(
                                "backend.services.rag_qa._rerank_chunks",
                                return_value=[wildcard_chunk, build_token_chunk, privilege_chunk],
                            ):
                                with patch("backend.services.rag_qa._invoke_llm_payload_with_trace", side_effect=_capture_payload):
                                    result = run_rag_query(
                                        "BuildTokenWithUid 和 BuildTokenWithUidAndPrivilege 有什么区别？"
                                    )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            captured_final_chunk_ids[0],
            ["build-token-with-uid", "build-token-with-uid-privilege"],
        )
        self.assertEqual(
            result.trace.selected_chunk_ids,
            ["build-token-with-uid", "build-token-with-uid-privilege"],
        )

    def test_run_rag_query_preserves_method_coverage_in_rerank_candidate_window_for_comparison_queries(self) -> None:
        wildcard_chunk = RetrievedChunk(
            chunk_id="wildcard-token",
            text="Wildcard tokens allow all users to join the same channel.",
            source_path="official/deploy-token-server.md",
            similarity=0.96,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/wildcard-tokens",
                "section_path": ["Deploy a token server", "Generate wildcard tokens"],
                "use_case": "wildcard_tokens",
            },
        )
        build_token_chunk = RetrievedChunk(
            chunk_id="build-token-with-uid",
            text="BuildTokenWithUid generates a token with appId, appCertificate, channelName, uid, role, and expiration.",
            source_path="official/deploy-token-server.md",
            similarity=0.93,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/reference/deploy-token-server",
                "language": "nodejs",
                "method_name": "BuildTokenWithUid",
                "section_path": ["Reference", "API Reference", "BuildTokenWithUid"],
                "use_case": "basic_authentication",
            },
        )
        privilege_chunk = RetrievedChunk(
            chunk_id="build-token-with-uid-privilege",
            text="BuildTokenWithUidAndPrivilege adds per-privilege expirations to the token payload.",
            source_path="official/deploy-token-server.md",
            similarity=0.92,
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/reference/deploy-token-server",
                "language": "nodejs",
                "method_name": "BuildTokenWithUidAndPrivilege",
                "section_path": ["Reference", "API Reference", "BuildTokenWithUidAndPrivilege"],
                "use_case": "advanced_permissions",
            },
        )
        captured_rerank_inputs: list[list[str]] = []

        def _capture_rerank(
            query: str,
            chunks: list[RetrievedChunk],
            config: dict[str, object],
            *,
            limit: int,
        ) -> list[RetrievedChunk]:
            _ = query
            _ = config
            _ = limit
            captured_rerank_inputs.append([chunk.chunk_id for chunk in chunks])
            return list(chunks)

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 3,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[wildcard_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[build_token_chunk, privilege_chunk]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=(
                                [wildcard_chunk, build_token_chunk, privilege_chunk],
                                {"post_rerank_count": 3, "hints": {}, "applied_filter": False, "filter_type": None},
                            ),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", side_effect=_capture_rerank):
                                with patch(
                                    "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                    return_value=(
                                        {
                                            "answer": "The privilege variant supports privilege-level expirations.",
                                            "key_steps": [],
                                            "citations": ["build-token-with-uid", "build-token-with-uid-privilege"],
                                            "insufficient_evidence": False,
                                        },
                                        10,
                                        5,
                                        "gpt-4.1",
                                    ),
                                ):
                                    result = run_rag_query(
                                        "BuildTokenWithUid 和 BuildTokenWithUidAndPrivilege 有什么区别？"
                                    )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            captured_rerank_inputs[0],
            ["build-token-with-uid", "build-token-with-uid-privilege", "wildcard-token"],
        )

    def test_run_rag_query_repairs_false_insufficient_evidence_before_fallback(self) -> None:
        token_server_chunk = RetrievedChunk(
            chunk_id="token-server",
            text="In production, your app server should generate the Agora token instead of the mobile client.",
            source_path="official/deploy-token-server.md",
            similarity=0.95,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "product": "video-calling",
                "section_path": ["Deploy a token server", "Token generation code", "Basic authentication"],
                "use_case": "basic_authentication",
            },
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 1,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[token_server_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=([token_server_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[token_server_chunk]):
                                with patch("backend.services.rag_qa._has_grounded_keyword_overlap", return_value=True):
                                    with patch(
                                        "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                        side_effect=[
                                            (
                                                {
                                                    "answer": INSUFFICIENT_EVIDENCE_REPLY,
                                                    "key_steps": [],
                                                    "citations": [],
                                                    "insufficient_evidence": True,
                                                },
                                                10,
                                                5,
                                                "gpt-4.1",
                                            ),
                                            (
                                                {
                                                    "answer": "Generate the Agora token on your app server in production.",
                                                    "key_steps": [],
                                                    "citations": ["token-server"],
                                                    "insufficient_evidence": False,
                                                },
                                                12,
                                                6,
                                                "gpt-4.1",
                                            ),
                                        ],
                                    ):
                                        result = run_rag_query(
                                            "Should I generate the Agora token on the mobile app or on my backend?"
                                        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn(
            "Generate the Agora token on your app server in production.",
            result.answer.answer,
        )
        self.assertTrue(result.trace.structured_retry_used)
        self.assertEqual(result.trace.generation_mode, "structured_answer")
        self.assertFalse(result.trace.extractive_fallback_used)

    def test_run_rag_query_repairs_invalid_structured_payload_before_returning_answer(self) -> None:
        token_server_chunk = RetrievedChunk(
            chunk_id="token-server",
            text="In production, your app server should generate the Agora token instead of the mobile client.",
            source_path="official/deploy-token-server.md",
            similarity=0.95,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "product": "video-calling",
                "section_path": ["Deploy a token server", "Token generation code", "Basic authentication"],
                "use_case": "basic_authentication",
            },
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 1,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[token_server_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=([token_server_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[token_server_chunk]):
                                with patch(
                                    "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                    side_effect=[
                                        (
                                            {
                                                "answer": "Generate the Agora token on your app server in production.",
                                                "key_steps": [],
                                                "citations": [],
                                                "insufficient_evidence": False,
                                            },
                                            10,
                                            5,
                                            "gpt-4.1",
                                        ),
                                        (
                                            {
                                                "answer": "Generate the Agora token on your app server in production.",
                                                "key_steps": [],
                                                "citations": ["token-server"],
                                                "insufficient_evidence": False,
                                            },
                                            12,
                                            6,
                                            "gpt-4.1",
                                        ),
                                    ],
                                ):
                                    result = run_rag_query(
                                        "Should I generate the Agora token on the mobile app or on my backend?"
                                    )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn(
            "Generate the Agora token on your app server in production.",
            result.answer.answer,
        )
        self.assertTrue(result.trace.structured_retry_used)
        self.assertEqual(result.trace.cited_chunk_ids, ["token-server"])
        self.assertEqual(result.trace.generation_mode, "structured_answer")

    def test_run_rag_query_uses_evidence_oriented_extractive_fallback_as_last_resort(self) -> None:
        token_server_chunk = RetrievedChunk(
            chunk_id="token-server",
            text="In production, your app server should generate the Agora token instead of the mobile client.",
            source_path="official/deploy-token-server.md",
            similarity=0.95,
            h1="Deploy a token server",
            h2="Token generation code",
            h3="Basic authentication",
            metadata={
                "product": "video-calling",
                "section_path": ["Deploy a token server", "Token generation code", "Basic authentication"],
                "use_case": "basic_authentication",
            },
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 1,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[token_server_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=([token_server_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[token_server_chunk]):
                                with patch(
                                    "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                    side_effect=[
                                        (None, 10, 5, "gpt-4.1"),
                                        (None, 11, 5, "gpt-4.1"),
                                    ],
                                ):
                                    result = run_rag_query("How do I generate a token for users joining a channel?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.generation_mode, "extractive_fallback")
        self.assertTrue(result.trace.extractive_fallback_used)
        self.assertTrue(result.trace.needs_human)

    def test_request_body_rescue_answer_uses_technical_case_when_llm_fails_closed(self) -> None:
        schema_chunk = RetrievedChunk(
            chunk_id="schema-recording-config",
            text=(
                "Request body schema: clientRequest.recordingConfig.transcodingConfig contains width, "
                "height, mixedVideoLayout, and layoutConfig."
            ),
            source_path="official/restful-api.md",
            source_type="official_markdown_upload",
            chunk_strategy="official_structured_v1",
            similarity=0.94,
            h1="Cloud Recording RESTful API",
            h2="Schemas",
            h3="recordingConfig",
            metadata={
                "request_body_evidence_type": "nested_schema",
                "request_body_matched_fields": [
                    "clientRequest.recordingConfig.transcodingConfig",
                    "clientRequest.recordingConfig.transcodingConfig.layoutConfig",
                ],
            },
        )
        technical_case = RetrievedChunk(
            chunk_id="technical-root-cause",
            text=(
                "**Issue Description** When using Agora Cloud Recording in Composite/Mix mode, "
                "the recorded output may always be generated as 360 x 640 portrait. "
                "**Root Cause** This usually happens when transcodingConfig is placed in the wrong "
                "part of the start request JSON, causing Cloud Recording to ignore those settings. "
                "--- **Prevention/Best Practice (optional)** - Always validate the JSON payload before sending it. "
                "**Step by Step Solution** 1. Check where transcodingConfig is placed in the start request. "
                "Incorrect structure: ```json "
                "{"
                "\"cname\":\"tr_test\","
                "\"uid\":\"12345\","
                "\"clientRequest\":{"
                "\"recordingConfig\":{"
                "\"channelType\":0,"
                "\"streamTypes\":2"
                "},"
                "\"transcodingConfig\":{"
                "\"width\":1280,"
                "\"height\":720,"
                "\"mixedVideoLayout\":3"
                "}"
                "}"
                "} ``` "
                "Correct structure: ```json "
                "{"
                "\"cname\":\"tr_test\","
                "\"uid\":\"12345\","
                "\"clientRequest\":{"
                "\"recordingConfig\":{"
                "\"channelType\":0,"
                "\"streamTypes\":2,"
                "\"videoStreamType\":0,"
                "\"maxIdleTime\":300,"
                "\"transcodingConfig\":{"
                "\"width\":1280,"
                "\"height\":720,"
                "\"fps\":15,"
                "\"bitrate\":1130,"
                "\"backgroundColor\":\"#000000\","
                "\"mixedVideoLayout\":3,"
                "\"layoutConfig\":[{"
                "\"uid\":\"2134\","
                "\"x_axis\":0,"
                "\"y_axis\":0,"
                "\"width\":1.0,"
                "\"height\":1.0,"
                "\"alpha\":1.0,"
                "\"render_mode\":1"
                "}]"
                "}"
                "}"
                "}"
                "} ``` "
                "2. Move transcodingConfig inside clientRequest.recordingConfig. "
                "3. Retest with a new recording session."
            ),
            source_path="technical/mix-mode-cloud-recording-output.md",
            source_type="technical_article_api",
            chunk_strategy="technical_case_units_v1",
            similarity=0.91,
            h1="Mix Mode Cloud Recording Output Fixed by Moving transcodingConfig Inside recordingConfig",
            h2="Article",
            metadata={
                "source_type": "technical_article_api",
                "chunk_strategy": "technical_case_units_v1",
                "chunk_type": "troubleshooting_procedure",
            },
        )
        evidence = RequestBodyEvidenceResult(
            triggered=True,
            query=RequestBodyEvidenceQuery(
                is_request_body_or_api_config=True,
                body_keys=["clientRequest"],
                nested_paths=["clientRequest.transcodingConfig.width"],
                schema_evidence_goals=["recordingConfig schema"],
            ),
            chunks=[
                RequestBodyEvidenceChunk(
                    chunk_id=schema_chunk.chunk_id,
                    evidence_type="nested_schema",
                    matched_fields=["clientRequest.recordingConfig.transcodingConfig"],
                    source_path=schema_chunk.source_path,
                    text_excerpt=schema_chunk.text,
                    similarity=schema_chunk.similarity,
                    original_chunk=schema_chunk,
                )
            ],
            missing_evidence=[],
        )

        answer = rag_qa._build_request_body_evidence_rescue_answer(
            question="How can we record the whole canvas with this Cloud Recording request body?",
            chunks=[schema_chunk, technical_case],
            request_body_evidence=evidence,
            requester="Zac",
            customer_id=None,
        )

        self.assertIsNotNone(answer)
        assert answer is not None
        self.assertIn("Hi Zac,", answer.answer)
        self.assertIn("transcodingConfig is placed in the wrong part", answer.answer)
        self.assertNotIn("Prevention/Best Practice", answer.answer)
        self.assertNotIn("Always validate", answer.answer)
        self.assertIn("1. Check where transcodingConfig is placed in the start request.", answer.answer)
        self.assertIn("Move transcodingConfig inside clientRequest.recordingConfig", answer.answer)
        fence_start = answer.answer.find("```json")
        self.assertNotEqual(-1, fence_start)
        fenced_json = answer.answer[fence_start + len("```json") :].split("```", 1)[0].strip()
        corrected_payload = json.loads(fenced_json)
        recording_config = corrected_payload["clientRequest"]["recordingConfig"]
        self.assertIn("transcodingConfig", recording_config)
        self.assertNotIn("transcodingConfig", corrected_payload["clientRequest"])
        self.assertEqual(recording_config["transcodingConfig"]["mixedVideoLayout"], 3)
        self.assertEqual(
            [citation["chunk_id"] for citation in answer.citations],
            ["technical-root-cause", "schema-recording-config"],
        )

    def test_request_body_rescue_answer_requires_technical_troubleshooting_context(self) -> None:
        schema_chunk = RetrievedChunk(
            chunk_id="schema-recording-config",
            text="Request body schema: clientRequest.recordingConfig.transcodingConfig.",
            source_path="official/restful-api.md",
            source_type="official_markdown_upload",
            chunk_strategy="official_structured_v1",
            similarity=0.94,
            metadata={"request_body_evidence_type": "nested_schema"},
        )
        evidence = RequestBodyEvidenceResult(
            triggered=True,
            query=RequestBodyEvidenceQuery(
                is_request_body_or_api_config=True,
                body_keys=["clientRequest"],
                nested_paths=["clientRequest.transcodingConfig.width"],
                schema_evidence_goals=["recordingConfig schema"],
            ),
            chunks=[
                RequestBodyEvidenceChunk(
                    chunk_id=schema_chunk.chunk_id,
                    evidence_type="nested_schema",
                    matched_fields=["clientRequest.recordingConfig.transcodingConfig"],
                    source_path=schema_chunk.source_path,
                    text_excerpt=schema_chunk.text,
                    similarity=schema_chunk.similarity,
                    original_chunk=schema_chunk,
                )
            ],
            missing_evidence=[],
        )

        answer = rag_qa._build_request_body_evidence_rescue_answer(
            question="How can we record the whole canvas with this Cloud Recording request body?",
            chunks=[schema_chunk],
            request_body_evidence=evidence,
            requester=None,
            customer_id=None,
        )

        self.assertIsNone(answer)

    def test_run_rag_query_rescues_request_body_insufficient_evidence_with_strong_context(self) -> None:
        schema_chunk = RetrievedChunk(
            chunk_id="schema-recording-config",
            text=(
                "Request body schema: clientRequest.recordingConfig.transcodingConfig contains width, "
                "height, mixedVideoLayout, and layoutConfig."
            ),
            source_path="official/restful-api.md",
            source_type="official_markdown_upload",
            chunk_strategy="official_structured_v1",
            similarity=0.94,
            h1="Cloud Recording RESTful API",
            h2="Schemas",
            h3="recordingConfig",
            metadata={"request_body_evidence_type": "nested_schema"},
        )
        technical_case = RetrievedChunk(
            chunk_id="technical-root-cause",
            text=(
                "**Issue Description** Cloud Recording renders the screen share as a vertical strip. "
                "**Root Cause** transcodingConfig is placed in the wrong part of the start request JSON. "
                "**Step by Step Solution** 1. Move transcodingConfig inside clientRequest.recordingConfig. "
                "2. Retest with a new recording session."
            ),
            source_path="technical/mix-mode-cloud-recording-output.md",
            source_type="technical_article_api",
            chunk_strategy="technical_case_units_v1",
            similarity=0.91,
            metadata={
                "source_type": "technical_article_api",
                "chunk_strategy": "technical_case_units_v1",
                "chunk_type": "troubleshooting_procedure",
            },
        )
        evidence = RequestBodyEvidenceResult(
            triggered=True,
            query=RequestBodyEvidenceQuery(
                is_request_body_or_api_config=True,
                body_keys=["clientRequest"],
                nested_paths=["clientRequest.transcodingConfig.width"],
                schema_evidence_goals=["recordingConfig schema"],
            ),
            chunks=[
                RequestBodyEvidenceChunk(
                    chunk_id=schema_chunk.chunk_id,
                    evidence_type="nested_schema",
                    matched_fields=["clientRequest.recordingConfig.transcodingConfig"],
                    source_path=schema_chunk.source_path,
                    text_excerpt=schema_chunk.text,
                    similarity=schema_chunk.similarity,
                    original_chunk=schema_chunk,
                )
            ],
            missing_evidence=[],
        )

        with patch.dict(os.environ, {"RAG_AGENT_ENABLED": "false"}):
            with patch("backend.services.rag_qa._get_rag_config") as config_mock:
                config_mock.return_value = {
                    "dsn": "postgresql://example",
                    "api_key": "test-key",
                    "table": "supportportal.docagent_chunks_bge_m3_1024",
                    "top_k": 2,
                    "vector_candidate_k": 10,
                    "bm25_candidate_k": 10,
                    "keyword_candidate_k": 10,
                    "fusion_candidate_k": 10,
                    "rerank_top_n": 5,
                    "bm25_k1": 1.2,
                    "bm25_b": 0.75,
                    "chat_model": "gpt-4.1",
                    "embedding_provider": "siliconflow",
                    "embedding_model": "BAAI/bge-m3",
                    "rerank_provider": "siliconflow",
                    "rerank_model": "BAAI/bge-reranker-v2-m3",
                    "rerank_api_key": "test-rerank-key",
                    "rerank_base_url": "https://api.siliconflow.cn/v1",
                    "rerank_timeout_seconds": 10.0,
                    "rerank_max_retries": 1,
                    "request_timeout_seconds": 20.0,
                    "max_retries": 1,
                }
                with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                    with patch("backend.services.rag_qa._request_body_evidence_result_for_query", return_value=evidence):
                        with patch("backend.services.rag_qa._retrieve_chunks", return_value=[schema_chunk, technical_case]):
                            with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                                with patch(
                                    "backend.services.rag_qa._metadata_rerank",
                                    return_value=(
                                        [schema_chunk, technical_case],
                                        {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None},
                                    ),
                                ):
                                    with patch("backend.services.rag_qa._rerank_chunks", return_value=[schema_chunk, technical_case]):
                                        with patch(
                                            "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                            side_effect=[
                                                (
                                                    {
                                                        "answer": INSUFFICIENT_EVIDENCE_REPLY,
                                                        "key_steps": [],
                                                        "citations": [],
                                                        "insufficient_evidence": True,
                                                    },
                                                    10,
                                                    5,
                                                    "gpt-4.1",
                                                ),
                                                (
                                                    {
                                                        "answer": INSUFFICIENT_EVIDENCE_REPLY,
                                                        "key_steps": [],
                                                        "citations": [],
                                                        "insufficient_evidence": True,
                                                    },
                                                    11,
                                                    5,
                                                    "gpt-4.1",
                                                ),
                                            ],
                                        ):
                                            result = run_rag_query(
                                                "How can we record the whole canvas with this Cloud Recording request body?",
                                                requester="Zac",
                                                product="audio_video_calling",
                                            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.generation_mode, "request_body_evidence_rescue")
        self.assertFalse(result.trace.needs_human)
        self.assertFalse(result.trace.extractive_fallback_used)
        self.assertIn("transcodingConfig is placed in the wrong part", result.answer.answer)
        self.assertIn("Move transcodingConfig inside clientRequest.recordingConfig", result.answer.answer)

    def test_run_rag_query_supplements_request_body_json_when_structured_answer_omits_it(self) -> None:
        schema_chunk = RetrievedChunk(
            chunk_id="schema-recording-config",
            text=(
                "Request body schema: clientRequest.recordingConfig.transcodingConfig contains width, "
                "height, mixedVideoLayout, and layoutConfig."
            ),
            source_path="official/restful-api.md",
            source_type="official_markdown_upload",
            chunk_strategy="official_structured_v1",
            similarity=0.94,
            h1="Cloud Recording RESTful API",
            h2="Schemas",
            h3="recordingConfig",
            metadata={"request_body_evidence_type": "nested_schema"},
        )
        technical_case = RetrievedChunk(
            chunk_id="technical-root-cause",
            text=(
                "**Issue Description** Cloud Recording renders the screen share as a vertical strip. "
                "**Root Cause** transcodingConfig is placed in the wrong part of the start request JSON. "
                "**Step by Step Solution** 1. Move transcodingConfig inside clientRequest.recordingConfig. "
                "Correct structure: ```json "
                "{"
                "\"cname\":\"tr_test\","
                "\"uid\":\"12345\","
                "\"clientRequest\":{"
                "\"recordingConfig\":{"
                "\"channelType\":0,"
                "\"streamTypes\":2,"
                "\"transcodingConfig\":{"
                "\"width\":1280,"
                "\"height\":720,"
                "\"mixedVideoLayout\":3"
                "}"
                "}"
                "}"
                "} ```"
            ),
            source_path="technical/mix-mode-cloud-recording-output.md",
            source_type="technical_article_api",
            chunk_strategy="technical_case_units_v1",
            similarity=0.91,
            metadata={
                "source_type": "technical_article_api",
                "chunk_strategy": "technical_case_units_v1",
                "chunk_type": "troubleshooting_procedure",
            },
        )
        evidence = RequestBodyEvidenceResult(
            triggered=True,
            query=RequestBodyEvidenceQuery(
                is_request_body_or_api_config=True,
                body_keys=["clientRequest"],
                nested_paths=["clientRequest.transcodingConfig.width"],
                schema_evidence_goals=["recordingConfig schema"],
            ),
            chunks=[
                RequestBodyEvidenceChunk(
                    chunk_id=schema_chunk.chunk_id,
                    evidence_type="nested_schema",
                    matched_fields=["clientRequest.recordingConfig.transcodingConfig"],
                    source_path=schema_chunk.source_path,
                    text_excerpt=schema_chunk.text,
                    similarity=schema_chunk.similarity,
                    original_chunk=schema_chunk,
                )
            ],
            missing_evidence=[],
        )

        with patch.dict(os.environ, {"RAG_AGENT_ENABLED": "false"}):
            with patch("backend.services.rag_qa._get_rag_config") as config_mock:
                config_mock.return_value = {
                    "dsn": "postgresql://example",
                    "api_key": "test-key",
                    "table": "supportportal.docagent_chunks_bge_m3_1024",
                    "top_k": 2,
                    "vector_candidate_k": 10,
                    "bm25_candidate_k": 10,
                    "keyword_candidate_k": 10,
                    "fusion_candidate_k": 10,
                    "rerank_top_n": 5,
                    "bm25_k1": 1.2,
                    "bm25_b": 0.75,
                    "chat_model": "gpt-4.1",
                    "embedding_provider": "siliconflow",
                    "embedding_model": "BAAI/bge-m3",
                    "rerank_provider": "siliconflow",
                    "rerank_model": "BAAI/bge-reranker-v2-m3",
                    "rerank_api_key": "test-rerank-key",
                    "rerank_base_url": "https://api.siliconflow.cn/v1",
                    "rerank_timeout_seconds": 10.0,
                    "rerank_max_retries": 1,
                    "request_timeout_seconds": 20.0,
                    "max_retries": 1,
                }
                with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                    with patch("backend.services.rag_qa._request_body_evidence_result_for_query", return_value=evidence):
                        with patch("backend.services.rag_qa._retrieve_chunks", return_value=[schema_chunk, technical_case]):
                            with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                                with patch(
                                    "backend.services.rag_qa._metadata_rerank",
                                    return_value=(
                                        [schema_chunk, technical_case],
                                        {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None},
                                    ),
                                ):
                                    with patch("backend.services.rag_qa._rerank_chunks", return_value=[schema_chunk, technical_case]):
                                        with patch(
                                            "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                            return_value=(
                                                {
                                                    "answer": (
                                                        "Move transcodingConfig under clientRequest.recordingConfig "
                                                        "and keep mixedVideoLayout set to 3."
                                                    ),
                                                    "key_steps": ["Retest with a new recording session."],
                                                    "citations": ["technical-root-cause", "schema-recording-config"],
                                                    "insufficient_evidence": False,
                                                },
                                                10,
                                                5,
                                                "gpt-4.1",
                                            ),
                                        ):
                                            result = run_rag_query(
                                                "How can we record the whole canvas with this Cloud Recording request body?",
                                                requester="Zac",
                                                product="audio_video_calling",
                                            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.generation_mode, "structured_answer")
        self.assertIn("```json", result.answer.answer)
        fenced_json = result.answer.answer.split("```json", 1)[1].split("```", 1)[0].strip()
        corrected_payload = json.loads(fenced_json)
        recording_config = corrected_payload["clientRequest"]["recordingConfig"]
        self.assertIn("transcodingConfig", recording_config)
        self.assertNotIn("transcodingConfig", corrected_payload["clientRequest"])

    def test_request_body_json_extraction_prefers_correct_labeled_payload_over_incorrect_example(self) -> None:
        evidence = RequestBodyEvidenceResult(
            triggered=True,
            query=RequestBodyEvidenceQuery(
                is_request_body_or_api_config=True,
                body_keys=["clientRequest"],
                nested_paths=[
                    "clientRequest.transcodingConfig.width",
                    "clientRequest.transcodingConfig.height",
                    "clientRequest.transcodingConfig.fps",
                    "clientRequest.transcodingConfig.bitrate",
                    "clientRequest.transcodingConfig.mixedVideoLayout",
                ],
            ),
            chunks=[],
        )
        article_text = """
        Step by Step Solution

        Incorrect structure:
        ```json
        {
          "cname": "tr_test",
          "uid": "12345",
          "clientRequest": {
            "recordingConfig": {
              "channelType": 0,
              "streamTypes": 2,
              "videoStreamType": 0,
              "maxIdleTime": 300
            },
            "transcodingConfig": {
              "width": 1280,
              "height": 720,
              "fps": 15,
              "bitrate": 1130,
              "mixedVideoLayout": 3
            }
          }
        }
        ```

        Correct structure:
        ```json
        {
          "cname": "tr_test",
          "uid": "12345",
          "clientRequest": {
            "recordingConfig": {
              "channelType": 0,
              "streamTypes": 2,
              "videoStreamType": 0,
              "maxIdleTime": 300,
              "transcodingConfig": {
                "width": 1280,
                "height": 720,
                "fps": 15,
                "bitrate": 1130,
                "mixedVideoLayout": 3
              }
            }
          }
        }
        ```
        """

        corrected_json = rag_qa._extract_corrected_request_body_json(article_text, evidence)

        corrected_payload = json.loads(corrected_json)
        recording_config = corrected_payload["clientRequest"]["recordingConfig"]
        self.assertIn("transcodingConfig", recording_config)
        self.assertNotIn("transcodingConfig", corrected_payload["clientRequest"])

    def test_request_body_json_supplement_appends_correction_when_answer_has_conflicting_json(self) -> None:
        schema_chunk = RetrievedChunk(
            chunk_id="schema-recording-config",
            text="Request body schema: clientRequest.recordingConfig.transcodingConfig.",
            source_path="official/restful-api.md",
            source_type="official_markdown_upload",
            chunk_strategy="official_structured_v1",
            similarity=0.94,
            metadata={"request_body_evidence_type": "nested_schema"},
        )
        technical_case = RetrievedChunk(
            chunk_id="technical-root-cause",
            text=(
                "Correct structure: ```json "
                "{"
                "\"cname\":\"tr_test\","
                "\"uid\":\"12345\","
                "\"clientRequest\":{"
                "\"recordingConfig\":{"
                "\"channelType\":0,"
                "\"streamTypes\":2,"
                "\"transcodingConfig\":{"
                "\"width\":1280,"
                "\"height\":720,"
                "\"mixedVideoLayout\":3"
                "}"
                "}"
                "}"
                "} ```"
            ),
            source_path="technical/mix-mode-cloud-recording-output.md",
            source_type="technical_article_api",
            chunk_strategy="technical_case_units_v1",
            similarity=0.91,
            metadata={
                "source_type": "technical_article_api",
                "chunk_strategy": "technical_case_units_v1",
                "chunk_type": "troubleshooting_procedure",
            },
        )
        evidence = RequestBodyEvidenceResult(
            triggered=True,
            query=RequestBodyEvidenceQuery(
                is_request_body_or_api_config=True,
                body_keys=["clientRequest"],
                nested_paths=["clientRequest.transcodingConfig.width"],
            ),
            chunks=[
                RequestBodyEvidenceChunk(
                    chunk_id=schema_chunk.chunk_id,
                    evidence_type="nested_schema",
                    matched_fields=["clientRequest.recordingConfig.transcodingConfig"],
                    source_path=schema_chunk.source_path,
                    text_excerpt=schema_chunk.text,
                    similarity=schema_chunk.similarity,
                    original_chunk=schema_chunk,
                )
            ],
        )
        answer_with_wrong_json = """
        Move transcodingConfig under clientRequest.recordingConfig.

        ```json
        {
          "clientRequest": {
            "recordingConfig": {
              "channelType": 0
            },
            "transcodingConfig": {
              "width": 1280
            }
          }
        }
        ```
        """

        supplemented = rag_qa._supplement_request_body_json_if_missing(
            answer_with_wrong_json,
            [schema_chunk, technical_case],
            evidence,
        )

        fenced_blocks = supplemented.split("```json")
        self.assertGreaterEqual(len(fenced_blocks), 3)
        corrected_payload = json.loads(fenced_blocks[-1].split("```", 1)[0].strip())
        recording_config = corrected_payload["clientRequest"]["recordingConfig"]
        self.assertIn("transcodingConfig", recording_config)
        self.assertNotIn("transcodingConfig", corrected_payload["clientRequest"])

    def test_execute_agentic_round_short_symptom_troubleshooting_skips_vector_original_when_lexical_support_is_weak(self) -> None:
        weak_chunk = RetrievedChunk(
            chunk_id="black-screen-weak",
            text="General black screen troubleshooting checklist for RTC apps.",
            source_path="official/troubleshooting-black-screen.md",
            similarity=0.34,
            h1="Troubleshooting",
            h2="Black screen",
            h3="Overview",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/troubleshooting/black-screen",
                "chunk_type": "troubleshooting_summary",
            },
        )
        plan = rag_qa.AgenticRetrievalPlan(
            query_class="troubleshooting_why",
            first_pass_tools=["p_vec", "p_bm25", "p_fts"],
            query_variants=[
                ("original", "I got black screen, what should I do?"),
                ("semantic", "black screen troubleshooting"),
                ("rewrite", "video black screen root cause"),
            ],
            decomposition_targets=[],
            evidence_goal="causal_grounding",
            recovery_bias="semantic",
            ticket_context_used=False,
            exact_terms=["black", "screen"],
            light_path=False,
            product="audio_video_calling",
        )
        retrieval_plan = RetrievalPlan(
            semantic_query="black screen troubleshooting",
            hard_filters={},
            soft_signals={"symptoms": ["black screen"]},
            rule_expansions=[],
        )
        config = {
            "top_k": 3,
            "fusion_candidate_k": 10,
            "rerank_top_n": 5,
            "agent_shadow_ratio_cap": 0.4,
            "agent_final_shadow_cap": 1,
            "agent_recovery_shadow_cap": 2,
            "vector_enabled": True,
            "_vector_runtime_available": True,
            "rerank_enabled": False,
            "_rerank_runtime_available": False,
        }

        with patch(
            "backend.services.rag_qa._retrieve_chunks",
            side_effect=AssertionError("p_vec original should be skipped when short lexical troubleshooting support is clearly weak"),
        ), patch(
            "backend.services.rag_qa._retrieve_bm25_chunks",
            return_value=[rag_qa._copy_chunk(weak_chunk)],
        ), patch(
            "backend.services.rag_qa._retrieve_fts_chunks",
            return_value=[],
        ), patch(
            "backend.services.rag_qa._metadata_rerank",
            side_effect=lambda *args, **kwargs: (
                list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                {"post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]), "hints": {}, "applied_filter": False, "filter_type": None},
            ),
        ), patch(
            "backend.services.rag_qa._rerank_chunks",
            side_effect=lambda query, chunks, config, *, limit=None: chunks,
        ):
            result = rag_qa._execute_agentic_round(
                message="I got black screen, what should I do?",
                config=config,
                plan=plan,
                round_index=1,
                retrieval_plan=retrieval_plan,
                query_understanding=None,
                ticket_context=None,
                recovery_action=None,
                seed_tool_results=None,
                lexical_result_cache={},
            )

        self.assertEqual(result.judge.decision, "escalate")
        self.assertEqual(result.judge.reason, "weak_top1_support")
        self.assertTrue(
            all(
                str(timing.get("tool_name") or "") != "p_vec"
                for timing in result.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )
        self.assertTrue(
            all(
                str(timing.get("query_kind") or "") not in {"semantic", "rewrite", "context"}
                for timing in result.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )

    def test_judge_agentic_round_short_black_screen_question_allows_release_note_guidance(self) -> None:
        release_note_chunk = RetrievedChunk(
            chunk_id="release-note-black-screen",
            text="Issues fixed: fixed occasional black screen on Firefox 138+ caused by browser rollback.",
            source_path="official/release-notes_web.md",
            source_url="https://docs.agora.io/en/video-calling/overview/release-notes?platform=web",
            similarity=0.83,
            h1="Release notes",
            h2="Issues fixed",
            metadata={"product": "video-calling"},
            index_role="primary",
        )
        support_chunk = RetrievedChunk(
            chunk_id="faq-black-screen",
            text="How can I fix black screen issues?",
            source_path="official/get-started-sdk_react-native.md",
            source_url="https://docs.agora.io/en/video-calling/get-started/get-started-sdk?platform=react-native",
            similarity=0.71,
            h1="Quickstart",
            h2="Frequently asked questions",
            metadata={"product": "video-calling"},
            index_role="primary",
        )

        decision = rag_qa._judge_agentic_round(
            message="I got black screen, what should I do?",
            query_class="troubleshooting_why",
            round_index=1,
            reranked_chunks=[release_note_chunk, support_chunk],
            final_chunks=[release_note_chunk, support_chunk],
            decomposition_targets=[],
            exact_terms=["black", "screen"],
            grounded_overlap=True,
            product="audio_video_calling",
            troubleshooting_recovery_unlikely=False,
        )

        self.assertEqual(decision.decision, "answer_now")
        self.assertEqual(decision.reason, "sufficient_first_pass_support")

    def test_judge_agentic_round_root_cause_black_screen_question_still_rejects_release_note_only_top_chunk(self) -> None:
        release_note_chunk = RetrievedChunk(
            chunk_id="release-note-black-screen",
            text="Issues fixed: fixed occasional black screen on Firefox 138+ caused by browser rollback.",
            source_path="official/release-notes_web.md",
            source_url="https://docs.agora.io/en/video-calling/overview/release-notes?platform=web",
            similarity=0.83,
            h1="Release notes",
            h2="Issues fixed",
            metadata={"product": "video-calling"},
            index_role="primary",
        )

        decision = rag_qa._judge_agentic_round(
            message="Why is the remote video black screen? What is the root cause?",
            query_class="troubleshooting_why",
            round_index=1,
            reranked_chunks=[release_note_chunk],
            final_chunks=[release_note_chunk],
            decomposition_targets=[],
            exact_terms=["black", "screen", "root", "cause"],
            grounded_overlap=True,
            product="audio_video_calling",
            troubleshooting_recovery_unlikely=False,
        )

        self.assertEqual(decision.decision, "escalate")
        self.assertEqual(decision.reason, "weak_top1_support")

    def test_resolve_active_vector_table_prefers_populated_fallback_when_configured_table_empty(self) -> None:
        config = {
            "dsn": "postgresql://example",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
        }

        with patch("backend.services.rag_qa._list_vector_tables_with_primary_counts") as list_mock:
            list_mock.return_value = [
                ("supportportal.docagent_chunks_bge_m3_1024", 0),
                ("supportportal.docagent_chunks_ag_docs_test_1024", 1907),
                ("supportportal.docagent_chunks", 16),
            ]

            resolved = _resolve_active_vector_table(config)

        self.assertEqual(resolved, "supportportal.docagent_chunks_ag_docs_test_1024")

    def test_resolve_active_vector_table_returns_configured_table_without_full_enumeration_when_populated(self) -> None:
        config = {
            "dsn": "postgresql://example",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
        }

        with patch("backend.services.rag_qa._count_primary_rows_in_table", return_value=65890):
            with patch("backend.services.rag_qa._list_vector_tables_with_primary_counts") as list_mock:
                resolved = _resolve_active_vector_table(config)

        self.assertEqual(resolved, "supportportal.docagent_chunks_bge_m3_1024")
        list_mock.assert_not_called()

    def test_probe_customer_rag_index_readiness_reports_configured_table_empty(self) -> None:
        with patch("backend.services.rag_qa._get_rag_config", return_value={
            "dsn": "postgresql://example",
            "api_key": "test-key",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
            "vector_enabled": True,
        }):
            with patch("backend.services.rag_qa._count_primary_rows_in_table", return_value=0):
                with patch(
                    "backend.services.rag_qa._resolve_active_vector_table",
                    return_value="supportportal.docagent_chunks_bge_m3_1024",
                ):
                    readiness = probe_customer_rag_index_readiness()

        self.assertEqual(readiness.status, "configured_table_empty")
        self.assertEqual(readiness.configured_table, "supportportal.docagent_chunks_bge_m3_1024")
        self.assertEqual(readiness.resolved_table, "supportportal.docagent_chunks_bge_m3_1024")
        self.assertEqual(readiness.configured_primary_rows, 0)

    def test_probe_customer_rag_index_readiness_reports_fallback_table_selected(self) -> None:
        with patch("backend.services.rag_qa._get_rag_config", return_value={
            "dsn": "postgresql://example",
            "api_key": "test-key",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
            "vector_enabled": True,
        }):
            with patch("backend.services.rag_qa._count_primary_rows_in_table", return_value=0):
                with patch(
                    "backend.services.rag_qa._resolve_active_vector_table",
                    return_value="supportportal.docagent_chunks_ag_docs_test_1024",
                ):
                    readiness = probe_customer_rag_index_readiness()

        self.assertEqual(readiness.status, "fallback_table_selected")
        self.assertEqual(readiness.configured_table, "supportportal.docagent_chunks_bge_m3_1024")
        self.assertEqual(readiness.resolved_table, "supportportal.docagent_chunks_ag_docs_test_1024")
        self.assertEqual(readiness.configured_primary_rows, 0)

    def test_resolve_active_vector_table_uses_ttl_cache_until_expiry(self) -> None:
        config = {
            "dsn": "postgresql://example",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
        }

        rag_qa.clear_active_vector_table_cache()
        with patch("backend.services.rag_qa._count_primary_rows_in_table", return_value=0) as count_mock:
            with patch("backend.services.rag_qa._list_vector_tables_with_primary_counts") as list_mock:
                list_mock.return_value = [
                    ("supportportal.docagent_chunks_bge_m3_1024", 0),
                    ("supportportal.docagent_chunks_ag_docs_test_1024", 1907),
                ]
                with patch("backend.services.rag_qa.time.time", return_value=100.0):
                    first = _resolve_active_vector_table(config)
                    second = _resolve_active_vector_table(config)
                with patch("backend.services.rag_qa.time.time", return_value=161.0):
                    third = _resolve_active_vector_table(config)

        self.assertEqual(first, "supportportal.docagent_chunks_ag_docs_test_1024")
        self.assertEqual(second, "supportportal.docagent_chunks_ag_docs_test_1024")
        self.assertEqual(third, "supportportal.docagent_chunks_ag_docs_test_1024")
        self.assertEqual(count_mock.call_count, 2)
        self.assertEqual(list_mock.call_count, 2)

    def test_run_rag_query_uses_resolved_vector_table_for_all_retrieval_paths(self) -> None:
        captured_tables: list[str] = []

        def _capture_vector(
            message: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            _ = index_role
            captured_tables.append(str(config["table"]))
            return []

        def _capture_bm25(
            message: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            _ = index_role
            captured_tables.append(str(config["table"]))
            return []

        def _capture_fts(
            message: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            _ = index_role
            captured_tables.append(str(config["table"]))
            return []

        def _capture_keyword(
            message: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ) -> list[RetrievedChunk]:
            _ = message
            _ = limit
            _ = index_role
            captured_tables.append(str(config["table"]))
            return []

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa._resolve_active_vector_table", return_value="supportportal.docagent_chunks_ag_docs_test_1024"):
                with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                    with patch("backend.services.rag_qa._retrieve_chunks", side_effect=_capture_vector):
                        with patch("backend.services.rag_qa._retrieve_bm25_chunks", side_effect=_capture_bm25):
                            with patch("backend.services.rag_qa._retrieve_fts_chunks", side_effect=_capture_fts):
                                with patch("backend.services.rag_qa._retrieve_keyword_chunks", side_effect=_capture_keyword):
                                    result = run_rag_query("why does audio fail when joining a channel")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            set(captured_tables),
            {"supportportal.docagent_chunks_ag_docs_test_1024"},
        )
        self.assertTrue(result.trace.needs_human)

    def test_run_rag_query_records_query_understanding_metadata_in_trace(self) -> None:
        vector_chunk = RetrievedChunk(
            chunk_id="chunk-1",
            text="Cloud Recording troubleshooting",
            source_path="official/cloud-recording.md",
            similarity=0.96,
            metadata={"product": "video-calling", "chunk_type": "troubleshooting_procedure"},
        )

        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v1",
            glossary_version="video-calling_glossary_en_v1",
            self_query_version="v1",
            normalized_query="How do I troubleshoot Cloud Recording jitter?",
            canonical_terms=["Cloud Recording", "Jitter"],
            glossary_hits=[
                {
                    "canonical_term": "Cloud Recording",
                    "matched_text": "Cloud Recording",
                    "definition": "Cloud Recording is a component provided by Agora.",
                },
                {
                    "canonical_term": "Jitter",
                    "matched_text": "jitter",
                    "definition": "Jitter is the variation in delay of data packets.",
                },
            ],
            dictionary_hits=[
                {
                    "source": "glossary",
                    "canonical_term": "Cloud Recording",
                    "matched_text": "Cloud Recording",
                    "definition": "Cloud Recording is a component provided by Agora.",
                },
                {
                    "source": "glossary",
                    "canonical_term": "Jitter",
                    "matched_text": "jitter",
                    "definition": "Jitter is the variation in delay of data packets.",
                },
            ],
            retrieval_plan=RetrievalPlan(
                semantic_query="cloud recording jitter troubleshooting",
                hard_filters={"product": "video-calling"},
                soft_signals={"keywords": ["jitter"], "chunk_type": ["troubleshooting_procedure"]},
                rewritten_queries=["cloud recording jitter troubleshooting"],
                decomposition_subqueries=[],
                fallback_mode="none",
            ),
            rewritten_queries=["cloud recording jitter troubleshooting"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=4.5,
            rewrite_latency_ms=3.2,
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-4.1",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa.understand_rag_query", return_value=understanding):
                    with patch("backend.services.rag_qa._retrieve_chunks", return_value=[vector_chunk]):
                        with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                            with patch(
                                "backend.services.rag_qa._metadata_rerank",
                                return_value=(
                                    [vector_chunk],
                                    {
                                        "post_rerank_count": 1,
                                        "hints": {},
                                        "applied_filter": True,
                                        "filter_type": "product",
                                        "query_understanding": {
                                            "query_profile": "en",
                                            "glossary_hit_terms": ["Cloud Recording", "Jitter"],
                                            "applied_hard_filters": {"product": "video-calling"},
                                            "applied_soft_signals": {
                                                "keywords": ["jitter"],
                                                "chunk_type": ["troubleshooting_procedure"],
                                            },
                                            "fallback_mode": "none",
                                        },
                                    },
                                ),
                            ):
                                with patch("backend.services.rag_qa._rerank_chunks", return_value=[vector_chunk]):
                                    with patch(
                                        "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                        return_value=(
                                            {
                                                "answer": "Use Cloud Recording diagnostics.",
                                                "key_steps": [],
                                                "citations": ["chunk-1"],
                                                "insufficient_evidence": False,
                                            },
                                            10,
                                            5,
                                            "gpt-4.1",
                                        ),
                                    ):
                                        result = run_rag_query("How do I troubleshoot Cloud Recording jitter?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.trace.query_understanding_enabled)
        self.assertEqual(result.trace.query_profile, "en")
        self.assertEqual(result.trace.query_understanding_version, "v1")
        self.assertEqual(result.trace.glossary_version, "video-calling_glossary_en_v1")
        self.assertEqual(result.trace.glossary_hit_terms, ["Cloud Recording", "Jitter"])
        self.assertEqual(result.trace.applied_hard_filters, {"product": "video-calling"})
        self.assertEqual(
            result.trace.applied_soft_signals["chunk_type"],
            ["troubleshooting_procedure"],
        )
        self.assertEqual(result.trace.rewritten_queries, ["cloud recording jitter troubleshooting"])
        self.assertEqual(result.trace.rewrite_latency_ms, 3.2)

    def test_run_rag_query_uses_prf_expansion_and_only_downpushes_rule_backed_filters(self) -> None:
        seed_chunk = RetrievedChunk(
            chunk_id="chunk-seed",
            text="Use the RTC engine to connect users.",
            source_path="official/channel.md",
            h1="Join flow",
            h2=None,
            similarity=0.92,
            metadata={
                "language": "nodejs",
                "keywords": ["channel name"],
                "topic": ["channel lifecycle"],
            },
        )
        prf_chunk = RetrievedChunk(
            chunk_id="chunk-prf",
            text="Users who join the same channel name can communicate with each other.",
            source_path="official/channel.md",
            h1="Channel",
            h2="Join by channel name",
            similarity=0.95,
            metadata={
                "language": "nodejs",
                "keywords": ["channel name"],
                "topic": ["channel lifecycle"],
            },
        )
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="How do I join a channel in Node.js?",
            canonical_terms=["Channel"],
            glossary_hits=[
                {
                    "source": "glossary",
                    "canonical_term": "Channel",
                    "matched_text": "channel",
                    "definition": "A channel groups users under the same channel name.",
                }
            ],
            dictionary_hits=[
                {
                    "source": "glossary",
                    "canonical_term": "Channel",
                    "matched_text": "channel",
                    "definition": "A channel groups users under the same channel name.",
                }
            ],
            retrieval_plan=RetrievalPlan(
                semantic_query="How do I join a channel in Node.js?",
                hard_filters={"language": "nodejs", "product": "video-calling"},
                soft_signals={"topic": ["channel lifecycle"]},
                rewritten_queries=[],
                decomposition_subqueries=[],
                fallback_mode="none",
                rule_expansions=[],
                llm_expansions=[],
                prf_expansions=[],
                hard_filter_sources={"language": "rule+llm", "product": "llm_only"},
                soft_signal_sources={"topic": ["rule"]},
            ),
            rewritten_queries=[],
            decomposition_subqueries=[],
            fallback_mode="none",
        )
        captured_queries: list[str] = []
        captured_downpush: list[dict[str, str]] = []

        def _capture_vector(
            query: str,
            config: dict[str, object],
            *,
            limit: int | None = None,
            index_role: str = "primary",
        ):
            _ = limit
            _ = index_role
            captured_queries.append(query)
            plan = config.get("_retrieval_plan")
            captured_downpush.append(downpush_hard_filters(plan) if isinstance(plan, RetrievalPlan) else {})
            if query == "channel name":
                return [prf_chunk]
            return [seed_chunk]

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "app_schema": "supportportal",
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa.understand_rag_query", return_value=understanding):
                    with patch("backend.services.rag_qa._retrieve_chunks", side_effect=_capture_vector):
                        with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                            with patch(
                                "backend.services.rag_qa._metadata_rerank",
                                return_value=(
                                    [prf_chunk],
                                    {
                                        "post_rerank_count": 1,
                                        "hints": {},
                                        "applied_filter": True,
                                        "filter_type": "language",
                                        "query_understanding": {
                                            "query_profile": "en",
                                            "glossary_hit_terms": ["Channel"],
                                            "applied_hard_filters": {"language": "nodejs", "product": "video-calling"},
                                            "applied_soft_signals": {"topic": ["channel lifecycle"]},
                                            "fallback_mode": "none",
                                            "dictionary_hits": understanding.dictionary_hits,
                                            "rule_expansions": [],
                                            "llm_expansions": [],
                                            "prf_expansions": ["channel name"],
                                            "hard_filter_sources": {"language": "rule+llm", "product": "llm_only"},
                                            "cache_hit": False,
                                            "prf_used": True,
                                        },
                                    },
                                ),
                            ):
                                with patch("backend.services.rag_qa._rerank_chunks", return_value=[prf_chunk]):
                                    with patch(
                                        "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                        return_value=(
                                            {
                                                "answer": "Join the same channel by using the same channel name.",
                                                "key_steps": [],
                                                "citations": ["chunk-prf"],
                                                "insufficient_evidence": False,
                                            },
                                            10,
                                            5,
                                            "gpt-5.4",
                                        ),
                                    ):
                                        with patch.dict(os.environ, {"RAG_AGENT_ENABLED": "0"}, clear=False):
                                            result = run_rag_query("How do I join a channel in Node.js?")

        self.assertGreaterEqual(len(captured_queries), 2)
        self.assertEqual(captured_queries[0], "How do I join a channel in Node.js?")
        self.assertEqual(captured_downpush[0], {})
        self.assertIn("channel name", captured_queries)
        self.assertIn({"language": "nodejs"}, captured_downpush)
        self.assertTrue(result.trace.prf_used)
        self.assertEqual(result.trace.prf_expansions, ["channel name"])
        self.assertEqual(result.trace.hard_filter_sources["language"], "rule+llm")
        self.assertTrue(all("product" not in downpush for downpush in captured_downpush))

    def test_run_rag_query_uses_shared_packed_evidence_for_answer_and_trace(self) -> None:
        long_chunk = RetrievedChunk(
            chunk_id="chunk-1",
            text=(
                "Use joinChannel with the same channel name to enter the same communication session. "
                "The first user creates the channel and the last user leaving closes it. "
            )
            * 10,
            source_path="official/channel.md",
            similarity=0.97,
            h1="Channel",
            h2="Join a channel",
            metadata={"product": "video-calling"},
        )
        captured_prompts: list[str] = []

        def _capture_answer_call(*, profile, system_prompt: str, user_prompt: str, extra_payload=None):
            _ = system_prompt
            _ = extra_payload
            if getattr(profile, "scenario", "") == "rag_agent_planner":
                return LlmTextResult(
                    text=(
                        '{"query_class":"configuration","first_pass_tools":["p_bm25","p_fts","p_vec"],'
                        '"decomposition_targets":[],"evidence_goal":"join channel flow",'
                        '"recovery_bias":"lexical_recovery","ticket_context_used":false}'
                    ),
                    model_name="gpt-5.4-mini",
                    prompt_tokens=10,
                    completion_tokens=5,
                )
            captured_prompts.append(user_prompt)
            return LlmTextResult(
                text=(
                    '{"answer":"Use joinChannel with the same channel name.",'
                    '"key_steps":[],"citations":["chunk-1"],"insufficient_evidence":false}'
                ),
                model_name="gpt-5.4",
                prompt_tokens=10,
                completion_tokens=5,
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 1,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_window": 900,
                "context_budget_enabled": True,
                "reserved_output_tokens": 120,
                "buffer_tokens": 80,
                "context_compression_enabled": True,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._retrieve_chunks", return_value=[long_chunk]):
                    with patch("backend.services.rag_qa._retrieve_bm25_chunks", return_value=[]):
                        with patch(
                            "backend.services.rag_qa._metadata_rerank",
                            return_value=(
                                [long_chunk],
                                {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
                            ),
                        ):
                            with patch("backend.services.rag_qa._rerank_chunks", return_value=[long_chunk]):
                                with patch(
                                    "backend.services.rag_qa.build_packed_evidence",
                                    return_value=PackedEvidence(
                                        budget=ContextBudget(
                                            context_window=900,
                                            system_prompt_tokens=120,
                                            history_tokens=0,
                                            prompt_tokens=90,
                                            tool_tokens=0,
                                            reserved_output_tokens=120,
                                            buffer_tokens=80,
                                            available_context_tokens=490,
                                        ),
                                        chunk_ids=["chunk-1"],
                                        prompt_context=(
                                            "[chunk-1] official/channel.md | Channel > Join a channel\n"
                                            "Use joinChannel with the same channel name to join the same channel."
                                        ),
                                        selected_contexts=[
                                            {
                                                "chunk_id": "chunk-1",
                                                "doc_id": None,
                                                "source_path": "official/channel.md",
                                                "heading": "Channel > Join a channel",
                                                "source_url": None,
                                                "source_type": None,
                                                "chunk_strategy": None,
                                                "similarity": 0.97,
                                                "metadata": {"product": "video-calling"},
                                                "rerank_score": None,
                                                "rerank_reasons": [],
                                                "text": "Use joinChannel with the same channel name to join the same channel.",
                                                "text_excerpt": "Use joinChannel with the same channel name to join the same channel.",
                                                "packing_mode": "compressive",
                                            }
                                        ],
                                        raw_context_token_estimate=480,
                                        packed_context_token_estimate=120,
                                        compression_triggered=True,
                                        compression_trigger_reason="token_budget",
                                        compression_mode="compressive",
                                        compression_model="gpt-5.4-mini",
                                        extractive_segment_count=1,
                                        packed_evidence_count=1,
                                    ),
                                ):
                                    with patch(
                                        "backend.services.rag_qa.invoke_responses_text",
                                        side_effect=_capture_answer_call,
                                    ):
                                        result = run_rag_query("How do I join a channel in Node.js with a token?")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.trace.compression_triggered)
        self.assertEqual(result.trace.compression_mode, "compressive")
        self.assertIn(
            "Use joinChannel with the same channel name to join the same channel.",
            captured_prompts[0],
        )
        self.assertEqual(
            result.trace.selected_contexts[0]["text"],
            "Use joinChannel with the same channel name to join the same channel.",
        )
        self.assertGreater(
            result.trace.raw_context_token_estimate,
            result.trace.packed_context_token_estimate,
        )

    def test_run_rag_query_agentic_starts_original_retrieval_before_query_understanding_finishes(self) -> None:
        vector_chunk = RetrievedChunk(
            chunk_id="vector-1",
            text="Vector chunk",
            source_path="official/vector.md",
            similarity=0.91,
        )
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="query-understanding-v1",
            glossary_version="glossary-v1",
            self_query_version="self-query-v1",
            normalized_query="How do I join a channel in Node.js with a token?",
            canonical_terms=[],
            glossary_hits=[],
            dictionary_hits=[],
            rewritten_queries=[],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(semantic_query="How do I join a channel in Node.js with a token?"),
            fallback_mode="none",
        )
        retrieval_started = threading.Event()
        understanding_observed_parallel_retrieval: list[bool] = []

        def fake_understand(_: str, **_kwargs):
            understanding_observed_parallel_retrieval.append(retrieval_started.wait(timeout=0.2))
            return understanding

        def fake_retrieve_chunks(*args, **kwargs):
            _ = args
            _ = kwargs
            return [vector_chunk]

        def fake_retrieve_bm25_chunks(*args, **kwargs):
            _ = args
            _ = kwargs
            retrieval_started.set()
            return []

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
            }
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa.understand_rag_query", side_effect=fake_understand):
                    with patch("backend.services.rag_qa._retrieve_chunks", side_effect=fake_retrieve_chunks):
                        with patch("backend.services.rag_qa._retrieve_bm25_chunks", side_effect=fake_retrieve_bm25_chunks):
                            with patch("backend.services.rag_qa._retrieve_fts_chunks", return_value=[]):
                                with patch(
                                    "backend.services.rag_qa._metadata_rerank",
                                    return_value=([vector_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
                                ):
                                    with patch("backend.services.rag_qa._rerank_chunks", return_value=[vector_chunk]):
                                        with patch(
                                            "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                            return_value=(
                                                {
                                                    "answer": "Use joinChannel.",
                                                    "key_steps": [],
                                                    "citations": ["vector-1"],
                                                    "insufficient_evidence": False,
                                                },
                                                10,
                                                5,
                                                "gpt-5.4",
                                            ),
                                        ):
                                            with patch.dict(os.environ, {"RAG_AGENT_ENABLED": "1"}, clear=False):
                                                run_rag_query("How do I join a channel in Node.js with a token?")

        self.assertEqual(understanding_observed_parallel_retrieval, [True])

    def test_run_rag_query_agentic_query_understanding_timeout_uses_raw_query_without_blocking(self) -> None:
        message = "Why does production audio sound distorted after deployment?"
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-raw-query",
            text="Tune the SDK audio profile before production deployment.",
            source_path="official/audio-profile.md",
            similarity=0.88,
        )
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="query-understanding-v1",
            glossary_version="glossary-v1",
            self_query_version="self-query-v1",
            normalized_query=message,
            canonical_terms=[],
            glossary_hits=[],
            dictionary_hits=[],
            rewritten_queries=["sdk audio profile configuration"],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(
                semantic_query="sdk audio profile configuration",
                rewritten_queries=["sdk audio profile configuration"],
            ),
            fallback_mode="none",
        )

        def slow_understand(_: str, **_kwargs):
            time.sleep(0.4)
            return understanding

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "low",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 1.0,
                "max_retries": 1,
                "context_budget_enabled": False,
            }
            started_at = time.perf_counter()
            with patch.dict(
                os.environ,
                {"RAG_AGENT_ENABLED": "1", "RAG_AGENT_QUERY_EXPANSION_TIMEOUT_SECONDS": "0.02"},
                clear=False,
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                side_effect=slow_understand,
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[bm25_chunk],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value={
                    "query_class": "configuration",
                    "first_pass_tools": ["p_bm25"],
                    "query_variants": [("original", message)],
                    "decomposition_targets": [],
                    "evidence_goal": "configuration_support",
                    "recovery_bias": "lexical",
                },
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                return_value=(
                    [bm25_chunk],
                    {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                return_value=[bm25_chunk],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                return_value=(
                    {
                        "answer": "Tune the SDK audio profile.",
                        "key_steps": [],
                        "citations": ["bm25-raw-query"],
                        "insufficient_evidence": False,
                    },
                    10,
                    5,
                    "gpt-5.4",
                ),
            ):
                result = run_rag_query(message)
            elapsed = time.perf_counter() - started_at

        self.assertLess(elapsed, 0.25)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.trace.needs_human)
        self.assertFalse(result.trace.query_understanding_enabled)
        self.assertFalse(result.trace.deadline_exhausted)
        self.assertEqual(result.trace.timeout_stage, "query_understanding")
        self.assertEqual(result.trace.selected_chunk_ids, ["bm25-raw-query"])

    def test_run_rag_query_agentic_warm_retrieval_timeout_degrades_to_round_retrieval(self) -> None:
        message = "Why does production audio sound distorted after deployment?"
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-round",
            text="Tune the SDK audio profile before production deployment.",
            source_path="official/audio-profile.md",
            similarity=0.89,
        )
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="query-understanding-v1",
            glossary_version="glossary-v1",
            self_query_version="self-query-v1",
            normalized_query=message,
            canonical_terms=[],
            glossary_hits=[],
            dictionary_hits=[],
            rewritten_queries=[],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(semantic_query=message),
            fallback_mode="none",
        )
        bm25_calls = 0
        bm25_lock = threading.Lock()

        def bm25_with_slow_warmup(*_args, **_kwargs):
            nonlocal bm25_calls
            with bm25_lock:
                bm25_calls += 1
                call_number = bm25_calls
            if call_number == 1:
                time.sleep(0.3)
            return [bm25_chunk]

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "low",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 1.0,
                "max_retries": 1,
                "context_budget_enabled": False,
            }
            with patch.dict(
                os.environ,
                {"RAG_AGENT_ENABLED": "1", "RAG_AGENT_QUERY_EXPANSION_TIMEOUT_SECONDS": "0.02"},
                clear=False,
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=understanding,
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=bm25_with_slow_warmup,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value={
                    "query_class": "configuration",
                    "first_pass_tools": ["p_bm25"],
                    "query_variants": [("original", message)],
                    "decomposition_targets": [],
                    "evidence_goal": "configuration_support",
                    "recovery_bias": "lexical",
                },
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                return_value=(
                    [bm25_chunk],
                    {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                return_value=[bm25_chunk],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                return_value=(
                    {
                        "answer": "Tune the SDK audio profile.",
                        "key_steps": [],
                        "citations": ["bm25-round"],
                        "insufficient_evidence": False,
                    },
                    10,
                    5,
                    "gpt-5.4",
                ),
            ):
                result = run_rag_query(message)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.trace.needs_human)
        self.assertFalse(result.trace.deadline_exhausted)
        self.assertEqual(result.trace.timeout_stage, "warm_original_bm25")
        self.assertEqual(result.trace.selected_chunk_ids, ["bm25-round"])
        self.assertGreaterEqual(bm25_calls, 2)
        self.assertFalse(
            any(
                timing.get("used_seed_tool")
                for timing in result.trace.retrieval_tool_timings
                if timing.get("tool_name") == "p_bm25"
            )
        )

    def test_run_rag_query_agentic_slow_ordinary_retrieval_respects_deadline(self) -> None:
        message = "Please explain the recommended SDK audio profile configuration for production deployments"
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-slow-ordinary",
            text="Configure the SDK audio profile before production deployment.",
            source_path="official/audio-profile.md",
            similarity=0.91,
        )
        answer_mock = None

        def slow_bm25(*_args, **_kwargs):
            time.sleep(0.35)
            return [bm25_chunk]

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "low",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 0.05,
                "max_retries": 1,
                "context_budget_enabled": False,
            }
            with patch.dict(
                os.environ,
                {"RAG_AGENT_ENABLED": "1", "RAG_QUERY_UNDERSTANDING_ENABLED": "0"},
                clear=False,
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=slow_bm25,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value={
                    "query_class": "configuration",
                    "first_pass_tools": ["p_bm25"],
                    "query_variants": [("original", message)],
                    "decomposition_targets": [],
                    "evidence_goal": "configuration_support",
                    "recovery_bias": "lexical",
                },
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                return_value=(
                    [bm25_chunk],
                    {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                return_value=[bm25_chunk],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                return_value=(
                    {
                        "answer": "Configure the SDK audio profile.",
                        "key_steps": [],
                        "citations": ["bm25-slow-ordinary"],
                        "insufficient_evidence": False,
                    },
                    10,
                    5,
                    "gpt-5.4",
                ),
            ) as answer_mock:
                started_at = time.perf_counter()
                result = run_rag_query(message)
                elapsed = time.perf_counter() - started_at

        self.assertLess(elapsed, 0.20)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.trace.needs_human)
        self.assertEqual(result.trace.handoff_reason, "deadline_exhausted")
        self.assertTrue(result.trace.deadline_exhausted)
        self.assertEqual(result.trace.timeout_stage, "round_1_retrieval")
        self.assertEqual(result.answer.answer, INSUFFICIENT_EVIDENCE_REPLY)
        answer_mock.assert_not_called()

    def test_run_rag_query_agentic_deadline_exhausted_before_generation_returns_handoff(self) -> None:
        message = "Please explain the recommended SDK audio profile configuration for production deployments"
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-deadline",
            text="Configure the SDK audio profile before production deployment.",
            source_path="official/audio-profile.md",
            similarity=0.91,
        )
        answer_mock = None

        def fast_bm25(*_args, **_kwargs):
            return [bm25_chunk]

        def slow_metadata_rerank(*_args, **_kwargs):
            time.sleep(0.08)
            return (
                [bm25_chunk],
                {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 2,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "low",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 0.05,
                "max_retries": 1,
                "context_budget_enabled": False,
            }
            with patch.dict(
                os.environ,
                {"RAG_AGENT_ENABLED": "1", "RAG_QUERY_UNDERSTANDING_ENABLED": "0"},
                clear=False,
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                side_effect=fast_bm25,
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value={
                    "query_class": "configuration",
                    "first_pass_tools": ["p_bm25"],
                    "query_variants": [("original", message)],
                    "decomposition_targets": [],
                    "evidence_goal": "configuration_support",
                    "recovery_bias": "lexical",
                },
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=slow_metadata_rerank,
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                return_value=[bm25_chunk],
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                return_value=(
                    {
                        "answer": "Configure the SDK audio profile.",
                        "key_steps": [],
                        "citations": ["bm25-deadline"],
                        "insufficient_evidence": False,
                    },
                    10,
                    5,
                    "gpt-5.4",
                ),
            ) as answer_mock:
                result = run_rag_query(message)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.trace.needs_human)
        self.assertEqual(result.trace.handoff_reason, "deadline_exhausted")
        self.assertTrue(result.trace.deadline_exhausted)
        self.assertEqual(result.trace.timeout_stage, "answer_generation")
        self.assertEqual(result.answer.answer, INSUFFICIENT_EVIDENCE_REPLY)
        answer_mock.assert_not_called()

    def test_run_rag_query_long_how_to_faq_uses_generic_join_pinned_chunks_without_light_path(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.86,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
                "use_case": "join_channel",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.85,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        issue_summary_chunk = RetrievedChunk(
            chunk_id="issue-summary",
            text="Unity/Web audio-video sync issue after joining the same channel.",
            source_path="cases/unity-web-sync-issue.md",
            similarity=0.94,
            metadata={
                "product": "video-calling",
                "doc_subtype": "troubleshooting_case",
                "keywords": ["join channel", "channel name"],
                "topic": ["channel lifecycle"],
            },
        )
        message = (
            "Hi Team, I am new to Agora and trying to integrate Agora SDK. However, I don't know "
            "how to join the channel as requested. Could you help explain to me and guide me to "
            "join the user into the channel?"
        )
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="query-understanding-v2",
            glossary_version="glossary-v2",
            self_query_version="self-query-v2",
            normalized_query=message,
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            rewritten_queries=["how to join channel token uid quickstart"],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(
                semantic_query="Agora SDK how to join a channel and guide a user into the channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
                llm_expansions=["how to join channel token uid quickstart"],
            ),
            fallback_mode="none",
        )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 20.0,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value={
                    "query_class": "how_to_faq",
                    "first_pass_tools": ["p_bm25", "p_fts"],
                    "query_variants": [("original", message)],
                    "decomposition_targets": [],
                    "evidence_goal": "how_to_usage_support",
                    "recovery_bias": "lexical",
                },
            ), patch(
                "backend.services.rag_qa._fetch_generic_join_pinned_chunks",
                return_value=[rag_qa._copy_chunk(join_chunk), rag_qa._copy_chunk(auth_chunk)],
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("vector retrieval should not be needed when pinned generic join support exists"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[rag_qa._copy_chunk(issue_summary_chunk)],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=lambda *args, **kwargs: (
                    list(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                    {
                        "post_rerank_count": len(kwargs.get("chunks") if "chunks" in kwargs else args[1]),
                        "hints": {},
                        "applied_filter": False,
                        "filter_type": None,
                    },
                ),
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda _query, chunks, _config, *, limit=None: chunks,
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=AssertionError("generic join deterministic answer should bypass answer generation"),
            ):
                result = run_rag_query(message, product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trace.answer_profile_used, "generic_join_deterministic")
        self.assertFalse(result.trace.light_path_used)
        self.assertEqual(result.trace.selected_chunk_ids[:2], ["join-android", "auth-android"])
        self.assertIn("join method", result.answer.answer.lower())

    def test_run_rag_query_generic_join_uses_deterministic_answer_when_deadline_exhausted_after_retrieval(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.86,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/get-started-sdk",
                "use_case": "join_channel",
            },
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Request a token from your app server for the channel name and user ID before joining.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.85,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/get-started/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        message = (
            "Hi Team, I am new to Agora and trying to integrate Agora SDK. However, I don't know "
            "how to join the channel as requested. Could you help explain to me and guide me to "
            "join the user into the channel?"
        )
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="query-understanding-v2",
            glossary_version="glossary-v2",
            self_query_version="self-query-v2",
            normalized_query=message,
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[{"canonical_term": "Channel"}],
            rewritten_queries=[],
            decomposition_subqueries=[],
            retrieval_plan=RetrievalPlan(
                semantic_query="Agora SDK how to join a channel and guide a user into the channel",
                soft_signals={"topic": ["channel lifecycle"], "use_case": ["join_channel"]},
                rule_expansions=["joinChannel token uid"],
            ),
            fallback_mode="none",
        )

        def slow_metadata_rerank(*args, **kwargs):
            time.sleep(0.08)
            chunks = list(kwargs.get("chunks") if "chunks" in kwargs else args[1])
            return (
                chunks,
                {
                    "post_rerank_count": len(chunks),
                    "hints": {},
                    "applied_filter": False,
                    "filter_type": None,
                },
            )

        with patch("backend.services.rag_qa._get_rag_config") as config_mock:
            config_mock.return_value = {
                "dsn": "postgresql://example",
                "api_key": "test-key",
                "app_schema": "supportportal",
                "table": "supportportal.docagent_chunks_bge_m3_1024",
                "top_k": 3,
                "vector_candidate_k": 10,
                "bm25_candidate_k": 10,
                "keyword_candidate_k": 10,
                "fusion_candidate_k": 10,
                "rerank_top_n": 5,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "chat_model": "gpt-5.4",
                "reasoning_effort": "high",
                "embedding_provider": "siliconflow",
                "embedding_model": "BAAI/bge-m3",
                "vector_enabled": True,
                "rerank_provider": "siliconflow",
                "rerank_model": "BAAI/bge-reranker-v2-m3",
                "rerank_api_key": "test-rerank-key",
                "rerank_base_url": "https://api.siliconflow.cn/v1",
                "rerank_enabled": True,
                "rerank_timeout_seconds": 10.0,
                "rerank_max_retries": 1,
                "request_timeout_seconds": 0.05,
                "max_retries": 1,
                "context_budget_enabled": False,
                "reserved_output_tokens": 1200,
                "buffer_tokens": 1200,
            }
            with patch.dict(
                os.environ,
                {"RAG_AGENT_ENABLED": "1", "RAG_QUERY_UNDERSTANDING_ENABLED": "0"},
                clear=False,
            ), patch(
                "backend.services.rag_qa._resolve_active_vector_table",
                return_value="supportportal.docagent_chunks_bge_m3_1024",
            ), patch(
                "backend.services.rag_qa.get_embedding_provider",
                return_value=self._FakeProvider(),
            ), patch(
                "backend.services.rag_qa.understand_rag_query",
                return_value=understanding,
            ), patch(
                "backend.services.rag_qa._invoke_agentic_planner",
                return_value={
                    "query_class": "how_to_faq",
                    "first_pass_tools": ["p_bm25", "p_fts"],
                    "query_variants": [("original", message)],
                    "decomposition_targets": [],
                    "evidence_goal": "how_to_usage_support",
                    "recovery_bias": "lexical",
                },
            ), patch(
                "backend.services.rag_qa._fetch_generic_join_pinned_chunks",
                return_value=[rag_qa._copy_chunk(join_chunk), rag_qa._copy_chunk(auth_chunk)],
            ), patch(
                "backend.services.rag_qa._retrieve_chunks",
                side_effect=AssertionError("generic join deterministic rescue should not need vector retrieval"),
            ), patch(
                "backend.services.rag_qa._retrieve_bm25_chunks",
                return_value=[rag_qa._copy_chunk(join_chunk), rag_qa._copy_chunk(auth_chunk)],
            ), patch(
                "backend.services.rag_qa._retrieve_fts_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._retrieve_keyword_chunks",
                return_value=[],
            ), patch(
                "backend.services.rag_qa._metadata_rerank",
                side_effect=slow_metadata_rerank,
            ), patch(
                "backend.services.rag_qa._rerank_chunks",
                side_effect=lambda _query, chunks, _config, *, limit=None: chunks,
            ), patch(
                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                side_effect=AssertionError("deterministic rescue should bypass answer generation"),
            ):
                result = run_rag_query(message, product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.trace.needs_human)
        self.assertIsNone(result.trace.handoff_reason)
        self.assertEqual(result.trace.answer_profile_used, "generic_join_deterministic")
        self.assertTrue(result.trace.deadline_exhausted)
        self.assertIn("join method", result.answer.answer.lower())


if __name__ == "__main__":
    unittest.main()
