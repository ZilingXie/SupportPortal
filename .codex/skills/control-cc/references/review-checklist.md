# Review Checklist

Codex accepts or rejects Claude Code work from evidence, not from the report's confidence.

## Required Review

Inspect:

```bash
git diff --stat
git diff -- <changed-files>
```

For candidate worktrees, inspect the candidate diff before exporting the patch, then inspect the real PR task worktree again after `git apply --3way`.

Check:

- The diff satisfies the current implementation plan and acceptance criteria.
- The change is the smallest practical implementation for the PR slice.
- No unrelated refactor, formatting churn, dependency churn, generated noise, or hidden behavior change is present.
- Public APIs, schemas, config, prompts, model behavior, and data contracts are unchanged unless the plan explicitly requires them.
- Verification command and result are present and credible, or the blocker is concrete.
- Nearby call sites around modified code still satisfy the old contract.
- New or adjusted tests cover the failure or behavior when practical.

## When To Expand Review

Read beyond changed files when there is evidence of:

- security, auth, payment, or secrets risk
- data migration, destructive operation, or data loss risk
- concurrency, ordering, idempotency, or consistency risk
- public API, schema, prompt, model, RAG, config, deployment, or restart changes
- test coverage gaps on the critical path
- worker failure, speculative edits, or broad refactoring

## Rejection Or Codex Fix Signals

Reject the patch, ask for one correction, or have Codex fix directly when:

- it changes behavior outside the goal
- it hides failure by weakening tests or validation
- it rewrites a subsystem to fix a local bug
- it leaves verification unrun without a concrete blocker
- it introduces TODOs, dead code, or temporary debug output
- it changes global skills or user-level configuration
- it cannot explain meaningful risk or uncertainty
