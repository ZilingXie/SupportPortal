# Automation ECS Runtime 与迁移计划

Recommendation: Sol medium for implementation. 运行时边界、数据生命周期和 release 规则已经收敛；后续基础设施与部署必须按门禁执行，不能重新解释 runtime contract。

状态：`stage_2_implementation_complete_artifact_build_pending`

- Project Task：`p1-53`
- Project Phase：`phase-1`
- Module：`platform-delivery`
- Function：`ecs-environment-migration`
- 当前完成范围：Production-safe runtime、schema、角色镜像定义、Release Manifest 与 promotion tooling
- 当前未完成范围：真实 OCI artifact build、ECR/ECS/AWS/Cloudflare/n8n/远端 RAG变更与任何流量切换

## 不可变边界

1. 现有 `https://support.stellarix.space/production/...` 是 EC2 backup，行为、进程、Nginx、n8n workflow、Redis、schema和 Worker均不修改、不重启、不切流。
2. 新服务入口为 `https://supportcenter.stellarix.space/automation/production/...`；路径含义固定为服务类型 `automation` + 环境 `production`。
3. 同一 release未来支持 `/automation/preproduction`；Staging最后在 EC2单独建设，不使用当前 production-safe镜像的控制面。
4. 当前 ECS release只有 `api`、`route`、`worker`三个长运行角色，不包含项目内 `rag_api`或 `rag_worker`。
5. 远端 RAG只通过 `RAG_SERVICE_URL`和 `RAG_SERVICE_SHARED_TOKEN`边界接入。真实 endpoint、请求/响应与 health contract未确认前不得猜测或建立兼容假象。
6. 所有外部副作用在认证之后发生；`outcome_unknown`保持终态并进入 Human Review，不得由自动重试掩盖。

## 新 n8n Intake Contract

Endpoint：

```text
POST /automation/{preproduction|production}/v1/intake
Authorization: Bearer <AUTOMATION_INTAKE_SHARED_TOKEN>
Content-Type: application/json
```

API在读取或解析 body前完成 token鉴权。未授权的 malformed JSON返回 `401`且零写入。成功首次接收返回 `202`；相同 `event_id`和相同 canonical payload返回同一 `execution_id`并标记 replay；相同 `event_id`但 payload不同返回 `409 event_payload_conflict`。

最小 envelope：

```json
{
  "schema_version": "automation-intake-v1",
  "event_id": "zendesk:ticket:12345:created",
  "event_type": "ticket.created",
  "occurred_at": "2026-08-27T10:00:00Z",
  "ticket": {
    "id": "12345",
    "status": "open",
    "subject": "Enable Media Relay",
    "description": "Please enable Media Relay for app abc.",
    "requester": {
      "id": "88",
      "name": "Customer",
      "email": "cx@example.com",
      "role": "end-user",
      "is_agent": false
    },
    "organization": null,
    "tags": ["automation"],
    "custom_fields": {},
    "updated_at": "2026-08-27T10:00:00Z"
  }
}
```

支持事件：

- `ticket.created`：必须包含非空 description。
- `ticket.updated`：用于持久化 Ticket snapshot并异步同步状态。
- `comment.created`：除 Ticket snapshot外，必须提供 `snapshot_complete=true`、`source_updated_at`、完整 comments数组和数组内的 `trigger_comment_id`。

Zendesk Ticket ID是唯一 Case业务身份；Comment ID同样要求数字字符串。系统只生成 Execution/Job/Action UUID，不生成或暴露 `AC-*` Case ID。

## 三角色 Runtime

```text
n8n
  |
  v
API: auth -> validate -> Case/Event/Execution persist -> Route Job -> 202
  |
  v  RDS FOR UPDATE SKIP LOCKED
Route Worker: classify -> persist Route -> fix Persona -> Processing Job
  |
  v  same RDS transaction boundary
Automation Worker: plan/process -> AI/remote RAG -> delivery ledgers -> terminal state
```

### API

- 不调用 Route、AI、RAG、Zendesk、邮件或 Slack。
- 原子持久化 Case snapshot、Comment snapshot、Intake Event、Execution、Step/Event timeline和 Route Job。
- 提供受保护的 Execution详情与 Zendesk Ticket历史查询。
- 提供公开 `health/live`、`health/release`和 `health/ready`；ready要求 schema正确且 Route/Worker heartbeat都在新鲜阈值内。

### Route Worker

- 只 claim `route` Job，调用 side-effect-free `decide_account_route(require_latest=True)`。
- 将 classification、decision、Prompt snapshots、Route build provenance与稳定 Persona assignment持久化。
- Route完成与 Processing Job创建在同一事务中。
- Route失败进入 Human Review，不创建 Processing Job；安全租约过期可重新 claim。
- Comment processing消费已持久化 Route结果，不在 Automation Worker内二次调用 Route模型。

### Automation Worker

- claim `processing` Job并按 `ticket.created`、`comment.created`、`ticket.updated`分支处理。
- 复用现有 Account persistence、field extraction、reply job、远端 RAG client、Zendesk、邮件与 Slack ledger原语。
- Account-only drain独立于 Redis ticket consumer运行；ECS模式不导入 `backend.main`。
- 在可能产生外部结果前持久化 external boundary与 delivery状态。边界后的异常、租约过期或 provider结果不确定均进入 `outcome_unknown`，Job不可再次 claim。
- Route/Worker都使用独立线程持续写 DB heartbeat，长 AI/RAG调用期间仍可验证新鲜度。

## Execution 与数据生命周期

每个已接受事件对应一个 Execution，并同时维护当前状态与 append-only timeline：

```text
route_pending -> routing -> processing_pending -> processing
                                              |-> completed
                                              |-> human_review
                                              `-> outcome_unknown
```

隔离配置：

- `AUTOMATION_DB_DSN` / `AUTOMATION_DB_MIGRATION_DSN`
- `AUTOMATION_DB_SCHEMA`，名称必须识别当前 environment
- `AUTOMATION_JOB_NAMESPACE`，名称必须识别当前 environment
- `TICKET_DB_DSN` / `TICKET_DB_MIGRATION_DSN` / `TICKET_DB_SCHEMA`
- `AUTOMATION_DB_RESOURCE_ID`与 `AUTOMATION_RUNTIME_IDENTITY`

ECS coordination schema包含 Case、Comment、Intake Event、Execution、Step、append-only Event、Route/Processing Job、Delivery Ledger和 Worker Heartbeat。PostgreSQL claim使用 `FOR UPDATE SKIP LOCKED`；DDL只由一次性 bootstrap角色执行，长运行角色仅做只读 schema preflight。

Execution query必须显示 environment、release/build/image/schema/Prompt provenance、当前 stage、failure stage/code、attempt、worker identity、Route、Persona、steps、timeline、deliveries与 Human Review要求。

## Release 与 ECR

ECR仓库按环境划分：

```text
supportportal-preproduction
supportportal-production
```

每个 release包含三个 immutable role tag：

```text
api-<release_id>
route-<release_id>
worker-<release_id>
```

`deployment/build_automation_ecs_release.sh`从干净 commit各构建一次 `linux/amd64` OCI layout，使用 Docker role `ecs-api`、`ecs-route`、`ecs-worker`，并生成 repository-independent `automation-release-v1` JSON Manifest。Manifest记录 commit、build time、Prompt Release、schema/contract versions、role tag和 OCI manifest digest，不记录环境 repository URI。现有 EC2 split release继续使用未改动的 `deployment/build_automation_release.sh`。

镜像物理排除：

- `backend.main`
- legacy `automation_runtime` / `automation_production_runtime`
- staging rerun/reset模块与测试代码/UI
- 项目内 `rag_api` / `rag_worker`入口

Preproduction验收后，`deployment/promote_automation_release.sh`按 digest读取 source OCI manifest并写入 Production repository，逐角色验证 target digest完全相等，另写 `automation-promotion-v1` Promotion Record。Promotion不修改 Release Manifest，也不包含 build命令。ECS task definition只允许 `repository@sha256:...`，不使用 tag作为运行引用。

当前主机没有 Docker可执行文件，因此本次实现没有生成或上传真实 OCI artifact。真实 build、Preproduction push、digest readback和 promotion仍是手工 gate。

## 阶段顺序

### Stage 0：盘点，已完成

只读确认 AWS/VPC/RDS/DNS/IAM/现有 EC2 runtime与线上路径；不改变外部状态。

### Stage 1：Runtime设计，已完成

确认三个角色、Zendesk Ticket ID身份、RDS durable Jobs、Execution trace、远端 RAG边界和 immutable promotion规则。

### Stage 2：Release实现与本地验证，代码完成

完成 runtime/schema/images/Manifest/promotion tooling及本地 contract/PostgreSQL测试。待具备 Docker Buildx的干净 commit生成真实 OCI bundle。

### Stage 3：ECS Foundation，待执行

创建两个 immutable ECR repository、IAM/OIDC、Fargate/ALB/ACM、Secrets/Logs/Alarms与隔离 schema身份。该阶段不切流。

### Stage 4：Preproduction，待执行

将一次构建的 OCI manifests上传 `supportportal-preproduction`，按 digest部署 `/automation/preproduction`，完成 schema/Prompt/heartbeat/远端 RAG和受控 Zendesk/邮件/Slack readback。

### Stage 5：Production，待执行

在 Preproduction证据通过后复制相同 manifests到 `supportportal-production`，验证 digest一致，再按 digest部署 `/automation/production`。此时仍不修改现有 `/production`。

### Stage 6：EC2 Staging，待执行

在现有 EC2建立独立 Staging runtime。Staging-only rerun/reset不得进入 ECS release。

### Stage 7：切流，待执行

确认 live n8n payload、少量真实 Case与所有外部 readback后，只把后续新 Case改投 ECS endpoint。回滚只改变后续 n8n目标，不迁移、不重放既有 Execution，也不重试 `outcome_unknown`。

## 部署前门禁

1. 远端 RAG endpoint/auth/query/response/health contract已由真实联调确认。
2. OCI bundle从干净 commit构建，Manifest validate通过，三个 role digest从 ECR readback一致。
3. Preproduction与Production schema、job namespace、runtime identity、Secrets和日志完全隔离。
4. Bootstrap使用短期 migration凭据；长运行 task没有 DDL权限。
5. API readiness同时看到 Route与Worker的新鲜 heartbeat，build/release/image/schema/Prompt provenance匹配 Manifest。
6. n8n使用新 Intake v1和 Bearer token；token错误在 body解析前失败。
7. Zendesk、邮件、Slack需要 provider readback；HTTP 200、queued或本地 `published_at`都不作为送达证明。
8. 任何 `outcome_unknown`留在 Human Review，不通过 replay或自动重试清除。
9. EC2 `/production`在整个部署与验收期间保持不变。
