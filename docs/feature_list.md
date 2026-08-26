# SupportPortal 主功能清单

本文件是 SupportPortal 的唯一主功能清单。

维护规则：
- 只记录主功能，不记录 UI 微调、文案小改、工单状态等小改动、纯 bugfix、测试或重构。
- 每条功能只写一句话，尽量简短，不写原因、实现细节、文件路径或验证信息。
- 跨端同一主功能要在所有相关分类重复记录，文案保持一致，不能写“同上”。
- 主功能完成后，要把对应条目从相关分类的 `未完成` 移到 `已完成`。
- 分类顺序固定为 `Client 端`、`Engineer 端`、`Ticket Dashboard`、`RAG Dashboard`、`RAG`。

## Client 端

### 已完成
- 客户提问会自动生成工单。
- 系统会识别 Agora 范围并分流。
- 系统会用 RAG 自动答复技术问题。
- 证据不足时会转工程师处理。
- 查询扩展会用词典、LLM 和 PRF 优化技术检索。
- 系统会自动识别 RTC 或 Cloud Recording，并在不确定时向客户确认后加载对应的 support prompt。
- 排查型问题会先向客户补齐必要信息，再自动创建工程师工单。
- 客户工单处理支持 main agent 调度 route、RAG 和 review 子 agent。
- Client 对话支持同 ticket 打断重发，并允许不同 ticket 并发等待 AI 回复。
- Client 与 Engineer 共用富文本 composer，支持粗体、斜体、列表、代码块和安全 markdown 渲染。
- 对话支持上传 txt/log/err 日志附件。
- Client AI 只能检索官网文档，Engineer AI 优先检索非官网知识并可按需回查官网文档。
- `/account` 的 Automated execution view 展示三类 active Automation：Account & Billing / Fraud Account、Account & Billing / Account Suspension 和 Backend Operation / Enablement；每个 Case 同时保留其 Primary Category。Backend Operation / Unregistered 仅作为发现 taxonomy 缺口的诊断 fallback，不属于 Automated 或 Human Review membership。
- Quota 自动化会处理配额审核、并发提升和 Big Event 容量报备，最多追问一次后将现有信息交给内部团队。
- Enablement 使用 LLM 从客户原文提取并校验字段证据，不限制 App ID 格式；缺失时生成上下文追问，不确定或多候选时转 Human Review。
- Fraud Account 使用 LLM 收集公司、联系人、使用场景和安全支付概况，Website 为可选，最多追问一次并阻止敏感支付凭据进入派生数据。
- Fraud Account 自动化通过公司 Outlook reply 接收内部处理结果。
- Detailed Invoice 仅保留 Account & Billing 分类，不进入 Automation 执行；既有自动化实现保留供未来启用。
- Enablement 内部回复的完成识别支持任意语言与拼写容错：英文关键词正则保底，正则未命中时由 LLM 单次仲裁（失败或关闭时回退正则结果），命中即取消待发提交确认并走完成关单链路，判定来源写入审计事件。
- Account 入口可通过 HTTP 或手动 UI 创建 Account Case，并记录 Automated 或非自动化路由。
- Account 入口可查看 Account Case 历史和详情。
- staging Account 入口的 AI 消息可由 Admin 选择写入关联 Zendesk ticket 的 internal comment；production Automated case 的 AI 回复自动以公开评论发给客户，人工改派工单后自动停止发言。
- production Automated case 在任何外部副作用前自动由配置的 AI Agent 接手 Zendesk 工单并持久化 ownership 状态，手动按钮已移除；ownership 失败 fail closed 转 Human Review。
- Account Automation 提供 Sid Precise、Sid Bright、Sid Warm 三套独立 Persona presets，首次客户回复随机分配并固定精确版本，完整 Rerun 后重新选择。
- Automation Behavior 只提取结构化字段和处理事实，所有实际客户文案在发送前统一由 Automation Persona 生成；Persona 失败时转 Human Review。
- Account 入口支持人工纠正完整路由元组，并通过 Route errors 视图分析误路由案例。
- Account 入口支持对每条工单的路由结果进行 pass/review 标记，默认只显示未 review 工单，可切换 reviewed 视图。
- Account 入口支持默认 All 的重叠 route filter，按 Automated、Backend Operation、Account & Billing、Tech、Security & Compliance、Conversation 和 Human Review 等细分类别分页查看，并显示同一快照的 case counts。
- Account 入口支持按 ticket # 精准打开 Case，并可对单 Case 执行仅保留客户消息、保留独立审计的完整 Rerun。
- Account Case 读取受 Workspace Admin 保护；n8n 可通过独立 Zendesk comment snapshot integration 将 Account Case 的 public/internal comments 幂等同步到独立 projection，并可用 trigger_comment_id 将新的客户公开评论触发进自动化处理（agent 评论与重放不触发），详情按不同标签和气泡展示，Rerun 不删除这些 Zendesk comments。
- n8n 可将 Zendesk 工单状态幂等同步到 Account Case：/account 与 /production 的列表和详情显示 Zendesk 状态，solved/closed 联动关闭本地工单并停止 AI 自动回复，重开后自动恢复。
- Account Rerun 先冻结目标 Case，再以无网络副作用的 Account-only preflight 校验数据库、Prompt runtime 和 Luna profile；首个 Case 的只读 Prepare 执行首次模型请求，任何错误立即停止并展示准确的失败阶段与未处理数量，支持从冻结 checkpoint Resume。
- Account 入口强制使用当前 layered route 并记录 pipeline 版本；Agora Router 将安全、隐私、信任、审计和合规请求归入 Security & Compliance classification-only 路由，Account & Billing 子 Router 将请求细分为 Account Suspension、Fraud Account、Detailed Invoice 或 Other，Backend Operation/Automation Router 将明确后台操作细分为 Enablement、Quota 或 Unregistered。每次新建异步全量 Rerun 都会重新执行路由、字段提取和 handler reconciliation，并允许 Automation 重新发送内部邮件，同时保留单个 job 内的幂等和审计历史。
- Account 入口通过 external ID 或来源 ticket ID 幂等处理重复请求，避免重复建单和重复发送内部邮件。
- Account Case 仅在命中已注册 Automation 时执行 handler 和延迟客户回复；其他路由只记录标签并进入对应人工或后续处理目标。
- Account 自动化遇到 AI/API、结构化输出、字段处理、Persona 或内部处理链路故障时最多重试 3 次且不使用 fallback；失败会停止客户回复、取消待处理 reply job、转为 human review，并向指定负责人发送脱敏的幂等故障告警。
- Enablement 使用 LLM 从客户原文提取并校验字段证据，不限制 App ID 格式；缺失时生成上下文追问，不确定或多候选时转 Human Review。
- Account Verification 使用 LLM 收集公司、联系人、使用场景和安全支付概况，最多追问一次并阻止敏感支付凭据进入派生数据。
- /production 独立环境提供与 /account 相同的 Account 处理能力（无 Run in Production），经独立数据库、独立 worker 和同域名路径路由运行；n8n 可将工单直接转发到 production，AI 回复自动以真实 Zendesk 公开评论发送，closing 类回复同次写入并置工单为 solved，确认后才关闭本地工单。
- /account 的 Run in Production 按钮将 Case 以 n8n 同款 intake 转发到 production 环境，由 production 侧完成完整路由与 Zendesk 公开评论投递；staging 库内晋级（PRD Case）逻辑已移除。
- /automation/staging、/automation/preproduction、/automation/production 提供三套独立 Route/Automation 执行环境与控制台 UI（staging 对齐 /account 模板、preproduction/production 对齐 /production 模板）：Execution token 门、执行历史列表（状态过滤+计数、Case 搜索、分页）、详情视图（meta、问答时间线、delivery ledger）、rerun 真实现（staging/preproduction）与 reset（staging 清空执行记录）；执行与查询 API 强制 Bearer token，Production 镜像与 UI 物理排除 rerun，旧 /account 与 /production 入口保留。
- Summary Agent 会在升级工程师工单前生成结构化上下文摘要包。

### 未完成
- 对话支持上传图片和 txt/log/md 文件。
- 对话支持流式输出。

## Engineer 端

### 已完成
- 升级工单会进入工程师任务池。
- 工程师可切换托管与接管模式。
- 证据不足时会转工程师处理。
- 调查中工单会按工程师 ticket 生命周期流转。
- 工程师审核草稿后会回传客户。
- 排查型问题会先向客户补齐必要信息，再自动创建工程师工单。
- Client 与 Engineer 共用富文本 composer，支持粗体、斜体、列表、代码块和安全 markdown 渲染。
- 对话支持上传 txt/log/err 日志附件。
- Client AI 只能检索官网文档，Engineer AI 优先检索非官网知识并可按需回查官网文档。
- Engineer AI 会在工程师关闭 case 后自动生成结构化学习反馈。
- Engineer AI 会把所有学习反馈写入 Case Memory Ledger，并默认关闭自动召回。
- `/workspace` 是正式 Engineer Case 处理入口，工程师登录后可查看个人 weekly schedule，并在点击 Ready to roll 后处理系统派发给自己的 case。
- `/workspace/admin` 可通过真实邮件邀请创建 Admin/Engineer 账号，一次性 setup link 将邀请邮箱锁定为不可修改的登录身份。
- `/workspace/admin` 可在独立 Schedule tab 以 30 分钟格持久化管理 Engineer weekly schedule，支持跨夜与 `24:00` 全天边界；Engineer Management 直接以 on/off-schedule 展示 dispatch availability。
- Engineer Case 使用 active 且 on-schedule 的 engineer 进行 round-robin 自动派单，派单后立即开始 3 小时 SLA。
- Engineer 离开 schedule、账号 inactive 或 3 小时 SLA 到期时，系统会把未完成 Engineer Case 自动派给下一个合格 engineer。
- Engineer Case 派单状态使用 pending、assigned、resolved，并通过版本保护、事务更新和审计避免重复派发。
- Client Ticket status 与 Engineer Case assignment status 在 API、Workspace 和 Admin 中独立展示与处理。
- Admin 可人工调整 Engineer Case 派单，所有调整会记录操作者、原因、前后 assignee、状态和版本。
- 旧 `/engineer` UI 已转为 legacy；`/api/engineer/*` 仍是 active backend contract，manual claim endpoint 已禁用。
- Summary Agent 会在升级工程师工单前生成结构化上下文摘要包。
- Engineer AI 会在调查前生成结构化 Plan Agent 计划。
- Engineer AI 会按 Plan Agent 计划执行 allowlisted subagents 并生成 evidence packet。
- Engineer AI 会根据执行结果生成 Review Agent 决策。
- Engineer multi-agent 默认关闭并与 9/1 Controlled Launch 主链路隔离。
- revise 不再自动跑 Plan/Execute/Review replan，也不再强制 max 2 retries，只保留可编辑/重新走 guardrail 的行为。
- Engineer AI 通过两段 approve 机制避免直接自动回复客户：第一次 approve 触发 deterministic guardrail 校验，第二次 final approve 才发送客户回复并关闭工单。final approve 后会写入 closure audit event（`engineer_case_closed_after_customer_reply`），并把处理结果记录为 Case Memory candidate；candidate 默认不可检索（`retrieval_enabled=False`）且不会自动晋升 active memory（`active_memory_status=inactive`）。
- Engineer AI 会在 final approve 后生成 replay eval dataset candidate，包含 summary packet、review decision、replan/revise 轨迹和 approved reply。
- Production Non automated Case 会创建一个 active Engineer Case；SupportPortal 直接发送到固定 Slack Channel 并持久化 thread binding，n8n 只校验并转发固定 Team/Channel/thread 内的 `@bot` 指导与按钮交互。首次有效指导会为 Case 随机固定一个已发布 Persona，AI 仅以该指导作为技术事实来源进行润色，再经 Guardrail 和 Final Approve 发布为 Zendesk public comment。客户新评论只更新 Case 上下文、使旧 Draft/审批失效并在原 thread 提示 `Cx has added a new comment`，不会自动调用 AI；下一次 `@bot` 才基于最新上下文生成 Draft。发布一轮后 Engineer Case、派单和 thread 继续保持活跃。
- Production Fraud Account 和 Account Suspension 最终 handoff 在 Zendesk 客户回复确认后通过 n8n 通知 Slack。
- Production Automation 分类完成后会将 Case 链接、客户问题和分类 path 邮件通知负责人。

### 未完成
- 对话支持上传图片和 txt/log/md 文件。
- 对话支持流式输出。

## Ticket Dashboard

### 已完成
- Dashboard 可查看全量工单列表。
- Dashboard 可查看工单详情与时间线。
- Dashboard 的 ticket detail 可查看按工单 family 聚合的 token 用量摘要。
- Dashboard 可跟踪实时事件流。
- Dashboard 的 ticket detail 可查看 client agent runtime 摘要与最近 agent events。
- Dashboard 的 ticket detail 可在单条 RAG 回复下展开检索计划、执行轮次和最终证据。
- Dashboard 的 ticket detail 可查看客户消息、路由、RAG、审核和最终结果组成的执行 Flow。
- `/workspace/admin` 可查看 Client Ticket、Engineer Case、SLA、派单/转派、Engineer schedule coverage、Automated Cases 和 guardrail 指标。
- `/workspace/admin` 将 Route Strategy 统一纳入 Agent Config，以 Agent-only 层级导航 Route Agent、Agora Router、Security & Compliance、Account & Billing Router、Backend Operation Router 与 Automation Router；Account Suspension、Fraud Account 和 Detailed Invoice 位于 Account & Billing Router 下，Security & Compliance 与 Detailed Invoice 作为 classification-only outcome 展示，Automation Workflow catalog 仅展示当前注册的执行/兜底流程。Account Prompt 支持 managed 版本管理，正式 skill 与 MCP 状态继续支持 Draft、Scheduled、Active、Diff、Restore 和历史版本管理，Scheduled Prompt 仅在下一次成功的每日部署后统一生效。
- 对话支持上传 txt/log/err 日志附件。
- Account 入口可通过 HTTP 或手动 UI 创建 Account Case，并记录 Automated 或非自动化路由。
- staging Account 入口的 AI 消息可由 Admin 选择写入关联 Zendesk ticket 的 internal comment；production Automated case 的 AI 回复自动以公开评论发给客户，人工改派工单后自动停止发言。
- production Automated case 在任何外部副作用前自动由配置的 AI Agent 接手 Zendesk 工单并持久化 ownership 状态，手动按钮已移除；ownership 失败 fail closed 转 Human Review。
- Account 入口支持人工纠正完整路由元组，并通过 Route errors 视图分析误路由案例。
- Account 入口支持对每条工单的路由结果进行 pass/review 标记，默认只显示未 review 工单，可切换 reviewed 视图。
- Account 入口支持默认 All 的重叠 route filter，按 Automated、Backend Operation、Account & Billing、Tech、Security & Compliance、Conversation 和 Human Review 等细分类别分页查看，并显示同一快照的 case counts。
- Account 入口支持按 ticket # 精准打开 Case，并可对单 Case 执行仅保留客户消息、保留独立审计的完整 Rerun。
- Account 入口强制使用当前 v8 分层分类并记录 pipeline 版本，支持以全新 Case 执行语义异步 Rerun 全部历史 Case；每个 Case 会保留客户消息和路由审计，删除旧 Account AI 回复、reply job、reply execution 与 Persona assignment 后再重建内部邮件与 Persona 回复。
- Account Case 仅在命中已注册 Automation 时执行 handler 和延迟客户回复；其他路由只记录标签并进入对应人工或后续处理目标。
- Fraud Account 自动化通过公司 Outlook reply 接收内部处理结果。
- Detailed Invoice 仅保留 Account & Billing 分类，不进入 Automation 执行；既有自动化实现保留供未来启用。
- Automation Behavior 只提取结构化字段和处理事实，所有实际客户文案在发送前统一由 Automation Persona 生成；Persona 失败时转 Human Review。
- Account Automation 提供 Sid Precise、Sid Bright、Sid Warm 三套独立 Persona presets，首次客户回复随机分配并固定精确版本，完整 Rerun 后重新选择。
- Account Verification 使用 LLM 收集公司、联系人、使用场景和安全支付概况，最多追问一次并阻止敏感支付凭据进入派生数据。

### 未完成
- 待补充。

## RAG Dashboard

### 已完成
- Dashboard 可同步本地 benchmark 数据集。
- Dashboard 可发起 benchmark 运行并查看会话。
- Dashboard 可按 benchmark run 和 session 查看诊断分布与对比结果。
- Dashboard 的 Overview 可查看 benchmark token 汇总与 provider/model 明细。
- Dashboard 可复盘 live 与 benchmark case。
- Dashboard 可查看 query-understanding、候选漏斗和 judge 分歧诊断。
- Dashboard 可评审样本并导出结果。

### 未完成
- 待补充。

## RAG

### 已完成
- 工程师可上传知识入库。
- 系统会做混合检索与重排召回。
- 查询扩展会用词典、LLM 和 PRF 优化技术检索。
- 系统会按上下文预算压缩证据再生成技术答案。
- 系统会按 provider/model 统计 RAG token，并支持 future-ready usage ledger。
- 系统会输出 benchmark 分层诊断与失败归因。
- 证据不足时会转工程师处理。
- 系统已具备本地 benchmark 评测链路。
- 系统会自动识别 RTC 或 Cloud Recording，并在不确定时向客户确认后加载对应的 support prompt。
- 排查型问题会先向客户补齐必要信息，再自动创建工程师工单。
- 客户工单处理支持 main agent 调度 route、RAG 和 review 子 agent。
- Dashboard 的 ticket detail 可在单条 RAG 回复下展开检索计划、执行轮次和最终证据。
- Client AI 只能检索官网文档，Engineer AI 优先检索非官网知识并可按需回查官网文档。
- 本地 lightweight 线上路径已支持 RAG+KG 辅助调用，KG 在 query expansion、rerank boost、结构化 fact 三个钩子作为可降级辅助信号，生产灰度仍由 flag 控制。

### 未完成
- RAG+KG 生产 shadow/灰度需要补齐真实 query 对照数据、telemetry 审计和一键回滚门禁。
