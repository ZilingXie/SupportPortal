from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.repositories.ticket_repository import InMemoryTicketRepository


def _load_script_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "fix_ticket_subject.py"
    spec = importlib.util.spec_from_file_location("fix_ticket_subject_script", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixTicketSubjectCliTests(unittest.TestCase):
    def _repository(self) -> InMemoryTicketRepository:
        repository = InMemoryTicketRepository()
        repository.initialize()
        repository.save_ticket(
            {
                "ticket_id": "TK-079",
                "customer_id": "C-001",
                "requester": "Customer",
                "subject": "Hello, Agora team. We are using the Ban User Privileges API (POST /dev/v1/kicking-rule) to disband c",
                "status": "open",
                "created_at": "2026-04-08T07:14:58.438179+00:00",
                "updated_at": "2026-04-08T07:14:58.438179+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Hello, Agora team. We are using the Ban User Privileges API and the title is too long.",
                        "created_at": "2026-04-08T07:14:58.438179+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "We are checking this now.",
                        "created_at": "2026-04-08T07:15:05.438179+00:00",
                    },
                ],
            },
            new_messages=[],
        )
        repository.save_ticket(
            {
                "ticket_id": "TK-080",
                "customer_id": "C-002",
                "requester": "Customer",
                "subject": "Other ticket subject",
                "status": "open",
                "created_at": "2026-04-08T07:16:00+00:00",
                "updated_at": "2026-04-08T07:16:00+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Other ticket",
                        "created_at": "2026-04-08T07:16:00+00:00",
                    }
                ],
            },
            new_messages=[],
        )
        return repository

    def test_dry_run_reports_change_without_writing(self) -> None:
        module = _load_script_module()
        repository = self._repository()

        with patch.object(module, "create_ticket_repository", return_value=repository), patch.object(
            module,
            "derive_ticket_title",
            return_value="Ban User Privileges API mismatch",
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = module.main(["--ticket-id", "TK-079"])

        self.assertEqual(exit_code, 0)
        payload = stdout.getvalue()
        self.assertIn("dry_run=True", payload)
        self.assertIn("old_subject=", payload)
        self.assertIn("new_subject=Ban User Privileges API mismatch", payload)
        self.assertEqual(
            repository.get_ticket("TK-079")["subject"],
            "Hello, Agora team. We are using the Ban User Privileges API (POST /dev/v1/kicking-rule) to disband c",
        )

    def test_apply_updates_only_target_ticket_subject(self) -> None:
        module = _load_script_module()
        repository = self._repository()
        before_other = repository.get_ticket("TK-080")

        with patch.object(module, "create_ticket_repository", return_value=repository), patch.object(
            module,
            "derive_ticket_title",
            return_value="Ban User Privileges API mismatch",
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = module.main(["--ticket-id", "TK-079", "--apply"])

        self.assertEqual(exit_code, 0)
        self.assertIn("applied=True", stdout.getvalue())
        updated = repository.get_ticket("TK-079")
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated["subject"], "Ban User Privileges API mismatch")
        self.assertEqual(len(updated["messages"]), 2)
        self.assertEqual(repository.get_ticket("TK-080"), before_other)

    def test_main_fails_when_ticket_does_not_exist(self) -> None:
        module = _load_script_module()
        repository = InMemoryTicketRepository()
        repository.initialize()

        with patch.object(module, "create_ticket_repository", return_value=repository):
            with self.assertRaisesRegex(SystemExit, "Ticket not found: TK-404"):
                module.main(["--ticket-id", "TK-404"])

    def test_main_fails_when_customer_message_is_missing(self) -> None:
        module = _load_script_module()
        repository = InMemoryTicketRepository()
        repository.initialize()
        repository.save_ticket(
            {
                "ticket_id": "TK-081",
                "customer_id": "C-003",
                "requester": "Customer",
                "subject": "No customer message",
                "status": "open",
                "created_at": "2026-04-08T07:20:00+00:00",
                "updated_at": "2026-04-08T07:20:00+00:00",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Waiting for customer input.",
                        "created_at": "2026-04-08T07:20:05+00:00",
                    }
                ],
            },
            new_messages=[],
        )

        with patch.object(module, "create_ticket_repository", return_value=repository):
            with self.assertRaisesRegex(SystemExit, "No customer message found for ticket TK-081"):
                module.main(["--ticket-id", "TK-081"])


if __name__ == "__main__":
    unittest.main()
