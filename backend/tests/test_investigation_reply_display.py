"""p2-207 contract tests: Investigation reply & display optimization v1.

Covers C3 (Chinese greeting normalization), C4 (sectioned Slack display and
lossless shards), C5 (structured evidence rendering), C6 (partial-failure
recovery on the notification ledger) plus the reverse checks the plan
requires (length rejection, evidence field loss, duplicate sends).
"""
from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from backend.services.investigation_slack_sections import (
    SECTION_ORDER,
    build_investigation_section_messages,
    deliver_investigation_sections,
    render_evidence_item,
)
from backend.services.automation_hermes_delivery import normalize_chinese_greeting


def _sample_record() -> dict[str, Any]:
    return {
        "summary": "Four viewer joins succeeded and the SDK received and decoded video at sampled instants.",
        "evidence": [
            {
                "source": "argus_query_call_events",
                "fact": "Client.join success=1 then createCameraVideoTrack success=0",
                "event_refs": [
                    {"time": 1789908530000, "api": "Client.join", "success": 1},
                    {"time": 1789908531000, "api": "createCameraVideoTrack", "success": 0, "errorCode": "PERMISSION_DENIED"},
                ],
                "query": "sids=B000…; eventIds=1091,20029",
                "limitation": "Renderer series ends earlier than receive samples (missing coverage, not proof of failure)",
            },
            {"source": "argus_query_call_counters", "detail": "recv 3847 kbps, 30 fps at 1789908532000"},
        ],
        "blockers": ["No timestamped device SDK logs provided"],
        "next_steps": ["Request the affected device's SDK logs", "Correlate with a screen recording"],
    }


class TestChineseGreeting(unittest.TestCase):
    def test_canonical_replacement_from_server_identity(self):
        text = "Ziling，您好。\n\n我们已核实四次进房。"
        self.assertEqual(
            normalize_chinese_greeting(text, greeting_name="Ziling"),
            "Ziling您好，\n\n我们已核实四次进房。",
        )

    def test_translation_example_form_is_normalized(self):
        # The exact wrong example from the old translation prompt.
        text = "Ziling, 您好\n\n正文。"
        self.assertTrue(normalize_chinese_greeting(text, greeting_name="Ziling").startswith("Ziling您好，"))

    def test_idempotent_no_duplicate_greeting(self):
        text = "Ziling您好，\n\n我们已核实四次进房。"
        self.assertEqual(normalize_chinese_greeting(text, greeting_name="Ziling"), text)

    def test_english_first_line_untouched(self):
        text = "Hi Ziling,\n\nWe verified four joins."
        self.assertEqual(normalize_chinese_greeting(text, greeting_name="Ziling"), text)

    def test_body_greeting_line_preserved_not_stripped(self):
        # Only the first line is normalized; a greeting-like line deeper in
        # the body is content and stays.
        text = "Ziling，您好。\n\n问候：您好，我们再次确认。"
        normalized = normalize_chinese_greeting(text, greeting_name="Ziling")
        self.assertTrue(normalized.startswith("Ziling您好，"))
        self.assertIn("您好，我们再次确认。", normalized)


class TestEvidenceRendering(unittest.TestCase):
    def test_nested_event_refs_show_api_success_error_code(self):
        rendered = render_evidence_item(_sample_record()["evidence"][0])
        self.assertIn("Client.join", rendered)
        self.assertIn("success: 1", rendered)
        self.assertIn("createCameraVideoTrack", rendered)
        self.assertIn("PERMISSION_DENIED", rendered)
        self.assertIn("argus_query_call_events", rendered)
        self.assertIn("limitation:", rendered)

    def test_never_degrades_to_source_only(self):
        # Reverse check: the OLD renderer showed only the first non-empty of
        # (detail, summary, text, note, source) truncated to 170 chars; a
        # structured entry must show more than just the source.
        rendered = render_evidence_item(_sample_record()["evidence"][0])
        self.assertNotEqual(rendered.strip(), "argus_query_call_events")
        self.assertGreater(len(rendered), 170)

    def test_unknown_structure_renders_full_json(self):
        entry = {"mystery": {"nested": [1, 2, {"k": "v"}]}}
        rendered = render_evidence_item(entry)
        self.assertIn("mystery", rendered)
        self.assertIn("nested", rendered)
        self.assertIn("1", rendered)

    def test_upstream_truncation_is_declared(self):
        entry = {"source": "bridge", "detail": "x" * 50 + "…[truncated 200 chars]"}
        rendered = render_evidence_item(entry)
        self.assertIn("already truncated", rendered)


class TestSectionMessages(unittest.TestCase):
    def test_fixed_section_order_and_labels(self):
        messages = build_investigation_section_messages(header="Hdr — ticket 1", record=_sample_record())
        sections = [m.section for m in messages]
        # summary, evidence, blockers, next_steps in order (evidence may
        # shard but the section sequence is fixed).
        deduped = [s for i, s in enumerate(sections) if i == 0 or sections[i - 1] != s]
        self.assertEqual(deduped, ["summary", "evidence", "blockers", "next_steps"])
        for message in messages:
            self.assertIn("Hdr — ticket 1", message.text)
            self.assertIn({"summary": "Summary", "evidence": "Evidence", "blockers": "Blockers", "next_steps": "Next steps"}[message.section], message.text)

    def test_empty_sections_skipped_no_button_loss(self):
        record = {"summary": "Only a summary.", "evidence": [], "blockers": [], "next_steps": []}
        messages = build_investigation_section_messages(header="H", record=record)
        self.assertEqual([m.section for m in messages], ["summary"])
        # The button lives in the ledger action message, not in a section —
        # empty next steps cannot lose it.

    def test_sharding_is_lossless_and_ordered(self):
        long_summary = "\n\n".join(f"Paragraph {i}: " + "x" * 400 for i in range(20))
        record = {"summary": long_summary}
        messages = build_investigation_section_messages(header="H", record=record)
        self.assertGreater(len(messages), 1)
        bodies = [m.text.split("\n", 1)[1] for m in messages]
        rejoined = "\n".join(bodies)
        for i in range(20):
            self.assertIn(f"Paragraph {i}:", rejoined)
        self.assertTrue(all(f"({m.index}/{m.total})" in m.text for m in messages))

    def test_code_fence_closed_and_reopened_across_shards(self):
        long_code = "```\n" + "\n".join(f"log line {i} " + "y" * 120 for i in range(40)) + "\n```"
        record = {"summary": long_code}
        messages = build_investigation_section_messages(header="H", record=record)
        self.assertGreater(len(messages), 1)
        for message in messages:
            body = message.text.split("\n", 1)[1]
            self.assertEqual(body.count("```") % 2, 0, f"unbalanced fence in shard {message.index}")
        joined = "\n".join(m.text.split("\n", 1)[1] for m in messages)
        # All log lines survive.
        for i in (0, 20, 39):
            self.assertIn(f"log line {i}", joined)

    def test_no_business_truncation_ellipsis(self):
        record = {"summary": "S" * 5000}
        messages = build_investigation_section_messages(header="H", record=record)
        joined = "".join(m.text for m in messages)
        self.assertNotIn("…", joined)
        self.assertGreaterEqual(joined.count("S"), 5000)


class _FakeRepository:
    """Ledger double honoring the enqueue/claim/finish semantics used."""

    def __init__(self, *, fail_second_post=False):
        self.rows: dict[str, dict[str, Any]] = {}
        self.fail_second_post = fail_second_post
        self.posted: list[str] = []

    def enqueue_native_notification(self, *, scope, key, created_at, payload):
        if key not in self.rows:
            self.rows[key] = {"idempotency_key": key, "state": "pending", "response_payload": dict(payload)}
        return self.rows[key]

    def claim_native_notification(self, *, scope, key, claim_token, updated_at):
        row = self.rows.get(key)
        if row is None or row["state"] not in {"pending", "failed"}:
            return None
        row["state"] = "sending"
        row["claim_token"] = claim_token
        return row

    def finish_native_notification(self, *, scope, key, claim_token, state, result, updated_at):
        row = self.rows.get(key)
        if row is None or row.get("claim_token") != claim_token:
            return False
        row["state"] = state
        row["response_payload"]["delivery"] = result
        return True

    def get_native_notification(self, *, scope, key):
        return self.rows.get(key)


def _slack_post_ok(event, thread_ts=None):
    return {"status": "delivered", "slack_channel_id": "C1", "slack_message_ts": "1234.5678"}


class TestLedgerDelivery(unittest.TestCase):
    def _deliver(self, repository, *, action="prepare_draft"):
        return deliver_investigation_sections(
            repository,
            namespace="test-ns",
            thread_ts="111.222",
            channel_id="C1",
            ticket_id="123",
            turn_id="turn-1",
            investigation=_sample_record(),
            environment="preproduction",
            action=action,
            event_type="hermes_investigation_result",
            header="Hermes investigation — Zendesk #123",
            claim_token="claim-1",
        )

    def test_all_sections_confirmed_then_action(self):
        repository = _FakeRepository()
        with patch("backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event",
            side_effect=_slack_post_ok,
        ) as post:
            result = self._deliver(repository)
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["action_sent"])
        action_events = [call.kwargs.get("event", call.args[0] if call.args else {}) for call in post.call_args_list]
        action_event = next(e for e in action_events if e.get("action") == "prepare_draft")
        self.assertIn("All sections delivered", action_event["message_text"])

    def test_second_shard_failure_first_not_resent_button_withheld(self):
        # C6: a failure on the second POST must leave the first shard's
        # receipt untouched; a retry skips the confirmed shard; the action
        # button never fires.
        repository = _FakeRepository()
        calls = {"n": 0}

        def flaky_post(event, thread_ts=None):
            calls["n"] += 1
            if calls["n"] == 2:
                from backend.services.engineer_slack import EngineerSlackDeliveryError

                raise EngineerSlackDeliveryError("engineer_slack_api_ratelimited")
            return _slack_post_ok(event, thread_ts)

        with patch("backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event",
            side_effect=flaky_post,
        ):
            result = self._deliver(repository)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["action_sent"])
        first_key = "investigation:123:turn-1:summary:1"
        self.assertEqual(repository.rows[first_key]["state"], "completed")
        failed_key = result["stopped_key"]
        self.assertEqual(repository.rows[failed_key]["state"], "failed")
        # Retry with a healthy poster: the confirmed shard is skipped and
        # the list completes, button included.
        with patch("backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event",
            side_effect=_slack_post_ok,
        ) as post2:
            retry = self._deliver(repository)
        self.assertEqual(retry["status"], "confirmed")
        self.assertTrue(retry["action_sent"])
        resent = [c.args[0]["event_id"] for c in post2.call_args_list]
        self.assertNotIn(f"test-ns:{first_key}", resent)

    def test_outcome_unknown_stops_list_no_auto_resend_no_button(self):
        from backend.services.engineer_slack import EngineerSlackDeliveryError

        repository = _FakeRepository()
        calls = {"n": 0}

        def unknown_post(event, thread_ts=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise EngineerSlackDeliveryError("native_slack_receipt_invalid", outcome_unknown=True)
            return _slack_post_ok(event, thread_ts)

        with patch("backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event",
            side_effect=unknown_post,
        ) as post:
            result = self._deliver(repository)
        self.assertEqual(result["status"], "outcome_unknown")
        self.assertFalse(result["action_sent"])
        # A retry of the same list must NOT resend the unknown shard.
        with patch("backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event",
            side_effect=_slack_post_ok,
        ) as post2:
            retry = self._deliver(repository)
        self.assertEqual(retry["status"], "outcome_unknown")
        self.assertEqual(post2.call_count, 0)
        self.assertFalse(retry["action_sent"])

    def test_adhoc_variant_sends_no_action(self):
        repository = _FakeRepository()
        with patch("backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event",
            side_effect=_slack_post_ok,
        ) as post:
            result = self._deliver(repository, action=None)
        self.assertEqual(result["status"], "confirmed")
        self.assertFalse(result["action_sent"])
        for call in post.call_args_list:
            event = call.args[0]
            self.assertNotIn("action", event)


class TestSectionOrderConstant(unittest.TestCase):
    def test_order_constant(self):
        self.assertEqual(SECTION_ORDER, ("summary", "evidence", "blockers", "next_steps"))


if __name__ == "__main__":
    unittest.main()
