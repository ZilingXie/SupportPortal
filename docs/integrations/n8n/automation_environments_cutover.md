# n8n 切流到三环境（/automation/*）设计（T4 · 方案先行）

**日期：** 2026-08-23 　**基线：** `main`=`6fdca35`，EC2 `release-20260822-005` 　**状态：** 设计评审稿，未实施。production 切流的实施前置 = T3 真实工单写入验收完成 + 用户单独批准（见 `docs/split_environments_report.md` §3 T4、§5）。

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
| `new_case_2_supporportal_prod` | `HTTP Request`（最终投递） | `POST /production/account`，表单 `title/question/customer_email/source/customer_name`，无鉴权 | `POST /automation/production/v1/cases`，JSON body + `Authorization: Bearer`（见 §3） |
| `new_case_2_supporportal_staging` | `HTTP Request` | `POST /account`，表单，无鉴权 | `POST /automation/staging/v1/cases`，JSON body + Bearer；**不得**传 `comment_visibility` |
| （可选新增）`new_case_automation_preproduction` | 克隆自 prod 工作流 | — | `POST /automation/preproduction/v1/cases`，JSON body + Bearer，`comment_visibility=internal`，受服务端 allowlist 门控 |
| `commen_sync` | 2× membership GET + 2× PUT comments | 旧端点 `…/api/integrations/zendesk/account-cases/{id}/…`（staging + production 两栈） | **不改 URL**。仅按 §6 统一 token（可整体换用 Bearer 凭据，服务端已支持回退） |
| `case_status_sync` | 2× membership GET + 2× PUT status | 同上 | **不改 URL**。仅按 §6 统一 token |
| `2_slack - SupportPortal Account Handoff -> Slack` | 入站 Webhook（SupportPortal→n8n） | 凭据 `2_SupportPortal`（`X-N8n-Request-Token`） | 结构不动。仅按 §6 把凭据值换成统一 token |

Zendesk Trigger、取数/富化（Get_Case_Info、Get_Requester_Info、Prepare_Account_Data 等）与 Company ID 门控逻辑全部保留原样，不在本文改动范围。

## 3. `POST /v1/cases` 投递契约

契约源：`backend/services/automation_contracts.py:59-70`（`AutomationExecutionRequest`，`extra="forbid"`）。

### 3.1 请求

```text
Method: POST
URL:    https://support.stellarix.space/automation/{env}/v1/cases
Header: Authorization: Bearer <AUTOMATION_{ENV}_EXECUTION_TOKEN>
        Content-Type: application/json
```

鉴权先于请求体校验：缺失/错误 token 一律 401 `invalid automation execution token`，即使 body 也非法。

### 3.2 请求体（最终 HTTP Request 节点的 JSON 模板）

以 prod 克隆工作流为例（`$json` 来自 `Prepare_Account_Data` 输出，字段 `ticket/customer_name/customer_email`）：

```text
Body type: JSON（Specify Body: Using JSON）
JSON body:
={{
  JSON.stringify({
    request_id: 'n8n-zd-' + $json.ticket.id,
    case_id: 'AC-' + $json.ticket.id,
    subject: $json.ticket.subject,
    question: $json.ticket.description,
    customer_email: $json.customer_email,
    customer_name: $json.customer_name,
    zendesk_ticket_id: String($json.ticket.id),
    comment_visibility: 'internal'
  })
}}
Node options → timeout: 290000（nginx 侧为 300s）
```

字段对照与约束：

| 字段 | 必填性/约束 | 旧表单映射 | 说明 |
|---|---|---|---|
| `request_id` | 必填，1–160 字符 | —（新增） | 幂等键。约定 `n8n-zd-{ticket.id}`：Zendesk Trigger 对同一工单重复触发时得到 200 `idempotent_replay:true` 而不是第二次执行。若上次执行终态非 completed/prepared/human_review，重放返回 409 `execution_requires_reconcile`——此时走 `/v1/executions/{id}/reconcile` 或 UI 对账，不要简单换 ID 重发 |
| `case_id` | 必填，1–160 字符 | —（新增） | 调用方自定义。沿用 `AC-{ticket.id}` 便于跨系统检索（新环境不校验唯一性/外键） |
| `question` | 必填，1–12000 字符 | `question`（=ticket.description） | 正文。旧表单的 `source`（ticket.url）**没有对应字段，不得传**——schema `extra="forbid"`，多传任何字段直接 422 |
| `subject` | 可选，≤300 | `title`（=ticket.subject） | |
| `customer_email` | 可选，≤320 | `customer_email` | |
| `customer_name` | 可选，≤160 | `customer_name` | |
| `zendesk_ticket_id` | preproduction/production 必填，staging 可选，≤128 | —（新增，=ticket.id） | 建议三个环境都传，便于执行记录与 Zendesk 对账 |
| `comment_visibility` | `internal`/`external`，见 3.3 | —（新增） | production 必填；preproduction 只接受 internal；staging 传了即 422 |
| `ticket_context` | 可选 | — | 暂不使用 |

### 3.3 三环境差异矩阵（`automation_contracts.py:89-162`）

| | staging | preproduction | production |
|---|---|---|---|
| Zendesk 写入 | 否（容器无凭据，`writes_zendesk=False`） | 是，强制 `internal`（请求 external → 422） | 是，**必须显式** `comment_visibility`（缺失 → 422） |
| `zendesk_ticket_id` | 可选 | 必填 + 在 `PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST`（当前 `12872,12895`）内，否则 422 | 必填 |
| side effects | 无 | ownership → internal comment → status→`pending`（开关已启用） | 同左，visibility 按请求 |

n8n 建议：prod 克隆默认 `comment_visibility: 'internal'`（与 preprod 同策略，客户不可见）；external 必须按单显式选择，不要做成工作流默认值。

### 3.4 响应处理

- 成功：`200 {"status": "completed|prepared|human_review", "environment": …, "execution": {…}}`。`prepared/human_review` 也是成功终态，不需要重试。
- 幂等重放：同 `request_id` 且终态为上述三者 → 200 带 `idempotent_replay:true`。
- 401/422/409：按上文语义处理；n8n 节点可关掉自动重试（`Retry On Fail` 关），避免对 422 反复重发。

## 4. production 灰度分流设计（已选方案）

原则：**不动现有工作流逻辑，用克隆工作流 + 互斥公司名单切流**；同一工单同一时刻只允许一条链路（见 §7）。

1. 在 n8n 复制 `new_case_2_supporportal_prod` 为 `new_case_automation_prod`：全部逻辑保留，仅改三处——
   - `Check_Company_ID1` 的 `TARGET_COMPANY_IDS` = **迁移名单**（初始建议 1 个低风险公司，如自有测试公司）；
   - 最终 `HTTP Request` 节点按 §3.1–3.2 改 URL/body/鉴权（`comment_visibility: 'internal'`）；
   - 挂上统一的 Bearer 凭据（§6）。
2. 把 `new_case_2_supporportal_prod` 的 `TARGET_COMPANY_IDS` 收缩为**未迁移名单**（从其中删除迁移的公司 ID）。两个名单必须互斥。
3. 激活克隆工作流。此后：迁移公司的新单 → 新环境（execution 记录 + Zendesk 副作用）；其余公司 → 旧 `/production/account` 照旧。
4. 扩大灰度 = 继续单向搬公司 ID；全量后停用旧工作流（观察期要求见 §9）。

两个 Zendesk Trigger 同时活跃会对每张新单各跑一遍取数富化（只多几次 Zendesk 读，无副作用）；只有名单命中的那条链路会发投递请求。

**preproduction（可选，用于 T3 演练）**：再克隆一份指向 `/automation/preproduction/v1/cases`、`comment_visibility: 'internal'`。服务端 allowlist 是权威门控；若不想每张非名单单产生 422 执行噪音，在克隆里加一个 `ticket.id in (12872,12895)` 的 IF 门。

**staging**：`new_case_2_supporportal_staging` 直接把最终节点改为 `/automation/staging/v1/cases`（测试数据环境，可先行，无 Zendesk 副作用）；若仍需旧 `/account` 的 account-case 测试链路，则同样用克隆方式并存。

**回滚（单公司粒度）**：把公司 ID 挪回旧工作流名单即可，克隆工作流无需停用（名单空则自然无流量）。

## 5. 不迁移的工作流与预期行为

- `commen_sync` / `case_status_sync`：无新环境等价端点，保持打旧端点。新环境工单在两个旧栈的 membership check 都 miss（`is_account_case=false` / 404）→ 工作流按既有逻辑跳过，这是预期；旧链路工单照常同步。
- Slack handoff（`2_slack …`）：由旧 production 栈 worker 驱动；工单切到新环境后不再产生 handoff 事件（新环境没有该功能）。灰度期间旧链路工单不受影响。

## 6. Token 统一（纯配置，零代码）

现状 5 个互不相关的 secret：`AUTOMATION_{STAGING,PREPRODUCTION,PRODUCTION}_EXECUTION_TOKEN`（新环境 Bearer）、`ZENDESK_ACCOUNT_SYNC_TOKEN`（旧同步三端点）、`n8n_request_token`（SupportPortal→n8n 出站）。方案：**同一个强随机值贯穿 5 个变量**；n8n 侧一个 Bearer 凭据覆盖全部入向调用——旧同步端点在无 `X-Zendesk-Account-Sync-Token` 头时**回退接受 `Authorization: Bearer`**（`backend/main.py:1956-1967`），新环境本来就只收 Bearer。

### 6.1 EC2（`zacbot:~/SupportPortal/.env`）

```bash
openssl rand -hex 32   # 生成一个新值 <TOKEN>，替换下列 5 个变量
AUTOMATION_STAGING_EXECUTION_TOKEN=<TOKEN>
AUTOMATION_PREPRODUCTION_EXECUTION_TOKEN=<TOKEN>
AUTOMATION_PRODUCTION_EXECUTION_TOKEN=<TOKEN>
ZENDESK_ACCOUNT_SYNC_TOKEN=<TOKEN>
n8n_request_token=<TOKEN>
```

重启范围（先做 automation 三个——当前无消费者，随时安全；其余三组在同一维护窗口内完成，切换期间旧同步工作流会有短暂 401 窗口，Zendesk webhook 会重试）：

- 三个 automation 容器：`./deployment/deploy_ec2.sh --release release-20260822-005 --environment {staging,preproduction,production}` 重新 apply（幂等，镜像不变、仅 env 变更触发 recreate）；
- `api`、`api_production`（`ZENDESK_ACCOUNT_SYNC_TOKEN`）：按主栈 recreate 规范，**必须显式带 `APP_RUNTIME_IMAGE=localhost/supportportal-app:017dd2e8f515` 且 `--no-deps`**（见 `docs/split_environments_report.md` §1.3 教训）；
- production worker 容器（`n8n_request_token`）：同窗口 recreate。

### 6.2 n8n

1. 新建 Header Auth 凭据 `SupportPortal Bearer`：Name=`Authorization`，Value=`Bearer <TOKEN>`。此凭据可用于：三个 `/automation/*/v1/cases` 投递节点 + `commen_sync`/`case_status_sync` 的全部 8 个调用节点（4× membership GET + 4× PUT，替代原 `X-Zendesk-Account-Sync-Token` 内联值）。
2. 更新凭据 `2_SupportPortal` 的值为 `<TOKEN>`（头仍是 `X-N8n-Request-Token`，方向是 SupportPortal→n8n，保留原头名）。
3. 逐节点把内联 token 字符串替换为上述凭据，消除散落的明文（`commen_sync` 4 处、`case_status_sync` 4 处）。

### 6.3 验证与回滚

- 统一后重跑 `./deployment/verify_split_environments.sh`（36 项，含三环境鉴权探针）。
- 抽查一个同步端点：无 token → 401；`Authorization: Bearer <TOKEN>` → 200/404（membership miss 也算鉴权通过）。
- Slack handoff：发一条 synthetic 测试事件（参照 `account_automation_slack_notification.md` 的样例 payload）确认 200 delivered。
- 回滚：`.env` 恢复旧值 → 按 6.1 同范围 recreate → n8n 凭据恢复旧值。

代价说明：单一值意味着该值泄露即全线有效（含 production）；换取的是轮换只需改 5 个变量 + 2 个 n8n 凭据。若未来要按环境隔离，改回各环境独立值即可，无需代码变更。

## 7. 双写防护红线

1. 旧 prod 工作流的公司名单与新 prod 克隆的名单**必须互斥**（§4 步骤 2）；否则同一工单会被两条链路各写一次 Zendesk（ownership/comment/status 双份）。
2. preproduction allowlist 工单（12872/12895）不得同时命中旧 prod 工作流的公司名单：启用 preprod 克隆前，先核对这些工单所属 org 的 `companyid` 是否在旧名单（当前 `1201099,200062458,1392055,1228534`）中；命中则先从旧名单移除或暂停旧工作流。
3. production 切流实施前置：T3 真实工单写入验收完成 + 用户批准。在此之前只允许 staging（无副作用）与 preproduction（internal + allowlist）接线。

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
