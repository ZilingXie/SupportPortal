---
name: api-design
description: REST API design patterns including resource naming, status codes, pagination, filtering, error responses, versioning, and rate limiting for production APIs.
origin: ECC
---

# API Design Patterns

Conventions and best practices for designing consistent, developer-friendly REST APIs.

## When to Activate

- Designing new API endpoints
- Reviewing existing API contracts
- Adding pagination, filtering, or sorting
- Implementing error handling for APIs
- Planning API versioning strategy
- Building public or partner-facing APIs

## Resource Design

### URL Structure

```
# Resources are nouns, plural, lowercase, kebab-case
GET    /api/v1/users
GET    /api/v1/users/:id
POST   /api/v1/users
PUT    /api/v1/users/:id
PATCH  /api/v1/users/:id
DELETE /api/v1/users/:id

# Sub-resources for relationships
GET    /api/v1/users/:id/orders

# Actions that don't map to CRUD
POST   /api/v1/orders/:id/cancel
POST   /api/v1/auth/login
```

## HTTP Methods and Status Codes

### Method Semantics

| Method | Idempotent | Safe | Use For |
|--------|-----------|------|---------|
| GET | Yes | Yes | Retrieve resources |
| POST | No | No | Create resources, trigger actions |
| PUT | Yes | No | Full replacement |
| PATCH | No* | No | Partial update |
| DELETE | Yes | No | Remove a resource |

### Status Code Reference

```
200 OK             — GET, PUT, PATCH (with response body)
201 Created        — POST (include Location header)
204 No Content     — DELETE, PUT (no response body)
400 Bad Request    — Validation failure, malformed JSON
401 Unauthorized   — Missing or invalid authentication
403 Forbidden      — Authenticated but not authorized
404 Not Found      — Resource doesn't exist
409 Conflict       — Duplicate entry, state conflict
422 Unprocessable  — Semantically invalid
429 Too Many       — Rate limit exceeded
500 Internal Error — Unexpected failure
502 Bad Gateway    — Upstream service failed
503 Unavailable    — Temporary overload, include Retry-After
```

## Response Format

### Success Response

```json
{
  "data": {
    "id": "abc-123",
    "email": "alice@example.com",
    "name": "Alice",
    "created_at": "2025-01-15T10:30:00Z"
  }
}
```

### Collection Response (with Pagination)

```json
{
  "data": [...],
  "meta": {
    "total": 142,
    "page": 1,
    "per_page": 20,
    "total_pages": 8
  },
  "links": {
    "self": "/api/v1/users?page=1&per_page=20",
    "next": "/api/v1/users?page=2&per_page=20",
    "last": "/api/v1/users?page=8&per_page=20"
  }
}
```

### Error Response

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [
      {"field": "email", "message": "Must be a valid email address", "code": "invalid_format"}
    ]
  }
}
```

## Pagination

### Offset-Based
```
GET /api/v1/users?page=2&per_page=20
```
Pros: Easy to implement, supports "jump to page N"
Cons: Slow on large offsets, inconsistent with concurrent inserts

### Cursor-Based
```
GET /api/v1/users?cursor=eyJpZCI6MTIzfQ&limit=20
```
Pros: Consistent performance, stable with concurrent inserts
Cons: Cannot jump to arbitrary page

## Filtering, Sorting, and Search

```
# Simple equality
GET /api/v1/orders?status=active

# Comparison operators
GET /api/v1/products?price[gte]=10&price[lte]=100

# Sorting (prefix - for descending)
GET /api/v1/products?sort=-created_at

# Full-text search
GET /api/v1/products?q=wireless+headphones
```

## Authentication and Authorization

- Bearer token in Authorization header: `Authorization: Bearer eyJ...`
- API key for server-to-server: `X-API-Key: sk_live_abc123`
- Check resource ownership before returning data
- Use role-based checks for privileged operations

## Rate Limiting

```
HTTP/1.1 200 OK
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1640000000

HTTP/1.1 429 Too Many Requests
Retry-After: 60
```

## Versioning

Use URL path versioning: `/api/v1/users`, `/api/v2/users`

Strategy:
1. Start with /api/v1/
2. Maintain at most 2 active versions
3. Non-breaking changes don't need new versions (adding fields, new optional params, new endpoints)
4. Breaking changes require new versions (removing/renaming fields, changing types, changing auth)

## API Design Checklist

- [ ] Resource URL follows naming conventions (plural, kebab-case, no verbs)
- [ ] Correct HTTP method used
- [ ] Appropriate status codes returned
- [ ] Input validated with schema (Pydantic, Zod, etc.)
- [ ] Error responses follow standard format
- [ ] Pagination implemented for list endpoints
- [ ] Authentication required (or explicitly public)
- [ ] Authorization checked
- [ ] Rate limiting configured
- [ ] Response does not leak internal details
- [ ] Consistent naming with existing endpoints
- [ ] Documented (OpenAPI/Swagger)
