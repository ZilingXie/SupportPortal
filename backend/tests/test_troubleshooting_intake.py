from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.troubleshooting_intake import evaluate_troubleshooting_intake


class TroubleshootingIntakeTests(unittest.TestCase):
    def test_how_to_question_stays_answer_mode(self) -> None:
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
        self.assertEqual(result.missing_information, [])
        self.assertFalse(result.ready_for_engineer_ticket)

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


if __name__ == "__main__":
    unittest.main()
