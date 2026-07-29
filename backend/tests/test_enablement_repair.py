from __future__ import annotations

import unittest
from unittest.mock import Mock

from backend.services.enablement_field_extractor import EnablementFieldExtraction
from backend.services.enablement_repair import repair_enablement_case


class _Repository:
    def __init__(self) -> None:
        self.case = {
            "account_case_id": "AC-12488",
            "client_ticket_id": "12488",
            "subcategory": "enablement",
            "missing_fields": ["app_id"],
            "collected_fields": {
                "requested_feature": "media_relay",
                "requested_feature_label": "media relay",
            },
            "internal_email_send_status": "not_ready",
            "route_classification": {"handler_binding_status": "active"},
        }
        self.ticket = {
            "ticket_id": "12488",
            "subject": "Enable media relay feature",
            "customer_id": "customer@example.com",
            "messages": [{"role": "customer", "content": "my app id is : project.prod/eu-west#alpha"}],
        }
        self.saved: dict[str, object] | None = None

    def get_account_case(self, account_case_id: str) -> dict[str, object] | None:
        return dict(self.case) if account_case_id == "AC-12488" else None

    def get_ticket(self, ticket_id: str) -> dict[str, object] | None:
        return dict(self.ticket) if ticket_id == "12488" else None

    def save_account_case(self, case: dict[str, object]) -> None:
        self.saved = dict(case)


class EnablementRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = _Repository()
        self.extraction = EnablementFieldExtraction(
            status="complete",
            collected_fields={
                "app_id": "project.prod/eu-west#alpha",
                "requested_feature": "media_relay",
                "requested_feature_label": "media relay",
            },
            grounding_status="passed",
        )

    def test_dry_run_does_not_write_or_send(self) -> None:
        sender = Mock()

        result = repair_enablement_case(
            self.repository,
            account_case_id="AC-12488",
            extractor=lambda **_: self.extraction,
            email_sender=sender,
        )

        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["app_id_found"])
        self.assertFalse(result["applied"])
        self.assertIsNone(self.repository.saved)
        sender.assert_not_called()

    def test_apply_updates_fields_without_rewriting_messages_or_sending(self) -> None:
        sender = Mock()

        result = repair_enablement_case(
            self.repository,
            account_case_id="AC-12488",
            apply=True,
            extractor=lambda **_: self.extraction,
            email_sender=sender,
        )

        self.assertTrue(result["applied"])
        assert self.repository.saved is not None
        self.assertEqual(self.repository.saved["missing_fields"], [])
        self.assertEqual(self.repository.saved["collected_fields"]["app_id"], "project.prod/eu-west#alpha")
        self.assertEqual(self.repository.saved["internal_email_send_status"], "pending")
        self.assertEqual(self.repository.ticket["messages"][0]["content"], "my app id is : project.prod/eu-west#alpha")
        sender.assert_not_called()

    def test_explicit_email_send_is_idempotent(self) -> None:
        sender = Mock(return_value={"status": "sent", "reason": ""})

        first = repair_enablement_case(
            self.repository,
            account_case_id="AC-12488",
            apply=True,
            send_email=True,
            extractor=lambda **_: self.extraction,
            email_sender=sender,
        )
        self.repository.case.update(self.repository.saved or {})
        second = repair_enablement_case(
            self.repository,
            account_case_id="AC-12488",
            apply=True,
            send_email=True,
            extractor=lambda **_: self.extraction,
            email_sender=sender,
        )

        self.assertEqual(first["email_status"], "sent")
        self.assertEqual(second["status"], "already_complete")
        sender.assert_called_once()

    def test_complete_case_with_generic_feature_is_reextracted_and_sent(self) -> None:
        self.repository.case.update(
            missing_fields=[],
            collected_fields={
                "app_id": "project.prod/eu-west#alpha",
                "requested_feature": "it",
                "requested_feature_label": "it",
            },
            internal_email_send_status="retry",
        )
        sender = Mock(return_value={"status": "sent", "reason": ""})

        result = repair_enablement_case(
            self.repository,
            account_case_id="AC-12488",
            apply=True,
            send_email=True,
            extractor=lambda **_: self.extraction,
            email_sender=sender,
        )

        self.assertTrue(result["applied"])
        assert self.repository.saved is not None
        self.assertEqual(self.repository.saved["collected_fields"]["requested_feature"], "media_relay")
        self.assertEqual(self.repository.saved["internal_email_send_status"], "sent")
        sender.assert_called_once()


if __name__ == "__main__":
    unittest.main()
