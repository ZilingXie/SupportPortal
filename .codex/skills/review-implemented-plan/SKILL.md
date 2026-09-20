---
name: review-implemented-plan
description: Use when the user asks Codex or Claude Code to review a completed implementation, finished plan, worker handoff, or local diff, including phrases like "实现了计划，你来review一下", "review this implementation", "检查这个改动", "帮我review并处理", "review and finalize", or similar requests after code/docs have been changed.
---

# Review Implemented Plan

## Core Rule

Review requests are read-only by default. Requests such as "实现了计划，你来review一下", "review this implementation", or a worker handoff authorize inspection and findings, not fixes or finalization.

Make changes or take over finalization only when the user explicitly authorizes that work, such as "修复这些问题" or "review and finalize". Existing authorization for the current scope remains valid across turns, including review of your own authorized implementation; do not ask again. A later explicit review-only instruction narrows that scope. Follow any user limits on edits, commits, merging, and deployment.

Follow the repository's execution mode: `实施计划，需要验收` requires independent acceptance before finalization/deployment; a plain `实施计划` does not add this wait. Executor self-review is useful verification but cannot satisfy the requested independent gate. A passing review permits continuation only within existing authorization. Use [the workflow details](../../../docs/agent_workflow_details.md#implementation-handoff-and-independent-acceptance) for handoff, review freshness, and resumption.

## Process

1. **Confirm scope and workspace**
   - Identify the branch/workspace under review and the authorization already given in the conversation; use read-only review when implementation/finalization has not been authorized.
   - Run the required Git/worktree safety checks for repo-tracked review/finalization.
   - Ignore unrelated branches/worktrees; stop only if the current task workspace/branch is wrong, ambiguous, detached, or dirty with unrelated changes.

2. **Inspect the implementation**
   - Review the diff, untracked task files, claimed plan, verification evidence, skipped checks, and known risks.
   - Identify the agreed contracts, accepted limitations, source baseline, and exact implementation under review. On the first pass, inspect all affected entry points and relevant states, recovery/repeat paths, and side-effect boundaries together.
   - If task files are untracked or unstaged, include them in the review instead of treating the handoff as complete.
   - For code context, use CodeGraph before broad file reads; use `rg` for literal docs/config/log/rule text.

3. **Review within the authorized scope**
   - Look for correctness bugs, behavioral regressions, security risks, broken workflow rules, missing logs, and missing targeted tests.
   - Check whether critical tests exercise the real entry point and observable outcome instead of copying the implementation's guard into the test. PostgreSQL-specific guarantees need isolated PostgreSQL evidence; an in-memory substitute is insufficient. Include relevant positive cases as well as rejection/recovery cases. Explain changed assertions against the agreed contract.
   - Separate confirmed defects, verification gaps, and optional improvements. For defects, give a trigger, code evidence, consequence, violated contract or material risk, and a regression case. A gap is not a proven bug, but a critical unverified contract can block acceptance. Severity labels alone do not decide whether an item blocks.
   - On subsequent passes, review unresolved findings, the new diff, and affected paths. Identify newly found blockers as an unmet original contract, a repair regression, or a newly discovered material defect. Do not turn optional improvements into new acceptance requirements without user agreement.
   - For `功能类/重大行为变更`, confirm the corresponding `docs/project/tasks/<task-id>.json` and, when the product capability list changes, `docs/feature_list.md` are synchronized; `docs/roadmap.html` is a historical snapshot and is not a progress-state source.
   - For read-only review, report findings and verification gaps without changing files, worktrees, Git state, configuration, APIs, or external state. Do not run checks that would cause those changes.
   - For authorized fixes, correct issues that are safe to decide directly in the task workspace and preserve the requested scope.
   - Stop for user input only when the issue is ambiguous, unsafe to decide, or blocked by missing external state.

4. **Verify and finalize**
   - Classify a changed diff as `文档改动` or `代码改动` before choosing verification depth; see `AGENTS.md`.
   - For read-only review, inspect existing verification evidence and use only checks consistent with the read-only boundary; report what remains unverified and stop after the review.
   - For authorized implementation/finalization, run targeted verification and respect any requested independent acceptance gate. While that gate is pending, hand off and retain the task workspace; do not call `scripts/workflow/finalize_task_to_main.sh`. Once the applicable gate is satisfied, follow the workflow's review-freshness checks and finalize within existing authorization.
   - The finalize script owns merge, root synchronization, and existing CodeGraph index synchronization. Cleanup is a separate `scripts/workflow/cleanup_task_worktree.sh <branch>` call from root `main`. The agent separately performs required post-merge live-stack verification before reporting overall completion.

## Reporting

- State which branch/commit and any additional diff were reviewed. List blocking defects and critical verification gaps first with file/line references; keep optional improvements separate, then state fixes and verification.
- Give an explicit review conclusion: passed, not passed, or insufficient evidence, with scope and residual risks. Passing requires no blocking defects or critical verification gaps. Mark executor self-review as self-review; it cannot release an independent acceptance gate.
- For read-only review, report findings and verification limits; do not describe it as unfinished implementation or start finalization.
- For authorized implementation/finalization, use the completion states in `docs/agent_workflow_details.md`; when paused, include the branch, workspace path, clean/dirty state, and blocker.
- For finalized work, include PR URL, merge commit, verification commands, task classification, and whether live stack verification was required.

## Do Not

- Do not treat a review request, a clean diff, or passing tests as authorization to fix, commit, merge, clean up, or deploy.
- Do not stop after listing fixable findings when implementation is already authorized for the current scope; preserve a requested independent gate after repairs.
- Do not ignore untracked task files in a handoff.
- Do not finalize if verification fails or task ownership is ambiguous.
- Do not clean unrelated worktrees/branches.
