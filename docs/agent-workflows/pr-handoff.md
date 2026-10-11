# PR handoff and review freshness

Verified against workflow sources: 2026-10-08. Roles and human decisions follow
[AGENTS.md](../../AGENTS.md#execution-modes) and
[implementation-handoff](../../.codex/skills/implementation-handoff/SKILL.md).

## Prepare without finalizing

Use the task worktree and keep the same branch/PR through repair. Commit all task
files, run targeted checks, push the branch, and create a Draft PR using `gh pr
create --draft --base main --head ... --body-file ...`. Supply real arguments;
write multiline text to a file rather than interpolating it into the shell.
Do not call `finalize_task_to_main.sh` just to create a PR: it also merges.

The PR body or a linked versioned document contains:

- Stable plan name/revision, goal, agreed scope, key contracts and authorization.
- Current commit, contract-to-check results, commands/environment, evidence links.
- Stable open/closed finding IDs; missing evidence and explicitly pending human decisions.
- Which phase is being accepted and the allowed next action.

For a nontrivial task, use this compact body order so another thread can review the PR without reading the chat history:

```text
计划名称 / 修订：
目标与排除项：
基线、分支、worktree、完整 HEAD：
合同 C1...Cn：
代表性真实路径：
测试载具自检与故障注入：
代码验收证据：
部署验收证据：
业务验收证据：
外部等待证据：
开放/关闭/等待/需人工决策的问题：
允许的下一步：
```

The PR is incomplete when a script, fixture, mock, generated input, or verification command required to reproduce the claim is only in an untracked local directory. Commit it or explicitly mark the claim unreviewable. Counts alone do not close a contract.

Keep data sanitized. The user can forward only the PR link, full HEAD and evidence
link; the reviewer retrieves the complete record. An uncommitted diff is not part
of that PR snapshot. If unavoidable, disclose and review it separately, then
commit/push and reassess freshness before merge. Cross-repository work binds every
PR and source commit, not just the orchestration repository.

## Review and repair

The reviewer stays read-only unless explicitly authorized to write review comments
or implement. A user-forwarded independent report bound to HEAD is sufficient;
GitHub approval UI is not required and does not replace the report. Executor
self-review cannot satisfy independence even when a different client is used.

Repair the same PR. Update the current evidence and retain closed findings unless
new changes affect them. A required human decision is asked directly in chat with
an explicit pause; do not hide it in a checkbox or interpret forwarding a PR as
approval of that decision.

## After an applicable pass

1. Confirm the plan, scope, independent report, current PR HEAD and any extra diff.
   Check new main/integration changes; rerun affected checks. Behavioral changes
   need focused re-review. Unrelated changes may preserve the previous review,
   with the difference assessment recorded at the new SHA.
   A recorded review SHA is not a user-imposed source pin. Preparing an in-scope
   focused re-review does not itself need human confirmation; the merge stays
   gated. Ask only for an actual unresolved choice or authorization change.
2. Only after that assessment, mark the Draft ready with `gh pr ready`. Read back
   HEAD and readiness; do not enable auto-merge while acceptance is pending.
3. Run `finalize_task_to_main.sh <branch> --reviewed-head <full-sha> --verify
   '<targeted command>'`. The explicit pin is checked after refreshing main and
   again after verification, before push/merge. It detects version drift; it is
   **not** proof of independent approval. The caller must first satisfy step 1.
   Tracked working-tree/index changes left by verification also block publishing
   under the pin; commit and assess them instead of testing one version and
   merging another. Untracked evidence files are not reviewed source commits.
4. On a pin mismatch, stop finalization, inspect the difference and reassess the
   applicable review. Do not automatically replace the pin with current HEAD.
5. Complete applicable post-merge verification and root-main CodeSight refresh
   for code changes. Run `cleanup_task_worktree.sh` from root, preserving evidence.

Plain authorized implementation without an independent gate may finalize without
`--reviewed-head`; the flag does not create a new approval requirement. Explicit
environment/business-write limits continue to bind either mode.

Code acceptance can release an authorized deployment step while live/business
acceptance remains open. Missing required pre-merge checks block code acceptance;
checks that require deployment stay in the later phase. Waiting for a natural
sample is reported with its resumption event, not repeatedly resubmitted unchanged.
