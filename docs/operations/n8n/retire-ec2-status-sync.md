# 旧 EC2 状态同步分支退役

计划名称：旧 EC2 状态同步分支退役；修订 R1；Task `p2-201`。
核对及发布日期：2026-10-14（Asia/Shanghai）。用户明确确认停用旧分支并为本次失败写处理说明。

## 范围和原因

`[case]Sync Status`（`03B6AvcrOgRkWlUc`）保留的两条旧 EC2 状态同步分支仍会处理历史工单。旧 `/production` 状态请求读取 `support_hermes_case_bindings` 时缺少权限，产生 500。用户确认旧工单不再维护，因此退役入口；不把退役记为数据库权限修复，不迁移或重放历史工单。

旧 EC2 分支包含 `Account Case membership check`、`Is Account Case`、`HTTP Request2` 及 `prodCase membership check`、`Is prodCase`、`HTTP Request1`。两条从 `Execution Data` 到 membership 的连接已删除，六个节点全部 `disabled=true`。节点保留用于历史恢复。ECS Preproduction 分支、凭据、其余节点和 workflow settings 保持修改前内容。ECS Production 状态同步不在本次范围。

## 发布与验证

- 修改前 active=draft：`80985a3f-fefd-420b-90e5-5ea25a21bde7`；先刷新脱敏 Git 快照并通过快照校验。
- MCP 原子更新九项操作：断开两条入口连接、禁用六节点、更新退役 description。
- 变更后、发布前逐字段比较：六节点只增加禁用标记；其余节点与 settings 一致；连接差异仅两条旧入口。
- 发布后 active=draft：`416d2b54-4f90-4be0-be95-4bad7a9a8be8`，workflow 仍 active，13 个节点；再次经 MCP 回读确认。
- 触发器可达图：`Zendesk Trigger → get_case_info → Execution Data → ECS Preproduction status membership → Is ECS Preproduction Case → Build ECS ticket.updated intake → Send status to ECS Preproduction`；六个旧 EC2 节点均不可达。
- MCP 验证提示：两处 membership 的断开输入是刻意退役的结果；`get_case_info` 的硬编码 authorization 提示为既有问题，本次未修改凭据。快照只保存脱敏占位符。
- 未执行、重跑真实 workflow 或业务探针；验证范围是发布配置及图可达性，不宣称自然执行成功。

## 失败处理台账

用户认可的本次失败为 execution `191922`，incident `ebeabbdb-36bf-43a4-bda1-780d627d501e`，sourceInstance `n8n-default`。

发布回读后重新 list 确认标识仍匹配，使用 `workflow-status-review` 的 process 写入退役原因：旧工单不再维护；两条旧分支已退役且发布回读；未修权限、未重放、未迁移。

process 返回 `success=true`、`alreadyProcessed=false`，processing ID `b245f778-973e-4691-8584-8544a46fb535`，处理时间 `2026-10-14T03:33:54.470Z`。随后 list 返回 `total=0`、`returned=0`，本次失败不再进入未处理日报。此操作只更新处理台账，不改 Incident 的 OPEN/IN_PROGRESS/RESOLVED 状态。

恢复基线：[当前发布快照](../../integrations/n8n/workflows/active/03B6AvcrOgRkWlUc.published.json)。恢复旧版本将重新启用历史分支，须有新的明确授权。
