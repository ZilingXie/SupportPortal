# Production 工单回归测试 Runbook（/automation/test）

目的：大改动（模型/提示词/路由/副作用管线/部署）上线或合并后，用**真实 Zendesk 工单**验证 production 自动化闭环未被破坏。覆盖四个 active 自动化分类：

| 分类 | taxonomy | 自动化 handler |
| --- | --- | --- |
| fraud_account | account_billing / fraud_account | account_verification（内部四组信息审核） |
| enablement | backend_operation / enablement | enablement |
| account_suspension | account_billing / account_suspension | account_suspension（production 一段式 direct handoff；staging/ECS 入口仍为两阶段确认） |
| detailed_invoice | account_billing / detailed_invoice | billing（内部邮件回复 + PDF 附件转发 Zendesk） |

> 范围说明：当前仅针对 `/production`（旧双栈，n8n → `POST /production/account`）。`/automation/{staging,preproduction,production}` 三环境上线后，再按本 runbook 派生对应变体。

---

## 1. 链路与工具

```
/automation/test 页面（api_production 服务）
  → Microsoft Graph 用【专用测试邮箱】发信到 support@agoraio.zendesk.com
  → Zendesk 建单（requester=测试邮箱，主题带 [zac test] 前缀）
  → n8n new_case_2_supporportal_prod 工作流五字段投递 POST /production/account
  → 分层路由（LLM account-layered-router + 确定性快路径）
  → 内部交接邮件 / 客户回复 job（6-10 分钟随机延迟）
  → worker 发布公开评论 → Zendesk 状态/指派/Slack 副作用
```

- 页面入口：`https://<host>/automation/test/`（nginx 指向 api_production；本地官方栈同路径）。
- 登录：复用 `/account` 的 workspace admin 账号（`POST /production/api/workspace/auth/login`）。
- 追踪表：`supportportal.automation_test_tickets`（production 库），页面刷新按钮自动按 主题+发送时间窗 关联 `support_account_cases`（processing_profile=production），并快照路由/自动化/内部邮件/回复 job/Zendesk 状态。

## 2. 前置检查（每次回归前）

1. **测试邮箱凭据**（首次）：由 `AUTOMATION_TEST_MAIL_TRANSPORT` 选择通道（EC2 与本地 `.env` 保持一致）：
   - `smtp`（当前采用，163 专用邮箱 `xieziling97@163.com`）：**复用既有 `BILLING_AUTOMATION_SMTP_HOST/PORT/USERNAME/PASSWORD`**（值已在 `.env`：`smtp.163.com:465` + 163 授权码；这些键自 billing 迁移 Graph 后无其他消费者），只需额外设 `AUTOMATION_TEST_MAIL_TRANSPORT=smtp`。换 QQ 邮箱时改这组值为 `smtp.qq.com` + QQ 授权码即可。
   - `graph`（Microsoft 365 邮箱，备选）：配置 `AUTOMATION_TEST_MAIL_TENANT_ID/CLIENT_ID/CLIENT_SECRET/USERNAME`，refresh token 放 `AUTOMATION_TEST_MAIL_TOKEN_CACHE`（默认 `.msgraph/automation-test-token.json`）。
   未配置时页面顶部会显示缺失键名，创建按钮禁用（fail-closed，不会发信）。
2. **production 副作用开关**：EC2 `.env` 中 `PRODUCTION_ZENDESK_SIDE_EFFECTS_ENABLED=1`、`PRODUCTION_TARGET_TICKET_STATUS` 与 `ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID` 保持既有配置。
3. **不要手动改测试工单的 assignee**：ownership 机制在人工接管后会停止自动回复（约 90 秒后 AI 接管）。
4. 页面能列出三类模板，顶部横幅显示发件人/收件人/`[zac test]` 主题标签。

## 3. 步骤 0：基线探测（首次或换邮箱后必做）

用 **enablement** 模板原样发送一封（确定性路由，不依赖 LLM）：

1. 页面选 Enablement → 不改内容 → Create test ticket → 确认弹窗。
2. 1-2 分钟后点该行 Refresh：预期 `link_status=linked`，出现 Zendesk 工单链接，Pipeline 列出现 `route: enablement`（绿）。
3. 若 5 分钟后仍 `not_found`：依次排查
   - 测试邮箱发件箱里邮件是否发出（Graph sendMail 失败会记录在 send_error）；
   - Zendesk 中该 `[zac test]` 工单是否创建、requester 是否为测试邮箱；
   - n8n 工作流是否把工单投递到 `/production/account`（Company-ID/表单门控可能过滤掉新 requester——此时换一个更接近真实客户的邮箱或调整 n8n 过滤）；
   - production 库 `support_account_cases` 是否新增行（`processing_profile='production'`）。
4. 关键验证点：主题 `subject` 是否与页面一致（关联按标题精确匹配，**手动改主题会导致无法关联**）。

基线通过后，三类回归按第 4 节执行。

## 4. 分类回归

通用操作：选分类卡片 → （可选）编辑主题/正文 → Create。创建后等待并在追踪表点 Refresh 观察信号。回复有 **6-10 分钟随机延迟**；AI 接管约 **90 秒**；内部邮件投递即时、内部回复轮询 **300 秒**。

### 4.1 fraud_account

**模板要点**：主题 "Account blocked for suspicious activity - please review"，正文含四组信息（Company/Contact/Use Case/Payment，齐全版）。

| 时刻 | 预期信号 |
| --- | --- |
| 即时 | 关联成功；`route: fraud_account`（绿）；automation: automation |
| 1 分钟内 | internal email: sent（`[Fraud Account Review] - Ticket {id}` 主题，收件人=审核邮箱） |
| ~90 秒 | Zendesk 工单 assignee → 审核人（ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID） |
| 6-10 分钟 | reply: published；公开评论含 "The relevant team will contact you within 24 hours." 精确句；Slack「Fraud Account」通知 |
| 终态 | 工单 **不 solved**，停在 pending 等人工；无 close |

**变体（追问路径）**：删掉正文里任意一组信息（如 Payment Information 整段）再发送 → 预期第一封回复是补信息追问、**不含** 24 小时承诺句、`internal_email_send_status` 保持未 sent（等待补齐后复审）；再从测试邮箱向工单回复补齐信息 → handoff 正常走完。追问最多一次。

### 4.2 enablement

**模板要点**："Please enable Media Relay from your end" + 32 位 App ID；正文避免 how/sdk/api/configure/integrate/error/why/cost 等词（否则掉出确定性快路径，交给 LLM）。

| 时刻 | 预期信号 |
| --- | --- |
| 即时 | `route: enablement`（绿，确定性路径 router_source=account_layered_hybrid） |
| 1 分钟内 | internal email: sent（`[Enablement Request] Media Relay - Ticket {id}`） |
| 6-10 分钟 | reply: published；submission 确认（"up to 24 hours" activation SLA）；**无 Slack** |

**深度闭环（手动）**：从内部交接邮件的收件邮箱直接回复该邮件，正文写 "Media Relay is enabled for this app."（中文「已开通」也可）→ 300 秒内 worker 轮询识别完成 → completion 回复发布 + Zendesk solved + 本地关单（`enablement_internal_resolution_received` 事件，classification source=regex|llm）。

### 4.3 account_suspension

**模板要点**：非欺诈封禁（balance ran out），单一诉求，不带退款等附加意图（附加意图会 human_review）；请求必须带有效客户邮箱（缺失/非法如 `a@b` → `suspension_missing_customer_email` 掉人工）。

**一段式（p2-140，production 新单）**：

| 时刻 | 预期信号 |
| --- | --- |
| 即时 | `route: account_suspension`（绿）；内部 handoff 邮件发出（联系邮箱=工单邮箱）；workflow state=handoff_pending→closing_reply_pending（intake_mode=direct_handoff, confirmed_email_source=ticket_email）；此时尚无 reply job |
| 邮件成功后 | 唯一 reply job（intent=account_suspension_handoff_and_close，不再问邮箱）；workflow 记录 closing_reply_job_id |
| 6-10 分钟 | reply: published；首封公开回复"已收到请求 + 24h 内相关团队联系"（问候 "Hi {名},"；不提关单/重开）→ assign 复审人（suhrid）+ automation_status=human_review_required，**不关单** |
| 客户后续回复 | no-op（由复审人人工处理；不再触发自动回复/邮件） |

**失败分支（可断言）**：内部邮件发送失败或 outcome_unknown、reply job 创建失败、客户邮箱缺失/非法 → workflow 与 case 均 human_review_required，无客户面输出。

**存量兼容（旧两阶段工单）**：已处于 awaiting_contact_confirmation 的历史工单，客户回复仍走原确认→内部邮件→closing 链路收尾；rerun/reroute 不再产出"问邮箱"回复（一律恢复 handoff intent）。

### 4.4 detailed_invoice

**模板要点**：主题/正文为明确的 detailed invoice 请求，且带齐三个字段（Issue date / Transaction ID / Amount）；缺字段会先走补信息追问，带争议/退款措辞会 human_review。

| 时刻 | 预期信号 |
| --- | --- |
| 即时 | `route: detailed_invoice`（绿）；automation: automation |
| 1 分钟内 | internal email: sent（`[Billing Request] Detailed invoice request - Ticket {id}`） |
| 6-10 分钟 | reply: published；submission 确认（内部审核中） |

**深度闭环（手动，回复内部邮件并附 PDF）**：从内部邮件收件邮箱直接回复该邮件，正文写 "The detailed invoice is attached." 并**附上 PDF 附件** → 300 秒内 worker 轮询处理 → PDF 存为 portal 资产 → completion 回复（`detailed_invoice_completed_and_close`）发布为 Zendesk 公开评论且**评论带该 PDF 附件**（Zendesk uploads）→ Zendesk solved + 本地关单。断言：`billing_internal_resolution_submitted` 事件、`support_account_zendesk_comment_deliveries.status=delivered`、消息 meta 带 attachments。不带 PDF 直接回复也走完成闭环（回复文本不提附件）。

## 4.5 自动化剧本回归（网页发起 + CLI 备用）

五条剧本（§4 的完整流程化，真链路：客户回合经 163 SMTP 发信、用 IMAP 读 Zendesk 通知邮件的线程头续接同一工单；断言只看结构化状态——reply intent / 内部邮件状态 / suspension 状态机 / Zendesk 状态，不比对文案）：

| 剧本 | 覆盖 |
| --- | --- |
| `E1` | enablement 顺路：带 AppID 建单 → submission 确认 → **手动批准** → completion + solved |
| `E2` | enablement 缺 AppID：追问 → 客户问什么是 AppID → **RAG 兜底回答** → 给 AppID → 内部邮件 → **手动批准** → completion + solved |
| `F1` | fraud：无信息建单 → 追问 → 补四组信息 → 内部邮件 + 24h 回复 + assign 复审人 + **不 solved** |
| `S1` | suspension：建单（有效邮箱）→ 内部邮件 → 首封"已收到+24h" → assign 复审人 + human_review_required + 不关单（p2-140 一段式） |
| `D1` | detailed invoice：带全三字段建单 → 内部邮件 → submission 确认 → **手动回内部邮件并附 PDF** → completion + Zendesk 公开评论带附件 delivered + solved |

**网页发起（推荐）**：`/automation/test/` 页面第 4 节「Scenario runs」——点剧本卡上的 Run scenario（同一时刻只允许一个进行中 run），页面实时展示逐步 PASS/FAIL、当前等待点与 Zendesk 工单链接，15 秒自动刷新；run 记录持久化在 `automation_test_scenario_runs` 表（含容器重启后 `interrupted` 标记与失败原因）。运行前服务端自动做 DB/SMTP/IMAP 连通检查，不通过则不建 run（502 显示原因）。

**enablement 内部批准保留人工**：run 进入 `waiting for approval` 时页面顶部出现黄色 MANUAL APPROVAL 横幅——从你的邮箱回复主题以 `[Enablement Request] {feature}` 开头的内部邮件（如 "Media Relay is enabled for this app."），系统轮询到 `enablement_internal_resolution_received` 事件后自动继续。D1 同理（横幅提示回复 `[Billing Request] Detailed invoice request` 邮件并**附上 PDF**，事件 `billing_internal_resolution_submitted`）。等待默认 45 分钟，超时 run 失败。

**CLI 备用**（同一引擎，本地跑）：

```bash
.venv/bin/python scripts/testing/production_ticket_scenarios.py --list    # 列剧本
.venv/bin/python scripts/testing/production_ticket_scenarios.py --check   # 只验 DB/SMTP/IMAP 连通，不发信
.venv/bin/python scripts/testing/production_ticket_scenarios.py --scenario E1
```

- 每轮等待默认 20 分钟（回复有 6-10 分钟随机延迟）；CLI 可用 `--turn-timeout-min/--approval-timeout-min` 调，网页用 env `AUTOMATION_TEST_TURN_TIMEOUT_MIN/AUTOMATION_TEST_APPROVAL_TIMEOUT_MIN`。
- 取消：网页 Cancel 按钮在等待间隙优雅退出（已发出的邮件不可撤回）。
- 每次运行创建真实 Zendesk 工单（主题带 `[zac test]`）；任一步 FAIL 即 run failed。

## 5. 失败排查

1. 追踪表 Pipeline 列即第一现场：route 不对→路由/prompt 回归；internal email failed→查 MSGRAPH/收件人 env；reply failed/manual_attention→查 worker 日志与 persona。
2. DB 直查（production 库）：
   ```sql
   SELECT execution_action, automation_status, internal_email_send_status,
          zendesk_ticket_status, automation_context->'account_suspension_contact_workflow' AS wf
   FROM support_account_cases WHERE zendesk_ticket_id='<id>';
   ```
3. 事件：`support_ticket_events` 里 `zendesk_fraud_review_handoff`、`enablement_internal_resolution_received`、`account_zendesk_status_synced`。
4. 追踪表本身：`SELECT * FROM automation_test_tickets ORDER BY id DESC LIMIT 10;`

## 6. 清理

- 每轮回归后，在 Zendesk 里删除或关闭 `[zac test]` 工单（solved 类由自动化自然关闭，fraud 类停在 pending 需手动处理）。
- fraud 测试单会真实指派给审核人并发内部邮件/Slack——回归前知会团队，或在非工作时间执行。
- 追踪表按需保留（审计记录），不需要清理。

## 7. 时序参数速查

| 参数 | 默认 | 来源 env |
| --- | --- | --- |
| AI ownership 接管 | 90s | ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS |
| 客户回复随机延迟 | 6-10 min | 硬编码（account_reply_jobs） |
| 内部回复轮询 | 300s | AUTOMATION_REPLY_POLL_INTERVAL_SECONDS |
| enablement 内部邮件重试 | 60s | ENABLEMENT_DELIVERY_RETRY_POLL_INTERVAL_SECONDS |
