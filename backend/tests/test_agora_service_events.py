from __future__ import annotations

import os
import unittest
import urllib.error
from unittest.mock import patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from fastapi.testclient import TestClient

import backend.main as main
from backend.services.agora_service_events import (
    build_default_service_events_payload,
    get_agora_service_events_payload,
    parse_agora_service_events_rss,
    reset_agora_service_events_cache,
)


_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Agora Status - Incident History</title>
    <item>
      <title><![CDATA[【Global】Real-Time Communication A small number of users are experiencing a black screen issue]]></title>
      <description><![CDATA[
        <p>
          <small>Posted Feb 24, 2026 - 01:04 PM UTC</small><br>
          <strong>Resolved</strong><br>
          We have identified that a limited number of users experienced a black screen phenomenon.
        </p>]]></description>
      <link>https://status.agora.io/events/44</link>
      <pubDate>Sat, 22 Jun 58120 14:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


class AgoraServiceEventsTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_agora_service_events_cache()

    def test_parse_agora_service_events_rss_extracts_stable_fields(self) -> None:
        items = parse_agora_service_events_rss(_SAMPLE_RSS, limit=3)

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["title"],
            "【Global】Real-Time Communication A small number of users are experiencing a black screen issue",
        )
        self.assertEqual(items[0]["status_label"], "Resolved")
        self.assertEqual(items[0]["posted_at_label"], "Posted Feb 24, 2026 - 01:04 PM UTC")
        self.assertEqual(items[0]["link"], "https://status.agora.io/events/44")
        self.assertIn("limited number of users experienced a black screen phenomenon", items[0]["summary"])

    def test_get_agora_service_events_payload_returns_empty_items_when_feed_fetch_fails(self) -> None:
        with patch(
            "backend.services.agora_service_events._fetch_agora_service_events_rss",
            side_effect=urllib.error.URLError("timed out"),
        ):
            payload = get_agora_service_events_payload()

        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["status_page_url"], "https://status.agora.io/")
        self.assertIsInstance(payload["fetched_at"], str)

    def test_get_agora_service_events_payload_uses_cache(self) -> None:
        calls: list[str] = []

        def _fake_fetch() -> str:
            calls.append("fetch")
            return _SAMPLE_RSS

        first = get_agora_service_events_payload(fetcher=_fake_fetch)
        second = get_agora_service_events_payload(fetcher=_fake_fetch)

        self.assertEqual(len(calls), 1)
        self.assertEqual(first["items"][0]["title"], second["items"][0]["title"])


class AgoraServiceEventsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def test_service_events_route_returns_stable_payload_shape(self) -> None:
        payload = build_default_service_events_payload(
            items=[
                {
                    "title": "RTC black screen issue",
                    "summary": "A limited number of users experienced a black screen phenomenon.",
                    "link": "https://status.agora.io/events/44",
                    "status_label": "Resolved",
                    "posted_at_label": "Posted Feb 24, 2026 - 01:04 PM UTC",
                }
            ],
            fetched_at="2026-04-21T02:00:00+00:00",
        )

        with patch("backend.main.get_agora_service_events_payload", return_value=payload):
            response = self.client.get("/api/client/service-events")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status_page_url"], "https://status.agora.io/")
        self.assertEqual(body["items"][0]["title"], "RTC black screen issue")
        self.assertEqual(body["items"][0]["status_label"], "Resolved")
        self.assertEqual(body["items"][0]["posted_at_label"], "Posted Feb 24, 2026 - 01:04 PM UTC")


if __name__ == "__main__":
    unittest.main()
