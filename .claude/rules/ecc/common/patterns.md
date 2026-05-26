# Design Patterns (Common)

## Repository Pattern
Separate data access from business logic. Repository classes encapsulate database queries.

## Service Layer
Business logic lives in services, not in controllers/handlers. Services orchestrate repositories and external calls.

## Dependency Injection
Pass dependencies in; don't create them internally. Enables testing with mock implementations.

## Factory Pattern
Use factory functions to construct complex objects. Centralize creation logic and configuration.

## Middleware Pipeline
Process requests/responses through a chain of middleware: auth → rate limiting → validation → handler.

## Error Boundary
Catch and handle errors at system boundaries. Return consistent error shapes. Log full context server-side.

## Caching Strategies

- **Cache-Aside**: Check cache → if miss, fetch from source → populate cache
- **Write-Through**: Write to cache and source simultaneously
- **Read-Through**: Cache handles miss by loading from source

## Async Patterns

- Use async/await for I/O-bound operations
- Don't mix sync blocking calls in async code
- Use connection pools for databases and HTTP clients
- Timeout all external calls
