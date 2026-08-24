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

    def test_only_inbound_engineer_workflows_remain_redacted_and_inactive(self) -> None:
        names = (
            "Slack_App_Mention_To_SupportPortal_Engineer.json",
            "Slack_Interaction_To_SupportPortal_Engineer.json",
        )
        for name in names:
            workflow, raw = self._workflow(name)
            self.assertFalse(workflow["active"], name)
            self.assertIn("REPLACE_WITH_", raw)
            self.assertNotIn("xoxb-", raw)
            self.assertNotIn("xoxp-", raw)
            self.assertNotIn("n8n_request_token=", raw)

        self.assertFalse((N8N_DIR / "SupportPortal_Engineer_Case_Slack_POST.json").exists())
        self.assertFalse((N8N_DIR / "SupportPortal_Engineer_Case_Slack_STATUS.json").exists())

    def test_app_mention_filters_resolves_claims_acks_then_forwards(self) -> None:
        workflow, raw = self._workflow("Slack_App_Mention_To_SupportPortal_Engineer.json")
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
            "/thread-bindings/resolve",
            "ON CONFLICT(inbound_id) DO NOTHING",
            "ACK Claimed Mention",
        ):
            self.assertIn(marker, raw)
        self.assertNotIn("n8n_supportportal_engineer_slack_threads", raw)

        resolver = next(node for node in workflow["nodes"] if node["name"] == "Resolve Active Binding")
        self.assertEqual(resolver["parameters"]["authentication"], "genericCredentialType")
        self.assertEqual(
            {item["name"] for item in resolver["parameters"]["queryParameters"]["parameters"]},
            {"team_id", "channel_id", "thread_ts"},
        )
        rejected_ack = next(node for node in workflow["nodes"] if node["name"] == "ACK Rejected Mention")
        self.assertIn("$json.ack || {ok:true}", rejected_ack["parameters"]["responseBody"])

        backend_body = raw[raw.index("JSON.stringify({schema_version:1") :]
        for forbidden in ("team_id:", "channel_id:", "thread_ts:"):
            self.assertNotIn(forbidden, backend_body.split("}) }}", 1)[0])

        connections = workflow["connections"]
        self.assertEqual(
            connections["Eligible Mention"]["main"][0][0]["node"],
            "Resolve Active Binding",
        )
        self.assertEqual(
            connections["Bound Case Thread"]["main"][0][0]["node"],
            "Claim Bound Mention",
        )
        self.assertEqual(
            connections["Bound Case Thread"]["main"][1][0]["node"],
            "ACK Rejected Mention",
        )
        self.assertEqual(
            connections["Claim Bound Mention"]["main"][0][0]["node"],
            "ACK Claimed Mention",
        )
        self.assertEqual(
            connections["ACK Claimed Mention"]["main"][0][0]["node"],
            "Forward Once",
        )

    def test_interaction_filters_resolves_and_forwards_versioned_action(self) -> None:
        workflow, raw = self._workflow("Slack_Interaction_To_SupportPortal_Engineer.json")
        for marker in (
            "invalid_slack_signature",
            "REPLACE_WITH_SLACK_TEAM_ID",
            "REPLACE_WITH_SLACK_CHANNEL_ID",
            "guardrail",
            "final_approve",
            "investigation_id",
            "draft_version",
            "/thread-bindings/resolve",
            "ON CONFLICT(inbound_id) DO NOTHING",
            "ACK Claimed Interaction",
        ):
            self.assertIn(marker, raw)
        self.assertNotIn("n8n_supportportal_engineer_slack_threads", raw)

        backend_body = raw[raw.index("JSON.stringify({interaction_id:") :]
        for forbidden in ("team_id:", "channel_id:", "thread_ts:"):
            self.assertNotIn(forbidden, backend_body.split("}) }}", 1)[0])
        connections = workflow["connections"]
        self.assertEqual(
            connections["Eligible Interaction"]["main"][0][0]["node"],
            "Resolve Active Binding",
        )
        self.assertEqual(
            connections["Bound Case Thread"]["main"][0][0]["node"],
            "Claim Bound Interaction",
        )
        self.assertEqual(
            connections["Bound Case Thread"]["main"][1][0]["node"],
            "ACK Rejected Interaction",
        )
        self.assertEqual(
            connections["Claim Bound Interaction"]["main"][0][0]["node"],
            "ACK Claimed Interaction",
        )

    def test_n8n_sql_has_only_inbound_ledger(self) -> None:
        raw = (N8N_DIR / "n8n_supportportal_engineer_slack.sql").read_text(encoding="utf-8")
        self.assertIn("n8n_supportportal_engineer_slack_inbound_events", raw)
        self.assertNotIn("n8n_supportportal_engineer_slack_events", raw)
        self.assertNotIn("n8n_supportportal_engineer_slack_threads", raw)

    def test_runbook_assigns_outbound_to_supportportal(self) -> None:
        raw = (N8N_DIR / "engineer_case_slack_runbook.md").read_text(encoding="utf-8")
        self.assertIn("PRODUCTION_ENGINEER_SLACK_ACCESS_TOKEN", raw)
        self.assertIn("N8N_BLOCK_ENV_ACCESS_IN_NODE=false", raw)
        self.assertIn("exact raw request body", raw)
        self.assertIn("never automatically replayed", raw)
        self.assertNotIn("PRODUCTION_ENGINEER_SLACK_N8N_WEBHOOK_URL", raw)

    def test_production_runtime_uses_direct_slack_configuration(self) -> None:
        compose = (ROOT / "deployment" / "docker-compose.single-host.yml").read_text(
            encoding="utf-8"
        )
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for variable in (
            "PRODUCTION_ENGINEER_SLACK_ACCESS_TOKEN",
            "PRODUCTION_ENGINEER_SLACK_TEAM_ID",
            "PRODUCTION_ENGINEER_SLACK_CHANNEL_ID",
            "PRODUCTION_ENGINEER_SLACK_TIMEOUT_SECONDS",
        ):
            self.assertIn(variable, compose)
            self.assertIn(variable, env_example)
        self.assertNotIn("PRODUCTION_ENGINEER_SLACK_N8N_WEBHOOK_URL", compose)
        self.assertNotIn("PRODUCTION_ENGINEER_SLACK_N8N_STATUS_URL", compose)

        migration = (
            ROOT
            / "backend"
            / "sql"
            / "migrations"
            / "2026_08_24_engineer_slack_direct_outbound.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS slack_thread_ts TEXT", migration)
        self.assertIn("idx_support_engineer_slack_events_root_thread", migration)


if __name__ == "__main__":
    unittest.main()
