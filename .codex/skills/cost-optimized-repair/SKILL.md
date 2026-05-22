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
4. Dispatch the payload through `scripts/run_repair_worker.py`, not by calling `claude` directly.
5. Review the returned files, diff, and test evidence with `references/review-checklist.md`.
6. If `worker_status` is `failed`, count it as a failed worker round, preserve the failure report, verify any partial diff was restored, and decide whether to send one correction payload or have Codex take over.
7. If the result is close but flawed, send one concise correction payload. After two failed, blocked, unsafe, timed-out, no-JSON, or no-report worker rounds, stop delegating and have Codex take over.
8. Finish with the repository's normal task classification, verification, and finalization rules.

## Claude CLI Invocation

Use the runner so Codex gets a structured result even when Claude Code hangs, returns non-JSON output, or leaves a partial diff. Store the payload outside the repo, for example under `/tmp`, so the task worktree stays clean before dispatch.

For implementation rounds:

```bash
python3 .codex/skills/cost-optimized-repair/scripts/run_repair_worker.py \
  --payload-file /tmp/repair-worker-payload.md \
  --restore-on-failure
```

The runner uses `--model opus --effort max` by default and does not set a budget cap. For read-only probes, add `--tools "Read,Bash"`. For intentionally short smoke tests only, add `--max-budget-usd`.

After every runner call, inspect `worker_status`, `failure_reason`, `partial_diff_stat`, `partial_diff_files`, `restored_partial_diff`, `normalized_worker_result`, `stdout`, `stderr`, `worker_result`, `total_cost_usd`, `modelUsage`, and `permission_denials`. Treat permission denials, CLI errors, timeouts, invalid JSON, missing worker sections, or unrestored partial diffs as a failed worker round. Record `total_cost_usd` for experiments, but do not set a budget cap for normal implementation rounds unless the user explicitly asks for a smoke test cap.

To verify the local CLI path without modifying files, run:

```bash
python3 .codex/skills/cost-optimized-repair/scripts/verify_claude_cli_flow.py
```

## Failure Reporting

Final task reports must say whether the worker succeeded, failed, or was skipped. If Codex takes over after worker failure, report the failure reason and cleanup state, for example: `worker timed out without JSON; partial diff touched <files>; runner restored it; Codex then implemented and verified the fix directly`.

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
