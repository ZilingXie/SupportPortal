from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path


class DashboardMetricsContractTests(unittest.TestCase):
    def test_ticket_dashboard_metrics_helper_returns_expanded_ticket_ops_payload(self) -> None:
        from backend.services.dashboard_ticket_ops import build_ticket_dashboard_metrics

        now = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
        tickets = [
            {
                "ticket_id": "TK-001",
                "status": "communicating",
                "created_at": "2026-03-21T07:15:00+00:00",
                "updated_at": "2026-03-21T11:20:00+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Can you check this?",
                        "created_at": "2026-03-21T11:10:00+00:00",
                        "sentiment_label": "neutral",
                    }
                ],
            },
            {
                "ticket_id": "TK-002",
                "status": "escalated",
                "created_at": "2026-03-21T08:10:00+00:00",
                "updated_at": "2026-03-21T11:40:00+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "My service is down and I am frustrated.",
                        "created_at": "2026-03-21T11:35:00+00:00",
                        "sentiment_label": "bad",
                    }
                ],
            },
            {
                "ticket_id": "TK-003",
                "status": "investigating",
                "created_at": "2026-03-21T05:30:00+00:00",
                "updated_at": "2026-03-21T10:30:00+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Thank you, this is improving.",
                        "created_at": "2026-03-21T10:00:00+00:00",
                        "sentiment_label": "good",
                    }
                ],
            },
            {
                "ticket_id": "TK-004",
                "status": "resolved",
                "created_at": "2026-03-21T05:00:00+00:00",
                "updated_at": "2026-03-21T10:10:00+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Following up on the earlier request.",
                        "created_at": "2026-03-21T09:55:00+00:00",
                        "sentiment_label": None,
                    }
                ],
            },
        ]
        events = [
            {
                "event": "ticket_created",
                "ticket_id": "TK-001",
                "status": "communicating",
                "created_at": "2026-03-21T01:15:00+00:00",
            },
            {
                "event": "ticket_message_sentiment_tagged",
                "ticket_id": "TK-002",
                "sentiment_label": "bad",
                "status": "escalated",
                "created_at": "2026-03-21T11:35:00+00:00",
            },
            {
                "event": "engineer_attention_required",
                "ticket_id": "TK-003",
                "status": "investigating",
                "created_at": "2026-03-21T10:05:00+00:00",
            },
        ]

        payload = build_ticket_dashboard_metrics(tickets, events, now=now)

        self.assertEqual(payload["today_ticket_count"], 4)
        self.assertEqual(payload["resolution_rate"], 25.0)
        self.assertEqual(payload["sentiment_alert_count"], 1)

        self.assertEqual(payload["cards"]["investigating_ticket_count"], 1)
        self.assertEqual(payload["cards"]["open_ticket_count"], 0)
        self.assertEqual(payload["cards"]["communicating_ticket_count"], 1)
        self.assertEqual(payload["cards"]["escalated_ticket_count"], 1)
        self.assertEqual(payload["cards"]["resolved_ticket_count"], 1)
        self.assertEqual(payload["cards"]["bad_sentiment_ticket_count"], 1)

        self.assertIn("queue_health_label", payload["summaries"])
        self.assertIn("queue_health_detail", payload["summaries"])
        self.assertIn("operator_summary_title", payload["summaries"])
        self.assertIn("operator_summary_detail", payload["summaries"])
        self.assertIn("escalation_summary_title", payload["summaries"])
        self.assertIn("escalation_summary_detail", payload["summaries"])

        self.assertEqual(len(payload["charts"]["event_volume_12h"]), 12)
        self.assertEqual(payload["charts"]["event_volume_12h"][-1]["value"], 1)
        self.assertEqual(payload["charts"]["event_volume_12h"][-2]["value"], 1)

        self.assertEqual(payload["charts"]["status_breakdown"][0]["label"], "Communicating")
        self.assertEqual(payload["charts"]["status_breakdown"][1]["label"], "Escalated")
        self.assertEqual(payload["charts"]["sentiment_breakdown"][0]["label"], "Bad")
        self.assertEqual(payload["charts"]["sentiment_breakdown"][1]["label"], "Neutral")
        self.assertEqual(payload["charts"]["sentiment_breakdown"][2]["label"], "Good")
        self.assertEqual(payload["charts"]["sentiment_breakdown"][3]["label"], "Unclassified")
        self.assertEqual(payload["charts"]["flow_breakdown"][0]["label"], "Communicating")
        self.assertIn("escalated", payload["summaries"]["queue_health_detail"].lower())

    def test_dashboard_metrics_route_uses_ticket_ops_helper(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn("build_ticket_dashboard_metrics", main_source)
        self.assertIn("ticket_repository.list_tickets(include_messages=False)", main_source)


if __name__ == "__main__":
    unittest.main()
