from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from redis import Redis as SyncRedis

LOGGER = logging.getLogger(__name__)
DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def _redis_url() -> str:
    return (os.getenv("REDIS_URL") or "").strip()


def _cache_ttl_seconds() -> int:
    raw = (os.getenv("RAG_QUERY_EXPANSION_CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_CACHE_TTL_SECONDS
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_CACHE_TTL_SECONDS
    return parsed if parsed > 0 else DEFAULT_CACHE_TTL_SECONDS


def build_query_expansion_cache_key(
    *,
    normalized_query: str,
    query_profile: str,
    query_understanding_version: str,
    glossary_version: str,
    prompt_model_version: str,
) -> str:
    payload = {
        "normalized_query": " ".join(str(normalized_query or "").split()).strip().lower(),
        "query_profile": str(query_profile or "").strip(),
        "query_understanding_version": str(query_understanding_version or "").strip(),
        "glossary_version": str(glossary_version or "").strip(),
        "prompt_model_version": str(prompt_model_version or "").strip(),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"support:rag:query-expansion:{digest}"


class QueryExpansionCache:
    def __init__(self, redis_url: str | None = None, ttl_seconds: int | None = None) -> None:
        self._redis_url = (redis_url or _redis_url()).strip()
        self._ttl_seconds = max(1, int(ttl_seconds or _cache_ttl_seconds()))
        self._redis: SyncRedis | None = None

    def is_enabled(self) -> bool:
        return bool(self._redis_url)

    def _client(self) -> SyncRedis | None:
        if not self.is_enabled():
            return None
        if self._redis is None:
            self._redis = SyncRedis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def get_json(self, key: str) -> dict[str, Any] | None:
        client = self._client()
        if client is None:
            return None
        try:
            payload = client.get(key)
        except Exception as exc:
            LOGGER.warning("Query expansion cache get failed: %s", exc)
            return None
        if not payload:
            return None
        try:
            parsed = json.loads(payload)
        except Exception:
            LOGGER.warning("Query expansion cache payload is not valid JSON")
            return None
        return parsed if isinstance(parsed, dict) else None

    def set_json(self, key: str, payload: dict[str, Any]) -> bool:
        client = self._client()
        if client is None:
            return False
        try:
            client.setex(key, self._ttl_seconds, json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return True
        except Exception as exc:
            LOGGER.warning("Query expansion cache set failed: %s", exc)
            return False

    def close(self) -> None:
        if self._redis is None:
            return
        try:
            self._redis.close()
        except Exception:
            pass
        self._redis = None
