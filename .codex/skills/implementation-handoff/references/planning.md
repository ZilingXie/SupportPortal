# Planning for another executor

The planner resolves the important decisions before handing off. Start with the user's goal and latest agreed choices; do not turn a requested outline into an implementation manual.

## Establish feasibility

Read the current task's source, configuration, and relevant runtime evidence. Identify the baseline, real entry points, caller/callee signatures, persisted fields and legal states, reusable primitives, and affected consumers. Verify decisive facts rather than inferring contracts from names or neighboring implementations. Cite files/symbols and the baseline; avoid line-by-line patches.

Resolve behavior, ownership, transaction/recovery boundaries, and acceptance choices. If a material fact is missing, first perform bounded read-only investigation. If a user decision remains, ask explicitly under the skill's human-decision rule. Until then label the affected phase unresolved, not ready for implementation.

For the actual risk, answer the applicable questions:

- **Identity:** what persisted relationship proves input, work, output, and delivery belong together? Time proximity alone is not identity.
- **Transaction/recovery:** what commits together, what memory mutates before commit, and what survives retry/restart? State the commit-uncertain and partial-failure outcomes. Do not prescribe retries before settling these facts.
- **External boundaries:** who owns each resource/write, what is the allowed call count, and how is repeated execution handled?
- **Deployment:** what source renders the configuration, what proves the target version is running, and what happens on rollback?
- **Evidence:** is the required trace/sample/model endpoint available, and must capture start before execution? Separate unavailable evidence from code defects.

Only cover boundaries involved in this change. Do not add migrations, queues, fallback, or frameworks to satisfy hypothetical cases.

## Produce one self-contained handoff

Include the stable plan name/revision, goal and exclusions, baseline/facts, settled design with files/symbols, critical contract table, ordered implementation steps, dependencies, and exact verification/stopping points. Distinguish existing tests from tests to add and pre-merge from post-deployment checks. Explicitly carry the authorization and independent-review mode.

For broad work, first implement one representative path across the module boundaries, then extend sibling inputs/scenarios. Assign shared-file integration and shared-environment publication ownership before parallel execution. A representative-path check is an executor checkpoint, not an extra human approval round.

Before handoff, review the proposed plan against actual code for omitted callers, invalid reuse prerequisites, recovery paths and false-positive tests. Replace phrases such as "ensure idempotency" or "reviewer will check concurrency" with the selected mechanism, its failure behavior, and observable acceptance. Keep equivalent local coding choices with the executor.

## Compact example

A reply verifier plan should identify different first-email and later-comment entry contracts, the stored job/message/delivery relationship, and where content is read. Its checks should prove a valid delivery passes, an unrelated delivered message fails, and unavailable identity cannot silently pass. It should not dictate a universal time window or require every application to implement the same schema.
