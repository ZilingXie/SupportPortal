# 启用 n8n 工作流说明

范围、命名规则、目录结构和旧名对照见[工作流目录](./README.md)；2026-09-16 已完成统一改名，并按用户要求回退当日第一批修复。本页各节标题保留旧名。16 段 **n8n description** 与回退后的远端回读一致；其后的入口、主路径和注意事项是配置分析，不是执行成功证明。流程 ID 取自各标题的 n8n 链接。

说明优先使用已发布图；画布上禁用或未连接的节点不计入当前主路径。知识生成、Slack 操作与质检流程仍各自承担原有职责，纳入本地文档不代表都直接调用 SupportPortal。

<a id="w-208nrQNRfpkSkQhM"></a>

## [slack]Notify Handoff（旧名 2_slack - SupportPortal Account Handoff -> Slack）

[n8n 工作流](https://n8n.stellarix.space/workflow/208nrQNRfpkSkQhM) · 直接接入 / Slack 通知

**n8n description**

> 接收 SupportPortal 的 account_automation_handoff_confirmed 事件，校验字段并按 event_id 在 PostgreSQL 认领去重，将交接通知发送到 Slack，再记录投递结果。

- **入口与主路径**：带鉴权的 POST Webhook → `Validate Event1` → `Claim Event1` → 仅新认领事件发送 Slack → `Mark Delivered1`。
- **契约与依赖**：事件类型 `account_automation_handoff_confirmed`、`schema_version=1`；PostgreSQL 表 `n8n_supportportal_slack_events` 按唯一 `event_id` 执行 `ON CONFLICT DO NOTHING`，记录 pending/delivered 及 Slack 投递关联。
- **与项目的关系**：SupportPortal 向 Slack 发出账户自动化交接通知；生产执行失败已关联 `[ops]Error Alert`。
- **排错与重试**：先核对事件认领记录与 Slack 实际消息；重复事件返回已有状态，不能据此假定 pending 事件会自动补发。发送已成功但记账失败时，重跑发送节点可能重复通知。

<a id="w-b1Unpl6miABzcTmZ"></a>

## [review]Case|Subflow（旧名 case_review_subflow）

[n8n 工作流](https://n8n.stellarix.space/workflow/b1Unpl6miABzcTmZ) · 配套 / 支持质检

**n8n description**

> 接收父流程的工单 ID 和审查类型，获取 Zendesk 工单与评论，由 AI 提炼问题和摘要，在 Slack 等待人工评审，再添加 reviewed 标签并写入 Google Sheets；属于支持质检配套流程。

- **入口与主路径**：父流程输入 `caseId`、`tag` → Zendesk 工单、负责人及评论 → AI 问题概括和摘要 → Slack `sendAndWait` 表单 → 添加 `reviewed` 标签 → Google Sheets。
- **调用与依赖**：由 `[review]Random`、`[review]Aged`、`[review]Rating` 三个评审流程按 workflow ID 调用；使用 Zendesk、AI、Slack、Google Sheets，错误交给 `[ops]Error Alert`。
- **与项目的关系**：支持质检配套流程，当前没有直接调用 SupportPortal API。
- **人工检查**：表单记录审查类别、建议和通过/失败。已经等待人工输入的 execution 应沿原等待入口继续，重新执行会创建另一轮评审。
- **已知配置问题**：状态条件为 `status != solved OR status != closed`，无法排除这两种状态；详见[待核对配置](#configuration-findings)。

<a id="w-zc2ndUDqDAS0uX1Y"></a>

## [case]Sync Comments（旧名 commen_sync_ecs_production）

[n8n 工作流](https://n8n.stellarix.space/workflow/zc2ndUDqDAS0uX1Y) · 直接接入 / 评论同步

**n8n description**

> 接收 Zendesk 新评论事件，按已有 Case 归属向旧 Production 同步评论快照，并向 ECS Production、Preproduction 发送 comment.created 事件；连接 Zendesk 与 SupportPortal 的评论处理链路。

- **入口与主路径**：Zendesk 新评论 POST Webhook → 并行查询 Case/执行归属 → 获取 Zendesk 评论 → 向所属目标提交。
- **实际目标**：旧 `support.stellarix.space/production/api/...` 分支 PUT 评论快照；ECS Production 和 Preproduction 分支在查询到关联执行后 POST `comment.created` 到各自 `/automation/{environment}/v1/intake`。
- **与项目的关系**：Zendesk 到 SupportPortal 的评论入口。名称中的 production 不能代表全部目标环境。
- **画布与执行区别**：旧根路径 `/api/...` 分支没有接入主链。评论查询含 `include=users&per_page=100` 及相关快照/分页配置；本次未以真实多页评论验证运行结果。
- **排错与重试**：分别核对三个目标分支的归属查询、输入、返回值和接收方事件记录；一个分支成功不代表其他分支成功，重放前确认已落库的事件。生产执行失败已关联 `[ops]Error Alert`。

<a id="w-GgDxPEWtW7ltT5BW"></a>

## [kb]Build|CSD（旧名 CSD_2_KB）

[n8n 工作流](https://n8n.stellarix.space/workflow/GgDxPEWtW7ltT5BW) · 知识入库 / CSD

**n8n description**

> 定时扫描近期已解决的 CSD Bug，经 PostgreSQL 去重和 AI 筛选后生成英文 Zendesk KB 草稿，并在 SupportPortal Memory 创建 Wiki、上传正文、触发 ingest。

- **入口与主路径**：Schedule Trigger → OAuth → Jira → 顺序处理 → PostgreSQL 去重 → AI 筛选/生成英文 KB → Zendesk 草稿 → SupportPortal Memory。
- **筛选与输出**：查询 CSD 项目中 RESOLVED 的 Bug，更新时间在过去 24 小时、创建时间在过去 60 天，排除 Won't Do、Duplicate、Reject。AI 判断是否具备清晰原因和解决方案。
- **与项目的关系**：调用 `/dashboard/memory/api/v1/knowledge/wiki/create`、`/wiki/raw/write`、`/wiki/ingest`；契约来源见 [Memory 接入说明](../../deploy_hermes_investigator_ecs.md)。Zendesk 设置 `draft=true`、`notify_subscribers=false`。
- **画布与依赖**：手动触发节点没有连接主链；旧 `2_rag` 节点禁用。依赖 Jira/OAuth、PostgreSQL、AI、Zendesk、Memory；错误交给 `[ops]Error Alert`。
- **排错与重试**：`csd` 去重记录在 AI 和外部写入之前产生。整轮重跑可能跳过未完成条目；直接删去重记录又可能重复创建草稿/Wiki。先定位已完成的外部写入和失败步骤，再决定恢复方式。本次未重放历史失败执行。

<a id="w-dV5vNA6l1MbDMHZt"></a>

## [ops]Error Alert（旧名 error_handle）

[n8n 工作流](https://n8n.stellarix.space/workflow/dV5vNA6l1MbDMHZt) · 配套 / 故障上报

**n8n description**

> 接收关联工作流的执行错误，由 AI 生成简短故障摘要，再上报 n8n 状态页的 failure 接口；属于支持自动化的告警配套流程，不负责自动重试。

- **入口与主路径**：Error Trigger → AI 生成约十词的故障摘要 → POST `https://n8n.stellarix.space/status/api/v1/ingest/failure`。
- **与项目的关系**：支持自动化的故障可见性；Case Intake/Comments/Status、Slack Forward/Action/Handoff、质检、知识、Slack Bot 和测试流程均已关联此错误工作流。
- **排错边界**：上报失败不等于原始执行没有错误；需同时查看源 execution 和本流程的上报节点。
- **验证与恢复**：该流程不负责自动重试。生产错误触发链需要对应触发条件；手动测试单个节点不能证明生产错误通知链完整。本次未触发错误测试。

<a id="w-G7snyHhdBCnIpbJV"></a>

## [review]Aged（旧名 longage_case_review）

[n8n 工作流](https://n8n.stellarix.space/workflow/G7snyHhdBCnIpbJV) · 配套 / 长龄工单质检

**n8n description**

> 每个工作日北京时间 06:00 查询创建超过 30 天且未解决的 Tier1 工单，随机抽取 1 条调用 [review]Case|Subflow，进入 Slack 人工评审与质检记录流程。

- **入口**：工作流时区 `Asia/Shanghai`，cron `0 6 * * 1-5`。
- **主路径**：查询创建超过 30 天、未解决的 Tier1 工单并应用排除条件 → 从返回结果随机取 1 条 → 以 `tag=Longaged` 调用 `[review]Case|Subflow`。
- **与项目的关系**：支持质量检查；实际写入和人工等待由评审子流程承担。
- **实现与排错**：抽样代码是 `Math.min(1, totalCases)`，不能按旧代码注释理解为抽取 5 条。重新抽样可能选到不同工单；优先检查原有子流程执行。

<a id="w-WT43uQ1i8SPsYRJi"></a>

## [review]Rating（旧名 negative_case_review）

[n8n 工作流](https://n8n.stellarix.space/workflow/WT43uQ1i8SPsYRJi) · 配套 / 满意度质检

**n8n description**

> 接收 Zendesk 满意度 Webhook，跳过评分为 GOOD 的事件，将其余工单标记为 Negative 并调用 [review]Case|Subflow，进入 Slack 人工评审与质检记录流程。

- **入口与主路径**：Zendesk 满意度 POST Webhook → 判断评分 → GOOD 停止，其余输入生成 `tag=Negative`、`caseId` → `[review]Case|Subflow`。
- **与项目的关系**：支持质量检查，依赖评审子流程和其中的 Zendesk、Slack、Google Sheets。
- **排错边界**：当前配置并非仅接受经过严格校验的 BAD；非 GOOD 的其他值也进入评审分支。排查时先看实际评分输入。
- **重试**：先检查是否已创建等待中的评审，避免重复发出表单。

<a id="w-1am2EuuDMV3RUwsJ"></a>

## [case]Intake|ECS Route（旧名 new_case_2_supporportal_prod）

[n8n 工作流](https://n8n.stellarix.space/workflow/1am2EuuDMV3RUwsJ) · 直接接入 / ECS 新工单

**n8n description**

> 接收 Zendesk 新工单，补全评论、请求人和组织资料，按公司 ID 名单将 ticket.created 事件分流至 SupportPortal ECS Preproduction 或 Production，并记录执行关联信息。

- **入口与主路径**：Zendesk 新工单触发 → 工单、评论、请求人 → 必要时补组织资料 → 标准化事件 → 公司 ID 名单分流。
- **实际目标**：匹配名单发送至 ECS Preproduction，其余发送至 ECS Production；使用 `https://supportcenter.stellarix.space/automation/{environment}/v1/intake`，事件为 `ticket.created`。
- **与项目的关系**：当前 ECS 工单自动化接入；按输入与实际请求 URL 判断环境，不能仅看流程名称。
- **凭据与排错**：回退恢复了原有组织查询、请求人字段和状态映射，但保留 n8n Zendesk Credential 引用，没有恢复旧内联 Authorization/Cookie。旧 `/production/account` 节点未接入主链。发布版本为 `35bebae8-92b6-49e8-b3b6-2b1c4ea5f7ed`；本次未用真实工单触发验证，重放前仍需检查接收方是否已有同一创建事件。

<a id="w-qFSNOmYXr97N2UGX"></a>

## [case]Intake|EC2 Staging（旧名 new_case_2_supporportal_staging）

[n8n 工作流](https://n8n.stellarix.space/workflow/qFSNOmYXr97N2UGX) · 直接接入 / 历史 Staging

**n8n description**

> 接收 Zendesk 新工单并补全评论、请求人和组织资料，提交到旧 EC2 的 /automation/staging/v1/cases；属于历史接入配置，需核对该入口当前可用性。

- **入口与主路径**：Zendesk 新工单触发 → 评论、工单、请求人和组织补全 → 标准化 → POST `https://support.stellarix.space/automation/staging/v1/cases`。
- **与项目的关系**：历史 EC2 接入配置，不是当前 ECS Preproduction 域名。
- **画布与实际门控**：优先级/公司判断旁支没有下游投递节点，不拦截主 POST。
- **已知风险**：该流程已按回退要求重新启用并移回 `01 - Cases`。仓库 Nginx 配置仍让旧 `/automation/staging` 返回 410，且它与 ECS Route 同时监听新工单，存在重复接入和持续失败风险；本次没有触发业务 execution。

<a id="w-r1HIW8UNuCabiOPn"></a>

## [slack]Forward Thread|Prod（旧名 NonAutomate_to_slack_fixed）

[n8n 工作流](https://n8n.stellarix.space/workflow/r1HIW8UNuCabiOPn) · 直接接入 / Slack 消息

**n8n description**

> 供 [slack]Route Support 调用：校验指定内部频道内由人发送的线程 @提及消息，解析 ECS Production 的 Engineer Case 线程绑定，再将消息转发给 SupportPortal。

- **入口与主路径**：由 `[slack]Route Support` 按 workflow ID 调用 → 校验人类消息、非 bot、无 subtype、指定内部团队/频道、非空线程文本及 @提及 → 查询线程绑定 → 已绑定时提交消息。
- **实际目标**：ECS Production 的 `/automation/production/api/integrations/slack/engineer-cases/thread-bindings/resolve` 和同前缀的 `/messages`。
- **与项目的关系**：数据方向是 Slack → SupportPortal，不能按名称理解为向 Slack 发消息。
- **排错与重试**：先看各过滤条件、线程绑定返回和消息接收记录；不要为了通过过滤而改写真实身份或线程字段。

<a id="w-vvyPwdWXvJENN1zm"></a>

## [review]Random（旧名 random_case_review）

[n8n 工作流](https://n8n.stellarix.space/workflow/vvyPwdWXvJENN1zm) · 配套 / 随机质检

**n8n description**

> 每个工作日北京时间 06:00 查询未解决且未 reviewed 的 Tier1 工单，为配置的每位负责人随机抽取 1 条，调用 [review]Case|Subflow 完成人工评审，并更新 reviewed 标签。

- **入口**：工作流时区 `Asia/Shanghai`，cron `0 6 * * 1-5`。
- **主路径**：查询未解决且没有 `reviewed` 标签的 Tier1 工单 → 对六位已配置负责人分别从返回结果随机抽 1 条 → 以 `tag=Random` 调用 `[review]Case|Subflow` → 更新 `reviewed` 标签。
- **与项目的关系**：支持质检配套；人工评审和记录由子流程承担。
- **画布与重试**：`get_random_case` 节点未连入主路径。检查父子 execution 和标签更新时间；整轮重跑会重新抽样，不能替代恢复原评审。

<a id="w-FKv8vtZBQk6tH4Gt"></a>

## [slack]Handle Action|Preprod（旧名 Slack Interaction to SupportPortal - Consume Button V3）

[n8n 工作流](https://n8n.stellarix.space/workflow/FKv8vtZBQk6tH4Gt) · 直接接入 / Slack 操作

**n8n description**

> 接收 Slack 按钮交互，校验团队、频道及线程绑定，转发至 SupportPortal ECS Preproduction 的 Hermes actions 接口，再更新 Slack 原消息移除操作按钮。

- **入口与主路径**：Slack 交互 POST Webhook → 解析 action → 团队、频道和线程过滤 → 解析绑定 → POST Hermes action → 在有效 `response_url` 下替换 Slack 原消息、移除按钮并追加已提交说明。
- **实际目标**：固定 ECS Preproduction；接口为 `/automation/preproduction/api/integrations/slack/hermes-cases/thread-bindings/resolve` 和同前缀的 `/actions`。
- **与项目的关系**：把人工按钮操作传给 Hermes。payload 的环境字段不能证明目标会动态切换。
- **安全边界**：已发布图没有执行 Slack v0 HMAC 验签。免费版 n8n 当前不能向 Code 节点提供环境变量，SupportPortal 外部 verifier 也已回退；格式检查、Team/Channel 过滤和时间戳检查不能证明请求来自 Slack。禁止把 signing secret 明文写入节点、execution 或 data table；解决验签承载方式前不能把该入口视为安全上线。
- **画布与重试**：旧 Production Engineer Case 分支未连入主链。当前回退版本为 `0192f828-0ee5-414e-87ef-ca67c0225ddb`，没有独立 verifier 草稿。恢复前分别检查 action 是否已接收、Slack 消息是否已更新；重跑可能再次提交动作和改写消息。

<a id="w-kyiA0QuiVx6JJ03i"></a>

## [slack]Route Support（旧名 Slack_zen_Bot）

[n8n 工作流](https://n8n.stellarix.space/workflow/kyiA0QuiVx6JJ03i) · 上游接入 / Slack 建单

**n8n description**

> 监听 Slack 消息，经 AI 筛选和支持请求判定后创建 Zendesk 工单并回传链接；缺少团队资料时等待人工补全。指定内部频道的线程消息通过子流程转交 SupportPortal ECS Production。

- **入口与主路径**：Slack 事件 → AI 首轮过滤及支持请求判断 → PostgreSQL `slack_team` 资料 → 必要时 Slack `sendAndWait` 补资料 → 生成标题 → 创建 Zendesk 工单和评论/关联 → Slack 回传链接。
- **另一条已连接路径**：首轮过滤的 false 分支中，符合配置的内部频道消息调用 `[slack]Forward Thread|Prod`，进入 ECS Production。
- **与项目的关系**：Zendesk 上游来源，并提供内部 Slack 线程向 SupportPortal 的消息通道；依赖 Slack、AI、PostgreSQL、Zendesk、消息转交子流程及 `[ops]Error Alert`。
- **版本边界**：本页按已发布版本描述；该流程的既有草稿仅修改 `Call 'NonAutomate_to_slack'`，尚未发布。当前没有其他启用流程的 divergent draft。
- **预期行为**：创建 Zendesk 记录型工单后立即设为 `solved` 是已确认的产品行为，后续优化不得把它当作故障移除。
- **排错与重试**：优先沿现有人工等待继续；建单后故障需先找已有 Zendesk 工单，避免重复建单。两个 PostgreSQL 节点仍存在字符串插值风险；因当前草稿另有一项未发布修改，本次没有把 SQL 修复混入并发布该草稿。

<a id="w-03B6AvcrOgRkWlUc"></a>

## [case]Sync Status（旧名 status_sync_automation_production）

[n8n 工作流](https://n8n.stellarix.space/workflow/03B6AvcrOgRkWlUc) · 直接接入 / 状态联动

**n8n description**

> 监听 Zendesk 状态变化，获取工单并按 Case 归属把状态同步到旧主栈与旧 Production（仅旧 EC2 环境）。原 ECS 分支调用的 /api/integrations 端点在 ECS 上不存在、从未成功，已于 2026-09-16 移除；ECS 由 /v1/intake 事件驱动，不做状态联动。属于 SupportPortal 状态联动流程。

- **入口与主路径**：Zendesk 状态变化触发 → 读取工单 → 查询 Case 归属 → 旧根 API 与旧 `/production/api` 分支分别 PUT 状态。
- **ECS 分支移除记录（2026-09-16）**：原 ECS 分支的归属检查 GET `supportcenter.stellarix.space/automation/production/api/integrations/zendesk/account-cases/{id}/comment-sync-target` 持续 404——线上该前缀由 `automation_ecs_api` 应答，没有 `/api/integrations/*` 路由，未匹配请求落到 UI 静态兜底（GET 404 / PUT 405，无凭据探测与仓库源码双重确认）。保留执行历史 1138 次全部失败、无成功记录；下游 `Get_Case_Comment3`、`Build complete comment snapshot2`、`Sync comments to automation production` 三处还各自引用本流程不存在的 `Zen_New_Comment_Webhook`，因 404 发生在上游而从未被执行到。已删除整条 ECS 分支（5 个节点）并发布版本 `6cfb5781`。
- **与项目的关系**：旧 EC2 双环境状态联动；ECS 侧不经此流程。ECS 的 `ticket.updated` intake 事件目前也仅留痕（route 阶段标记 `ticket_updated_no_turn` 忽略），如需 ECS 状态联动须先在 ECS 侧实现消费逻辑。
- **排错边界**：移除已回读确认（9 节点、仅两条旧栈分支、激活版本与草稿一致）；后续以真实 Zendesk 状态变化产生的成功 execution 为准。

<a id="w-3zJvu5KQFZIoOoqu"></a>

## [ops]Error Test|Zendesk（旧名 test_error）

[n8n 工作流](https://n8n.stellarix.space/workflow/3zJvu5KQFZIoOoqu) · 配套 / 运维测试

**n8n description**

> 通过聊天入口接收工单号，记录执行信息并查询 Zendesk 工单，用于联调错误处理链路；执行失败时由 [ops]Error Alert 接收，属于运维测试流程。

- **入口与主路径**：公开 Chat Trigger → 使用 `chatInput` 查询 Zendesk 工单 → 记录执行数据。
- **与项目的关系**：错误链路联调工具，失败交给 `[ops]Error Alert`。
- **行为边界**：查询节点复用现有 n8n Zendesk Credential，没有恢复旧内联 Authorization/Cookie。输入有效工单号时可能成功，并非无条件制造错误。发布版本为 `39f40c69-53a6-43ff-9e92-85d017bcb8ba`；本次没有实际触发测试。

<a id="w-MM3Z3T469Eru3Q1I"></a>

## [kb]Build|Solved Cases（旧名 ticket_2_KB）

[n8n 工作流](https://n8n.stellarix.space/workflow/MM3Z3T469Eru3Q1I) · 知识入库 / Zendesk

**n8n description**

> 接收 Zendesk SOLVED 事件，先在 PostgreSQL 去重，再整理评论并由 AI 判断是否适合入库；生成英文 Zendesk KB 草稿、写入 Google Sheets，并提交到 SupportPortal 知识库接口。

- **入口与主路径**：Zendesk 关闭事件 POST Webhook → 仅 SOLVED → PostgreSQL `ticket(solved_ticket)` 去重 → 评论/作者查询、脱敏与对话组装 → AI 技术问题筛选 → KB 标题、正文和 HTML。
- **输出与项目关系**：Zendesk 草稿（`draft=true`、`notify_subscribers=false`）→ Google Sheets → `support.stellarix.space` 的 `/api/engineer/knowledge/articles`。
- **人工门控现状**：Slack 审批节点禁用或未连入主链；当前实际链路依赖 AI 筛选，不能描述为经过人工批准后入库。
- **依赖与重试**：依赖 Zendesk、PostgreSQL、AI、Google Sheets、SupportPortal，错误交给 `[ops]Error Alert`。去重 INSERT ON CONFLICT 在下游处理前执行；整轮重跑可能跳过半成品，强行清除记录可能重复创建草稿。先核对三个输出位置。

<a id="configuration-findings"></a>

## 待核对配置

下列问题来自 2026-09-16 节点图、执行记录和仓库配置核对。表中明确区分已处理项和仍开放项；配置发布均未通过业务重跑验证。

| 顺序 | 工作流 | 已核实配置 | 下一步 |
| --- | --- | --- | --- |
| 1 | [case]Intake\|EC2 Staging | 第一批停用已回退；此前七天 82/82 次执行因旧入口 410 失败，且与 ECS Route 重复处理同一工单 | 当前已启用并移回 `01 - Cases`；重新评估重复接入和 410 风险，任何再次停用都需单独授权 |
| 2 | [[case]Intake\|ECS Route](#w-1am2EuuDMV3RUwsJ) | 第一批缺组织 ID、创建状态和请求人 fallback 修复已回退；安全 credential 引用保留，旧内联认证头未恢复 | 用后续自然事件观察缺组织和非 new 状态输入；不要重放历史建单 execution |
| 3 | [[slack]Handle Action\|Preprod](#w-FKv8vtZBQk6tH4Gt) | 第一批动作校验、回执文案和 SupportPortal 外部验签草稿均已回退；当前 active 图没有 HMAC 验签 | 在不向 workflow 写入 secret 的前提下选择可用验签承载方式；完成前不要把该自定义 Interaction 入口视为安全上线 |
| 4 | [[review]Case\|Subflow](#w-b1Unpl6miABzcTmZ) | `status != solved OR status != closed` 不能排除 solved/closed | 明确各评审类型允许的状态集合，再调整条件并验证边界 |
| 5 | [[slack]Route Support](#w-kyiA0QuiVx6JJ03i) | 两个 SQL 节点仍有字符串插值风险；当前草稿另有一项未发布修改 | 先确认现有草稿的发布归属，再将 SQL 参数化并发布，避免顺带发布用户草稿 |

## 共用恢复注意事项

| 场景 | 已有行为 | 恢复前必须查清 |
| --- | --- | --- |
| [kb]Build\|CSD / [kb]Build\|Solved Cases | 去重写入早于 KB 草稿、Wiki/知识入库 | 哪些输出已创建，哪些步骤未完成；是否能复用已有资源继续 |
| 交接通知 | 先认领事件，再发 Slack、记录 delivered | pending 是否已实际投递；不要把相同事件重发当作可靠补偿 |
| 质检 / Slack 团队资料表单 | Slack sendAndWait 等待人工输入 | 原 execution 是否仍等待；使用原等待入口，避免创建新表单 |
| Slack 建单 / 按钮动作 | 会创建工单或提交业务动作并更新消息 | 接收方是否已处理，重跑是否重复写入 |
| 多环境或多分支同步 | 每个分支有自己的查询和投递 | 各环境独立结果及实际 URL；不能用一个分支的成功替代整轮验收 |

以上用于选择排错路径，不承诺所有失败都能从任意节点无副作用恢复。具体修改、执行和发布仍按当前任务授权及 [项目规则](../../../AGENTS.md) 操作。
