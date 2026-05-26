# Performance Guidelines

## Database

- Always index foreign keys and WHERE clause columns
- Use EXPLAIN ANALYZE to verify query plans
- Avoid N+1 queries (use eager loading or batch fetch)
- Use connection pooling
- Keep transactions short

## Caching

- Cache expensive, frequently-read data
- Set explicit TTLs based on data volatility
- Invalidate cache on write, not on timer (when possible)
- Use Redis for shared cache, not in-process memory

## Async

- Use async I/O for network and database calls
- Don't block the event loop with CPU-bound work
- Use connection pools (don't create per-request connections)

## API Performance

- Add pagination to all list endpoints
- Use cursor-based pagination for large datasets
- Enable compression for large payloads
- Set timeouts on all external HTTP calls

## Frontend

- Code-split by route
- Lazy load images and heavy components
- Debounce user input that triggers API calls
- Minimize bundle size (tree shaking, dead code elimination)

## General

- Measure before optimizing
- Profile to find actual bottlenecks
- Don't prematurely optimize
- Consider the trade-off: speed vs readability
