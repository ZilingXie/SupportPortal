# 三环境（/automation/*）状态报告与多 Thread 修复计划

**生成日期：** 2026-08-23（v1）　**刷新日期：** 2026-08-24（v2）　**基线：** `main`=`2fbb6b1`（PR #838–#900）　**EC2 运行版本：** 以部署面为准（已知 release-011=`478b45d` 起，/automation/test 回归链路已上线；PR #899/p2-104 待部署）

本文档是拆分给多个 Thread 并行执行的工作分发包，同时是三环境工作线的状态与方向记录。每个任务包自包含：目标、范围、验收标准、前置依赖、冲突域。开始任何任务包前，先通读第 0 节（总目标）、第 1 节（共同上下文）与第 4 节（协调规则）。

> **v2 刷新说明（2026-08-24）：** 按用户决策新增第 0 节总目标与路线；修正第 1 节过时内容（鉴权已统一为单一 `X-N8n-Request-Token`，preproduction allowlist 已支持 `*`）；T1–T6 状态改判（第 3 节）；新增任务包 T7（preproduction 配置统一）与 T8（production 最终切流与旧端点下线）。

---

## 0. 总目标与当前方向（2026-08-24 用户决策）

**终态目标：** `/automation/staging`、`/automation/preproduction`、`/automation/production` 三环境上线并**完全替代旧 `/account` 与 `/production`**。当前旧端点仍是主链路。

**已定方向：**

1. **preproduction 与 production 配置做成一模一样**。只保留架构固有差异：独立 schema/表/队列/网络；production 镜像级物理排除 rerun/reset（preproduction 保留 rerun）。进入的 case 由 **n8n 控制**：production 环境收 production case；preproduction 收 n8n 筛选后的 case（服务端 allowlist 设 `*` 放行，过滤交给 n8n 侧 IF 门）。
2. **production 最后切流上线**，避免与现有 `/production` 冲突：切流期间旧端点保持不动，按 Company ID 互斥名单灰度单向搬迁，全量并稳定后再下线旧端点。

**路线顺序：**

```
T7 preproduction 配置统一 + n8n 筛选流量影子验收
  → T8 production 最终切流（Company ID 互斥灰度，最后上线）
    → 观察期 → 旧端点（/account、/production）下线
```

**当前状态（2026-08-24）：** 三环境基础设施已上线（release-005 起，release-011=`478b45d` 含 p2-94/95）；staging 已切公网 200；preproduction 待重测；production 有一笔执行 `exec-bf0c82e1` 待 reconcile 后重试；`/automation/test` 回归链路（p2-97–p2-101）已建并在 EC2 上线；PR #899（p2-104）待用户部署。旧 `/account`、`/production` 仍是主链路。

---

## 1. 共同上下文（每个 Thread 必读）

### 1.1 三环境定义与策略矩阵

| 能力 | Staging | Preproduction | Production |
|---|---|---|---|
| 外部路径 | `/automation/staging/` | `/automation/preproduction/` | `/automation/production/` |
| Zendesk 写入 | 否（容器无凭据） | 是，**当前**强制 internal + allowlist（按 §0 决策，T7 将统一为与 production 一致） | 是，每次显式 `comment_visibility` |
| Take ownership / 改 status | 否 | 是 | 是 |
| Rerun / Reset | rerun + reset 均可 | 仅 rerun（无 reset） | **物理排除**（镜像缺代码 + 独立入口无路由） |
| Ticket 限制 | 测试数据 | allowlist 三态：逗号名单 / `*` 放行（过滤交 n8n）/ 空 fail-closed；T7 后设 `*` | 生产工单（无 allowlist） |
| 数据表 | `supportportal_staging.automation_executions_staging`（库 `supportportal`） | `supportportal_preproduction.automation_executions_preproduction`（库 `supportportal`） | `supportportal_production.automation_executions_production`（库 `supportportal_production`） |

架构契约：Route 容器只做分类与 action-plan 准备（无副作用）；Automation 容器按环境策略执行真实动作。策略矩阵硬编码于 `backend/services/automation_contracts.py` 的 `POLICIES`（当前：preproduction `forced_visibility=internal`，production `requires_visibility=True`）。生产执行必须显式传 `comment_visibility`，缺失即 422 拒绝。

### 1.2 鉴权与关键配置（p2-91 后已统一，v1 的 Bearer 描述作废）

- **所有 n8n 入向端点只接受 `X-N8n-Request-Token` 请求头**，值只来自单一环境变量 `n8n_request_token`（EC2 `.env`；三个环境共享同值；`hmac.compare_digest` 比较，未配置 503、缺失/错误 401，鉴权先于请求体校验）。旧的 `Authorization: Bearer` 回退、`X-Zendesk-Account-Sync-Token` 头与三个 `AUTOMATION_{ENV}_EXECUTION_TOKEN` 变量**已废弃删除**，EC2 `.env` 中可清理。
- UI（三个 `/automation/*` 页面）的 Execution token 输入框发 `X-N8n-Request-Token` 头；`/v1/auth/login` 可用 admin 凭据（`AUTOMATION_ADMIN_USERNAME/PASSWORD`，默认 admin/admin，可覆盖）换取该 token（p2-90）。
- `/v1/cases` 兼容旧五字段 intake body（`title/question/customer_email/source/customer_name`，表单或 JSON），自动推导 `zendesk_ticket_id`/`request_id`/`case_id`（p2-94）；production 仍必须显式 `comment_visibility`。
- 副作用开关：`PREPRODUCTION/PRODUCTION_ZENDESK_SIDE_EFFECTS_ENABLED=1`、`*_TARGET_TICKET_STATUS=pending`；容器内变量同名同构，开关为 0 或 target status 为空时 fail-closed（`zendesk_side_effects_not_enabled` / `automation_target_ticket_status_missing`）。
- `zendesk_basic_auth` 接受裸值与 base64 双格式（p2-95）；EC2 `.env` 保持裸值即可。

### 1.3 部署与验证操作（EC2 `zacbot:~/SupportPortal`）

```bash
# 只读验收探针（health/capabilities/鉴权/404/容器不变量/网络/DNS/Zendesk 凭据/旧端点）
./deployment/verify_split_environments.sh

# 构建/部署（按 release manifest；顺序固定 staging -> preproduction -> production）
./deployment/build_automation_release.sh --release-id release-YYYYMMDD-NNN
./deployment/deploy_ec2.sh --branch main --environment staging --release <id>
DEPLOY_PRODUCTION_APPROVED=1 ./deployment/deploy_ec2.sh --branch main --environment production --release <id>

# 回滚（仅目标环境）
./deployment/deploy_ec2.sh --environment <env> --rollback

# 本地（podman）三环境验证入口；EC2 部署面标准化对齐脚本
scripts/workflow/start_local_split_environments.sh
./deployment/deploy_surfaces_ec2.sh   # PR #875：按部署面落后判断的一键对齐
```

注意事项（均有实测教训）：
- EC2 上跑长命令用 `nohup ... > /tmp/x.log 2>&1 &` + 轮询完成标记，ssh 直连长构建会话易断。
- **recreate 主栈容器必须显式带 `APP_RUNTIME_IMAGE=localhost/supportportal-app:<ref>`**（缺失会落到残留旧镜像 `:unknown`）且加 `--no-deps`（否则连带重建依赖服务）。`deploy_ec2.sh` 现已用 `export_env_value APP_RUNTIME_IMAGE` 持久化镜像 ref，脚本层已堵住该坑，但手工 recreate 时仍须遵守。
- split 网络必须允许出站（route 调 LLM、automation 连 RDS）；deploy 脚本对既存 internal 网络 fail-closed。
- 详细契约见 `docs/deploy_automation_release.md`；n8n 切流契约见 `docs/integrations/n8n/automation_environments_cutover.md`。

### 1.4 硬约束（所有 Thread 一致遵守）

1. **不自行对真实 Zendesk 工单发起副作用验证**：真实写入只发生在用户明确批准或主导的步骤（T7 影子验收的筛选流量由用户经 n8n 送入；T8 灰度需用户批准切流窗口）。
2. **旧端点 `/account`、`/production` 在 T8 切流完成前保持不动**。
3. 所有 tracked 修改走标准 worktree 流程（`scripts/workflow/create_task_worktree.sh` → PR → `finalize_task_to_main.sh`）；一个任务包一个 PR。
4. `docs/project/tasks/p2-88.json` 是本工作线的登记源，按第 4 节协调更新。
5. 选用测试/灰度工单时注意 `solved` 状态的工单会被 side-effect 执行 reopen（如 12895 曾为 solved）。

---

## 2. 已完成基线（免于重复探索）

v1 基线（PR #838–#856，release-005 时代）：

| 成果 | PR |
|---|---|
| 三环境拆分、独立 compose project/schema/表、production 镜像物理排除 rerun | #843, #844 |
| release builder + manifest 晋级/回滚、DSN 复用、分环境 execution 表、nginx edge 接入 | #845–#849 |
| 网络出站修复（去 `--internal`）、执行 token 鉴权、production 未知写路径 404 | #850 |
| compose 显式 default 网络（podman 兼容）；鉴权先于 body 校验；human_review 500 修复；验收探针与 blocker 登记 | #851–#856 |

v1 报告之后的增量（PR #857–#900）：

| 成果 | PR |
|---|---|
| T1/T6 承接：三环境控制台 UI/功能对齐旧端点、execution 持久化原始请求、rerun 链式真实现（staging reset）、GET `/v1/executions` | #860–#863（p2-89） |
| 三环境 `/v1/auth/login`（admin 凭据换 execution token） | #864, #865（p2-90） |
| 入向鉴权统一为单一 `X-N8n-Request-Token`/`n8n_request_token`，旧头旧变量全部关闭 | #866（p2-91） |
| `/v1/cases` 兼容旧五字段 intake body | #871（p2-94） |
| `zendesk_basic_auth` 裸/base64 双格式兼容 | #882（p2-95） |
| preproduction allowlist `*` 放行（过滤交上游 n8n） | #883（p2-96） |
| release-011（`478b45d`）切流实操：staging 公网 200、preprod allowlist 修复 | 线上操作 |
| `/automation/test` 回归控制台 + 163 SMTP + 四剧本真链路驱动器 + 控制台跑剧本 | #887, #892, #893, #895, #896（p2-97–p2-101） |
| 剧本引擎共享化（backend/services/automation_test_scenarios.py）+ 测试表建表 | #897, #898（p2-102/103） |
| detailed_invoice 自动化 + 回复 PDF 转发 Zendesk | #899（p2-104） |

已线上验证（release-005 时代口径）：staging 带 token 端到端（`prepared` 落库）；preproduction 四种路由路径与 human_review 无副作用落库；suspension prepared 链路 failed+pending ledger 正确落库；三环境 rollback drill 完成。后续 release-011 起 staging 已切公网、preprod 待重测（见 §0 当前状态）。

---

## 3. 任务包

### T1 · Rerun 真实实现（P1 · 代码）

> **状态更新（2026-08-23）：已完成**——由 p2-89 承接（PR #860–#863）：execution 持久化原始请求字段（向后兼容读取，旧记录 rerun 返回 422 `execution_request_not_persisted`）、`/v1/reruns` 以新 request_id 创建链式新 execution、staging/preproduction UI 提供 rerun 入口、staging reset 真实现、production UI/镜像维持物理排除。

**背景与原范围（存档）：** 非 production runtime 的 `POST /v1/reruns` 原为 `accepted` 存根；execution 未持久化原始请求字段。原范围覆盖 execution store、`/v1/reruns`、三 UI 与测试。

### T2 · Execution store 的 DB 级回归测试 + 历史悬案复查（P3 · 代码/测试 · 可选 backlog）

> **状态更新（2026-08-24）：未承接，降级为可选 backlog。** 现状：`backend/tests/test_automation_execution_store.py` 仅覆盖表名/schema 限定与非法表名拒绝，**没有 DB 模式持久化语义断言**（save/get/idempotent upsert/human_review 落库）；release-004 记录丢失悬案未复查。与 T7/T8 无依赖，任何 Thread 可随时认领。

**背景与原范围（存档）：** 现有 runtime 契约测试全部用内存模式（`AUTOMATION_RUNTIME_ALLOW_MEMORY=1`）；为 `AutomationExecutionStore` 增加 DB-backed 测试（本地 postgres 或事务回滚式 fake），复查 release-004 时代一次 preproduction 500 后表中无记录的悬案，结论可复现则修复，否则书面排除。

### T3 · 真实工单写入验收（原 P1 · 线上操作）

> **状态更新（2026-08-24）：改判收束。** 正式的三段式验收（preprod internal / production internal+external + readback + ledger 三条 completed）未按本包格式闭环；期间线上已发生真实闭环（如 12940 全自动闭环）。按 §0 新方向，本包剩余范围**并入 T7**（preproduction 段：n8n 筛选流量全链路验收）与 **T8**（production 段：灰度切流时验收）。本包不再单独执行。

**背景与原范围（存档）：** 拿到用户批准的测试工单后，preproduction allowlist 工单 internal 全链路、production internal/external 全链路、每步核对 execution 表与 Zendesk 实际状态。

### T4 · 旧端点切流方案（原 P2 · 方案先行）

> **状态更新（2026-08-24）：设计已产出且切流已实操，剩余由 T8 承接。** 设计文档 `docs/integrations/n8n/automation_environments_cutover.md`（克隆工作流 + `TARGET_COMPANY_IDS` 互斥名单灰度、token 统一 runbook、双写红线、验证与下线清单）已合并；切流已实操至 release-011（staging 公网 200、preprod allowlist 已修复）。剩余操作——preproduction 重测、production `exec-bf0c82e1` 先 reconcile 后重试、production 最终切流与旧端点下线——全部由 **T8** 承接，设计文档仍是 T8 的权威参考。

**背景与原范围（存档）：** 产出切流设计（n8n 指向、观察期、下线清单、promote 双投递风险）。

### T5 · 运维手册补全 + verify 脚本增强（P3 · 小 · 可选 backlog）

> **状态更新（2026-08-24）：未承接，范围收缩。** 文档半边（`deploy_automation_release.md` 补容器 recreate 规范与 EC2 agent 操作红线）保留为可选 P3——`deploy_ec2.sh` 已用 `export_env_value APP_RUNTIME_IMAGE` 在脚本层堵住 `:unknown` 残留镜像坑，手工 recreate 教训仍值得沉淀成文。探针半边（`--with-staging-probe`）**放弃**：已被 `/automation/test` 回归体系（p2-97–p2-101）在更完整的轴上超越。

**背景与原范围（存档）：** runbook 补 recreate 规范；verify 脚本加带 token 的真实 staging 执行探针（默认关闭）。

### T6 · design.md 覆盖检查与 UI 迭代评估（P3 · 小）

> **状态更新（2026-08-23）：已完成**——由 p2-89 承接：design.md 新增 6.10 三环境控制台条目；UI 迭代（执行历史、详情视图、rerun/reset/reconcile 入口）已随 p2-89 实施并超出原评估范围。

### T7 · preproduction 配置与 production 统一 + n8n 筛选流量影子验收（P1 · 代码+配置+线上验收 · 独立可开工）

**目标：** 落实 §0 决策 1——preproduction 行为与 production 一模一样，进入的 case 由 n8n 控制。

**范围：**
- `backend/services/automation_contracts.py`：`POLICIES` 中 preproduction 由 `forced_visibility=CommentVisibility.INTERNAL` 改为 `requires_visibility=True`（与 production 相同）。连带行为：preproduction 显式 `comment_visibility=external` 从 422 变为允许；缺失 `comment_visibility` 从默认回落 internal 变为 422。
- capabilities 广播同步：`backend/automation_runtime.py` `/v1/capabilities` 对 preproduction 从 `["internal"]` 变 `["internal","external"]`。
- 配置（EC2 `.env`）：`PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST=*`（放行全部，过滤交给 n8n IF 门）。**机制保留不删**——三态语义（名单/`*`/空 fail-closed）与默认 fail-closed 是既有契约。
- 测试：`test_automation_contracts.py` 中 preproduction external 422、缺省回落 internal 的用例改为新契约（external 允许、缺失 422）；runtime capabilities 用例同步。
- 文档联动：`docs/integrations/n8n/automation_environments_cutover.md` §3.3/§4、`docs/deploy_automation_release.md` 中"preproduction 只接受 internal"的表述更新为统一后契约。
- 保留差异声明（不改动）：独立 schema/表/队列/网络；production 镜像级 rerun/reset 物理排除；preproduction 保留 rerun。

**验收：** 单测全绿；部署新 release 后 capabilities 显示 preproduction `["internal","external"]`；n8n 侧配置 IF 筛选门后，用户送入的筛选 case 走 preproduction 全链路——execution `completed`、ledger 三条 operation 均 completed、Zendesk readback（ownership/internal comment/status）核对一致（即原 T3 preproduction 段）。external comment 在 preproduction 仅在用户显式要求时验证。

**冲突域：** `automation_contracts.py`、`automation_runtime.py` capabilities、契约测试、cutover/runbook 文档、EC2 `.env`、n8n preproduction 工作流。规模：中。
**登记：** 认领时在 `docs/project/tasks/` 新建任务 ID（p2-10x），更新 p2-88 history 指向。

### T8 · production 最终切流与旧端点下线（P1 · 线上操作+文档 · 前置：T7 全绿 + 用户批准切流窗口）

**目标：** 落实 §0 决策 2——production 最后上线，最终替代 `/account` 与 `/production`。

**范围（以 `docs/integrations/n8n/automation_environments_cutover.md` §4/§8/§9 为权威操作手册）：**
1. 前置清账：production 既有执行 `exec-bf0c82e1` 先 reconcile（服务端 Zendesk readback）后按需重试。
2. n8n 克隆 `new_case_2_supporportal_prod` 为 `new_case_automation_prod`：改 URL 至 `/automation/production/v1/cases`、表单加 `comment_visibility=internal`、挂 `X-N8n-Request-Token` 凭据；旧新两个 `TARGET_COMPANY_IDS` 名单**必须互斥**（双写红线）。
3. Company ID 单向灰度搬迁（初始建议 1 个低风险公司），逐步扩大；production 环境最后承接 production case，避免与现有 `/production` 冲突。
4. 验收（即原 T3 production 段）：灰度公司新单 → 新环境 execution `completed` + Zendesk readback；旧 `/production/account` 不再出现该工单 account case；`commen_sync`/`case_status_sync` 对新环境工单 membership miss 属预期。
5. 观察期后下线：移除 nginx 旧 location（`/account`、`/production/*`）与旧容器；处置旧 `automation_executions` 数据；复查 promote 双投递残留（p2-73/p2-74 历史）。

**验收：** cutover 文档 §8 清单全过（staging/preproduction/production 三段 + 旧链路对照 + `verify_split_environments.sh` 全绿）；旧端点下线后回归无残留依赖。
**冲突域：** n8n 工作流、EC2 线上操作、nginx 配置、p2-88 evidence；代码改动预期为零（如遇契约缺口回 T7 修）。

---

## 4. 多 Thread 协调规则

1. **Worktree/分支命名：** `split-<任务包缩写>-<slug>`（如 `split-t7-config-parity`、`split-t2-store-tests`），一个任务包一个 PR。
2. **p2-88 更新：** 只 append evidence/history，不改他人条目；合并前 rebase 最新 `origin/main`；evidence.type 只允许 `pr/test/deployment/document/decision`。
3. **EC2 部署串行：** 单机构建，同一时间只允许一个 Thread 构建/部署 release（标准化入口 `deploy_surfaces_ec2.sh`，按部署面落后判断）；部署前后各跑一次 `verify_split_environments.sh` 并在 PR/报告中记录结果。production 部署带 `DEPLOY_PRODUCTION_APPROVED=1`。
4. **不越界：** 各任务包只做自己范围；发现范围外问题记录到 p2-88 history，不顺手修。
5. **状态源：** 本文件 + `p2-88.json` + `docs/integrations/n8n/automation_environments_cutover.md` + `docs/deploy_automation_release.md`；冲突以最近合并的 `main` 为准。

## 5. 待用户决策清单

已决策（2026-08-24，记录于 §0，不再列待办）：终态=三环境替代 `/account` 与 `/production`；preproduction 配置与 production 统一；进入 case 由 n8n 控制；production 最后切流。

| 决策 | 影响任务 | 说明 |
|---|---|---|
| n8n 进 preproduction 的筛选规则 | T7 验收 | 用户在 n8n 侧配置 IF 门；规则内容用户自定，只需确认生效并用于影子验收 |
| T7/T8 的 EC2 部署与切换窗口 | T7, T8 | 用户侧 EC2 agent 执行，或明确授权本 agent 执行 |
| production `exec-bf0c82e1` reconcile 重试时机 | T8 前置 | 建议 reconcile（internal）确认状态后作为 T8 第一步重试 |
| 旧端点下线时间表 | T8 | 观察期长度与最终下线动作需用户批准 |
