# Investigation 附件闭环 v1

版本：R1。基线：4cc76f2673aa6d7ee71e61cb67db6ecb1338c436。

不使用 S3、不保存二进制、不自动读图；仅 Investigation 绑定 thread 的文字+文件+@Hermes。附件冻结到 turn/draft，审批后经现有 delivery ledger 发 Zendesk。Production、Automation、真实工单重放不变。

C1 元数据入 ECS；C2 客户附件到正确 Slack thread；C3 工程师附件绑定 turn/draft；C4 新回合/新客户版本失效；C5 正文与附件同评论；C6 并发/重试/未知结果不重复；C7 来源与结果可追溯。

当前状态：实现中，未 finalize、未部署。外部文件仅在内存流转；下载/上传失败拒绝公开正文，未知结果保留并回读。

## 实施合同与验证（2026-10-09）

- 客户附件：六字段入库，authenticated Zendesk comment 重新解析私有地址；文件经内存上传到绑定 thread，不向文本暴露 URL。每附件使用 namespace/ticket/comment/attachment 的 delivery intent，先持久化 Slack reserved file ID。未知 completion 仅回读该 ID 的 thread shares，禁止重传。
- 工程师附件：n8n 仅 PP 的人类 file_share +文字+@Hermes 新入口；API 校验 file owner/message/channel/thread 并验证可下载，字节立即丢弃。turn 和 draft 只持久化来源元数据。模型不可读取文件、不自动读图。
- draft：权威 store 保存附件快照；新客户 case revision 和新反馈回合使旧附件 draft 失效，旧附件 source turn 也不能在新回合后再生成 reply draft。
- 发送：现有 ledger claim 后下载全部文件，逐个 Zendesk upload，checkpoint 精确 attachment IDs，最后在 case/binding/draft 锁内校验版本并一次公开 POST 正文和全部 tokens。下载/upload 失败不发正文；发送未知只回读精确 ID 集合与正文/时间，不能按正文相同认领历史评论。
- API 的 Slack token 使用现有 PP SSM 引用；现行 token 的 auth.test 已确认 files:read/files:write。生产渲染不增加此引用。
- 文件上限 50 MiB、每条工程师消息最多 10 文件；source 失效明确提示重新附加。不新增资源、不使用 S3、不保存附件二进制。Zendesk signed CDN redirects 剥离鉴权，非 provider 域名拒绝。

| 契约 | 本地证明 | 外部证明状态 |
|---|---|---|
| C1 | 实际 n8n JavaScript → AutomationIntakeEvent → PG 评论 JSONB | 待发布回读 |
| C2 | memory/PG intent → external upload 顺序和未知回读，绑定 channel/thread | 待隔离 Slack 文件样本 |
| C3 | handle_slack_hermes_message → feedback → reply → draft 权威快照 | 待 PP runtime 验证 |
| C4 | 新反馈即 stale、旧 source 拒绝继续、worker 拒发 stale | memory/隔离 PG 已验证 |
| C5 | 实际 approved draft → ledger → worker 双附件一次 POST；成功 receipt 校验 attachment IDs | 待隔离 Zendesk 样本 |
| C6 | memory/PG 并发 claim 单胜者，原 payload 重试不再发；未知需精确 IDs 回读 | 外部故障注入未执行 |
| C7 | comment/source event/message/file IDs 与 ledger/checkpoint 状态可回读 | 待运行链路 IDs |

入口回归：371 passed、1 skipped、2 subtests passed；追加 source-round、表单编码和 API secret 边界后附件/部署专项 98 passed（隔离 PG）。SQL DDL 与 repository 内嵌 DDL 同步，ticket schema v21 兼容 v20。早前原 worker 的 ownership 断言失败经干净 main 同样失败，为预存基线项，不修改断言。

n8n 修改前 active/draft 已再次远端回读，两个目标 workflow 均无 divergent draft；已刷新脱敏恢复快照并通过 validate_workflow_snapshots（15 published、1 非目标 divergent、53 redactions）。远端尚未修改。先上线支持附件契约的 SP，再发布 n8n PP 节点。

运行验证使用 ECS Preproduction；本地官方栈当前未运行，不启动会发真实回复的 poller。沿用会话中已明确授权的 ECS 技术验收替代本地官方栈。真实客户工单与 Production 不操作。
