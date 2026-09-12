# 测试导航

本页用于按改动选择验证入口，区分本地测试、数据库集成、部署核验和真实业务回归。源码核对日期：2026-09-12；基线：`7cbd383e`。这里列出可执行入口，不表示这些测试已在当前版本运行通过。

## 先选验证层

| 层次 | 适用改动与入口 | 前置条件和副作用 | 结果如何解释 |
| --- | --- | --- | --- |
| 文档检查 | 文案、链接、文档组织；`git diff --check` 和直接阅读 | 无需应用数据库、重建或重启 | 链接及表述正确；不作运行状态证明 |
| 单元与契约测试 | 改动所对应的 `backend/tests/test_*.py` | 在任务工作区执行，核对 fixture 和外部调用 mock | 证明被选测试覆盖的行为；不等于全量或真实链路通过 |
| PostgreSQL 集成 | store、迁移、并发、reader 的 `*_postgres.py` | 专用测试 DSN；测试可能建表、写入及删除测试 schema | 必须区分 passed、failed 和 skipped |
| 镜像与部署脚本测试 | 角色裁剪、Manifest、CodeBuild、pipeline、Terraform 契约 | 部分用 mock/stub；实际构建另需 Docker/Podman 或 CodeBuild | 脚本断言通过不等于目标环境部署成功 |
| 部署后技术验证 | Health、provenance、heartbeat、ALB、Provider 与 drift | 在已授权的目标环境按 Runbook 执行；可能运行一次性 ECS task、生成本地 evidence | 证明版本及技术依赖状态；不等于业务闭环通过 |
| 业务端到端回归 | `/automation/test/`、真实工单 ScenarioEngine | 可发邮件、创建/修改 Zendesk 工单及触发工作流动作，包含人工等待点 | 必须核对具体环境和业务状态；不可作为无副作用探针 |

## 单元和契约测试入口

以下命令在当前任务工作区根目录执行，`python` 应来自已准备好项目测试依赖的环境。如果复用根工作区的虚拟环境解释器，仍保持工作目录和待验证源码指向当前任务工作区。命令中的测试文件存在不代表它已经执行。

| 改动范围 | 建议先读的测试 |
| --- | --- |
| ECS API、鉴权、intake、健康 | [test_automation_ecs_api.py](../../backend/tests/test_automation_ecs_api.py) |
| Route/Worker 和执行契约 | [test_automation_ecs_route_worker.py](../../backend/tests/test_automation_ecs_route_worker.py)、[test_automation_ecs_worker.py](../../backend/tests/test_automation_ecs_worker.py)、[test_automation_ecs_contracts.py](../../backend/tests/test_automation_ecs_contracts.py) |
| 镜像裁剪与依赖锁 | [test_automation_ecs_images.py](../../backend/tests/test_automation_ecs_images.py)、[test_python_dependency_locks.py](../../backend/tests/test_python_dependency_locks.py) |
| 构建、部署、晋级门禁 | [test_automation_codebuild_release.py](../../backend/tests/test_automation_codebuild_release.py)、[test_automation_ecs_release_pipeline.py](../../backend/tests/test_automation_ecs_release_pipeline.py)、[test_automation_ecs_deploy.py](../../backend/tests/test_automation_ecs_deploy.py) |
| 工单回归控制台或剧本引擎 | [test_automation_test_console.py](../../backend/tests/test_automation_test_console.py)、[test_automation_test_scenarios.py](../../backend/tests/test_automation_test_scenarios.py) |

例如只改 ECS 裁剪契约时：

```bash
rtk proxy python -m pytest -q backend/tests/test_automation_ecs_images.py
```

测试采用 pytest 入口，以加载 [conftest.py](../../backend/tests/conftest.py) 的共享 fixture。该 fixture 拦截指定调用方的 Account failure alert，原因是导入主应用可能加载 `.env`，历史失败路径曾发送真实告警邮件。它不是所有外部调用的统一封锁；涉及邮件、Zendesk、Slack、Archer 或真实 Provider 的测试还需查看各自 mock。不要直接运行测试函数或绕过 fixture 来假定同样的隔离效果。

## PostgreSQL 集成测试

| 测试 | 环境变量 | 已有隔离方式 |
| --- | --- | --- |
| [ECS store](../../backend/tests/test_automation_ecs_store_postgres.py)、[Dashboard reader](../../backend/tests/test_automation_ecs_dashboard_reader_postgres.py) | `AUTOMATION_ECS_TEST_POSTGRES_DSN` | 测试 fixture 创建独立 schema 并清理，具体权限见测试文件 |
| [Admin reader](../../backend/tests/test_automation_ecs_admin_reader_postgres.py) | `AUTOMATION_ECS_ADMIN_TEST_POSTGRES_DSN` | 要求专用空库；使用固定的 `supportportal_production`、`supportportal_preproduction` schema，完成后清理；发现既有同名 schema 时立即失败 |

测试 DSN 必须指向允许创建、写入和清理测试数据的专用测试数据库，不复用未经确认的运行或迁移 DSN。凭据只通过环境提供，不写进命令参数、文档或测试报告。其他 PostgreSQL 测试使用的变量以对应文件为准。

配置好专用环境后，例如：

```bash
rtk proxy python -m pytest -q -rs backend/tests/test_automation_ecs_store_postgres.py
```

这些测试缺少 DSN 时会 skip。即使命令退出码为 0，也必须报告 skip 原因，不能称为数据库验证通过。

## 镜像和部署后技术验证

镜像验证的层次见 [镜像与裁剪](./runtime-images.md)。正式 ECS 检查入口及参数沿用 [ECS Runbook](../deploy_automation_ecs_release.md)，不在本页另造一套命令。

- 核对源码 commit、Manifest、各角色实际 digest、Prompt/schema 与运行 provenance 的一致性。
- 核对 API Health、Route/Worker heartbeat、ECS 服务与 ALB target 健康；Terraform 的 zero-drift 结果不能由源码测试替代。
- Provider probe 与业务回归分开。既有发布探针不发信、不创建工单、不执行 Archer enablement；但运行一次性 ECS task 本身属于运维动作，应在部署授权范围内执行。
- `--check-only` 的范围按具体脚本判断。ECS preflight 会生成本地 evidence；不能把整个流程理解成“没有任何写入”。

单机运行相关代码合并后，按 [Agent 工作流详情](../agent_workflow_details.md) 从根 `main` 验证官方栈、`/health.app_build.ref` 和改动对应的实际标记。默认本地 Lightweight 可以连接远端数据库；它不等于隔离测试环境。纯文档改动不运行这套重启验证。

## 真实工单回归

[Production 工单回归 Runbook](../testing/production_ticket_regression_runbook.md) 的明确范围是 legacy `/production`；[EC2 Nginx](../../deployment/nginx/supportportal.conf) 将 `/automation/test/` 指向这一链路。不能直接用它证明 ECS Preproduction 或 ECS Production 的业务验收。

[CLI](../../scripts/testing/production_ticket_scenarios.py) 与网页共用 [ScenarioEngine](../../backend/services/automation_test_scenarios.py)，会加载配置。`--list` 列举剧本，`--check` 连接 DB/SMTP/IMAP 但不发信；`--scenario` 会运行真实业务链路。现有 CLI 的显式选项为 `E1/E2/F1/S1/all`，引擎另有 `D1`；选取方式以当前 CLI 和网页实现为准。

运行前明确目标入口、测试数据和允许的外部动作。结果检查 reply intent、内部邮件状态、工作流状态、Zendesk readback 等结构化证据，人工批准或附件回复的等待点保留人工。模拟结果、连通检查、API 接受请求和业务完成是不同的验证结论。

## 自动执行与报告

[finalize_task_to_main.sh](../../scripts/workflow/finalize_task_to_main.sh) 要求调用者提供 `--verify`，同步最新 `origin/main` 后执行该命令；它不会自动为每个改动选出完整测试集。首次整理基线没有受版本控制的 GitHub Actions workflow，不能据此索引宣称所有测试都是 CI 必过项。

验证记录至少包含：被验证的 commit、工作区/目标环境、实际命令、通过/失败/跳过结果、相关 evidence 位置与未完成项。CodeSight coverage 采用文件名或字符串匹配，适合找测试线索，不能作为执行覆盖率或发布门禁。按 [AGENTS.md](../../AGENTS.md) 使用最小相关验证，不默认运行整套业务回归或 `supportportal-run-report`。

[返回运维索引](./README.md)
