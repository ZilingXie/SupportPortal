# 知识来源接收契约（n8n → SupportPortal）v1

状态：**提案冻结（frozen proposal）**。本契约由 n8n 来源迁移计划（p2-183）按计划步骤 1 整理并冻结字段与回执语义；SupportPortal 侧接收接口尚未实现，路径与认证为提案值，须由治理模块实现时确认或修订（见[开放项](#开放项)）。在 SP 侧确认前，本契约只约束 n8n 侧投递节点的结构，不声明任何线上端点已可用。

## 背景与边界

目标架构：n8n 只负责取得并投递来源快照；Hermes 负责总结和独立 Review；SupportPortal 负责审计与受控写入（WeKnora）。因此：

- n8n 投递的是**原始来源快照**，不做脱敏、不做"是否值得入库"的前置判断、不生成 KB 内容。
- Review、去重决策和知识库写入都不留在 n8n。
- n8n 收到成功回执只表示**已接收**，不表示已通过 Review 或已写入知识库。
- Case ID、Hermes session 和 Slack thread 由 SupportPortal 关联；n8n 不猜测、不填充这些值。

## 端点与认证（提案）

- 端点：`POST {automation base}/v1/knowledge/sources`，automation base 先指向 Preproduction：
  `https://supportcenter.stellarix.space/automation/preproduction/v1/knowledge/sources`
- 认证：`Authorization: Bearer <token>`，与现有 `/automation/{environment}/v1/intake` 同机制（`backend/automation_ecs_api.py` 的 intake token 中间件）。n8n 侧复用现有 `httpBearerAuth` credential（`automation_production`，id `toE81efBqb60KXsy`，与两条 intake 分流节点相同），不新增明文凭据。

## 请求契约

```json
{
  "schema_version": "knowledge-source-v1",
  "source_type": "zendesk_ticket | csd_issue",
  "source_id": "字符串，来源系统内唯一 ID",
  "source_updated_at": "ISO 8601，来源系统的最后更新时间，作为来源版本",
  "payload": { "来源系统原始快照对象" },
  "references": { "来源 URL 等关联引用" }
}
```

| source_type | source_id | payload 内容 | references |
| --- | --- | --- | --- |
| `zendesk_ticket` | Zendesk ticket ID | `{ ticket: 完整 ticket 对象, comments: 全部分页后的评论对象 }` | `{ zendesk_url }` |
| `csd_issue` | Jira issue key（如 CSD-12345） | `{ issue: 完整 Jira issue 对象（fields=*,comment） }` | `{ jira_url }` |

CSD 快照以 `fields.comment.total` 对照 `comments.length` 判定完整性（Jira 单 issue 详情的 comment 字段超过 `maxResults` 时截断）；判定不完整即显式失败，不投递半份快照。

## 回执契约

HTTP 2xx 且 body 含以下 `status` 之一即视为投递成功：

| status | 语义 | task_id |
| --- | --- | --- |
| `accepted` | 新快照（source_id+source_updated_at 首见）已持久化并创建任务 | 非空 |
| `already_exists` | 同 source_id 且同 source_updated_at 已接收，幂等重投被吸收 | 已有任务 ID |
| `stale_ignored` | 同 source_id 已存在更新的 source_updated_at，旧版本乱序到达被忽略 | 已有任务 ID |

- 唯一性键：`source_type + source_id`；版本：`source_updated_at`（严格比较）。三种成功状态的回执都必须携带非空 `task_id`（`accepted` 为新建任务 ID，其余为已有任务 ID）；`task_id` 缺失或为空视为异常回执。
- 回执异常（非 2xx、超时、2xx 但 status 不在三者之内、或 `task_id` 缺失/为空）都使 n8n 执行失败并触发 `[ops]Error Alert`，不允许静默吞掉。

## 幂等与重投

- n8n 侧不再先写本地 PostgreSQL 去重表；重复与乱序由回执 `already_exists` / `stale_ignored` 兜底。
- 投递节点带节点级重试（同一来源版本重投安全）；超时或失败后可整轮重跑，无需删除任何去重记录恢复。
- Zendesk 工单更新后再次 SOLVED 触发：`source_updated_at` 变化 → `accepted` 新版本；旧版本迟到 → `stale_ignored`。

## n8n 侧不变量（两条来源流程共用）

1. 投递前必须完成**评论完整性校验**（Zendesk：`next_page` 为空且评论数不少于 `comment_count`；CSD：`fields.comment` 对象、`comments` 数组与数值型 `total` 必须齐备，`comments.length` 不少于 `total`），结构缺失/异常或数量不满足均显式失败（fail-closed），不投递半份快照。
2. 投递原始内容，不做脱敏或改写（脱敏与质量判断属于 Review/写入侧）。
3. 原有 Zendesk KB 草稿与 Google Sheets 输出节点保留在画布但与主链断开并禁用，待 Review 通过后的输出链对接；旧 Memory Wiki 直写节点整体删除。
4. 定时扫描（CSD JQL）与触发条件（Zendesk SOLVED）只决定**来源范围**，不决定知识质量。
5. Zendesk 读取与输出节点一律使用 n8n credential 引用（`zendeskApi`，复用 `[case]Intake|ECS Route` 的 `Zendesk account 3`），不在节点参数中保存 inline Authorization/Cookie；回执成功与否由 status 三态 **加** 非空 `task_id` 共同判定（`Check Delivery Receipt` + `Check Receipt Task`）。

## 开放项

1. **SP 接收接口实现**：本契约的端点路径、认证机制、回执字段需由 SupportPortal 治理模块实现时确认；如最终路径或认证不同，仅需修改两条 n8n 流程中的 `Deliver Source Snapshot` 节点。
2. **接口就绪前不发布**：n8n 草稿完成并通过独立验收后，仍须等待 Preproduction 接口可用才能发布，否则投递必然失败并中断知识入库链路。
3. **Review 输出链**：Hermes 总结与 Review 链尚不存在；Zendesk 草稿与 Sheets 的"Review 通过后触发"对接为后续工作。
4. **WeKnora 写入链**：SupportPortal 受控写入目标，尚未建设。

## 验证矩阵（对应计划验收项）

| 场景 | 验证方式 | 前置 |
| --- | --- | --- |
| 首次投递 → accepted | 真实/测试来源投递后核对回执与 SP 持久化 | SP 接口就绪 |
| 重复事件 → already_exists | 同版本重投 | SP 接口就绪 |
| 更新后新版本 → accepted；旧版本 → stale_ignored | 更新来源后重投 | SP 接口就绪 |
| 评论不完整 → 显式失败 | 构造分页不完整输入，确认执行失败且未投递 | 草稿级可验 |
| SP 超时 → 失败告警，重投安全 | 断开接口/模拟超时 | SP 接口就绪 |
| Review 拒绝不影响 n8n | Review 链行为，n8n 侧无动作 | Review 链就绪 |
| 旧 Memory Wiki 写入调用为零 | 草稿/发布图中无任何 Memory 端点节点 | 草稿级可验 |
