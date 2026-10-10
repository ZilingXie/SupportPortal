# Executing an agreed plan

Before the first edit, recover the current plan revision, source baseline, worktree/branch, PR and review gate. Confirm them against the current workspace. If the plan, ownership, scope, or required evidence materially disagrees with the repository, stop the dependent work and ask the human; do not silently rewrite the contract.

1. **Recover the current agreement.** Read the latest plan/revision and open findings, source baseline, task workspace, allowed environment/side effects, and review gate. Retrieve available context yourself; do not ask the user to reconstruct it. Confirm critical prerequisites against current source before dependent edits. Material disagreement with the plan requires a human decision; equivalent implementation choices do not.
2. **Prove the test carrier.** Before relying on a high-risk result, verify fresh command/session state, process ownership, fixture identity, and per-run logs. Save exit code and stderr, clear stale outputs on failure, and distinguish expected rejection from harness failure. Inject one bounded fault or invalid input and confirm the harness fails at the intended stage.
3. **Implement a representative path.** Exercise the real entry before copying the pattern into sibling routes. Reuse existing primitives. Keep ownership and state semantics explicit. Do not invent storage fields, tool names, request schemas, or helper prerequisites to fit a mock.
4. **Verify the changed contract.** Invoke the CLI/handler/worker/scenario with default and relevant alternate inputs; mock external boundaries. Assert persisted identity/state and relevant external calls. Negative tests must fail at the intended stage, not merely raise any exception. Do not swallow assertions. Source-string checks are supplementary, not entry-point evidence. Use the actual database engine when the guarantee depends on it.
5. **Check recovery and detection where it matters.** For a material bug fix, use an old-version comparison or bounded fault injection when feasible. Recovery tests must retry the original payload after clearing faults, without test-only reconstruction of information production lost. Assert final data, not only helper call counts. Do not apply expensive reverse tests to trivial edits.
6. **Review before handoff.** Check the new diff and actual sibling consumers of changed mechanisms. Complete locally available required checks. Map every contract to code, deployment, business, or external-waiting evidence; disclose missing evidence and why. If the same boundary fails after repair, return to its design and reproduction rather than adding another patch or silently switching models.
7. **Publish a reviewable version.** Follow the project's branch/PR rules. Commit and push every file needed to reproduce the result, including scripts, fixtures and tests. Update the same PR with the current plan revision, full HEAD, evidence ledger, open findings and next action. For gated work, stop before finalize/merge/deploy. Keep the workspace for repair. For ungated work, continue the authorized finalization workflow; this skill does not add a new wait.

## Stop versus continue

- Unresolved product/architecture choice, expanded side effect, changed user pin, weaker required evidence, uncertain resource ownership: explicitly ask the human and pause dependent work.
- Ordinary implementation bug, test fixture mismatch, equivalent refactor: repair within scope and verify.
- Required natural sample or unavailable environment: state the exact missing evidence and resumption event. Do not manufacture success, endlessly poll, or repeatedly submit unchanged acceptance requests.
- Matching review pass and unchanged scope: resume without asking for another "continue". Reassess any new diff and preserve the project's release gates.

When a material decision is required, use this direct stop format and pause dependent work:

> 需要你确认：任务已暂停在 `<step>`。已确认：`<facts>`。选项 A：`<choice and consequence>`。选项 B：`<choice and consequence>`。建议：`<recommendation>`。

Do not convert an unavailable external sample into a code failure. Mark it `waiting-for-evidence`, record the event that can resume the check, and avoid resubmitting unchanged implementation work.

Report the actual outcome, remaining limits, and allowed next action. Do not replace per-contract evidence with a large regression-test count.
