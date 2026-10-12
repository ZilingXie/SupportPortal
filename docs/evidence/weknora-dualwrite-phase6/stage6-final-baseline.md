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
