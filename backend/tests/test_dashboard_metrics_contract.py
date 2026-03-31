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
                "priority": "normal",
                "created_at": "2026-03-21T07:15:00+00:00",
                "updated_at": "2026-03-21T11:20:00+00:00",
            },
            {
                "ticket_id": "TK-002",
                "status": "escalated",
                "priority": "high",
                "created_at": "2026-03-21T08:10:00+00:00",
                "updated_at": "2026-03-21T11:40:00+00:00",
            },
            {
                "ticket_id": "TK-003",
                "status": "investigating",
                "priority": "urgent",
                "created_at": "2026-03-21T05:30:00+00:00",
                "updated_at": "2026-03-21T10:30:00+00:00",
            },
            {
                "ticket_id": "TK-004",
                "status": "resolved",
                "priority": "urgent",
                "created_at": "2026-03-21T05:00:00+00:00",
                "updated_at": "2026-03-21T10:10:00+00:00",
            },
        ]
        events = [
            {
                "event": "ticket_created",
                "ticket_id": "TK-001",
                "priority": "normal",
                "status": "communicating",
                "created_at": "2026-03-21T01:15:00+00:00",
            },
            {
                "event": "ticket_escalated",
                "ticket_id": "TK-002",
                "priority": "high",
                "status": "escalated",
                "created_at": "2026-03-21T11:35:00+00:00",
            },
            {
                "event": "engineer_attention_required",
                "ticket_id": "TK-003",
                "priority": "urgent",
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
        self.assertEqual(payload["cards"]["urgent_ticket_count"], 2)

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
        self.assertEqual(payload["charts"]["priority_breakdown"][0]["label"], "Urgent")
        self.assertEqual(payload["charts"]["flow_breakdown"][0]["label"], "Communicating")
        self.assertIn("escalated", payload["summaries"]["queue_health_detail"].lower())

    def test_dashboard_metrics_route_uses_ticket_ops_helper(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn("build_ticket_dashboard_metrics", main_source)


if __name__ == "__main__":
    unittest.main()
