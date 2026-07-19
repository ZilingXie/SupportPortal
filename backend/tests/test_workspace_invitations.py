from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta, timezone

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.workspace_invitations import WorkspaceInvitationService


class WorkspaceInvitationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.now = datetime(2026, 7, 19, 4, 0, tzinfo=timezone.utc)
        self.sent: dict[str, str] = {}
        self.service = WorkspaceInvitationService(
            self.repository,
            mail_sender=lambda **kwargs: self.sent.update(kwargs),
            now_provider=lambda: self.now,
        )

    def _create_and_extract_token(self, *, role: str = "engineer") -> str:
        invitation = self.service.create(
            email="New.Engineer@Example.com",
            role=role,
            created_by="admin-1",
        )
        self.assertEqual(invitation["email"], "new.engineer@example.com")
        match = re.search(r"[?&]token=([^\s]+)", self.sent["body"])
        self.assertIsNotNone(match)
        assert match is not None
        self.assertNotIn(match.group(1), str(self.repository._workspace_invitations))
        return match.group(1)

    def test_role_is_frozen_and_token_can_only_be_used_once(self) -> None:
        raw_token = self._create_and_extract_token(role="admin")

        inspected = self.service.inspect(raw_token)
        account = self.service.complete(
            raw_token=raw_token,
            display_name="New Admin",
            password="new-admin-password",
        )

        self.assertEqual(inspected["role"], "admin")
        self.assertEqual(account["role"], "admin")
        self.assertEqual(account["account_id"], "new.engineer@example.com")
        self.assertEqual(account["email"], "new.engineer@example.com")
        with self.assertRaisesRegex(ValueError, "invitation unavailable"):
            self.service.complete(
                raw_token=raw_token,
                display_name="Another Admin",
                password="another-password",
            )

    def test_expired_invitation_is_unavailable(self) -> None:
        raw_token = self._create_and_extract_token()
        self.now += timedelta(hours=25)

        with self.assertRaisesRegex(ValueError, "invitation unavailable"):
            self.service.inspect(raw_token)

    def test_delivery_failure_is_recorded_and_retry_is_allowed(self) -> None:
        failed_service = WorkspaceInvitationService(
            self.repository,
            mail_sender=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("mail unavailable")),
            now_provider=lambda: self.now,
        )
        with self.assertRaisesRegex(RuntimeError, "could not be sent"):
            failed_service.create(
                email="retry@example.com",
                role="engineer",
                created_by="admin-1",
            )

        retry = WorkspaceInvitationService(
            self.repository,
            mail_sender=lambda **kwargs: self.sent.update(kwargs),
            now_provider=lambda: self.now,
        ).create(email="retry@example.com", role="engineer", created_by="admin-1")

        self.assertEqual(retry["delivery_status"], "sent")
        events = self.repository.list_workspace_audit_events()
        self.assertTrue(any(event["event_type"] == "workspace_invitation_delivery_failed" for event in events))

    def test_duplicate_active_invitation_is_rejected(self) -> None:
        self._create_and_extract_token()

        with self.assertRaisesRegex(ValueError, "active workspace invitation"):
            self.service.create(
                email="new.engineer@example.com",
                role="engineer",
                created_by="admin-1",
            )


if __name__ == "__main__":
    unittest.main()
