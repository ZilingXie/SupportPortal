# stage6-csd-governance-cap-10 r1 — Draft 制作与复核记录（2026-10-20）

**状态：仅 Draft，未发布。** 发布/自然执行/治理开关开启=后续单独授权。

## 基线（修改前）

- CSD workflow `GgDxPEWtW7ltT5BW`，active=versionId=**6511c2e9**（与仓库快照逐字一致，回读验证）；回滚版本=6511c2e9。
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
