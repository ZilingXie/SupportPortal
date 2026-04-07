# 工单数据库设计（client ticket / engineer case 拆分后）

## 目标
- 将客户侧 `ticket` 与工程师侧 `case` 拆成两个一等持久化实体。
- client UI 继续只消费 client ticket；engineer UI 改为消费 first-class engineer case。
- 保留 parent-child 关联、编号递增规则和历史调查可回放能力。

## 表设计

### 1. `support_tickets`
- 一行一个 `client ticket` 当前快照。
- 核心字段：
  - `ticket_id`（PK，例：`TK-040`）
  - `customer_id`
  - `requester`
  - `subject`
  - `status`（`open|communicating|escalated|investigating|resolved`）
  - `last_engineer_action`（JSONB）
  - `active_engineer_case_id`（当前 active engineer case，例：`TK-040-1`，可空）
  - `engineer_case_count`（该 client ticket 生命周期内已创建过的 engineer case 数量）
  - `created_at` / `updated_at`
- 说明：
  - `support_tickets` 不再保存 engineer-only 的 `handoff` 或 `agent state`。
  - client 侧所有主身份仍然是 `ticket_id + subject`。

### 2. `support_ticket_messages`
- 多行对应一张 client ticket 的公开对话消息。
- 字段：
  - `id`（BIGSERIAL PK）
  - `ticket_id`（FK -> `support_tickets.ticket_id`）
  - `role`（`customer|assistant|engineer|system`）
  - `content`
  - `created_at`
  - `sources`（JSONB，可空）
  - `citations`（JSONB，可空）
  - `sentiment_label`（`good|bad|neutral`，可空，仅客户消息使用）
  - `meta`（JSONB，默认 `{}`，保存 assistant route/runtime、client intake 等非固定消息字段）
- 说明：
  - `support_ticket_messages` 的固定列只保存公开消息正文与通用展示字段。
  - 其余消息级结构化字段统一写入 `meta`，例如 `answer_route`、`route_reason`、`route_confidence`、`search_used`、`scope_label`、`workflow_action`、`client_agent_run_id`、`client_agent_runtime_status`、`client_intake_phase`、`client_intake_ready_for_engineer_ticket`、`client_intake_missing_information`。
  - 读取时 `meta` 会平铺回消息顶层，保持 Postgres 存储与内存模式返回契约一致。

### 3. `support_ticket_events`
- 记录 client ticket 级别的业务事件。
- 字段：
  - `id`（BIGSERIAL PK）
  - `ticket_id`（FK，可空，始终是 client ticket id）
  - `event_type`
  - `payload`（JSONB）
  - `created_at`
- 当事件同时关联 engineer case 时，payload 额外带：
  - `client_ticket_id`
  - `engineer_case_id`

### 4. `support_engineer_cases`
- 一行一个 `engineer case` 当前快照。
- 核心字段：
  - `engineer_case_id`（PK，例：`TK-040-1`）
  - `client_ticket_id`（FK -> `support_tickets.ticket_id`）
  - `case_sequence`（1, 2, 3...）
  - `title`（创建时冻结的 unresolved issue title，例：`black screen issue`）
  - `status`（`communicating|escalated|investigating|resolved`）
  - `trigger_source`
  - `trigger_reason`
  - `draft_customer_reply`
  - `final_confirmation_requested_at`
  - `engineer_handoff_packet`（JSONB）
  - `engineer_agent_state`（JSONB）
  - `opened_at` / `updated_at` / `closed_at`

#### `engineer_handoff_packet`
- 作为 engineer case 顶层后台字段保存，不直接原样展示给工程师。
- 保存 client AI 升级到 engineer AI 时的结构化交接上下文。
- 固定字段：
  - `source`
  - `conversation_summary`
  - `latest_customer_message`
  - `latest_client_ai_reply`
  - `route_summary`
  - `rag_result`
  - `unresolved_reason`
  - `customer_language_hint`
  - `created_at`
  - `updated_at`
- `rag_result` 固定包含：
  - `candidate_answer`
  - `sources`
  - `citations`
  - `evidence_summary`

#### `engineer_agent_state`
- 作为 engineer case 顶层后台字段保存，不直接把原始 JSON 渲染到 engineer UI。
- 保存 engineer AI 最近一次持久工作状态。
- 固定字段：
  - `phase`
  - `issue_understanding`
  - `knowledge_summary`
  - `why_not_solved`
  - `goal`
  - `known_facts`
  - `missing_information`
  - `next_request_for_engineer`
  - `resolution_hypothesis`
  - `ready_to_reply`
  - `last_refreshed_at`
- case 关闭后不会立即清空，保留为最近一次 case cycle 的快照；下次新 case 创建时重新生成。

### 5. `support_engineer_case_messages`
- 对应 engineer case 的内部调查线程。
- 字段：
  - `id`（BIGSERIAL PK）
  - `message_id`（业务层稳定 message id）
  - `engineer_case_id`（FK -> `support_engineer_cases.engineer_case_id`）
  - `role`（`engineer_ai|engineer|system`）
  - `content`
  - `created_at`
  - `meta`（JSONB，可空）

### 6. `support_engineer_case_events`
- 对应 engineer case 的工程师侧事件流。
- 字段：
  - `id`（BIGSERIAL PK）
  - `engineer_case_id`（FK -> `support_engineer_cases.engineer_case_id`）
  - `event_type`
  - `payload`（JSONB）
  - `created_at`

## 编号与标题规则
- 第一次进入 engineer-visible 生命周期时：
  - 为 client ticket `TK-040` 创建 `TK-040-1`
- active engineer case 未关闭时：
  - 后续客户补充消息继续刷新同一个 engineer case
- 已关闭后再次进入 engineer handling：
  - 创建下一个 suffix，如 `TK-040-2`
- engineer case title 是创建时快照：
  - 优先来自客户问题描述、handoff summary、engineer AI 初始理解
  - 不默认复用 parent client ticket subject
  - 当前版本不支持自动改名

## 索引
- `support_tickets(status, updated_at desc)`
- `support_ticket_messages(ticket_id, created_at asc, id asc)`
- `support_ticket_events(ticket_id, created_at desc)`
- `support_engineer_cases(client_ticket_id, updated_at desc)`
- `support_engineer_cases(status, updated_at desc)`
- `support_engineer_case_messages(engineer_case_id, created_at asc, id asc)`
- `support_engineer_case_events(engineer_case_id, created_at desc)`

## Dashboard 情绪信号
- 仪表盘不再基于工单优先级统计。
- `sentiment_alert_count` 与 `sentiment_breakdown` 都基于每张 client ticket 最新一条客户消息的 `sentiment_label`。
- 未打标签的工单归入 `Unclassified`。

## 读写策略
- `POST /api/tickets/query`
  - 读取和更新 client ticket；
  - 写入公开 client 消息；
  - 如需 engineer 介入，则创建或刷新 linked engineer case。
- 自动升级到 `investigating` 时：
  - client ticket 进入 `investigating`
  - 创建或刷新 `support_engineer_cases`
  - route decision、candidate answer、sources、citations、evidence summary 写入 `engineer_handoff_packet`
  - engineer AI 的问题理解、目标、缺失信息、下一步请求写入 `engineer_agent_state`
- investigation 期间客户继续补充消息时：
  - 继续写入 parent client ticket 的公开消息
  - 刷新 active engineer case 的 `conversation_summary` / `latest_customer_message`
  - 保留原始 `rag_result`
  - 重跑 engineer AI 并更新 `engineer_agent_state`
- 手动 `request engineer assistance`
  - 创建或刷新一个 `status = escalated` 的 engineer case
- 工程师 investigation / confirmation 接口
  - 写 `support_engineer_case_messages`
  - 更新 `support_engineer_cases`
  - 将客户可见回复回写到 `support_ticket_messages`
  - 同时写 client ticket event 和 engineer case event

## 配置
- `TICKET_DB_DSN`：工单库连接串
- `TICKET_DB_SCHEMA`：默认 `supportportal`
- `TICKET_DB_CONNECT_TIMEOUT`：默认 `5` 秒

如果未配置 `TICKET_DB_DSN`，系统会回退到内存存储模式（仅用于本地调试）。

## 建表与迁移方式
- 后端启动时自动建表（idempotent）。
- 对已有库采用 additive migration：
  - `support_tickets` 增加 `active_engineer_case_id` / `engineer_case_count`
  - 新增 `support_engineer_cases`
  - 新增 `support_engineer_case_messages`
  - 新增 `support_engineer_case_events`
- legacy `active_investigation` / `investigation_history` 会在初始化时 best-effort backfill 成 engineer cases，不做 destructive reset。
- SQL 参考：[backend/sql/ticket_storage.sql](/Users/xieziling/Desktop/personal_proj/SupportPortal/backend/sql/ticket_storage.sql)
