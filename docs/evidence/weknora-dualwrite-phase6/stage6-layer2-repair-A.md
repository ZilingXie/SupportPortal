# WeKnora 并行双写与人工治理链（阶段二）层 2 修复

修订：r4-stage6-repair-A。任务：p2-197。原复核 HEAD dbcae60a1144e7bec3189d48df36cec6995027c5；失败部署源码 42a187ae884015b833277b7b4e23d624a647663b。
用户于 2026-10-13 授权实施，并选择 A：保留层 2 只排队、层 3 才执行 AI。原独立验收门、人工 Slack Review 和观察窗口后的全量人工停点保留。
工作树 .worktrees/stage6-deploy；分支 codex/stage6-deploy。main 4ecea043 已整合（含 ef52d04e native 恢复/发布门禁）；本轮没有部署、重投样本、修改 n8n 或外部知识数据。

## 当前合同与 finding

2026-10-13 独立验收 HEAD `266e720914ead230d1e12adb61d25312922ba7a2`：未通过。F1-F4 核心修复已由独立 reviewer 核验；新增阻断 F5/F6 来自完整 PR 的 n8n 快照 diff。本次修复保持计划修订 r4-stage6-repair-A，补充 F5/F6；执行方自测完成，等待同 PR 新 HEAD 独立复验，不自行释放门禁。

| Finding / 合同 | 原因与修复边界 | 关闭证据 / 限制 |
|---|---|---|
| F1 / C1 共享配置 | 隐式 legacy 覆盖 native；沿用 main 的显式 engine 与降级门禁。知识发布比较三角色发布前/渲染后的全部非知识 environment/secrets，只有 provenance 可变化，配置名差异即拒绝。| 真实 CLI 漂移矩阵（engine、typed mode、enablement、gateway secret、其他 runtime flag）及保留知识/provenance 正向；部署前及激活前双点调用。尚未在线发布。 |
| F2 / C2 构建与 release 身份 | 命令替换中的 buildx 失败落入旧 tag digest 查询；显式失败退出。新阶段需唯一 release suffix；resume 仅复用同身份且重新校验 manifest/publish-record 的 release。| 三角色失败注入均停止，无失败角色旧 digest 查询、无 manifest/publish-record 上传；真实 pipeline 新 ID / 同 ID 拒绝 / unchanged resume / altered identity 拒绝 / damaged publish-record 拒绝。AWS 边界为 fake。 |
| F3 / C3 生产与执行分离 | Summary/Review 门误依赖旧 Case real，且双写 worker 不控制 AI；知识就绪解耦 mode，独立 AI execution 缺省关闭，Case/standalone 服务 drain 与正式 worker 入口都检查执行门。| typed disabled 下创建 pending；AI 关闭不 claim、不调用 gateway；开启后同任务完成两独立会话并进入人工 Review；双写 worker 开也不能绕过 AI 关闭。隔离 PG pending/lease 原样保留。 |
| F4 / C4 历史证据纠正 | 探针 not found 不能推定 ECR 镜像缺失；#190 SUCCEEDED 不能证明重建；回滚至 legacy 不代表 native 恢复。| 已更正旧基线记录，保留事实与未知；镜像拉取底层原因仍未知，未加盲目重试。 |
| F5 / C5 SHA-256（implementation / test-carrier） | 两个 Build 手写 padding 漏预留末尾 8 字节，UTF-8 长度余数 56-63 错误。未发布 candidate 改为 Math.ceil((l + 1 + 8) / 64) * 64；历史原文保持。 | 两个完整 Build 真实 jsCode 对 Node SHA-256 oracle 逐值比较，覆盖余数 55/56/63/0、ASCII、中文 emoji、多块和 100KB 输入；四个故障注入恢复漏 8 字节实现，原断言均能抓到错误。未证明线上 hash 已修复。 |
| F6 / C6 快照身份（implementation / evidence-gap） | 后来版本 ID 混入旧节点图；本地修复又冒用旧远端 Draft 身份。六份历史文件恢复 Git 4ecea043 原始 bytes，修复内容迁到无远端版本 ID 的 local candidates；source base/manifest 分别登记。 | 六文件 byte equality；validator 对 source/manifest/存在的 inner versionId 与 activeVersionId 一致性校验；正向正确版本、12 个内部冲突、候选伪造身份/旧地址/metadata 冲突负向均通过。缺省历史字段不补造；线上版本需发布前另行刷新。 |

F1-F4 保留独立验收证据；F5/F6 当前为执行方修复完成、待独立复验。没有新增产品选择、迁移、外部写入或重投。CSD candidate 的 captured_fields 已改成实际显式请求清单，未改变请求节点字段范围，也不再称完整 Jira 全字段。

## 验证与下一步

本轮自测不释放独立验收门。代码通过独立验收后，才可按已授权 Preproduction 发布：先实时回读三角色与其他发布状态、冻结新基线，显式保留 engine=hermes/typed=disabled/agent/persona 和全部共享配置，使用新 release ID。回读新开关、digest、Prompt、健康后才受控重投 CSD-79923/CSD-79994。

层 2必须证明 pending 且未 claim/零 AI；层 3单独开启 KNOWLEDGE_AI_EXECUTION_ENABLED 后取得 Summary/Review 真实会话与关联证据；双写、Slack Review、draft 和全量停点按原合同推进。发布/业务未完成前 p2-197 不标 done。

## 实际自测记录（2026-10-13）

- Python 3.13（根区 .venv）/ 本机 PostgreSQL 14.19；PG 套件各自创建并清理独立随机 schema，不连接业务数据库。外部 AWS/Docker/Hermes/知识写入边界由 fake/mock 承载。
- 23 套件同次回归：497 passed，8 subtests，0 skipped/failed，51.44s；4 个既有 FastAPI on_event 弃用告警。之后仅补 3 个真实渲染正向用例并移除两个未使用 import，专项 `test_stage6_repair_contract.py` 26 passed，4.47s。两次结果有重叠，不合并为一次测试计数。
- 三角色 buildx immutable-tag 失败注入必须非零退出，失败角色不查旧 tag、后续角色不再构建、release-tool 不调用、manifest/publish-record 不上传；成功路径仍得到三角色 digest 和发布记录。
- 两个正式 worker 入口在 execution 缺省/0 时不调用服务 drain，在 1 时调用；服务测试证明 pending 保留后开启执行得到独立 Summary/Review 会话，PG 证明未领取的行及 lease 完全未变。
- 真实 renderer 三角色跨知识阶段保留共享配置，API/Worker typed disabled + AI execution=0，Route engine=hermes；真实 CLI 15 个非知识漂移注入均拒绝且诊断无值泄漏。
- `bash -n` 两发布脚本、`git diff --check`、Overview `--write`/`--check` 通过。
- n8n snapshot validator exit 0：15 published / 4 divergent draft / 56 redactions；仍有 29 LEGACY-EXPOSED 历史 published 告警。本轮只验证随原分支带入的历史记录，未刷新在线 active/draft，不作为当前运行态证据；发布前按 AGENTS.md 重新读回。

完整回归可复现（从任务 worktree；PYTHON 选已装依赖解释器，TICKET_DB_DSN 指向本机测试库；不可使用业务库）：

```bash
rtk proxy env RUN_POSTGRES_INTEGRATION=1 TICKET_DB_DSN=postgresql://xieziling@/postgres "$PYTHON" -m pytest -q \
  backend/tests/test_automation_codebuild_release.py \
  backend/tests/test_automation_ecs_deploy.py \
  backend/tests/test_automation_ecs_release_pipeline.py \
  backend/tests/test_stage6_repair_contract.py \
  backend/tests/test_native_hermes_release_guard.py \
  backend/tests/test_hermes_knowledge_workflow.py \
  backend/tests/test_hermes_knowledge_workflow_postgres.py \
  backend/tests/test_knowledge_standalone_workflow.py \
  backend/tests/test_standalone_knowledge_postgres.py \
  backend/tests/test_knowledge_source_repository.py \
  backend/tests/test_knowledge_governance_review_fixes.py \
  backend/tests/test_knowledge_governance_fixes_postgres.py \
  backend/tests/test_knowledge_dual_write.py \
  backend/tests/test_stage3_dual_retrieval.py \
  backend/tests/test_stage4_slack_review.py \
  backend/tests/test_stage5_knowledge_review.py \
  backend/tests/test_stage5_knowledge_review_postgres.py \
  backend/tests/test_stage6_source_allowlist.py \
  backend/tests/test_knowledge_delivery_postgres.py \
  backend/tests/test_knowledge_delivery_repository.py \
  backend/tests/test_agent_memory_delivery.py \
  backend/tests/test_weknora_client.py \
  backend/tests/test_weknora_promotion_worker.py
```

PR 包含原分支的历史 n8n 快照/URL 校验修正及 checkpoint/evidence 档案（本轮新增代码之外的 diff），因为 #1482 已合并而这些记录尚未合入。保留原分支并创建新 Draft 修复 PR；独立验收需检查完整 PR diff，历史快照不能替代在线状态。可复验所需文件均提交，不依赖未跟踪脚本。

## F5/F6 实际修复与验证（2026-10-13）

- 历史原文锚点：`4ecea0431f1b84490bab4bf51220faeef815e6b2`。CSD/Solved/Forward 六份 published/draft 文件逐字节一致；manifest 的 published/draft IDs 保留，Forward nodeCount 更正为原图 13。历史旧 URL 只在历史快照校验路径允许，本地候选必须使用当前正确地址。
- 两份 `workflows/candidates/*.candidate.json` 声明 `unpublished_local_candidate`，只有 baseVersionId/baseSnapshotGitCommit，禁止 source 与 workflow 携带远端版本身份。没有写成已发布版本，也没有调用 n8n API。
- `rtk proxy /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_n8n_source_only_contracts.py backend/tests/test_n8n_snapshot_identity.py`：76 passed / 0 failed，最终复跑 7.51s。本轮仅使用合成输入与临时目录，无网络、无业务数据库。该结果与前轮 497/26 分开，不汇总。
- 完整 Build 测试不提供 global crypto；标准库只用作 oracle。旧 padding 注入被同一 hash equality 断言捕获；校验器冲突注入证明结构通过不足以掩盖版本混合。
- 最终 CLI validator：15 published / 4 divergent drafts / 56 redactions，另列 2 unpublished local candidates；29 LEGACY-EXPOSED 历史告警保留，不作为 live 状态证据。Overview --write/--check 与 git diff --check 通过。
- 原 F1-F4 代码未变，不重跑隔离 PG 大回归。代码接受、部署成功、样本真实 AI/知识投递是三个独立阶段，本轮只申请 F5/F6 及完整 diff 的代码复验。
