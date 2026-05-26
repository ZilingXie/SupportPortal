---
paths: ["**/*.py"]
---

# Python Hooks Configuration Reference

Reference for configuring hooks in Claude Code settings.

## Hook Types

Python projects typically benefit from these hook patterns:

### Pre-Commit Style Hooks (via Stop hooks)
- Format check: `ruff format --check .`
- Lint check: `ruff check .`
- Type check: `mypy .`

### Post-Edit Hooks
- Run affected tests on file save
- Check for `print()` statements that should be `logging`

### Session Start
- Activate virtual environment
- Load project environment variables

## Hook Configuration

Hooks are configured in `.claude/settings.local.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "command": "ruff check . && mypy ."
      }
    ]
  }
}
```

## Best Practices

- Keep hook commands fast (<5s) for development flow
- Use `ruff` over multiple separate linters for speed
- Run heavy checks (full test suite) manually, not on every edit
- Use async hooks for slow operations
