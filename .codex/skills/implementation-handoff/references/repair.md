# Repair after failed acceptance

Keep one current repair handoff attached to the same plan and PR. Use stable finding IDs (F1, F2) across rounds; do not renumber an unresolved defect merely because a new review occurred.

Classify every finding before repairing it: `contract-gap`, `implementation`, `test-carrier`, `evidence-gap`, `waiting-for-evidence`, or `needs-human-decision`. Only the first four are implementation work. `waiting-for-evidence` records the resumption event and does not create another code round; `needs-human-decision` pauses dependent work.

For each open finding record:

| Field | Required substance |
| --- | --- |
| Contract / origin | Original unmet contract, repair regression, or newly discovered material defect |
| Reproduction | Trigger, input/state or event ordering, actual consequence |
| Cause | Verified cause versus remaining hypothesis |
| Affected boundary | Real callers, consumers, exits and recovery states sharing this mechanism |
| Repair scope | Smallest change that closes the boundary, preserving agreed behavior |
| Closure | Relevant positive, negative, and recovery outcome; concrete verification |
| Evidence | Commit, check/result, artifact or missing evidence |

Group findings by common cause and dependency. For an exception change, trace the final exception through all affected exits; for a parser, inspect actual consumers; for a guard, inspect callers handling each result. Do not widen this into an unrelated repository audit.

Maintain a short closed/open/waiting-for-evidence/needs-human-decision ledger. Keep closed findings closed unless the new diff affects them. A repair must not remove an existing entry-point test or weaken an assertion without explaining it against the agreed contract.

When a finding is caused by a false-positive or stale test carrier, repair the carrier first and rerun the original failing path. Do not accept a green result until the carrier has demonstrated that it can catch a deliberately injected error and that the evidence belongs to the current run.

If a repaired boundary fails again, reconstruct its state/identity/recovery contract before the next patch. Resolve design facts by inspection; ask the human if the required design changes agreed behavior, scope, resources, or acceptance. Do not substitute a stronger model or add retries as a default escalation.

The reviewer should return a consolidated repair scope, not a succession of isolated example strings. Later findings identify why they block under the original contract or a new material risk. Optional improvements and unchanged waiting conditions do not create new acceptance rounds.

Submit the updated PR HEAD and evidence once the repair's available checks are complete. Distinguish "this finding closed" from "all code accepted" and "business flow accepted". An open human decision or mandatory evidence gap cannot disappear into a partial pass.
