---
name: cost-optimized-repair
description: Token-efficient repair delegation for this SupportPortal project. Use when the user asks to use cost-optimized-repair, requests token-efficient delegation, or a code repair can safely be delegated to Claude Code or DeepSeek while Codex keeps responsibility for triage, minimal payload construction, diff review, verification review, and final acceptance.
---

# Cost Optimized Repair

## Overview

Keep Codex focused on judgment, dispatch, and acceptance. Delegate cheap implementation and test iteration to the project-local Claude Code worker skill at `.claude/skills/repair-worker/`.

Do not create or rely on global skills under `~/.codex/skills` or `~/.claude/skills` for this workflow.

## Delegation Gate

Delegate only when the likely fix is bounded and Codex can review it cheaply. Good candidates are small and medium bug fixes with clear symptoms, likely files, and targeted verification.

Do not delegate by default when the task involves security, auth, payment, data migration, data loss risk, concurrency, consistency, public API or schema changes, production secrets, broad architecture, or paths that tests cannot cover. See `references/escalation-policy.md`.

## Codex Workflow

1. Classify whether the repair is safe to delegate. If unclear, read `references/escalation-policy.md`.
2. Read only enough context to create a precise payload: the user report, failing output, likely files from `scope_hints`, targeted `rg` results, and nearby call sites when necessary.
3. Build a short payload using `references/payload-schema.md`. Do not paste a full worker brief.
4. Dispatch the payload to the available Claude Code and DeepSeek worker path. If no direct dispatch path is available, give the payload to the user and ask for the worker result instead of expanding Codex implementation.
5. Review the returned files, diff, and test evidence with `references/review-checklist.md`.
6. If the result is close but flawed, send one concise correction payload. After two failed, blocked, or unsafe worker rounds, stop delegating and have Codex take over.
7. Finish with the repository's normal task classification, verification, and finalization rules.

## Context Budget

Prefer targeted commands and narrow reads:

- `git diff --stat`
- `git diff -- <changed-files>`
- targeted `rg`
- failing test output
- changed-file neighbors and high-risk call sites

Do not default to rereading the whole repository. Expand only for the escalation triggers in `references/escalation-policy.md`.

## Payload Shape

Every delegated task must start with:

```md
/repair-worker

goal:

scope_hints:

known_context:

constraints:
- Smallest correct change
- No unrelated refactor
- Preserve public APIs unless required

verification:

acceptance:
```

See `references/payload-schema.md` for field rules and correction payloads.

## Worker Result Contract

Require the worker to return:

```md
## Result
Fixed / Not fixed / Blocked

## Files Changed
- ...

## What Changed
- ...

## Verification
- Command:
- Result:

## Risk / Uncertainty
- ...

## Needs Codex Review
- ...
```

## Cost Experiment

When measuring this workflow, compare Codex-direct repair against Codex delegation plus review on three real tasks: small bug, medium bug, and high-risk bug. Record Codex tokens, worker tokens, elapsed time, first-pass test result, rework rounds, and final diff quality.

If a task class does not reduce Codex tokens below 70 percent of direct repair, tighten Codex reads, payload length, or review scope before using that class as a default delegation target.
