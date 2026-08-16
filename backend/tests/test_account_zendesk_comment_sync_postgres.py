from __future__ import annotations

import unittest

from backend.repositories.ticket_repository import PostgresTicketRepository
from backend.services.account_zendesk_comments import normalize_snapshot


class _Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._last_query = ""

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
            return ("AC-PG-12620",)
        if "support_account_case_comment_sync_state" in self._last_query:
            return None
        return None

    def fetchall(self):
        if "SELECT zendesk_comment_id FROM" in self._last_query:
            return []
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()

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


class AccountZendeskCommentPostgresTests(unittest.TestCase):
    def test_sync_comment_insert_and_state_queries_use_expected_parameter_counts(self) -> None:
        repository = PostgresTicketRepository("postgresql://example.invalid/test")
        connection = _Connection()
        repository._run_with_connection_retry = lambda _operation_name, operation: operation(connection)  # type: ignore[method-assign]
        snapshot = normalize_snapshot(
            {
                "source_updated_at": "2026-08-16T02:00:00Z",
                "snapshot_complete": True,
                "comments": [
                    {
                        "id": "100",
                        "public": False,
                        "author": {"id": "agent-1", "name": "Agent", "role": "agent"},
                        "body": "Internal note",
                        "created_at": "2026-08-16T01:10:00Z",
                    }
                ],
            }
        )

        result = repository.sync_account_case_comments(
            ticket_id="12620",
            account_case_id="AC-PG-12620",
            snapshot=snapshot,
            synced_at="2026-08-16T02:01:00+00:00",
        )

        self.assertEqual(result["status"], "synced")
        comment_insert = next(
            (params for query, params in connection.cursor_instance.calls if "INSERT INTO" in query and "zendesk_comment_id" in query),
            None,
        )
        self.assertIsNotNone(comment_insert)
        assert comment_insert is not None
        self.assertEqual(len(comment_insert), 12)
        state_insert = next(
            (params for query, params in connection.cursor_instance.calls if "support_account_case_comment_sync_state" in query and "INSERT INTO" in query),
            None,
        )
        self.assertIsNotNone(state_insert)
        assert state_insert is not None
        self.assertEqual(len(state_insert), 7)


if __name__ == "__main__":
    unittest.main()
