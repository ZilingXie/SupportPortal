# 阶段 6 证据 — 最终发布基线冻结（p2-197，步 3 / v2-A #4+#5 前置盘点）

- 计划名称：WeKnora 并行双写与人工治理链（阶段二），修订 r3-stage6-deploy v2-A
- 代码基线：main **42a187ae**（PR #1482 合入；行为 5937c9e7+B1+I1 sync dff24e83）
- 采集时间：2026-10-12（步 2 合入后、全关闭部署前）；AWS/n8n/DB 全程只读

## 运行态基线（部署前快照，回滚锚点）

| 项 | 当前值（r20261012-ca94a4c，他线 Hermes v3.2 部署） |
|---|---|
| api | task def **:139**，APP_BUILD_REF=ca94a4cc…，digest sha256:76f5bb34… |
| worker | task def **:139**，digest sha256:9475eb89… |
| route | task def **:137**，digest sha256:404a8151… |
| hermes | task def **:45**（不变） |
| PROMPT_RELEASE_ID | pr-9737f67803f1（三角色一致） |
| AGENT_MODEL_ID | gpt-6.1-sol |
| HERMES_CASE_WORKFLOW_MODE | disabled（三角色）；ENABLEMENT_WORKFLOW_MODE=manual |
| persona/agent | ENGINEER_INVESTIGATION_REPLY_* 与 HERMES_AGENT_* secrets 均在（api+worker）→ 本次部署镜像 `--hermes-persona-enabled --hermes-agent-enabled` |
| 治理开关（api+worker） | master=0、六开关全 0、allowlist 空、lease 300、WEKNORA_PROMOTION_ENABLED=0（worker）——他线部署已把步 1 渲染器上线 |
| 知识面 SSM 引用（worker） | AGENT_MEMORY_WIKI_BASE_URL/SERVICE_ID/TEAM_ID/USER_KEY + KNOWLEDGE_SLACK_WORKSPACE_DOMAIN（步 1 渲染器注入）；**无任何 WeKnora 引用**（步 2 代码未上线，本次部署补齐） |

## SSM 基线（v2-A #5 确认）

`/supportportal/preproduction/` 知识面 8 参数全部就绪：

| 参数 | 类型 | 版本 |
|---|---|---|
| agent-memory-wiki-base-url / -service-id / -team-id | String | 1 |
| AGENT_MEMORY_WIKI_USER_KEY → hermes-wiki-core-user-key（复用） | SecureString | 1 |
| knowledge-slack-workspace-domain | String | 1 |
| weknora-base-url / -knowledge-base-id | String | 1 |
| weknora-api-token | SecureString | 1 |
| weknora-api-contract-json | String | **4**（合同 v4，实弹 15/16 验证版） |

## n8n 基线（与步 1 冻结一致，未变）

| 链 | active | draft（待步 8 发布） |
|---|---|---|
| [kb]Build\|CSD `GgDxPEWtW7ltT5BW` | b5cf6d6b | 6fed47a6 |
| [kb]Build\|Solved `MM3Z3T469Eru3Q1I` | 1f544830 | 6a262f9a |
| [slack]Forward Thread `r1HIW8UNuCabiOPn` | b02f3ed5 | 4ed2c0c3 |

## DB schema 现状与本次部署预期

- 线上 schema：v22 已落（support_knowledge_source_intakes/summaries/reviews + support_knowledge_deliveries 存在）；**v23（Slack review 六列）未落**——本次部署带 `--bootstrap-account-schema` 落地 v23。
- Terraform 零漂移由 preflight `terraform plan -detailed-exitcode` 门禁验证（本次部署内执行）。

## 历史队列盘点（allowlist 缺口 #3，部署前只读清点）

| 表 | 行数 | 状态分布 | 可消费性结论 |
|---|---|---|---|
| support_knowledge_source_intakes | 101（65 zendesk_ticket + 36 csd_issue，2026-10-01~06） | 全 accepted | 36 条 csd_issue 已配对 completed Summary（不重处理）；**65 条 zendesk_ticket 无 Summary——开启 Summary/Review 后由 KNOWLEDGE_SOURCE_ALLOWLIST 挡住**（精确 allowlist 不含这些工单 id；快照保留不删除）。步 10 全量 zendesk_ticket:* 通配放开属已授权的扩大范围动作（需用户再次确认） |
| support_knowledge_source_summaries | 36 | 全 completed（全 csd_issue） | 已终结，无消费者动作 |
| support_knowledge_source_reviews | 36 | 全 completed | 已终结 |
| support_knowledge_deliveries | 0 | — | 无投递行 |
| support_weknora_promotions（旧 p2-181 队列） | 64 | 全 human_review | 消费者=已退役旧 promotion worker（WEKNORA_PROMOTION_ENABLED 恒 0，渲染器钉死）→ **不可消费，无需隔离** |
| support_hermes_summary_tasks（旧链） | 1 | failed（991000183-1 测试工单，2026-10-02） | 消费者受 HERMES_KNOWLEDGE_WORKFLOW_ENABLED+hermes real mode 双门（本次部署 mode=disabled、master=0）→ **不可消费，无需隔离** |

**结论：不存在需要隔离方案的未授权可消费行**。唯一会在开启后暴露的历史面=65 条无 Summary 的 zendesk_ticket 快照，由方案 A allowlist 默认严格合同挡住（不在 allowlist=拒绝创建任务、快照保留、rejection reason 可审计）。

## 本次部署参数（全关闭态）

```
--release-commit 42a187ae884015b833277b7b4e23d624a647663b
--prompt-release-id pr-9737f67803f1
--through preproduction
--hermes-case-workflow-mode disabled --hermes-persona-enabled --hermes-agent-enabled
--bootstrap-account-schema（落 v23）
（无 --knowledge-stage-config → 默认全关：master=0、六开关=0、allowlist 空）
```

## 全关闭部署结果（v2-A #7，r20261012-42a187a）

部署管线全阶段 passed（preflight→prompt_schema→route_worker_rollout→heartbeat→api_rollout→collector→activation→release_note；codebuild 经 S3 证据恢复；总 1284s，SLO breach=true 系 resume+bootstrap 开销，非失败）。证据：`.deployments/ecs-pipeline-r20261012-42a187a/preproduction-deploy/evidence.json`（根区）。

部署后回读（全部通过）：

| 验证项 | 结果 |
|---|---|
| ECS revisions | api **:140** / worker **:140** / route **:138**，服务 ACTIVE 1/1×3 |
| 目标 release | 三角色 APP_BUILD_REF=42a187ae…、AUTOMATION_RELEASE_ID=r20261012-42a187a |
| 治理开关 | master=0、六开关全 0、KNOWLEDGE_SOURCE_ALLOWLIST 空（api+worker） |
| WeKnora 客户端 | worker 七引用（WEKNORA_BASE_URL/API_TOKEN/API_CONTRACT_JSON/KNOWLEDGE_BASE_ID+HERMES_WEKNORA_*）+WEKNORA_AUTH_HEADER_NAME=X-API-Key+空 scheme 首次上线；无 WEKNORA_MEMORY_IDENTITY |
| 旧路径 | WEKNORA_PROMOTION_ENABLED=0（worker） |
| schema v23 | support_ticket_schema_meta=2026-single-ai-managed-v23-knowledge-slack-review；support_weknora_promotions Slack 六列实证（slack_review_event_id/status/message_ts/failure_code/owner_token/claimed_at+human_decided_slack_user_id） |
| Terraform | preflight plan 零漂移（exit 0） |
| 步 2 门禁 | 部署日志两见 "Knowledge stage config verified on rendered api/worker definitions"（渲染后+激活前） |

过程披露：首次启动因 TICKET_DB_SCHEMA 缺省（源 Prompt 校验读 supportportal schema 无权限）失败；恢复时孤儿 CodeBuild 已 SUCCESS，经 S3 证据恢复路径复用（无重复构建）；二次因缺 DEPLOY_PREPRODUCTION_APPROVED=1 于 deploy 门停下；三次带全环境完成。三次启动均落在同一 checkpoint（identity 一致）。
