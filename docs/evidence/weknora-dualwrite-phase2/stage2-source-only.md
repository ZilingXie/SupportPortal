# 阶段 2 证据 — n8n source-only（p2-194）

- 计划名称：WeKnora 并行双写与人工治理链（阶段二：n8n source-only），修订 r1
- 基线：main 19917249（工作树实际基于 812f6601=19917249+并行 p2-187 线 #1460，无关本任务，已披露）
- 快照契约选择：**A**（接口不变；幂等身份=source_type+source_id+source_updated_at；snapshot_hash 入 references）。按计划推荐采用，交互确认未获答复，已在 PR body 标注"独立验收时可推翻"。
- 采集与变更时间：2026-10-10；n8n 变更仅 Draft，未发布，Active 全程未变。

## C0 线上基线重读与比对（变更前）

n8n MCP 只读重读（本会话工具绑定丢失，经用户配置的 HTTP MCP 端点直连 JSON-RPC 完成同通道调用，凭证不落盘）：

| 链 | active | activeVersionId（与阶段 0 登记/仓库快照一致） | draft versionId（改造后） | 节点数 |
|---|---|---|---|---|
| [kb]Build\|CSD `GgDxPEWtW7ltT5BW` | true | `b5cf6d6b`（未变） | `5c3359cc`（source-only） | 12 |
| [kb]Build\|Solved Cases `MM3Z3T469Eru3Q1I` | true | `1f544830`（未变） | `6a262f9a`（source-only） | 8 |

- 冻结基线（改造前全图+连接+凭据引用+图哈希）：`stage2/baseline-csd.json`（graph sha256 `8066d6d4…`）、`stage2/baseline-solved.json`（`4dac4380…`）。
- C0 合同：线上版本与阶段 0 登记完全一致后才动手；变更后回读确认 `activeVersionId` 两条均未变（上表）。

## C1 source-only 主链（Draft，未发布）

- **Solved**（8 节点）：Ticket_Closure_Trigger → If(SOLVED) → Get Ticket Snapshot → Get All Comments（内置分页）→ Validate Snapshot Completeness（原有节点，已含 next_page=null+数量校验）→ **Build knowledge-source-v1**（新）→ **Deliver Source Snapshot**（新，POST `https://support.stellarix.space/automation/preproduction/v1/knowledge/sources`，凭据 `preprodcution`(httpHeaderAuth)，retryOnFail×3）→ **Verify Receipt**（新，三态+非空 task_id，失败 throw→errorWorkflow `dV5vNA6l1MbDMHZt`）。删除 24 节点：AI_Approval/AI_Filter/三 LLM 生成节点/Mask/Conversation/restructure/Remove_blank/Aggregate/Edit Fields/Loop Over each comment/HTTP Request(users)/Execution Data×3/Update_DB1（本地 PG 去重）/If1/2_tencent×3（Wiki 直写）/2_rag/HTTP_Create_KB/Append row in sheet。
- **CSD**（12 节点）：Schedule Trigger → get_access_token → Query_CSD_list（JQL 保持）→ Url_list → Loop Over Items → Get_CSD_Detail（`fields=summary,description,updated,created,status,resolution,comment,…` 保持）→ **Validate CSD Snapshot**（新：fields.comment 存在+数组≥total，缺失即 throw）→ **Build csd_issue Snapshot**（新）→ Deliver → Verify Receipt → 回 Loop；done→Scan Complete。删除 19 节点：四 LLM 节点/Structure_comment/structure/Update_DB1（csd 本地去重）/If/If1/Aggregate/Edit Fields/Execution Data×2/2_tencent×3/2_rag/HTTP_Create_KB/Split Out。
- 回读证据：`/tmp/*_draft_readback.json` 的判定输出已嵌入本文件表格与测试断言；仓库快照（drafts/*.draft.json）即由改造后回读原文生成，与线上一致。
- 快照：`docs/integrations/n8n/workflows/drafts/{GgDxPEWtW7ltT5BW,MM3Z3T469Eru3Q1I}.draft.json`（manifest 标记 `draftSourceOnly: true`；CSD 快照含 3 处 secret 字段脱敏+ledger）。

## C2/C3/C4 合同验证（离线，真实执行 Code 节点 JS）

`backend/tests/test_n8n_source_only_contracts.py`：18 passed。用 node v20.14.0 经 n8n shim（$input/$()/crypto）执行**仓库快照中的实际 jsCode**，合成 fixture（零真实业务数据）：

| 场景 | 结果 |
|---|---|
| Solved 完整分页→建快照 | 产出通过 SupportPortal `KnowledgeSourceSnapshot`（extra=forbid）真实模型校验；snapshot_hash 64 位入 references；同输入重放哈希一致 |
| Solved 评论数不足 | throw "Comment pagination incomplete: got 3, expected 5"（不投递） |
| Solved next_page 未结束 | throw "next_page still present"（不投递） |
| CSD 完整 comment→建快照 | 通过 v1 模型校验，comment_count=total |
| CSD 缺 fields.comment | throw（不投递） |
| CSD 评论数<total | throw（不投递） |
| 三态回执 accepted/already_exists/stale_ignored+task_id | Verify Receipt 全过 |
| 未知 status/空 task_id | throw（→错误告警路径） |
| 重投/乱序（C4） | 真实 InMemory 仓库实测：同三元组重投=already_exists、旧版本=stale_ignored，且两种回执都能过 Verify Receipt 的实际 JS |
| 直写禁令 | 校验器+测试双重断言：两 Draft 无 Wiki/WeKnora/engineer-knowledge 引用、节点类型全在允许清单、URL 前缀全在允许清单（含动态表达式前缀） |

## 校验器加固（交付物）

`scripts/n8n/validate_workflow_snapshots.py`：新增端点闭合检查、source-only 节点类型允许清单、URL 前缀允许清单、直写 URL 禁令；legacy 快照同类问题降级为 `LEGACY-EXPOSED` 警告（本次实测暴露 29 项历史问题：Solved published 快照存在 p2-183 残留悬空端点 Check Delivery Receipt 等+两链 Wiki 直写 URL）；`draftSourceOnly: true` 快照全部硬校验。当前输出：15 published + 3 divergent drafts + 56 redactions，退出码 0。

## waiting-for-evidence（真实执行证据）

n8n 侧真实 execution 证据（execution ID→投递→回执→快照 hash 对应链）**未采集**，登记为 waiting-for-evidence，理由与恢复条件：

- 采集会触发真实 Zendesk/Jira 来源读取（违反"不重放真实业务来源"），且 Preproduction 接收开关关闭（阶段 2 不启用治理开关），真实投递必然 503。
- 恢复事件：阶段 6 发布顺序第 3-4 步（发布 n8n source-only 并验证只投快照）获得授权后，以自然调度（CSD 每日 12:00 北京）或授权受控样本补齐：n8n execution ID、source receipt、snapshot_hash 对应链。

## 其他命令验证

`validate_workflow_snapshots.py` ✓（上）；`generate_project_overview.py --check` ✓；`git diff --check` ✓；`test_n8n_source_only_contracts.py` 18 passed ✓。
