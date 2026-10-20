from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.automation_test_scenarios import ScenarioEngine
from scripts.testing.preproduction import __main__ as cli


class _FakePreproductionEngine(ScenarioEngine):
    def __init__(self, *, processing_profile: str = "preproduction", db_schema: str = "supportportal_preproduction"):
        super().__init__(
            smtp_host="smtp.test",
            smtp_port=465,
            sender="xieziling97@163.com",
            smtp_password="pw",
            imap_host="imap.test",
            imap_port=993,
            db_dsn="postgresql://example.invalid/test",
            db_schema=db_schema,
            processing_profile=processing_profile,
            poll_interval_seconds=0,
        )
        self.sent = []

    def send_email(self, subject, body, to_address, headers=None):
        self.sent.append({"subject": subject, "body": body, "to": to_address})

    def wait_for(self, description, probe, timeout_seconds):
        return probe()

    def db_query(self, sql, params):
        self.assert_query = (sql, params)
        return [{
            "account_case_id": "AC-TEST-1",
            "client_ticket_id": "14130",
            "zendesk_ticket_id": "14130",
            "title": "[zac test] investigation intake",
        }]


class PreproductionTicketCreationTests(unittest.TestCase):
    def test_cli_create_ticket_path_uses_the_same_helper(self):
        engine = _FakePreproductionEngine()
        with (
            patch.object(cli, "load_env_into_process"),
            patch.object(cli, "_ensure_preprod_db_env"),
            patch.object(cli, "_build_listener", return_value=None),
            patch.object(ScenarioEngine, "from_env", return_value=engine),
            patch("sys.argv", [
                "preproduction",
                "--create-ticket",
                "--ticket-subject", "investigation intake",
                "--ticket-body", "Please investigate this test issue.",
                "--yes",
            ]),
        ):
            self.assertEqual(cli.main(), 0)

        self.assertEqual(len(engine.sent), 1)
        self.assertEqual(engine.sent[0]["subject"], "[zac test] investigation intake")

    def test_create_ticket_reuses_e3_smtp_start_ticket_contract(self):
        engine = _FakePreproductionEngine()

        report = cli._create_preproduction_ticket(
            engine,
            subject="investigation intake",
            body="Please investigate this test issue.",
        )

        self.assertEqual(report["transport"], "e3_smtp")
        self.assertEqual(report["processing_profile"], "preproduction")
        self.assertEqual(report["db_schema"], "supportportal_preproduction")
        self.assertEqual(report["zendesk_ticket_id"], "14130")
        self.assertEqual(len(engine.sent), 1)
        self.assertEqual(engine.sent[0]["to"], "support@agoraio.zendesk.com")
        self.assertEqual(engine.sent[0]["subject"], "[zac test] investigation intake")
        self.assertEqual(engine.sent[0]["body"], "Please investigate this test issue.")

    def test_create_ticket_rejects_non_preproduction_engine_before_send(self):
        engine = _FakePreproductionEngine(processing_profile="production")

        with self.assertRaisesRegex(RuntimeError, "processing_profile=preproduction"):
            cli._create_preproduction_ticket(engine, subject="subject", body="body")

        self.assertEqual(engine.sent, [])

    def test_create_ticket_rejects_non_preproduction_schema_before_send(self):
        engine = _FakePreproductionEngine(db_schema="supportportal")

        with self.assertRaisesRegex(RuntimeError, "TICKET_DB_SCHEMA=supportportal_preproduction"):
            cli._create_preproduction_ticket(engine, subject="subject", body="body")

        self.assertEqual(engine.sent, [])


if __name__ == "__main__":
    unittest.main()
