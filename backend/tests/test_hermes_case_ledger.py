from __future__ import annotations

import json

from backend.services.hermes_case_workflow import (
    render_case_ledger_markdown,
    render_persisted_case_ledger_markdown,
)


def test_database_ledger_renders_fixed_markdown_sections_without_file_metadata() -> None:
    rendered = render_case_ledger_markdown(
        {
            "engineer_case_id": "123-1",
            "client_ticket_id": "123",
            "case_title": "Cannot join",
            "customer_name": "Customer A",
            "vid": "VID-1",
            "zendesk_ticket_id": "456",
            "slack_channel_id": "C-1",
            "slack_thread_ts": "100.200",
            "hermes_conversation_key": "supportportal:engineer-case:123-1",
            "hermes_session_id": "session-1",
            "investigation_id": "INV-1",
            "episode": 2,
            "revision": 7,
            "status": "active",
            "problem_description": "Customer cannot join.",
            "investigation_process": "- Checked token\n- Checked AP logs",
            "misjudgment_corrections": "- ~~Token expired~~ Corrected: token is valid.",
            "current_conclusion_next_steps": "Collect the SDK log.",
            "references": "- Zendesk ticket 123",
        }
    )

    assert rendered.startswith('---\nengineer_case_id: "123-1"')
    assert "\n# Problem description\n" in rendered
    assert "\n# Investigation process\n" in rendered
    assert "\n# Misjudgment corrections\n" in rendered
    assert "\n# Current conclusion and next steps\n" in rendered
    assert "\n# References\n" in rendered
    assert "filename" not in rendered
    assert "artifact_path" not in rendered
    assert 'case_title: "Cannot join"' in rendered
    assert 'customer_name: "Customer A"' in rendered
    assert 'slack_thread_ts: "100.200"' in rendered
    assert 'hermes_session_id: "session-1"' in rendered


def test_renderer_encodes_multiline_metadata_as_one_stable_scalar() -> None:
    rendered = render_case_ledger_markdown(
        {
            "engineer_case_id": "123-1",
            "client_ticket_id": "123",
            "case_title": "Join failure: Android\nsecond line",
            "episode": 1,
            "revision": 1,
            "status": "active",
        }
    )
    title_line = next(line for line in rendered.splitlines() if line.startswith("case_title: "))
    assert json.loads(title_line.removeprefix("case_title: ")) == (
        "Join failure: Android\nsecond line"
    )


def test_renderer_preserves_corrected_wrong_branch_text() -> None:
    rendered = render_case_ledger_markdown(
        {
            "engineer_case_id": "123-1",
            "client_ticket_id": "123",
            "episode": 1,
            "revision": 3,
            "status": "active",
            "problem_description": "Issue",
            "investigation_process": "First direction was token expiry.",
            "misjudgment_corrections": "WRONG: token expiry\nCORRECTED: AP timeout",
            "current_conclusion_next_steps": "Inspect AP path.",
            "references": "Argus sample A",
        }
    )
    assert "WRONG: token expiry" in rendered
    assert "CORRECTED: AP timeout" in rendered


def test_persisted_renderer_joins_existing_case_ticket_slack_and_binding_metadata() -> None:
    class Repository:
        def get_hermes_case_ledger(self, engineer_case_id):
            return {
                "engineer_case_id": engineer_case_id,
                "client_ticket_id": "123",
                "episode": 1,
                "revision": 2,
                "status": "active",
                "problem_description": "Issue",
                "investigation_process": "Checked logs",
                "misjudgment_corrections": "None",
                "current_conclusion_next_steps": "Continue",
                "references": "Ref 1",
            }

        def get_hermes_case_binding(self, engineer_case_id):
            return {
                "client_ticket_id": "123",
                "hermes_conversation_key": "supportportal:engineer-case:123-1",
                "hermes_session_id": "session-1",
                "investigation_id": "INV-1",
            }

        def get_engineer_case(self, engineer_case_id, include_client_messages=False):
            return {"subject": "Engineer title"}

        def get_ticket(self, ticket_id):
            return {"subject": "Ticket title", "requester": "requester@example.com"}

        def get_account_case_by_ticket_id(self, ticket_id):
            return {
                "title": "Account case title",
                "customer_name": "Customer A",
                "vid": "VID-1",
                "zendesk_ticket_id": "456",
            }

        def get_engineer_slack_thread_binding(self, engineer_case_id, active_only=False):
            return {"slack_channel_id": "C-1", "slack_thread_ts": "100.200"}

    rendered = render_persisted_case_ledger_markdown(
        Repository(), engineer_case_id="123-1"
    )
    assert 'case_title: "Account case title"' in rendered
    assert 'customer_name: "Customer A"' in rendered
    assert 'vid: "VID-1"' in rendered
    assert 'zendesk_ticket_id: "456"' in rendered
    assert 'slack_channel_id: "C-1"' in rendered
    assert 'hermes_conversation_key: "supportportal:engineer-case:123-1"' in rendered
