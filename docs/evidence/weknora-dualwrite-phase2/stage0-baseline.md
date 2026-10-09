# 阶段 0 冻结基线记录 — WeKnora 并行双写与人工治理链（阶段二）

- 计划名称：WeKnora 并行双写与人工治理链（阶段二），修订 r1
- 任务：p2-193
- 基线 main：d958c66f（2026-10-10 采集时根区 clean、与 origin/main 同步）
- 采集时间：2026-10-10（北京时间当日）
- 采集方式：全部只读（代码读取 + n8n MCP 只读接口 + AWS CLI 只读 describe/list）
- 本记录不做任何运行态变更；此后阶段 1+ 的实现以本记录为对照基线

## 1. 两条 n8n 工作流 active/draft 版本（线上实测）

| 链 | ID | 状态 | 当前 versionId（=activeVersionId，无发散草稿） | 节点数 | updatedAt | 触发 |
|---|---|---|---|---|---|---|
| [kb]Build\|CSD | `GgDxPEWtW7ltT5BW` | active | `b5cf6d6b-34b0-4e64-9d40-8e7c50512266` | 26 | 2026-10-06T09:28:56Z | Schedule Trigger |
| [kb]Build\|Solved Cases | `MM3Z3T469Eru3Q1I` | active | `1f544830-fc6d-4212-bbca-bd4b84d936c1` | 29 | 2026-10-06T09:25:32Z | Webhook（Ticket_Closure_Trigger，POST） |

- 两条链的描述均为 p2-186 AgentMemory 恢复链：**当前 n8n 内仍含 AgentMemory Wiki create/raw-write/ingest 直写节点**（阶段 2 需拆除的部分）。
- 仓库快照 `docs/integrations/n8n/workflows/active/{GgDxPEWtW7ltT5BW,MM3Z3T469Eru3Q1I}.published.json` 的 versionId/updatedAt 与线上完全一致，capturedAt=2026-10-06T09:29:48Z；`workflows/drafts/` 仅 `kyiA0QuiVx6JJ03i.draft.json`（与本任务无关）。
- p2-183 的 source-only 草稿（adb1156a 等）已不存在于线上——被 p2-186 的恢复发布取代；其快照合同与操作经验仍可复用（登记收敛见 §5）。

## 2. Hermes Preproduction 运行态（AWS 只读实测，2026-10-10）

集群 `supportportal-preproduction`，服务 `supportportal-preproduction-hermes`：desiredCount=1，ACTIVE，task definition **`supportportal-preproduction-hermes:45`**（容器镜像 digest 钉定）。

hermes:45 四容器结构（AgentMemory 侧配置，机密值不落盘）：

| 容器 | 关键配置 |
|---|---|
| memory-core | `TDAI_DATA_DIR=/data/tdai-memory`；LLM/Embedding key 走 SSM（`.../hermes-memory-llm-api-key`、`.../hermes-memory-embedding-api-key-v2`） |
| hermes | `MEMORY_TENCENTDB_GATEWAY_HOST=127.0.0.1`、`GATEWAY_PORT=8420`、`AGENT_ID=agt-7oifq1fctv`、`TEAM_ID=team-7oif6fsv17`、`USER_ID=usr-7oie0fnvkz`、`REQUIRE_TENANCY=1`、`RAW_CAPTURE_ENABLED=false`、`TDAI_MEMORY_ENDPOINT=http://127.0.0.1:8420`；Dashboard 开、API_SERVER 开 |
| memory-panel | `KNOWLEDGE_SERVICE_URL=http://127.0.0.1:8424` |
| knowledge（边车） | `KNOWLEDGE_PUBLIC_BASE_URL=http://127.0.0.1:8424/v3`、`LLM_MODE=custom/PROTOCOL=openai`、`LLM_MODEL=gpt-5.6-luna`、`REMOTE_INSTANCE_NAME=preproduction`（指向 8420 网关） |

- **当前知识读取路径**：Hermes → 本地 memory-core 网关（8420）→ AgentMemory；knowledge 边车（8424）经 REMOTE_INSTANCE 指向同一网关。WeKnora 不在 hermes task def 内。
- WeKnora 独立集群 `supportportal-weknora`：五服务（app/frontend/docreader/paradedb/redis）全 ACTIVE，app/frontend/docreader 均 td **`:4`**（镜像 digest 钉定）；app 容器 8080 端口、`FRONTEND_BASE_URL=https://supportcenter.stellarix.space/dashboard/weknora`、`RETRIEVE_DRIVER=postgres`（paradedb）、S3 文档桶 `supportportal-weknora-docs-891612554546-us-east-1`。阶段一（p2-188）交付的独立部署持续在运行。

## 3. 现有 SupportPortal 治理开关（线上实测，task def 环境）

| 开关 | 位置 | 当前值 |
|---|---|---|
| `HERMES_KNOWLEDGE_WORKFLOW_ENABLED` | api:121 / worker:121 | **0**（治理主开关关闭，AgentMemory 恢复期口径） |
| `WEKNORA_PROMOTION_ENABLED` | worker:121 | **0** |
| `AUTOMATION_RUNTIME_ALLOW_MEMORY` | api:121 / worker:121 / route:120 | 0 |
| `HERMES_CASE_WORKFLOW_MODE` | api:121 / worker:121 | real |
| `ENGINEER_SLACK_OUTBOUND_ENABLED` | api:121 / worker:121 | 1（频道 `C0BS0N61D1R`，team `T1CBEDLJY`） |

- 当前 Preprod task def：api **:121**、worker **:121**、route **:120**、hermes **:45**。
- `docs/release_notes.json` 仅记 Production 发布；Preprod 最新发布目录观察到 `.deployments/direct-r20261009-1a7b6e5-*`（1a7b6e51）。
- **SupportPortal→WeKnora 客户端参数缺失（重要缺口）**：SSM `supportportal/*` 共 76 个参数中无任何 `WEKNORA_BASE_URL/API_TOKEN/KNOWLEDGE_BASE_ID` 类条目；`/supportportal/weknora/*` 前缀仅 WeKnora 自身 12 个 secrets（admin_password、jwt_secret、db 凭证等）。worker:121 的环境变量中也无 WEKNORA_*。阶段 1 双写 worker 需要新建这组 SSM 参数与 task def 注入（属计划内配置变更，实施时按发布门禁走）。

## 4. 现有代码能力盘点（main d958c66f，阶段 1/4/5 的可复用底座）

### 来源接口（已存在，backend/automation_ecs_api.py）

- `POST /automation/preproduction/v1/knowledge/sources`（202）：接收快照 → `accept_knowledge_source` 持久化（仓库级幂等 task id）→ `zendesk_ticket` 走 case 绑定 Summary；`csd_issue`/`article` 走 standalone Summary（`knowledge_standalone_workflow.queue_standalone_summary_for_source`，receipt 回传 summary task 状态）。
- `GET /automation/preproduction/v1/knowledge/sources/{task_id}`（回读，去 payload/references）。

### promotion 查询/决策（已存在）

- `GET .../knowledge/promotions?status=human_review`：全量 review 输出随队列项下发（proposed_content 全文、target/base_version、Slack 线程、WeKnora object/version、人工决定审计字段）。
- `POST .../knowledge/promotions/{promotion_id}/decision`：approve（须带 resolution.action+完整 post-operation content）/reject；代际防护（来源版本/指纹漂移/reopen 拒绝）；治理主开关关闭时 **409 fail-closed**。

### Slack 人工 Review（已存在，边界与计划差距见 §6）

- 出站：`notify_knowledge_review_candidate`（engineer_slack.py）→ **绑定既有 engineer case 线程**；全文截断 1500 字符 + 指引队列 API；Slack 失败不影响 worker（通知非依赖）。
- 入站：`automation_hermes_slack_actions.py` 解析 `@bot knowledge approve <new|supplement|replace|merge> [target=] [base_version=] <正文>` / `knowledge reject`；**多候选同线程已拒绝歧义**（"use the decision API to disambiguate"）；同样的代际防护与主开关 fail-closed；bot mention 前缀为 n8n Forward Thread 过滤的硬前提（`ENGINEER_SLACK_BOT_USER_ID`）。

### WeKnora 写侧（已存在，p2-182 交付，单目标）

- `weknora_client.py`（env：`WEKNORA_BASE_URL/API_TOKEN/API_CONTRACT_JSON/KNOWLEDGE_BASE_ID/MEMORY_IDENTITY/TENANT_ID`…）+ `weknora_promotion_adapter.py`（失败模型：检索失败→outcome_unknown 或 human_review、版本变化→human_review、定向决策缺 base_version→failed、超时→outcome_unknown 同幂等键重试、写后回读不证→outcome_unknown、重试先回读对账）+ `weknora_promotion_repository.py`（状态机含 outcome_unknown/invalidated）+ worker 开关 `WEKNORA_PROMOTION_ENABLED`。
- lineage 元数据钉定 12 字段（case/ticket/summary/review run/Slack thread/source 三元组）。

### 治理工作流（已存在）

- `hermes_knowledge_workflow.py`：`knowledge_governance_enabled()`（主开关）、Summary/Review task 与 session 的代际生成（指纹=episode+ledger+conversation+全部 linked source versions）、`weknora_promotion_generation_current` 代际校验（API/Slack/写边界三处共用）。

## 5. 历史登记收敛（p2-182 / p2-183 / p2-186 / p2-188）

| 任务 | 登记 | 与阶段二的关系 |
|---|---|---|
| p2-182 WeKnora 写入适配层 | **active** | 代码已合入 main 并经隔离 PG/回归测试（189+ 用例）；**未部署激活**（WEKNORA_PROMOTION_ENABLED=0、客户端参数缺）。其 adapter/repository 即阶段 1 WeKnora 目标的复用底座；本任务推进后其剩余目标由 p2-193 承接，收口时一并迁移/关账 |
| p2-183 n8n 来源迁移 | **review** | source-only 草稿已被 p2-186 恢复发布覆盖（线上无存留）；快照合同（schema version/来源三元组/分页完整性/快照哈希/幂等键）与 snapshot 校验器流程为阶段 2 直接输入；任务停留在 review 状态，阶段 2 完成后按实际结果关账或迁移 |
| p2-186 AgentMemory 恢复 | **active** | 当前有效运行合同：治理链关闭（本基线 §3 实测）、n8n 两条 AgentMemory 直写链 active（§1 实测版本）、Preprod r20261006-c17ea45 起生效；剩余=两 n8n 链正向入库自然样本观察（与本任务阶段 2 改造存在时序交叉：source-only 改造会改变链形态，关账时需对齐） |
| p2-188 WeKnora 阶段一 | **done** | 独立 WeKnora 集群运行中（§2 实测）；其交接项（管理员密码轮换、额度监控、文件桶备份边界）继续有效，不属阶段二范围 |

## 6. 基线与计划的差距清单（阶段 1+ 要补的缺口）

1. **无候选级双目标 delivery**：现有 promotion 是单目标（WeKnora）状态机；AgentMemory 目标、`target` 维度、两目标独立补写均不存在——阶段 1 核心。
2. **无自动双写判定层**：现链路 AI Review 后一律经人工/单目标 promotion；`no_change` 显式记录、自动双写七条件门禁不存在。
3. **n8n 仍为 AgentMemory 直写链**（§1）：AI 筛选、本地去重、Wiki create/raw-write/ingest 节点待拆（阶段 2）；p2-183 的 source-only 快照合同可复用。
4. **source-only 候选无 Slack 根线程**：现有出站通知强制绑定 engineer case 线程（`build_knowledge_review_event` 必填 engineer_case_id）；知识审核频道根消息创建与 channel/thread 落库不存在（阶段 4）。
5. **操作人身份**：Slack 路径 `decided_by` 固定为 `slack-engineer`，API 路径接受客户端提交的任意 `operator` 字符串；与计划的"已验证 Slack 身份、不信任客户端 operator"有差距（阶段 4/5 收紧）。
6. **页面能力**：`promotions` 队列 API 存在，但面向双目标 delivery 状态/错误/回读的页面视图不存在（阶段 5）。
7. **Review 输入面**：现 Review 输入不含 AgentMemory 与 WeKnora 双侧检索结果（阶段 3 扩展）；检索可用性依赖 §3 的客户端参数缺口先补齐。
8. **开关粒度**：现有 `HERMES_KNOWLEDGE_WORKFLOW_ENABLED`（主）+`WEKNORA_PROMOTION_ENABLED`（WeKnora worker）两级；计划的六独立开关（来源接收/Summary-Review/双写 worker/AgentMemory delivery/WeKnora delivery/Slack 通知）需新增配置面（阶段 1/6，全部默认关闭部署）。

## 7. 环境与工具备注（采集过程）

- AWS CLI 位于 `/Users/xieziling/.local/bin/aws`（PATH 中无，须绝对路径）；凭证有效（账号 891612554546/Zac）。
- n8n 经会话内 n8n MCP 只读接口查询（get_workflow_details）；线上两条链 `versionId == activeVersionId`，即"无未发布草稿"判断的直接依据。
- 本记录中 SSM 参数仅登记名称与归属，未落任何机密值；hermes/weknora 容器 env 中密钥项以 SSM 引用形式记录。
