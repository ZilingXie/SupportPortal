# Escalation Policy

Use this policy to decide how much of a PR slice Claude Code should execute and how much Codex should handle directly.

## Good Delegation Targets

Delegate when all are true:

- The PR slice has concrete expected behavior or acceptance criteria.
- Codex can write a clear implementation plan.
- A narrow verification command exists.
- Candidate patch review is cheaper than direct implementation.
- Public APIs, schema, runtime config, prompts, model behavior, RAG data, and persistent data are unlikely to change.

## Prefer Codex Direct Work Or Deeper Review

Codex should implement directly, split further, or review much more deeply when the work touches:

- security, auth, permissions, payment, or secrets
- database migrations, destructive operations, or backfills
- concurrency, async ordering, idempotency, or consistency boundaries
- public APIs, schemas, runtime config, prompts, model selection, or RAG behavior
- release, deployment, compose, restart, or production workflow changes
- failures where tests cannot cover the critical path

These risks do not ban Claude Code entirely, but they require Codex to keep a tighter hand on design, patch review, and verification.

## Worker Failure Limit

One correction round is allowed for a plan. Codex takes over after any of these:

- two worker rounds fail verification
- the worker reports `Blocked` twice
- the worker returns broad or unrelated changes
- the worker changes global configuration or user-level files
- the worker cannot explain risk or verification gaps

When Codex takes over, keep useful findings but independently verify the final diff and test evidence.
