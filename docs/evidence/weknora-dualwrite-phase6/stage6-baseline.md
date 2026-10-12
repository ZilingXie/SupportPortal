# 阶段 6 证据 — 基线冻结（p2-197，步 1 只读）

- 计划名称：WeKnora 并行双写与人工治理链（阶段二），修订 r2-stage6；授权=用户方案 A（2026-10-12 确认：七点按建议+先补 KNOWLEDGE_SOURCE_ALLOWLIST）
- 基线 main：c6addcc0（干净、与 origin 同步）；采集时间 2026-10-12；全程只读

## 运行态基线（AWS 只读实测）

| 项 | 当前值 |
|---|---|
| api | task def **:135**，release r20261011-434ee35-p2（git 434ee356…），HERMES_KNOWLEDGE_WORKFLOW_ENABLED=0 |
| worker | task def **:135**，image digest sha256:4b7d3582…，release 同上；HERMES_KNOWLEDGE_WORKFLOW_ENABLED=0、WEKNORA_PROMOTION_ENABLED=0（知识面仅此两个 env，无任何 KNOWLEDGE_*/AGENT_MEMORY_* 注入） |
| route | task def **:134**，release 同上 |
| hermes | task def **:45**（image digest sha256:0d75c8db…） |
| worker env 总数 | 40（含既有全部 env；治理/知识面基线如上） |

**治理开关基线（api+worker 一致）**：HERMES_KNOWLEDGE_WORKFLOW_ENABLED=0；WEKNORA_PROMOTION_ENABLED=0；六个独立开关（SOURCE_INTAKE/SUMMARY_REVIEW/DUALWRITE_WORKER/AGENT_MEMORY_DELIVERY/WEKNORA_DELIVERY/SLACK_NOTIFY）**均未注入**（代码默认关）。

## SSM 基线

- `/supportportal/preproduction/` 前缀下无任何 WEKNORA_* / AGENT_MEMORY_WIKI_* / KNOWLEDGE_* 参数（0 命中）——与阶段 0 结论一致，阶段 6 需新建。
- 既有相关参数（只记名）：hermes-wiki-core-user-key（AgentMemory Wiki API 用户密钥，hermes 侧已用）、engineer-slack-access-token（v1）、automation-intake-shared-token、n8n-request-token。
- WeKnora 自身参数在 `/supportportal/weknora/*`（12 个，不动）。

## n8n 基线（只读回读）

| 链 | active | activeVersionId | draft versionId | 状态 |
|---|---|---|---|---|
| [kb]Build\|CSD `GgDxPEWtW7ltT5BW` | true | `b5cf6d6b`（p2-186 AgentMemory 直写链） | `6fed47a6`（source-only，R1 修复后） | 分歧待发布 |
| [kb]Build\|Solved `MM3Z3T469Eru3Q1I` | true | `1f544830`（p2-186） | `6a262f9a`（source-only） | 分歧待发布 |
| [slack]Forward Thread `r1HIW8UNuCabiOPn` | true | `b02f3ed5` | `4ed2c0c3`（阶段 4 raw_text+bot_user_id） | 分歧待发布 |

三条 draft 均为本项目各阶段交付的待发布版本；快照已在仓库（15 published+4 drafts+56 redactions，validator 通过）。

## 已知限制

- 公网 release/health 端点在采集时不可达：supportcenter.stellarix.space 经本机 DNS 解析为 fake-ip（198.18.0.71）且返回错误证书（CN=github.com）——本机代理 fake-ip 模式问题，非服务端故障（n8n 域名同法正常）；release 标识改由 task definition env（AUTOMATION_RELEASE_ID/APP_BUILD_REF）取得，三方一致 r20261011-434ee35-p2。
- 线上 DB schema 版本待步 2 部署时随 v23 migration 落地（当前线上为 v21 前版本；v22/v23 未部署）。
