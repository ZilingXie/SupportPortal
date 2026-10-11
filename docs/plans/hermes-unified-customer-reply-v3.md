# Hermes Unified Customer Reply — v3

## Contract

计划名称：`Hermes Unified Customer Reply`；修订：`v3`。

所有 Hermes 客户可见回复统一由 `hermes_persona` 生成，服务端持有
`hermes_customer_reply_basis_v1` 事实 basis，Persona 只负责自然措辞。
自动化回复使用 `publication_policy=auto`，调查回复保持人工审批；称呼由
服务端投影，有姓名为 `Hi <Name>,`，无姓名为 `Hi Customer,`，翻译失败不投递。

## Baseline and scope

实现分支从 `main` 的祖先 `2107e80a` 上创建，当前 main 为
`c6addcc0`；期间主线变更与本计划无冲突。范围为 Hermes automation、
investigation、conversation follow-up、message action 的客户回复事实合同、
Persona draft 门禁、Prompt 版本和针对性测试；不包含数据库迁移、Production
发布、真实客户工单或外部业务写入。

## Evidence ledger

| ID | Contract | Mechanism | Verification |
| --- | --- | --- | --- |
| C1 | Work → Persona 绑定 | `hermes_customer_reply_basis_v1` carries source turn/route/facts/claims/policy | targeted unit + real Hermes tool entry |
| C2 | 单一回复所有权 | `reply_owner=hermes_persona`; legacy `skip_persona` is compatibility metadata | automation work result assertions |
| C3 | 安全与事实门禁 | server validator rejects payment requests, internal terms, duplicate greeting, handoff re-ask, turn mismatch | negative validator tests |
| C4 | 称呼与语言 | `apply_greeting_projection` + existing translation/preparation ledger | greeting and delivery tests |
| C5 | Investigation gate | investigation draft remains `manual`; automation remains `auto` | publication gate tests |

## Stopping point

代码和离线/入口测试完成后，保留 Draft PR 与任务工作区，独立验收、
Preproduction Prompt Release、发布与真实客户投递证据另行记录；本计划不授权
Production。Enablement 的旧 relay readback 仍以 legacy
`account_reply_job_id` 为兼容绑定，直到其释放协议改为读取 Hermes delivery
ledger；这是一项待确认的范围边界，不宣称已完成全量替换。
