# Preproduction native Hermes 配置切换记录

日期：2026-10-13

本提交用于为 Preproduction native Hermes 配置切换创建新的不可变 release 身份。业务代码、Prompt Release、Production 配置和旧 release evidence 均不变。

## 目标运行配置

- `AUTOMATION_CASE_ENGINE=hermes`
- `HERMES_AGENT_ENABLED=true`
- `HERMES_PERSONA_ENABLED=true`
- `HERMES_CASE_WORKFLOW_MODE=disabled`
- `ENABLEMENT_WORKFLOW_MODE=manual`

`HERMES_CASE_WORKFLOW_MODE=disabled` 表示不启用旧的 typed Case Workflow；native Hermes 仍通过 Route Worker 的 `AUTOMATION_CASE_ENGINE=hermes` 分流，并由 Worker 创建 Hermes Agent turn、按 route 加载 Prompt 和 toolset。

## 发布边界

- 目标环境：Preproduction
- 源 Prompt Release：沿用当前 active `pr-9737f67803f1`
- 不创建测试工单
- 不发送 Slack、Zendesk 或客户消息
- 不执行 Production 晋级
- 使用正式 ECS CodeBuild/release pipeline，不覆盖既有 release evidence

## 验收指针

发布完成后记录如下：

- Release：`r20261013-570aec2`
- 源码 commit：`570aec29f56b7407fd3cb2c976db59637bb72ed8`
- Prompt Release：`pr-9737f67803f1`，目标库已 active
- API：task definition `:142`，digest `sha256:716097d38c4dfdbcbfd0adf77ec284adb9666d614888c1e3401e877309bbffdc`
- Route：task definition `:140`，digest `sha256:3af33f845f4397669faf3f657c2f2862b50c916ae4fecefa7236caf385715f2a`
- Worker：task definition `:142`，digest `sha256:f07d3b4effe466f012d1ad8786ece2a518783fa318576511491b457c3e0c3390`
- `/automation/preproduction/health/release`：`status=ok`，release/commit/image/schema/Prompt provenance 与 release evidence 一致；`hermes_case_workflow.mode=disabled`
- Route task definition：`AUTOMATION_CASE_ENGINE=hermes`
- Worker task definition：`HERMES_AGENT_BASE_URL`、`HERMES_AGENT_API_TOKEN` secret references 在位，`AGENT_MODEL_ID=gpt-6.1-sol`
- ECS 三角色单一 PRIMARY、`COMPLETED`、`1/1/0`；heartbeat、provider probe、CloudWatch error window、Terraform zero-drift、ECR digest 对账均通过
- 正式 evidence：`.deployments/ecs-pipeline-r20261013-570aec2/preproduction-deploy/evidence.json`

本次 pipeline 的总耗时超过 900 秒目标，evidence 记录 `slo_breach=true`；所有硬门禁仍为 passed。业务测试另行执行，不在本次配置切换中自动触发。
