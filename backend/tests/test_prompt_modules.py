from __future__ import annotations

import unittest

from backend.services.prompts.rag_answer import (
    build_rag_answer_system_prompt,
    build_rag_answer_user_prompt,
)
from backend.services.prompts.rag_context_compression import (
    build_rag_context_compression_system_prompt,
    build_rag_context_compression_user_prompt,
)
from backend.services.prompts.query_understanding import (
    build_query_decomposition_system_prompt,
    build_query_decomposition_user_prompt,
    build_query_rewrite_system_prompt,
    build_query_rewrite_user_prompt,
    build_self_query_system_prompt,
    build_self_query_user_prompt,
)
from backend.services.prompts.engineer_investigation_reply import (
    ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION,
    build_engineer_investigation_reply_system_prompt,
    build_engineer_investigation_reply_user_prompt,
)
from backend.services.prompts.product_selection import (
    PRODUCT_SELECTION_PROMPT_VERSION,
    build_product_selection_system_prompt,
    build_product_selection_user_prompt,
)
from backend.services.prompts.rag_agent_planner import (
    build_rag_agent_planner_system_prompt,
    build_rag_agent_planner_user_prompt,
)
from backend.services.prompts.rag_sufficiency import (
    build_rag_sufficiency_system_prompt,
    build_rag_sufficiency_user_prompt,
)
from backend.services.prompts.router import (
    build_router_system_prompt,
    build_router_user_prompt,
)
from backend.services.prompts.web_search import (
    build_web_search_system_prompt,
    build_web_search_user_prompt,
)


class PromptModuleTests(unittest.TestCase):
    def test_router_prompt_v2_is_sectioned_and_classification_only(self) -> None:
        system_prompt = build_router_system_prompt(
            route_examples=[
                {
                    "message": "Who's the CEO of Agora?",
                    "hints": {"agora": ["agora"], "public_info": ["ceo"]},
                    "output": {
                        "scope_label": "agora_non_technical",
                        "confidence": 0.98,
                        "reason": "agora_company_info",
                        "matched_signals": ["agora", "ceo"],
                    },
                }
            ]
        )
        user_prompt = build_router_user_prompt(
            payload={
                "message": "I got black screen issue, what should I do?",
                "ticket_subject": "RTC issue",
                "ticket_context": [{"role": "customer", "content": "remote video is black"}],
                "response_language": "en",
                "hints": {"technical": ["black screen"], "flags": ["looks_like_question"]},
            }
        )

        self.assertIn("## Role", system_prompt)
        self.assertIn("You only classify the request. You do not answer it.", system_prompt)
        self.assertIn("## Few-shot Examples", system_prompt)
        self.assertIn("prefer agora_technical", system_prompt)
        self.assertIn("## Inputs", user_prompt)
        self.assertIn("## Latest Message", user_prompt)
        self.assertIn("## Routing Hints", user_prompt)
        self.assertIn("black screen", user_prompt)

    def test_web_search_prompt_v2_is_grounded_and_has_insufficient_fallback(self) -> None:
        system_prompt = build_web_search_system_prompt(
            response_language="en",
            official_only=True,
        )
        user_prompt = build_web_search_user_prompt(question="Who's the CEO of Agora?")

        self.assertIn("## Role", system_prompt)
        self.assertIn("only from retrieved sources", system_prompt)
        self.assertIn("reply exactly INSUFFICIENT", system_prompt)
        self.assertIn("## Few-shot Examples", system_prompt)
        self.assertIn("## User Question", user_prompt)
        self.assertIn("Who's the CEO of Agora?", user_prompt)

    def test_rag_answer_prompt_v2_is_sectioned_and_preserves_exact_insufficient_reply(self) -> None:
        insufficient_reply = "I couldn't find enough information in the available support knowledge base to answer that question."
        system_prompt = build_rag_answer_system_prompt(insufficient_reply=insufficient_reply)
        user_prompt = build_rag_answer_user_prompt(
            question="How do I join a channel?",
            context_block="chunk-1: Join a channel with the SDK's join method.",
            insufficient_reply=insufficient_reply,
            repair_mode=True,
        )

        self.assertIn("## Role", system_prompt)
        self.assertIn("only on the provided context chunks", system_prompt)
        self.assertIn("## User Question", user_prompt)
        self.assertIn("## Context Chunks", user_prompt)
        self.assertIn("## Output Requirements", user_prompt)
        self.assertIn("## Fallback Policy", user_prompt)
        self.assertIn("## Few-shot Examples", user_prompt)
        self.assertIn(insufficient_reply, user_prompt)
        self.assertIn("## Repair Requirements", user_prompt)

    def test_rag_answer_system_prompt_includes_selected_product_scope(self) -> None:
        system_prompt = build_rag_answer_system_prompt(
            insufficient_reply="INSUFFICIENT",
            product_role="You are Agora tech support handling a Cloud Recording issue.",
            product_scope="Selected support product: Cloud Recording.",
        )

        self.assertIn("Cloud Recording issue", system_prompt)
        self.assertIn("## Product Scope", system_prompt)
        self.assertIn("Cloud Recording", system_prompt)

    def test_rag_sufficiency_prompt_v2_is_sectioned_and_conservative(self) -> None:
        system_prompt = build_rag_sufficiency_system_prompt()
        user_prompt = build_rag_sufficiency_user_prompt(
            message="How do I join a channel?",
            ticket_subject="Join channel",
            ticket_context=[{"role": "customer", "content": "How do I join a channel?"}],
            route_summary={"scope_label": "agora_technical", "execution_action": "rag"},
            rag_answer="Use the SDK's join-channel method with the required parameters.",
            sources=["https://docs.agora.io/en/video-calling/get-started/get-started-sdk"],
            citations=[{"chunk_id": "chunk-1", "source_path": "official/join.md"}],
            evidence_summary={"quality_signals": {"selected_doc_count": 1}},
        )

        self.assertIn("## Role", system_prompt)
        self.assertIn("When in doubt, choose investigate", system_prompt)
        self.assertIn("Do not rewrite the answer.", system_prompt)
        self.assertIn("## Few-shot Examples", system_prompt)
        self.assertIn("## Customer Message", user_prompt)
        self.assertIn("## Candidate Answer", user_prompt)
        self.assertIn("## Evidence Summary", user_prompt)
        self.assertIn("## Required Output Schema", user_prompt)

    def test_query_understanding_prompts_are_sectioned_and_grounded(self) -> None:
        self_query_system = build_self_query_system_prompt()
        self_query_user = build_self_query_user_prompt(
            query="Compare BuildTokenWithUid vs BuildTokenWithUidAndPrivilege for Node.js.",
            glossary_hits=[
                {
                    "canonical_term": "App certificate",
                    "matched_text": "app certificate",
                    "definition": "An app certificate is a randomly generated string provided by Agora.",
                }
            ],
        )
        rewrite_system = build_query_rewrite_system_prompt()
        rewrite_user = build_query_rewrite_user_prompt(
            query="How do I handle token expiry?",
            canonical_terms=["App certificate"],
            glossary_hits=[{"canonical_term": "App certificate"}],
            retrieval_plan_summary={"semantic_query": "token expiry troubleshooting"},
        )
        decomposition_system = build_query_decomposition_system_prompt()
        decomposition_user = build_query_decomposition_user_prompt(
            query="Compare BuildTokenWithUid vs BuildTokenWithUidAndPrivilege for Node.js.",
            retrieval_plan_summary={"hard_filters": {"language": "nodejs"}},
        )

        self.assertIn("## Role", self_query_system)
        self.assertIn("Return JSON only", self_query_system)
        self.assertIn("## Field Definitions", self_query_system)
        self.assertIn("## Few-shot Examples", self_query_system)
        self.assertIn("## Glossary Hits", self_query_user)
        self.assertIn("BuildTokenWithUidAndPrivilege", self_query_user)

        self.assertIn("## Role", rewrite_system)
        self.assertIn("Do not change the user intent", rewrite_system)
        self.assertIn("## Fallback Policy", rewrite_system)
        self.assertIn("## Canonical Terms", rewrite_user)
        self.assertIn("App certificate", rewrite_user)

        self.assertIn("## Role", decomposition_system)
        self.assertIn("Only decompose when the request is genuinely multi-part", decomposition_system)
        self.assertIn("## Required Output Schema", decomposition_user)
        self.assertIn("nodejs", decomposition_user)

    def test_rag_agent_planner_prompt_is_sectioned_and_ticket_context_aware(self) -> None:
        system_prompt = build_rag_agent_planner_system_prompt(
            product_role="You are Agora tech support handling an Audio/Video Calling issue.",
            product_scope="Selected support product: Audio/Video Calling.",
        )
        user_prompt = build_rag_agent_planner_user_prompt(
            message="What does error 109 mean?",
            ticket_context=[{"role": "customer", "content": "We only see this on iOS 4.6.0"}],
            query_understanding_summary={
                "query_profile": "en",
                "semantic_query": "error 109 meaning",
                "hard_filters": {"language": "ios"},
                "rewritten_queries": ["error 109 meaning ios"],
            },
            top_k=5,
            round_index=1,
        )

        self.assertIn("## Role", system_prompt)
        self.assertIn("You plan retrieval only", system_prompt)
        self.assertIn("Audio/Video Calling issue", system_prompt)
        self.assertIn("## Product Scope", system_prompt)
        self.assertIn("## Output Requirements", system_prompt)
        self.assertIn('"how_to_faq"', system_prompt)
        self.assertIn("## Latest User Question", user_prompt)
        self.assertIn("## Ticket Context", user_prompt)
        self.assertIn("## Query Understanding Prior", user_prompt)
        self.assertIn("error 109 meaning ios", user_prompt)

    def test_product_selection_prompt_is_sectioned_and_json_only(self) -> None:
        system_prompt = build_product_selection_system_prompt()
        user_prompt = build_product_selection_user_prompt(
            latest_customer_message="We call acquire, get the sid, and then no recording file is generated.",
            ticket_subject="recording file missing",
            ticket_context=[{"role": "customer", "content": "No recording file is generated."}],
            current_product=None,
            awaiting_confirmation=False,
            allowed_products=[
                {"value": "audio_video_calling", "label": "Audio/Video Calling"},
                {"value": "cloud_recording", "label": "Cloud Recording"},
            ],
        )

        self.assertEqual(PRODUCT_SELECTION_PROMPT_VERSION, "product-selection-v1")
        self.assertIn("## Role", system_prompt)
        self.assertIn("You only classify the product.", system_prompt)
        self.assertIn('"unknown"', system_prompt)
        self.assertIn('Return JSON only with keys "product", "confidence", "reason", and "matched_signals".', system_prompt)
        self.assertIn("## Latest Customer Message", user_prompt)
        self.assertIn("## Recent Ticket Context", user_prompt)
        self.assertIn("## Allowed Products", user_prompt)
        self.assertIn("sid", user_prompt)

    def test_rag_context_compression_prompt_is_sectioned_and_json_only(self) -> None:
        system_prompt = build_rag_context_compression_system_prompt()
        user_prompt = build_rag_context_compression_user_prompt(
            question="How do I join a channel?",
            evidence_segments=[
                {
                    "chunk_id": "chunk-1",
                    "source_path": "official/channel.md",
                    "heading": "Channel > Join a channel",
                    "snippet": "Use joinChannel with the same channel name to enter the same communication session.",
                }
            ],
            available_context_tokens=320,
        )

        self.assertIn("## Role", system_prompt)
        self.assertIn("Return strict JSON only", system_prompt)
        self.assertIn("## Few-shot Examples", system_prompt)
        self.assertIn("## User Question", user_prompt)
        self.assertIn("## Compression Budget", user_prompt)
        self.assertIn("chunk-1", user_prompt)
        self.assertIn("## Required Output Schema", user_prompt)

    def test_engineer_investigation_reply_prompt_is_sectioned_and_json_only(self) -> None:
        system_prompt = build_engineer_investigation_reply_system_prompt()
        user_prompt = build_engineer_investigation_reply_user_prompt(
            customer_language_hint="en",
            latest_customer_message="I got a black screen after joining the call.",
            latest_public_assistant_reply="This issue requires further internal investigation, which may take some time. Thank you for your patience. We expect to reply here within 24 hours.",
            ticket_conversation_summary="Customer: black screen after join | Sid: opened engineer ticket",
            investigation_thread_summary=(
                "Sid: Please confirm the reproduction scope first. | "
                "jack: you need to get the channel name"
            ),
            handoff_packet_summary="unresolved_reason=rag_post_check_insufficient; product=audio_video_calling",
            agent_state_summary="phase=gather_missing_inputs; next_request_for_engineer=Confirm the missing channel name.",
            engineer_message="you need to get the channel name",
            revision_note="",
            current_draft_customer_reply="",
        )

        self.assertEqual(
            ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION,
            "engineer-investigation-reply-v7",
        )
        self.assertIn("## Role", system_prompt)
        self.assertIn("You are Sid inside an internal support investigation workflow.", system_prompt)
        self.assertIn("If the customer-facing draft self-refers, use Sid as the assistant name.", system_prompt)
        self.assertIn("formal customer email", system_prompt)
        self.assertIn('end with exactly "Best Regards," followed by "Sid"', system_prompt)
        self.assertIn("symptom_and_workaround_only", system_prompt)
        self.assertIn("root_cause_confirmed", system_prompt)
        self.assertIn("advisory_followups", system_prompt)
        self.assertIn("symptom-level", system_prompt)
        self.assertIn("Conclusion is recommended but not required.", system_prompt)
        self.assertIn("Without an explicit conclusion, you may only use symptom_and_workaround_only", system_prompt)
        self.assertIn("known_facts must only contain current customer reports, verified reproduction details, logs, versions, config facts, or cited evidence.", system_prompt)
        self.assertIn("Do not put Sid/client AI candidate answers, draft recommendations, or unverified suggestions into known_facts.", system_prompt)
        self.assertIn("Return strict JSON only", system_prompt)
        self.assertIn('"state"', system_prompt)
        self.assertIn('"draft_customer_reply"', system_prompt)
        self.assertIn('"reply_readiness"', system_prompt)
        self.assertIn('"proof_anchors"', system_prompt)
        self.assertIn('"reply_scope"', system_prompt)
        self.assertIn("## Latest Customer Message", user_prompt)
        self.assertIn("## Current Investigation Thread", user_prompt)
        self.assertIn("## Ticket-Level Agent State", user_prompt)
        self.assertIn("## Latest Engineer Update", user_prompt)
        self.assertIn("channel name", user_prompt)


if __name__ == "__main__":
    unittest.main()
