from __future__ import annotations

import base64
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from backend.services.zendesk_comments import ZendeskCommentError
from backend.services.zendesk_ticket_assignment import (
    assign_ticket_to_configured_ai,
    assign_ticket_to_reviewer,
    read_ticket_ownership_snapshot,
    route_ticket_back_to_queue,
)


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


def _conflict_error() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://agoraio.zendesk.com/api/v2/tickets/12899.json",
        409,
        "Conflict",
        None,
        io.BytesIO(b'{"error":"Conflict"}'),
    )


class ZendeskTicketAssignmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.basic_auth = base64.b64encode(b"supportportal@example.com:zendesk-api-token").decode("ascii")
        self.config = {
            "zendesk_basic_auth": self.basic_auth,
            "ZENDESK_AI_ASSIGNEE_EMAIL": "ai-support-agent@agora.io",
        }

    def test_updates_only_assignee_and_preserves_ticket_group(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "name": "AI Support", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 31116634341396, "group_id": 27216254064148, "status": "pending", "updated_at": "2026-08-20T07:03:44Z"}}),
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
            {
                "ticket": {
                    "assignee_id": 48557297720084,
                    "group_id": 29388501432596,
                    "safe_update": True,
                    "updated_stamp": "2026-08-20T07:03:44Z",
                    "custom_fields": [{"id": 31503099534100, "value": "video_calling"}],
                }
            },
        )

    def test_assignment_fills_required_field_only_when_ticket_value_is_empty(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 31116634341396, "group_id": 27216254064148, "updated_at": "2026-08-20T07:03:44Z", "custom_fields": [{"id": 31503099534100, "value": "voice_calling"}]}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 48557297720084, "group_id": 29388501432596}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            assign_ticket_to_configured_ai(ticket_id="12807")

        update_request = urlopen.call_args_list[2].args[0]
        self.assertEqual(
            json.loads(update_request.data.decode("utf-8")),
            {
                "ticket": {
                    "assignee_id": 48557297720084,
                    "group_id": 29388501432596,
                    "safe_update": True,
                    "updated_stamp": "2026-08-20T07:03:44Z",
                }
            },
        )

    def test_ownership_snapshot_reports_required_field_state(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12875, "assignee_id": 31116634341396, "group_id": 27216254064148, "updated_at": "2026-08-20T07:05:37Z", "custom_fields": [{"id": 31503099534100, "value": "video_calling"}]}}),
            _FakeResponse({"comments": [], "users": [], "next_page": None}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ):
            snapshot = read_ticket_ownership_snapshot(ticket_id="12875")

        self.assertFalse(snapshot.required_field_missing)
        self.assertTrue(snapshot.comments_revision)

    def test_already_assigned_does_not_put(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 48557297720084, "group_id": 29388501432596}}),
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
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 31116634341396, "group_id": 27216254064148, "updated_at": "2026-08-20T07:03:44Z"}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 31116634341396, "group_id": 29388501432596}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertEqual(raised.exception.error_code, "zendesk_assignment_unverified")

    def test_http_error_body_is_captured_as_detail(self) -> None:
        import io
        import urllib.error

        routing_rejection = urllib.error.HTTPError(
            "https://agoraio.zendesk.com/api/v2/tickets/12807.json",
            422,
            "Unprocessable Entity",
            None,
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "title": "RecordInvalid",
                            "message": "Assignee cannot be set while the ticket is being routed",
                        },
                        "description": "RecordInvalid",
                    }
                ).encode("utf-8")
            ),
        )
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 31116634341396, "group_id": 27216254064148, "updated_at": "2026-08-20T07:03:44Z"}}),
            routing_rejection,
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.error_code, "zendesk_http_error")
        self.assertEqual(
            raised.exception.detail,
            "Assignee cannot be set while the ticket is being routed",
        )

    def test_http_error_body_details_are_preserved_in_detail(self) -> None:
        import io
        import urllib.error

        routing_rejection = urllib.error.HTTPError(
            "https://agoraio.zendesk.com/api/v2/tickets/12807.json",
            422,
            "Unprocessable Entity",
            None,
            io.BytesIO(
                json.dumps(
                    {
                        "error": "RecordInvalid",
                        "description": "Record validation errors",
                        "details": {
                            "assignee_id": [
                                {
                                    "description": "Assignee cannot be set while the ticket is being routed",
                                    "error": "InvalidValue",
                                }
                            ]
                        },
                    }
                ).encode("utf-8")
            ),
        )
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12807, "assignee_id": 31116634336660, "group_id": 27216254064148, "updated_at": "2026-08-20T07:03:44Z"}}),
            routing_rejection,
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.error_code, "zendesk_http_error")
        self.assertEqual(
            raised.exception.detail,
            'RecordInvalid | {"assignee_id":[{"description":"Assignee cannot be set while the ticket is being routed","error":"InvalidValue"}]}',
        )

    def test_invalid_configured_user_fails_closed(self) -> None:
        response = _FakeResponse({"user": {"id": 48557297720084, "email": "other@example.com", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}})
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            return_value=response,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_configured_ai(ticket_id="12807")

        self.assertEqual(raised.exception.error_code, "zendesk_assignee_invalid")

    def test_inactive_or_non_agent_user_fails_closed(self) -> None:
        response = _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "end-user", "active": True, "suspended": False, "default_group_id": 29388501432596}})
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

    def test_ownership_snapshot_reads_all_comment_pages_and_finds_human_reply(self) -> None:
        second_page = "https://agoraio.zendesk.com/api/v2/tickets/12875/comments.json?page=2"
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "name": "AI Support", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12875, "assignee_id": 31116634341396, "group_id": 27216254064148, "updated_at": "2026-08-20T07:05:37Z"}}),
            _FakeResponse({
                "comments": [
                    {"id": 1, "public": True, "author_id": 100, "body": "Initial", "created_at": "2026-08-20T07:03:44Z"},
                    {"id": 2, "public": False, "author_id": 200, "body": "Internal", "created_at": "2026-08-20T07:04:00Z"},
                ],
                "users": [
                    {"id": 100, "name": "Customer", "role": "end-user"},
                    {"id": 200, "name": "Engineer", "role": "agent"},
                ],
                "next_page": second_page,
            }),
            _FakeResponse({
                "comments": [
                    {"id": 3, "public": True, "author_id": 300, "body": "Public reply", "created_at": "2026-08-20T07:06:00Z"},
                ],
                "users": [{"id": 300, "name": "Admin", "role": "admin"}],
                "next_page": None,
            }),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            snapshot = read_ticket_ownership_snapshot(ticket_id="12875")

        self.assertEqual(urlopen.call_count, 4)
        self.assertTrue(snapshot.human_replied)
        self.assertEqual(snapshot.blocking_comment_id, "3")
        self.assertIsNone(snapshot.unresolved_public_comment_id)
        self.assertTrue(snapshot.required_field_missing)

    def test_ownership_snapshot_marks_unknown_public_author_unresolved(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12875, "assignee_id": 31116634341396, "group_id": 27216254064148, "updated_at": "2026-08-20T07:05:37Z"}}),
            _FakeResponse({
                "comments": [
                    {"id": 1, "public": True, "author_id": 100, "body": "Initial", "created_at": "2026-08-20T07:03:44Z"},
                    {"id": 2, "public": True, "author_id": 999, "body": "Unknown", "created_at": "2026-08-20T07:06:00Z"},
                ],
                "users": [{"id": 100, "name": "Customer", "role": "end-user"}],
                "next_page": None,
            }),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ):
            snapshot = read_ticket_ownership_snapshot(ticket_id="12875")

        self.assertFalse(snapshot.human_replied)
        self.assertEqual(snapshot.unresolved_public_comment_id, "2")

    def test_customer_follow_up_and_ai_reply_do_not_count_as_human_takeover(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "name": "AI Support", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12875, "assignee_id": 48557297720084, "group_id": 29388501432596, "updated_at": "2026-08-20T07:05:37Z"}}),
            _FakeResponse({
                "comments": [
                    {"id": 1, "public": True, "author_id": 100, "body": "Initial", "created_at": "2026-08-20T07:03:44Z"},
                    {"id": 2, "public": True, "author_id": 100, "body": "Customer follow-up", "created_at": "2026-08-20T07:04:00Z"},
                    {"id": 3, "public": True, "author_id": 48557297720084, "body": "AI reply", "created_at": "2026-08-20T07:05:00Z"},
                ],
                "users": [{"id": 100, "name": "Customer", "role": "end-user"}],
                "next_page": None,
            }),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ):
            snapshot = read_ticket_ownership_snapshot(ticket_id="12875")

        self.assertFalse(snapshot.human_replied)
        self.assertIsNone(snapshot.blocking_comment_id)
        self.assertIsNone(snapshot.unresolved_public_comment_id)


class ZendeskReviewerAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.basic_auth = base64.b64encode(b"supportportal@example.com:zendesk-api-token").decode("ascii")
        self.config = {
            "zendesk_basic_auth": self.basic_auth,
            "ZENDESK_AI_ASSIGNEE_EMAIL": "ai-support-agent@agora.io",
        }
        self.reviewer = {
            "id": 31116634341396,
            "email": "xieziling@agora.io",
            "name": "Xie Ziling",
            "role": "agent",
            "active": True,
            "suspended": False,
            "default_group_id": 27216254064148,
        }

    def test_assigns_ticket_to_reviewer_and_default_group(self) -> None:
        responses = [
            _FakeResponse({"user": self.reviewer}),
            _FakeResponse({"ticket": {"id": 12895, "assignee_id": 48557297720084, "group_id": 29388501432596, "updated_at": "2026-08-21T03:12:05Z", "custom_fields": [{"id": 31503099534100, "value": "video_calling"}]}}),
            _FakeResponse({"ticket": {"id": 12895, "assignee_id": 31116634341396, "group_id": 27216254064148}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = assign_ticket_to_reviewer(
                ticket_id="12895",
                reviewer_user_id="31116634341396",
            )

        self.assertFalse(result.already_assigned)
        self.assertEqual(result.assignee_id, "31116634341396")
        self.assertEqual(result.group_id, "27216254064148")
        self.assertTrue(result.group_changed)
        update_request = urlopen.call_args_list[2].args[0]
        self.assertEqual(update_request.method, "PUT")
        self.assertEqual(
            json.loads(update_request.data.decode("utf-8")),
            {
                "ticket": {
                    "assignee_id": 31116634341396,
                    "group_id": 27216254064148,
                    "safe_update": True,
                    "updated_stamp": "2026-08-21T03:12:05Z",
                }
            },
        )

    def test_reviewer_already_assigned_does_not_put(self) -> None:
        responses = [
            _FakeResponse({"user": self.reviewer}),
            _FakeResponse({"ticket": {"id": 12895, "assignee_id": 31116634341396, "group_id": 27216254064148, "updated_at": "2026-08-21T03:20:00Z"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = assign_ticket_to_reviewer(
                ticket_id="12895",
                reviewer_user_id="31116634341396",
            )

        self.assertTrue(result.already_assigned)
        self.assertEqual(urlopen.call_count, 2)

    def test_non_numeric_reviewer_id_fails_closed(self) -> None:
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
        ) as urlopen:
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_reviewer(
                    ticket_id="12895",
                    reviewer_user_id="not-a-number",
                )

        self.assertEqual(raised.exception.error_code, "zendesk_assignment_input_invalid")
        urlopen.assert_not_called()

    def test_inactive_reviewer_fails_closed(self) -> None:
        inactive = dict(self.reviewer, active=False)
        responses = [_FakeResponse({"user": inactive})]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                assign_ticket_to_reviewer(
                    ticket_id="12895",
                    reviewer_user_id="31116634341396",
                )

        self.assertEqual(raised.exception.error_code, "zendesk_reviewer_invalid")

    def test_route_back_releases_ai_to_saved_source_group_and_reads_back(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:00Z"}}),
            _FakeResponse({"ticket": {"id": 12899}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": None, "group_id": 27216253642772, "status": "open"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = route_ticket_back_to_queue(
                ticket_id="12899", source_group_id="27216253642772"
            )

        self.assertEqual(result.status, "queued")
        self.assertTrue(result.updated)
        update_request = urlopen.call_args_list[2].args[0]
        self.assertEqual(update_request.method, "PUT")
        self.assertEqual(
            json.loads(update_request.data.decode("utf-8"))["ticket"],
            {
                "assignee_id": None,
                "group_id": 27216253642772,
                "status": "open",
                "custom_status_id": 26895324619412,
                "additional_tags": ["auto_route", "supportportal_human_fallback"],
                "safe_update": True,
                "updated_stamp": "2026-08-21T07:00:00Z",
                "custom_fields": [{"id": 31503099534100, "value": "video_calling"}],
            },
        )

    def test_route_back_conflict_accepts_concurrent_human_assignment(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:00Z"}}),
            _conflict_error(),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 31116634341396, "group_id": 27216253642772, "status": "open", "updated_at": "2026-08-21T07:00:01Z"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = route_ticket_back_to_queue(
                ticket_id="12899", source_group_id="27216253642772"
            )

        self.assertEqual(result.status, "already_human_owned")
        self.assertFalse(result.updated)
        self.assertEqual(
            sum(call.args[0].method == "PUT" for call in urlopen.call_args_list), 1
        )

    def test_route_back_conflict_accepts_concurrent_queue_release(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:00Z"}}),
            _conflict_error(),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": None, "group_id": 27216253642772, "status": "open", "updated_at": "2026-08-21T07:00:01Z"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = route_ticket_back_to_queue(
                ticket_id="12899", source_group_id="27216253642772"
            )

        self.assertEqual(result.status, "queued")
        self.assertFalse(result.updated)
        self.assertEqual(
            sum(call.args[0].method == "PUT" for call in urlopen.call_args_list), 1
        )

    def test_route_back_conflict_retries_once_with_fresh_stamp(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:00Z"}}),
            _conflict_error(),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:01Z", "custom_fields": [{"id": 31503099534100, "value": "voice_calling"}]}}),
            _FakeResponse({"ticket": {"id": 12899}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": None, "group_id": 27216253642772, "status": "open"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = route_ticket_back_to_queue(
                ticket_id="12899", source_group_id="27216253642772"
            )

        self.assertEqual(result.status, "queued")
        put_requests = [
            call.args[0] for call in urlopen.call_args_list if call.args[0].method == "PUT"
        ]
        self.assertEqual(len(put_requests), 2)
        retry_payload = json.loads(put_requests[1].data.decode("utf-8"))["ticket"]
        self.assertEqual(retry_payload["updated_stamp"], "2026-08-21T07:00:01Z")
        self.assertNotIn("custom_fields", retry_payload)

    def test_route_back_conflict_fails_closed_when_ticket_was_closed(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:00Z"}}),
            _conflict_error(),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "solved", "updated_at": "2026-08-21T07:00:01Z"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            with self.assertRaises(ZendeskCommentError) as raised:
                route_ticket_back_to_queue(
                    ticket_id="12899", source_group_id="27216253642772"
                )

        self.assertEqual(raised.exception.error_code, "zendesk_ticket_closed")
        self.assertEqual(
            sum(call.args[0].method == "PUT" for call in urlopen.call_args_list), 1
        )

    def test_route_back_second_conflict_fails_without_third_put(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:00Z"}}),
            _conflict_error(),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:01Z"}}),
            _conflict_error(),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            with self.assertRaises(ZendeskCommentError) as raised:
                route_ticket_back_to_queue(
                    ticket_id="12899", source_group_id="27216253642772"
                )

        self.assertEqual(raised.exception.error_code, "zendesk_update_conflict")
        self.assertEqual(
            sum(call.args[0].method == "PUT" for call in urlopen.call_args_list), 2
        )

    def test_route_back_never_clears_existing_human_assignment(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 31116634341396, "group_id": 27216253642772, "status": "open", "updated_at": "2026-08-21T07:00:00Z"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = route_ticket_back_to_queue(ticket_id="12899")

        self.assertEqual(result.status, "already_human_owned")
        self.assertFalse(result.updated)
        self.assertEqual(urlopen.call_count, 2)

    def test_route_back_already_queued_does_not_put(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": None, "group_id": 27216253642772, "status": "open", "updated_at": "2026-08-21T07:00:00Z"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = route_ticket_back_to_queue(ticket_id="12899")

        self.assertEqual(result.status, "queued")
        self.assertFalse(result.updated)
        self.assertEqual(urlopen.call_count, 2)

    def test_route_back_recovers_source_group_from_assignment_audit(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:00Z", "custom_fields": [{"id": 31503099534100, "value": "voice_calling"}]}}),
            _FakeResponse({"audits": [{"id": 700, "events": [{"field_name": "group_id", "previous_value": "27216254064148", "value": "29388501432596"}]}], "next_page": None}),
            _FakeResponse({"ticket": {"id": 12899}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 40430228336660, "group_id": 27216254064148, "status": "open"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ):
            result = route_ticket_back_to_queue(ticket_id="12899")

        self.assertEqual(result.status, "assigned")
        self.assertEqual(result.source_group_id, "27216254064148")

    def test_route_back_outcome_unknown_reconciles_with_get_without_second_put(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:00Z"}}),
            urllib.error.URLError("connection reset"),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": None, "group_id": 27216253642772, "status": "open"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            result = route_ticket_back_to_queue(
                ticket_id="12899", source_group_id="27216253642772"
            )

        self.assertEqual(result.status, "queued")
        self.assertEqual(
            sum(call.args[0].method == "PUT" for call in urlopen.call_args_list), 1
        )

    def test_route_back_rejects_closed_ticket(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "closed", "updated_at": "2026-08-21T07:00:00Z"}}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            with self.assertRaises(ZendeskCommentError) as raised:
                route_ticket_back_to_queue(ticket_id="12899")

        self.assertEqual(raised.exception.error_code, "zendesk_ticket_closed")
        self.assertEqual(urlopen.call_count, 2)

    def test_route_back_fails_closed_when_no_prior_human_group_is_proven(self) -> None:
        responses = [
            _FakeResponse({"user": {"id": 48557297720084, "email": "ai-support-agent@agora.io", "role": "agent", "active": True, "suspended": False, "default_group_id": 29388501432596}}),
            _FakeResponse({"ticket": {"id": 12899, "assignee_id": 48557297720084, "group_id": 29388501432596, "status": "pending", "updated_at": "2026-08-21T07:00:00Z"}}),
            _FakeResponse({"audits": [], "next_page": None}),
        ]
        with patch.dict(os.environ, self.config, clear=False), patch(
            "backend.services.zendesk_ticket_assignment.urllib.request.urlopen",
            side_effect=responses,
        ) as urlopen:
            with self.assertRaises(ZendeskCommentError) as raised:
                route_ticket_back_to_queue(ticket_id="12899")

        self.assertEqual(raised.exception.error_code, "zendesk_source_group_unavailable")
        self.assertEqual(
            sum(call.args[0].method == "PUT" for call in urlopen.call_args_list), 0
        )


if __name__ == "__main__":
    unittest.main()
