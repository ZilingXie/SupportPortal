---
name: backend-patterns
description: Backend architecture patterns, API design, database optimization, and server-side best practices.
origin: ECC
---

# Backend Development Patterns

Backend architecture patterns and best practices for scalable server-side applications.

## When to Activate

- Designing REST or GraphQL API endpoints
- Implementing repository, service, or controller layers
- Optimizing database queries (N+1, indexing, connection pooling)
- Adding caching (Redis, in-memory, HTTP cache headers)
- Setting up background jobs or async processing
- Structuring error handling and validation for APIs
- Building middleware (auth, logging, rate limiting)

## API Design Patterns

### RESTful API Structure

```
# Resource-based URLs
GET    /api/markets                 # List resources
GET    /api/markets/:id             # Get single resource
POST   /api/markets                 # Create resource
PUT    /api/markets/:id             # Replace resource
PATCH  /api/markets/:id             # Update resource
DELETE /api/markets/:id             # Delete resource

# Query parameters for filtering, sorting, pagination
GET /api/markets?status=active&sort=volume&limit=20&offset=0
```

### Repository Pattern

```python
from abc import ABC, abstractmethod
from typing import Optional


class MarketRepository(ABC):
    @abstractmethod
    async def find_all(self, filters: Optional[dict] = None) -> list[Market]:
        ...

    @abstractmethod
    async def find_by_id(self, id: str) -> Optional[Market]:
        ...

    @abstractmethod
    async def create(self, data: dict) -> Market:
        ...

    @abstractmethod
    async def update(self, id: str, data: dict) -> Market:
        ...

    @abstractmethod
    async def delete(self, id: str) -> None:
        ...
```

### Service Layer Pattern

Business logic separated from data access layer.

### Middleware Pattern

```python
from fastapi import Request, HTTPException


async def auth_middleware(request: Request, call_next):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    # verify token and attach user to request
    response = await call_next(request)
    return response
```

## Database Patterns

### Query Optimization

- Select only needed columns
- Use proper indexing
- Batch fetch to avoid N+1 queries
- Use transactions for multi-step operations

### N+1 Query Prevention

Use eager loading or batch fetching:
```python
# BAD: N+1
markets = await get_markets()
for market in markets:
    market.creator = await get_user(market.creator_id)

# GOOD: Batch fetch
markets = await get_markets()
creator_ids = [m.creator_id for m in markets]
creators = await get_users(creator_ids)
creator_map = {c.id: c for c in creators}
for market in markets:
    market.creator = creator_map.get(market.creator_id)
```

## Caching Strategies

### Cache-Aside Pattern

```python
async def get_market_with_cache(id: str) -> Market:
    cache_key = f"market:{id}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)
    market = await db.markets.find_by_id(id)
    if market:
        await redis.setex(cache_key, 300, json.dumps(market))
    return market
```

## Error Handling Patterns

### Centralized Error Handler

```python
class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
```

### Retry with Exponential Backoff

```python
import asyncio

async def fetch_with_retry(fn, max_retries=3):
    for i in range(max_retries):
        try:
            return await fn()
        except Exception as e:
            if i == max_retries - 1:
                raise
            await asyncio.sleep(2 ** i)
```

## Authentication & Authorization

### JWT Token Validation

### Role-Based Access Control

Define permissions per role; check at the middleware or dependency level.

## Rate Limiting

Use a shared store such as Redis for rate limiting. Do not use per-process in-memory counters for production APIs.

## Background Jobs & Queues

Use a task queue (Celery, RQ, ARQ) for async processing outside the request-response cycle.

## Logging & Monitoring

### Structured Logging

```python
import logging
import json

logger = logging.getLogger(__name__)

def log_event(level: str, message: str, **context):
    entry = {"timestamp": datetime.now().isoformat(), "level": level, "message": message, **context}
    getattr(logger, level)(json.dumps(entry))
```

**Remember**: Backend patterns enable scalable, maintainable server-side applications. Choose patterns that fit your complexity level.
