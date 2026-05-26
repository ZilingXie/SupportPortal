---
paths: ["**/*.py"]
---

# Python Coding Style

## Core Principles

- Follow PEP 8 as the baseline style guide
- Use `black` for automatic formatting (line length 88)
- Use `ruff` for linting (replaces flake8, isort, pylint)
- Use `mypy` for type checking (strict mode where possible)

## Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Modules | lowercase_underscore | `user_service.py` |
| Classes | PascalCase | `UserRepository` |
| Functions | lowercase_underscore | `get_user_by_id()` |
| Variables | lowercase_underscore | `user_count` |
| Constants | UPPERCASE_UNDERSCORE | `MAX_CONNECTIONS` |
| Private members | _leading_underscore | `_internal_cache` |

## Imports

```python
# Standard library
import os
from pathlib import Path

# Third-party
from fastapi import FastAPI
from pydantic import BaseModel

# Local
from app.core.config import settings
from app.models.user import User
```

- Never use `from module import *`
- Avoid circular imports; use late imports or restructure modules
- Group imports: stdlib, third-party, local

## Type Hints

- All public functions must have type annotations
- Use `| None` over `Optional[X]` (Python 3.10+)
- Use built-in generics: `list[X]` not `List[X]`

## Code Structure

- Max line length: 88 (black default)
- Max function length: ~50 lines
- Max file length: ~500 lines
- Prefer many small, focused modules over few large ones
