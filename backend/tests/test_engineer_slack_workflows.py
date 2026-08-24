from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
N8N_DIR = ROOT / "docs" / "integrations" / "n8n"


class EngineerSlackWorkflowContractTests(unittest.TestCase):
    def _workflow(self, name: str) -> tuple[dict[str, object], str]:
        raw = (N8N_DIR / name).read_text(encoding="utf-8")
        return json.loads(raw), raw

    def test_all_engineer_workflow_exports_are_redacted_and_inactive(self) -> None:
        names = (
            "SupportPortal_Engineer_Case_Slack_POST.json",
            "SupportPortal_Engineer_Case_Slack_STATUS.json",
            "Slack_App_Mention_To_SupportPortal_Engineer.json",
            "Slack_Interaction_To_SupportPortal_Engineer.json",
        )
        for name in names:
            workflow, raw = self._workflow(name)
            self.assertFalse(workflow["active"], name)
            self.assertIn("REPLACE_WITH_", raw)
            self.assertNotIn("xoxb-", raw)
            self.assertNotIn("xapp-", raw)
            self.assertNotIn("n8n_request_token=", raw)

    def test_outbound_owns_fixed_destination_and_never_accepts_override(self) -> None:
        workflow, raw = self._workflow("SupportPortal_Engineer_Case_Slack_POST.json")
        self.assertIn("REPLACE_WITH_SLACK_TEAM_ID", raw)
        self.assertIn("REPLACE_WITH_SLACK_CHANNEL_ID", raw)
        self.assertIn("destination_override_forbidden", raw)
        self.assertIn("thread_binding_missing", raw)
        self.assertIn("engineer_case_closed", raw)
        self.assertIn("active=FALSE", raw)
        self.assertIn("ON CONFLICT(event_id) DO NOTHING", raw)
        reply_node = next(node for node in workflow["nodes"] if node["name"] == "Reply In Thread")
        reply_parameters = reply_node["parameters"]
        self.assertEqual(reply_parameters["messageType"], "block")
        blocks = reply_parameters["blocksUi"]
        self.assertIn("Run guardrail", blocks)
        self.assertIn("Approve & publish", blocks)
        self.assertIn("action_id:$json.payload.action", blocks)
        self.assertIn("investigation_id:$json.payload.investigation_id", blocks)
        self.assertIn("draft_version:$json.payload.draft_version", blocks)
        for forbidden in ("team_id", "channel_id", "thread_ts"):
            self.assertNotIn(forbidden, blocks)

    def test_status_is_read_only_and_reports_missing(self) -> None:
        _workflow, raw = self._workflow("SupportPortal_Engineer_Case_Slack_STATUS.json")
        self.assertIn("'missing'", raw)
        self.assertNotIn("INSERT INTO", raw)
        self.assertNotIn("UPDATE public", raw)

    def test_app_mention_filters_source_channel_thread_and_event_shape(self) -> None:
        _workflow, raw = self._workflow("Slack_App_Mention_To_SupportPortal_Engineer.json")
        for marker in (
            "invalid_slack_signature",
            "REPLACE_WITH_SLACK_TEAM_ID",
            "REPLACE_WITH_SLACK_CHANNEL_ID",
            "REPLACE_WITH_SLACK_BOT_USER_ID",
            "app_mention",
            "thread_ts",
            "bot_id",
            "subtype",
            "edited",
            "deleted_ts",
            "active=TRUE",
            "ON CONFLICT(inbound_id) DO NOTHING",
            "ACK Claimed Mention",
        ):
            self.assertIn(marker, raw)
        backend_body = raw[raw.index("JSON.stringify({schema_version:1") :]
        for forbidden in ("team_id:", "channel_id:", "thread_ts:"):
            self.assertNotIn(forbidden, backend_body.split("}) }}", 1)[0])

        connections = _workflow["connections"]
        self.assertEqual(
            connections["Claim Bound Mention"]["main"][0][0]["node"],
            "ACK Claimed Mention",
        )
        self.assertEqual(
            connections["ACK Claimed Mention"]["main"][0][0]["node"],
            "Forward Once",
        )

        runbook = (N8N_DIR / "engineer_case_slack_runbook.md").read_text(encoding="utf-8")
        self.assertIn("N8N_BLOCK_ENV_ACCESS_IN_NODE=false", runbook)
        self.assertIn("exact raw request body", runbook)

    def test_interaction_filters_and_forwards_only_versioned_actions(self) -> None:
        workflow, raw = self._workflow("Slack_Interaction_To_SupportPortal_Engineer.json")
        for marker in (
            "invalid_slack_signature",
            "REPLACE_WITH_SLACK_TEAM_ID",
            "REPLACE_WITH_SLACK_CHANNEL_ID",
            "guardrail",
            "final_approve",
            "investigation_id",
            "draft_version",
            "active=TRUE",
            "ON CONFLICT(inbound_id) DO NOTHING",
            "ACK Claimed Interaction",
        ):
            self.assertIn(marker, raw)
        backend_body = raw[raw.index("JSON.stringify({interaction_id:") :]
        for forbidden in ("team_id:", "channel_id:", "thread_ts:"):
            self.assertNotIn(forbidden, backend_body.split("}) }}", 1)[0])
        connections = workflow["connections"]
        self.assertEqual(
            connections["Claim Bound Interaction"]["main"][0][0]["node"],
            "ACK Claimed Interaction",
        )
        self.assertEqual(
            connections["ACK Claimed Interaction"]["main"][0][0]["node"],
            "Forward Once",
        )

    def test_n8n_sql_has_separate_thread_and_inbound_ledgers(self) -> None:
        raw = (N8N_DIR / "n8n_supportportal_engineer_slack.sql").read_text(encoding="utf-8")
        self.assertIn("n8n_supportportal_engineer_slack_events", raw)
        self.assertIn("n8n_supportportal_engineer_slack_threads", raw)
        self.assertIn("n8n_supportportal_engineer_slack_inbound_events", raw)
        self.assertIn("UNIQUE (slack_team_id, slack_channel_id, slack_thread_ts)", raw)


if __name__ == "__main__":
    unittest.main()
