# Investigation Slack ingress metadata v1

状态：实现完成，等待独立验收。仅包含代码、n8n 版本化快照和本地测试；未部署 Preproduction/Production，未重放历史 execution，未修改真实客户数据。

## 目标

修复可信 Slack app `file_share` 事件进入 Investigation 文件登记的结构化元数据链路：父 workflow 显式传递稳定字段，子 workflow 只接受固定 team/channel、受信 bot `B08RHT5TZV2`、非空 `parent_user_id` 和真实 `file_share` 文件事件。SupportPortal 侧继续对 Slack file ID、消息、thread、actor 和来源事件做服务端校验。

文件 Map 增加受控 `short_description` 展示字段。描述最多 240 个字符，拒绝控制字符；Slack 消息文本仅作为受限候选，超限时使用稳定默认描述。重复来源仍由来源系统、case 和 source file ID 幂等去重。

## 根因与范围

execution `197129` / `197130` 的 parent Execute Workflow 使用字符串化输入，child 又要求 `!Input.bot_id`，导致 Slack app `file_share` 附件分支被跳过。修复关闭字符串化，显式构造结构化 Input，并将 child 条件绑定到已验证的 app bot 和父消息操作者。不会重传 `F0CAR4LED0B`，不会关闭工单 `14170`。

## 数据与迁移

`investigation_case_files.short_description` 是 schema 016 内的 add-only 字段演进：建表包含字段，已有表通过 `ADD COLUMN IF NOT EXISTS` 补齐，空值记录按 managed filename 回填。没有新增 schema revision，不删除或改写既有 Map 数据。

## 验证

- 定向测试：`TICKET_DB_DSN='postgresql://test/test' .venv/bin/python -m pytest -q backend/tests/test_investigation_attachments.py backend/tests/test_investigation_attachments_n8n_contract.py backend/tests/test_investigation_case_files.py`；结果 `41 passed, 29 skipped`，4 个既有 FastAPI 弃用警告。
- n8n 快照校验：`python3 scripts/n8n/validate_workflow_snapshots.py` 通过；输出中的 `LEGACY-EXPOSED` 仅为既有历史 published 快照。
- `py_compile`、`git diff --check` 通过。

## 未覆盖边界

本轮不证明 Preproduction 运行时回读、真实 Slack/Zendesk 闭环、Hermes 文件 Skill、Zac 文件协议或历史 execution 重放。完成独立验收后仍需由获准流程决定是否发布；Production 需要单独授权。
