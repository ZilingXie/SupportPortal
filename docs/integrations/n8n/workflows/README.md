# n8n workflow Git snapshots

本目录保存个人 n8n 项目中所有启用工作流的版本化恢复基线。它补充 n8n 自带的版本历史：n8n 仍是流程编辑与执行事实来源，Git 提供可审查的节点图、版本 ID 和脱离 n8n 保留期的结构备份。

## 目录约定

- `manifest.json`：当前启用清单、workflow ID、已发布版本 ID、草稿版本 ID、更新时间和文件位置。
- `active/<workflow-id>.published.json`：每条启用工作流的已发布恢复基线。
- `drafts/<workflow-id>.draft.json`：仅当草稿与已发布版本不同时保存，不能当作线上恢复基线直接发布。

2026-09-16 第一批修复后的初始快照保留在 Git 历史中；当前基线包含 16 条启用工作流和 1 份未发布草稿。快照来自 n8n MCP `get_workflow_details` 的安全化结果，不包含 execution、pin data 或客户输入。n8n 返回的 credential ID/name 会保留，明文 Authorization、Cookie、token、secret、API key、私钥和邮箱会替换为 `__REDACTED__` 类占位符，并记录在每个文件的 `restoreNotes.redactedValues` 中。

## 修改流程

1. 修改远端 n8n 前，先按项目 ID 读取最新启用清单和目标 workflow 的 full details，记录 draft/published version ID。
2. 刷新目标的 `published.json`；若 draft 不同，同时刷新 `draft.json` 和 `manifest.json`。不得写入 execution、pin data、明文凭据或客户数据。
3. 运行 `python3 scripts/n8n/validate_workflow_snapshots.py`。校验通过后，才修改、发布或取消发布远端 workflow。
4. 修改后再次回读实际 draft/published 版本并刷新快照。Git diff 应只包含预期节点、连接、settings、description 或版本元数据变化。
5. workflow 启停时同步增删本目录文件和 `manifest.json`；未启用工作流仍只在运维清单中维护，除非另行决定纳入备份。

## 恢复顺序

1. 停止继续发布或重放 execution，确认该 workflow 已经产生的外部写入和去重状态。
2. 同一 n8n 实例优先使用 `manifest.json` 中的 `publishedVersionId` 调用版本恢复，再比较当前 draft、恢复版本和 Git 快照。恢复版本后仍需单独发布；恢复本身不授权重跑历史 execution。
3. n8n 版本历史已被清理时，用对应 `published.json` 的 `workflow` 对象重建一条未启用副本。重新绑定 credential，并逐项补齐 `restoreNotes.redactedValues`，完成节点校验和无副作用检查后再替换原流程。
4. 有 divergent draft 的 workflow 必须保留 draft，不得在恢复已发布版本时顺带发布。当前这类流程是 `[slack]Route Support`。

Git 快照不是密钥备份。需要依靠 n8n credential store、SupportPortal SSM 或密钥管理系统恢复凭据；禁止为了“一键恢复”把明文密钥提交到仓库。
