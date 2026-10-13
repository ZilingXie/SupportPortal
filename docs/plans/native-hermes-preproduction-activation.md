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

发布完成后补充 release ID、源码 commit、三角色 revision/digest、`/health/release` 配置回读、ECS 健康、CloudWatch、Terraform zero-drift 和 Prompt 激活状态。业务测试另行执行，不在本次配置切换中自动触发。
