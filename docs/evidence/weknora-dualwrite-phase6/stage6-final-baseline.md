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

## n8n 三链发布与关闭态验证（v2-A #8+#9 前半，2026-10-12）

发布前三链快照与线上逐字一致（CSD 6fed47a6 / Solved 6a262f9a / FT 4ed2c0c3），validator 通过（15 published+4 drafts+56 redactions；LEGACY-EXPOSED 为旧链已知告警）。发布后回读 activeVersionId 已切换：

| 链 | 新 active（原 draft） | 回滚基线（原 active） |
|---|---|---|
| [kb]Build\|CSD | **6fed47a6**（source-only） | b5cf6d6b（p2-186 直写链） |
| [kb]Build\|Solved | **6a262f9a**（source-only） | 1f544830 |
| [slack]Forward Thread | **4ed2c0c3**（raw_text+bot_user_id） | b02f3ed5 |

关闭态验证（全部只读/零副作用）：

- 探针 POST（合法 knowledge-source-v1 schema）→ **503** "knowledge source intake is disabled … no snapshot was accepted"，DB 零写入（intakes 表探针源 0 行）。
- 治理开关回读：master=0、六开关全 0、allowlist 空（api:140/worker:140 task def）。
- 自然触发路径：CSD 下一自然调度=2026-10-13 12:00 北京（发布前当日 12:00 已跑过旧链）；Solved 随真实工单关闭触发。关闭期内两链将收 503 并走 errorWorkflow 告警=关闭态的线上自然证据（预期行为；开启 intake 后消失）。
- 零 claim/零外部写入/零 Slack：开关全 0 下 worker drain 与 delivery 目标均不可达（构造性 fail-closed+task def env 回读）。

## 小范围开启层 1：master+intake（v3-A 步 9 层 1，r20261013-42a187a）

发布：`--knowledge-stage-config`（master=1、KNOWLEDGE_SOURCE_INTAKE_ENABLED=1、其余五开关 0、allowlist 空）；全阶段 passed（UTC 日期翻转后新 release id，无状态目录冲突；旧全关部署 checkpoint/evidence/timings 已归档至本目录 alloff-deploy-state/）。回读：worker **:141**、master=1、intake=1、summary/review/worker/delivery/slack 全 0、allowlist 空；步 2 双 verify 门再次通过（部署日志两见）。

接收层验证（探针=显式合成非业务源，永不入 allowlist）：

- POST `csd_issue:CSD-STAGE6-L1-PROBE` → **202 accepted** + task_id 回执；intakes 表落库（总数 101→102，状态 accepted）。
- 零 AI：probe Summary 0 条；summaries 总数 36 不变、旧任务 1 不变——受执与 AI 处理面在本层彻底分离。
- 任务创建机制确认：allowlist 挡任务创建（受执时评估）；**重投=重试触发器**（queue_standalone_summary_for_source R25 合同）——层 2 加 allowlist 后对选定快照受控重投即建任务。

层 2 待自然到达来源：CSD 链下次自然扫描 2026-10-13 12:00 北京（04:00 UTC）经 source-only active 链投递（JQL 窗口内 RESOLVED CSD）；Solved 链随真实工单关闭 webhook 触发。到达来源在 R1 态=202 受执+零任务（allowlist 空），从中选定 1–2 个可唯一追踪来源进层 2 的 allowlist，受控重投后走全链。

## CSD 自然首跑暴露的链缺陷与修复（2026-10-13 04:00 UTC）

CSD 链首次真实执行（execution 191877）逐步暴露三个阶段二作者期缺陷（此前均为 waiting-for-evidence 状态）：

| # | 缺陷 | 修复 | 版本 |
|---|---|---|---|
| D1 | Build 节点 `crypto.createHash` 在 n8n JS task-runner 沙箱无 `crypto` 全局（ReferenceError） | 嵌入纯 JS SHA-256（本地对 node:crypto 六向量验证含 unicode/emoji/100KB），语义不变 | CSD/Solved 已发布 |
| D2 | 投递 URL 钉旧域名 `support.stellarix.space`（nginx 410 Gone） | 两链 Deliver 节点改 `supportcenter.stellarix.space/.../knowledge/sources`（与探针实证一致） | CSD 85bbcb0c / Solved f665df09 |
| D3 | n8n 凭据 `preprodcution`（id wk9Nn0OELBE6MOb7）的 intake token 无效（SP 401 invalid intake token；SSM token 从未轮转过 v1，凭据系作者期即错） | **未修复——阻断在 n8n owner 侧**：需在 n8n UI 更新凭据（Header Auth：Name=Authorization，Value=Bearer <SSM automation-intake-shared-token 现值>）或提供 n8n API key。Public API（X-N8N-API-KEY）与内部 REST 及浏览器会话（无登录）三条自动路径均已试不通 | 待用户 |

修复后验证：D1+D2 修复实弹执行推进到投递节点（execution 191883：JQL 21 条→逐条详情→完整性门→快照（哈希 93f998cc…64 位正确）→投递 401=D3）。快照已按新版本刷新（validator 通过 15+4+56，URL allowlist 更新至正确主机并注释原因）。凭据修复后：受控重跑 CSD 即完成自然到达投递（21 条 RESOLVED CSD 满足 JQL 窗口），层 2 样本从中选定。

## D3/D4 修复与 CSD 全链 SUCCESS（2026-10-13 05:23 UTC）

- **D3（凭据）已修**：用户提示下经 n8n MCP `setNodeCredential` 将两链 Deliver 节点切换至 `automation_production`（httpBearerAuth，`toE81efBqb60KXsy`）——旧 case 链每天用它在同一 SP `/v1/*` token 中间件下成功投递的凭据；原 `preprodcution` 凭据从未被该校验（FT 旧链的消费者走 `/api/` 路径不经中间件）。
- **D4（字段清单）已修**：source-only 重写把详情节点 `fields` 从旧链显式清单改成 `*,comment`，Jira 解析为仅返回 comment → `source_updated_at` 空 → SP 422。已恢复显式清单（summary,description,updated,created,status,resolution,comment,customfield_10700,customfield_13915）。
- **结果：execution 191901 全链 SUCCESS**（17 秒）：JQL 21 条→逐条详情→完整性门→快照（纯 JS SHA-256）→投递 202→三态回执校验→Scan Complete。**20 条自然 csd_issue 来源落库（全 accepted）**；零 AI 不变量保持（summaries=36 不变）。
- 版本：CSD active **6511c2e9**、Solved active **d4c50a10**；快照已同步刷新（validator 15+4+56 通过）。
- 层 2 输入就绪：20 条自然到达来源可从中选定受控样本（+受控重投触发建任务机制已确认）。

## 层 2 执行记录（2026-10-13）

- 样本选定：**CSD-79923**（approve 候选，payload 40KB 最丰富）+ **CSD-79994**（reject 候选，V2V 双语详细调查）；层 2 config={master=1, intake=1, summary_review=1, allowlist=csd_issue:CSD-79923,csd_issue:CSD-79994}。
- 层 2b（r20261013-42a187a 第二发，mode=disabled）：全阶段 passed，worker:143 开关与 allowlist 逐字上线；但受控重投（202 already_exists）**未建任务**——根因=`knowledge_workflow_active()` 要求 HERMES_CASE_WORKFLOW_MODE=real+agent gateway（standalone drain 与建任务门共用该门），层 2b 镜像了当时线上 disabled 模式故门关。层 2b checkpoint 已归档 layer2-disarmed-state/。
- 层 2c（第三发，mode=real+--hermes-agent-enabled+--bootstrap-account-schema，runbook 钉定的治理管线配对）：进行中。完成后受控重投两样本即应建 pending Summary 任务（worker 开关仍关=零 AI 执行）。
- ★机制记录：同日同 SHA 的分层发布=每层归档前一 checkpoint（identity 含 knowledge_stage+mode）；重投回执 already_exists 仍会重跑建任务门（端点代码 560-600 行）。

## 层 2c 失败记录（2026-10-13，已回滚）

mode=real+agent+bootstrap 的层 2c 在部署后 Provider 探针任务失败：`CannotPullContainerError … supportportal/preproduction@sha256:dfce32bd…: not found`（ECR 无该 digest）。部署门禁按设计工作：Prompt 激活前失败→任务定义还原→无分裂状态。线上保持层 2b（master=1/intake=1/summary_review=1/allowlist 两样本/mode=disabled——建任务门因 mode 关仍无效，等层 2c 修复）。待诊断：探针任务引用的 digest 来源（本轮 codebuild 重建过镜像；探针可能引用了陈旧 digest 或本次 push 异常）。
