# Executing an agreed plan

1. **Recover the current agreement.** Read the latest plan/revision and open findings, source baseline, task workspace, allowed environment/side effects, and review gate. Retrieve available context yourself; do not ask the user to reconstruct it. Confirm critical prerequisites against current source before dependent edits. Material disagreement with the plan requires a human decision; equivalent implementation choices do not.
2. **Implement a representative path.** Exercise the real entry before copying the pattern into sibling routes. Reuse existing primitives. Keep ownership and state semantics explicit. Do not invent storage fields, tool names, request schemas, or helper prerequisites to fit a mock.
3. **Verify the changed contract.** Invoke the CLI/handler/worker/scenario with default and relevant alternate inputs; mock external boundaries. Assert persisted identity/state and relevant external calls. Negative tests must fail at the intended stage, not merely raise any exception. Do not swallow assertions. Source-string checks are supplementary, not entry-point evidence. Use the actual database engine when the guarantee depends on it.
4. **Check test detection where it matters.** For a material bug fix, use an old-version comparison or bounded fault injection when feasible. Recovery tests must retry the original payload after clearing faults, without test-only reconstruction of information production lost. Assert final data, not only helper call counts. Do not apply expensive reverse tests to trivial edits.
5. **Review before handoff.** Check the new diff and actual sibling consumers of changed mechanisms. Complete locally available required checks. Map each critical contract to evidence; disclose missing evidence and why. If the same boundary fails after repair, return to its design and reproduction rather than adding another patch or silently switching models.
6. **Publish a reviewable version.** Follow the project's branch/PR rules. Commit and push task files, including new tests. Update the same PR with the current evidence and unresolved findings. For gated work, stop before finalize/merge/deploy. Keep the workspace for repair. For ungated work, continue the authorized finalization workflow; this skill does not add a new wait.

## Stop versus continue

- Unresolved product/architecture choice, expanded side effect, changed user pin, weaker required evidence, uncertain resource ownership: explicitly ask the human and pause dependent work.
- Ordinary implementation bug, test fixture mismatch, equivalent refactor: repair within scope and verify.
- Required natural sample or unavailable environment: state the exact missing evidence and resumption event. Do not manufacture success, endlessly poll, or repeatedly submit unchanged acceptance requests.
- Matching review pass and unchanged scope: resume without asking for another "continue". Reassess any new diff and preserve the project's release gates.

Report the actual outcome, remaining limits, and allowed next action. Do not replace per-contract evidence with a large regression-test count.
