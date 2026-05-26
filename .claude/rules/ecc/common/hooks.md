# Hooks Best Practices

## General Hook Guidelines

- Keep hooks fast: target <5s execution time
- Use async execution for slow operations
- Don't block the user on non-critical hooks
- Handle errors gracefully — a hook failure shouldn't break the session

## Recommended Hooks

### Pre-Commit Quality
Run fast checks before edits are committed:
- Linting (ruff check)
- Type checking (mypy) for changed files only

### Session Management
- SessionStart: load .env, activate venv
- SessionEnd: persist learning, update metrics

### Post-Edit
- Check for debug output left in code
- Verify file formatting

## Hook Configuration

Configure in `.claude/settings.local.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "**/*.py",
        "command": "ruff check"
      }
    ]
  }
}
```

## Anti-Patterns

- Don't run full test suites on every edit
- Don't run slow network-dependent checks synchronously
- Don't use hooks for enforcement that belongs in CI
