from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Iterator
from unittest.mock import MagicMock

from backend.services.automation_ecs_dashboard_reader import (
    PostgresDashboardCaseReader,
    _safe_collected_fields,
)
from backend.tests.test_automation_ecs_store import _settings


class _FixtureReader(PostgresDashboardCaseReader):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(_settings())
        self.rows = rows

    @contextmanager
    def _read_cursor(self) -> Iterator[Any]:
        yield object()

    def _list_rows(self, cursor: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del cursor, kwargs
        return copy.deepcopy(self.rows)


def _row(
    ticket_id: str,
    *,
    ticket_status: str,
    updated_at: str,
    category: str,
    subcategory: str | None,
    route_status: str,
    execution_status: str,
) -> dict[str, Any]:
    return {
        "zendesk_ticket_id": ticket_id,
        "title": f"Ticket {ticket_id}",
        "ticket_status": ticket_status,
        "ticket_updated_at": updated_at,
        "automation_status": execution_status,
        "route": subcategory,
        "scope_label": category,
        "route_family": "automated" if route_status == "automated" else category,
        "execution_action": subcategory,
        "category": category,
        "subcategory": subcategory,
        "route_status": route_status,
        "automation_handler": subcategory,
        "route_classification": {},
        "execution_id": f"exec-{ticket_id}",
        "event_id": f"event-{ticket_id}",
        "event_type": "ticket.updated",
        "status": execution_status,
        "current_stage": "automation.complete",
        "failure_stage": None,
        "failure_code": None,
        "requires_human_review": execution_status == "human_review",
        "created_at": updated_at,
        "updated_at": updated_at,
        "execution_count": 1,
    }


def test_case_page_defaults_active_sorts_by_ticket_update_and_builds_facets() -> None:
    reader = _FixtureReader(
        [
            _row(
                "13119",
                ticket_status="open",
                updated_at="2026-08-29T03:12:00Z",
                category="backend_operation",
                subcategory="enablement",
                route_status="automated",
                execution_status="completed",
            ),
            _row(
                "13120",
                ticket_status="solved",
                updated_at="2026-08-31T03:12:00Z",
                category="account_billing",
                subcategory="fraud_account",
                route_status="automated",
                execution_status="completed",
            ),
            _row(
                "13121",
                ticket_status="pending",
                updated_at="2026-08-30T03:12:00Z",
                category="conversation",
                subcategory="follow_up",
                route_status="not_automated",
                execution_status="human_review",
            ),
        ]
    )
    page = reader.list_cases(page=1, page_size=25)
    assert [item["zendesk_ticket_id"] for item in page["items"]] == ["13121", "13119"]
    assert page["total"] == 2
    assert page["facets"]["ticket_statuses"]["all"] == 3
    assert page["facets"]["ticket_statuses"]["active"] == 2
    assert page["facets"]["route_groups"]["automation"] == 1
    assert page["facets"]["route_groups"]["backend_operation"] == 1


def test_case_page_combines_ticket_status_category_subcategory_and_pages() -> None:
    reader = _FixtureReader(
        [
            _row(
                "13119",
                ticket_status="open",
                updated_at="2026-08-29T03:12:00Z",
                category="backend_operation",
                subcategory="enablement",
                route_status="automated",
                execution_status="completed",
            ),
            _row(
                "13122",
                ticket_status="open",
                updated_at="2026-08-30T03:12:00Z",
                category="backend_operation",
                subcategory="quota",
                route_status="not_automated",
                execution_status="human_review",
            ),
        ]
    )
    page = reader.list_cases(
        page=1,
        page_size=1,
        route_group="backend_operation",
        route_subcategory="enablement",
        ticket_status="open",
    )
    assert page["total"] == 1
    assert page["pages"] == 1
    assert page["items"][0]["matched_execution_id"] == "exec-13119"
    assert page["facets"]["route_subcategories"] == {"enablement": 1, "quota": 1}


def test_collected_fields_allow_only_contract_keys_and_shallow_safe_values() -> None:
    safe = _safe_collected_fields(
        {
            "products": ["rtc", {"token": "hidden"}],
            "requested_limits": {
                "rtc": 50_000,
                "claim_token": "hidden",
                "metadata": {"secret": "hidden"},
            },
            "prompt_snapshot": "hidden",
            "contact_email": "not part of the quota contract",
        },
        handler="quota",
        subcategory="quota",
    )
    assert safe == {
        "products": ["rtc"],
        "requested_limits": {"rtc": 50_000},
    }
    assert _safe_collected_fields(
        {"app_id": {"token": "hidden"}},
        handler="enablement",
        subcategory="enablement",
    ) == {}


def test_postgres_reader_uses_the_runtime_environment_as_account_profile() -> None:
    settings = replace(
        _settings(),
        environment="preproduction",
        base_path="/automation/preproduction",
        db_schema="supportportal_preproduction",
        job_namespace="supportportal-preproduction",
    )
    reader = PostgresDashboardCaseReader(settings)
    cursor = MagicMock()
    cursor.fetchall.return_value = []

    reader._list_rows(
        cursor,
        zendesk_ticket_id=None,
        execution_id=None,
        execution_status=None,
        event_type=None,
    )

    parameters = cursor.execute.call_args.args[1]
    assert parameters == (
        "supportportal-preproduction",
        "preproduction",
        "supportportal-preproduction",
        "supportportal-preproduction",
    )
