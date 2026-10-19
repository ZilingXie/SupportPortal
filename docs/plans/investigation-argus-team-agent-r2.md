# Investigation 首条时间窗口与 Team Agent 咨询修复（R2）

基线：`main@5bfbd86a6564afc9f5d1c7aab0f4dfb2ad5184d1`。本轮只修复首条
`ticket.created` 的相对日期识别和 API 侧 Team Agent 配置，不重放 `#14122`，不发送真实客户、Slack、Zendesk、Argus 或 Zac 消息，不发布 Production。

## 合同

| ID | 合同 | 验证 |
| --- | --- | --- |
| R2-C1 | 首条正文仅在无客户 comment、无 trigger_comment_id 的 `ticket.created` fallback；当前 comment 优先，代理/历史消息不触发 | policy 集成场景，中英文 today/今天 |
| R2-C2 | Team Agent 使用现有 Hermes endpoint，`/v1` 只出现一次；缺配置、非 HTTPS、query/fragment/凭据 fail closed；SSE 语义与 Zac wire/storage alias 保持兼容 | client/settings 回归 |
| R2-C3 | Team Agent secret/env 只进入 API；Route/Worker 不注入，模型固定 `team-agent` | ECS renderer 回归 |
| R2-C4 | route、fixed task、Investigation 审核、draft、Slack、close 语义不变 | 既有 Prompt/Agent 回归 |

## 实施与证据

- `investigation_runtime_policy.py` 增加首条 description fallback，并记录 `window_source`。
- `zac_consultation.py` 增加 canonical `TeamAgentSettings/TeamAgentClient`，保留 `ZacSettings/ZacClient` alias；规范化 endpoint 和 fail-closed 校验。
- `automation_ecs_deploy.py` 将 Team Agent secret 映射到现有 Hermes SSM 参数，仅 API 注入 `TEAM_AGENT_MODEL=team-agent`。
- Prompt 用户可见咨询名称改为 Team Agent，保留现有 `support_zac_consult` wire alias 和真实 skill 名。
- 测试证据：Investigation/policy + deploy 定向回归、Prompt/Agent 回归、Python compileall；PG 专项依环境可用性记录。

真实业务样本保持 `waiting-for-evidence`。
