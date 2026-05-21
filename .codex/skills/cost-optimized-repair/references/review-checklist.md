# Review Checklist

Codex accepts or rejects the worker result from evidence, not from the worker's confidence.

## Required Review

Run or inspect:

```bash
git diff --stat
git diff -- <changed-files>
```

Then check:

- Changed files match the payload scope.
- Diff is the smallest correct change for the stated goal.
- No unrelated refactor, formatting churn, dependency churn, or generated noise.
- Public APIs, schemas, config, prompts, model behavior, and data contracts are unchanged unless explicitly required.
- Verification command and result are present and credible.
- Nearby call sites around modified code still satisfy the old contract.
- New or adjusted tests cover the failure when practical.

## When To Expand Review

Read more than the changed files only when there is evidence of:

- security, auth, or payment risk
- data migration or data loss risk
- concurrency, ordering, or consistency risk
- public API, schema, or config changes
- test coverage gaps on the critical path
- unclear ownership of a changed subsystem
- worker failure, speculative edits, or broad refactoring

## Rejection Signals

Reject or correct the worker result when:

- it changes behavior outside the goal
- it hides failure by weakening tests or validation
- it rewrites a subsystem to fix a local bug
- it leaves verification unrun without a concrete blocker
- it introduces TODOs, dead code, or temporary debug output
- it changes global skills or user-level configuration
