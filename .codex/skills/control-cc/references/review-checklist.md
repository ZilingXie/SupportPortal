# Review Checklist

Codex accepts or rejects Claude Code work from evidence, not from the report's confidence.

## Required Review

Start with the small artifacts:

```bash
cat /tmp/control-cc-runs/<thread>/pr-XX/plan-YY/review_packet.json
```

Only then expand as needed:

```bash
git diff --stat
git diff -- <changed-files>
```

For candidate worktrees, inspect the candidate packet and targeted diff before exporting the patch, then inspect the real PR task worktree again after `git apply --3way`.

Check:

- The diff satisfies the current implementation plan and acceptance criteria.
- The change is the smallest practical implementation for the PR slice.
- No unrelated refactor, formatting churn, dependency churn, generated noise, or hidden behavior change is present.
- Public APIs, schemas, config, prompts, model behavior, and data contracts are unchanged unless the plan explicitly requires them.
- Verification command and result are present and credible, or the blocker is concrete.
- Nearby call sites around modified code still satisfy the old contract.
- New or adjusted tests cover the failure or behavior when practical.

`review_packet.json` is a mechanical triage aid, not the acceptance decision. It should include changed files, diff stat, artifact/temp files, non-ASCII additions, debug/TODO markers, missing changelog signals, optional root workspace status, and a short worker-result excerpt. If it flags no risk and the task is low-risk, Codex may keep review to the packet plus targeted changed-file hunks.

## Quality Score

Every accepted Claude Code result needs a Codex review score:

```text
quality_score: X/10
accepted: true|false
score_reasons:
- <required when quality_score < 8>
followup_recommendation: none|minor cleanup|direct fix|correction payload|codex takeover
```

Score the implementation result, not Claude Code cost:

- Correctness: 4 points
- Test and verification evidence: 2 points
- Diff focus and simplicity: 1.5 points
- Project conventions: 1 point
- Worktree and artifact hygiene: 1 point
- Report quality and risk notes: 0.5 points

If `quality_score < 8`, Codex must explain the deductions and choose a follow-up. Low score does not automatically reject the patch; correctness or verification gaps should trigger correction or Codex takeover, while local style, ASCII, changelog, or artifact issues can be fixed directly by Codex.

Follow-up policy:

- `8-10`: accept after fresh verification; apply minor Codex cleanup if needed.
- `6-7.9`: choose direct Codex cleanup for local issues, or one correction payload for semantic gaps.
- `<6`: default to Codex takeover unless a Claude redo is clearly lower risk.

## When To Expand Review

Read beyond changed files when there is evidence of:

- security, auth, payment, or secrets risk
- data migration, destructive operation, or data loss risk
- concurrency, ordering, idempotency, or consistency risk
- public API, schema, prompt, model, RAG, config, deployment, or restart changes
- test coverage gaps on the critical path
- worker failure, speculative edits, or broad refactoring
- `review_packet.json` reports artifact/temp files, non-ASCII additions, debug/TODO markers, missing required changelog, or root workspace dirtiness

## Rejection Or Codex Fix Signals

Reject the patch, ask for one correction, or have Codex fix directly when:

- it changes behavior outside the goal
- it hides failure by weakening tests or validation
- it rewrites a subsystem to fix a local bug
- it leaves verification unrun without a concrete blocker
- it introduces TODOs, dead code, or temporary debug output
- it changes global skills or user-level configuration
- it cannot explain meaningful risk or uncertainty
