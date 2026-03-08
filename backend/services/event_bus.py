from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis

LOGGER = logging.getLogger(__name__)


def _redis_url() -> str:
    return (os.getenv("REDIS_URL") or "").strip()


def _event_channel() -> str:
    return (os.getenv("EVENT_BUS_CHANNEL") or "support.events").strip()


class AsyncRedisEventBus:
    def __init__(self, redis_url: str | None = None, channel: str | None = None) -> None:
        self._redis_url = (redis_url or _redis_url()).strip()
        self._channel = (channel or _event_channel()).strip()
        self._redis: AsyncRedis | None = None
        self._lock = asyncio.Lock()

    def is_enabled(self) -> bool:
        return bool(self._redis_url and self._channel)

    async def _client(self) -> AsyncRedis | None:
        if not self.is_enabled():
            return None
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = AsyncRedis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def publish(self, payload: dict[str, Any]) -> bool:
        client = await self._client()
        if client is None:
            return False
        try:
            await client.publish(self._channel, json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as exc:
            LOGGER.warning("Redis publish failed: %s", exc)
            return False

    async def close(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.aclose()
        except Exception:
            pass
        self._redis = None


class SyncRedisEventBus:
    def __init__(self, redis_url: str | None = None, channel: str | None = None) -> None:
        self._redis_url = (redis_url or _redis_url()).strip()
        self._channel = (channel or _event_channel()).strip()
        self._redis: SyncRedis | None = None

    def is_enabled(self) -> bool:
        return bool(self._redis_url and self._channel)

    def _client(self) -> SyncRedis | None:
        if not self.is_enabled():
            return None
        if self._redis is None:
            self._redis = SyncRedis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def publish(self, payload: dict[str, Any]) -> bool:
        client = self._client()
        if client is None:
            return False
        try:
            client.publish(self._channel, json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as exc:
            LOGGER.warning("Redis publish failed: %s", exc)
            return False

    def close(self) -> None:
        if self._redis is None:
            return
        try:
            self._redis.close()
        except Exception:
            pass
        self._redis = None
