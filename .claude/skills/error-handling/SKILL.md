---
name: error-handling
description: Patterns for robust error handling across TypeScript, Python, and Go. Covers typed errors, error boundaries, retries, circuit breakers, and user-facing error messages.
origin: ECC
---

# Error Handling Patterns

Consistent, robust error handling patterns for production applications.

## When to Activate

- Designing error types or exception hierarchies for a new module or service
- Adding retry logic or circuit breakers for unreliable external dependencies
- Reviewing API endpoints for missing error handling
- Implementing user-facing error messages and feedback
- Debugging cascading failures or silent error swallowing

## Core Principles

1. **Fail fast and loudly** — surface errors at the boundary where they occur; don't bury them
2. **Typed errors over string messages** — errors are first-class values with structure
3. **User messages != developer messages** — show friendly text to users, log full context server-side
4. **Never swallow errors silently** — every `catch` block must either handle, re-throw, or log
5. **Errors are part of your API contract** — document every error code a client may receive

## Python

### Custom Exception Hierarchy

```python
class AppError(Exception):
    """Base application error."""
    def __init__(self, message: str, code: str, status_code: int = 500):
        super().__init__(message)
        self.code = code
        self.status_code = status_code

class NotFoundError(AppError):
    def __init__(self, resource: str, id: str):
        super().__init__(f"{resource} not found: {id}", "NOT_FOUND", 404)

class ValidationError(AppError):
    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message, "VALIDATION_ERROR", 422)
        self.details = details or []
```

### FastAPI Global Exception Handler

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": str(exc)}},
    )

@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unexpected error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}},
    )
```

## Retry with Exponential Backoff

```python
import asyncio

async def with_retry(fn, max_attempts=3, base_delay_ms=500, max_delay_ms=10000):
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as e:
            if attempt == max_attempts:
                raise
            jitter = random.random() * base_delay_ms
            delay = min(base_delay_ms * (2 ** (attempt - 1)) + jitter, max_delay_ms)
            await asyncio.sleep(delay / 1000)
```

## User-Facing Error Messages

Map error codes to human-readable messages. Keep technical details out of user-visible text.

```python
USER_ERROR_MESSAGES = {
    "NOT_FOUND": "The requested item could not be found.",
    "UNAUTHORIZED": "Please sign in to continue.",
    "FORBIDDEN": "You don't have permission to do that.",
    "VALIDATION_ERROR": "Please check your input and try again.",
    "RATE_LIMITED": "Too many requests. Please wait a moment and try again.",
    "INTERNAL_ERROR": "Something went wrong on our end. Please try again later.",
}
```

## Error Handling Checklist

- [ ] Every `catch` block handles, re-throws, or logs — no silent swallowing
- [ ] API errors follow the standard envelope `{ error: { code, message } }`
- [ ] User-facing messages contain no stack traces or internal details
- [ ] Full error context is logged server-side
- [ ] Custom error classes extend a base `AppError` with a `code` field
- [ ] Retry logic only retries retriable errors (not 4xx client errors)
