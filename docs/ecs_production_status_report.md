# ECS Production 当前状态与验收边界

日期：2026-09-04

范围：SupportPortal `/automation/production` 的 Automation API、Route、Worker、发布门禁与基础设施状态。

当前结论：ECS Production 的代码、镜像、Prompt Release、基础设施和只读依赖门禁均已通过，已准备接收全新 Enablement、Fraud、Account Suspension 工单。业务与外部 readback 尚未完成；EC2 `/production` 继续作为健康 backup。

## 1. 当前 Release

| 项目 | 当前值 |
| --- | --- |
| Release | `r20260904-1f13334` |
| Git commit | `1f13334ea2dcc5cddd63747562ffb1dd02c2f199` |
| Prompt Release | `pr-c9b3a291ecf1`，`active`，28 items |
| Promotion | `local-oci -> supportportal/production`，首次获批 bootstrap 例外 |
| API | revision `28`，`1/1/0`，deployment `COMPLETED` |
| Route | revision `23`，`1/1/0`，deployment `COMPLETED` |
| Worker | revision `26`，`1/1/0`，deployment `COMPLETED` |

运行中的三个 task digest 与 Release Manifest、Promotion Record 和 ECR readback 一致：

- API：`sha256:b954862ad4cc4742e94ed1fd94fdda8574ac4010539e26405caf00c006b089c7`
- Route：`sha256:78d10c594239f35a782ee2a6a730ad24fb2561321d6724d1ccf8b498a5900436`
- Worker：`sha256:e40fc2872c274a3e74e981e20f70ce3a919bba1437b216d90ea2fcfb745bff7a`

这次 `local-oci` 发布是 Preproduction 尚未建立时经 owner 批准的一次性 Production bootstrap。后续 release 在 Preproduction 建成后必须恢复同一 OCI digest 晋升，不得在 Production 重建。

## 2. 已通过的技术门禁

- 正式 `deploy_automation_ecs_release.sh --check-only` 与授权部署均通过；部署顺序为 Route、Worker、heartbeat、API、Prompt activation。
- 公网 `/health/live`、`/health/release`、`/health/ready` 均通过；release、commit、Prompt Release 与运行 digest 匹配。
- 最新 Route/Worker heartbeat 属于当前 release，age 小于 1 秒，`provenance_mismatches=[]`。
- Prompt target `pr-c9b3a291ecf1` 已激活，build ref 为 `76d22d5ae1a3`，完整内容指纹校验通过。
- CloudWatch 最近 15 分钟 API/Route/Worker 错误数为 `0/0/0`。
- EC2 backup `https://support.stellarix.space/health` 正常。
- Terraform `1.9.8` 使用远程 S3 state 与 DynamoDB lock；发布后真实 `plan -detailed-exitcode` 返回 exit `0`、`No changes`。
- Worker task definition 保留 Graph EFS 与 Suspension secret；Pilot env、volume、mount 均为 0。
- 一次性同 revision Worker 只读探针 exit `0`：镜像内不存在 `/app/bin/pilot`，Archer GET、Graph `/me` 与 Zendesk identity 均成功。
- 上述探针未发送邮件、未创建或修改工单，也未重试历史 `outcome_unknown`。

## 3. 内部邮件收件人

三组 Production JSON 配置均格式有效且为 `To=1/Cc=1`：

| 流程 | To 合同 | Cc 合同 | 当前 readback |
| --- | --- | --- | --- |
| Enablement | `zhonghuang@agora.io` | owner | 匹配；用户于 2026-09-04 明确确认保持该值 |
| Fraud | Suhrid | owner | 匹配 |
| Account Suspension | Suhrid | owner | 匹配 |

收件人值来自 Worker task definition 引用的 SSM SecureString。发布与检查日志只输出格式、数量和匹配布尔值，不回显参数内容。

## 4. 三类真实工单验收

用户负责创建工单并提供 Ticket ID；本任务负责只读追踪和结果核对。不得重放历史工单。

### Enablement

- Intake、Route、Persona 和 App ID 提取正确，客户称呼来自当前消息作者。
- 有效 App ID 时 Archer 执行与写后 readback 符合合同，公开回复成功，工单状态与 execution 终态一致。
- 缺失、非法或查无项目时只发送对应补充/纠正文案，不产生错误成功声明；失败按既有 Human Review 合同处理。
- 核对 Execution、Job、Delivery、Zendesk comment/status 和 Archer readback；内部邮件若按失败/升级合同触发，收件人为已确认的 Enablement 配置。

### Fraud

- 首轮缺字段时只追问缺失项；客户补齐后继续原 handler，不误入 RAG fallback。
- 内部 handoff 邮件 sent 后生成客户回复，assign Suhrid，进入 Human Review；不把失败或不确定结果报告为成功。
- 核对字段提取、Persona、Execution/Job/Delivery、邮件、Zendesk comment/status/assignee。

### Account Suspension

- 新单走一段式 direct handoff，不询问邮箱；严格使用有效 ticket email。
- 内部 handoff 邮件 sent 后才创建唯一 closing reply job。
- v25 首封只称 `this request`，包含感谢提交、内部审核和我们 24 小时内回复三要素，不出现类别词。
- 发布后 assign Suhrid但不 solved；assign 后不再发送冗余 reviewer 通知。owner 总邮件数应为分类通知加 handoff 两封。
- 核对 `intake_mode=direct_handoff`、`confirmed_email_source=ticket_email`、Execution/Job/Delivery、邮件与 Zendesk comment/status/assignee。

## 5. 当前边界

- n8n 切流由用户单独处理，本任务不修改 workflow。
- 不创建、回复、修改或重放历史工单；不重试 `outcome_unknown`。
- 不因 ECS `1/1/0` 或 health 200 单独宣称业务验收完成；三类工单都需要数据库与外部系统 readback。
- EC2 `/production` 保持可用 backup；业务验收前不下线或删除其数据库、volume、镜像和 worker。
- Dashboard 认证与 status sync 不在本次验收范围。

## 6. 下一步

等待用户提供三张全新工单的 Ticket ID。收到后按 Enablement、Fraud、Account Suspension 三条链分别追踪，不产生额外业务副作用，并在三条证据链全部闭环后更新对应 Task 状态。
