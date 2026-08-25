# n8n 切流到三环境（/automation/*）设计（T4 · 方案先行）

**日期：** 2026-08-23 　**基线：** `main`=`6fdca35`，EC2 `release-20260822-005` 　**状态：** 已被 ECS 三环境迁移方案取代。自 2026-08-25 起，EC2 上的 `/automation/staging`、`/automation/preproduction`、`/automation/production` 返回 410；本文中的 EC2 endpoint、发布和切流命令只保留为历史设计证据，不得继续执行。新的入口与 n8n publish 只以 ECS `p1-53` 实施结果为准；现有 EC2 `/production` 保持独立运行。

本文回答两个问题：n8n 工作流切到新三环境时**最终发送端点怎么改、端点是否已存在**；以及 n8n→SupportPortal 各调用如何**统一用同一个 token 验证**。

---

## 1. 结论：端点是否已存在

| 能力 | 新环境端点 | 状态 |
|---|---|---|
| 新工单投递（触发自动化执行） | `POST https://support.stellarix.space/automation/{staging\|preproduction\|production}/v1/cases` | **已存在**，nginx 已路由（`deployment/nginx/supportportal.conf:133-179`，300s 超时），release-005 已部署，36/36 探针绿，staging 端到端实测通过 |
| Zendesk 评论快照同步 | 无 | **不存在**。三环境 runtime 只有 `/health`、`/v1/capabilities`、`/v1/cases`、`/v1/reruns`（仅 staging/preprod）、`/v1/reset`（仅 staging）、`/v1/executions/{id}/reconcile`（`backend/automation_runtime.py`、`backend/automation_production_runtime.py`） |
| Zendesk 工单状态同步 | 无 | **不存在**（同上） |
| account case 创建（旧五字段 intake 的完整语义） | 无 | **不存在**。`/v1/cases` 只做「分类 → action-plan → Zendesk 副作用 → 执行记录」，不创建 support ticket/account case、不发内部邮件、不进旧 account-case UI 与 human review 收件箱 |

因此：**只有两个 new_case 工作流的最后一个 HTTP Request 节点需要改端点**；`commen_sync` 与 `case_status_sync` 保持旧端点不动；切到新环境的工单会被评论/状态同步工作流的 membership check 自然 miss 跳过（预期行为，不是故障）。

语义差异须知：工单切到新环境后，它只存在于 Zendesk（事实源）+ 该环境 `automation_executions_{env}` 执行表；不再出现在旧 `/account`、`/production` UI，不再有评论快照投影、状态同步闭环、human review 内部邮件和 Slack handoff。灰度期间旧链路工单不受影响。

## 2. 端点对照表（按工作流）

| n8n 工作流 | 节点 | 现在 | 切流后 |
|---|---|---|---|
| `new_case_2_supporportal_prod` | `HTTP Request`（最终投递） | `POST /production/account`，表单 `title/question/customer_email/source/customer_name`，无鉴权 | `POST /automation/production/v1/cases`，**body 原样不用改**（旧五字段表单直接可发，见 §3.2），加 `X-N8n-Request-Token` 头即可（p2-109 起 production 废除即时 comment 副作用，`comment_visibility` 不再必填，可不传） |
| `new_case_2_supporportal_staging` | `HTTP Request` | `POST /account`，表单，无鉴权 | `POST /automation/staging/v1/cases`，**body 原样不用改**，加 `X-N8n-Request-Token` 头；**不得**传 `comment_visibility` |
| （可选新增）`new_case_automation_preproduction` | 克隆自 prod 工作流 | — | `POST /automation/preproduction/v1/cases`，body 原样 + 头，受服务端 allowlist 门控 |
| `commen_sync` | 2× membership GET + 2× PUT comments | 旧端点 `…/api/integrations/zendesk/account-cases/{id}/…`（staging + production 两栈） | 灰度迁移期可将 production origin 指向 `/automation/production/api/integrations/zendesk/account-cases/{id}/…`（p2-110 起同构可用：membership GET + PUT comments + 评论触发链）；未迁移工单保持旧端点。鉴权头按 §6 统一为 `X-N8n-Request-Token` |
| `case_status_sync` | 2× membership GET + 2× PUT status | 同上 | 灰度迁移期可将 production origin 指向 `/automation/production/api/integrations/zendesk/account-cases/{id}/status`（p2-112 起同构可用：状态投影 + solved/closed 关 case/Engineer Case）；未迁移工单保持旧端点。鉴权头按 §6 统一为 `X-N8n-Request-Token` |
| `2_slack - SupportPortal Account Handoff -> Slack` | 入站 Webhook（SupportPortal→n8n） | 凭据 `2_SupportPortal`（`X-N8n-Request-Token`） | 结构不动。仅按 §6 把凭据值换成统一 token |
| Slack App Mention/Interaction → Engineer（入向） | POST SupportPortal 端点 | 旧栈 `/api/integrations/slack/engineer-cases/messages\|actions` | 灰度迁移期可指向 `/automation/production/api/integrations/slack/engineer-cases/...`（p2-113 起同构可用：messages/actions/thread-bindings/resolve，幂等与 guardrail/final_approve 语义一致）；出站直发 Slack（#918）无需 n8n 改动 |

Zendesk Trigger、取数/富化（Get_Case_Info、Get_Requester_Info、Prepare_Account_Data 等）与 Company ID 门控逻辑全部保留原样，不在本文改动范围。

## 3. `POST /v1/cases` 投递契约

契约源：`backend/services/automation_contracts.py:59-70`（`AutomationExecutionRequest`，`extra="forbid"`）。

### 3.1 请求

```text
Method: POST
URL:    https://support.stellarix.space/automation/{env}/v1/cases
Header: X-N8n-Request-Token: <n8n_request_token 的值>
        Content-Type: application/json
```

鉴权先于请求体校验：缺失/错误 token 一律 401 `invalid automation execution token`，即使 body 也非法。

### 3.2 请求体（p2-94 兼容层：旧五字段 body 原样可发）

**n8n 侧不需要改 body。** `/v1/cases` 同时接受旧 `/account` 的五字段投递（表单编码或同字段名 JSON）：`title`、`question`、`customer_email`、`source`、`customer_name`。服务端兼容层（`backend/services/automation_intake_compat.py`）自动完成旧 intake 内部同款的推导：

- `title` → 映射为 `subject`；
- `source`（Zendesk 工单 URL）→ 解析出 `zendesk_ticket_id`（复用旧 intake 的 host+路径正则语义：`…zendesk.com/agent/tickets/{id}` 与 `/api/v2/tickets/{id}.json`）；
- `request_id` 缺省 → `n8n-zd-{ticket_id}`（确定性幂等键：同一工单重复触发返回 200 `idempotent_replay`，不会二次执行）；无法从 source 解析时生成一次性 id（与旧 intake 无 source 时跳过去重的行为一致）；
- `case_id` 缺省 → `AC-{ticket_id}`（与旧 intake 的 account case 编号约定一致）。

表单字段只是**增量可选**：直接传新契约字段（`request_id`/`case_id`/`zendesk_ticket_id`/`comment_visibility`）时优先采用调用方值。除 `title`/`source` 两个被消费的旧字段外，其余未知字段仍然 422（`extra="forbid"` 的防呆保留——字段名拼错会立刻暴露而不是被静默忽略）。

**p2-109 起 production 不再要求 `comment_visibility`**：intake 改为旧栈 /production 语义（分类 → 内部邮件/追问 reply job → 延迟 public 回复），没有即时 Zendesk comment，该字段可选且仅作记录。staging 传了它反而 422；preproduction 只接受 internal（这两个环境契约不变）。

原生 JSON 契约（显式传全字段）同样继续受支持，适合 UI 或脚本调用：

```json
{
  "request_id": "n8n-zd-12999",
  "case_id": "AC-12999",
  "subject": "…",
  "question": "…",
  "customer_email": "…",
  "customer_name": "…",
  "zendesk_ticket_id": "12999",
  "comment_visibility": "internal"
}
```

原生契约字段约束（兼容层映射后的最终校验）：`request_id`/`case_id` 1–160 字符；`question` 必填 1–12000；`subject` ≤300；`customer_email` ≤320；`customer_name` ≤160；`zendesk_ticket_id` ≤128；`ticket_context` 暂不使用。幂等重放语义：同 `request_id` 且终态 completed/prepared/human_review → 200 `idempotent_replay:true`；其他终态 → 409 `execution_requires_reconcile`（走 reconcile 端点或 UI 对账，不要换 ID 重发）。节点超时建议 290000（nginx 侧 300s）。

### 3.3 三环境差异矩阵（`automation_contracts.py:89-162`）

| | staging | preproduction | production |
|---|---|---|---|
| Zendesk 写入 | 否（容器无凭据，`writes_zendesk=False`） | 是，强制 `internal`（请求 external → 422） | 是（经 parity 管线：ownership gate + 延迟 public 回复；`comment_visibility` 可选） |
| `zendesk_ticket_id` | 可选 | 必填 + 在 `PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST` 内（`*` 放行全部；空拒绝全部），否则 422 | 必填 |
| side effects | 无 | ownership → internal comment → status→`pending`（开关已启用） | 同左，visibility 按请求 |

n8n 建议：prod 克隆无需加 `comment_visibility`；客户可见回复由 parity worker 延迟发布（public+solved）。

### 3.4 响应处理

- 成功：`200 {"status": "completed|prepared|human_review", "environment": …, "execution": {…}}`。`prepared/human_review` 也是成功终态，不需要重试。
- 幂等重放：同 `request_id` 且终态为上述三者 → 200 带 `idempotent_replay:true`。
- 401/422/409：按上文语义处理；n8n 节点可关掉自动重试（`Retry On Fail` 关），避免对 422 反复重发。

## 4. production 灰度分流设计（已选方案）

原则：**不动现有工作流逻辑，用克隆工作流 + 互斥公司名单切流**；同一工单同一时刻只允许一条链路（见 §7）。

1. 在 n8n 复制 `new_case_2_supporportal_prod` 为 `new_case_automation_prod`：全部逻辑保留，body 五字段原样不动，仅改三处——
   - `Check_Company_ID1` 的 `TARGET_COMPANY_IDS` = **迁移名单**（初始建议 1 个低风险公司，如自有测试公司）；
   - 最终 `HTTP Request` 节点改 URL（§3.1）；body 五字段原样不动（`comment_visibility` 已非必填）；
   - 挂上统一的 `X-N8n-Request-Token` 凭据（§6）。
2. 把 `new_case_2_supporportal_prod` 的 `TARGET_COMPANY_IDS` 收缩为**未迁移名单**（从其中删除迁移的公司 ID）。两个名单必须互斥。
3. 激活克隆工作流。此后：迁移公司的新单 → 新环境（execution 记录 + Zendesk 副作用）；其余公司 → 旧 `/production/account` 照旧。
4. 扩大灰度 = 继续单向搬公司 ID；全量后停用旧工作流（观察期要求见 §9）。

两个 Zendesk Trigger 同时活跃会对每张新单各跑一遍取数富化（只多几次 Zendesk 读，无副作用）；只有名单命中的那条链路会发投递请求。

**preproduction（可选，用于 T3 演练）**：再克隆一份指向 `/automation/preproduction/v1/cases`、`comment_visibility: 'internal'`。服务端 allowlist 是权威门控（`.env` 的 `PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST`：逗号分隔工单号；`*` = 放行全部，工单过滤交给 n8n 工作流；留空 = 拒绝全部）；不想配名单时设 `*` 并在 n8n 侧加 IF 门。

**staging**：`new_case_2_supporportal_staging` 直接把最终节点改为 `/automation/staging/v1/cases`（测试数据环境，可先行，无 Zendesk 副作用）；若仍需旧 `/account` 的 account-case 测试链路，则同样用克隆方式并存。

**回滚（单公司粒度）**：把公司 ID 挪回旧工作流名单即可，克隆工作流无需停用（名单空则自然无流量）。

## 5. 不迁移的工作流与预期行为

- `commen_sync` / `case_status_sync`：无新环境等价端点，保持打旧端点。新环境工单在两个旧栈的 membership check 都 miss（`is_account_case=false` / 404）→ 工作流按既有逻辑跳过，这是预期；旧链路工单照常同步。
- Slack handoff（`2_slack …`）：由旧 production 栈 worker 驱动；工单切到新环境后不再产生 handoff 事件（新环境没有该功能）。灰度期间旧链路工单不受影响。

## 6. Token 统一（X-N8n-Request-Token 单一机制）

**最终方案（p2-91 实施，替代本文件早期"同值贯穿 5 变量 + Bearer 凭据"的设计）：** 服务端所有 n8n 入向端点**只接受 `X-N8n-Request-Token` 头**，值只来自单一环境变量 `n8n_request_token`（`backend/main.py` 的 `require_n8n_request_token`、两个 automation runtime 的 `_require_execution_token`，均为 `hmac.compare_digest` 比较；未配置 503，缺失/错误 401）。旧的 `X-Zendesk-Account-Sync-Token` 头、`Authorization: Bearer` 回退、`ZENDESK_ACCOUNT_SYNC_TOKEN` 与三个 `AUTOMATION_*_EXECUTION_TOKEN` 变量全部**不再接受/不再使用**。出向（SupportPortal→n8n Slack handoff）本就使用同名头、同值变量，无需改动。

服务端代码已随 p2-91 合并；**生效需要重新构建 release 并部署到 EC2**（见 6.1），n8n 侧节点在同一切换窗口内改头。

### 6.1 EC2（`zacbot:~/SupportPortal/.env`）

- `n8n_request_token` 已配置（这就是唯一 token，无需生成新值；如需轮换，改这一个变量）。
- 可删除现已无消费者的旧变量：`ZENDESK_ACCOUNT_SYNC_TOKEN`、`AUTOMATION_STAGING_EXECUTION_TOKEN`、`AUTOMATION_PREPRODUCTION_EXECUTION_TOKEN`、`AUTOMATION_PRODUCTION_EXECUTION_TOKEN`（compose 不再引用；deploy 校验改为要求 `n8n_request_token` 必填）。
- 部署/重启顺序（切换窗口内完成；旧同步工作流在新头改完前会 401，Zendesk webhook 会重试）：
  1. 构建新 release 并按 staging → preproduction → production 部署（`build_automation_release.sh` + `deploy_ec2.sh --release <id>`，automation 容器随新镜像带上新鉴权代码与 `n8n_request_token` 注入）；
  2. 主栈 `api`、`api_production`（同步三端点新鉴权）与 production worker：按主栈 recreate 规范，**必须显式带 `APP_RUNTIME_IMAGE=…` 且 `--no-deps`**（见 `docs/split_environments_report.md` §1.3 教训）；
  3. 立即执行 6.2 的 n8n 侧修改。

### 6.2 n8n

1. 新建一个 Header Auth 凭据（Name=`X-N8n-Request-Token`，Value=`n8n_request_token` 的值），或直接复用现有 `2_SupportPortal` 凭据（同头同名同值）。
2. 三个 `/automation/*/v1/cases` 投递节点、`commen_sync`/`case_status_sync` 的全部 8 个调用节点（4× membership GET + 4× PUT）统一挂该凭据；删除原 `X-Zendesk-Account-Sync-Token` 内联值（`commen_sync` 4 处、`case_status_sync` 4 处）。
3. 三个 automation 控制台 UI（`/automation/*`）的 Execution token 输入框改发 `X-N8n-Request-Token` 头（p2-91 已随代码更新，输入的值即 `n8n_request_token`；localStorage 旧键继续可用）。

### 6.3 验证与回滚

- 部署后重跑 `./deployment/verify_split_environments.sh`（401 负例探针已改为 `X-N8n-Request-Token: wrong-token`）。
- 抽查一个同步端点：无 token → 401；`X-N8n-Request-Token: <TOKEN>` → 200/404（membership miss 也算鉴权通过）；旧头 `X-Zendesk-Account-Sync-Token: <旧值>` → **401**（验证旧路径确实关闭）。
- Slack handoff：发一条 synthetic 测试事件（参照 `account_automation_slack_notification.md` 的样例 payload）确认 200 delivered。
- 回滚：git revert p2-91 的合并提交 → 重新构建部署 → n8n 节点换回旧头/旧值（或整体回退 release：`deploy_ec2.sh --environment <env> --rollback`）。

代价说明：单值意味着该值泄露即全线有效（含 production）；轮换只需改 `n8n_request_token` 一个变量 + 一个 n8n 凭据 + 重新部署/recreate。

## 7. 双写防护红线

1. 旧 prod 工作流的公司名单与新 prod 克隆的名单**必须互斥**（§4 步骤 2）；否则同一工单会被两条链路各写一次 Zendesk（ownership/comment/status 双份）。
2. preproduction allowlist（`$ENV{PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST`}：逗号分隔工单号；`*` = 放行全部、由上游 n8n 过滤；空 = 拒绝全部）——若使用工单号名单，名单内工单不得同时命中旧 prod 工作流的公司名单；命中则先从旧名单移除或暂停旧工作流。
3. production 切流实施前置：T3 真实工单写入验收完成 + 用户批准。在此之前只允许 staging（无副作用）与 preproduction（internal；allowlist 按上述三种形态配置）接线。

## 8. 切流后验证清单（用户执行时）

1. staging：新单触发 → `automation_executions_staging` 新记录，`prepared` 秒级返回，无任何 Zendesk 写入。
2. preproduction：allowlist 单 → execution `completed`，Zendesk 侧 ownership + internal comment + status=pending，ledger 三条 operation 均 completed。
3. prod 灰度公司新单 → 新环境 execution `completed` + Zendesk readback；旧 `/production/account` 不再出现该工单的 account case。
4. 旧链路公司新单 → 旧 intake 照常；`commen_sync`/`case_status_sync` 对其照常工作，对新环境工单 membership miss 跳过。
5. `verify_split_environments.sh` 36/36；Slack handoff synthetic 事件 delivered。

## 9. 观察期与下线清单（T4 后续实施，本文不执行）

- 观察指标：旧 `/account`、`/production/account` 新工单量趋零；同步工作流 membership miss 比例；新环境各 execution 成功率与 failed+pending ledger 数。
- 全量切换并稳定后：移除 nginx 旧 location（`/account`、`/production/*`）与旧容器；处置旧 `automation_executions` 数据；复查 promote 双投递残留风险（p2-73/p2-74 历史）。
- 旧 `POST /account`、`/production/account` 目前**完全无鉴权**（`backend/main.py:9636` 无任何 token/CSRF/IP 校验）且公网可达：下线前可选加固（复用统一 Bearer，需同步给 `/account` UI 浏览器直投路径配 token）——若做，另开任务。
