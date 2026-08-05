from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from backend.services.billing_automation import (
    BILLING_ACTION_ACCOUNT_VERIFICATION,
    BillingRequestReply,
    build_billing_automation_result,
    poll_automation_request_replies,
    poll_billing_request_replies,
    record_billing_request_reply,
    send_billing_internal_email,
)

GRAPH_ENV = {
    "BILLING_AUTOMATION_MAIL_TRANSPORT": "graph",
    "BILLING_AUTOMATION_GRAPH_TENANT_ID": "60275374-3eaa-49c2-83c3-cc189d126981",
    "BILLING_AUTOMATION_GRAPH_CLIENT_ID": "client-id",
    "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET": "client-secret",
    "BILLING_AUTOMATION_GRAPH_USERNAME": "ai-support-agent@agora.io",
}


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object] | None = None, status: int = 202) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        if self.payload is None:
            return b""
        return json.dumps(self.payload).encode("utf-8")


class _FakeBytesResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> "_FakeBytesResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _json_request_body(request) -> dict[str, object]:  # type: ignore[no-untyped-def]
    raw = request.data.decode("utf-8") if request.data else "{}"
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


class BillingAutomationEmailTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = patch.dict(
            os.environ,
            {
                key: ""
                for key in (
                    "BILLING_AUTOMATION_EMAIL_FROM",
                    "BILLING_AUTOMATION_MAIL_TRANSPORT",
                    "BILLING_AUTOMATION_GRAPH_TENANT_ID",
                    "BILLING_AUTOMATION_GRAPH_CLIENT_ID",
                    "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET",
                    "BILLING_AUTOMATION_GRAPH_USERNAME",
                    "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE",
                    "BILLING_AUTOMATION_SMTP_PASSWORD",
                )
            },
        )
        self._env_patcher.start()

    def tearDown(self) -> None:
        self._env_patcher.stop()

    def test_internal_email_defaults_to_company_outlook_sender(self) -> None:
        result = build_billing_automation_result(
            action=BILLING_ACTION_ACCOUNT_VERIFICATION,
            message=(
                "Company: ExampleCorp. Company location: Singapore. "
                "Website: https://example.com. Email: admin@example.com. "
                "Phone: +65-1234-5678. Use Case: internal video calls."
            ),
            ticket_id="TK-1",
            customer_email="customer@example.com",
        )

        self.assertIsNotNone(result.internal_email)
        assert result.internal_email is not None
        self.assertEqual(result.internal_email["from"], "ai-support-agent@agora.io")
        self.assertEqual(result.internal_email["body_content_type"], "HTML")
        self.assertIn("Request details", result.internal_email["body_html"])

    def test_send_billing_internal_email_uses_graph_sendmail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "billing-graph-token.json"
            cache_path.write_text(json.dumps({"refresh_token": "refresh-token"}), encoding="utf-8")
            requests = []

            def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                requests.append(request)
                url = request.full_url
                if url.endswith("/oauth2/v2.0/token"):
                    return _FakeHttpResponse(
                        {
                            "access_token": "access-token",
                            "refresh_token": "new-refresh-token",
                            "expires_in": 3600,
                        },
                        status=200,
                    )
                if url == "https://graph.microsoft.com/v1.0/me/sendMail":
                    return _FakeHttpResponse(status=202)
                raise AssertionError(f"unexpected URL {url}")

            env = dict(GRAPH_ENV)
            env["BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"] = str(cache_path)
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = send_billing_internal_email(
                    {
                        "to": "billing@example.com",
                        "from": "xieziling97@163.com",
                        "subject": "Account verification request",
                        "body": "Hi team,\nPlease review this account.",
                    }
                )

        self.assertEqual(result, {"status": "sent", "reason": ""})
        self.assertEqual(len(requests), 2)
        token_body = requests[0].data.decode("utf-8")
        self.assertIn("refresh_token=refresh-token", token_body)
        self.assertIn("client_secret=client-secret", token_body)
        self.assertEqual(requests[1].headers["Authorization"], "Bearer access-token")
        graph_payload = json.loads(requests[1].data.decode("utf-8"))
        self.assertEqual(graph_payload["message"]["subject"], "Account verification request")
        self.assertEqual(graph_payload["message"]["body"]["content"], "Hi team,\nPlease review this account.")
        self.assertEqual(
            graph_payload["message"]["toRecipients"],
            [{"emailAddress": {"address": "billing@example.com"}}],
        )
        self.assertNotIn("xieziling97@163.com", requests[1].data.decode("utf-8"))

    def test_send_billing_internal_email_prefers_html_body(self) -> None:
        with patch.dict(os.environ, dict(GRAPH_ENV), clear=False), patch(
            "backend.services.billing_automation._load_graph_mail_config",
            return_value={"tenant_id": "tenant", "client_id": "client", "client_secret": "secret", "username": "agent@example.com", "token_cache": "cache"},
        ), patch(
            "backend.services.billing_automation._acquire_graph_access_token",
            return_value="access-token",
        ), patch("backend.services.billing_automation._send_graph_mail") as send_mail:
            result = send_billing_internal_email(
                {
                    "to": "billing@example.com",
                    "from": "agent@example.com",
                    "subject": "Invoice",
                    "body": "Plain fallback",
                    "body_html": "<p>Pretty</p>",
                }
            )

        self.assertEqual(result, {"status": "sent", "reason": ""})
        send_mail.assert_called_once_with(
            access_token="access-token",
            to_address="billing@example.com",
            subject="Invoice",
            body="<p>Pretty</p>",
            content_type="HTML",
        )

    def test_send_billing_internal_email_never_uses_legacy_smtp(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BILLING_AUTOMATION_MAIL_TRANSPORT": "",
                "BILLING_AUTOMATION_SMTP_HOST": "smtp.163.com",
                "BILLING_AUTOMATION_SMTP_USERNAME": "xieziling97@163.com",
                "BILLING_AUTOMATION_SMTP_PASSWORD": "legacy-password",
            },
        ), patch("smtplib.SMTP_SSL") as smtp_ssl:
            result = send_billing_internal_email(
                {
                    "to": "billing@example.com",
                    "from": "xieziling97@163.com",
                    "subject": "Account verification request",
                    "body": "Hi team",
                }
            )

        self.assertEqual(result["status"], "skipped_config_missing")
        self.assertIn("BILLING_AUTOMATION_GRAPH", result["reason"])
        smtp_ssl.assert_not_called()

    def test_send_billing_internal_email_accepts_msal_token_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "msal-cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "AccessToken": {
                            "token-key": {
                                "secret": "cached-access-token",
                                "expires_on": "4102444800",
                                "target": "Mail.Send User.Read",
                            }
                        },
                        "RefreshToken": {
                            "refresh-key": {
                                "secret": "cached-refresh-token",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            requests = []

            def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                requests.append(request)
                return _FakeHttpResponse(status=202)

            env = dict(GRAPH_ENV)
            env["BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"] = str(cache_path)
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = send_billing_internal_email(
                    {
                        "to": "billing@example.com",
                        "subject": "Account verification request",
                        "body": "Hi team",
                    }
                )

        self.assertEqual(result, {"status": "sent", "reason": ""})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].full_url, "https://graph.microsoft.com/v1.0/me/sendMail")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer cached-access-token")

    def test_poll_billing_request_replies_reads_recent_messages_without_unread_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "billing-graph-token.json"
            cache_path.write_text(
                json.dumps({"access_token": "cached-access-token", "expires_at": 4102444800}),
                encoding="utf-8",
            )
            requests = []
            handled = []

            def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                requests.append(request)
                url = request.full_url
                if url.startswith("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?"):
                    return _FakeHttpResponse(
                        {
                            "value": [
                                {
                                    "id": "msg-match",
                                    "subject": "Re: [Billing Request] Detailed invoice request - Ticket TK-1",
                                    "from": {"emailAddress": {"address": "billing@example.com"}},
                                    "receivedDateTime": "2026-07-02T06:00:00Z",
                                    "isRead": True,
                                },
                                {
                                    "id": "msg-other",
                                    "subject": "Re: unrelated support thread",
                                    "from": {"emailAddress": {"address": "other@example.com"}},
                                },
                            ]
                        },
                        status=200,
                    )
                if url.startswith("https://graph.microsoft.com/v1.0/me/messages/msg-match?"):
                    return _FakeHttpResponse(
                        {
                            "id": "msg-match",
                            "subject": "Re: [Billing Request] Detailed invoice request - Ticket TK-1",
                            "from": {"emailAddress": {"address": "billing@example.com"}},
                            "body": {
                                "contentType": "html",
                                "content": "<p>Approved. Please proceed.</p><p>Thanks &amp; regards.</p>",
                            },
                            "receivedDateTime": "2026-07-02T06:00:00Z",
                        },
                        status=200,
                    )
                raise AssertionError(f"unexpected URL {url}")

            def fake_mark_read_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                if request.full_url == "https://graph.microsoft.com/v1.0/me/messages/msg-match" and request.get_method() == "PATCH":
                    requests.append(request)
                    return _FakeHttpResponse(status=200)
                return fake_urlopen(request, timeout=timeout)

            env = dict(GRAPH_ENV)
            env["BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"] = str(cache_path)
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", side_effect=fake_mark_read_urlopen):
                replies = poll_billing_request_replies(handler=handled.append)

        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].message_id, "msg-match")
        self.assertEqual(replies[0].subject, "Re: [Billing Request] Detailed invoice request - Ticket TK-1")
        self.assertEqual(replies[0].sender, "billing@example.com")
        self.assertEqual(replies[0].body_text, "Approved. Please proceed.\nThanks & regards.")
        self.assertEqual(handled, replies)
        self.assertEqual([request.get_method() for request in requests], ["GET", "GET"])
        query = urllib.parse.parse_qs(urllib.parse.urlparse(requests[0].full_url).query)
        self.assertNotIn("isRead", query["$filter"][0])
        self.assertIn("receivedDateTime ge ", query["$filter"][0])
        self.assertEqual(query["$orderby"], ["receivedDateTime desc"])

    def test_poll_billing_request_replies_ignores_unmatched_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "billing-graph-token.json"
            cache_path.write_text(
                json.dumps({"access_token": "cached-access-token", "expires_at": 4102444800}),
                encoding="utf-8",
            )
            requests = []

            def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                requests.append(request)
                if request.full_url.startswith("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?"):
                    return _FakeHttpResponse(
                        {
                            "value": [
                                {
                                    "id": "msg-other",
                                    "subject": "Re: unrelated support thread",
                                    "from": {"emailAddress": {"address": "other@example.com"}},
                                }
                            ]
                        },
                        status=200,
                    )
                raise AssertionError(f"unexpected URL {request.full_url}")

            env = dict(GRAPH_ENV)
            env["BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"] = str(cache_path)
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                replies = poll_billing_request_replies()

        self.assertEqual(replies, [])
        self.assertEqual(len(requests), 1)

    def test_poll_automation_request_replies_does_not_download_enablement_attachments(self) -> None:
        summary = {
            "id": "msg-enablement",
            "subject": "Re: [Enablement Request] Media Relay - Ticket TK-1",
            "hasAttachments": True,
        }
        message = {
            **summary,
            "from": {"emailAddress": {"address": "enablement@example.com"}},
            "body": {"contentType": "text", "content": "Review is complete."},
        }

        with patch.dict(os.environ, GRAPH_ENV), patch(
            "backend.services.billing_automation._acquire_graph_access_token",
            return_value="access-token",
        ), patch(
            "backend.services.billing_automation._list_recent_inbox_messages",
            return_value=[summary],
        ), patch(
            "backend.services.billing_automation._get_graph_message",
            return_value=message,
        ), patch(
            "backend.services.billing_automation._download_billing_reply_pdf_attachments",
        ) as download_mock, patch(
            "backend.services.billing_automation._mark_graph_message_read",
        ) as mark_read_mock:
            replies = poll_automation_request_replies(
                subject_prefixes=("[Billing Request]", "[Enablement Request]"),
            )

        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].body_text, "Review is complete.")
        self.assertEqual(replies[0].attachments, ())
        download_mock.assert_not_called()
        mark_read_mock.assert_called_once_with(access_token="access-token", message_id="msg-enablement")

    def test_poll_automation_request_replies_does_not_count_already_processed_message(self) -> None:
        summary = {
            "id": "msg-enablement",
            "subject": "Re: [Enablement Request] Media Relay - Ticket TK-1",
            "isRead": True,
        }
        message = {
            **summary,
            "from": {"emailAddress": {"address": "enablement@example.com"}},
            "body": {"contentType": "text", "content": "Review is complete."},
        }

        with patch.dict(os.environ, GRAPH_ENV), patch(
            "backend.services.billing_automation._acquire_graph_access_token",
            return_value="access-token",
        ), patch(
            "backend.services.billing_automation._list_recent_inbox_messages",
            return_value=[summary],
        ), patch(
            "backend.services.billing_automation._get_graph_message",
            return_value=message,
        ), patch(
            "backend.services.billing_automation._mark_graph_message_read",
        ) as mark_read_mock:
            replies = poll_automation_request_replies(
                handler=lambda _reply: False,
                subject_prefixes=("[Enablement Request]",),
            )

        self.assertEqual(replies, [])
        mark_read_mock.assert_not_called()

    def test_poll_automation_request_replies_keeps_in_progress_message_unread(self) -> None:
        summary = {"id": "msg-active", "subject": "[Enablement Request] Media Relay - Ticket 12555"}
        message = {**summary, "body": {"contentType": "text", "content": "Done."}}
        with patch.dict(os.environ, GRAPH_ENV), patch(
            "backend.services.billing_automation._acquire_graph_access_token", return_value="access-token"
        ), patch(
            "backend.services.billing_automation._list_recent_inbox_messages", return_value=[summary]
        ), patch(
            "backend.services.billing_automation._get_graph_message", return_value=message
        ), patch(
            "backend.services.billing_automation._mark_graph_message_read"
        ) as mark_read_mock:
            replies = poll_automation_request_replies(
                handler=lambda _reply: "in_progress", subject_prefixes=("[Enablement Request]",)
            )
        self.assertEqual(replies, [])
        mark_read_mock.assert_not_called()

    def test_poll_automation_request_replies_marks_completed_duplicate_read(self) -> None:
        summary = {"id": "msg-done", "subject": "[Enablement Request] Media Relay - Ticket 12555"}
        message = {**summary, "body": {"contentType": "text", "content": "Done."}}
        with patch.dict(os.environ, GRAPH_ENV), patch(
            "backend.services.billing_automation._acquire_graph_access_token", return_value="access-token"
        ), patch(
            "backend.services.billing_automation._list_recent_inbox_messages", return_value=[summary]
        ), patch(
            "backend.services.billing_automation._get_graph_message", return_value=message
        ), patch(
            "backend.services.billing_automation._mark_graph_message_read"
        ) as mark_read_mock:
            replies = poll_automation_request_replies(
                handler=lambda _reply: "already_completed", subject_prefixes=("[Enablement Request]",)
            )
        self.assertEqual(replies, [])
        mark_read_mock.assert_called_once_with(access_token="access-token", message_id="msg-done")

    def test_poll_billing_request_replies_leaves_message_unread_when_handler_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "billing-graph-token.json"
            cache_path.write_text(
                json.dumps({"access_token": "cached-access-token", "expires_at": 4102444800}),
                encoding="utf-8",
            )
            requests = []

            def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                requests.append(request)
                url = request.full_url
                if url.startswith("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?"):
                    return _FakeHttpResponse(
                        {
                            "value": [
                                {
                                    "id": "msg-match",
                                    "subject": "[Billing Request] Account verification request - Ticket TK-2",
                                    "from": {"emailAddress": {"address": "billing@example.com"}},
                                }
                            ]
                        },
                        status=200,
                    )
                if url.startswith("https://graph.microsoft.com/v1.0/me/messages/msg-match?") and request.get_method() == "GET":
                    return _FakeHttpResponse(
                        {
                            "id": "msg-match",
                            "subject": "[Billing Request] Account verification request - Ticket TK-2",
                            "from": {"emailAddress": {"address": "billing@example.com"}},
                            "body": {"content": "Looks good."},
                        },
                        status=200,
                    )
                raise AssertionError(f"unexpected URL {url}")

            env = dict(GRAPH_ENV)
            env["BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"] = str(cache_path)
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                with self.assertRaisesRegex(RuntimeError, "handler failed"):
                    poll_billing_request_replies(handler=lambda _reply: (_ for _ in ()).throw(RuntimeError("handler failed")))

        self.assertEqual([request.get_method() for request in requests], ["GET", "GET"])

    def test_poll_billing_request_replies_downloads_pdf_attachments_without_ocr_before_marking_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "billing-graph-token.json"
            cache_path.write_text(
                json.dumps({"access_token": "cached-access-token", "expires_at": 4102444800}),
                encoding="utf-8",
            )
            requests = []
            handled = []

            def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                requests.append(request)
                url = request.full_url
                if url.startswith("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?"):
                    return _FakeHttpResponse(
                        {
                            "value": [
                                {
                                    "id": "msg-with-pdf",
                                    "subject": "Re: [Billing Request] Detailed invoice request - Ticket TK-1",
                                    "from": {"emailAddress": {"address": "billing@example.com"}},
                                    "receivedDateTime": "2026-07-02T06:00:00Z",
                                    "hasAttachments": True,
                                }
                            ]
                        },
                        status=200,
                    )
                if url.startswith("https://graph.microsoft.com/v1.0/me/messages/msg-with-pdf?"):
                    return _FakeHttpResponse(
                        {
                            "id": "msg-with-pdf",
                            "subject": "Re: [Billing Request] Detailed invoice request - Ticket TK-1",
                            "from": {"emailAddress": {"address": "billing@example.com"}},
                            "body": {"contentType": "text", "content": "Please see attached approval."},
                            "receivedDateTime": "2026-07-02T06:00:00Z",
                            "hasAttachments": True,
                        },
                        status=200,
                    )
                if url.startswith("https://graph.microsoft.com/v1.0/me/messages/msg-with-pdf/attachments?"):
                    return _FakeHttpResponse(
                        {
                            "value": [
                                {
                                    "id": "att-pdf",
                                    "name": "invoice-approval.pdf",
                                    "contentType": "application/pdf",
                                    "size": 32,
                                    "isInline": False,
                                },
                                {
                                    "id": "att-logo",
                                    "name": "logo.png",
                                    "contentType": "image/png",
                                    "size": 12,
                                    "isInline": True,
                                },
                            ]
                        },
                        status=200,
                    )
                if url == "https://graph.microsoft.com/v1.0/me/messages/msg-with-pdf/attachments/att-pdf/$value":
                    return _FakeBytesResponse(b"%PDF-1.4\nfake billing approval\n%%EOF")
                if "paddleocr.aistudio-app.com" in url or url == "https://ocr.example.test/result.jsonl":
                    raise AssertionError("PDF attachment forwarding must not call OCR")
                if url == "https://graph.microsoft.com/v1.0/me/messages/msg-with-pdf" and request.get_method() == "PATCH":
                    return _FakeHttpResponse(status=200)
                raise AssertionError(f"unexpected URL {url}")

            def handle(reply: BillingRequestReply) -> None:
                handled.append(reply)
                self.assertEqual(reply.attachment_names, ("invoice-approval.pdf",))
                self.assertEqual(reply.attachment_text, "")
                self.assertEqual(len(reply.attachments), 1)
                self.assertEqual(reply.attachments[0].name, "invoice-approval.pdf")
                self.assertEqual(reply.attachments[0].content_type, "application/pdf")
                self.assertEqual(reply.attachments[0].content, b"%PDF-1.4\nfake billing approval\n%%EOF")
                self.assertFalse(any(request.get_method() == "PATCH" for request in requests))

            env = dict(GRAPH_ENV)
            env["BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"] = str(cache_path)
            env["PADDLEOCR_API_TOKEN"] = ""
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                replies = poll_billing_request_replies(handler=handle)

        self.assertEqual(handled, replies)
        self.assertEqual(replies[0].attachment_names, ("invoice-approval.pdf",))
        self.assertEqual(replies[0].attachment_text, "")
        self.assertEqual([request.get_method() for request in requests], ["GET", "GET", "GET", "GET", "PATCH"])

    def test_poll_billing_request_replies_leaves_pdf_message_unread_when_handler_fails_after_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "billing-graph-token.json"
            cache_path.write_text(
                json.dumps({"access_token": "cached-access-token", "expires_at": 4102444800}),
                encoding="utf-8",
            )
            requests = []

            def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                requests.append(request)
                url = request.full_url
                if url.startswith("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?"):
                    return _FakeHttpResponse(
                        {
                            "value": [
                                {
                                    "id": "msg-with-pdf",
                                    "subject": "Re: [Billing Request] Detailed invoice request - Ticket TK-1",
                                    "hasAttachments": True,
                                }
                            ]
                        },
                        status=200,
                    )
                if url.startswith("https://graph.microsoft.com/v1.0/me/messages/msg-with-pdf?"):
                    return _FakeHttpResponse(
                        {
                            "id": "msg-with-pdf",
                            "subject": "Re: [Billing Request] Detailed invoice request - Ticket TK-1",
                            "body": {"content": "Attached."},
                            "hasAttachments": True,
                        },
                        status=200,
                    )
                if url.startswith("https://graph.microsoft.com/v1.0/me/messages/msg-with-pdf/attachments?"):
                    return _FakeHttpResponse(
                        {
                            "value": [
                                {
                                    "id": "att-pdf",
                                    "name": "invoice-approval.pdf",
                                    "contentType": "application/pdf",
                                    "size": 32,
                                    "isInline": False,
                                }
                            ]
                        },
                        status=200,
                    )
                if url == "https://graph.microsoft.com/v1.0/me/messages/msg-with-pdf/attachments/att-pdf/$value":
                    return _FakeBytesResponse(b"%PDF-1.4\nfake billing approval\n%%EOF")
                if "paddleocr.aistudio-app.com" in url:
                    raise AssertionError("PDF attachment forwarding must not call OCR")
                raise AssertionError(f"unexpected URL {url}")

            env = dict(GRAPH_ENV)
            env["BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"] = str(cache_path)
            env["PADDLEOCR_API_TOKEN"] = ""
            with patch.dict(os.environ, env), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                with self.assertRaisesRegex(RuntimeError, "handler failed"):
                    poll_billing_request_replies(
                        handler=lambda _reply: (_ for _ in ()).throw(RuntimeError("handler failed"))
                    )

        self.assertFalse(any(request.get_method() == "PATCH" for request in requests))

    def test_record_billing_request_reply_appends_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = Path(temp_dir) / "billing-replies.jsonl"
            reply = BillingRequestReply(
                message_id="msg-1",
                subject="Re: [Billing Request] Detailed invoice request - Ticket TK-1",
                sender="billing@example.com",
                body_text="Approved. Please proceed.",
                received_at="2026-07-02T06:00:00Z",
            )

            record_billing_request_reply(reply, record_path=record_path)

            lines = record_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["message_id"], "msg-1")
        self.assertEqual(payload["subject"], "Re: [Billing Request] Detailed invoice request - Ticket TK-1")
        self.assertEqual(payload["sender"], "billing@example.com")
        self.assertEqual(payload["body_text"], "Approved. Please proceed.")
        self.assertEqual(payload["received_at"], "2026-07-02T06:00:00Z")
        self.assertIsInstance(payload["recorded_at"], int)


if __name__ == "__main__":
    unittest.main()
