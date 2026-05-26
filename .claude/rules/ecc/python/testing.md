---
paths: ["**/*.py"]
---

# Python Testing Guidelines

## Framework

- Use `pytest` as the primary test framework
- Use `pytest-asyncio` for async tests
- Use `httpx.ASGITransport` for FastAPI test clients
- Use `pytest-cov` for coverage reporting

## Test Structure

```
tests/
├── conftest.py          # Shared fixtures
├── test_models.py       # Unit tests
├── test_services.py     # Service layer tests
├── test_api/            # API integration tests
│   ├── test_users.py
│   └── test_auth.py
└── factories/           # Test data factories
```

## Coverage Target

- 80%+ line coverage
- 80%+ branch coverage
- Critical paths: 100%

## Naming Conventions

- Test files: `test_<module>.py`
- Test functions: `test_<what>_<condition>_<expected>()`
- Example: `test_create_user_with_duplicate_email_raises_conflict()`

## Best Practices

- Tests must be independent (no shared state between tests)
- Use fixtures for setup, not class inheritance
- Mock external services (APIs, Redis, S3) with `unittest.mock` or `pytest-mock`
- FastAPI: override dependencies with `app.dependency_overrides`
- Test error paths, not just happy paths
- Use parametrize for edge case coverage

```python
@pytest.mark.parametrize("email,is_valid", [
    ("user@example.com", True),
    ("invalid", False),
    ("", False),
    (None, False),
])
def test_email_validation(email, is_valid):
    ...
```

## Running Tests

```bash
pytest                              # All tests
pytest tests/test_services.py       # Specific file
pytest -k "test_create_user"        # Name filter
pytest --cov --cov-report=term      # With coverage
pytest -x                           # Stop on first failure
```
