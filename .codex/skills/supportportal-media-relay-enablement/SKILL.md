---
name: supportportal-media-relay-enablement
description: SupportPortal Media Relay（跨频道连麦/typeId=6）自动开通的本地执行技能。当日汇总入口、四步执行（归属确认、预检 dry-run、批准后执行、独立回读）、两次审批产物与 AgentRelay 回传契约。触发词：enablement 汇总、media relay 开通、relay task 处理、enablement-relay-request。
---

# SupportPortal Media Relay Enablement（p2-163 本地执行技能）

在 Mac 上处理 SupportPortal 派发到 AgentRelay 的 Enablement 自动开通申请。
ECS 侧不做任何 Archer 写入；本技能是唯一执行方，且**两次人工批准前零写入、零回传**。

## 固定目标参数（不得擅改）

```json
{
  "archer_url": "https://archer.agora.io",
  "typeId": 6,
  "status": 1,
  "region": 2,
  "maxSubscribeLoad": 10
}
```

- 只处理国际实例 `https://archer.agora.io`；国内实例项目一律阻断转人工。
- **不降配铁律**：项目已启用但参数不同（含已有 `maxSubscribeLoad=50`）时，只报告差异并停止，
  不得自动修改。
- **成功只认独立回读**：写入命令的退出码、`changed`、成功文案都不能替代写后独立的
  `pilot archer status`；Pilot 可能把 HTTP 403 解释为 already enabled，尤其如此。

## 日汇总入口（工作日 10:00，Asia/Shanghai）

1. 收集当前所有有效待处理 `enablement-relay-request-v1` 任务（来自 AgentRelay Listener 收件箱；
   不限 24 小时窗口；空队列不生成报告；当日错过调度最多补跑一次）。
2. 对每个申请运行预检（本技能 `scripts/relay_enablement.py precheck`），生成统一报告
   `report-<date>.md`：case/申请版本/工单、邮箱+AppID+Project/Company ID 归属核验、当前状态与
   目标参数、dry-run 结果、建议动作（可执行 / 已满足 / 暂缓 / 阻断）与异常原因。
3. **第一次审批**：请 owner 批准（全部 / 部分 / 暂缓 / 修改建议）。批准只授权当前报告版本中的
   申请；邮箱、AppID、实例或目标参数变化后旧审批失效。
4. 批准后逐申请执行（`execute` 子命令：先复核申请仍有效，再 `pilot archer open`，随后独立
   `pilot archer status` 回读），按 Task 生成分组回传草稿：成功项说明是否写入/实际回读值/验证
   时间；失败项说明阶段/原因/是否尝试过写入/已知状态，并明确"ECS 将触发 internal note、人工接管
   与通知邮件"。
5. **第二次审批**：owner 批准草稿后才逐 Task 回传（用 AgentRelay 的 reply/消息工具发送
   `enablement-relay-result-v1` JSON 文本 part；业务内容放 parts，不假设 reply 支持任意 metadata）。
   回传后由 ECS 关闭 Relay Task；本地失败同样走草稿审批后回传。
6. 恢复与幂等：重启后"已执行未回传"只恢复回传，不重新开通；已回读成功仅回传暂时失败时恢复传输。

## SSO 与凭证边界

- Pilot 提示 SSO 会话过期时，**停止写入**，提示 owner 在本机运行 `pilot auth login`，
  本次处理可等待登录或按到期规则收尾。
- 浏览器 token、Cookie、Pilot 会话绝不进入 ECS、Relay 消息或任何报告/草稿。

## 判定表

| 检查结果 | 处理 |
|---|---|
| 未配置，归属与 dry-run 通过 | 审批后开通（outcome=enabled） |
| 已启用且 region=2 / maxSubscribeLoad=10 | 不重复写入（outcome=already_satisfied） |
| 已启用但参数不同（含 50） | 报告差异，停止自动修改（outcome=config_mismatch） |
| 邮箱与 AppID 不匹配、项目不存在 | 不写入（outcome=ownership_mismatch / project_not_found） |
| 写入返回成功但回读失败/不一致 | 不判成功，保存证据（outcome=enable_failed 或 outcome_unknown） |
| Pilot 超时/中断/回读不可用 | outcome_unknown：不盲目重写，交接要求人工先核对 |

## 结果回传契约（reply 消息的 text part，单条 JSON）

```json
{
  "schema_version": "enablement-relay-result-v1",
  "request_id": "<原样回传>",
  "outcome": "enabled | already_satisfied | config_mismatch | ownership_mismatch | project_not_found | enable_failed | outcome_unknown | cancelled_by_user",
  "write_attempted": true,
  "detail": "<脱敏说明，禁止包含 AppID 全量/邮箱/token>",
  "readback": {"state": "enabled", "region": 2, "maxSubscribeLoad": 10, "verified_at": "<iso8601>"},
  "approval_ref": {"batch": "<报告批次>", "approved_by": "zac", "approved_at": "<iso8601>"}
}
```

- `request_id` 必须与申请完全一致（ECS 按此绑定，不匹配即拒绝消费）。
- 写入前再次通过只读状态确认申请未被取消/接管/auto 仍启用；与写入之间的短竞态无法原子撤销，
  结果不确定时按 `outcome_unknown` 交接并要求人工先核对。

## 命令形态

```bash
python3 <skill-dir>/scripts/relay_enablement.py precheck  --request <request.json>
python3 <skill-dir>/scripts/relay_enablement.py execute   --request <request.json> \
        --approval-ref '<json 或 文件>'
python3 <skill-dir>/scripts/relay_enablement.py readback  --appid '<appid>'
```

Pilot 调用统一形态（`--url` 固定国际实例；预检加 `--dry-run`；归属用 `--email`；回读独立执行）：

```bash
pilot archer appid   --email '<customer_email>' --url 'https://archer.agora.io' -o json
pilot archer status  --appid '<appid>' --type 6 --url 'https://archer.agora.io' -o json
pilot archer open    --appid '<appid>' --type 6 --region 2 --max-subscribe-load 10 \
                    --dry-run --url 'https://archer.agora.io' -o json
pilot archer open    --appid '<appid>' --type 6 --region 2 --max-subscribe-load 10 \
                    --url 'https://archer.agora.io' -o json
```

本地运行数据（申请状态、执行记录、报告与草稿）放在
`~/Library/Application Support/supportportal-media-relay-enablement/`，与仓库隔离。

## Codex 侧定时拉起（owner 配置，repo 不含定时器）

- 工作日 10:00（Asia/Shanghai）触发一次"日汇总入口"对话（Codex App 定时任务或 Inbox 桥接的
  汇总入口均可）；Listener 保持持续接收并持久化后及时 ACK，不等到 10:00。
- 有未完成的汇总任务时优先继续，避免重复领取；恢复连接后补收。
- Relay Task 有效期 14 个自然日（覆盖周末与审批等待），到期由 ECS 侧按失败收尾，本地不再执行。
