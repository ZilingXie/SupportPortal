from __future__ import annotations

import os
import types
import unittest
from unittest.mock import patch

from backend.services.troubleshooting_intake import build_client_intake_state, evaluate_troubleshooting_intake


class TroubleshootingIntakeTests(unittest.TestCase):
    def test_how_to_question_requests_goal_and_blocker_clarification(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = evaluate_troubleshooting_intake(
                message="How do I join channel?",
                product="audio_video_calling",
                ticket_subject="Join channel",
                ticket_context=[{"role": "customer", "content": "How do I join channel?"}],
                current_state=None,
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
            )

        self.assertEqual(result.issue_mode, "answer")
        self.assertEqual(result.missing_information, ["desired_outcome", "blocked_step_or_error"])
        self.assertFalse(result.ready_for_engineer_ticket)
        self.assertIn("What are you trying to achieve", result.customer_reply)
        self.assertIn("What error or blocker are you seeing", result.customer_reply)

        intake_state = build_client_intake_state(
            result,
            product="audio_video_calling",
            now_value="2026-04-04T10:00:00Z",
        )
        self.assertIsNotNone(intake_state)
        assert intake_state is not None
        self.assertEqual(intake_state["issue_mode"], "answer")
        self.assertEqual(
            intake_state["missing_information"],
            ["desired_outcome", "blocked_step_or_error"],
        )

    def test_audio_video_issue_requests_channel_uid_and_timestamp(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = evaluate_troubleshooting_intake(
                message="I got black screen issue.",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[{"role": "customer", "content": "I got black screen issue."}],
                current_state=None,
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
            )

        self.assertEqual(result.issue_mode, "investigation")
        self.assertEqual(result.known_information["issue_symptom"], "black screen issue")
        self.assertEqual(
            result.missing_information,
            ["channel_name", "problematic_uid", "issue_timestamp"],
        )
        self.assertFalse(result.ready_for_engineer_ticket)
        self.assertIn("Known so far", result.customer_reply)

    def test_cloud_recording_issue_requests_sid(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = evaluate_troubleshooting_intake(
                message="Cloud recording failed to generate the file.",
                product="cloud_recording",
                ticket_subject="Cloud recording failed",
                ticket_context=[{"role": "customer", "content": "Cloud recording failed to generate the file."}],
                current_state=None,
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
            )

        self.assertEqual(result.issue_mode, "investigation")
        self.assertEqual(result.known_information["issue_symptom"], "cloud recording failed to generate the file")
        self.assertEqual(result.missing_information, ["sid", "issue_timestamp"])
        self.assertIn("sid", result.customer_reply.lower())

    def test_follow_up_merges_existing_inputs_and_marks_ready_for_engineer_ticket(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = evaluate_troubleshooting_intake(
                message="Channel name is demo-room. Problematic uid is 42. The issue happened at 2026-04-04T10:30:00Z.",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[
                    {"role": "customer", "content": "I got black screen issue."},
                    {
                        "role": "assistant",
                        "content": "Please share the channel name, problematic uid, and issue timestamp.",
                    },
                ],
                current_state={
                    "phase": "gather_customer_inputs",
                    "product": "audio_video_calling",
                    "issue_mode": "investigation",
                    "known_information": {"issue_symptom": "black screen issue"},
                    "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                    "ready_for_engineer_ticket": False,
                    "last_updated_at": "2026-04-04T10:00:00Z",
                },
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
            )

        self.assertEqual(result.issue_mode, "investigation")
        self.assertEqual(result.known_information["channel_name"], "demo-room")
        self.assertEqual(result.known_information["problematic_uid"], "42")
        self.assertEqual(result.known_information["issue_timestamp"], "2026-04-04T10:30:00Z")
        self.assertEqual(result.missing_information, [])
        self.assertTrue(result.ready_for_engineer_ticket)

    def test_answer_mode_follow_up_merges_goal_and_blocker_and_marks_ready_for_engineer_ticket(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = evaluate_troubleshooting_intake(
                message=(
                    "I'm trying to join a channel with two users, but I get error 109 "
                    "when calling joinChannel."
                ),
                product="audio_video_calling",
                ticket_subject="Join channel",
                ticket_context=[
                    {"role": "customer", "content": "How do I join channel?"},
                    {
                        "role": "assistant",
                        "content": (
                            "I couldn't verify a grounded answer yet. What are you trying to achieve? "
                            "What error or blocker are you seeing?"
                        ),
                    },
                ],
                current_state={
                    "phase": "gather_customer_inputs",
                    "product": "audio_video_calling",
                    "issue_mode": "answer",
                    "known_information": {},
                    "missing_information": ["desired_outcome", "blocked_step_or_error"],
                    "ready_for_engineer_ticket": False,
                    "last_updated_at": "2026-04-04T10:00:00Z",
                },
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
            )

        self.assertEqual(result.issue_mode, "answer")
        self.assertIn("join a channel", result.known_information["desired_outcome"].lower())
        self.assertIn("error 109", result.known_information["blocked_step_or_error"].lower())
        self.assertEqual(result.missing_information, [])
        self.assertTrue(result.ready_for_engineer_ticket)
        self.assertEqual(result.customer_reply, "")

    def test_llm_cannot_mark_investigation_ready_when_required_fields_are_missing(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
            "backend.services.troubleshooting_intake.invoke_responses_text",
            return_value=types.SimpleNamespace(
                text=(
                    '{"issue_mode":"investigation","known_information":{"issue_symptom":"black screen issue"},'
                    '"missing_information":[],"ready_for_engineer_ticket":true,"customer_reply":""}'
                )
            ),
        ):
            result = evaluate_troubleshooting_intake(
                message="I got black screen issue.",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[{"role": "customer", "content": "I got black screen issue."}],
                current_state=None,
                rag_result={
                    "reason": "rag_post_check_insufficient",
                    "answer": "The current grounded answer is still not enough to prove a fix.",
                    "evidence_summary": {},
                },
            )

        self.assertEqual(result.issue_mode, "investigation")
        self.assertEqual(result.known_information["issue_symptom"], "black screen issue")
        self.assertEqual(
            result.missing_information,
            ["channel_name", "problematic_uid", "issue_timestamp"],
        )
        self.assertFalse(result.ready_for_engineer_ticket)
        self.assertIn("channel name", result.customer_reply.lower())
        self.assertIn("problematic uid", result.customer_reply.lower())
        self.assertIn("issue timestamp", result.customer_reply.lower())


if __name__ == "__main__":
    unittest.main()
