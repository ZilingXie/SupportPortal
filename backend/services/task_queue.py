from __future__ import annotations

import json
import logging
import os
from typing import Any

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis

LOGGER = logging.getLogger(__name__)


def _redis_url() -> str:
    return (os.getenv("REDIS_URL") or "").strip()


def _queue_name() -> str:
    return (os.getenv("TASK_QUEUE_NAME") or "support.tasks").strip()


class AsyncRedisTaskQueue:
    def __init__(self, redis_url: str | None = None, queue_name: str | None = None) -> None:
        self._redis_url = (redis_url or _redis_url()).strip()
        self._queue_name = (queue_name or _queue_name()).strip()
        self._redis: AsyncRedis | None = None

    def is_enabled(self) -> bool:
        return bool(self._redis_url and self._queue_name)

    async def _client(self) -> AsyncRedis | None:
        if not self.is_enabled():
            return None
        if self._redis is None:
            self._redis = AsyncRedis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def enqueue(self, task: dict[str, Any]) -> bool:
        client = await self._client()
        if client is None:
            return False
        try:
            await client.rpush(self._queue_name, json.dumps(task, ensure_ascii=False))
            return True
        except Exception as exc:
            LOGGER.warning("Task enqueue failed: %s", exc)
            return False

    async def close(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.aclose()
        except Exception:
            pass
        self._redis = None


class SyncRedisTaskQueue:
    def __init__(self, redis_url: str | None = None, queue_name: str | None = None) -> None:
        self._redis_url = (redis_url or _redis_url()).strip()
        self._queue_name = (queue_name or _queue_name()).strip()
        self._redis: SyncRedis | None = None

    def is_enabled(self) -> bool:
        return bool(self._redis_url and self._queue_name)

    def _client(self) -> SyncRedis | None:
        if not self.is_enabled():
            return None
        if self._redis is None:
            self._redis = SyncRedis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def dequeue(self, timeout_seconds: int = 5) -> dict[str, Any] | None:
        client = self._client()
        if client is None:
            return None
        try:
            item = client.blpop(self._queue_name, timeout=max(1, int(timeout_seconds)))
        except Exception as exc:
            LOGGER.warning("Task dequeue failed: %s", exc)
            return None
        if not item:
            return None
        raw_payload = item[1]
        try:
            payload = json.loads(raw_payload)
        except Exception:
            LOGGER.warning("Invalid task payload: %s", raw_payload)
            return None
        return payload if isinstance(payload, dict) else None

    def close(self) -> None:
        if self._redis is None:
            return
        try:
            self._redis.close()
        except Exception:
            pass
        self._redis = None
