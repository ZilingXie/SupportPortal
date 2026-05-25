---
name: cost-optimized-repair
description: Token-efficient repair delegation for this SupportPortal project. Use when the user asks to use cost-optimized-repair, requests token-efficient delegation, or a code repair can safely be delegated to Claude Code or DeepSeek while Codex keeps responsibility for triage, focused repair planning, diff review, verification review, and final acceptance.
---

# Cost Optimized Repair

## Overview

Keep Codex focused on judgment, dispatch, and acceptance. Delegate cheap implementation and test iteration to the project-local Claude Code worker skill at `.claude/skills/repair-worker/`.

Do not create or rely on global skills under `~/.codex/skills` or `~/.claude/skills` for this workflow.

## Claude Code Preflight

Before planning or dispatching delegated repair work, verify Claude Code can answer non-interactively:

```bash
claude --bare -p 'Smoke test only. Reply exactly: CLAUDE_CODE_OK' \
  --output-format json \
  --permission-mode bypassPermissions \
  --tools Read \
  --model opus \
  --effort low \
  --no-session-persistence
```

Continue only when the command exits 0, returns valid JSON, has `is_error=false`, no permission denials, and `result` is exactly `CLAUDE_CODE_OK`. If the preflight fails, terminate the repair task immediately; do not create worker payloads, do not start implementation, and report the Claude Code failure reason, exit status, and any useful stderr/stdout path or JSON error details.

## Delegation Gate

Delegate only when the likely fix is bounded and Codex can review it cheaply. Good candidates are small and medium bug fixes with clear symptoms, likely files, and targeted verification.

Do not delegate by default when the task involves security, auth, payment, data migration, data loss risk, concurrency, consistency, public API or schema changes, production secrets, broad architecture, or paths that tests cannot cover. See `references/escalation-policy.md`.

## Task Decomposition

Codex must decompose the user request before any implementation worker runs. This is required whether the user provides a detailed multi-PR plan or only describes a bug to fix.

First split the request into PR-sized slices:

- If the user provides a plan with multiple PRs or clearly separable phases, process exactly one PR slice at a time in the requested order.
- If the user only describes a repair, create the smallest PR-sized slice that can restore the behavior and be verified.
- For each PR slice, define the goal, scope boundaries, expected changed files, targeted verification, and acceptance checks before dispatch.
- Do not bundle multiple PR slices into one broad payload. Finish review and verification for the current slice before starting the next slice.

Then split the current PR slice into one or more Claude Code agent payloads and dispatch them:

- Dispatch at least one Claude Code implementation agent for every delegated PR slice. Do not self-implement first unless the Delegation Gate says the slice is unsafe to delegate, Claude Code preflight failed, or the worker failure limit has been reached.
- Start multiple Claude Code agents simultaneously when the current PR slice contains independent subtasks that can be safely isolated.
- Start exactly one Claude Code agent when the PR slice is not safely parallelizable.
- Start multiple agents simultaneously only when their write scopes are independent and each can run from a clean isolated workspace or is read-only.
- Never point two writing workers at the same task worktree. If isolated write workspaces are not available, run one writing worker and optionally run parallel read-only probes with `--tools "Read,Bash"`.
- Give every parallel payload an explicit write scope, out-of-scope list, verification command, and final output contract.
- Codex remains responsible for merging or accepting worker outputs sequentially, reviewing each diff, and resolving conflicts.

## Codex Workflow

1. Run the Claude Code preflight. Stop and report if it fails.
2. Classify whether the repair is safe to delegate. If unclear, read `references/escalation-policy.md`.
3. Decompose the request into PR-sized slices, then decompose the current slice into one or more safe Claude Code agent payloads.
4. Read enough context to create high-quality worker payloads: the user report, failing output, likely files from `scope_hints`, targeted `rg` results, nearby call sites when necessary, verification commands, and explicit out-of-scope boundaries. Prioritize code quality over minimizing this planning step.
5. Build concise payloads using `references/payload-schema.md`. Do not paste a full worker brief, but do include the `final_output_contract`.
6. Dispatch the Claude Code agent payload or payloads through `scripts/run_repair_worker.py`, not by calling `claude` directly. Use parallel dispatch when the Task Decomposition safety rules allow it; otherwise dispatch one worker.
7. Treat each runner as the completion hook: wait for its final report, then review the compact report, changed files, diff, and test evidence with `references/review-checklist.md`. Do not consume long Claude stdout/stderr unless the compact report points to a failure that needs it.
8. If `worker_status` is `failed`, count it as a failed worker round for that payload, preserve the failure report, verify any partial diff was restored, and decide whether to send one correction payload or have Codex take over.
9. If the result is close but flawed, send one concise correction payload. After two failed, blocked, unsafe, timed-out, no-JSON, or no-report worker rounds for the same payload, stop delegating that payload and have Codex take over.
10. After all accepted payloads for a PR slice are reviewed, run the slice-level targeted verification. Then continue to the next PR slice or finish with the repository's normal task classification, verification, and finalization rules.

## Claude CLI Invocation

Use the runner so Codex gets a structured result even when Claude Code hangs, returns non-JSON output, or leaves a partial diff. Store the payload outside the repo, for example under `/tmp`, so the task worktree stays clean before dispatch.

For implementation rounds:

```bash
python3 .codex/skills/cost-optimized-repair/scripts/run_repair_worker.py \
  --payload-file /tmp/repair-worker-payload.md \
  --restore-on-failure \
  --compact-output \
  --report-file /tmp/repair-worker-report.json
```

The runner uses `--model opus --effort max` by default and does not set a budget cap. For read-only probes, add `--tools "Read,Bash"`. For intentionally short smoke tests only, add `--max-budget-usd`.

For multiple safe read-only probes, start multiple runner commands concurrently with separate payload and report files. For writing implementation workers, run concurrently only from separate clean isolated workspaces; otherwise run writing workers sequentially.

After every runner call, inspect the compact stdout first: `worker_status`, `failure_reason`, `partial_diff_stat`, `partial_diff_files`, `restored_partial_diff`, `saved_partial_patch`, `normalized_worker_result`, `worker_call_report`, `total_cost_usd`, and `full_report_path`. Open the full report or saved stdout/stderr artifacts only when the compact report is insufficient for review. Treat permission denials, CLI errors, timeouts, invalid JSON, missing worker sections, unrestored partial diffs, or a low quality score as a failed or correction-needed worker round. Record `total_cost_usd` for experiments, but do not set a budget cap for normal implementation rounds unless the user explicitly asks for a smoke test cap.

To verify the local CLI path without modifying files, run:

```bash
python3 .codex/skills/cost-optimized-repair/scripts/verify_claude_cli_flow.py
```

## Failure Reporting

Final task reports must include a short Claude Code call report. If the worker succeeded, report success, verification status, and Codex's repair-quality score out of 10. If the score is below 7, briefly state why and the optimization plan. If the worker failed, report the failure reason, cleanup state, and one short optimization plan, for example: `Claude Code: failed; reason=timeout; partial diff touched <files>; runner restored it; optimization=narrow the payload and verification command`.

## Context Budget

Spend Codex tokens on diagnosis, planning, and acceptance, not on watching the worker process. Prefer targeted commands and narrow reads:

- `git diff --stat`
- `git diff -- <changed-files>`
- targeted `rg`
- failing test output
- changed-file neighbors and high-risk call sites

Do not default to rereading the whole repository or long Claude stdout/stderr. Expand only for the escalation triggers in `references/escalation-policy.md` or when the compact report shows a specific review gap.

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

final_output_contract:
- Final answer starts exactly with `## Result`.
- Under `## Result`, write exactly one of `Fixed`, `Not fixed`, or `Blocked`.
- Do not write `Fixed.`, `Success`, `Implemented`, bullets, or code formatting on the result line.
- Use exactly these six H2 headings in order: `## Result`, `## Files Changed`, `## What Changed`, `## Verification`, `## Risk / Uncertainty`, `## Needs Codex Review`.
```

See `references/payload-schema.md` for field rules and correction payloads.

## Worker Result Contract

Require the worker to return:

```md
## Result
Fixed

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

The result line may also be exactly `Not fixed` or `Blocked`; do not include punctuation or multiple options on that line.

## Cost Experiment

When measuring this workflow, compare Codex-direct repair against Codex delegation plus review on three real tasks: small bug, medium bug, and high-risk bug. Record Codex tokens, worker tokens, elapsed time, first-pass test result, rework rounds, and final diff quality.

If a task class does not reduce Codex tokens below 70 percent of direct repair, tighten Codex reads, payload length, or review scope before using that class as a default delegation target.
