# 三环境（/automation/*）状态报告与多 Thread 修复计划

**生成日期：** 2026-08-23 　**基线：** `main`=`1ba6a56`（PR #838–#856）　**EC2 运行版本：** `release-20260822-005` 　**当前健康度：** `verify_split_environments.sh` 36/36 全绿，主栈 `/production` 凭据已修复

本文档是拆分给多个 Thread 并行修复的工作分发包。每个任务包自包含：目标、范围、验收标准、前置依赖、冲突域。开始任何任务包前，先通读第 1 节（共同上下文）与第 4 节（协调规则）。

---

## 1. 共同上下文（每个 Thread 必读）

### 1.1 三环境定义与策略矩阵

| 能力 | Staging | Preproduction | Production |
|---|---|---|---|
| 外部路径 | `/automation/staging/` | `/automation/preproduction/` | `/automation/production/` |
| Zendesk 写入 | 否（容器无凭据） | 是，强制 internal | 是，每次显式 internal/external |
| Take ownership / 改 status | 否 | 是 | 是 |
| Rerun / Reset | 允许 | 允许 rerun | **物理删除**（镜像无代码、OpenAPI 无路由） |
| Ticket 限制 | 测试数据 | 必须在 allowlist（`.env: PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST`，当前 `12872,12895`） | 生产工单 |
| 数据表 | `supportportal_staging.automation_executions_staging`（库 `supportportal`） | `supportportal_preproduction.automation_executions_preproduction`（库 `supportportal`） | `supportportal_production.automation_executions_production`（库 `supportportal_production`） |

架构契约：Route 容器只做分类与 action-plan 准备（无副作用）；Automation 容器按环境策略执行真实动作。生产执行必须显式传 `comment_visibility`，缺失即 422 拒绝。

### 1.2 鉴权与关键配置

- 所有执行端点（`/v1/cases`、`/v1/reruns`、`/v1/reset`、`/v1/executions/{id}/reconcile`）要求 `Authorization: Bearer <token>`；token 在 EC2 `.env` 的 `AUTOMATION_{STAGING,PREPRODUCTION,PRODUCTION}_EXECUTION_TOKEN`，鉴权经路由级 `Depends` 先于请求体校验（空 body 无 token 也 401）。UI（三个 `/automation/*` 页面）有 Execution token 输入框，存 localStorage。
- 副作用开关：`PREPRODUCTION/PRODUCTION_ZENDESK_SIDE_EFFECTS_ENABLED=1`、`*_TARGET_TICKET_STATUS=pending` 已配置生效；开关为 0 或 target status 为空时执行 fail-closed（`zendesk_side_effects_not_enabled` / `automation_target_ticket_status_missing`）。
- `zendesk_basic_auth` 已于 2026-08-23 更新为有效 `email:token` 值（主栈与 split 均已验证 200）。

### 1.3 部署与验证操作（EC2 `zacbot:~/SupportPortal`）

```bash
# 只读验收探针（36 项：health/capabilities/鉴权/404/容器不变量/网络/DNS/Zendesk 凭据/旧端点）
./deployment/verify_split_environments.sh

# 构建/部署（按 release manifest；顺序固定 staging -> preproduction -> production）
./deployment/build_automation_release.sh --release-id release-YYYYMMDD-NNN
./deployment/deploy_ec2.sh --branch main --environment staging --release <id>
DEPLOY_PRODUCTION_APPROVED=1 ./deployment/deploy_ec2.sh --branch main --environment production --release <id>

# 回滚（仅目标环境）
./deployment/deploy_ec2.sh --environment <env> --rollback
```

注意事项（均有实测教训）：
- EC2 上跑长命令用 `nohup ... > /tmp/x.log 2>&1 &` + 轮询完成标记，ssh 直连长构建会话易断。
- **recreate 主栈容器必须显式带 `APP_RUNTIME_IMAGE=localhost/supportportal-app:017dd2e8f515`**（`.env` 无此键，缺失会落到残留旧镜像 `:unknown`）且加 `--no-deps`（否则连带重建依赖服务）。
- split 网络必须允许出站（route 调 LLM、automation 连 RDS）；deploy 脚本对既存 internal 网络 fail-closed。
- 详细契约见 `docs/deploy_automation_release.md`。

### 1.4 硬约束（所有 Thread 一致遵守）

1. **不写真实 Zendesk 工单**：一切真实写入验收仅在 T3 获用户明确批准的工单后进行。
2. **旧端点 `/account`、`/production` 保持不动**：切流需用户单独批准（T4）。
3. 所有 tracked 修改走标准 worktree 流程（`scripts/workflow/create_task_worktree.sh` → PR → `finalize_task_to_main.sh`）；一个任务包一个 PR。
4. `docs/project/tasks/p2-88.json` 是本工作线的登记源，按第 4 节协调更新。
5. ticket 12895 当前为 `solved`——side-effect 执行会将其 reopen，选测试单时注意。

---

## 2. 已完成基线（免于重复探索）

| 成果 | PR |
|---|---|
| 三环境拆分、独立 compose project/schema/表、production 镜像物理排除 rerun | #843, #844 |
| release builder + manifest 晋级/回滚、DSN 复用、分环境 execution 表、nginx edge 接入 | #845–#849 |
| 网络出站修复（去 `--internal`）、执行 token 鉴权、production 未知写路径 404 | #850 |
| compose 显式 default 网络（podman 兼容） | #851 |
| 鉴权先于 body 校验（Depends） | #852 |
| 非 production runtime human_review 分支缺 return 的 500 修复 | #853 |
| 验收证据与 blocker 登记、blocker 解除 | #854, #856 |
| `verify_split_environments.sh` 探针、restart 预建网络、finalize 立即合并、workflow 测试债务清理 | #855 |

已线上验证：staging 带 token 端到端（`prepared` 9 秒返回并落库）；preproduction 四种路由路径与 human_review 无副作用落库；suspension prepared 链路执行到 Zendesk 调用点并以 failed+pending ledger 正确落库；三环境 rollback drill 完成。

---

## 3. 任务包

### T1 · Rerun 真实实现（P1 · 代码 · 独立可并行）

**背景：** 非 production runtime 的 `POST /v1/reruns` 目前只返回 `accepted`，不创建新 execution——与设计"Preproduction rerun 创建新 execution"存在差距。且 execution 记录未持久化原始请求字段（question/subject/ticket 等），真 rerun 无从重建输入。

**范围：**
- `backend/services/automation_execution_store.py`：execution payload 增加原始请求字段（向后兼容读取）。
- `backend/automation_runtime.py`：`/v1/reruns` 由存根改为加载原 execution → 以新 `request_id` 创建并执行新 execution，记录 `rerun_of_execution_id` 链。
- 三 UI（`ui/automation-{staging,preproduction,production}/app.js`）：staging/preproduction 增加 rerun 入口（production 无 rerun，勿加）。
- 测试：rerun 产生新 execution、原 execution 不可变、rerun 链可追溯、production OpenAPI 仍无 rerun。

**验收：** 单测全绿；staging/preproduction 各跑一次真实 rerun（允许，无 Zendesk 副作用风险：staging 无凭据；preproduction rerun 若路由 eligible 会写 Zendesk——**preproduction rerun 验证也须等 T3 批准，或用必然 human_review 的请求**）。

**冲突域：** `automation_runtime.py`、execution store、三个 UI。规模：中。

### T2 · Execution store 的 DB 级回归测试 + 历史悬案复查（P2 · 代码/测试 · 独立）

**背景：** 现有 runtime 契约测试全部用内存模式（`AUTOMATION_RUNTIME_ALLOW_MEMORY=1`），DB 模式下"执行记录必须持久化"没有直接断言。历史悬案：release-004 时代一次 preproduction 执行返回 500（human_review 分支 UnboundLocalError），按代码路径首次 `store.save` 应已落库，但事后表中无该记录且原因未证实（怀疑窗口期容器曾短暂异常，未定论）。

**范围：** 为 `AutomationExecutionStore` 增加 DB-backed 测试（可用本地 postgres 或事务回滚式 fake）：save/get/idempotent upsert/human_review 路径落库断言；复查该悬案并在结论可复现时修复对应缺陷或关闭疑点。

**验收：** 新测试覆盖 DB 模式持久化语义；悬案有明确结论（复现修复，或书面排除）。
**冲突域：** 仅新增测试文件与可能的 store 小修；与 T1 在 store 上有轻度重叠，先合并者为准、后者 rebase。

### T3 · 真实工单写入验收（P1 · 线上操作 · 阻塞：需用户提供工单）

**前置：** 用户明确批准测试工单号（或用户在 UI 自行执行）。12895 为 `solved`，执行会 reopen，需用户知情。

**范围（拿到批准后）：**
1. Preproduction：allowlist 工单跑 internal 全链路——ownership + internal comment + status=pending + delivery ledger + 服务端 Zendesk readback（outcome_unknown 时走 reconcile）。
2. Production：internal 链路同上（`comment_visibility=internal`）。
3. Production external：仅限用户指定工单。
4. 每步执行后核对 execution 表记录与 Zendesk 实际状态；更新 p2-88 evidence。

**验收：** 三段执行全部 `completed` 且 ledger 三条 operation 均 completed；p2-88 记录证据；用户确认工单侧可见结果符合预期。
**冲突域：** 线上操作 + p2-88（append evidence）；无代码。

### T4 · 旧端点切流方案（P2 · 方案先行 · 阻塞：T3 完成 + 用户批准）

**范围：** 产出切流设计（先 PR 文档评审，批准后才动实施）：n8n 转发指向新 production API（需带执行 token 的凭据管理方案）→ `/account`、`/production` 流量观察期 → 旧容器/路径/代码下线清单（旧 `automation_executions` 数据处置、`promote` 双投递风险——见 p2-73/p2-74 历史任务）。
**验收：** 设计文档合并；实施另开任务包。
**冲突域：** docs + 后续 deployment；暂无代码。

### T5 · 运维手册补全 + verify 脚本增强（P3 · 小 · 独立）

**范围：**
- `docs/deploy_automation_release.md` 补"容器 recreate 规范"（`APP_RUNTIME_IMAGE` 必填、`--no-deps`、nohup+轮询模式）与 EC2 agent 操作红线（本次两类实测事故的沉淀）。
- `deployment/verify_split_environments.sh` 增加 `--with-staging-probe` 可选项：带 token 发一次真实 staging 执行（`prepared` 断言；有 LLM 成本故默认关闭）。

**验收：** 文档评审通过；新探针项在 EC2 实测一次 PASS。
**冲突域：** docs、verify 脚本；与 T1 的 UI 无关。

### T6 · design.md 覆盖检查与 UI 迭代评估（P3 · 小 · 独立）

**范围：** 检查 `design.md` 是否覆盖三个 automation UI 的设计条目（规则：新/改 UI 必须以 design.md 为源）；缺失则补条目；评估 UI 迭代需求（如 execution 历史列表、rerun 入口展示——与 T1 联动处仅做标注不实施）。
**验收：** design.md 状态明确（已覆盖/补充合并）；迭代建议列表产出。
**冲突域：** design.md、可能少量 UI 文案。

---

## 4. 多 Thread 协调规则

1. **Worktree/分支命名：** `split-<任务包缩写>-<slug>`（如 `split-t1-rerun`、`split-t5-runbook`），一个任务包一个 PR。
2. **p2-88 更新：** 只 append evidence/history，不改他人条目；合并前 rebase 最新 `origin/main`；evidence.type 只允许 `pr/test/deployment/document/decision`。
3. **EC2 部署串行：** 单机构建，同一时间只允许一个 Thread 构建/部署 release；部署前后各跑一次 `verify_split_environments.sh` 并在 PR/报告中记录结果。production 部署带 `DEPLOY_PRODUCTION_APPROVED=1`。
4. **不越界：** 各任务包只做自己范围；发现范围外问题记录到 p2-88 history，不顺手修。
5. **状态源：** 本文件 + `p2-88.json` + `docs/deploy_automation_release.md`；冲突以最近合并的 `main` 为准。

## 5. 待用户决策清单

| 决策 | 影响任务 | 说明 |
|---|---|---|
| 批准真实写入验收的测试工单号 | T3 | 12895 将被 reopen；或指定其他受控工单 |
| Production external comment 验证的工单 | T3 | external 对客户可见，仅用户指定 |
| 旧端点切流时间与方式 | T4 | T3 全绿后启动设计评审 |
| preprod allowlist 正式化 | T3 | 当前 `12872,12895` 为临时测试单 |
