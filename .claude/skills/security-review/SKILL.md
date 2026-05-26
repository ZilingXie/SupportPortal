---
name: security-review
description: Security review workflow covering secrets management, input validation, SQL injection, XSS, CSRF, authentication, authorization, rate limiting, and dependency auditing.
origin: ECC
---

# Security Review

Systematic security review covering the OWASP Top 10 and common vulnerability patterns.

## When to Activate

- Building features involving authentication or user input
- Handling secrets, API keys, or sensitive data
- Integrating payment systems
- Storing or transmitting PII
- Adding third-party API integrations
- Pre-deployment security audit

## Core Security Sections

### 1. Secrets Management

**FAIL**: Hardcoded API keys or passwords in source code.
**PASS**: Reading secrets from environment variables with explicit missing-value errors.

Checklist:
- [ ] No hardcoded secrets in source code
- [ ] All secrets read from environment variables
- [ ] `.env` and `.env.local` excluded via `.gitignore`
- [ ] Production secrets stored in hosting platform secret manager
- [ ] No secrets in git history

### 2. Input Validation

**FAIL**: Unvalidated user input passed directly to backend logic.
**PASS**: Schema-based validation (Pydantic/Zod) with structured error responses.

Checklist:
- [ ] All user inputs validated with schema
- [ ] File uploads restricted by size, MIME type, and extension
- [ ] No direct user input in queries
- [ ] Whitelist validation (not blacklist)
- [ ] Error messages don't leak internal details

### 3. SQL Injection Prevention

**FAIL**: String interpolation building SQL queries (f-strings or concatenation).
**PASS**: Parameterized queries via ORM or `$1`-style placeholders.

Checklist:
- [ ] All queries parameterized
- [ ] Zero string concatenation in SQL
- [ ] ORM/query builder used correctly

### 4. Authentication & Authorization

**FAIL**: Storing tokens in localStorage (XSS-vulnerable).
**PASS**: httpOnly, Secure, SameSite=Strict cookies.

Checklist:
- [ ] Tokens in httpOnly cookies, not localStorage
- [ ] Authorization checked before sensitive operations
- [ ] Row Level Security enabled on database tables
- [ ] RBAC implemented for privileged operations

### 5. XSS Prevention

**FAIL**: Rendering unsanitized user HTML.
**PASS**: Sanitize with DOMPurify or framework built-in protection.

Checklist:
- [ ] User HTML sanitized before rendering
- [ ] Content Security Policy headers configured
- [ ] No unvalidated dynamic content in DOM

### 6. CSRF Protection

**FAIL**: State-changing endpoints without CSRF protection.
**PASS**: CSRF tokens on all state-changing requests; SameSite=Strict cookies.

Checklist:
- [ ] CSRF tokens on POST/PUT/PATCH/DELETE
- [ ] SameSite=Strict on session cookies
- [ ] Double-submit cookie pattern where applicable

### 7. Rate Limiting

**FAIL**: No rate limiting on API endpoints.
**PASS**: Rate limits with configurable windows per endpoint.

Checklist:
- [ ] Rate limiting on all API endpoints
- [ ] Stricter limits for auth and expensive operations
- [ ] IP-based and user-based limiting

### 8. Sensitive Data Exposure

**FAIL**: Logging passwords, tokens, or full credit card numbers.
**PASS**: Redact sensitive fields; log only non-sensitive identifiers.

Checklist:
- [ ] No secrets/passwords/tokens in logs
- [ ] Generic user-facing error messages
- [ ] Detailed errors logged server-side only
- [ ] No stack traces exposed to clients

### 9. Dependency Security

```bash
pip list --outdated          # Check outdated packages
pip-audit                    # Audit for known vulnerabilities
```

Checklist:
- [ ] Dependencies up to date
- [ ] No known vulnerabilities
- [ ] Lock files committed
- [ ] Dependabot or similar enabled

## Pre-Deployment Security Checklist

- [ ] Secrets in environment variables, not code
- [ ] Input validation on all endpoints
- [ ] SQL injection prevention verified
- [ ] XSS protections in place
- [ ] CSRF tokens on state-changing operations
- [ ] Authentication required where needed
- [ ] Authorization checks before sensitive operations
- [ ] Rate limiting configured
- [ ] HTTPS enforced
- [ ] Security headers set (CSP, X-Frame-Options, etc.)
- [ ] Error handling doesn't leak internals
- [ ] Logging redacts sensitive data
- [ ] Dependencies audited and current
- [ ] CORS configured restrictively

## Security Testing Patterns

- **Auth test**: Assert 401 on unprotected endpoints
- **Authorization test**: Assert 403 for insufficient permissions
- **Input validation test**: Assert 400/422 on malformed input
- **Rate limit test**: Verify 429 after exceeding limits

## Guiding Principle

When in doubt, err on the side of caution. Security is mandatory, not optional. A single vulnerability can compromise the entire platform.
