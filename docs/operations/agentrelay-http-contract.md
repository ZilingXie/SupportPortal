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

## 服务器实测勘误与补充（2026-09-16，v0.6 服务器端代码+真实 token 闭环验证）

> 身份已就绪：`supportportal-preproduction` / `supportportal-production`（enabled、仅 v0.6、
> service_agent）。token 三元组只落服务器两处 0600 文件：
> `/home/ubuntu/projects/agentrelay/agentRelay/data/local-env/supportportal-*.env` 与
> `data/agentrelay-auth.json`（首批 token 曾在排错中意外进入一次会话输出，已轮换并 shred 临时文件，
> 现行 env 文件内为轮换后新 token）。入库走 `server.store_v06.V06Store.upsert_agent`
> （`create_agent_identity.sh` 的 legacy store 写不进 v06 活跃库，需 `--no-agent` + v06 upsert）。

以下以服务器端实现与两身份间完整闭环（create→register(http)→recovery→ack→reply→complete `goal_met`）为准，修正/细化上文：

1. **mutation 的 `message_id` 填当前消息 id**（乐观锁语义），不是新消息 id——新消息 id 由服务端生成；
   `expected_task_version` / `turn_sequence` 每次操作前从 `GET /tasks/{id}` 重取（**ack 会推进
   task_version**）。实测错填会 409 `stale_message` / `stale_task_version`。
2. **ack 分两种形态**：
   - 消息事件：`POST /workers/{id}/messages/{message_id}/ack`，字段
     `task_id, event_id, message_id, turn_sequence, expected_task_version, idempotency_key,
     listener_instance_id, readiness_epoch` 全必填、拒绝未知字段；恰好一次（event→acked、
     message→delivered）。
   - **通知类事件**（delivery_changed 等，无 message 绑定）：`POST /workers/{id}/events/{event_id}/ack`，
     body 仅 `{idempotency_key, listener_instance_id, readiness_epoch}`。
   - NACK：`POST .../messages/{id}/delivery-fail`，同消息 ack 字段 + `reason`，**reason 仅允许
     `listener_persistence_failed`**（效果=park）。
3. **v0.6 没有 `/close`**（实测 410 "mutations are retired"）。终态只有 `/complete`（**仅 requester**，
   且要求 target 回复在本侧已 delivered+事件已 ack，`completed_against_message_id`=该回复=当前消息）与
   `/fail`（reason 枚举 `agent_reported_failure`（仅 target）/`max_turns_exhausted`（仅 requester 且轮次
   用满）/两个仅 relay 内部）。**没有主动取消**：放弃任务只能等 TTL 到期转 `expired`。
4. `transport` 服务端只要求非空字符串、原样存储（合规客户端用 `"websocket"`）；ECS 无头消费
   `"http"` 实测 201 合法。readiness 窗口 300s、建议 60s publish；listener 新鲜就绪时事件出生
   `queued`（可推送），否则出生即 `parked`（仅恢复拉取可取）；register 带 `recover_if_stale=true`
   在旧 readiness 300s 内会 409，不带则无条件接管（epoch+1）。
5. 恢复拉取：**每请求至多 1 个事件**，fence=query 两参数；返回事件 `inflight_via=recovery`、
   60s ACK 租约；未 ack 重拉幂等重发同一事件；租约过期回 `parked` 并记 `ack_lease_expired`。
   **事件对象不含消息正文**，正文从 `GET /tasks/{task_id}` 取。
6. `task_expires_at`：epoch 秒，必须大于当前时间，缺省 now+24h，**无上限校验**；硬截止（到期原子转
   `expired`）。
7. reply：仅当前 `to_agent_id` 可发下一条（严格轮流）。作用域实测：伪造 requester 403、target 调
   complete 409 "only requester may complete"、错配 Agent-Id 头 403。
8. `max_inflight` 默认 1（可经 `scripts/set_agent_delivery_limit.py` 调 1-100）；backlog 准入上限
   1000 条未决事件/agent（`queued/inflight/retry_wait/parked` 计入，acked/exhausted 不占），全服
   常量不可按 agent 配置。
