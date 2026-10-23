# Investigation Slack ingress metadata v1

当前修订：**v1-repair-1（F1 / 方案 A，2026-10-24）**。仅完成 n8n human file_share 草稿修复和隔离验证，待完整 HEAD 独立代码验收。用户已明确选择：本轮保留 API 兼容阻塞，**不发布 live、不部署 SupportPortal**。`ready_for_real_file_tests=false`。

当前合同与脱敏结果以 [证据的 current_repair](investigation-case-files-slack-ingress-metadata-v1-evidence.json) 为准；其余 v2 字段和下方历史内容保留当时的范围，不代表本轮已发布或业务通过。

## 当前 F1 修复与停止点

- 工作区：`.worktrees/investigation-case-files-slack-ingress-metadata-v1`，分支 `codex/investigation-case-files-slack-bot-config-v1`，继续 Draft PR #1545。
- 起点：`a37757f062458ba9c3f6bd7928d99fd7314ba2f1`；同步主干的合并提交 `ce75bbf4` 保留双方 Task/evidence，Overview 通过生成器重建。
- live parent：`8f5f02d4-5448-45cb-b9f3-3499e7c254e8`；live child：`7400713d-c7e3-436d-9e05-fa53d6674fd7`，均未改变。
- 新 child draft：`ff147b6e-888a-4564-9824-fcc1676469c9`，18 节点；原 bot/普通文字节点及分支保留，只改变 human 入口和发送器的可信输入选择。
- 已验证根因：n8n 2.38.7 Slack Trigger 仅输出 `req.body.event`，丢弃 envelope team；自然 human file_share 无 team，原 child 两个校验器均不命中。

用户选择的方案 A 已实现：候选 human file_share 经现有 Slack credential 的 `auth.test`、`conversations.info`、`conversations.replies` 三次 GET，核对 workspace/channel、准确消息 ts/thread/user/text/mention、文件 id/owner/name/type/size 后，才从 API `auth.team_id` 补入 team。没有 `file.user_team` 或静态回退；显式错误 team 不予修复。未经核验的 event_id/标记和私有 URL 不进入可信输入。API/超时/不完整返回或来源不匹配均停止，之后仍需原 Investigation binding gate；附件不降级到 Production/adhoc。

| 合同 | 验证 | 边界 |
| --- | --- | --- |
| F1-A1 真实入口 | 实际 snapshot 表达式、Code、边和发送 payload 经 Node VM 执行；native n8n draft 正例 `198401` 到 mock sender | 所有外部写入均拦截，不是业务投递 |
| F1-A2 来源与 fail closed | API 缺失/错误 team、channel/thread/user/file/parent/client/mention、重复身份、分页/失败/超时均拒绝 | 本地实际图负例；云端 `198402`–`198407` 核对路径 |
| F1-A3 回归与载具自检 | trusted bot/parent 严格门禁及普通文字分支保留；故意绕过 verification、错误接 adhoc 被载具检出 | 无实际 SupportPortal 请求 |
| F1-A4 实际 Slack 只读 | `198408`：真实 auth/channel 一致，不存在的准确消息被拒绝；`198409`：只读旧消息，合成 actor 不匹配被拒绝 | 真实 GET；所有业务端点 pin，未重放旧事件 |
| F1-API-01 | live `/health/release` 返回 `3d60e9a5f4d9d976cd0fcd0d7dda852be44d5045`；该提交的 handler 附件仍要求 trusted bot+parent | 用户明确留待后续；本轮不改 API、不发布 live |

本地命令：根目录 `.venv/bin/python -m pytest backend/tests/test_n8n_human_file_share.py backend/tests/test_investigation_attachments_n8n_contract.py backend/tests/test_n8n_snapshot_identity.py backend/tests/test_n8n_source_only_contracts.py -q`，**146 passed**。Native n8n 节点配置 6/6 valid，原子更新 validationWarnings 为空。旧快照测试的两个未变知识 workflow 继续逐字比较；Slack 更新绑定实际 live/draft ID，保留全部身份冲突负例，移除过期数量而非放宽校验。

旧上传 `F0CBQTEP1S5` 仍在原 thread；只读数据库回读 case/file Map 均 0、namespace 总数 4。本轮未重新上传、未转发原上传事件、未调用附件 API、未手写 Map，未调用 Hermes C3。C2 仍为自然上传失败且未重试，C7 waiting-for-evidence，业务去重未通过重放测试。

允许的下一步：将 PR 完整 HEAD、draft bytes、隔离 execution 和脱敏证据交指定线程独立验收。本轮验收范围只包含 n8n 修复；通过也不授权本轮 live 发布或 API 部署。保留工作区和 Draft PR。

## 历史 v2（2026-10-22）

以下记录历史 metadata/bot 与 WeKnora intake-only 发布范围；不以其中当时的 pending 状态替代上方本轮结论。

## 当前执行合同

- 源码：最新已合并 main `a08be077334b1e6d5a1f2f43ba21216465b89fe9`（PR #1540 metadata 与 PR #1544 bot identity 均已通过独立代码验收）。
- 先通过正式 Fargate schema-bootstrap task 补齐 schema 016 内的 `investigation_case_files.short_description`，保留 Map/文件；再切换 `RUNTIME_SCHEMA_MODE=check` 的服务。revision 相同不能代替列级回读。
- 保留 immutable releases、checkpoint、可刷新 AWS provider、Terraform zero-drift、Prompt、digest、健康与 rollback 门禁；不得隐式应用 subnet 或其他基础设施变更。
- API/Worker 配置 `ENGINEER_SLACK_BOT_ID=B08RHT5TZV2`；文件功能保留当前已启用值 `1`；`HERMES_CASE_WORKFLOW_MODE=disabled`。
- 最终 intake-only release：master 与 `KNOWLEDGE_SOURCE_INTAKE_ENABLED` 为 `1`；Summary/Review、AI execution、dual-write worker、AgentMemory/Slack/WeKnora delivery 均为 `0`；source allowlist 为空。先发布 bot 基线，再复用独立的 intake-only release，以满足共享配置保护门；不改写原 release 身份。
- n8n 仅发布已验收的结构化 Input、可信 `file_share` 条件及 parent/bot metadata；保留远端 `raw_text`、凭据引用、其他节点和连接。每次发布前后保存脱敏 published/draft 快照并校验。
- 只读回读 release/commit/digest、列、全部开关、n8n active 版本与 Hermes image/profile/toolset/Skill；注册或资产存在不等于真实文件读取、Skill 作用域或业务验收。
- 禁止创建 case、上传 Slack 文件、发送 Hermes/Zendesk/Zac 业务消息、重放历史 execution 或触碰 Production。Hermes overlay 不在本次替换范围，读回差异必须记录，不能宣称新桥接已部署。
- 发布完成后交回指定独立验收线程 `01a1349a-c8e9-7183-829d-6b158e735eb5`；原 R1、真实文件、Automation live 反向与 Zac C7 门禁继续保留。

## 目标

修复可信 Slack app `file_share` 事件进入 Investigation 文件登记的结构化元数据链路：父 workflow 显式传递稳定字段，子 workflow 只接受固定 team/channel、受信 bot `B08RHT5TZV2`、非空 `parent_user_id` 和真实 `file_share` 文件事件。SupportPortal 侧继续对 Slack file ID、消息、thread、actor 和来源事件做服务端校验。

文件 Map 增加受控 `short_description` 展示字段。描述最多 240 个字符，拒绝控制字符；Slack 消息文本仅作为受限候选，超限时使用稳定默认描述。重复来源仍由来源系统、case 和 source file ID 幂等去重。

## 根因与范围

execution `197129` / `197130` 的已回读根因是 child 附件条件要求 `!Input.bot_id`，而实际 Slack app `file_share` 事件带有 `bot_id` 和 `subtype=file_share`，因此附件分支不命中。parent 的字符串化输入是需要消除的传输风险，但不是本次已证实的跳过原因。修复显式构造结构化 Input，并将 child 条件绑定到已验证的 app bot 和父消息操作者。不会重传 `F0CAR4LED0B`，不会关闭工单 `14170`。

## 数据与迁移

`investigation_case_files.short_description` 是 schema 016 内的 add-only 字段演进：建表包含字段，已有表通过 `ADD COLUMN IF NOT EXISTS` 补齐，空值记录按 managed filename 回填。没有新增 schema revision，不删除或改写既有 Map 数据。

## 验证

- 历史实现自测：`TICKET_DB_DSN='postgresql://test/test' .venv/bin/python -m pytest -q backend/tests/test_investigation_attachments.py backend/tests/test_investigation_attachments_n8n_contract.py backend/tests/test_investigation_case_files.py`；当时结果 `50 passed, 31 skipped`，4 个既有 FastAPI 弃用警告；不作为当前运行时证据。
- n8n 快照校验：`python3 scripts/n8n/validate_workflow_snapshots.py` 通过；输出中的 `LEGACY-EXPOSED` 仅为既有历史 published 快照。
- 后续独立代码验收（PR #1540，HEAD `dbe0ae1c93dfa1f8cbc80affc396b588f5465f35`）：定向 `44 passed, 30 skipped`；实际 PostgreSQL 14.19 隔离实例 `13 passed`，覆盖 add-only 升级、唯一键、metadata 更新/读取；snapshot validator、编译和 diff check 通过。该代码结论已释放合并门禁，不等于部署/业务验收。
- `py_compile`、`git diff --check` 通过。

## 未覆盖边界

本轮部署/只读回读的实际状态以当前证据文件为准；代码验收和 n8n 内存表达式验证不证明真实 Slack/Zendesk 文件闭环、Hermes 实际文件读取或 Skill 运行作用域、Zac 文件协议。禁止重放历史 execution；Production 需要单独授权。原 R1 和真实业务门禁继续保留。

## 2026-10-22 执行结果（待独立部署验收）

- 正式 schema-bootstrap task :45 exit 0；后续只读事务确认 schema 016 和 `short_description TEXT NOT NULL`。
- 先完成 bot 基线 `r20261022-a08be07`，再完成 intake-only `r20261022-a08be07-intake-only-r6`；API174/Route169/Worker172 的运行 digest、源码、Prompt 和健康回读一致。Terraform 前后 zero-drift，无 apply；正式状态逐项保留：prompt target-state 为 resume 专用项 `not_started`，本次实际 sync/validate/activation active；schema bootstrap 因前置独立迁移完成为 `skipped_current`。最终 pipeline 总计 1377.366s（超过900s SLO），其中部署 phase 汇总768.601s；bot 基线 pipeline 总计1493.168s。`rollback=not_started`，未做回滚演练。
- API/Worker：bot `B08RHT5TZV2`、文件开关1、知识 master/intake=1、其余六开关0、allowlist 空、case workflow disabled；Route 不承载这些开关，保留 check schema。
- n8n parent active `8f5f02d4-5448-45cb-b9f3-3499e7c254e8`、child active `7400713d-c7e3-436d-9e05-fa53d6674fd7`；发布前后快照、版本和表达式校验通过，未创建 execution。
- `HERMES-METADATA-01`：Hermes 保持 td54/7df5f3a3；toolset/资产存在，但 live list schema 缺 description、Skill 未记 short_description。仅回读，不替换 overlay/Profile，不把注册状态等同实际 Skill/session 作用域。
- 未注入 case、Slack 文件、Hermes/Zendesk/Zac 业务消息或知识样本，未重放历史；原 R1、启用文件未覆盖路径、Automation live 反向、Zac 和完整知识业务门禁继续保留。
