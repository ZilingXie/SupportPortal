# Planning for another executor

The planner resolves the important decisions before handing off. Start with the user's goal and latest agreed choices; do not turn a requested outline into an implementation manual.

For a nontrivial task, write the handoff using [implementation-plan-template.md](implementation-plan-template.md). The executor must be able to start from that record alone. A plan that leaves the executor to infer a state, identity, failure, or evidence contract is not ready for implementation.

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

For high-risk or externally visible work, also record these execution gates before handoff:

- **Representative path:** one real entry from input through the persisted or external result. Sibling paths wait until this path proves the mechanism.
- **Test-carrier proof:** how the harness proves the command, session, fixture, and log it reads belong to this run; include one deliberate fault or rejection that must be detected.
- **State matrix:** legal states, unknown/error states, transition owner, and the fail-closed result for every missing or conflicting observation.
- **Evidence map:** each acceptance claim mapped to a command, environment, commit, artifact, and layer (`code`, `deployment`, `business`, or `external waiting`).
- **Stop points:** exact conditions that require the executor to ask the human, with dependent actions paused.

Use stable contract IDs (`C1`, `C2`, ...) and keep them unchanged during repairs. A compact matrix should look like this:

| Contract | Real entry / state | Identity or transaction proof | Positive result | Failure / recovery result | Verification and evidence |
| --- | --- | --- | --- | --- | --- |
| C1 | concrete command or handler | persisted relationship or commit boundary | exact state/output | exact fail-closed or retry behavior | command, environment, artifact |

Do not mark a contract complete from a test count, a health check, or an accepted request alone. Those prove only the layer they actually exercise.

For broad work, first implement one representative path across the module boundaries, then extend sibling inputs/scenarios. Assign shared-file integration and shared-environment publication ownership before parallel execution. A representative-path check is an executor checkpoint, not an extra human approval round.

Before handoff, review the proposed plan against actual code for omitted callers, invalid reuse prerequisites, recovery paths and false-positive tests. Replace phrases such as "ensure idempotency" or "reviewer will check concurrency" with the selected mechanism, its failure behavior, and observable acceptance. Keep equivalent local coding choices with the executor.

The handoff is ready only when the executor can answer, without asking the planner to reconstruct context: what to edit, what not to edit, which real path to exercise first, what failure must be rejected, what evidence is sufficient, and where to stop for a human decision.

## Compact example

A reply verifier plan should identify different first-email and later-comment entry contracts, the stored job/message/delivery relationship, and where content is read. Its checks should prove a valid delivery passes, an unrelated delivered message fails, and unavailable identity cannot silently pass. It should not dictate a universal time window or require every application to implement the same schema.
