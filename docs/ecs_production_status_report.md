# ECS Production 当前状态与迁移盘点

日期：2026-09-02（增量更新：Fraud Suhrid handoff 配置修复与 AC-13212 验收）  
范围：SupportPortal `/automation/production`、Hermes 调查 agent、最近两天已合并的 Account/Enablement/Fraud 变更。  
当前结论：ECS 基础运行环境已经可用，但在 Archer 新 release 和三类业务 Case 全部验收前，不能下架 EC2 `/production`。

## 1. 当前 ECS 拓扑

| 组件 | 当前状态 | 作用 |
| --- | --- | --- |
| ECS cluster `supportportal-production` | ACTIVE | 承载生产自动化和 Hermes 服务 |
| `supportportal-production-api:14` | `1/1/0`, deployment `COMPLETED` | Intake 鉴权、校验、持久化、查询和 health endpoint |
| `supportportal-production-route:15` | `1/1/0`, deployment `COMPLETED` | Route Worker，负责分类和 durable Processing Job 交接 |
| `supportportal-production-worker:16` | `1/1/0`, deployment `COMPLETED` | Account/Fraud/Enablement/Quota/Suspension 自动化、回复和内部邮件轮询 |
| `supportportal-production-hermes:2` | `1/1/0`, deployment `COMPLETED` | Hermes agent + `memory-core` 双容器服务；公网入口 `/v1` |
| ALB `supportportal-production-alb` | internet-facing，HTTP 80 / HTTPS 443，active | `supportcenter.stellarix.space` 的公网入口；自动化 target 使用 `/automation/production/health/live` |
| ECR `supportportal/production` | immutable、scan-on-push、AES256 | API/Route/Worker 的环境 release 镜像 |
| ECR `supportportal/hermes` | immutable、scan-on-push、AES256 | Hermes、腾讯 AgentMemory memory-core 镜像 |
| RDS `n8n-postgres-db` | available，PostgreSQL 17.9，`db.t4g.micro`，20 GB | ECS coordination、Account case、Execution/Job/Delivery、Prompt 和 heartbeat 数据；生产 schema 为 `supportportal_production` |
| Valkey `supportportal-production-redis-001` | available，Valkey 9.1.0，`cache.t4g.micro`，1 node | 现有生产自动化的缓存/协调依赖；ECS durable Job 主链不依赖 Redis |
| 加密 EFS `fs-0ded23be6872d82da` | available，bursting | `graph-token-cache`、`hermes-home`、`tdai-data`、`pilot-creds` 四个 Access Point；其中 `tdai-data` 给腾讯 AgentMemory memory-core 使用，`pilot-creds` 给 Archer Pilot 使用 |
| CloudWatch `/ecs/supportportal/production` | retention 7 天 | API、Route、Worker、Hermes 日志 |

Hermes 的“腾讯 DB”不是 AWS 中单独新增的 RDS 实例，而是 `memory-core` 容器使用腾讯 AgentMemory/TencentDB 集成；AWS 侧负责 ECS 网络、ECR 镜像和 EFS 持久化。已完成真实 LLM turn、记忆写入和检索闭环验证。

## 2. 最近两天完成的变更

### 2.1 Hermes 与腾讯 AgentMemory（p2-133，已完成）

- 将 Hermes agent 和 Tencent AgentMemory `memory-core` 从本地 Podman 栈迁移为 ECS Fargate 独立 Service。
- 两个容器在同一 task 内通过 `awsvpc`/localhost 协作，`hermes` 等待 `memory-core` 健康后启动。
- 使用独立 ECR `supportportal/hermes`，镜像按 digest 引用。
- 通过 `/v1/models`、`/v1/responses` 完成认证和真实 LLM turn 验证；记忆可写入并由搜索读回。
- EC2 `/production` 的 Engineer Investigation reply 已接到 Hermes 公网端点；EC2 backup 本身仍保留。

### 2.2 Enablement 改为 Archer 自动流程（PR #1021，代码已合并）

- Media Relay Enablement 从内部邮件主路径改为 ECS Worker 内的 Archer executor。
- Archer Skill 和 checksum 固定的 amd64 Pilot 只进入 Worker 镜像；API/Route 镜像不包含 Pilot 或 Skill。
- 明确支持三种结果：`enabled`、`appid_invalid`、`project_not_found`；失败路径继续 fail closed 并进入人工处理。
- 新增对应的 reply intent、Persona 合同、幂等 job 和回归测试。
- PR #1021 已 squash 合并，当前 `main` 为 `ccb7ebc`。
- 重要版本边界：当前运行中的 ECS release 是 `r20260901-69e9836`（commit `69e9836`），早于 #1021，因此 Archer 代码尚未部署到 ECS Worker。p2-134 仍为 active。

### 2.3 Enablement 相关缺陷修复

近期已修复并进入主线的 Enablement 问题包括：

- submission contract 字段/确认语义不一致导致 `automation_persona` 失败；
- 内部执行人回复“App ID 不正确”时只记账、不排客户 follow-up job，导致客户收不到后续回复；
- completion/Media Relay canonical 名称归一和敏感字段投影边界；
- Archer 接入后保留非法 App ID、查无项目、执行失败的人工处理和可继续流程。

代码回归已通过，但真正的 Archer 生产验收必须在新 Worker release、Pilot deposit 和只读 GET probe 完成后进行。

### 2.4 Fraud/Account 回复链修复

- 修复 ECS Route 在 partial Fraud reply 时绕过 active handler、误进入 RAG fallback 的问题；已有字段只补充部分字段时继续走旧 `/production` 的 handoff 语义。
- 修复共享 Fraud builder 使用错误 `intent_router` 场景的问题，统一使用 `ACCOUNT_EXTRACTOR`。
- `uncertain`/`sensitive` extraction failure 现在按旧合同 reconciliation 到 Human Review，取消 pending reply jobs，不再误调用 RAG。
- 修复 `+86` 电话号码被 Luhn 规则误判为银行卡号，恢复“客户补齐字段后回复 + assign Suhrid + Human Review”的闭环。
- 当前 release `r20260901-69e9836` 已包含上述 Fraud 修复；近期聚焦回归为 430 passed、91 subtests passed。

### 2.5 Fraud handoff assignee SSM 配置漂移修复（2026-09-02，已完成并验收）

- 现象：AC-13196（2026-09-01 受控 Fraud Case）全链处理正常，但最终 handoff assign 给了 xieziling（31116634341396）而非 suhrid.das（31116644140308）。
- 根因：ECS Worker 的 `ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID` 经容器级 secrets 从手工管理的 SSM SecureString `/supportportal/production/zendesk-fraud-review-assignee-id` 注入；8 月 29 日 ECS 部署时写入的是 8 月 24 日之前的旧 reviewer 值，而 8 月 24 日"reviewer 换 suhrid"的变更只更新了 EC2 `.env`，两个配置面互不相通。DB 事件 `zendesk_fraud_review_handoff` payload 中 `reviewer_email=xieziling@agora.io` 为直接证据，处理代码链路本身无缺陷。
- 修复：SSM 参数覆盖为 `31116644140308`（Version 2）并 `force-new-deployment` 重启 Worker（task definition rev16 不变，无代码变更）；AC-13196 的 assignee 已手动改回 suhrid；本地 `.env` 同步新值。
- 验收：AC-13212（2026-09-02）完整通过 partial reply（追问 office address）→ 客户补料 → 内部 handoff（`support_owned_after_internal_handoff`）→ 客户 24 小时说明 → Suhrid assignment（DB 事件 assignee_id=31116644140308、reviewer_email=suhrid.das@agora.io，Zendesk 工单 assignee/group/tags readback 一致）→ `human_review_required`。
- 教训：EC2 `.env` 变更不会同步 ECS；ECS Worker 运行配置面是 SSM `/supportportal/production/*`，修改后必须重启 task 才生效。

## 3. 当前验收矩阵

| 范围 | 代码/基础设施 | 当前 ECS 是否已部署 | 业务验收状态 |
| --- | --- | --- | --- |
| Account 基础链路 | Intake、Route、Processing、邮件回复、RAGFlow、Delivery ledger、heartbeat | 是 | 等待新的受控 Account Case readback |
| Fraud | partial reply、字段提取、sensitive reconciliation、内部邮件、Suhrid handoff | 是，随 `r20260901-69e9836` | **已通过（AC-13212，2026-09-02，含 2.5 节 SSM 修复后的 Suhrid assignment readback）** |
| Enablement | Archer executor、Pilot、Persona/reply contracts | 代码在 `main`，当前 ECS 尚未部署 #1021 | 必须先 build/deploy Archer release，再做三类 Enablement Case |
| Account Suspension | 既有两阶段确认、handoff 邮件、客户回复和状态流 | 是，沿用 Account Worker | 等待新的全新 Account Suspension Case |
| Hermes | 独立 ECS service、Tencent AgentMemory memory-core、`/v1` | 是，`supportportal-production-hermes:2` | 运行级验证已通过；下一真实 needs-investigating Case 继续观察质量 |
| Slack Engineer Case | thread binding、`@bot`、Guardrail、Final Approve | 否 | 明确延期，不属于本次 Account parity 发布 |

## 4. 下架 EC2 `/production` 前的门槛

下列条件全部满足后，才可以停止 EC2 `/production` 的新流量：

1. 从 `main@ccb7ebc` 构建新的三角色 immutable `linux/amd64` release，并按同一 release 更新 API、Route、Worker。
2. 在生产 Worker 完成 Pilot deposit 和只读 Archer GET probe；不把凭据写入 task definition、日志或 release manifest。
3. Enablement 三类全新 Case 通过：
   - 有效 App ID：Archer enabled、客户公开回复成功、工单 solved；
   - 非法 App ID：客户收到纠正提示，Case 保持 open/pending，可继续提交；
   - 查无项目：客户收到核对/重发提示，Case 保持 open/pending，可继续提交。
4. ~~Fraud 全新 Case 通过 partial reply、内部邮件、客户 24 小时说明、Suhrid assignment 和 Human Review。~~ 已完成（AC-13212，2026-09-02）。
5. Account Suspension 全新 Case 通过 contact confirmation、内部 handoff 邮件、客户回复、指派和不自动关闭语义。
6. 每类 Case 都完成 Execution、Processing Job、Reply Job、Delivery ledger、Zendesk comment/status/assignee 的直接 readback；不能只以 HTTP 200、ECS `RUNNING` 或 ALB healthy 作为验收。
7. 新 release 连续观察至少两个 Outlook poll 周期，health/readiness、heartbeat provenance、CloudWatch 无持续错误，且没有无关的 Execution/Job/Delivery 增长。
8. n8n 将新流量固定到 `/automation/production` 后，先保留 EC2 `/production` 作为 backup 和回切路径；确认观察窗口通过后再考虑停止 EC2 worker，不删除历史数据库、volume 或镜像。

## 5. 当前不应做的事情

- 不要用当前 `r20260901-69e9836` 直接验收 Archer，因为它不包含 PR #1021。
- 不要重放或修改历史 `outcome_unknown`、`13190` 等失败 Case。
- 不要因为三项 health 为 200 就宣称完成三类业务 parity。
- 不要在 Slack Engineer Case 迁移完成前宣称完整旧 `/production` parity。
- 不要在新 ECS release 和三类 Case 全部验收前删除 EC2 `/production` backup。

## 6. 下一步

1. 构建并发布包含 #1021 的新 ECS release。
2. 完成 Pilot deposit 和 Archer GET probe，部署三角色并做稳定性观察。
3. 由用户手动创建新的 Enablement、Account Suspension 测试工单，逐条做业务 readback（Fraud 已由 AC-13212 完成，见 2.5 节）。
4. 三类全部通过后，再执行 n8n 流量固定和 EC2 backup 的受控下线。

