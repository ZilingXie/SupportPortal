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
- Billing 白名单问题会自动收集字段并升级内部团队处理。
- Billing 自动化统一通过公司 Outlook reply 接收内部处理结果，并可将 PDF 附件转发到客户工单。
- Account 入口可通过 HTTP 或手动 UI 创建客户工单并记录 Billing 自动化或人工审核路由。
- Account 入口可查看 Billing ticket 历史和详情。
- Account 入口支持人工纠正完整路由元组，并通过 Route errors 视图分析误路由案例。
- Account 入口支持对每条工单的路由结果进行 pass/review 标记，默认只显示未 review 工单，可切换 reviewed 视图。
- Account 入口会对 Not automated 工单按可配置比例创建 Engineer Case，当前支持每第 10 单试运行并可切换到 100%。
- Account 入口通过 external ID 或来源 ticket ID 幂等处理重复请求，避免重复建单和重复发送内部邮件。
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
- `/workspace` 是正式 Engineer Case 处理入口，工程师通过账号登录后仅能查看和处理系统派发给自己的 case。
- `/workspace/admin` 可通过真实邮件邀请创建 Admin/Engineer 账号，并由一次性 setup link 完成账号设置。
- `/workspace/admin` 可持久化管理 Engineer weekly schedule 和 available/unavailable 状态。
- Engineer Case 使用 on-schedule 且 available 的 engineer 进行 round-robin 自动派单，派单后立即开始 3 小时 SLA。
- Engineer unavailable、离开 schedule 或 3 小时 SLA 到期时，系统会把未完成 Engineer Case 自动派给下一个合格 engineer。
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
- `/workspace/admin` 可查看 Client Ticket、Engineer Case、SLA、派单/转派、Engineer availability、Billing automation 和 guardrail 指标。
- 对话支持上传 txt/log/err 日志附件。
- Account 入口可通过 HTTP 或手动 UI 创建客户工单并记录 Billing 自动化或人工审核路由。
- Account 入口支持人工纠正完整路由元组，并通过 Route errors 视图分析误路由案例。
- Account 入口支持对每条工单的路由结果进行 pass/review 标记，默认只显示未 review 工单，可切换 reviewed 视图。
- Billing 自动化统一通过公司 Outlook reply 接收内部处理结果，并可将 PDF 附件转发到客户工单。

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
