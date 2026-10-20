# preproduction-prompt-sync-resilience v1 — Prompt Release Sync 可观测性与瞬时连接恢复

## 计划身份

- 计划名称：`preproduction-prompt-sync-resilience`
- 修订：`v1`（本文件为当前合同与证据正本）
- 目标：让 `prompt-target-state` 读取与 Prompt sync 在瞬时数据库连接故障时有限恢复，并保留脱敏错误证据。
- 角色：planner → executor（本次 ZCode）→ 独立 reviewer
- Review gate：独立代码验收通过后才能 finalize/合并；本计划**不部署**、不触碰真实业务系统。
- 登记：`docs/project/tasks/p2-206.json`（Function `ecs-environment-migration`）

## 基线

- worktree：`.worktrees/preprod-env-safety`，分支 `codex/preprod-env-safety`
- 基线提交：`3362f7e35752cfc7cbff5e8236a245b25056f5cd`（= 计划给定基线；分支落后 main 一个纯登记提交 `12357b29`，与本任务无关，未 rebase）
- 实施提交：`6acb8254`（Python 分类/脱敏）→ `e6ffe3fe`（shell 包装与阶段函数）→ `49e968cd`（测试）→ 文档/登记提交；最终 HEAD 以 Draft PR 正文为准。
- 故障事实（计划给定）：Terraform/AWS/源库/目标库身份均通过；`prompt-target-state` 首次读取失败且 stderr 被 `2>/dev/null` 丢弃；`prompt-sync.json` 为空无法区分错误类别；后续只读重试读到目标 Prompt 为 `active`。

## 范围

包含：两条目标操作的脱敏错误保存；连接超时与可重试错误分类；有限、可审计的重试；evidence 记录失败阶段与分类；定向测试与 shell 语法检查；Prompt sync 真实入口的隔离验证。

不包含：Prompt 内容或 Prompt Release 数据模型；放宽权限；自动 schema migration；自动重试 ECS/Prompt activation/其他阶段；Zendesk/客户回复/业务逻辑；重建或重发当前业务 release。

## 合同与证据

| 合同 | 触发 / 真实入口 | 已验证机制 | 可观察结果 | 检查与阶段 | 证据 |
| --- | --- | --- | --- | --- | --- |
| C1 错误不泄密 | 任一目标操作失败 | 原始 stderr 只写 `PROMPT_SCRATCH_DIR`（checkpoint 外，0700，cleanup 删除）；`redact_sensitive_text` 去除 DSN URI、`password=`/`token=`/`Bearer`/AKIA、环境中 secret 命名变量字面值及 DSN 的 password 分量；`record_prompt_operation_failure` 写出前过 `assert_evidence_secret_free`，失败即拒写，shell 回退 `*_unknown` 无消息 | artifact/evidence/stdout 不含 DSN、密码、token；scratch 中原始 stderr 含密钥但随 cleanup 消失 | 代码层 | `test_redaction_*`（4）、`test_record_failure_refuses_an_artifact_that_still_carries_a_dsn`（强制脱敏失效→拒写负控）、`test_shell_target_state_stops_after_three_*`（RAW_WITH_SECRET=3 仅在 scratch；STATE_DIR 全文件零命中；cleanup 后 scratch 不存在）、`test_shell_classifier_failure_records_*` |
| C2 目标状态读取 | `--resume` → `run_prompt_release_phase` → `prompt-target-state` | `run_prompt_operation_with_recovery prompt-target-state`：成功才 `mv` 生成 `prompt-current.json`；失败不留空文件，写 `prompt-target-state-error.json`（分类+attempts） | 成功：完整 `prompt-current.json`；失败：无 json、有分类 artifact、`fail` 带分类码退出 | 代码层 | `test_shell_target_state_recovers_after_transient_failures`、`test_shell_resume_stops_when_the_state_readback_is_denied`、`test_shell_resume_recovers_the_state_readback_then_syncs_a_candidate` |
| C3 瞬时故障重试 | 分类为 `*_connection_timeout` / `*_transient_connection` | 最多 `PROMPT_OPERATION_MAX_ATTEMPTS=3`，固定退避 `(1 3)` 秒；达上限 `return 1` → `fail`，不进入 sync/ECS | 3 次调用、`sleep 1`、`sleep 3`、停止 | 代码层 | `..._stops_after_three_transient_failures_and_reports_evidence`（calls=3, sleeps=[1,3]）、`..._sync_replays_only_while_readback_proves_missing_and_stops_at_three`；突变负控：MAX=5 时该两测试失败 |
| C4 非瞬时错误 | 权限（42501/28000/28P01）、schema 缺失（42P01/3F000）、Prompt 校验 ValueError、内容指纹不一致、unknown | `retryable=false` → 首次失败即 `return 1`，无 sleep | 恰 1 次调用 | 代码层 | `test_shell_non_transient_failures_run_exactly_once`（权限/schema/ValueError 三参数）、分类表 25 例 |
| C5 Prompt sync 写入 | `run_prompt_operation_with_recovery prompt-sync` 失败 | 先 `prompt_sync_target_readback_status`（单次只读 `prompt-target-state`，失败=`unavailable`）；`classify_prompt_operation_failure` 对 sync：连接类且回读≠`missing` → `prompt_sync_write_result_unknown`，`retryable=false`；08007 对 sync 恒为 write_result_unknown；不自动激活 | 回读 candidate/active/unavailable：1 次调用、evidence `prompt_sync=failed`；回读 missing：允许有限重放 | 代码层 | `test_sync_lost_connection_is_replayable_only_when_readback_proves_missing`（6 参数）、`test_shell_sync_lost_connection_is_fail_closed_unless_the_release_row_is_missing`（3 参数）、`test_shell_sync_recovers_when_the_release_row_is_still_missing`；突变负控：去掉回读门后 9 项失败 |
| C6 证据绑定 | 每次失败/恢复 | artifact 字段 `operation/release_id/prompt_release_id/attempts/max_attempts/exit_code/classification/retryable/exception_type/sqlstate/target_readback_status/redacted_message/outcome/generated_at`，`-attempts.jsonl` 追加历史；evidence `checks` 增 7 字段；`phase-attempts.jsonl` 记 `prompt_schema failed` | 可追溯到 release、Prompt ID、操作、次数、分类、时间、脱敏消息；无 DSN/原始 stderr | 代码层 | `test_record_failure_writes_a_secret_free_artifact_and_append_only_history`、`..._reports_evidence`（checks 字段断言 + `assert_evidence_secret_free(evidence)`） |

## 分类码（与计划一致，共 12 个）

`prompt_target_state_{connection_timeout, transient_connection, permission_denied, schema_missing, unknown}`
`prompt_sync_{connection_timeout, transient_connection, write_result_unknown, validation_failed, permission_denied, schema_missing, unknown}`

分类依据优先级（`_prompt_failure_kind`）：

1. psycopg 异常类名（`TimeoutError/PoolTimeout`→timeout；`ConnectionFailure/AdminShutdown/CannotConnectNow/TooManyConnections/ConnectionRefusedError…`→transient；`TransactionResolutionUnknown`→write_result_unknown（仅 sync）；`InsufficientPrivilege/InvalidPassword/InvalidAuthorizationSpecification`→permission；`UndefinedTable/InvalidSchemaName`→schema_missing；`ValueError`→sync 已知校验文本则 validation_failed，否则 unknown，**永不**按文本判为连接错误）。
2. SQLSTATE（08000/08001/08003/08004/08006/57P01/57P02/57P03/53300→transient；08007→write_result_unknown；42501/28000/28P01→permission；42P01/3F000→schema_missing）。
3. 明确 libpq/PostgreSQL 文本（`connection timeout expired`、`timeout expired`、`connection timed out`、`operation timed out`→timeout；`connection refused`、`server closed the connection unexpectedly`、`the database system is starting up`、DNS/路由类等→transient；`permission denied`、`password authentication failed`、`no pg_hba.conf entry`、`role "x" does not exist`→permission；`relation|table|schema "x" does not exist`→schema_missing）。
4. 其余 `unknown`，不可重试。裸 `timeout` 字样不触发重试（`statement timeout while reading`→unknown 有测试钉住）。

两个 Prompt CLI（`automation_ecs_release_pipeline`、`prompt_release`）的 `main()` 现在在 stderr 末行输出 `{"ok": false, "error", "exception_type", "sqlstate"}`，使出进程分类也能走优先级 1-2；`parse_prompt_cli_failure` 回退解析 traceback 末行。

## 设计落点

- `deployment/deploy_automation_ecs_release.sh`：`run_prompt_release_phase`（原 main 内 `prompt_schema` 阶段原样抽出，仅两处调用改为包装器）、`run_prompt_operation_with_recovery`、`classify_prompt_operation_failure`（shell）、`prompt_sync_target_readback_status`、`record_prompt_operation_outcome`、`ensure_prompt_scratch_dir`、`cleanup` 删 scratch、`write_evidence` 新字段。bash 3.2 兼容（无关联数组）。
- `backend/scripts/automation_ecs_release_pipeline.py`：常量与分类表、`redact_sensitive_text`、`parse_prompt_cli_failure`、`classify_prompt_operation_failure`、`prompt_cli_failure_detail`、`record_prompt_operation_failure`、子命令 `classify-prompt-operation-failure`、`main()` 捕获 `psycopg.Error` 并输出结构化明细、`collect_prompt_target_state` 显式 `connect_timeout=10`。
- `backend/scripts/prompt_release.py`：`_create_sync_target_repository` 显式 `connect_timeout=10`（计划"sync 目标连接路径设置显式连接超时"）；`main()` stderr 明细增 `exception_type/sqlstate`。

## 决策与偏差（执行侧等价局部选择，供验收核对）

1. 脱敏消息字段命名 `redacted_message` 而非计划示例的 `last_error`：既有结构守卫 `assert "last_error" not in script`（PR #1054，heartbeat 收敛循环不得外露原始错误）仍完整保留。
2. `validation_failed` 仅属 `prompt_sync_*`：target-state 上的 ValueError 折叠为 `unknown`，使分类码严格等于计划清单。
3. evidence 除计划示例的 `prompt_sync_*` 三字段外，对称增加 `prompt_target_state*` 四字段（范围项"记录失败阶段和错误分类"）。
4. `prompt_schema` 阶段抽为 `run_prompt_release_phase`：仅为真实入口测试（含"目标已 active 时 resume 不重复 sync/activation"）；阶段体逐行不变。结构测试 `test_formal_deploy_script_enforces_order_*` 的计数与顺序断言随之改为 main 3 次 + 阶段 2 次，顺序意图不变。
5. C5 回读门为"先回读再分类"：sync 任一失败都做一次只读回读（读操作，开销可忽略）并把结果写入 artifact `target_readback_status`。
6. 既有层披露：`PostgresTicketRepository._run_with_connection_retry` 对连接错误仍有仓库级单次重试（读写均有，`sync_prompt_release` 单事务+咨询锁+幂等），本计划不改；最坏情况一次 CLI 调用内部有 2 次连接尝试。

## 证据台账（2026-10-20，macOS bash 3.2.57，root `.venv` Python 3.13.11 / pytest 9.0.2 / psycopg 3.3.3）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 新测试模块 | `pytest backend/tests/test_prompt_sync_resilience.py` | 59 passed |
| 受影响模块回归 | `pytest` test_prompt_sync_resilience, test_prompt_versioning, test_automation_ecs_deploy, test_automation_ecs_release_pipeline, test_stage6_repair_contract, test_native_hermes_release_guard, test_enablement_mode_release_guard, test_investigation_case_files_deploy, test_automation_ecs_cloudwatch_gate | 335 passed |
| 突变负控 | 临时 `PROMPT_OPERATION_MAX_ATTEMPTS=5` + 删除 C5 回读门 → `-k "stops_after_three or fail_closed_unless or replayable_only_when or stops_at_three"` | 10 failed / 1 passed；`git checkout` 复原后 11 passed |
| 强制泄漏负控 | `test_record_failure_refuses_an_artifact_that_still_carries_a_dsn`（patch 脱敏为恒等） | 抛 `DSN is forbidden`，artifact 与 jsonl 均未写出 |
| shell 语法 | `bash -n deployment/deploy_automation_ecs_release.sh` | OK |
| Python 编译 | `python -m compileall` 两个脚本 + 四个测试文件 | OK |
| 空白检查 | `git diff --check 3362f7e3..HEAD` | OK |
| shellcheck | 本机未安装 | 跳过（披露） |
| 基线既有失败 | `test_automation_ecs_terraform.py::test_preproduction_network_and_runtime_identity_are_explicit` | 在干净根区 main `12357b29` 同样失败（ECS 子网表达式断言与现有 terraform 不符），与本任务无关，未修 |

## 部署层 / 业务层

本计划不执行部署，不运行 PP-A1/E3/真实工单。后续任一部署须重新确认 release/source identity、Prompt sync/activation、ECS revisions/digests、health/heartbeat/CloudWatch、Terraform zero drift。本机制尚无 Preproduction 真实故障样本，`waiting-for-evidence`：首次在真实发布中出现 `prompt-*-error.json` 时回读其分类是否与实际原因一致。

## 停止点

停在独立代码验收（Draft PR）。未合并、未部署、未触碰真实业务系统。验收通过后按项目正常 finalize 流程合入 main；本改动不触发应用 ECS 部署：`backend/scripts/prompt_release.py` 与 `backend/scripts/automation_ecs_release_pipeline.py` 仅被 `deployment/*.sh` 与测试引用、无运行时导入（2026-10-20 `rg` 核对），属部署工具/诊断路径，随下一次正常发布自然进入 release worktree 生效。

## 收口记录（2026-10-20）

- 独立代码验收：**通过**，评审 HEAD `7855b9361d2d56ca20abd716fc94df56372b8a4b`（PR #1523）；评审方复跑 335 passed 与全部静态检查，确认 C1-C6 与两项破坏性负控；已知限制如上表所列，含先前 `d579b035` 随 PR 合入已披露并被接受。
- 集成评估：合入 `origin/main`（`12357b29`…`0bd735ea`，p2-192/p2-197 登记与 p2-192 修复）得 `42e71f7107cfb6a899434e86fd51af2857123b34`；评审文件与 7855b936 逐字节一致，仅生成的 `docs/projectoverview-data.js` 重建；新 SHA 复检 335 passed、bash -n/compileall/diff --check 通过。
- finalize：`--reviewed-head 42e71f71` + 同一 verify 集通过，squash 合并提交 **`2856b178d16d0eafdab3f1b2ec4103bc6ba12bec`**；根区 main 快进、CodeGraph 同步、根区复跑 257 passed、CodeSight wiki 刷新；worktree/分支已清理。
- 边界：代码验收完成；部署验收与业务验收不属本计划。下一次正常 Preproduction 发布若产生 `prompt-*-error.json`，回读其分类与脱敏（waiting-for-evidence）。
