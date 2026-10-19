# stage6-weknora-agent-bridge r1 — 步 0 实时基线冻结（2026-10-19，全程只读）

工作树：.worktrees/stage6-weknora-agent-bridge（分支 codex/stage6-weknora-agent-bridge，自 main 5bfbd86a 创建）。

## 运行态

| 项 | 值 |
|---|---|
| release | **r20261019-15f3602-argus-zac-r1**（APP_BUILD_REF=15f3602c，今日 11:52 由并行线部署） |
| api | task def **:159**，digest sha256:2388e4fa…，服务运行 :159（1/1） |
| worker | task def **:158**，digest sha256:f8d474d9…，服务运行 :158（1/1） |
| route | task def **:155**，digest sha256:11fb8e8b…，服务运行 :155（1/1） |
| hermes | task def **:46** |
| Prompt release | **pr-88298d4e0a3f**（三角色一致；已从 pr-9737f67803f1 更换） |
| AGENT_MODEL_ID | gpt-6.1-sol |
| HERMES_CASE_WORKFLOW_MODE | disabled（api/worker） |

治理开关（api+worker 一致）：master=**1**、intake=**1**、summary_review=**1**、worker/delivery×2/slack=**0**、allowlist=csd_issue:CSD-79923,csd_issue:CSD-79994；**并行线新增第七开关 KNOWLEDGE_AI_EXECUTION_ENABLED=0**（我的步 2 渲染器时代不存在——步 2 实现须基于现行 main 重读 renderer/runtime）。注意：mode=disabled 下 standalone 建任务门仍关。

## SSM（知识面 8 参数，版本不变）

agent-memory-wiki-base-url/-service-id/-team-id（v1）、knowledge-slack-workspace-domain（v1）、weknora-base-url/knowledge-base-id（v1）、weknora-api-token（SecureString v1）、weknora-api-contract-json（**v4**）。

## DB 计数（supportportal_preproduction）

intakes=**292**（10-13 后持续自然到达）、summaries=**42**（+6）、reviews=36、promotions=64（全 human_review，历史保留）、deliveries=**0**、v23 Slack 六列在位。

## n8n（与 10-13 修复版一致，无新 draft）

CSD active=6511c2e9、Solved active=d4c50a10、Forward Thread active=4ed2c0c3（均 versionId==activeVersionId）。

## Terraform 1.9.8 refresh-only plan（preproduction）

exit=2：**唯一漂移=三个 aws_ecs_service.account 的 task_definition 指针**（state api:118/route:117/worker:118 → 实际 :159/:155/:158）；无任何其他资源属性变化——发布流任务定义指针滞后为已知形态，非基础设施配置漂移。

## 并行发布者与凭证

- 无进行中的发布进程（ps 零命中）；最近发布=今日 11:52（argus-zac-r1）。
- 并行线工作树 ~/.codex/worktrees/stage6-preprod-release（分支 codex/stage6-preprod-baseline，HEAD f10b28d4 "docs: pause stage6 continuation after Hermes rollout failure"，**零脏文件**）——其自述"Hermes rollout 失败后暂停"。今天 11:52 的发布来自其后续（argus-zac r1，即 main 15f3602c/PR#1511 的正式部署）。
- AWS：identity 正常（891612554546/Zac/login 可刷新 provider），无 OAuth 限流迹象。
- 我的旧 origin/codex/stage6-deploy 分支保留（止于 dbcae60a），工作树已清。

## 停止条件评估

| 条件 | 结果 |
|---|---|
| 仍有并行发布 | **无**（零进程；最近发布已完成 4+ 小时） |
| release 与 task definition 不一致 | **一致**（三角色 APP_BUILD_REF/release id/digest 同属 r20261019-15f3602，服务运行=最新 revision） |
| Terraform 有漂移 | **非配置漂移**（仅 task def 指针滞后，已知发布形态） |
| AWS OAuth 限流未恢复 | **正常** |
| 线上状态无法证明属于当前发布 | **可证明**（三方一致：task def env ↔ 服务运行 revision ↔ .deployments/ecs-pipeline-r20261019-15f3602-argus-zac-r1） |

**结论：五项停止条件全部不触发，步 0 通过，可进步 1。**

## 步 1：WeKnora 写入/发布/索引合同确认（fork 源码只读核实，2026-10-19）

| 能力 | fork 侧 | 合同 v4 侧 | 结论 |
|---|---|---|---|
| create/update | ✅ POST /knowledge-bases/:id/knowledge/manual、PUT /knowledge/manual/{id} | ✅ 已有 | 已具备 |
| publish | ✅ ManualKnowledgePayload.Status ∈ {draft,publish}（types/knowledge.go:90-91）；create（knowledge_create.go:819-850）与 update（:1011-1095）均处理 publish→ParseStatus=pending+异步索引任务 | ❌ body 模板无 status 绑定（恒 draft） | **需补合同** |
| index/ready 回读 | ✅ 生命周期=pending→processing→**completed**（types ParseStatus*）+ 解析完成置 EnableStatus=**enabled**（knowledge_process.go:152/241）；draft 恒 disabled 且不入索引；GET 回读含 parse_status/enable_status/metadata.version/metadata.lineage | ⚠️ read 的 receipt 保留全 payload 可达但未提为合同字段 | **需补合同** |
| full content/version/lineage 回读 | ✅ data.metadata.content/version/lineage（governance lineage 落 manual metadata，阶段 2 已实证） | ✅ 已有（content_key/version_key 在位） | 已具备 |

**生命周期定义（步 2/7 合同基础）**：ready = `parse_status=completed ∧ enable_status=enabled`；draft/未索引/未知 = 不可进 Agent 上下文（C3/C8）。发布路径=创建或更新时 status=publish（单次调用即触发异步解析+索引）；ready 为异步 → 需有界轮询回读（超时 fail-closed，C10）。

**判定：当前合同无 publish/index 显式能力 → 按计划步 1 停止条件执行：先补合同与适配器，不猜 endpoint、不投业务样本。**

## 合同 v5 + 适配器设计（步 2 实施蓝本）

1. **合同 v5**（SSM weknora-api-contract-json 升 v5）：create/update body 增 `"status": {"$":"status"}` 语义绑定；read 增根级 `parse_status_key=data.parse_status`、`enable_status_key=data.enable_status` 显式提取。
2. **适配器**（weknora_promotion_adapter）：delivery 写入带 status=publish（新计划方向取代 draft 保持）；写后首次回读复用为轮询第 1 次，适配器实际调用有界 readiness 轮询（read→parse_status=completed∧enable_status=enabled），超时/传输故障为 outcome_unknown，明确解析失败为 failed，均 fail-closed。
3. **统一检索服务**（新共享模块，Review 与 Agent 共用避免双实现漂移）：WeKnora hybrid-search（索引面=仅已发布对象，结构性满足 C3/C8）→ 逐 hit 按 ID readback → 严格结构校验（五层）→ 提取 object_id/metadata.version/metadata.lineage（source_type/source_id/candidate/promotion）→ 组装 evidence；`retrieval_source=weknora|agent_memory_fallback|escalate_human` 标记；Agent 侧入口=engineer_execute_agent.py internal_knowledge_search（现消费旧 rag_result）。
4. 待实现时核实现行 main 的渲染器/运行时（并行线已新增第七开关 KNOWLEDGE_AI_EXECUTION_ENABLED——步 2 实现基于 5bfbd86a 重读，不沿用旧假设）。

## 步 2 实施侦察（现行 main 5bfbd86a 实读，2026-10-19）

- **入口现状**：engineer_execute_agent.py:367 `run_internal_knowledge_search_subagent(task, summary_packet, now_value)` 为 v1 占位——仅消费 summary_packet.rag_result（clues/candidate_answer/sources/citations），无真实检索；结果信封 `_make_result(task_id, skill, status, summary, evidence_refs, missing_information)`，status∈{succeeded,blocked}。改造点=此函数+其调用方（dispatcher 注入路径，续做首查）。
- **第七开关已并入**：knowledge_dual_write.py 现 7 开关（新增 KNOWLEDGE_AI_EXECUTION_ENABLED=Summary/Review AI 消费）；renderer KNOWLEDGE_GOVERNANCE_SWITCHES 同步 7 项且依赖 AI_EXECUTION→SUMMARY_REVIEW（automation_ecs_deploy.py:200/227）。阶段配置入口与 verify 门沿用。
- **底层客户端**：hermes_weknora.py HermesWeKnoraClient（search+read，硬编码 X-API-Key，search 响应严格 data-list 校验已在）=检索服务底层；weknora_client.py 合同驱面（v4 五操作）=补 status/parse_status_key/enable_status_key。
- **检索服务落位**：新建 backend/services/weknora_knowledge_retrieval.py（共享，Review 与 Agent 共用）；Agent 侧 evidence 字段按计划 C4（retrieval_source/weknora_object_id/content_version/content_hash/source_type/source_id/evidence）。

## 步 2 完成状态与修复后独立验收准备（2026-10-19，整合 HEAD 82fdf70e）

已实现并提交（全部内容经 git 提交保全）：B1 修复（status 未提供时省略字段不上 wire）；B2 wire 级回归 9 项（publish 携带/v4 无绑定 fail-closed 零外发/非法值拒绝/省略验证/read 回执 lifecycle 提取/readiness 九参数矩阵）；适配器 publish_on_write+requested_status+lifecycle_at_accept 回执+实际接线的 weknora_wait_until_ready 有界轮询（首次回读复用为 poll #1，超时/传输故障 outcome_unknown、parse failed 快败为 failed、5xx 瞬态重试）；共享检索服务 weknora_knowledge_retrieval.py（C4-C8：ready 过滤/lineage 提取/逐命中 malformed readback fallback/降级仅限 transport+timeout+auth+5xx+malformed/空结果不降级/双侧不可用 escalate）+Agent 桥（run_internal_knowledge_search_subagent WeKnora 优先+escalate 转 blocked+无配置保持 v1 行为）。

**初次提交的验证状态（历史记录）**：client 49 passed、adapter+worker+PG-skip 101 passed；检索服务+Agent 桥 14 项测试当时因 Desktop/iCloud 路径的 inode 级文件系统异常未能收集。内容由 040bbd35 保全；该环境阻断已在本轮健康工作树中解除并复跑，当前结果见下方修复轮记录。

**修复后状态（2026-10-19）**：F1 模块导入、F2 逐命中 malformed readback fallback、F3 适配器 readiness 接线均已修复；新增回归与知识面全套已在本机健康工作树用 Python 3.13/3.12 各复跑通过。完整命令和结果见下节。独立验收门仍保持：不 finalize、不部署、不重投业务样本。Terraform exit-2（task definition 指针滞后）仍是正式部署前的运行证据限制。


## 修复轮：独立验收阻断 F1–F3（2026-10-19）

| Finding | 分类 | 修复与可观察合同 | 关闭证据 |
|---|---|---|---|
| F1 共享检索模块导入 `wknora_client` 不存在 | implementation | 改为现有 `weknora_client`，Agent 真实入口可收集并执行 | 检索/Agent 测试收集通过 |
| F2 单个 hit 的 `invalid_response` 被吞成合法空结果 | implementation | 逐命中畸形回读提升为 WeKnora 不可用，进入 AgentMemory fallback；无 fallback 时人工升级 | `test_malformed_per_hit_readback_falls_back_to_agent_memory` |
| F3 readiness helper 未接入写入适配器 | implementation | knowledge publish 写入完成首次回读后调用有界 readiness；首次回读作为 poll #1；`completed+enabled` 才 accepted；timeout/transport/5xx 为 `outcome_unknown`，parse failed 为 `failed`；已知对象重试同样守卫 | 适配器 readiness 成功/超时/解析失败、重试与 5xx 瞬态回归 |
| F4 关键测试此前未执行 | evidence-gap/test-carrier | 修复测试载体后在健康工作树、两解释器、隔离 PostgreSQL 复跑 | 下方两次完整命令 |

### 本轮实际验证

```text
Python 3.13（/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python）
RUN_POSTGRES_INTEGRATION=1 TICKET_DB_DSN=postgresql://xieziling@/postgres pytest -q
  25 个阶段 6/知识面文件（含 test_weknora_knowledge_retrieval.py 与
  test_weknora_promotion_adapter.py）
结果：582 passed / 0 failed / 8 subtests passed，62.26s

Python 3.12（/opt/homebrew/bin/python3.12）
同一 25 文件命令与隔离 PostgreSQL
结果：582 passed / 0 failed / 8 subtests passed，60.41s

定向修复集合（5 个工作流/适配器/检索文件）：125 passed / 0 failed。
静态：git diff --check 通过；未改 n8n、未部署、未重投业务样本。
```

最新 main 已在任务工作树合入（merge 82fdf70e，唯一冲突为生成 Overview，已从登记源重生成）；合并后两解释器完整回归仍通过。本轮代码验证不释放独立验收门，下一步是以整合后的完整 HEAD 提交增量独立验收。部署前仍需重新冻结实时基线并重新处理 Terraform task definition 指针差异。

## B3/B4 修复与真实入口回归（2026-10-19，HEAD aba81a59）

- **B3（DI 全链）**：run_allowlisted_subagent（engineer_execute_agent.py:663）+execute_engineer_plan（:788）增 weknora_client/agent_memory_client 参数并传递；main.py 主入口（:14538）显式注入 `_default_weknora_retrieval_client()`+`_default_agent_memory_client()`；subagent 侧保留 None 时惰性默认构造兜底。
- **B4（Review 共享原语）**：HermesWeKnoraClient.read 回执增 parse_status/enable_status；Review `_collect_weknora_evidence` 对每个 enriched 条目标记 `agent_ready`（completed∧enabled，与 Agent 路径同一判定）——Review 冲突检测仍看全部索引对象（决策流不变），但两条路径的 readiness 语义同源。
- **真实入口回归**（经 run_allowlisted_subagent 与 execute_engineer_plan 实跑）：WeKnora transport 故障+AM 可用→`knowledge_agent_memory_fallback` 成功；双侧故障→blocked+missing_information（escalate）；engineer_plan 全链传递实证。
- **实测**（/tmp/spwt 隔离副本，双 homebrew 解释器一致）：检索/桥接 20+client 49+adapter/worker 全套 **124 passed / 0 failed（4 subtests）**；知识面 19 文件 **528 passed / 4 failed**——4 个失败（test_repository_configuration 的 account_reply 系列，IndexError）经 origin/main（a217702e）基线复跑**同样失败=存量问题非本分支引入**，如实披露。
- 环境事件披露：会话中段本机文件系统出现新写文件间歇性消失（com.apple 文件提供进程持有相关句柄；跨 Desktop/home//tmp 多路径复现；git 对象库与网络操作全程正常）。应对=全部内容即时提交保全+测试在 /tmp 副本执行；本轮 3 个提交（39fc358e/aba81a59+证据）均经 git 正常读写。建议另立任务处理本机文件系统异常（疑似安全/DLP 或文件提供代理干扰）。
