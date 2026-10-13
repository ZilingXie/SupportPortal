# WeKnora 并行双写与人工治理链（阶段二）层 2 修复

修订：r4-stage6-repair-A。任务：p2-197。原复核 HEAD dbcae60a1144e7bec3189d48df36cec6995027c5；失败部署源码 42a187ae884015b833277b7b4e23d624a647663b。
用户于 2026-10-13 授权实施，并选择 A：保留层 2 只排队、层 3 才执行 AI。原独立验收门、人工 Slack Review 和观察窗口后的全量人工停点保留。
工作树 .worktrees/stage6-deploy；分支 codex/stage6-deploy。main 4ecea043 已整合（含 ef52d04e native 恢复/发布门禁）；本轮没有部署、重投样本、修改 n8n 或外部知识数据。

## 当前合同与 finding

| Finding / 合同 | 原因与修复边界 | 关闭证据 / 限制 |
|---|---|---|
| F1 / C1 共享配置 | 隐式 legacy 覆盖 native；沿用 main 的显式 engine 与降级门禁。知识发布比较三角色发布前/渲染后的全部非知识 environment/secrets，只有 provenance 可变化，配置名差异即拒绝。| 真实 CLI 漂移矩阵（engine、typed mode、enablement、gateway secret、其他 runtime flag）及保留知识/provenance 正向；部署前及激活前双点调用。尚未在线发布。 |
| F2 / C2 构建与 release 身份 | 命令替换中的 buildx 失败落入旧 tag digest 查询；显式失败退出。新阶段需唯一 release suffix；resume 仅复用同身份且重新校验 manifest/publish-record 的 release。| 三角色失败注入均停止，无失败角色旧 digest 查询、无 manifest/publish-record 上传；真实 pipeline 新 ID / 同 ID 拒绝 / unchanged resume / altered identity 拒绝 / damaged publish-record 拒绝。AWS 边界为 fake。 |
| F3 / C3 生产与执行分离 | Summary/Review 门误依赖旧 Case real，且双写 worker 不控制 AI；知识就绪解耦 mode，独立 AI execution 缺省关闭，Case/standalone 服务 drain 与正式 worker 入口都检查执行门。| typed disabled 下创建 pending；AI 关闭不 claim、不调用 gateway；开启后同任务完成两独立会话并进入人工 Review；双写 worker 开也不能绕过 AI 关闭。隔离 PG pending/lease 原样保留。 |
| F4 / C4 历史证据纠正 | 探针 not found 不能推定 ECR 镜像缺失；#190 SUCCEEDED 不能证明重建；回滚至 legacy 不代表 native 恢复。| 已更正旧基线记录，保留事实与未知；镜像拉取底层原因仍未知，未加盲目重试。 |

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
