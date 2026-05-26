---
name: control-cc
description: Use when the user asks for control-cc or wants Codex to coordinate Claude Code for repairs, features, refactors, optimizations, tests, or code-adjacent docs while Codex keeps planning, review, and acceptance responsibility.
---

# Control CC

## Overview

Use Control CC when Codex should manage the work and Claude Code should do most implementation. Codex owns PR sequencing, implementation plans, candidate patch integration, diff review, verification, final acceptance, and repository finalization. Claude Code executes clear plans inside the project-local `.claude/skills/control-cc-worker/` skill.

Do not create or rely on global skills under `~/.codex/skills` or `~/.claude/skills`.

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
4. Review the report, `git diff --stat`, changed-file diffs, and verification evidence.
5. If the result is close but incomplete, Codex improves the diff directly in the task worktree.
6. Run fresh targeted verification, then use the repository finalization workflow.

### Goal Plus Multiple PRs

When the user gives a goal and several PR slices:

1. Sort slices before execution: explicit dependencies first, shared contracts/backend/core logic before consumers/UI, otherwise keep the user's order.
2. Process exactly one real PR slice at a time.
3. For each PR slice, create or reuse the active task branch/worktree for that PR slice, then split the slice into one or more implementation plans.
4. Independent plans may run in parallel through sub-agents. Dependent plans run sequentially.
5. After all plans for the current PR are integrated and verified, finalize that PR to `main` before starting the next PR.
6. Write `/tmp/control-cc-runs/<thread>/pr-XX/handoff.md` after each merged PR. Carry forward only the summary, verification evidence, and important diff conclusions.

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
  --restore-on-failure \
  --compact-output \
  --report-file /tmp/control-cc-runs/<thread>/pr-01/plan-01/report.json
```

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
- dispatches plan sub-agents when parallel candidate work is safe
- integrates accepted patches into the real PR task worktree
- performs final diff review, verification, change-log updates, and repository finalization

Plan sub-agent:

- receives one implementation plan, candidate worktree path, verification command, and acceptance criteria
- runs `run_cc_plan.py`, reviews the returned report and local diff, and asks Claude Code for one correction only when useful
- may make small local fixes in the candidate worktree when that is cheaper than another Claude round
- exports an accepted patch and reports changed files, verification evidence, risks, and patch path to the main agent

Claude Code worker:

- executes the plan through `/control-cc-worker`
- runs the requested verification when possible
- returns a concise completion report; strict report headings are optional
- does not commit, push, or finalize

## Review And Acceptance

Accept work from evidence, not from Claude's confidence.

- Review `run_cc_plan.py` JSON first: status, failure reason, changed files, diff stat, saved patch paths, cost, stdout/stderr paths, and verification text in the worker result.
- Inspect `git diff --stat` and changed-file diffs in either the candidate or real task worktree.
- Reject or fix work that hides verification failures, changes unrelated behavior, edits global skills, performs broad refactors, or leaves unexplained risk.
- Codex may directly improve an accepted-but-imperfect diff in the real task worktree before final verification.
- Run the narrowest fresh verification that proves the PR slice before finalization. For stack-relevant work, follow the repository post-merge live stack verification rules.

## Legacy Compatibility

The legacy v2 runner path remains available for older `/repair-worker` payloads. It is not the default path for new Control CC work.
