from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

if importlib.util.find_spec("redis") is None:
    redis_module = types.ModuleType("redis")
    redis_asyncio_module = types.ModuleType("redis.asyncio")

    class _FakeRedis:
        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> "_FakeRedis":
            return cls()

        def publish(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def blpop(self, *_args: object, **_kwargs: object) -> None:
            return None

        def rpush(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def close(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    redis_module.Redis = _FakeRedis
    redis_asyncio_module.Redis = _FakeRedis
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio_module


def _load_worker_module():
    module_path = Path(__file__).resolve().parents[1] / "worker.py"
    spec = importlib.util.spec_from_file_location(
        "backend.tests._worker_under_test",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load backend.worker for tests")

    fake_main = types.ModuleType("backend.main")
    fake_main.MANAGED_MODE = "managed"
    fake_main.TAKEOVER_MODE = "takeover"
    fake_main.build_answer = lambda *_args, **_kwargs: ("", 0.0, [], [], False)
    fake_main.build_client_sync_event = lambda *_args, **_kwargs: {}
    fake_main.build_engineer_followup_request = lambda *_args, **_kwargs: "follow up"
    fake_main.ensure_ticket_defaults = lambda _ticket: None
    fake_main.now_iso = lambda: "2026-03-22T00:00:00+00:00"
    fake_main.ticket_repository = Mock()

    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"backend.main": fake_main}):
        spec.loader.exec_module(module)
    return module


worker = _load_worker_module()


def _build_ticket(
    *,
    ticket_id: str = "T-RETRY",
    customer_message: str = "Need help with token generation",
    message_created_at: str = "2026-03-22T00:00:00+00:00",
) -> dict[str, object]:
    return {
        "ticket_id": ticket_id,
        "customer_id": "C-123",
        "requester": "Customer",
        "subject": "Token question",
        "status": "open",
        "priority": "normal",
        "engineer_mode": worker.MANAGED_MODE,
        "pending_engineer_question": None,
        "created_at": "2026-03-22T00:00:00+00:00",
        "updated_at": "2026-03-22T00:00:00+00:00",
        "messages": [
            {
                "role": "customer",
                "content": customer_message,
                "created_at": message_created_at,
            },
            {
                "role": "assistant",
                "content": "I am checking the knowledge base for you now.",
                "created_at": "2026-03-22T00:00:01+00:00",
            },
        ],
    }


class WorkerResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "task_type": "ticket_query",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

    def test_process_ticket_query_retries_transient_save_ticket_failure(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.side_effect = [
            psycopg.OperationalError("connection timeout expired"),
            None,
        ]
        repository.record_event.return_value = None
        bus = Mock()

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "build_answer",
            return_value=(
                "Use the Node.js token builder sample.",
                0.91,
                ["official/deploy-token-server.md"],
                [{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
                False,
            ),
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ), patch.object(
            worker,
            "TICKET_REPOSITORY_RETRY_MAX",
            1,
        ), patch.object(
            worker,
            "TICKET_REPOSITORY_RETRY_BASE_DELAY_SECONDS",
            0.05,
        ), patch.object(
            worker.time,
            "sleep",
        ) as sleep_mock:
            worker._process_ticket_query(bus, dict(self.task))

        self.assertEqual(repository.save_ticket.call_count, 2)
        saved_ticket = repository.save_ticket.call_args_list[-1].args[0]
        self.assertEqual(saved_ticket["messages"][-1]["content"], "Use the Node.js token builder sample.")
        self.assertEqual(repository.record_event.call_count, 1)
        sleep_mock.assert_any_call(0.05)

    def test_schedule_ticket_task_retry_reenqueues_retryable_db_failure(self) -> None:
        queue = Mock()
        queue.enqueue.return_value = True

        with patch.object(worker, "TICKET_TASK_RETRY_MAX", 2), patch.object(
            worker,
            "TICKET_TASK_RETRY_BASE_DELAY_SECONDS",
            0.5,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:02:00+00:00",
        ), patch.object(worker.time, "sleep") as sleep_mock:
            scheduled = worker._schedule_ticket_task_retry(
                queue,
                dict(self.task),
                psycopg.OperationalError("server closed the connection unexpectedly"),
            )

        self.assertTrue(scheduled)
        queue.enqueue.assert_called_once()
        retry_task = queue.enqueue.call_args.args[0]
        self.assertEqual(retry_task["worker_retry_count"], 1)
        self.assertEqual(retry_task["last_retry_at"], "2026-03-22T00:02:00+00:00")
        self.assertIn("server closed the connection unexpectedly", retry_task["last_error"])
        sleep_mock.assert_called_once_with(0.5)

    def test_process_ticket_query_skips_duplicate_final_response_after_requeue(self) -> None:
        initial_ticket = _build_ticket()
        refreshed_ticket = _build_ticket()
        refreshed_ticket["messages"].append(
            {
                "role": "assistant",
                "content": "Use the Node.js token builder sample.",
                "created_at": "2026-03-22T00:01:00+00:00",
                "sources": ["official/deploy-token-server.md"],
                "citations": [
                    {
                        "source": "official/deploy-token-server.md",
                        "label": "Deploy a token server",
                    }
                ],
            }
        )
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(refreshed_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "build_answer",
            return_value=(
                "Use the Node.js token builder sample.",
                0.91,
                ["official/deploy-token-server.md"],
                [{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
                False,
            ),
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        repository.save_ticket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
