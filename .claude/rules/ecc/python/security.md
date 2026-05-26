---
paths: ["**/*.py"]
---

# Python Security Guidelines

## Secrets Management

- Never hardcode API keys, tokens, or passwords
- Use environment variables via `os.environ` or Pydantic `BaseSettings`
- `.env` files must be in `.gitignore`
- Rotate secrets regularly; never commit them to git

## SQL Injection Prevention

```python
# BAD: f-string interpolation
query = f"SELECT * FROM users WHERE email = '{email}'"

# GOOD: Parameterized query
result = await session.execute(
    select(User).where(User.email == email)
)

# GOOD: Raw SQL with parameters
result = await session.execute(
    text("SELECT * FROM users WHERE email = :email"),
    {"email": email}
)
```

## Input Validation

- Validate all user input with Pydantic models
- Use strict types and constraints (min_length, max_length, regex patterns)
- File uploads: check size, MIME type, and extension
- Never use `pickle.load()` on untrusted data
- Use `yaml.safe_load()` not `yaml.load()`

## Authentication & Authorization

- Hash passwords with bcrypt or argon2
- Validate JWT signature, expiry, issuer, and audience
- Check authorization before executing sensitive operations
- Use `httpOnly`, `Secure`, `SameSite=Strict` cookies for sessions

## Safe Deserialization

- Avoid `pickle` entirely for untrusted data
- Use `json.loads()` for JSON
- Use `yaml.safe_load()` for YAML
- Validate deserialized data against a schema

## Command Injection Prevention

```python
# BAD: shell=True with user input
subprocess.run(f"convert {user_file} output.pdf", shell=True)

# GOOD: List arguments, no shell
subprocess.run(["convert", user_file, "output.pdf"])
```

## Logging

- Never log passwords, tokens, or full credit card numbers
- Redact PII from log output
- Use structured logging (JSON format) for production

## Dependencies

- Run `pip-audit` regularly
- Pin dependencies with exact versions in production
- Review dependency licenses for compliance
