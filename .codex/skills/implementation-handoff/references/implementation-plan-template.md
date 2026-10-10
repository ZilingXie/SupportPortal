# Implementation handoff template

Copy this template into the PR body or a linked versioned document for every nontrivial handoff. Remove guidance comments before submission. Keep one current revision; do not maintain competing summaries in chat, PR comments, and local notes.

## Plan identity

- 计划名称：`<stable name>`
- 修订：`v<N>`
- 目标：`<observable outcome>`
- 执行角色：`planner / executor / reviewer`（client/model independent）
- 执行约束：`规划者解决关键设计；执行者不应依赖隐藏推理或聊天上下文，低思考强度也能按此记录执行`
- Review gate：`none / independent acceptance before merge`
- Authorization and limits：`<allowed side effects, environment, deployment and business-write limits>`

## Baseline and scope

- Source baseline: `<branch, full commit, worktree>`
- Verified facts: `<facts with source/symbol or command>`
- Assumptions: `<clearly labeled assumptions>`
- In scope: `<files, entry points, phases>`
- Excluded: `<business behavior, migration, deployment or other limits>`
- Open human decisions: `<decision, options, consequence>` or `none`

## Representative path

- Real entry: `<CLI/API/worker/script/scenario>`
- Input: `<concrete sanitized shape>`
- Persisted or external result: `<exact state/output>`
- Identity chain: `<how input, work, output and delivery are tied together>`
- First proof before sibling paths: `<positive and negative cases>`

## Contract matrix

| Contract | Trigger / state | Mechanism and identity proof | Positive result | Failure / recovery result | Check and evidence |
| --- | --- | --- | --- | --- | --- |
| C1 | `<...>` | `<...>` | `<...>` | `<...>` | `<...>` |

## Test-carrier proof

- Commands and environment: `<exact commands>`
- Freshness/ownership checks: `<run id, process, log, database or artifact binding>`
- Deliberate fault/rejection: `<what is injected and expected failure stage>`
- Real-entry test: `<why this is not only a source-string or helper test>`
- Recovery test: `<original input retried after clearing the fault, if applicable>`

## Ordered implementation

1. `<preflight and representative path>`
2. `<smallest implementation change>`
3. `<targeted positive, negative and recovery checks>`
4. `<sibling paths and integration checks>`
5. `<PR evidence update and handoff>`

## Stop points

Pause with `需要你确认：任务已暂停在 ...` when the work requires a product or architecture choice, new resource/migration/side effect, changed acceptance, uncertain ownership/authorization, or evidence that the plan says is mandatory but is unavailable. Record the verified facts, options, recommendation and consequence; do not continue dependent actions.

## Evidence ledger

| Layer | Claim | Command / artifact | Environment and commit | Result | Status |
| --- | --- | --- | --- | --- | --- |
| code / deployment / business / external waiting | `<claim>` | `<...>` | `<...>` | `<...>` | `open / closed / waiting-for-evidence / needs-human-decision` |

## Handoff

- PR: `<URL or pending>`
- Full HEAD: `<sha>`
- Changed files and extra local diff: `<...>`
- Tests and actual results: `<...>`
- Open findings: `<stable IDs>`
- Waiting evidence and resumption event: `<...>`
- Allowed next action: `<...>`
