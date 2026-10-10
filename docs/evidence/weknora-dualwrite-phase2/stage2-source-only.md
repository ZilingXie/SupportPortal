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


## 修复轮 R1（2026-10-10，阶段 2 首轮验收两阻断后）

| 阻断 | 修复 | 回归 |
|---|---|---|
| CSD total 缺失/非数字回退 comments.length（未 fail-closed） | Validate CSD Snapshot 的 jsCode 改为 `typeof comment.total !== 'number'` 即 throw（快照契约第 61 行"数值型 total 必须齐备"）；线上 Draft 经 setNodeParameter 更新并回读确认 | test_csd_missing_or_non_numeric_total_fails_closed ×3（缺失/字符串/数组全 throw，消息含 "total missing or non-numeric"） |
| Get_CSD_Detail 请求固定字段列表而非完整快照 | queryParameters 改为 `fields=*,comment`（契约第 36 行"完整 Jira issue 对象"）；首次 replace 误清 url/sendQuery 的即时修正已回读确认（url/认证/jira_zac 凭据完整、validationWarnings=[]） | test_csd_detail_requests_the_full_field_set（断言 fields=*,comment+url 表达式完整） |

- 修复后 CSD Draft 版本 `6fed47a6`（回读确认 activeVersionId=b5cf6d6b 未变）；仓库快照由回读重建（3 处 secret 脱敏+ledger 保持）。
- 契约选择：A（两轮评审一致推荐，继续采用；PR 披露可推翻）。
- 验证：合同测试 **22 passed**（18+4 新回归）；快照校验 15+3+56 退出码 0；overview --check、git diff --check 通过。


# 阶段 3 证据 — Summary/Review 双侧检索（p2-194，同 PR 延续）

- 范围：Review 证据面加入 AgentMemory 检索结果（计划 r1 §四）；Summary 结构不变（既有 structured packet）。

## AgentMemory 检索 API 实证（2026-10-10，只读探测）

经公共面板 API（X-Tdai-Service-Id + X-Tdai-User-Key，密钥即取即用不落盘）实证：

- `POST /api/v1/knowledge/wiki/list {"team_id","limit","offset"}` → `data{items[{wiki_id,name,status,version,page_count,summary,…}],total}`（实测团队 93 wiki，limit=100 全量返回）；
- `POST /api/v1/knowledge/wiki/search {"wiki_id","query","top_k"?}` → `data{count,results[{title,snippet,score,path,type,hop}]}`（实测命中 13 条）；
- 无全局搜索端点（/knowledge/search 等 404 实证）——检索=fan-out：list 全部 ready wiki（上限 100）+逐 wiki search（top_k 5）。

## 实现

- `AgentMemoryWikiClient` 新增读面：`wiki_list`（items/total 校验，invalid_response fail-closed）、`wiki_search`、`search_knowledge`（ready-only fan-out，任一失败整体抛错=面不可用）。
- `_collect_agent_memory_evidence`：每候选用 statement（≤256 字符）检索；未配置/任一失败→面不可用（与 WeKnora 面同语义）。
- 两条 review 路径（case 绑定 + standalone）bundle 均新增 `agent_memory{available,results,wiki_count,searched}`；`_downgrade_decisions_without_evidence` 新增 `agent_memory_available`：可写决策需 WeKnora 各面 AND AgentMemory 全应答，理由区分"WeKnora 与 AgentMemory 均不可用"/"AgentMemory 不可用"；skill 降级不变。
- worker 两个 drain 注入 `AgentMemoryWikiClient()`（未配置即 fail-closed 降级，运行态零变更：Preprod 未配 AGENT_MEMORY_WIKI_* 时检索面恒不可用=现状更保守）。

## 验证

- 新测试 13 项：客户端读面 5（list 校验/search 归一/ready-only/失败传播/缺 total fail-closed）+ 降级 4（AM 不可用双侧类型降级/双侧应答存活/双侧不可用合并理由/skill 独立）+ 采集器 3（hits/未配置/中途失败）+ standalone bundle 面 1（available+results 断言+AM 不可用变体降级）。
- 存量适配 3 处（e2e/sessions/legal-empty 注入 AM 假客户端——新合同下"可写存活"必须双侧应答）。
- 全套回归 **444 passed / 0 failed**（RUN_POSTGRES_INTEGRATION=1，16 套件）。


## 阶段 3 修复轮（2026-10-10，验收一项阻断后）

阻断：AM 读面三处 fail-closed 缺口+一处死循环风险（`wiki_search` 未校验 count 与元素结构、`search_knowledge` 静默跳过非对象元素、`results=[null]`/缺 count 被当作"无命中"使面保持可用、空页+total>0 时 offset 不前进）。

修复（全部任一异常→`invalid_response`→整面不可用→可写决策降级 human_review）：

1. `wiki_search` 严格校验 `count`（数值型且 ≥0）与 `results` 每个元素为对象；
2. `search_knowledge` 不再静默跳过（元素校验前移至 `wiki_search`，malformed 即抛）；
3. 分页无进展防护：空页且未达 total → 立即 `invalid_response`（不再死循环）。

回归 5 项（全部复现验收场景）：缺 count / 非数值 count / `results=[null]` / 空页+total=5 首页即抛且仅一次调用 / malformed 端到端链（面不可用→`new` 降级 human_review、理由含 AgentMemory evidence unavailable）。

验证：全套 **449 passed / 0 failed**（16 套件含隔离 PG）。


## 阶段 3 修复轮 2（2026-10-10，第二轮验收一项同类阻断后）

阻断：Python `bool` 是 `int` 子类——`isinstance(count/total, int)` 会放行 JSON `true/false`，实测 `count=true` 返回成功空结果、`total=false` 得到 `available=True` 的空检索面。

修复：`wiki_search.count` 与 `wiki_list.total` 显式拒绝 `bool`（先 `isinstance(x, bool)` 后 `isinstance(x, int)`），malformed 布尔响应即 `invalid_response` → 面不可用 → 可写降级。

回归 2 项（count=true/false、total=true/false 四形态全部拒绝）。验证：AM 套件 28 passed；全套 **451 passed / 0 failed**（16 套件含隔离 PG）。


## 阶段 3 修复轮 3（2026-10-10，第三轮验收一项同类阻断后）

阻断：`wiki_list.total` 未拒绝负数——实测 `total=-1/-100` 使 `search_knowledge` 返回 wiki_count=0/searched=0/hits=[] 且面保持可用。

修复：`total < 0` → `invalid_response`；并主动收口同族最后一处解析缺口——`items` 每元素必须为对象（`items=[null]/["x"]/[42]` 拒绝，避免下游 AttributeError 形态的延迟失败）。至此 `wiki_list` 与 `wiki_search` 的响应自证完整：total/count 非布尔、非负、真整数；items/results 逐元素对象。

回归 3 项（负数 total 两形态、非对象 items 三形态、负 total 端到端面不可用链）。验证：AM 套件 31 passed；全套 **454 passed / 0 failed**（16 套件含隔离 PG）。


## 阶段 3 修复轮 4（2026-10-10，第四轮验收一项同族阻断后）

阻断：对象形但缺必需字段的条目仍放行——`items=[{}]/[{"status":"ready"}]/[{"wiki_id":"w1"}]` 得可用空检索面（wiki 被跳过）；`results=[{}]` 归一化为空命中（伪证据）。

修复：必需字段校验——inventory 条目必须有非空字符串 `wiki_id` 与 `status`（身份+生命周期，缺一则该 wiki 无法被检索/分类，面必须失效）；搜索结果必须有非空字符串 `title` 与 `path`（verified 合同中每个命中都携带的内容字段，缺失即伪证据）。非字符串类型（数字/布尔/空白）同样拒绝。

回归 4 项+扩展变体：四形态（`[{}]`/`[{"status"}]`/`[{"wiki_id"}]`/`results=[{}]`）+ 非字符串/空白字段变体 + 端到端（contentless result → 面不可用 → `new` 降级 human_review）。验证：AM 套件 35 passed；全套 **458 passed / 0 failed**（16 套件含隔离 PG）。

至此读面响应自证五层闭环：类型（对象）→ 数值（非布尔/非负/真整数）→ 元素结构（逐元素对象）→ **必需字段（身份/状态/内容）** → 分页进展。
