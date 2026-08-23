from __future__ import annotations

import base64
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from backend.services.zendesk_comments import (
    ZendeskCommentError,
    ZendeskCommentResult,
    add_internal_comment,
    add_ticket_comment,
    find_private_internal_comment,
    upload_ticket_attachment,
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


class ZendeskCommentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.basic_auth = base64.b64encode(b"supportportal@example.com:zendesk-api-token").decode("ascii")

    def test_puts_one_private_comment_to_the_expected_ticket_endpoint(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "audit": {
                        "events": [
                            {"field_name": "status", "id": 987653, "type": "Change", "value": "open"},
                            {
                                "body": "validation marker",
                                "id": 987654,
                                "public": False,
                                "type": "Comment",
                            },
                        ]
                    },
                    "ticket": {"id": 12807, "status": "open"},
                }
            ),
        ) as urlopen:
            result = add_internal_comment(ticket_id="12807", body="validation marker")

        self.assertIsInstance(result, ZendeskCommentResult)
        self.assertEqual(result.comment_id, "987654")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://agoraio.zendesk.com/api/v2/tickets/12807.json")
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.get_header("Authorization"), f"Basic {self.basic_auth}")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"ticket": {"comment": {"body": "validation marker", "public": False}}},
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 15.0)

    def test_http_5xx_is_retryable_without_returning_zendesk_details(self) -> None:
        error = urllib.error.HTTPError(
            "https://agoraio.zendesk.com/api/v2/tickets/12807.json",
            503,
            "secret zendesk response",
            {"X-Request-ID": "private-request-id"},
            None,
        )
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            side_effect=error,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                add_internal_comment(ticket_id="12807", body="private body")

        self.assertEqual(raised.exception.category, "retryable")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.error_code, "zendesk_http_error")
        self.assertNotIn("private body", str(raised.exception))
        self.assertNotIn("secret zendesk response", str(raised.exception))
        self.assertNotIn("private-request-id", str(raised.exception))

    def test_network_failure_is_outcome_unknown(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            side_effect=urllib.error.URLError("private network detail"),
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                add_internal_comment(ticket_id="12807", body="private body")

        self.assertEqual(raised.exception.category, "outcome_unknown")
        self.assertEqual(raised.exception.error_code, "zendesk_network_outcome_unknown")
        self.assertNotIn("private body", str(raised.exception))
        self.assertNotIn("private network detail", str(raised.exception))

    def test_audit_readback_finds_exact_private_comment_without_writing(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "audits": [
                        {
                            "events": [
                                {"body": "different body", "id": 4, "public": False, "type": "Comment"},
                                {"body": "validation marker", "id": 5, "public": False, "type": "Comment"},
                            ]
                        }
                    ]
                }
            ),
        ) as urlopen:
            result = find_private_internal_comment(ticket_id="12807", body="validation marker")

        self.assertEqual(result, ZendeskCommentResult(comment_id="5", status_code=200))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.full_url, "https://agoraio.zendesk.com/api/v2/tickets/12807/audits.json")
        self.assertIsNone(request.data)

    def test_success_requires_verified_private_visibility(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "audit": {
                        "events": [
                            {"body": "private body", "id": 1, "public": True, "type": "Comment"}
                        ]
                    },
                    "ticket": {"id": 12807},
                }
            ),
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                add_internal_comment(ticket_id="12807", body="private body")

        self.assertEqual(raised.exception.category, "outcome_unknown")
        self.assertEqual(raised.exception.error_code, "zendesk_comment_visibility_unverified")

    def test_missing_comment_event_is_outcome_unknown(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "audit": {"events": [{"field_name": "status", "type": "Change", "value": "open"}]},
                    "ticket": {"id": 12807},
                }
            ),
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                add_internal_comment(ticket_id="12807", body="private body")

        self.assertEqual(raised.exception.category, "outcome_unknown")
        self.assertEqual(raised.exception.error_code, "zendesk_comment_visibility_unverified")

    def test_missing_or_invalid_basic_auth_fails_closed(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": "not-base64"}, clear=False):
            with self.assertRaises(ZendeskCommentError) as raised:
                add_internal_comment(ticket_id="12807", body="private body")
        self.assertEqual(raised.exception.error_code, "zendesk_basic_auth_invalid")

        with patch.dict(os.environ, {"zendesk_basic_auth": ""}, clear=False):
            with self.assertRaises(ZendeskCommentError) as raised:
                add_internal_comment(ticket_id="12807", body="private body")
        self.assertEqual(raised.exception.error_code, "zendesk_basic_auth_missing")

    def test_input_validation_does_not_open_a_network_request(self) -> None:
        with patch("backend.services.zendesk_comments.urllib.request.urlopen") as urlopen:
            with self.assertRaises(ZendeskCommentError) as raised:
                add_internal_comment(ticket_id="", body="private body")

        self.assertEqual(raised.exception.error_code, "zendesk_comment_input_invalid")
        urlopen.assert_not_called()

    def test_upload_ticket_attachment_posts_binary_and_returns_token(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse({"upload": {"token": "upload-token-1"}}, status=201),
        ) as urlopen:
            token = upload_ticket_attachment(
                filename="invoice-approval.pdf",
                data=b"%PDF-1.4\nfake invoice\n%%EOF",
            )

        self.assertEqual(token, "upload-token-1")
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://agoraio.zendesk.com/api/v2/uploads?filename=invoice-approval.pdf",
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), f"Basic {self.basic_auth}")
        self.assertEqual(request.get_header("Content-type"), "application/pdf")
        self.assertEqual(request.data, b"%PDF-1.4\nfake invoice\n%%EOF")

    def test_upload_ticket_attachment_network_failure_is_retryable(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            side_effect=urllib.error.URLError("private network detail"),
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                upload_ticket_attachment(filename="invoice.pdf", data=b"%PDF-1.4")

        self.assertEqual(raised.exception.category, "retryable")
        self.assertEqual(raised.exception.error_code, "zendesk_upload_network_failed")
        self.assertNotIn("private network detail", str(raised.exception))

    def test_upload_ticket_attachment_is_forbidden_in_staging(self) -> None:
        with patch.dict(
            os.environ,
            {"zendesk_basic_auth": self.basic_auth, "AUTOMATION_ENVIRONMENT": "staging"},
            clear=False,
        ):
            with self.assertRaises(ZendeskCommentError) as raised:
                upload_ticket_attachment(filename="invoice.pdf", data=b"%PDF-1.4")

        self.assertEqual(raised.exception.category, "permanent")
        self.assertEqual(raised.exception.error_code, "zendesk_outbound_forbidden_staging")

    def test_add_ticket_comment_includes_upload_tokens(self) -> None:
        with patch.dict(os.environ, {"zendesk_basic_auth": self.basic_auth}, clear=False), patch(
            "backend.services.zendesk_comments.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "audit": {
                        "events": [
                            {
                                "body": "validation marker",
                                "id": 987654,
                                "public": True,
                                "type": "Comment",
                            },
                        ]
                    },
                    "ticket": {"id": 12807, "status": "solved"},
                }
            ),
        ) as urlopen:
            result = add_ticket_comment(
                ticket_id="12807",
                body="validation marker",
                public=True,
                solve=True,
                uploads=("upload-token-1", "upload-token-2"),
            )

        self.assertEqual(result.comment_id, "987654")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            payload["ticket"]["comment"]["uploads"],
            ["upload-token-1", "upload-token-2"],
        )
        self.assertEqual(payload["ticket"]["comment"]["body"], "validation marker")
        self.assertEqual(payload["ticket"]["status"], "solved")


if __name__ == "__main__":
    unittest.main()
