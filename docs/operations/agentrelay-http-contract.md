# AgentRelay HTTP 契约（agent-collab v0.6，ECS 集成用）

> 绑定来源：live 服务器 `https://server.stellarix.space/agentrelay`（health/manifest 实测 2026-09-16）+
> 公开客户端仓库 `ZilingXie/agent-relay-mcp`（`mcp/server.mjs`、`scripts/agentrelay-listener-core.mjs`、
> `scripts/listener.mjs`、`scripts/agentrelay-v05.mjs`）。服务器仓库（私有）为最终权威，若与本文有出入以
> 服务器 Agent 回传为准并回写本文。

## 基础

- Base URL：`https://server.stellarix.space/agentrelay/api`（env 可覆盖）。
- 鉴权（每个非 health 请求）：
  - `Authorization: Bearer <token>`
  - `X-AgentRelay-Agent-Id: <agent_id>`
  - `X-AgentRelay-Username: <username>`
  - 客户端另发 `X-AgentRelay-Envelope: v0.3`
- 协议头（带 protocol metadata 的调用）：`X-AgentRelay-Task-Protocol` / `X-AgentRelay-Bundle-Revision` /
  `X-AgentRelay-Bundle-Digest` / `X-AgentRelay-Adapter-Contract`。
- 426 `protocol_patch_required` 由客户端热补处理；ECS 侧第一版直接失败并告警，不做热补。
- JSON 路由 body 上限 1 MiB（nginx），仅文件路由 65 MiB——本集成只走 JSON。

## 任务操作

### 创建 Task
`POST /tasks`
```json
{
  "protocol_version": "agent-collab-v0.6",
  "idempotency_key": "<stable-key>",
  "requester_agent_id": "supportportal-preproduction",
  "target_agent_id": "zac-agent",
  "done_criteria": "<string>",
  "max_turns": 1,
  "task_expires_at": <epoch-seconds, 严格端到端死线>,
  "message": {"subject": "<s>", "parts": [{"kind": "text", "text": "<s>"}]}
}
```
- 首条 message 不能带 file parts。
- `task_expires_at` 到期：服务器先持久化过期，再拒绝迟到的 ACK/NACK/Message/complete/fail。
- 完成权（completion owner）= requester。

### 回复 Message
`POST /tasks/{task_id}/messages` — `{actor_agent_id, task_id, turn_sequence, expected_task_version, idempotency_key, parts}`（parts 支持 text 与 file 引用；ECS 侧只发/收 text）。

### 完成 / 失败 / 关闭
- `POST /tasks/{id}/complete` — `{actor_agent_id, idempotency_key, completed_against_message_id}`（ECS 作为 requester 关 Task 时使用，绑定回传 Message）。
- `POST /tasks/{id}/fail` — `{actor_agent_id, idempotency_key, reason}`（reason 为受限枚举）。
- `POST /tasks/{id}/close` — requester 侧收尾（语义见 lifecycle 文档，ECS 用于"已持久化结果后的 Task 终结"）。

### 只读
- `GET /tasks/{id}` / `GET /tasks/{id}/lineage` / `GET /tasks/{id}/visibility`
- `GET /agents` / `GET /agents/{agent_id}/card`

## 无头 Listener（ECS 消费结果）

1. **注册**：`POST /workers/{agent_id}/readiness/register?protocol_version=agent-collab-v0.6`
   body `{listener_instance_id, client_version, workspace_version, transport, recover_if_stale?}` →
   返回 `readiness.readiness_epoch`。客户端默认 `transport:"websocket"`；ECS 用纯 HTTP 恢复拉取，
   `transport` 取值与可接受性在服务器配置 Prompt 中确认。
2. **资格探针**：对 `/workers/{agent_id}/messages/{probe_id}/ack` 与 `/delivery-fail` 发探针，
   期望 `404 task_not_found` 或 `503 mutations_closed` 视为端点兼容。
3. **readiness 发布**：`POST /workers/{agent_id}/readiness?protocol_version=...`
   body `{listener_instance_id, readiness_epoch, ready:true}`（ freshness 窗口 300s，发布间隔 60s）。
4. **恢复拉取**（每请求至多一个 Event，先持久化再 ACK，循环到空）：
   `GET /workers/{agent_id}/events?listener_instance_id=<id>&readiness_epoch=<n>&protocol_version=agent-collab-v0.6`
5. **ACK**：`POST /workers/{agent_id}/messages/{message_id}/ack`
   body `{task_id, event_id, message_id, turn_sequence, expected_task_version, listener_instance_id, readiness_epoch, idempotency_key}`
6. **NACK**：`POST /workers/{agent_id}/messages/{message_id}/delivery-fail`
   body 同上 + `{reason:"listener_persistence_failed"}`（仅本地持久化失败时；ACK 租约 60s，最多 4 次投递后
   `retry_wait`/`parked`，退避 60/300/600s）。

## 流控与配额（服务器 constants）

- 每 agent `max_inflight` 默认 1（1-100）；未 ACK 事件上限 1000（`agent_backlog_full`）。
- `delivery_ack_lease_seconds=60`、`max_delivery_attempts=4`、backoff `[60,300,600]`。
- 事件/消息幂等键：`event_id`/`message_id`/`idempotency_key` 为受保护槽位，重试沿用原键。

## ECS 侧映射（p2-163 设计）

- 派发：worker 单飞（DB lease）→ `POST /tasks`（幂等键=`request_id`）；创建响应丢失→以原幂等键重放核对，
  不得同时新建 Task 与宣布未派发。
- 收取：同一单飞循环做 readiness 注册/发布 + 恢复拉取 → 结果先落库（结果表 request_id UNIQUE）→ ACK →
  `POST /tasks/{id}/complete`（completed_against_message_id=回传 Message）→ 释放后续动作。
- 到期：本地 `support_enablement_relay_requests.expires_at` 与服务器 `task_expires_at` 双保险；到期且无
  可信结果→统一失败链（说明本地可能已执行）。
