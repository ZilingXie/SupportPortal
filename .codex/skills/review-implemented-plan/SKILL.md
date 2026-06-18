---
name: review-implemented-plan
description: Use when the user asks Codex or Claude Code to review a completed implementation, finished plan, worker handoff, or local diff, including phrases like "实现了计划，你来review一下", "review this implementation", "检查这个改动", "帮我review并处理", "review and finalize", or similar requests after code/docs have been changed.
---

# Review Implemented Plan

## Core Rule

Treat implementation review as task ownership transfer unless the user explicitly says review-only. Codex and Claude Code may both own the review, make fixes, verify, commit, PR, merge/finalize, run required post-merge checks, and clean the current task workspace/branch. Reasonix remains handoff-only and must wait for Codex or Claude Code review.

## Process

1. **Confirm scope and workspace**
   - Identify the branch/workspace under review and whether the request is review-only or ownership transfer.
   - Run the required Git/worktree safety checks for repo-tracked review/finalization.
   - Ignore unrelated branches/worktrees; stop only if the current task workspace/branch is wrong, ambiguous, detached, or dirty with unrelated changes.

2. **Inspect the implementation**
   - Review the diff, untracked task files, claimed plan, verification evidence, skipped checks, and known risks.
   - If task files are untracked or unstaged, include them in the review instead of treating the handoff as complete.
   - For code context, use CodeGraph before broad file reads; use `rg` for literal docs/config/log/rule text.

3. **Review like an owner**
   - Look for correctness bugs, behavioral regressions, security risks, broken workflow rules, missing logs, and missing targeted tests.
   - For `功能类/重大行为变更`, confirm `docs/roadmap.html` 已同步或明确不需要同步 before finalization.
   - If issues are fixable and safe to decide, fix them directly in the task workspace.
   - Stop for user input only when the issue is ambiguous, unsafe to decide, or blocked by missing external state.

4. **Verify and finalize**
   - Classify the task (`文档类`, `修复类`, or `功能类/重大行为变更`).
   - Run the narrowest targeted verification that proves the reviewed/fixed change.
   - If not review-only and verification passes, continue through the normal direct-to-`main` workflow: commit task-owned changes, push, create/reuse PR, squash-merge, fast-forward root `main`, run `codegraph sync`, run required post-merge live stack verification for stack-relevant changes, then clean only the current task workspace/branch.

## Reporting

- If findings exist, list them first with file/line references, then state fixes and verification.
- If no findings remain, say so and include residual risks or skipped checks.
- For paused work, report `paused, not finalized` with branch, workspace path, and clean/dirty state.
- For finalized work, include PR URL, merge commit, verification commands, task classification, and whether live stack verification was required.

## Do Not

- Do not stop after listing fixable findings when the user expects completion.
- Do not ignore untracked task files in a handoff.
- Do not finalize if verification fails or task ownership is ambiguous.
- Do not clean unrelated worktrees/branches.
