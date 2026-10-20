# stage6-csd-governance-cap-10 r1 — Draft 制作与复核记录（2026-10-20）

**状态：仅 Draft，未发布。** 发布/自然执行/治理开关开启=后续单独授权。

## 基线（修改前）

- CSD workflow `GgDxPEWtW7ltT5BW` 线上回读：顶层 `versionId=86f9fb06`（=当前 Draft）；`activeVersionId=6511c2e9`（=线上 Active，回滚基线）。仓库 active 快照与 6511c2e9 的 12 节点图一致。
- 节点图 12 节点；Url_list（Code，issues→{index,url}）→ Loop Over Items（batchSize=1）；JQL `updated ASC`。

## Draft 变更（versionId **86f9fb06**，activeVersionId 仍=6511c2e9）

新增 1 节点：`Cap 10 sources (stage6-csd-governance-cap-10)`（n8n-nodes-base.limit v1，maxItems=10，位置 Url_list 与 Loop 之间）。连接重接：Url_list→**Cap**→Loop Over Items（原 Url_list→Loop 已移除）。

- **保序**：JQL updated ASC + Limit 取前 10=最旧优先 10 条；<10 全过；Url_list 的 index 编号保留（execution 证据核对用）；第 10 条后 Loop 走 done→Scan Complete，第 11 条永不入环。
- **未动**：SP source endpoint、Jira 查询/字段/完整性校验、snapshot 格式与 hash、三态 receipt 校验、error workflow、凭据、Solved workflow。

## 静态验证

- validator 通过（15 published+4 drafts+56 redactions；白名单新增 n8n-nodes-base.limit——仅放行该类型，maxItems 由快照承载）。
- 快照同步：draft 文件+manifest（draftVersionId/candidateBase）+candidate 文件全部锚定 86f9fb06。
- 图检查：13 节点、单一 source endpoint（Deliver→supportcenter.../knowledge/sources）、无 Wiki/旧 KB/WeKnora 直写节点、Limit maxItems=10、done 路径→Scan Complete。

## 前置检查（计划要求保留）

线上知识治理=全关闭（master=0 七开关 0，r20261019-21c6eed）。本 Draft 仅限进入 intake 的数量；AI/Slack/WeKnora 开启需另行 Preproduction 配置发布（运行态变更，不在本 n8n 修改内）。

## 停止条件（沿用计划）

>10 条/未审批即写 WeKnora/Slack 链接缺失/reject 仍写入/重复写入/回读版本 hash 不一致/active 与预期不一致/endpoint 漂移。

## 快照对齐修复（2026-10-20，B1/B2/B3）

根因：2026-10-13 的 D1-D4 n8n 链修复只推在未合入 main 的旧分支（origin/codex/stage6-deploy@dbcae60a），main 的快照仍是修复前状态。本轮以 dbcae60a 的 D 修复后 draft 为基（含纯 JS SHA-256+supportcenter 端点+bearer 凭据+显式 Jira 字段）重建四件套：

- **active**：版本锚定 6511c2e9（source-only 12 节点，supportcenter+httpBearerAuth automation_production，无 Wiki/旧 KB 直写——B1）；
- **draft**：86f9fb06=active+Limit 节点+重接（endpoint/凭据同 active——B2）；
- **candidate**：local_candidate 结构（含 Limit maxItems=10+Url_list→Cap→Loop 重接+现行 endpoint/认证+publicationStatus=unpublished_local_candidate，不携带远程版本声明——B3）；
- **manifest**：published=6511c2e9/draft=86f9fb06/candidateBase=86f9fb06/nodeCount=12。

验证：validator 通过（15+4+56）；**B1/B2/B3 断言脚本全过**（active 版本+端点+凭据+无直写；draft 端点+凭据+版本；candidate 含 Limit+重接+端点+凭据；三文件均单一 source endpoint）。validator 局限（内部一致性≠线上一致性）已由断言脚本补足——断言对照的是线上回读值（6511c2e9/86f9fb06/supportcenter/bearer）。

## 独立验收通过（2026-10-20，HEAD 0cd6752a）

Draft 验收通过：三份快照与线上回读一致（Active 6511c2e9/12 节点；Draft 86f9fb06/13 节点含 Cap 10）、endpoint/认证/重接/未发布标记/manifest 元数据全部确认；validator 15+4+56 通过；B1/B2/B3+元数据断言通过；两树干净。validator 的 13 条 LEGACY-EXPOSED 属历史 Solved 快照与本 Draft 无关（复核确认）。

**验收边界**：仅释放 Draft 验收门。发布、自然每日执行、治理开关开启=三个后续单独授权，均未释放。
