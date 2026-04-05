from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


if importlib.util.find_spec("redis") is None:
    redis_module = types.ModuleType("redis")
    redis_asyncio_module = types.ModuleType("redis.asyncio")

    class _PlaceholderRedis:
        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> "_PlaceholderRedis":
            return cls()

    redis_module.Redis = _PlaceholderRedis
    redis_asyncio_module.Redis = _PlaceholderRedis
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio_module


from backend.services import task_queue


class _FakeAsyncRedisClient:
    def __init__(self) -> None:
        self.rpush_calls: list[tuple[str, str]] = []

    async def rpush(self, queue_name: str, payload: str) -> int:
        self.rpush_calls.append((queue_name, payload))
        return 1

    async def aclose(self) -> None:
        return None


class _FakeAsyncRedisFactory:
    clients: list[_FakeAsyncRedisClient] = []

    @classmethod
    def from_url(cls, *_args: object, **_kwargs: object) -> _FakeAsyncRedisClient:
        client = _FakeAsyncRedisClient()
        cls.clients.append(client)
        return client


class _FakeSyncRedisClient:
    def __init__(self) -> None:
        self.blpop_calls: list[tuple[object, int]] = []

    def blpop(self, queue_names: object, timeout: int) -> None:
        self.blpop_calls.append((queue_names, timeout))
        return None

    def close(self) -> None:
        return None


class _FakeSyncRedisFactory:
    clients: list[_FakeSyncRedisClient] = []

    @classmethod
    def from_url(cls, *_args: object, **_kwargs: object) -> _FakeSyncRedisClient:
        client = _FakeSyncRedisClient()
        cls.clients.append(client)
        return client


class TaskQueueRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeAsyncRedisFactory.clients.clear()
        _FakeSyncRedisFactory.clients.clear()

    def test_async_queue_routes_query_and_aux_tasks_to_separate_queues(self) -> None:
        with patch.object(task_queue, "AsyncRedis", _FakeAsyncRedisFactory), patch.dict(
            os.environ,
            {
                "REDIS_URL": "redis://example.test/0",
                "TICKET_QUERY_QUEUE_NAME": "support.ticket_queries",
                "TICKET_AUX_QUEUE_NAME": "support.ticket_aux",
            },
            clear=False,
        ):
            queue = task_queue.AsyncRedisTaskQueue()
            asyncio.run(queue.enqueue({"task_type": "ticket_query", "ticket_id": "TK-1"}))
            asyncio.run(queue.enqueue({"task_type": "ticket_message_sentiment", "ticket_id": "TK-1"}))
            asyncio.run(queue.close())

        self.assertEqual(len(_FakeAsyncRedisFactory.clients), 1)
        client = _FakeAsyncRedisFactory.clients[0]
        self.assertEqual(client.rpush_calls[0][0], "support.ticket_queries")
        self.assertEqual(client.rpush_calls[1][0], "support.ticket_aux")

    def test_sync_queue_polls_only_selected_query_queue(self) -> None:
        with patch.object(task_queue, "SyncRedis", _FakeSyncRedisFactory), patch.dict(
            os.environ,
            {
                "REDIS_URL": "redis://example.test/0",
                "TICKET_QUERY_QUEUE_NAME": "support.ticket_queries",
                "TICKET_AUX_QUEUE_NAME": "support.ticket_aux",
            },
            clear=False,
        ):
            queue = task_queue.SyncRedisTaskQueue(task_types=("ticket_query",))
            queue.dequeue(timeout_seconds=7)
            queue.close()

        self.assertEqual(len(_FakeSyncRedisFactory.clients), 1)
        client = _FakeSyncRedisFactory.clients[0]
        self.assertEqual(client.blpop_calls[0][0], ["support.ticket_queries"])
        self.assertEqual(client.blpop_calls[0][1], 7)
