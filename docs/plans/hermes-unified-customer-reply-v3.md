# Hermes Unified Customer Reply — v3.2

## Contract

计划名称：`Hermes Unified Customer Reply v3.2 — Enablement Relay Identity Fail-Closed`；修订：`v3.2`。

所有 Hermes 客户可见回复统一由 `hermes_persona` 生成，服务端持有
`hermes_customer_reply_basis_v1` 事实 basis，Persona 只负责自然措辞。
自动化回复使用 `publication_policy=auto`，调查回复保持人工审批；称呼由
服务端投影，有姓名为 `Hi <Name>,`，无姓名为 `Hi Customer,`，翻译失败不投递。

## Baseline and scope

修复基线为完整 HEAD `e1f7c2a88577a57e5a03a665ff8296292cf2ebde`，当前 main 为
`d8fe89e7`；本轮保持同一任务分支。范围为 Hermes automation、
investigation、conversation follow-up、message action 的客户回复事实合同、
Persona draft 门禁、Prompt 版本和针对性测试；不包含数据库迁移、Production
发布、真实客户工单或外部业务写入。

## Repair contract and evidence ledger

| ID | Contract | Mechanism | Verification |
| --- | --- | --- | --- |
| C1 | Work → Persona 绑定 | `hermes_customer_reply_basis_v1` carries source turn/route/facts/claims/policy | targeted unit + real Hermes tool entry |
| C2 | 单一回复所有权 | `reply_owner=hermes_persona`; legacy `skip_persona` is compatibility metadata | automation work result assertions |
| C3 | 安全与事实门禁 | server validator rejects payment requests, internal terms, duplicate greeting, handoff re-ask, turn mismatch | negative validator tests |
| C4 | 称呼与语言 | `apply_greeting_projection` + existing translation/preparation ledger | greeting and delivery tests |
| C5 | Investigation gate | investigation draft remains `manual`; automation remains `auto` | publication gate tests |
| R1 | 权威事实不可被模型覆盖 | `tool_save_reply_draft` 只从持久化 Work/investigation 结果生成 basis；缺失或 turn 不匹配时 fail-closed | basis/draft 入口反例测试 |
| R2 | Validator 遵循业务语义 | 支付/账单凭据、重复已收字段、虚构恢复/状态/时间承诺被阻断；否定表达和 `network route` 保持可用 | validator 正反例回归 |
| R3 | 自然 Persona 措辞 | Fraud、Detailed Invoice、Enablement 提供事实、缺失项、阶段和允许承诺；Prompt 不再要求固定 VERBATIM 锚点 | Prompt assembly 与 route basis 测试 |
| R4 | 投递后业务收尾 | Hermes delivery readback 投影 Enablement 兼容身份、释放 gated email/relay，并完成 Fraud/Suspension 审核分派与收尾 | worker delivery side-effect 与 email/handoff 测试 |
| R5 | 统一所有权与兼容身份 | `reply_owner=hermes_persona`；旧 `account_reply_job_id` 仅作 `hermes_compatibility_only` relay identity，不能生成第二封回复 | Enablement/Fraud/Suspension tool 与 worker 测试 |

## Repair stopping point

本轮修复保留 Draft PR 与任务工作区，不执行 finalize、Preproduction/Production
发布或真实客户投递。代码和离线/入口证据完成后提交独立复验；代码验收、
Preproduction 发布验证和真实业务验收仍分别判断。Enablement 保留
`account_reply_job_id` 作为兼容 relay identity，但标记
`hermes_compatibility_only=true`，旧 worker 不能再生成第二封正文；Enablement
缺少、错误或无法读回该身份时 fail-closed，进入 `human_review_required`，不生成
客户回复、不进入 Persona、不释放内部邮件或 relay，并记录原因码和幂等事件；Hermes
delivery readback 负责释放内部邮件、relay 请求和后续业务收尾。
