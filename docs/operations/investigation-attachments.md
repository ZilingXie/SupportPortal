# Investigation 附件闭环 v1 运维

核对日期：2026-10-09；合同与证据：[R1](../plans/investigation-attachments-v1.md)。

工程师在绑定 Investigation thread 内发送文字、文件并 @Hermes。只有当前回合附件进入 draft；审批后正文与全部文件一起发送。v1 不自动读图，模型只能看文件名/类型等元数据。客户附件由已认证 Zendesk 评论下载后上传到原 thread。

来源删除或权限失效：在原 thread 重新附加文件并 @Hermes，重新生成和审批 draft。失败不会发送半份公开回复。50 MiB/file，最多10个工程师文件。

追踪：评论 attachments JSONB 保存客户元数据；native-hermes-notification:{namespace} 中 attachment:{ticket}:{comment}:{attachment} 保存 Slack reserved file ID 与 delivery 状态。公开回复 ledger attachments 保存 Slack 来源与 Zendesk upload attachment ID；delivered 保存 Zendesk comment ID。二进制与私有 URL 不落库。

outcome_unknown/sending 不可直接清状态重发。先以 reserved Slack file ID 回读 channel/thread shares，或以 Zendesk attachment IDs+正文+时间回读 audits。无法确认时保留未知；明确失效后重新附加走新回合，不伪造成功。

Prerequisite：PP API/worker 复用 ENGINEER_SLACK_ACCESS_TOKEN（files:read/files:write）与 zendesk_basic_auth；需部署 schema bootstrap v21（SQL/内嵌DDL均包含两个 JSONB 字段）。先 SP 上线再发布 n8n PP 节点。Production 无本次变更。

源码：[transfer](../../backend/services/investigation_attachments.py)、[native delivery](../../backend/services/automation_native_notifications.py)、[worker](../../backend/worker.py)、[回归](../../backend/tests/test_investigation_attachments.py)。
