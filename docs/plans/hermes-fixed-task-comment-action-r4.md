# Hermes 固定 Case Task 与 Comment Message Action（r4）

本实施按单线程、单 branch、单 release 执行；三方 benchmark、Production 发布、合并和 Preproduction 部署均留给后续门禁。

## 基线与恢复点

- 实施源基线：`main@30485f9ea4b2110dd303fea8400d1ac6b7d7b503`。
- 最近可定位的 Preproduction release：`r20261008-e76bb53`，代码 `e76bb539fb41917b37a07192e2a159f2d2a8f710`，Prompt Release `pr-d9166ff58459`，schema `automation-ecs-014`。
- 旧 image digest：api `sha256:5faf6771ad8eaa9635350662745397d966872b7d97206fadb905e4efee17dafa`；route `sha256:bdcb55b4f7d673397d8867a7c5b3367b524f34eacb15b51783640ab47acc0cdb`；worker `sha256:bcf42c0c555d8bc43ad2c0e0558376a1dc6c54b4eb6ced7a15318aa9c5eab807`。
- 以上均为本地既有发布产物的只读记录；本次没有创建 tag、修改 ECS、激活 Prompt Release 或改变环境变量。

## 当前契约

- C1：`ticket.created` 在 Hermes 引擎中调用 Production Account Router 一次；结果归一化为锁定的 `hermes-case-task-v1`。只有注册 automation 和 technical/RAG investigation 进入 Hermes，其余保存 classification-only。
- C2：binding 持久化 `case_task`、Prompt Release、snapshot 和 `flow_version`；schema revision 为 `automation-ecs-015`，InMemory/PostgreSQL 具备同名字段和迁移。
- C3：`comment.created` 读取固定 task，不再调用 Account Router。Investigation 进入既有 `investigation_feedback`；其他固定 task 进入 `message_action`，action 只允许六个枚举，多意图、未知、非法 JSON 和无法判断 fail closed 到 `handoff_human`。
- C4：message action turn 跳过 route phase；回复类 action 只进入 Persona，`continue_task` 才使用固定 route 的 Work；`case_task` 不可被 comment 覆盖。
- C5：prompt catalog 新增 `hermes-message-action-manual-v1`，core prompt 明确 task 锁定和 comment 不重路由。

## 证据与限制

- 已通过：`uv run pytest -q backend/tests/test_hermes_case_task.py backend/tests/test_automation_ecs_route_worker.py backend/tests/test_automation_ecs_store.py backend/tests/test_automation_ecs_contracts.py backend/tests/test_prompt_modules.py backend/tests/test_agent_config.py backend/tests/test_hermes_route_schema_normalizer_alignment.py`（共 55 项）。
- 已通过：Route Worker Hermes 新 case / comment 集成用例及更新后的 Hermes handoff 用例（12 项）。
- PostgreSQL 测试入口已收集但本机无 DSN，27 项按仓库约定 skip；真实隔离 PostgreSQL 仍是验收前置。
- Hermes follow-up 套件保留 5 个既有预生产 handoff 断言失败：当前既有逻辑在 `environment=preproduction` 将内部 note/route back 标记为 `skipped_not_production`，本实施未改变该边界。
- 未创建 Prompt Release、未合并 PR、未部署 Preproduction、未运行 `scripts.testing.preproduction`，因此不声明运行时或业务完成。
