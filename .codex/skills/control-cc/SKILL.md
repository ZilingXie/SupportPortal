---
name: control-cc
description: Use when the user asks for control-cc or wants Codex to coordinate Claude Code for repairs, features, refactors, optimizations, tests, or code-adjacent docs while Codex keeps planning, review, and acceptance responsibility.
---

# Control CC

## Overview

Use Control CC when Codex should orchestrate and Claude Code should own most implementation. Codex plans, creates the real task worktree, dispatches Claude Code, reads compact artifacts, reviews the result, fixes only what is necessary, verifies, and finalizes. Claude Code executes implementation plans inside the project-local `.claude/skills/control-cc-worker/` skill.

Do not create or rely on global skills under `~/.codex/skills` or `~/.claude/skills`.

Default to Orchestrator Mode:

- Codex writes a PR-sized implementation plan with target files, verified facts, acceptance criteria, and verification commands.
- Claude Code gets implementation freedom inside the active task or candidate worktree. Do not add write-scope gates, packet scoring, line-by-line patch instructions, or budget caps unless the user explicitly asks.
- Codex keeps quality control through artifacts: `report.json`, `review_packet.json`, saved patch paths, targeted diff review, `quality_score`, and fresh verification.
- During Claude Code execution, Codex stays in low-token waiting mode and does not read long logs, summarize heartbeat output, or poll diffs.

## Preflight

Before dispatching Claude Code, verify the CLI can answer non-interactively:

```bash
claude --bare -p 'Smoke test only. Reply exactly: CLAUDE_CODE_OK' \
  --output-format json \
  --permission-mode bypassPermissions \
  --tools Read \
  --model opus \
  --effort low \
  --no-session-persistence
```

Continue only when the command exits 0, returns valid JSON, has `is_error=false`, no permission denials, and `result` is exactly `CLAUDE_CODE_OK`.

For a full local-path smoke test, write a tiny `/control-cc-worker` plan under `/tmp/control-cc-runs/<thread>/smoke.md` and run:

```bash
python3 .codex/skills/control-cc/scripts/run_cc_plan.py \
  --plan-file /tmp/control-cc-runs/<thread>/smoke.md \
  --tools Read,Bash \
  --restore-on-failure \
  --timeout-sec 180 \
  --compact-output
```

Stop delegation for the current task if either preflight fails. Report the failure reason and any saved stdout, stderr, report, or patch paths.

## Input Modes

### Goal Only

When the user gives one target such as "fix this feature":

1. Follow the repository worktree rules to create one real `codex/<thread>` task branch and worktree.
2. Turn the goal into one PR-sized implementation plan with acceptance criteria and targeted verification.
3. Run Claude Code with `scripts/run_cc_plan.py` from the task worktree, or from one detached candidate worktree if isolation is useful.
4. Review the runner compact report and `review_packet.json` first; open full diffs or logs only for changed files, flagged risks, or high-risk tasks.
5. If the result is close but incomplete, Codex improves the diff directly in the task worktree.
6. Run fresh targeted verification, then use the repository finalization workflow.

### Goal Plus Multiple PRs

When the user gives a goal and several PR slices:

1. Sort slices before execution: explicit dependencies first, shared contracts/backend/core logic before consumers/UI, otherwise keep the user's order.
2. Process exactly one real PR slice at a time.
3. For each PR slice, create or reuse the active task branch/worktree for that PR slice, then split the slice into one or more implementation plans.
4. Independent plans may run in parallel through sub-agents. Dependent plans run sequentially.
5. Plan sub-agents act as runner supervisors and artifact collectors. They should not spend tokens doing deep review unless the packet flags risk.
6. After all plans for the current PR are integrated and verified, finalize that PR to `main` before starting the next PR.
7. Write `/tmp/control-cc-runs/<thread>/pr-XX/handoff.md` after each merged PR. Carry forward only the summary, verification evidence, quality score, and important diff conclusions.

## Candidate Worktrees

Use detached candidate worktrees for parallel or risky plan execution. They are temporary local execution spaces, not task branches.

Create one from the current PR task worktree:

```bash
python3 .codex/skills/control-cc/scripts/candidate_worktree.py create \
  --run-dir /tmp/control-cc-runs/<thread>/pr-01/plan-01 \
  --base-ref HEAD
```

Run Claude Code inside the returned `worktree_path`:

```bash
python3 .codex/skills/control-cc/scripts/run_cc_plan.py \
  --plan-file /tmp/control-cc-runs/<thread>/pr-01/plan-01/plan.md \
  --heartbeat-sec 60 \
  --retry-interval-sec 10 \
  --max-unavailable-retries 3 \
  --restore-on-failure \
  --compact-output \
  --report-file /tmp/control-cc-runs/<thread>/pr-01/plan-01/report.json \
  --review-packet-file /tmp/control-cc-runs/<thread>/pr-01/plan-01/review_packet.json
```

After dispatch, enter low-token waiting mode. Keep only the plan id, candidate worktree path, report path, and review packet path in active context. Let `run_cc_plan.py` monitor the Claude Code process; do not poll diffs, reread stdout/stderr, or summarize intermediate logs while it is still running.

If the runner returns `failure_reason=claude_unavailable`, Codex must take over the current repair or plan directly. In parallel, start a separate diagnostic pass that checks the Claude CLI path, PATH resolution, permission mode, model/effort arguments, the saved stderr path, and a one-line smoke test. Do not block the user-facing fix on that diagnostic unless the same Claude failure prevents all further work.

Export and integrate accepted work sequentially in the real PR task worktree:

```bash
python3 .codex/skills/control-cc/scripts/candidate_worktree.py export-patch \
  --worktree /tmp/control-cc-runs/<thread>/pr-01/plan-01/worktree \
  --patch-file /tmp/control-cc-runs/<thread>/pr-01/plan-01/accepted.patch

git apply --3way /tmp/control-cc-runs/<thread>/pr-01/plan-01/accepted.patch
```

Clean candidates after integration or rejection:

```bash
python3 .codex/skills/control-cc/scripts/candidate_worktree.py cleanup \
  --worktree /tmp/control-cc-runs/<thread>/pr-01/plan-01/worktree
```

Never push, finalize, or merge from a candidate worktree. The real PR task branch is the only branch that may be finalized.

## Agent Responsibilities

Main Codex agent:

- interprets the user request, sorts PR slices, creates real task worktrees, and writes implementation plans
- dispatches plan sub-agents only when parallel candidate work is truly independent
- integrates accepted patches into the real PR task worktree
- reads `review_packet.json` before full diffs, then performs final diff review, verification, change-log updates, and repository finalization

Plan sub-agent:

- receives one implementation plan, candidate worktree path, verification command, and acceptance criteria
- runs `run_cc_plan.py`, waits on the runner rather than polling logs, and reads the compact report plus `review_packet.json`
- asks Claude Code for one correction only when the packet or targeted diff review shows a concrete issue
- may make small local fixes in the candidate worktree when that is cheaper than another Claude round
- exports an accepted patch and reports changed files, verification evidence, risks, and patch path to the main agent

Claude Code worker:

- executes the plan through `/control-cc-worker`
- may choose the implementation approach inside the plan's goal and acceptance criteria
- runs the requested verification when possible
- returns a concise completion report; strict report headings are optional
- does not commit, push, or finalize

## Review And Acceptance

Accept work from evidence, not from Claude's confidence.

- Review `run_cc_plan.py` compact JSON first: status, failure reason, changed files, saved patch paths, stdout/stderr paths, and `review_packet_path`.
- Read `review_packet.json` before opening full diffs. It highlights changed files, diff stat, artifact/temp files, non-ASCII additions, debug/TODO markers, required changelog gaps, optional root workspace status, and a short worker-result excerpt.
- Expand review to `git diff --stat`, changed-file diffs, or stdout/stderr only when the packet flags risk, the task is high risk, or the worker evidence is insufficient.
- Reject or fix work that hides verification failures, changes unrelated behavior, edits global skills, performs broad refactors, or leaves unexplained risk.
- Codex may directly improve an accepted-but-imperfect diff in the real task worktree before final verification.
- Every accepted Claude Code result must receive `quality_score: X/10` in Codex's review summary. If `quality_score < 8`, include `score_reasons` and `followup_recommendation`.
- Suggested follow-up policy: `8-10` accept with optional cleanup; `6-7.9` direct cleanup or one correction payload based on issue type; `<6` default to Codex takeover unless a Claude redo is clearly lower risk.
- Run the narrowest fresh verification that proves the PR slice before finalization. For stack-relevant work, follow the repository post-merge live stack verification rules.

## Legacy Compatibility

The legacy v2 runner path remains available for older `/repair-worker` payloads. It is not the default path for new Control CC work.
