# Investigation 附件统一管理（Slack）

修订：R2；执行日期：2026-10-14。状态：独立代码验收通过，未 finalize；等待 Preproduction 授权与验证。

## 版本与授权

- 原始源码基线：`0124173bbc6c818efd89f05de9127684f6133ac4`。
- 已整合主干：`fce24b4fda64372acd6253221ed64e6faf4741cb`。知识执行与状态同步修改已合入本分支，重叠的 worker/部署入口纳入回归。
- 分支：`codex/investigation-case-files`；工作区：`.worktrees/investigation-case-files`；完整最终 HEAD 由 Draft PR 与验收交接消息提供。
- 用户授权实施后交给聊天 `01a1349a-c8e9-7183-829d-6b158e735eb5` 独立验收。通过前不得 finalize、merge、deploy；通过后沿用正式 Preproduction 发布门禁。Production、新真实业务写入另需授权。
- 未改外部 agent-infra、未安装或调用 `hermes-zac-engineering-handoff`、未修改 n8n、未重放历史工单。

## 已实现的合同

文件存放在 Slack；新增 Map 元数据表 `investigation_case_files`，coordination schema 为 `automation-ecs-016`。主键生成 `{case}-{source}-{id}{extension}`，同时保留 original/managed/slack 三名称。唯一键为 namespace/case/source/source-file，数据库事务内分配名称。Map 不保存下载 URL、凭据或二进制。

工程师入口先核验可信事件的 uploader、消息、thread、file ID，再登记；已有 Slack 文件不改名或复制。客户附件复用既有交付账本，上传使用管理名，确认 thread shares 后 available。读取使用实时 Slack 元数据与临时文件，来源比较采用 slack_filename。旧工程师文件在 list 时按需核验登记，不批量重传。

Hermes Work 的私有运行 metadata 持有签名 capability，后端验证 namespace/case/turn/session/request/phase/当前 revision。模型不能传 case/turn/session 或来源 URL。Persona 的 capability 仅供草稿选择，文件工具与 Skill 仅开放绑定 Investigation Work。新开关默认关闭。

云端交付为 [版本化补丁包](../../deployment/hermes-case-files/)：实际 gateway `_run_sync` 注入 ContextVar，插件注册五个工具，Skill discovery/list/view 均执行范围检查；下载走单次二进制流，发布路径限定在该运行 workspace/临时目录。另提供 profile staging 与 immutable-image overlay 构建文件，尚未云端构建或发布。

生成文件复用既有通知账本，以 Map ID 和稳定 operation ID 去重；已预留 file ID 而结果未知时先回读，不能盲目重传。Zac 的可信 acquisition adapter 导入 seam 已实现，来源 message/session 写入 Map，case binding 单独保存 zac_session_id；真实协议仍未知。

新能力下草稿显式保存 selected Map refs，审批冻结名称、版本、Slack ID 与来源。worker 逐字段比较快照、重新核验下载，任一附件失败则公开正文不提交；同正文更换附件快照拒绝，新回合使旧草稿失效。旧能力关闭时保留 v1 行为。

## 合同与证据边界

| 合同 | 本地真实入口与断言 | 云端/业务证据 |
| --- | --- | --- |
| C1 命名与去重 | Slack feedback 入口同名不同 file ID；重试原事件；PG 多连接并发登记，唯一 Map/管理名；客户通知用管理名上传 | 等待 PP Slack 回读 |
| C2 Case 隔离 | 实际工具 API 拒绝外 case Map、伪造 turn/case/capability，下载 mock 未调用；当前 revision/session 校验 | 等待 PP capability 负向验证 |
| C3 真实读取 | 实际工具 API 返回指定日志第2行及 Map 引用，search 行位置、ZIP目录/文本成员；实际桥接下载字节与引用；processor 私有 metadata 注入且 capability 不在模型输入 | Slack/HTTP 外部边界为 mock；等待 PP 样本 |
| C4 失败恢复 | 删除拒绝并标 unavailable、入口超限拒绝、generated 预留 file ID 超时后回读可用且只上传一次；不支持格式明确 download-only | 等待 PP 权限拒绝与未知结果场景 |
| C5 审批交付 | 实际 Persona API→PG/Memory草稿→审批→worker；冻结快照冲突拒绝、删除拒发、旧回合 stale | Zendesk/Slack 边界 mock，无真实回执 |
| C6 Automation 隔离 | toolsets 按 route/automation/adhoc 拒绝；实际 patched skills_list/skill_view 含路径/qualified bypass负向执行；插件 check_fn 与无scope调用拒绝 | 等待 PP runtime 回读 |
| C7 生成与 Zac | 生成未知结果回读不重复；可信 Zac acquire seam 合成文件调用及 session/source关联 | 真实 Zac 获取 `waiting-for-evidence` |

测试脚本与 fixtures 均纳入本 PR；证据命令/结果见 [本地检查记录](investigation-case-files-r2-evidence.json)。隔离 PostgreSQL 使用 `p2192_case_files_test`，外部服务以 mock 隔离；这不证明 Slack/Zendesk 实际发送或云端上线。

## 等待与下一步

- R2-CODE-REVIEW：独立验收尚未执行；验收范围为 SupportPortal diff、云端补丁/构建包与本地入口证据。执行者自测不释放门禁。
- R2-PP：等待代码验收后，回读真实 Hermes immutable image、实际 overlay 与 profile；保留既有 close/其他能力，构建输入 drift 不可覆盖。按[运维步骤](../operations/investigation-case-files.md)执行正式 PP 发布并记录 SP/Hermes 版本、Map、Slack IDs、Zendesk回执。
- R2-ZAC：真实鉴权文件获取协议/样本缺失。提供实际下载入口后再接 adapter 并验证；合成 acquire 通过不能关闭此等待。
- R1-EXISTING：p2-192 原 F-R1-DEPLOY-01 发布复审/正式 pipeline、真实业务闭环门禁保留；本轮测试不替代既有证据。历史详情见 [v1](investigation-attachments-v1.md)。

没有未决产品选择；当前允许的下一步仅是指定聊天独立代码验收。工作区保留用于修复，验收前不清理。
