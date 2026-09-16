# AgentRelay 服务器侧配置 Prompt（p2-163 Phase 0）

> 用途：在运行 AgentRelay 服务器（server.stellarix.space）的管理 Agent/会话中执行以下指令，为
> SupportPortal ECS 集成完成服务身份与契约确认。结果（尤其 token）走私密渠道回传给用户本人，
> 不进聊天记录、不进任何仓库提交或日志。

```text
请为 SupportPortal ECS 的 AgentRelay 集成完成以下配置并回传结果：

1. 用仓库内的 scripts/create_agent_identity.sh 创建两个服务身份：
   - supportportal-preproduction（用于 Preproduction ECS worker）
   - supportportal-production（用于 Production ECS worker，暂不启用消费，仅预留）
   要求：enabled、protocol 支持 agent-collab-v0.6、不要求 WebSocket listener。
   每个身份的 (username, agent_id, token) 三元组以安全方式单独交回用户（例如写入服务器上
   仅用户可读的文件并告知路径），不要在回复正文中输出 token。

2. 以 v0.6 契约为准，逐项确认"无头 HTTP 服务作为 requester 消费结果"所需细节：
   a) POST /workers/{agent_id}/readiness/register 的 transport 字段取什么值表示纯 HTTP 恢复拉取
      （客户端默认 "websocket"）；是否接受非 WebSocket 的 staged offline listener。
   b) readiness 发布（POST /workers/{agent_id}/readiness）的 freshness 窗口与最小发布间隔，
      以及 readiness 过期后 recovery 拉取的行为（listener_recovery_not_allowed / stale_readiness_epoch
      的恢复路径）。
   c) GET /workers/{agent_id}/events 恢复拉取的单事件语义、分页参数与 events 数组结构（字段名）。
   d) POST /workers/{agent_id}/messages/{message_id}/ack 与 /delivery-fail 的必填字段与幂等键语义。
   e) POST /tasks 创建时 task_expires_at 的单位（epoch 秒）与最大允许值；done_criteria 长度限制。
   f) POST /tasks/{id}/complete 的 completed_against_message_id 语义（是否必须指向回传 Message）与
      POST /tasks/{id}/close 与 complete/fail 的区别（ECS 应该用哪个做"结果已持久化后的收尾"）。
   g) 这两个新身份的 max_inflight 默认值与 backlog 上限；如建议调整请给出建议值。

3. 确认作用域约束：这两个身份的 token 只能以各自 agent_id 身份创建/回复/关闭其名下 Task，
   不能消费或修改其他 agent 的 Task。

4. 输出一份最小 curl 示例集（含鉴权头）：create task → (模拟目标 reply) → readiness register/publish
   → recovery 拉取 → ack → complete，供 SupportPortal 侧客户端实现对照。
```

## 回传后的落地动作（SupportPortal 侧，由实施任务执行）

- token/username/agent_id/base_url 写入 SSM：`/supportportal/preproduction/agentrelay-base-url`、
  `agentrelay-agent-id`、`agentrelay-username`、`agentrelay-token`（SecureString）；production 同构。
- 若第 2 点任何一项与 `docs/operations/agentrelay-http-contract.md` 不一致，以服务器回传为准回写该文档。
