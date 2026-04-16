from __future__ import annotations

import os
import types
import unittest
from unittest.mock import patch

from backend.services.troubleshooting_intake import (
    build_client_intake_state,
    customer_follow_up_adds_requested_investigation_detail,
    evaluate_troubleshooting_intake,
    resolve_investigation_clarification_rounds_used,
)


class TroubleshootingIntakeTests(unittest.TestCase):
    _BAN_API_MISMATCH_MESSAGE = """Hello, Agora team.

We are using the Ban User Privileges API (POST /dev/v1/kicking-rule) to disband channels after a broadcast ends, but we have found some differences between the official documentation and the actual API behavior, so we would like to inquire about them.

1. uid: 0 cannot be used
According to the documentation
(https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel), when targeting all users in a channel, it says to use uid: 0. However, in actual use:
"uid": 0 (number) -> Error: uid '0' must be a number, or set str_uid = true

2. Cannot create a permanent rule with time: 0
The documentation states that time: 0 means the rule is applied permanently. However, when we actually send time: 0, the API returns {"status":"success","id":0}, but no rule is created."""

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

        intake_state = build_client_intake_state(
            result,
            product="audio_video_calling",
            now_value="2026-04-04T10:00:00Z",
        )
        self.assertIsNotNone(intake_state)
        assert intake_state is not None
        self.assertEqual(intake_state["clarification_rounds_used"], 1)

    def test_resolve_investigation_clarification_rounds_used_infers_prior_assistant_intake_prompt(self) -> None:
        rounds_used = resolve_investigation_clarification_rounds_used(
            current_state={
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {"issue_symptom": "black screen issue"},
                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "last_updated_at": "2026-04-04T10:00:00Z",
            },
            latest_assistant_message=None,
            ticket_context=[
                {"role": "customer", "content": "i got black screen issue"},
                {
                    "role": "assistant",
                    "content": (
                        "Known so far: issue symptom is black screen issue. "
                        "To investigate this Audio/Video Calling issue, please share channel name, "
                        "problematic uid, and issue timestamp."
                    ),
                },
            ],
        )

        self.assertEqual(rounds_used, 1)

    def test_partial_timestamp_follow_up_counts_as_added_requested_investigation_detail(self) -> None:
        self.assertTrue(
            customer_follow_up_adds_requested_investigation_detail(
                message="the issue happened on april 3rd",
                product="audio_video_calling",
                current_state={
                    "phase": "gather_customer_inputs",
                    "product": "audio_video_calling",
                    "issue_mode": "investigation",
                    "known_information": {
                        "issue_symptom": "black screen issue",
                        "channel_name": "zilingtest",
                        "problematic_uid": "2",
                    },
                    "missing_information": ["issue_timestamp"],
                    "ready_for_engineer_ticket": False,
                    "last_updated_at": "2026-04-14T02:08:33.337732+00:00",
                },
                message_created_at="2026-04-14T02:11:08.752498+00:00",
            )
        )

    def test_docs_api_semantics_mismatch_does_not_request_channel_or_timestamp(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = evaluate_troubleshooting_intake(
                message=self._BAN_API_MISMATCH_MESSAGE,
                product="audio_video_calling",
                ticket_subject="Ban User Privileges API mismatch",
                ticket_context=[{"role": "customer", "content": self._BAN_API_MISMATCH_MESSAGE}],
                current_state=None,
                rag_result={
                    "reason": "deadline_exhausted",
                    "answer": "",
                    "evidence_summary": {
                        "diagnostics": {
                            "retrieval_plan_snapshot": {
                                "query_class": "api_semantics_mismatch",
                            }
                        }
                    },
                },
            )

        self.assertEqual(result.issue_mode, "answer")
        self.assertNotIn("channel_name", result.missing_information)
        self.assertNotIn("issue_timestamp", result.missing_information)
        self.assertIn("platform", result.customer_reply.lower())
        self.assertIn("sdk", result.customer_reply.lower())

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

    def test_partial_timestamp_follow_up_only_requests_missing_timezone(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = evaluate_troubleshooting_intake(
                message="channel name:zilingtest, uid 1, happened around 3/4 at 12pm",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[
                    {"role": "customer", "content": "i got black screen, what should i do?"},
                    {
                        "role": "assistant",
                        "content": (
                            "Known so far: issue symptom is black screen issue. "
                            "Please share the channel name, problematic uid, and issue timestamp."
                        ),
                    },
                ],
                current_state={
                    "phase": "gather_customer_inputs",
                    "product": "audio_video_calling",
                    "issue_mode": "investigation",
                    "known_information": {"issue_symptom": "black screen issue"},
                    "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                    "ready_for_engineer_ticket": False,
                    "last_updated_at": "2026-03-04T04:00:00+00:00",
                },
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
                message_created_at="2026-03-04T04:00:00+00:00",
            )

        self.assertEqual(result.issue_mode, "investigation")
        self.assertEqual(result.known_information["channel_name"], "zilingtest")
        self.assertEqual(result.known_information["problematic_uid"], "1")
        self.assertEqual(result.known_information["issue_symptom"], "black screen issue")
        self.assertEqual(result.missing_information, ["issue_timestamp"])
        self.assertFalse(result.ready_for_engineer_ticket)
        self.assertIn("timezone", result.customer_reply.lower())
        self.assertNotIn("full timestamp", result.customer_reply.lower())
        self.assertNotIn("date", result.customer_reply.lower())

        intake_state = build_client_intake_state(
            result,
            product="audio_video_calling",
            now_value="2026-03-04T04:00:00+00:00",
        )
        self.assertIsNotNone(intake_state)
        assert intake_state is not None
        self.assertEqual(
            intake_state["issue_timestamp_parts"],
            {"date": "2026-03-04", "time": "12:00pm"},
        )

    def test_follow_up_merges_timestamp_fragments_across_turns_and_later_full_timestamp_overrides(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            first_result = evaluate_troubleshooting_intake(
                message="channel name:zilingtest, uid 1, happened around 3/4 at 12pm",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[{"role": "customer", "content": "i got black screen, what should i do?"}],
                current_state={
                    "phase": "gather_customer_inputs",
                    "product": "audio_video_calling",
                    "issue_mode": "investigation",
                    "known_information": {"issue_symptom": "black screen issue"},
                    "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                    "ready_for_engineer_ticket": False,
                    "last_updated_at": "2026-03-04T04:00:00+00:00",
                },
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
                message_created_at="2026-03-04T04:00:00+00:00",
            )
            current_state = build_client_intake_state(
                first_result,
                product="audio_video_calling",
                now_value="2026-03-04T04:00:00+00:00",
            )
            assert current_state is not None

            merged_result = evaluate_troubleshooting_intake(
                message="it happened at 12:00pm utc+8",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[
                    {"role": "customer", "content": "i got black screen, what should i do?"},
                    {
                        "role": "customer",
                        "content": "channel name:zilingtest, uid 1, happened around 3/4 at 12pm",
                    },
                    {"role": "assistant", "content": first_result.customer_reply},
                ],
                current_state=current_state,
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
                message_created_at="2026-03-04T04:05:00+00:00",
            )

            overridden_result = evaluate_troubleshooting_intake(
                message="2026-03-06 12:00pm utc+8",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[
                    {"role": "customer", "content": "i got black screen, what should i do?"},
                    {
                        "role": "customer",
                        "content": "channel name:zilingtest, uid 1, happened around 3/4 at 12pm",
                    },
                    {"role": "assistant", "content": first_result.customer_reply},
                    {"role": "customer", "content": "it happened at 12:00pm utc+8"},
                ],
                current_state=build_client_intake_state(
                    merged_result,
                    product="audio_video_calling",
                    now_value="2026-03-04T04:05:00+00:00",
                ),
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
                message_created_at="2026-03-06T04:00:00+00:00",
            )

        self.assertEqual(merged_result.missing_information, [])
        self.assertTrue(merged_result.ready_for_engineer_ticket)
        self.assertEqual(merged_result.known_information["issue_timestamp"], "2026-03-04 12:00pm UTC+8")
        self.assertEqual(overridden_result.missing_information, [])
        self.assertTrue(overridden_result.ready_for_engineer_ticket)
        self.assertEqual(overridden_result.known_information["issue_timestamp"], "2026-03-06 12:00pm UTC+8")

    def test_month_name_date_with_ordinal_counts_as_complete_issue_timestamp(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = evaluate_troubleshooting_intake(
                message="channel name: zilingtest,uid:1, happened on April 3rd 12pm utc+8",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[{"role": "customer", "content": "i got black screen, what should i do?"}],
                current_state={
                    "phase": "gather_customer_inputs",
                    "product": "audio_video_calling",
                    "issue_mode": "investigation",
                    "known_information": {"issue_symptom": "black screen issue"},
                    "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                    "ready_for_engineer_ticket": False,
                    "last_updated_at": "2026-04-14T02:08:33.337732+00:00",
                },
                rag_result={
                    "reason": "rag_insufficient_evidence",
                    "answer": "I couldn't find enough information in the available support knowledge base to answer that question.",
                    "evidence_summary": {},
                },
                message_created_at="2026-04-14T02:11:08.752498+00:00",
            )

        self.assertEqual(result.missing_information, [])
        self.assertTrue(result.ready_for_engineer_ticket)
        self.assertEqual(result.known_information["issue_timestamp"], "2026-04-03 12:00pm UTC+8")
        self.assertEqual(
            result.issue_timestamp_parts,
            {"date": "2026-04-03", "time": "12:00pm", "timezone": "UTC+8"},
        )

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

    def test_llm_partial_issue_timestamp_still_requires_missing_timezone(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
            "backend.services.troubleshooting_intake.invoke_responses_text",
            return_value=types.SimpleNamespace(
                text=(
                    '{"issue_mode":"investigation","known_information":{"issue_symptom":"black screen issue",'
                    '"channel_name":"zilingtest","problematic_uid":"1","issue_timestamp":"3/4 at 12pm"},'
                    '"missing_information":[],"ready_for_engineer_ticket":true,"customer_reply":""}'
                )
            ),
        ):
            result = evaluate_troubleshooting_intake(
                message="channel name:zilingtest, uid 1, happened around 3/4 at 12pm",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[
                    {"role": "customer", "content": "i got black screen, what should i do?"},
                    {
                        "role": "assistant",
                        "content": (
                            "Known so far: issue symptom is black screen issue. "
                            "Please share the channel name, problematic uid, and issue timestamp."
                        ),
                    },
                ],
                current_state={
                    "phase": "gather_customer_inputs",
                    "product": "audio_video_calling",
                    "issue_mode": "investigation",
                    "known_information": {"issue_symptom": "black screen issue"},
                    "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                    "ready_for_engineer_ticket": False,
                    "last_updated_at": "2026-03-04T04:00:00+00:00",
                },
                rag_result={
                    "reason": "rag_processing_timeout",
                    "answer": "",
                    "evidence_summary": {},
                },
                message_created_at="2026-03-04T04:00:00+00:00",
            )

        self.assertFalse(result.ready_for_engineer_ticket)
        self.assertEqual(result.missing_information, ["issue_timestamp"])
        self.assertIn("timezone", result.customer_reply.lower())
        self.assertNotIn("date", result.customer_reply.lower())

    def test_llm_cannot_downgrade_black_screen_issue_to_answer_mode(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
            "backend.services.troubleshooting_intake.invoke_responses_text",
            return_value=types.SimpleNamespace(
                text=(
                    '{"issue_mode":"answer","known_information":{"symptom":"black screen"},'
                    '"missing_information":["desired_outcome","blocked_step_or_error"],'
                    '"ready_for_engineer_ticket":false,'
                    '"customer_reply":"What are you trying to achieve? What error or blocker are you seeing?"}'
                )
            ),
        ):
            result = evaluate_troubleshooting_intake(
                message="i got black screen, what should i do?",
                product="audio_video_calling",
                ticket_subject="Black screen issue",
                ticket_context=[{"role": "customer", "content": "i got black screen, what should i do?"}],
                current_state=None,
                rag_result={
                    "reason": "rag_unavailable",
                    "answer": "",
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
