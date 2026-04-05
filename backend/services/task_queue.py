from __future__ import annotations

import json
import logging
import os
from typing import Any

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis

LOGGER = logging.getLogger(__name__)
SUPPORTED_TASK_TYPES = ("ticket_query", "ticket_message_sentiment")


def _redis_url() -> str:
    return (os.getenv("REDIS_URL") or "").strip()


def _legacy_queue_name() -> str:
    return (os.getenv("TASK_QUEUE_NAME") or "").strip()


def _ticket_query_queue_name() -> str:
    return (os.getenv("TICKET_QUERY_QUEUE_NAME") or "").strip() or _legacy_queue_name() or "support.ticket_queries"


def _ticket_aux_queue_name() -> str:
    return (os.getenv("TICKET_AUX_QUEUE_NAME") or "").strip() or _legacy_queue_name() or "support.ticket_aux"


def _normalize_task_types(task_types: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in task_types or []:
        value = str(item or "").strip().lower()
        if value in SUPPORTED_TASK_TYPES and value not in normalized:
            normalized.append(value)
    return tuple(normalized) if normalized else tuple(SUPPORTED_TASK_TYPES)


def _queue_name_for_task_type(task_type: Any) -> str:
    normalized = str(task_type or "").strip().lower()
    if normalized == "ticket_query":
        return _ticket_query_queue_name()
    if normalized == "ticket_message_sentiment":
        return _ticket_aux_queue_name()
    return _legacy_queue_name() or _ticket_query_queue_name()


def _queue_names(queue_name: str | None = None, task_types: tuple[str, ...] | list[str] | None = None) -> tuple[str, ...]:
    explicit_queue = (queue_name or "").strip()
    if explicit_queue:
        return (explicit_queue,)
    names: list[str] = []
    for task_type in _normalize_task_types(task_types):
        candidate = _queue_name_for_task_type(task_type)
        if candidate and candidate not in names:
            names.append(candidate)
    return tuple(names)


class AsyncRedisTaskQueue:
    def __init__(
        self,
        redis_url: str | None = None,
        queue_name: str | None = None,
        task_types: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self._redis_url = (redis_url or _redis_url()).strip()
        self._queue_names = _queue_names(queue_name, task_types)
        self._redis: AsyncRedis | None = None

    def is_enabled(self) -> bool:
        return bool(self._redis_url and self._queue_names)

    async def _client(self) -> AsyncRedis | None:
        if not self.is_enabled():
            return None
        if self._redis is None:
            self._redis = AsyncRedis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _target_queue_name(self, task: dict[str, Any]) -> str:
        if len(self._queue_names) == 1:
            return self._queue_names[0]
        return _queue_name_for_task_type(task.get("task_type"))

    async def enqueue(self, task: dict[str, Any]) -> bool:
        client = await self._client()
        if client is None:
            return False
        try:
            await client.rpush(self._target_queue_name(task), json.dumps(task, ensure_ascii=False))
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
    def __init__(
        self,
        redis_url: str | None = None,
        queue_name: str | None = None,
        task_types: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self._redis_url = (redis_url or _redis_url()).strip()
        self._queue_names = _queue_names(queue_name, task_types)
        self._redis: SyncRedis | None = None

    def is_enabled(self) -> bool:
        return bool(self._redis_url and self._queue_names)

    def _client(self) -> SyncRedis | None:
        if not self.is_enabled():
            return None
        if self._redis is None:
            self._redis = SyncRedis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _target_queue_name(self, task: dict[str, Any]) -> str:
        if len(self._queue_names) == 1:
            return self._queue_names[0]
        return _queue_name_for_task_type(task.get("task_type"))

    def enqueue(self, task: dict[str, Any]) -> bool:
        client = self._client()
        if client is None:
            return False
        try:
            client.rpush(self._target_queue_name(task), json.dumps(task, ensure_ascii=False))
            return True
        except Exception as exc:
            LOGGER.warning("Task enqueue failed: %s", exc)
            return False

    def dequeue(self, timeout_seconds: int = 5) -> dict[str, Any] | None:
        client = self._client()
        if client is None:
            return None
        try:
            item = client.blpop(list(self._queue_names), timeout=max(1, int(timeout_seconds)))
        except Exception as exc:
            LOGGER.warning("Task dequeue failed: %s", exc)
            return None
        if not item:
            return None
        raw_payload = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item
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
