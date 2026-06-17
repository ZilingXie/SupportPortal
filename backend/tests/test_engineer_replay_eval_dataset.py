"""Contract tests for the engineer replay eval dataset builder.

These tests verify that the builder produces deterministic, complete
replay eval items from the closed engineer case lifecycle, and that
missing optional fields degrade gracefully.
"""

from __future__ import annotations

import unittest

from backend.services.engineer_replay_eval_dataset import (
    build_engineer_replay_eval_item,
    SCHEMA_VERSION,
)


def _base_client_ticket() -> dict:
    return {
        "ticket_id": "TK-RE-1",
        "customer_id": "C-TEST",
        "requester": "ziling",
        "product": "audio_video_calling",
        "status": "communicating",
        "engineer_handoff_packet": {
            "source": "worker_async_rag",
            "packet_id": "summary_EC-RE-1-1",
            "packet_version": "engineer-summary-packet-v1",
            "summary_agent_version": "engineer-summary-agent-v1",
            "conversation_summary": "Token renew callback does not fire.",
            "latest_customer_message": "token renew callback never fires",
            "latest_client_ai_reply": "...",
            "route_summary": {"answer_route": "rag", "route_reason": "rag_insufficient_evidence"},
            "rag_result": {
                "candidate_answer": "Upgrade to SDK 4.2.2.",
                "sources": ["https://docs.agora.io/..."],
                "citations": [],
            },
            "unresolved_reason": "rag_insufficient_evidence",
            "customer_language_hint": "en",
            "created_at": "2026-06-01T09:00:00Z",
            "escalation": {"reason": "rag_insufficient_evidence", "confidence": 0.7},
            "engineer_case_ref": {
                "engineer_case_id": "EC-RE-1-1",
                "trigger_reason": "rag_insufficient_evidence",
            },
        },
    }


def _base_engineer_case() -> dict:
    return {
        "engineer_case_id": "EC-RE-1-1",
        "client_ticket_id": "TK-RE-1",
        "case_sequence": 1,
        "title": "Token renew issue",
        "status": "resolved",
        "engineer_agent_state": {
            "phase": "closed",
            "run_id": "run-re-1",
            "plan_id": "plan-re-1",
            "evidence_packet_id": "evidence-re-1",
            "execution_id": "exec-re-1",
            "review_id": "review-re-1",
            "review_version": "engineer-review-agent-v1",
            "review_agent_version": "engineer-review-agent-v1",
            "issue_understanding": "Token renew callback does not fire on Android 14.",
            "goal": "Send the approved SDK upgrade guidance to the customer.",
            "active_review": {
                "review_id": "review-re-1",
                "review_decision": "ready_for_engineer",
                "evidence_packet_id": "evidence-re-1",
                "created_at": "2026-06-01T09:05:00Z",
            },
            "active_guardrail_final": {
                "guardrail_id": "GRD-re-1",
                "guardrail_version": "engineer-guardrail-final-v1",
                "decision": "approved_for_final_engineer_review",
                "checks": {
                    "proof": {"passed": True, "detail": "ok"},
                    "citation": {"passed": True, "detail": "ok"},
                    "no_internal_leakage": {"passed": True, "detail": "ok"},
                    "no_unsupported_claims": {"passed": True, "detail": "ok"},
                    "style": {"passed": True, "detail": "ok"},
                },
                "blockers": [],
            },
            "replan_history": [],
            "final_approved_at": "2026-06-01T09:10:00Z",
        },
    }


def _base_feedback() -> dict:
    return {
        "feedback_id": "hitl_auto_EC-RE-1-1",
        "engineer_case_id": "EC-RE-1-1",
        "client_ticket_id": "TK-RE-1",
        "feedback_type": "resolve",
        "evidence_refs": [{"source_id": "msg-1"}],
        "memory_candidate": "needs_review",
        "memory_safety": "internal_only",
        "created_by": "engineer_ai_auto_review",
        "created_at": "2026-06-01T09:10:00Z",
    }


def _base_ledger() -> dict:
    return {
        "memory_record_id": "cm_hitl_auto_EC-RE-1-1",
        "engineer_case_id": "EC-RE-1-1",
        "client_ticket_id": "TK-RE-1",
        "source_feedback_id": "hitl_auto_EC-RE-1-1",
        "ledger_status": "candidate",
        "retrieval_enabled": False,
        "active_memory_status": "inactive",
        "created_at": "2026-06-01T09:10:00Z",
    }


class EngineerReplayEvalDatasetTests(unittest.TestCase):
    # -- positive path -------------------------------------------------

    def test_item_id_is_deterministic_for_same_engineer_case(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        feedback = _base_feedback()
        ledger = _base_ledger()

        item1 = build_engineer_replay_eval_item(
            client_ticket=ticket,
            engineer_case=engineer_case,
            closed_investigation=None,
            saved_feedback=feedback,
            saved_ledger=ledger,
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        item2 = build_engineer_replay_eval_item(
            client_ticket=ticket,
            engineer_case=engineer_case,
            closed_investigation=None,
            saved_feedback=feedback,
            saved_ledger=ledger,
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item1["eval_item_id"], item2["eval_item_id"])
        self.assertEqual(item1["eval_item_id"], "ereplay_EC-RE-1-1")

    def test_summary_packet_id_and_version_are_captured(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item["source_summary_packet_id"], "summary_EC-RE-1-1")
        self.assertEqual(item["source_summary_packet_version"], "engineer-summary-packet-v1")

    def test_summary_packet_prefers_engineer_case_handoff_packet(self) -> None:
        client_ticket = _base_client_ticket()
        client_ticket.pop("engineer_handoff_packet")
        engineer_case = _base_engineer_case()
        engineer_case["engineer_handoff_packet"] = _base_client_ticket()["engineer_handoff_packet"]

        item = build_engineer_replay_eval_item(
            client_ticket=client_ticket,
            engineer_case=engineer_case,
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )

        self.assertEqual(item["source_summary_packet_id"], "summary_EC-RE-1-1")
        self.assertEqual(item["replay_input"]["summary_packet_id"], "summary_EC-RE-1-1")
        self.assertNotIn("missing_source_summary_packet_id", item["data_quality_warnings"])

    def test_review_decision_and_trace_are_saved(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item["review_decision"], "ready_for_engineer")
        self.assertIsInstance(item["review_trace"], dict)
        decisions = item["review_trace"].get("decisions", [])
        self.assertGreaterEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["review_decision"], "ready_for_engineer")
        self.assertEqual(decisions[0]["review_id"], "review-re-1")

    def test_replan_history_becomes_replan_notes(self) -> None:
        engineer_case = _base_engineer_case()
        engineer_case["engineer_agent_state"]["replan_history"] = [
            {
                "review_id": "review-re-2",
                "review_decision": "replan_required",
                "replan_reason": "missing evidence",
                "plan_id": "plan-re-2",
                "created_at": "2026-06-01T09:06:00Z",
            },
        ]
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=engineer_case,
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertGreaterEqual(len(item["replan_notes"]), 1)
        replan_note = item["replan_notes"][0]
        self.assertEqual(replan_note["reason"], "missing evidence")
        self.assertEqual(replan_note["plan_id"], "plan-re-2")

    def test_revise_context_carries_engineer_feedback(self) -> None:
        engineer_case = _base_engineer_case()
        engineer_case["engineer_agent_state"]["last_revise_context"] = {
            "engineer_id": "eng",
            "note": "Root cause is SDK bug, not config.",
            "created_at": "2026-06-01T09:07:00Z",
            "previous_review": {
                "review_decision": "ready_for_engineer",
                "problem_statement": "Token renew fails.",
                "evidence_packet_id": "evidence-re-1",
            },
        }
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=engineer_case,
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertGreaterEqual(len(item["engineer_revise_feedback"]), 1)
        revise = item["engineer_revise_feedback"][0]
        self.assertEqual(revise["engineer_id"], "eng")
        self.assertIn("Root cause is SDK bug", revise["note"])
        self.assertEqual(revise["previous_review_decision"], "ready_for_engineer")
        self.assertEqual(revise["previous_review_problem"], "Token renew fails.")
        self.assertEqual(revise["previous_review_evidence_packet_id"], "evidence-re-1")

    def test_final_approved_reply_in_reference_output(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Hi there,\n\nPlease upgrade to SDK 4.2.2.\n\nBest Regards,\nSid",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(
            item["reference_output"]["approved_reply"],
            "Hi there,\n\nPlease upgrade to SDK 4.2.2.\n\nBest Regards,\nSid",
        )
        self.assertEqual(item["approved_reply"], "Hi there,\n\nPlease upgrade to SDK 4.2.2.\n\nBest Regards,\nSid")

    def test_expected_outcome_is_resolved_with_customer_reply(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item["expected_outcome"], "resolved_with_customer_reply")

    def test_dataset_status_is_candidate(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item["dataset_status"], "candidate")

    def test_schema_version_is_set(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item["schema_version"], SCHEMA_VERSION)

    def test_guardrail_final_is_captured(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        gf = item["guardrail_final"]
        self.assertEqual(gf["guardrail_id"], "GRD-re-1")
        self.assertEqual(gf["decision"], "approved_for_final_engineer_review")
        self.assertEqual(len(gf["blockers"]), 0)
        self.assertIn("proof", gf["checks"])

    def test_replay_input_contains_minimal_context(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        ri = item["replay_input"]
        self.assertEqual(ri["summary_packet_id"], "summary_EC-RE-1-1")
        self.assertEqual(ri["summary_packet_version"], "engineer-summary-packet-v1")
        self.assertEqual(ri["trigger_source"], "worker_async_rag")
        self.assertEqual(ri["trigger_reason"], "rag_insufficient_evidence")

    def test_reference_output_contains_evidence_refs_and_memory_candidate_ids(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        ro = item["reference_output"]
        self.assertEqual(len(ro["evidence_refs"]), 1)
        self.assertEqual(ro["evidence_refs"][0]["source_id"], "msg-1")
        self.assertIn("cm_hitl_auto_EC-RE-1-1", ro["case_memory_candidate_ids"])

    # -- missing optional fields ---------------------------------------

    def test_missing_summary_packet_generates_warning_but_succeeds(self) -> None:
        ticket = _base_client_ticket()
        ticket["engineer_handoff_packet"] = {}
        item = build_engineer_replay_eval_item(
            client_ticket=ticket,
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        # Still produces a candidate
        self.assertEqual(item["dataset_status"], "candidate")
        self.assertEqual(item["source_summary_packet_id"], "")
        self.assertIn("missing_source_summary_packet_id", item["data_quality_warnings"])

    def test_missing_approved_reply_generates_warning(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item["dataset_status"], "candidate")
        self.assertIn("missing_approved_reply", item["data_quality_warnings"])
        self.assertEqual(item["approved_reply"], "")

    def test_missing_replan_yields_empty_notes(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item["replan_notes"], [])

    def test_missing_revise_yields_empty_feedback(self) -> None:
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item["engineer_revise_feedback"], [])

    def test_missing_handoff_packet_entirely_still_produces_valid_item(self) -> None:
        ticket = _base_client_ticket()
        del ticket["engineer_handoff_packet"]
        item = build_engineer_replay_eval_item(
            client_ticket=ticket,
            engineer_case=_base_engineer_case(),
            closed_investigation=None,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        self.assertEqual(item["dataset_status"], "candidate")
        self.assertEqual(item["source_summary_packet_id"], "")

    def test_investigation_messages_flow_into_replan_notes_and_revise_feedback(self) -> None:
        closed_investigation = {
            "id": "INV-RE-1",
            "state": "closed",
            "messages": [
                {
                    "id": "msg-re-1",
                    "role": "engineer",
                    "content": "Root cause is actually SDK bug, not config issue.",
                    "created_at": "2026-06-01T09:07:00Z",
                },
                {
                    "id": "msg-re-2",
                    "role": "engineer_ai",
                    "content": "Replan complete.",
                    "created_at": "2026-06-01T09:08:00Z",
                },
            ],
        }
        item = build_engineer_replay_eval_item(
            client_ticket=_base_client_ticket(),
            engineer_case=_base_engineer_case(),
            closed_investigation=closed_investigation,
            saved_feedback=_base_feedback(),
            saved_ledger=_base_ledger(),
            customer_reply="Please upgrade.",
            created_at="2026-06-01T09:10:00Z",
        )
        # engineer messages go to both replan_notes and engineer_revise_feedback
        self.assertGreaterEqual(len(item["replan_notes"]), 1)
        self.assertGreaterEqual(len(item["engineer_revise_feedback"]), 1)


if __name__ == "__main__":
    unittest.main()
