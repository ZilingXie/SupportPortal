from __future__ import annotations

import base64
import json
import os
import unittest
from unittest.mock import patch

from backend.services.zendesk_comments import ZendeskCommentError
from backend.services.zendesk_ticket_assignment import assign_ticket_to_configured_ai


class _FakeResponse:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.status = status
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class ZendeskTicketAssignmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.basic_auth = base64.b64encode(b"supportportal@example.com:zendesk-api-token").decode("ascii")
        self.config = {
            "zendesk_basic_auth": self.basic_auth,
            "ZENDESK_AI_ASSIGNEE_EMAIL": "ai-support-agent@agora.io",
        }

    def test_updates_only_assignee_and_preserves_ticket_group(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "name": "AI Support", "role": "agent", "active": True, "suspended": False}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 31116634341396, "group_id": 27216254064148, "status": "pending"}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertEqual(result.assignee_id, "48557297720084")
        self.assertEqual(result.group_id, "29388501432596")
        self.assertEqual(result.previous_group_id, "27216254064148")
        self.assertTrue(result.group_changed)
        self.assertFalse(result.already_assigned)
        self.assertEqual(urlopen.call_count, 3)
        identity_request = urlopen.call_args_list[0].args[0]
        self.assertEqual(identity_request.full_url, "https://agoraio.zendesk.com/api/v2/users/me.json")
        update_request = urlopen.call_args_list[2].args[0]
        self.assertEqual(update_request.method, "PUT")
        self.assertEqual(
            json.loads(update_request.data.decode("utf-8")),
            {"ticket": {"assignee_id": 48557297720084}},
        )

    def test_already_assigned_does_not_put(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 48557297720084, "group_id": 27216254064148}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertTrue(result.already_assigned)
        self.assertFalse(result.group_changed)
        self.assertEqual(urlopen.call_count, 2)

    def test_put_response_with_wrong_assignee_fails_closed(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 31116634341396, "group_id": 27216254064148}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 31116634341396, "group_id": 29388501432596}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertEqual(raised.exception.error_code, "zendesk_assignment_unverified")

    def test_invalid_configured_user_fails_closed(self) -> None:
        response = _FakeResponse({"user": {"id": 48557297720084, "email": "other@example.com", "role": "agent", "active": True, "suspended": False}})
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            return_value=response,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertEqual(raised.exception.error_code, "zendesk_assignee_invalid")

    def test_inactive_or_non_agent_user_fails_closed(self) -> None:
        response = _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "end-user", "active": True, "suspended": False}})
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            return_value=response,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertEqual(raised.exception.error_code, "zendesk_assignee_invalid")

    def test_missing_assignee_email_fails_closed(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=True):
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertEqual(raised.exception.error_code, "zendesk_assignee_config_missing")


if __name__ == "__main__":
    unittest.main()
