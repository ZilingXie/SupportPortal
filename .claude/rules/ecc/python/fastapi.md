---
paths: ["**/*.py"]
---

# FastAPI Rules

## App Structure

- Use `create_app()` factory pattern with lifespan context manager
- Register routers, middleware, exception handlers in the factory
- Keep route handlers thin — delegate to services and dependencies

## Pydantic Schemas

- Separate request (Create/Update) and response models
- Response models must never expose passwords, tokens, or internal state
- Use `model_config = ConfigDict(from_attributes=True)` for ORM mode
- Validate all inputs with Pydantic models

## Dependencies

- Use `Depends` for database sessions, auth, and request-scoped resources
- Never create sessions or clients inline in route handlers
- Override dependencies in tests using `app.dependency_overrides`

## Async

- All route handlers should be `async def` when performing I/O
- Use async database drivers and `httpx.AsyncClient` for HTTP calls
- Never call blocking `requests` or `time.sleep` in async routes

## Error Handling

- Centralize exception handling with `@app.exception_handler`
- Define custom `ApiError` base class with status_code, code, message
- Return consistent error shape: `{"error": {"code": "...", "message": "..."}}`

## Security

- CORS origins must be environment-specific
- Never use `allow_origins=["*"]` with `allow_credentials=True`
- Validate JWT issuer, audience, expiry, and algorithm
- Rate limit auth and write-heavy endpoints
