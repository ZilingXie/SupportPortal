# Investigation 回复长度优化 v1

当前合同：R1。任务：p2-191。范围是 Investigation 客户 draft 与工程师 Slack 调查展示；不改变 route、附件、状态同步、审批语义或 Production。

## 当前合同

| Contract | 入口与状态 | 机制 | 可观察结果 |
| --- | --- | --- | --- |
| C1 | `engineer_investigation_reply` Persona 首次输出超过预算 | 复用现有两次生成循环，使用 `automation_persona_reply_too_long` 反馈 | 第二次请求明确要求只保留结论、关键限制/证据和下一步 |
| C2 | Investigation draft 保存前，含 greeting 和引用的完整内容 | `tool_save_reply_draft` 以 `reply_too_long` fail-closed | 超过 1200 字符不调用 guardrail、不创建 draft |
| C3 | Investigation result、ad-hoc result、review pending Slack 展示 | `engineer_slack` 共享 bounded renderer，最终 2000 字符检查 | 条目数量和单项长度有界，超预算不发送 |
| C4 | 调查进度持久化与 Slack 展示 | 只限制展示，不截断 `save_hermes_investigation` 数据 | 数据库保留完整调查记录；合法 draft 在审批消息完整显示 |
| C5 | 版本与回归 | prompt version v2、定向测试、Preproduction 技术检查 | 可追溯到源 commit、运行 commit、prompt release 和测试结果 |

## 验证边界

- 代码验证：`test_automation_persona.py`、`test_hermes_zendesk_agent_tools.py`、`test_engineer_slack.py`。
- 集成验证：相关 Hermes follow-up、Zendesk draft/approval 和 worker 套件。
- Preproduction：使用隔离测试入口验证长调查与长 draft，不重放已关闭 Ticket 13923，不发送真实客户回复或真实 Slack 测试消息。
- Production 发布和自然业务样本不在本任务授权内。

## R1 完成证据（2026-10-10）

- 代码提交 `42a2da282973195edf4fb53730f96e4b117011bd` 已合入当前 `main`；受影响四个测试文件合计 **181 passed、92 subtests passed**，`compileall` 与 `git diff --check` 通过。
- Preproduction release `r20261009-1a7b6e5`（source `1a7b6e511d3a670ac3b471bc5c42a291b6042760`，Prompt Release `pr-ee28a3c51d44`）正式 evidence 为 `complete`。API/Route/Worker 为 `:121/:120/:121`，三角色 digest 与 manifest 一致；Prompt active/sync、CloudWatch、provider probe、public health、Terraform pre/post zero-drift 全通过。
- 只读 live release endpoint 与 `scripts.testing.preproduction --check` 均通过，确认 DB/Zendesk/SMTP/Relay/Pilot 连通；未创建工单、未发送客户或 Slack 内容。
- 长度合同由隔离测试入口证明：Persona v2 首次超长只使用现有一次重生成，第二次仍超长阻断；完整 draft（含 greeting）超过 1,200 字符返回 `reply_too_long`；Investigation Slack 展示不超过 2,000 字符且调查记录保持完整。
- 真实客户/Slack 样本和 Ticket 13923 重放仍按授权边界不执行，Production 不发布。
