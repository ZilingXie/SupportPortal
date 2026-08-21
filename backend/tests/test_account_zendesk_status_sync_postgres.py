from __future__ import annotations

import unittest

from backend.repositories.ticket_repository import PostgresTicketRepository


class _Cursor:
    def __init__(self, case_row: tuple | None) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._case_row = case_row
        self._last_query = ""
        self.rowcount = 0

    @staticmethod
    def query_text(query: object) -> str:
        try:
            return query.as_string()
        except Exception:
            return str(query)

    def execute(self, query: object, params: tuple[object, ...] = ()) -> None:
        self._last_query = self.query_text(query)
        self.calls.append((self._last_query, tuple(params)))

    def fetchone(self):
        if "support_account_cases" in self._last_query and "FOR UPDATE" in self._last_query:
            return self._case_row
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _Connection:
    def __init__(self, case_row: tuple | None) -> None:
        self.cursor_instance = _Cursor(case_row)

    def cursor(self):
        return self.cursor_instance

    def transaction(self):
        connection = self

        class Transaction:
            def __enter__(self_inner):
                return connection

            def __exit__(self_inner, exc_type, exc, tb) -> bool:
                return False

        return Transaction()


CASE_ROW = (
    "AC-12896",  # account_case_id
    "AC-12896",  # billing_ticket_id
    "12896",  # client_ticket_id
    "automation",  # automation_status
    {},  # automation_context
    "open",  # zendesk_ticket_status
    "2026-08-21T09:00:00+00:00",  # zendesk_status_updated_at
    "2026-08-21T09:00:01+00:00",  # zendesk_status_synced_at
)


def _queries(connection: _Connection) -> dict[str, tuple[object, ...]]:
    return {query: params for query, params in connection.cursor_instance.calls}


class AccountZendeskStatusPostgresTests(unittest.TestCase):
    def test_solved_close_runs_case_update_ticket_close_and_audit_in_one_transaction(self) -> None:
        repository = PostgresTicketRepository("postgresql://example.invalid/test")
        connection = _Connection(CASE_ROW)
        connection.cursor_instance.rowcount = 1
        repository._run_with_connection_retry = lambda _operation_name, operation: operation(connection)  # type: ignore[method-assign]

        result = repository.update_account_case_zendesk_status(
            account_case_id="AC-12896",
            zendesk_status="solved",
            synced_at="2026-08-21T10:00:00+00:00",
            source_updated_at="2026-08-21T09:30:00Z",
        )

        self.assertEqual(result["status"], "updated")
        self.assertTrue(result["local_ticket_closed"])
        self.assertEqual(result["automation_status"], "closed")
        queries = _queries(connection)
        case_updates = [params for query, params in queries.items() if "zendesk_ticket_status=%s" in query]
        self.assertEqual(len(case_updates), 1)
        self.assertEqual(len(case_updates[0]), 7)
        self.assertEqual(case_updates[0][0], "solved")
        ticket_close = [params for query, params in queries.items() if "SET status='resolved'" in query]
        self.assertEqual(len(ticket_close), 1)
        self.assertEqual(ticket_close[0][2], "12896")
        audit_inserts = [
            params
            for query, params in queries.items()
            if "support_workspace_audit_events" in query
        ]
        self.assertEqual(len(audit_inserts), 1)
        self.assertEqual(audit_inserts[0][0], "account_zendesk_status_synced")
        self.assertEqual(audit_inserts[0][1], "zendesk_n8n")

    def test_reopen_restores_prior_automation_status_without_ticket_write(self) -> None:
        closed_row = (
            "AC-12896",
            "AC-12896",
            "12896",
            "closed",
            {"zendesk_status_sync": {"prior_automation_status": "automation", "closed_at": "2026-08-21T09:00:00+00:00"}},
            "solved",
            "2026-08-21T09:00:00+00:00",
            "2026-08-21T09:00:01+00:00",
        )
        repository = PostgresTicketRepository("postgresql://example.invalid/test")
        connection = _Connection(closed_row)
        repository._run_with_connection_retry = lambda _operation_name, operation: operation(connection)  # type: ignore[method-assign]

        result = repository.update_account_case_zendesk_status(
            account_case_id="AC-12896",
            zendesk_status="open",
            synced_at="2026-08-21T10:00:00+00:00",
            source_updated_at="2026-08-21T09:30:00Z",
        )

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["restored_automation_status"], "automation")
        queries = _queries(connection)
        self.assertFalse(any("SET status='resolved'" in query for query in queries))
        case_updates = [params for query, params in queries.items() if "zendesk_ticket_status=%s" in query]
        self.assertEqual(case_updates[0][3], "automation")

    def test_unchanged_and_stale_skip_all_writes(self) -> None:
        repository = PostgresTicketRepository("postgresql://example.invalid/test")
        unchanged_connection = _Connection(CASE_ROW)
        repository._run_with_connection_retry = lambda _operation_name, operation: operation(unchanged_connection)  # type: ignore[method-assign]
        unchanged = repository.update_account_case_zendesk_status(
            account_case_id="AC-12896",
            zendesk_status="open",
            synced_at="2026-08-21T10:00:00+00:00",
        )
        self.assertEqual(unchanged["status"], "unchanged")
        self.assertEqual(len(_queries(unchanged_connection)), 1)

        stale_connection = _Connection(CASE_ROW)
        repository._run_with_connection_retry = lambda _operation_name, operation: operation(stale_connection)  # type: ignore[method-assign]
        stale = repository.update_account_case_zendesk_status(
            account_case_id="AC-12896",
            zendesk_status="solved",
            synced_at="2026-08-21T10:00:00+00:00",
            source_updated_at="2026-08-21T08:00:00Z",
        )
        self.assertEqual(stale["status"], "stale_ignored")
        self.assertEqual(len(_queries(stale_connection)), 1)

    def test_missing_case_raises_key_error(self) -> None:
        repository = PostgresTicketRepository("postgresql://example.invalid/test")
        connection = _Connection(None)
        repository._run_with_connection_retry = lambda _operation_name, operation: operation(connection)  # type: ignore[method-assign]
        with self.assertRaises(KeyError):
            repository.update_account_case_zendesk_status(
                account_case_id="AC-missing",
                zendesk_status="open",
                synced_at="2026-08-21T10:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()
