# Automation ECS Release 手工 Runbook

本文只描述手工 build、publish、promotion和部署输入。本任务没有执行其中任何 AWS、ECR、ECS、EC2、Cloudflare或 n8n命令。

## Release Bundle

在干净 commit并确认当前 Prompt Release后执行：

```bash
./deployment/build_automation_ecs_release.sh \
  --release-id rYYYYMMDD-<commit7> \
  --prompt-release-id <active-prompt-release-id>
```

输出目录：

```text
.deployments/releases/<release_id>/
  api.oci.tar
  route.oci.tar
  worker.oci.tar
  release-manifest.json
```

脚本优先使用 Docker Buildx；Docker不可用时可自动使用 Podman，也可显式传
`--builder docker|podman`。两条路径都把构建平台固定为 `linux/amd64`，Podman
路径先构建本地临时镜像再导出 `oci-archive`并清理临时 tag。每个角色只执行
一次 build；脚本不登录 registry、不 push、不 deploy。重复路径会 fail closed，
防止覆盖既有 release artifact。Manifest validator会拒绝非单一
`linux/amd64`的 artifact。

重新验证 bundle：

```bash
<project-python> -m backend.scripts.automation_release validate \
  --manifest .deployments/releases/<release_id>/release-manifest.json
```

## Runtime Configuration

三个角色共享：

```text
AUTOMATION_ENVIRONMENT=preproduction|production
AUTOMATION_BASE_PATH=/automation/<environment>
AUTOMATION_DB_DSN=<runtime coordination DSN>
AUTOMATION_DB_SCHEMA=<environment-specific schema>
AUTOMATION_DB_RESOURCE_ID=<resource identity>
AUTOMATION_JOB_NAMESPACE=<environment-specific namespace>
AUTOMATION_RELEASE_ID=<release_id>
AUTOMATION_IMAGE_DIGEST=sha256:<role digest>
AUTOMATION_RUNTIME_IDENTITY=<task/worker identity>
APP_BUILD_REF=<full commit>
APP_BUILD_TIME=<manifest build_time>
PROMPT_RELEASE_ID=<manifest prompt release>
TICKET_DB_DSN=<environment Account schema runtime DSN>
TICKET_DB_SCHEMA=<environment Account schema>
```

Route和Worker额外获得`TICKET_DB_DSN`；远端RAG URL/token只注入Worker。
`AUTOMATION_DB_MIGRATION_DSN`与`TICKET_DB_MIGRATION_DSN`只属于一次性bootstrap
task，不得进入三个长运行task definition。

API额外要求：

```text
AUTOMATION_INTAKE_SHARED_TOKEN=<n8n bearer token>
AUTOMATION_WORKER_HEARTBEAT_MAX_AGE_SECONDS=30
AUTOMATION_DASHBOARD_ADMIN_USERNAME=<independent dashboard administrator>
AUTOMATION_DASHBOARD_ADMIN_PASSWORD=<dashboard administrator secret>
AUTOMATION_DASHBOARD_SESSION_SECRET=<at least 32 random characters>
```

Dashboard 管理员三项配置从 Secrets Manager 只注入 API role。登录成功仅设置
`HttpOnly`、`Secure`、`SameSite=Strict` 的短期 session cookie，不返回或复用
`AUTOMATION_INTAKE_SHARED_TOKEN`；Route 与 Worker 不接收 dashboard secret。

Worker是否允许 Zendesk副作用由环境显式控制：

```text
AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED=0|1
AUTOMATION_REPLY_POLL_ENABLED=true
AUTOMATION_REPLY_POLL_INTERVAL_SECONDS=300
INTERNAL_EMAIL_SUBJECT_NAMESPACE=[automation]
RAG_SERVICE_URL=<verified remote RAG base URL>
RAG_SERVICE_SHARED_TOKEN=<secret>
```

任何 schema或 job namespace不包含当前 environment时，runtime拒绝启动。Secrets不得写入 Release Manifest、task definition明文或 Promotion Record。

## Schema Bootstrap

用一次性 task和 migration身份执行：

```bash
python -m backend.scripts.automation_ecs_bootstrap bootstrap
```

长运行角色启动前只读检查：

```bash
python -m backend.scripts.automation_ecs_bootstrap check
```

该 bootstrap初始化 Account/Persona/Reply/Delivery所需 schema与 ECS coordination schema，不检查或创建项目内 knowledge/PGVector/RAG runtime。

## Publish To Preproduction

目标 repository：

```text
supportportal/preproduction
```

role tag严格来自 Manifest：

```text
api-<release_id>
route-<release_id>
worker-<release_id>
```

使用能保留 OCI manifest digest的 registry client上传三个 OCI archive。上传后必须通过 ECR readback逐角色验证：

```text
ECR digest == release-manifest.json component digest
```

如果上传工具转换 media type、compression或 manifest并导致 digest变化，该 release不能继续；不得把 ECR的新 digest回写或覆盖原 Manifest来伪造通过。应修正上传路径并重新从原 OCI bundle发布。

Preproduction ECS task definition使用：

```text
<account>.dkr.ecr.<region>.amazonaws.com/supportportal/preproduction@sha256:<digest>
```

禁止使用 tag作为 task definition image引用。

## Promote To Production

Preproduction真实验收与外部 readback完成后，由用户执行：

```bash
./deployment/promote_automation_release.sh \
  --manifest .deployments/releases/<release_id>/release-manifest.json \
  --region <aws-region> \
  --registry-id <aws-account-id>
```

脚本要求本机已有 `aws`与 `crane`，并会使用当前 AWS身份登录 ECR。脚本：

1. 按 Manifest digest从 `supportportal/preproduction`读取原始 OCI manifest。
2. 通过 registry-to-registry copy将原始 manifest及其 layers以同一 role tag写入 `supportportal/production`，不 build。
3. 验证每个 Production digest与 Manifest完全相等。
4. 生成单独的 `promotion-record.json`，不修改 Release Manifest。

Production task definition使用：

```text
<account>.dkr.ecr.<region>.amazonaws.com/supportportal/production@sha256:<digest>
```

## n8n Cutover Input

新的 n8n HTTP节点：

```text
Method: POST
URL: https://supportcenter.stellarix.space/automation/production/v1/intake
Header: Authorization: Bearer <AUTOMATION_INTAKE_SHARED_TOKEN>
Header: Content-Type: application/json
```

Body使用 `automation-intake-v1`，不能继续发送旧 `/production/account`的五字段 form body。`event_id`由 Zendesk event稳定生成；retry必须复用完全相同的 event ID和 payload。

在切流前依次验证：

1. `health/live`返回当前 environment。
2. `health/release`的 release/build/image/schema/Prompt provenance与 Manifest/task definition一致。
3. `health/ready`同时包含新鲜 Route和Worker heartbeat。
4. 未授权 malformed JSON返回 `401`且没有 Execution。
5. 一个受控 Ticket产生唯一 Execution，并能查询完整 steps/events/deliveries。
6. 远端 RAG、Zendesk、邮件和Slack均完成真实 provider readback。
7. `outcome_unknown`不会被 retry或 replay清除。
8. `https://support.stellarix.space/production/...`仍保持原有 EC2行为。
