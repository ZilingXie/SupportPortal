from __future__ import annotations

import base64
import json
import os
import unittest
from unittest.mock import patch

from backend.services.zendesk_comments import (
    ZendeskCommentError,
    ZendeskCommentResult,
    add_ticket_comment,
    get_ticket_status,
    read_ticket_comment_audit,
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


class ZendeskPublicCommentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.basic_auth = base64.b64encode(
            b"supportportal@example.com:zendesk-api-token"
        ).decode("ascii")

    def test_public_write_uses_public_comment_payload_and_verifies_visibility(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "audit": {
                        "events": [
                            {
                                "body": "public reply body",
                                "id": 52660001,
                                "public": True,
                                "type": "Comment",
                            }
                        ]
                    },
                    "ticket": {"id": 12838, "status": "open"},
                }
            ),
        ) as urlopen:
            result = add_ticket_comment(ticket_id="12838", body="public reply body", public=True)

        self.assertEqual(result.comment_id, "52660001")
        self.assertEqual(result.ticket_status, "open")
        request = urlopen.call_args.args[0]
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"ticket": {"comment": {"body": "public reply body", "public": True}}},
        )

    def test_solve_write_uses_single_put_with_status_solved(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "audit": {
                        "events": [
                            {
                                "body": "closing reply",
                                "id": 52660002,
                                "public": True,
                                "type": "Comment",
                            }
                        ]
                    },
                    "ticket": {"id": 12865, "status": "solved"},
                }
            ),
        ) as urlopen:
            result = add_ticket_comment(
                ticket_id="12865",
                body="closing reply",
                public=True,
                solve=True,
            )

        self.assertEqual(result.comment_id, "52660002")
        self.assertEqual(result.ticket_status, "solved")
        request = urlopen.call_args.args[0]
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {
                "ticket": {
                    "comment": {"body": "closing reply", "public": True},
                    "status": "solved",
                    "custom_fields": [{"id": 36379228408724, "value": True}],
                }
            },
        )

    def test_solve_write_without_solved_readback_is_outcome_unknown(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "audit": {
                        "events": [
                            {
                                "body": "closing reply",
                                "id": 52660003,
                                "public": True,
                                "type": "Comment",
                            }
                        ]
                    },
                    "ticket": {"id": 12865, "status": "open"},
                }
            ),
        ):
            with self.assertRaises(ZendeskCommentError) as ctx:
                add_ticket_comment(
                    ticket_id="12865",
                    body="closing reply",
                    public=True,
                    solve=True,
                )

        self.assertEqual(ctx.exception.error_code, "zendesk_ticket_status_unverified")
        self.assertEqual(ctx.exception.category, "outcome_unknown")

    def test_public_write_rejects_private_visibility_readback(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "audit": {
                        "events": [
                            {
                                "body": "public reply body",
                                "id": 52660004,
                                "public": False,
                                "type": "Comment",
                            }
                        ]
                    },
                    "ticket": {"id": 12838, "status": "open"},
                }
            ),
        ):
            with self.assertRaises(ZendeskCommentError) as ctx:
                add_ticket_comment(ticket_id="12838", body="public reply body", public=True)

        self.assertEqual(ctx.exception.error_code, "zendesk_comment_visibility_unverified")

    def test_audit_readback_reports_comment_and_solved_change(self) -> None:
        audits_payload = {
            "audits": [
                {
                    "id": 1,
                    "events": [
                        {
                            "type": "Change",
                            "field_name": "status",
                            "value": "solved",
                        }
                    ],
                },
                {
                    "id": 2,
                    "events": [
                        {
                            "body": "closing reply",
                            "id": 52660005,
                            "public": True,
                            "type": "Comment",
                        }
                    ],
                },
            ]
        }
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(audits_payload),
        ):
            comment, solved_seen = read_ticket_comment_audit(
                ticket_id="12865",
                body="closing reply",
                public=True,
            )

        self.assertIsInstance(comment, ZendeskCommentResult)
        self.assertEqual(comment.comment_id, "52660005")
        self.assertTrue(solved_seen)

    def test_audit_readback_accepts_platform_appended_signature(self) -> None:
        clean_body = "Hi Customer,\n\nThe feature is enabled, and this ticket is closing."
        appended_body = clean_body + "\n\nBest regards,\nMay Collins\nAgora Support Engineer\nJoin our Discord..."
        audits_payload = {
            "audits": [
                {
                    "id": 3,
                    "events": [
                        {
                            "body": appended_body,
                            "id": 52660007,
                            "public": True,
                            "type": "Comment",
                        },
                        {
                            "type": "Change",
                            "field_name": "status",
                            "value": "solved",
                        },
                    ],
                }
            ]
        }
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(audits_payload),
        ):
            comment, solved_seen = read_ticket_comment_audit(
                ticket_id="12839",
                body=clean_body,
                public=True,
            )

        self.assertIsNotNone(comment)
        self.assertEqual(comment.comment_id, "52660007")
        self.assertTrue(solved_seen)

    def test_audit_readback_without_solved_change(self) -> None:
        audits_payload = {
            "audits": [
                {
                    "id": 2,
                    "events": [
                        {
                            "body": "public reply body",
                            "id": 52660006,
                            "public": True,
                            "type": "Comment",
                        }
                    ],
                }
            ]
        }
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(audits_payload),
        ):
            comment, solved_seen = read_ticket_comment_audit(
                ticket_id="12838",
                body="public reply body",
                public=True,
            )

        # The comment is found but no solved change exists, so the solve half
        # of a closing write stays unverified.
        self.assertIsNotNone(comment)
        self.assertEqual(comment.comment_id, "52660006")
        self.assertFalse(solved_seen)

    def test_get_ticket_status_reads_without_writing(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse({"ticket": {"id": 12865, "status": "Solved"}}),
        ) as urlopen:
            status = get_ticket_status(ticket_id="12865")

        self.assertEqual(status, "solved")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "GET")


if __name__ == "__main__":
    unittest.main()
