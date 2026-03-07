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
  - `status`（`open|waiting_for_engineer|resolved`）
  - `priority`（`urgent|high|normal|low`）
  - `engineer_mode`（`managed|takeover`）
  - `pending_engineer_question`
  - `last_engineer_action`（JSONB）
  - `created_at` / `updated_at`

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

### 3. `support_ticket_events`
- 记录业务事件（工单创建、升级、模式切换等）。
- 字段：
  - `id`（BIGSERIAL PK）
  - `ticket_id`（FK，可空）
  - `event_type`
  - `payload`（JSONB）
  - `created_at`

## 索引
- `support_tickets(status, updated_at desc)`
- `support_tickets(priority, updated_at desc)`
- `support_ticket_messages(ticket_id, created_at asc, id asc)`
- `support_ticket_events(ticket_id, created_at desc)`

## 读写策略
- `POST /api/tickets/query`
  - 读取工单快照 + 现有消息；
  - 仅插入本次新增消息；
  - 更新工单快照；
  - 写入事件记录。
- 工程师动作接口（action/mode/managed-response）
  - 更新 `support_tickets`；
  - 如有新增回复，写入 `support_ticket_messages`；
  - 写入 `support_ticket_events`。

## 配置
- `TICKET_DB_DSN`：工单库连接串（推荐单独配置）
- `TICKET_DB_SCHEMA`：默认 `public`
- `TICKET_DB_CONNECT_TIMEOUT`：默认 `5` 秒

如果未配置 `TICKET_DB_DSN`，系统会回退到内存存储模式（仅用于本地调试）。

## 建表方式
- 后端启动时自动建表（idempotent）。
- SQL 参考：[backend/sql/ticket_storage.sql](/Users/xieziling/Desktop/personal_proj/SupportPortal/backend/sql/ticket_storage.sql)。
