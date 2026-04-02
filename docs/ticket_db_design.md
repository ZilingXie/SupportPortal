# 工单数据库设计（本地 POC）

## 目标
- 使用 PostgreSQL 持久化工单，替代内存 `dict`。
- 保持现有 API 不变，前端无需改动。
- 支持审计追踪：消息历史 + 事件历史可回放。

## 表设计

### 1. `support_tickets`
- 一行一个工单（当前状态快照）。
- 核心字段：
  - `ticket_id`（PK）
  - `customer_id`
  - `requester`
  - `subject`
  - `status`（`open|communicating|escalated|investigating|resolved`）
  - `last_engineer_action`（JSONB）
  - `engineer_handoff_packet`（JSONB，client AI 升级给 engineer AI 的结构化交接包）
  - `engineer_agent_state`（JSONB，engineer AI 最近一次持久工作状态快照）
  - `created_at` / `updated_at`

#### `engineer_handoff_packet`
- 作为 ticket 顶层后台字段保存，不直接展示给工程师。
- 用于记录自动进入 `investigating` 时 client AI 已经整理好的交接上下文，供后续 engineer dashboard 使用。
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
- 作为 ticket 顶层后台字段保存，不直接把原始 JSON 渲染到 engineer UI。
- 记录 engineer AI 最近一次对问题理解、目标、缺失信息和下一步请求的持久工作状态。
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
- investigation 关闭后这两个 ticket-level 字段不会立即清空，会保留为最近一次 engineer-side cycle 的快照；下一次自动 investigation 开始时覆盖。

### 2. `support_ticket_messages`
- 多行对应一张工单的对话消息。
- 字段：
  - `id`（BIGSERIAL PK）
  - `ticket_id`（FK -> `support_tickets.ticket_id`）
  - `role`（`customer|assistant|engineer|system`）
  - `content`
  - `created_at`
  - `sources`（JSONB，可空）
  - `citations`（JSONB，可空）
  - `sentiment_label`（`good|bad|neutral`，可空，仅客户消息使用）

### 3. `support_ticket_events`
- 记录业务事件（工单创建、升级、调查、确认等）。
- 字段：
  - `id`（BIGSERIAL PK）
  - `ticket_id`（FK，可空）
  - `event_type`
  - `payload`（JSONB）
  - `created_at`

## 索引
- `support_tickets(status, updated_at desc)`
- `support_ticket_messages(ticket_id, created_at asc, id asc)`
- `support_ticket_events(ticket_id, created_at desc)`

## Dashboard 情绪信号
- 仪表盘不再基于工单优先级统计。
- `sentiment_alert_count` 与 `sentiment_breakdown` 都基于每张工单最新一条客户消息的 `sentiment_label`。
- 未打标签的工单归入 `Unclassified`。

## 读写策略
- `POST /api/tickets/query`
  - 读取工单快照 + 现有消息；
  - 仅插入本次新增消息；
  - 更新工单快照；
  - 写入事件记录。
- 自动升级到 `investigating` 时：
  - 创建或刷新 `active_investigation`；
  - 将当次 route decision、candidate answer、sources、citations、evidence summary 写入 `engineer_handoff_packet`；
  - 将 engineer AI 的问题理解、目标、缺失信息、下一步请求写入 `engineer_agent_state`。
- investigation 期间客户继续补充消息时：
  - 刷新 `engineer_handoff_packet.conversation_summary` 与 `latest_customer_message`；
  - 保留原始 `rag_result`；
  - 重跑 engineer AI，并更新 `engineer_agent_state`。
- 工程师动作接口（action / investigation）
  - 更新 `support_tickets`；
  - 如有新增回复，写入 `support_ticket_messages`；
  - 写入 `support_ticket_events`。
  - 事件 payload 只带轻量 agent 摘要字段，不带完整 handoff packet。

## 配置
- `TICKET_DB_DSN`：工单库连接串（推荐单独配置）
- `TICKET_DB_SCHEMA`：默认 `public`
- `TICKET_DB_CONNECT_TIMEOUT`：默认 `5` 秒

如果未配置 `TICKET_DB_DSN`，系统会回退到内存存储模式（仅用于本地调试）。

## 建表方式
- 后端启动时自动建表（idempotent）。
- 对已有库采用 additive migration，通过 `ADD COLUMN IF NOT EXISTS` 补齐 ticket-level agent 字段，不做 destructive reset。
- SQL 参考：[backend/sql/ticket_storage.sql](/Users/xieziling/Desktop/personal_proj/SupportPortal/backend/sql/ticket_storage.sql)。
