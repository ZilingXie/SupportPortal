# 环境矩阵与边界

本页用于选择正确的运行环境、入口和核对来源。源码核对日期：2026-09-12；基线：`7cbd383e`。以下是仓库配置与操作契约，不是线上资源盘点或当前部署成功证明。

## 三套环境

| 项目 | ECS Production | ECS Preproduction | EC2 Staging / 主栈 |
| --- | --- | --- | --- |
| 用途 | 正式 Automation 业务运行 | ECS 变更的预发布验证 | 用户称为 Staging 的 EC2 主栈；现有 legacy 入口另见下文 |
| 运行方式 | ECS API、Route、Worker | ECS API、Route、Worker；Hermes 配置另行核对 | Docker Compose 主栈，Nginx 分发请求 |
| 入口配置 | ALB `/automation/production` | ALB `/automation/preproduction` | `/account`、`/api/account` 及其他主栈页面；另保留 legacy `/production` 配置 |
| 镜像 | 晋级已验证的相同 OCI digest | CodeBuild 构建三个 `linux/amd64` 角色镜像 | 主应用 Full 镜像；不能套用本地 Lightweight 默认值 |
| 数据边界 | 环境专用 schema、job namespace 和运行身份 | 与 Production 分开的 schema、job namespace 和运行身份 | 由目标 EC2 `.env`、Compose 服务与 processing profile 决定，不能从 Staging 名称推导 |
| 外部业务动作 | 按实际工作流和配置执行；部署授权不等于测试工单或发信授权 | 名称本身不保证零业务写入；技术探针与业务回归分开 | legacy 路径可能执行真实邮件、Zendesk 等动作；尚不能据本页认定“全部模拟”已实现 |
| 部署入口 | 预发布验证后，按独立 Production 授权进行同 digest promotion/deploy | 正式 ECS pipeline 的默认目标 | EC2 部署脚本及主栈对齐脚本 |
| 详细来源 | [Production Terraform](../../infra/terraform/production/README.md)、[ECS Runbook](../deploy_automation_ecs_release.md) | [Preproduction Terraform](../../infra/terraform/preproduction/README.md)、[ECS Runbook](../deploy_automation_ecs_release.md) | [单机指南](../deploy_single_host_ec2.md)、[Compose](../../deployment/docker-compose.single-host.yml) |

环境入口需要同时包含目标主机或 ALB，不能只根据 URL 中的 `production` 判断环境。实际域名路由、接入流量和服务数量需要运行时核对。

## EC2 名称与历史路径

当前 [Nginx 配置](../../deployment/nginx/supportportal.conf) 能确认的映射如下：

| EC2 路径 | 仓库中的处理 | 使用含义 |
| --- | --- | --- |
| `/account`、`/api/account` | 转发到主 API | 不能仅凭路径或机器名称认定数据与外部服务已隔离 |
| `/production/`、`/production/api/`、`/production/account` | 转发到 legacy production API | 与 ECS `/automation/production` 分开识别 |
| `/automation/test/` | 转发到 legacy production API | 真实工单回归入口，不是无副作用 Staging 沙箱 |
| `/automation/staging`、`/automation/preproduction`、`/automation/production` 及其子路径 | 返回 `410` | EC2 已退役的 split 路径，不代表前置 ALB 上的 ECS 路径不可用 |

本页保留 EC2 Staging 的称呼，同时明确源码仍包含的 legacy 行为。若要将 EC2 整体改为“真实 Agent 处理模拟工单、所有业务写入模拟”，需要单独实施并验证数据、凭据和副作用隔离；本次文档整理不宣称该迁移已完成。

[旧 EC2 split Runbook](../deploy_automation_release.md) 已标为退役，仅供历史恢复参考。Compose 中仍有某个 service 定义，也不等于当前 EC2 部署脚本会启动它。

## ECS 配置与资源归属

[AutomationEcsSettings](../../backend/services/automation_ecs_runtime.py) 只接受 `preproduction` 和 `production`：

- `AUTOMATION_BASE_PATH` 必须匹配 `/automation/<environment>`。
- `AUTOMATION_DB_SCHEMA` 必须是有效 PostgreSQL 标识符，并包含环境名；`AUTOMATION_JOB_NAMESPACE` 也必须包含环境名。
- 核对 `AUTOMATION_DB_RESOURCE_ID`、运行 DSN 所指资源以及 Account 的 `TICKET_DB_SCHEMA`，不能仅因 schema 名不同就推断物理数据库已隔离。
- 长运行任务使用 runtime 凭据；迁移 DSN 属于一次性 bootstrap 任务。API intake 身份与 Dashboard session 身份分开。
- `AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED` 控制相应 Zendesk 动作，不能据此推断邮件、Slack、Archer 或所有工作流都被模拟。

配置键、角色注入和模式参数以 [ECS Runbook](../deploy_automation_ecs_release.md) 为准。本文不复制密钥值，也不固定各环境的当前开关状态。

Terraform 管理稳定基础设施；部署脚本管理 ECS task-definition revision 和服务指针。两套 Terraform root 的管理范围不同，不能互相套用；共享 VPC、ALB、RDS、EFS 等也不意味着所有环境资源都由同一个 root 管理。

Preproduction Terraform 包含 Hermes 的独立 IAM、EFS access point 和服务发现配置。Hermes 的实际运行位置、实例数、状态目录和启用模式需结合 [Hermes 部署说明](../deploy_hermes_investigator_ecs.md) 与目标环境只读证据核对，不从 Terraform 文件存在与否推断在线状态。

## 部署与验证入口

| 操作 | 入口与预期边界 |
| --- | --- |
| 常规 ECS 变更 | [release_automation_ecs_pipeline.sh](../../deployment/release_automation_ecs_pipeline.sh)：先到 Preproduction；Production 需单独授权并复用 digest |
| ECS 部署核验与恢复 | [deploy_automation_ecs_release.sh](../../deployment/deploy_automation_ecs_release.sh) 及 [Runbook](../deploy_automation_ecs_release.md)：检查 Manifest、Prompt/schema、角色健康、运行 provenance、Terraform drift 和恢复证据 |
| EC2 主栈部署 | [deploy_ec2.sh](../../deployment/deploy_ec2.sh)：在目标 EC2 执行，详细前置条件和旧镜像恢复见 [单机指南](../deploy_single_host_ec2.md) |
| EC2 主栈对齐或自动调度 | [deploy_surfaces_ec2.sh](../../scripts/ops/deploy_surfaces_ec2.sh)、[auto_deploy_ec2.sh](../../scripts/ops/auto_deploy_ec2.sh)：管理 EC2 主栈，不负责 ECS 部署；timer 是否启用需现场确认 |
| 本地开发栈 | [restart_single_host_stack.sh](../../scripts/workflow/restart_single_host_stack.sh)：本地 Podman，与三套远端环境分开；Lightweight 仍可能使用远端数据库 |

Production 直发仅适用于 [AGENTS.md](../../AGENTS.md) 明确规定的紧急 Production hotfix 授权。技术验证通过不会自动授权 Production 晋级或真实业务回归。

操作前记录目标环境、主机/服务、源码或 release、配置来源与授权范围；操作后按 [测试导航](./testing.md) 记录实际验证结果。服务 revision、digest、Prompt active 状态和健康结果应从该次 evidence 或运行服务读取，并附时间，不在本表中维护一份易过期的“当前版本”。

[返回运维索引](./README.md)
