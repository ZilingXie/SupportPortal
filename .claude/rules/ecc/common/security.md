# Security Guidelines (Common)

## Fundamental Principles

1. **Defense in depth**: Multiple layers of security controls
2. **Least privilege**: Minimum permissions needed for each component
3. **Fail securely**: Default to denying access on errors
4. **Never trust input**: Validate and sanitize all external data
5. **Keep dependencies current**: Regular updates and audits

## Secrets Management

- Never hardcode credentials, API keys, or tokens
- Use environment variables with secure defaults
- `.env` files must be in `.gitignore`
- Rotate secrets on any suspected exposure

## Input Validation

- Validate all user input at system boundaries
- Use schema validation (Pydantic, Zod, etc.)
- Whitelist allowed values; don't blacklist
- Validate file uploads: size, type, extension, content

## SQL Injection Prevention

- Always use parameterized queries
- Never concatenate user input into SQL strings
- Use ORM methods correctly (`.eq()`, `.where()` with bind parameters)

## Authentication

- Hash passwords with bcrypt, argon2, or equivalent
- Use httpOnly, Secure, SameSite cookies for sessions
- Validate JWT signature, expiry, issuer, and audience
- Implement rate limiting on auth endpoints

## Authorization

- Check permissions before every sensitive operation
- Implement RBAC for role-based access
- Enable Row-Level Security on multi-tenant database tables

## Logging & Error Handling

- Never log passwords, tokens, or PII
- Return generic error messages to clients
- Log full error context server-side
- Don't expose stack traces in API responses
