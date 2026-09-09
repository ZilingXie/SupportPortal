---
name: review-implemented-plan
description: Use when the user asks Codex or Claude Code to review a completed implementation, finished plan, worker handoff, or local diff, including phrases like "实现了计划，你来review一下", "review this implementation", "检查这个改动", "帮我review并处理", "review and finalize", or similar requests after code/docs have been changed.
---

# Review Implemented Plan

## Core Rule

Review requests are read-only by default. Requests such as "实现了计划，你来review一下", "review this implementation", or a worker handoff authorize inspection and findings, not fixes or finalization.

Make changes or take over finalization only when the user explicitly authorizes that work, such as "修复这些问题" or "review and finalize". Existing authorization for the current scope remains valid across turns, including review of your own authorized implementation; do not ask again. A later explicit review-only instruction narrows that scope. Follow any user limits on edits, commits, merging, and deployment.

## Process

1. **Confirm scope and workspace**
   - Identify the branch/workspace under review and the authorization already given in the conversation; use read-only review when implementation/finalization has not been authorized.
   - Run the required Git/worktree safety checks for repo-tracked review/finalization.
   - Ignore unrelated branches/worktrees; stop only if the current task workspace/branch is wrong, ambiguous, detached, or dirty with unrelated changes.

2. **Inspect the implementation**
   - Review the diff, untracked task files, claimed plan, verification evidence, skipped checks, and known risks.
   - If task files are untracked or unstaged, include them in the review instead of treating the handoff as complete.
   - For code context, use CodeGraph before broad file reads; use `rg` for literal docs/config/log/rule text.

3. **Review within the authorized scope**
   - Look for correctness bugs, behavioral regressions, security risks, broken workflow rules, missing logs, and missing targeted tests.
   - For `功能类/重大行为变更`, confirm the corresponding `docs/project/tasks/<task-id>.json` and, when the product capability list changes, `docs/feature_list.md` are synchronized; `docs/roadmap.html` is a historical snapshot and is not a progress-state source.
   - For read-only review, report findings and verification gaps without changing files, worktrees, Git state, configuration, APIs, or external state. Do not run checks that would cause those changes.
   - For authorized fixes, correct issues that are safe to decide directly in the task workspace and preserve the requested scope.
   - Stop for user input only when the issue is ambiguous, unsafe to decide, or blocked by missing external state.

4. **Verify and finalize**
   - Classify a changed diff as `文档改动` or `代码改动` before choosing verification depth; see `AGENTS.md`.
   - For read-only review, inspect existing verification evidence and use only checks consistent with the read-only boundary; report what remains unverified and stop after the review.
   - For authorized implementation/finalization, run targeted verification, then use `scripts/workflow/finalize_task_to_main.sh` unless the user has limited that workflow. The script owns merge, root synchronization, existing CodeGraph index synchronization, and task cleanup. The agent must separately run any required post-merge live-stack verification from root `main` before reporting completion.

## Reporting

- If findings exist, list them first with file/line references, then state fixes and verification.
- If no findings remain, say so and include residual risks or skipped checks.
- For read-only review, report findings and verification limits; do not describe it as unfinished implementation or start finalization.
- For authorized implementation/finalization, use the completion states in `docs/agent_workflow_details.md`; when paused, include the branch, workspace path, clean/dirty state, and blocker.
- For finalized work, include PR URL, merge commit, verification commands, task classification, and whether live stack verification was required.

## Do Not

- Do not treat a review request, a clean diff, or passing tests as authorization to fix, commit, merge, clean up, or deploy.
- Do not stop after listing fixable findings when implementation is already authorized for the current scope.
- Do not ignore untracked task files in a handoff.
- Do not finalize if verification fails or task ownership is ambiguous.
- Do not clean unrelated worktrees/branches.
