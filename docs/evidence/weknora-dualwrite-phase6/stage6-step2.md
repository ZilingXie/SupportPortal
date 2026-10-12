# 阶段 6 证据 — 步 2 配置与渲染（p2-197）

- 计划名称：WeKnora 并行双写与人工治理链（阶段二），修订 r2-stage6
- 环境：Preproduction；Production 未触碰
- 记录日期：2026-10-11
- 代码提交：`dd7ca37f8ef2a5f8a56b6376bc96854aadc5047c`（allowlist + task-definition renderer/test）及 `a7e218b79961c4c27efcec09108e704e05772020`（阶段 6 开关/allowlist 测试适配与 AgentMemory 读面回归）

## SSM 变更

只记录参数元数据，不记录任何 secret value：

| 参数 | 类型 | 版本 | 用途 |
|---|---|---:|---|
| `/supportportal/preproduction/agent-memory-wiki-base-url` | String | 1 | AgentMemory Wiki 公共 API base（`https://supportcenter.stellarix.space/dashboard/memory`） |
| `/supportportal/preproduction/agent-memory-wiki-service-id` | String | 1 | `default` |
| `/supportportal/preproduction/agent-memory-wiki-team-id` | String | 1 | Hermes 当前任务的 `team-7oif6fsv17` |
| `/supportportal/preproduction/knowledge-slack-workspace-domain` | String | 1 | `agoraiolab`，用于只读 Slack thread URL |
| `/supportportal/preproduction/hermes-wiki-core-user-key` | SecureString | 1 | 已存在；`AGENT_MEMORY_WIKI_USER_KEY` 复用此引用，未复制密钥 |

参数拥有者和区域由 `supportportal` Preproduction AWS 账户 `891612554546` / `us-east-1` 回读确认。

## Renderer 合同

以线上 API/Worker task definition 135 为输入做离线渲染预览：

- API 与 Worker 都显式写入 `HERMES_KNOWLEDGE_WORKFLOW_ENABLED=0`；
- 六个独立 `KNOWLEDGE_*_ENABLED` 开关均为 `0`；
- `KNOWLEDGE_SOURCE_ALLOWLIST` 为空（default-strict）；
- `KNOWLEDGE_SLACK_REVIEW_LEASE_SECONDS=300`；
- 五个 `AGENT_MEMORY_WIKI_*`/`KNOWLEDGE_SLACK_WORKSPACE_DOMAIN` 引用 SSM；
- Worker 保持 `WEKNORA_PROMOTION_ENABLED=0`；
- 当前尚未 register task definition、update ECS service 或启动新任务。

## 验证

- renderer/allowlist/AgentMemory 定向套件：114 passed / 0 failed（首版 renderer 合同）。
- 阶段 6 当前 21 套件，使用一次性本机 PostgreSQL 14.19（`RUN_POSTGRES_INTEGRATION=1`，仅本机 Unix socket，未写入外部业务系统）：540 passed / 0 failed，8 subtests；包含 `test_hermes_knowledge_workflow_postgres.py` 的 case-binding allowlist 适配。
- `git diff --check`：通过。
- SSM `describe-parameters`：四个新参数均存在、String、Version 1；既有用户密钥仅回读 SecureString 元数据。
