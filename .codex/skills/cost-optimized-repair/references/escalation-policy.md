# Escalation Policy

Use this policy to decide whether Codex should keep delegating or take direct control.

## Delegate

Delegate when all are true:

- The issue has a concrete expected behavior.
- The likely change is bounded to a small or medium code area.
- A narrow verification command exists.
- Codex can review the resulting diff without rereading the whole repository.
- Public APIs, schema, runtime configuration, and persistent data are unlikely to change.

## Codex Handles Directly

Codex should handle the task directly, or perform a much deeper review, when the repair touches:

- security, auth, permissions, payment, or secrets
- database migrations, destructive data operations, or backfills
- concurrency, async ordering, idempotency, or consistency boundaries
- public APIs, schemas, config, prompts, model selection, or RAG behavior
- release, deployment, compose, or restart workflows
- failures where tests cannot cover the critical path

## Worker Failure Limit

One worker correction round is allowed. Codex takes over after any of these:

- two worker rounds fail verification
- the worker reports `Blocked` twice
- the worker returns a broad refactor instead of a minimal repair
- the worker changes unrelated files
- the worker cannot explain risk or verification gaps

When Codex takes over, keep the worker's useful findings but independently verify the diff and the final test evidence.
