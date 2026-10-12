# Investigation 附件传输与续轮英文展示修复

计划名称：Investigation 附件传输与续轮英文展示修复。修订：R1。归属 p2-192（Investigation 附件闭环 v1），原合同 C1–C7 保留。原附件基线：4cc76f2673aa6d7ee71e61cb67db6ecb1338c436；修复规划基线：c6addcc0ccf74f0b75007ed8186d109f63e5a317。

不使用 S3、不保存二进制、不自动读图；仅 Investigation 绑定 thread 的文字+文件+@Hermes。附件冻结到 turn/draft，审批后经现有 delivery ledger 发 Zendesk。Production、Automation、真实工单重放不变。

C1 元数据入 ECS；C2 客户附件到正确 Slack thread；C3 工程师附件绑定 turn/draft；C4 新回合/新客户版本失效；C5 正文与附件同评论；C6 并发/重试/未知结果不重复；C7 来源与结果可追溯。

当前状态：R1 修复已通过用户转交的独立代码复审，PR #1451 已合并；正式 ECS Preproduction 发布门禁失败，自动回滚已成功。现有 n8n binding gate 证据保持有效；外部文件仅在内存流转，下载/上传失败拒绝公开正文，未知结果保留并回读。真实 Slack/Zendesk 业务样本仍未发送。

## R1 修复合同（Investigation 附件传输与续轮英文展示修复）

- C8 下载请求统一带 `User-Agent: SupportPortal-AttachmentSync/1.0`；Zendesk 原始请求携带鉴权，跨到 `zdusercontent.com` 后只保留 UA。
- C9 先读取 HTTP 状态再判断 `OSError`：401/403/404 停止自动重试，429/5xx/连接中断沿用恢复周期；持久化旧的 403 retry 标记也按永久失败归一化。
- C10 恢复入口先检查人工接管、终态、thread 绑定，再解析固定 `case_task.direction`；`pending + investigation` 可恢复，冲突/缺失只记录跳过原因，不打印异常堆栈。
- L1 客户续轮原文完整写入账本和 ticket mirror，Slack 使用英文译文、`plain_text`、comment/turn/来源时间/Zendesk 链接；翻译失败发送英文提示，不改变调查输入、route 或客户回复流程；已完成通知不重复翻译或发送。

代码证据：新增 `backend/tests/test_investigation_attachment_followup_repair.py` 和 `scripts/testing/verify_investigation_followup_repair.py`，通过 processor、notification delivery、background drain 和隔离 PostgreSQL schema 验证；反向验证将 UA、恢复方向和原文展示分别还原，每项均以断言失败检出旧行为。

## 实施合同与验证（2026-10-09）

- 客户附件：六字段入库，authenticated Zendesk comment 重新解析私有地址；文件经内存上传到绑定 thread，不向文本暴露 URL。每附件使用 namespace/ticket/comment/attachment 的 delivery intent，先持久化 Slack reserved file ID。未知 completion 仅回读该 ID 的 thread shares，禁止重传。
- 工程师附件：n8n 仅 PP 的人类 file_share +文字+@Hermes 新入口；API 校验 file owner/message/channel/thread 并验证可下载，字节立即丢弃。turn 和 draft 只持久化来源元数据。模型不可读取文件、不自动读图。
- draft：权威 store 保存附件快照；新客户 case revision 和新反馈回合使旧附件 draft 失效，旧附件 source turn 也不能在新回合后再生成 reply draft。
- 发送：现有 ledger claim 后下载全部文件，逐个 Zendesk upload，checkpoint 精确 attachment IDs，最后在 case/binding/draft 锁内校验版本并一次公开 POST 正文和全部 tokens。下载/upload 失败不发正文；发送未知只回读精确 ID 集合与正文/时间，不能按正文相同认领历史评论。
- API 的 Slack token 使用现有 PP SSM 引用；现行 token 的 auth.test 已确认 files:read/files:write。生产渲染不增加此引用。
- 文件上限 50 MiB、每条工程师消息最多 10 文件；source 失效明确提示重新附加。不新增资源、不使用 S3、不保存附件二进制。Zendesk signed CDN redirects 剥离鉴权，非 provider 域名拒绝。

| 契约 | 本地证明 | 外部证明状态 |
|---|---|---|
| C1 | 实际 n8n JavaScript → AutomationIntakeEvent → PG 评论 JSONB | n8n active `df3da950…`；ECS release schema/运行版本已验证 |
| C2 | memory/PG intent → external upload 顺序和未知回读，绑定 channel/thread | n8n active `b02f3ed5-667b-4633-be2c-ede582fcdf23`；binding gate 负向/正向契约测试通过；待隔离 Slack 文件样本 |
| C3 | handle_slack_hermes_message → feedback → reply → draft 权威快照 | ECS runtime 与入口回归已验证；工程师附件入口三场景契约测试通过；待隔离 Slack 文件样本 |
| C4 | 新反馈即 stale、旧 source 拒绝继续、worker 拒发 stale | memory/隔离 PG 已验证 |
| C5 | 实际 approved draft → ledger → worker 双附件一次 POST；成功 receipt 校验 attachment IDs | 待隔离 Zendesk 样本 |
| C6 | memory/PG 并发 claim 单胜者，原 payload 重试不再发；未知需精确 IDs 回读 | 外部故障注入未执行 |
| C7 | comment/source event/message/file IDs 与 ledger/checkpoint 状态可回读 | 待运行链路 IDs |

定向测试记录按组件归一：附件专项 20 passed、评论同步 23 passed、ECS 部署 70 passed；合计 113 passed。入口回归：371 passed、1 skipped、2 subtests passed。SQL DDL 与 repository 内嵌 DDL 同步，ticket schema v21 兼容 v20。早前原 worker 的 ownership 断言失败经干净 main 同样失败，为预存基线项，不修改断言。

n8n 修改前 active/draft 已再次远端回读，两个目标 workflow 均无 divergent draft；修改后仅更新 PP ECS intake 与 Hermes file_share 分支，Production 分支节点保持原样。验收指出附件入口的 binding 类型保护不足后，`[slack]Forward Thread|Prod+Preprod` 新增独立 Preproduction Hermes resolver、`status=bound && zendesk_ticket_id` gate 和无副作用忽略分支，当前 active `b02f3ed5-667b-4633-be2c-ede582fcdf23`；`[case]Sync Comments` active 仍为 `df3da950-e2ca-44ac-8df8-db8503e985eb`。脱敏恢复快照已刷新，`validate_workflow_snapshots.py` 通过（15 published、1 非目标 divergent、53 redactions）。

运行验证使用 ECS Preproduction；本地官方栈当前未运行，不启动会发真实回复的 poller。沿用会话中已明确授权的 ECS 技术验收替代本地官方栈。真实客户工单与 Production 不操作。

## R1 收尾证据（2026-10-11）

- 独立代码复审：通过；PR #1451，完整 HEAD `38c1a3fd63af40a4837855b4fe95313040ca3ae4`。复审者的 PG 用例在其环境跳过，不将该结果描述为 PG 通过。
- finalize：PR #1451 已合并，应用源提交 `f1efe32acbde73a0408bdebd589d32941cb80dac`。finalize 中未传 PG 环境变量的一轮为 108 passed / 89 skipped / 3 subtests；该轮不提供 PG 新证据。
- 技术环境：沿用用户已授权的 ECS Preproduction 技术验收，豁免本地官方栈重启。发布保留 active Prompt Release `pr-68ed5b0cbda8`、Hermes real/persona/agent 与 Archer flags。
- 边界：未授权实际 Slack/Zendesk 测试写入；没有恢复 Ticket 14002 的两份附件、没有重放 13923、没有 Production 晋级。p2-192 的业务样本合同仍为 waiting-for-evidence，恢复事件为用户明确授权合成附件/测试续轮的实际写入。

- 合并后 root main 定向复核：`RUN_POSTGRES_INTEGRATION=1 TICKET_DB_DSN=postgresql:///p2192_attachment_repair_test NATIVE_NOTIFICATION_TEST_DSN=postgresql:///p2192_attachment_repair_test .venv/bin/python -m pytest -q backend/tests/test_investigation_attachment_followup_repair.py backend/tests/test_native_hermes_notifications.py backend/tests/test_investigation_route_status_fix.py` → 127 passed / 1 skipped。唯一 skip 是内存 fixture 下不适用的 SQL transaction guarantee，同一测试的 PostgreSQL 参数实例通过；没有跳过 PG 修复专项。使用独立本地数据库和每测试临时 schema，非线上业务库。

- 首轮镜像 probe：worker :136、digest `sha256:ef09c0cfd1cd3f8a27b78d97f01d7a9ef6282d9c15c0e6938f37c6c9e7beb04a`，四项合同通过、exit 0、业务写入 0；见 `investigation-followup-pp-probe.json`。`investigation-followup-image-probe.py` 为可复现脚本，Fargate worker override command 为 `python -c <脚本原文>`，使用该角色现有 networkConfiguration；必须在正式 pipeline 完成后运行，因为预期翻译失败会在相同 CloudWatch log group 留下模拟 traceback。首轮执行时序安排有误，原证据保留，正式门禁需要新干净观察窗口。

### F-R1-DEPLOY-01（已授权门禁修复，等待独立复审）

CloudWatch 正式检查为 api=0、route=0、worker=273：旧 worker :135/task `dc4aaa796ddf44f291672610ddf068b8` 的 `attachment_binding_changed` 包含 136 条 ERROR + 136 条 traceback；隔离 probe/task `49d8beaebd3746c8889026fac7d94c60` 有 1 条预期翻译失败 traceback。旧 task 的 digest 为 `sha256:4b7d3582cdf87f13a2ea04f5409349566f986ccd728a3b9b276e1e9e3a49ceb2`。因此不能把失败只归因为 probe；新服务 revision 没有窗口内匹配错误，仍不能当作正式门禁通过。

原脚本 `verify_cloudwatch` 按整个 role prefix 从 deployment start 查询，包含滚动切换中的旧 worker；直接重发仍会受到同一错误流影响。用户已明确授权“门禁修复，独立复审后重发”：匹配本轮新 revision 的全部服务 task（包括已停止/启动失败 task，不限于最终 running task），旧版本与隔离 probe 错误保留独立证据，Provider probe 保留自己的硬门禁。独立复审前不 finalize/merge/deploy。恢复并保留原失败证据，禁止改写错误计数、跳过门禁或擅自暂停 poller。

另有本机 TLS 检查失败：本机 DNS 返回 `198.18.0.71`，SNI 为 SupportPortal 却呈现 github.com 证书；DoH 查询 CNAME 与 AWS ALB DNS 一致，`/usr/bin/curl --doh-url https://cloudflare-dns.com/dns-query` 在正常 TLS 校验下回读 `/automation/preproduction/health/live` 通过。仅验证了本次进程解析替代，未修改主机配置或部署目标；无需通过关闭证书校验解决。

最终首轮状态：`r20261011-f1efe32` 的 pipeline exit 1、`failed_before_activation`、`checks.rollback=succeeded`。2026-10-11T14:12Z 后独立回读三服务 api :135 / route :134 / worker :135，均单 PRIMARY、COMPLETED、desired/running/pending=1/1/0；公网正常 TLS+DoH 回读原 release `r20261011-434ee35-p2`。两条 14002 附件仍 failed、403、无 Slack file ID、updated_at 与发布前相同，未重投递。正式收尾证据见 `investigation-followup-pp-release-evidence.json`。第二轮未启动；任务工作区保留，门禁修复完成后交独立复审。

#### 新增门禁合同（R1，不改变 C1–C10/L1）

| 合同 | 真实入口 / 行为 | 证据 |
|---|---|---|
| G1 | verify_cloudwatch → Python CLI → AWS 边界；完整 prefix 窗口、精确 service group + 新 revision；运行中/已停止任务均计入，新服务错误阻断 | 实际 bash/CLI 回归，272 旧错误 + 1 同 revision probe 排除且保留事件归属；running/stopped 错误负向 |
| G2 | 无日志的启动失败/非零退出阻断；未知 task/stream、AWS 失败、缺失新服务、停止时间或退出状态未知 fail closed | 实际 gate 负向；三角色继续采集，100 task 分批不遗漏 |
| G3 | 未通过与排除项全部进入角色 report 和最终 evidence；Provider 仍阻断；凭证来源与原发布脚本相同 | evidence 回读、Provider gate 回归、AWS 边界凭证断言 |
| G4 | resume 保留原窗口与复用 task；55 分钟采集上限、结束时一小时硬上限，不能认证已过 ECS 停止任务保留保证的历史窗口 | resume 创建时间不排除、历史停止时间区分、过期窗口阻断 |

新增代码仅涉及发布采集器、shell 门禁、回归与 Runbook，未修改附件/业务行为。当前修复基线
`bd2ce145a16672ecb38f0892598ffa4681ad61d5`，已包含 `main` 的原失败证据记录
`11da09080388059b3934197aeb09c5200acce128`。验证命令与结果统一见
[`investigation-cloudwatch-gate-repair-evidence.json`](investigation-cloudwatch-gate-repair-evidence.json)。
原失败记录保留；代码自测不等于独立复审通过，更不等于 Preproduction 或真实业务验收。
