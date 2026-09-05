# Automation ECS Release Runbook

本文描述 CodeBuild build、Preproduction publish/deploy、同 digest promotion 和正式 Production deploy。发布记录必须明确镜像来自 Preproduction 或获批的历史本地 OCI bootstrap，不得混淆来源。

## Normal CodeBuild Release Path

常规 release 固定一个可从 `origin/main` 到达的完整 40 位 commit。CodeBuild 使用
`NO_SOURCE` project 拉取该 commit，一次构建 API/Route/Worker 三个原生
`linux/amd64` 镜像，直接 push 到 immutable `supportportal/preproduction`，并把
`automation-release-v2` Manifest 与 `automation-preproduction-publish-v1` Publish
Record 写入加密、版本化的 release evidence bucket。CodeBuild 无 ECS、RDS 或
Production ECR 写权限，不能自行部署。

先通过独立 Terraform root 创建 release tooling：

```bash
terraform -chdir=infra/terraform/release init -reconfigure
terraform -chdir=infra/terraform/release plan -out release.tfplan
terraform -chdir=infra/terraform/release apply release.tfplan
```

之后从 clean、同步的 `main` 触发构建；源 Prompt DSN 只通过环境传递：

```bash
AUTOMATION_RELEASE_EVIDENCE_BUCKET=<versioned-bucket> \
TICKET_DB_DSN=<source-dsn> \
./deployment/start_automation_codebuild_release.sh \
  --git-commit <full-main-sha> \
  --prompt-release-id <active-prompt-release-id>
```

输出只包含无 secret 的 Manifest v2 和 Publish Record。requested/observed commit、
三角色 tag/digest、平台、Prompt build ref/content fingerprint、CodeBuild ARN 与 S3
object version 必须全部一致。第一次冷构建只记录耗时；warm-cache目标从下一次真实
release测量，不为测速重复构建同一 release。

## One-time Preproduction Bootstrap

`infra/terraform/preproduction` 使用独立 remote state，自己管理 Preproduction
cluster、ECR、target group/path rule、security group、日志、IAM、Graph EFS access
point、Hermes私有Cloud Map服务和迁移备份桶；VPC、ALB、RDS、ACM和EFS file system
只是共享输入。Terraform 不创建或读取 SecureString value。

bootstrap分两次 add-only apply，不使用 `terraform -target`，也不复制 Production
task definition：

1. 以 `create_account_services=false` apply foundation。
2. 运行 `backend.scripts.bootstrap_automation_preproduction --check-only`，确认目标
   SSM namespace、schema和roles均为空；正式执行时创建全新
   `supportportal_preproduction` schema、runtime/migration roles与独立SSM参数。
3. 触发CodeBuild；对Manifest/Publish Record和ECR digest完成readback。
4. 先运行 `register_automation_ecs_initial_task_definitions.sh --check-only`，再以
   `AUTOMATION_INITIAL_TASK_DEFINITIONS_APPROVED=1` 注册三份canonical initial
   definition。脚本只注册revision，不创建或更新Service。
5. 将生成的 `account-services.auto.tfvars.json` 作为第二次plan输入，设置
   `create_account_services=true`，只新增API/Route/Worker三个Service。
6. 使用正式Preproduction deploy完成schema、Prompt、service、heartbeat及公网门禁，
   最后要求Preproduction Terraform plan为exit 0。

Preproduction保持真实环境身份：路径、schema、role、namespace、Prompt target、secret、
日志和heartbeat均为`preproduction`；Account业务合同与Production相同，不存在应用
allowlist或forced-internal comment。n8n是唯一工单入口控制，本流程不修改n8n。

```bash
DEPLOY_PREPRODUCTION_APPROVED=1 \
TICKET_DB_DSN=<source-prompt-dsn> \
PROMPT_RELEASE_TARGET_DSN=<preproduction-migration-dsn> \
./deployment/deploy_automation_ecs_release.sh \
  --environment preproduction \
  --manifest <release-manifest.json> \
  --publish-record <publish-record.json> \
  --bootstrap-account-schema \
  --hermes-case-workflow-mode disabled
```

独立 `--check-only` 始终只读。正式 deploy 自带同一套 preflight，不要求紧邻执行一次
重复 check-only。schema bootstrap 只有在健康 runtime 的 `schema_revision` 与
Manifest一致且目标数据库直接 schema check成功时才跳过。

Preproduction业务验收后，`promote_automation_release.sh`只复制已验收的三个digest到
`supportportal/production`，不rebuild、不复制数据库、Prompt rows、secret、日志、
task definition或Hermes状态；Production deploy仍要求独立
`DEPLOY_PRODUCTION_APPROVED=1`与Promotion Record。

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
`linux/amd64`的 artifact。构建器选择和 OCI build 开始前，脚本还会只读校验
指定 Prompt Release 确实存在、状态可部署且内容与当前代码 catalog 一致。

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
AUTOMATION_DASHBOARD_SESSION_SECRET=<at least 32 random characters>
```

Dashboard 管理员用户名和密码按 owner 明确批准的临时例外固定为 `admin/admin`；
只有独立 Session secret 注入 API role：Terraform 管理的目标配置使用 Secrets
Manager，当前手工管理的 ECS Production Task Definition 沿用现有 SSM SecureString
前缀。登录成功仅设置
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

## Enablement Archer Worker 发布门禁

包含 `p2-134` 的 Worker 通过 `ARCHER_OAUTH_COOKIE` secret 直连 Archer：

```text
secret ARCHER_OAUTH_COOKIE <- SSM SecureString /supportportal/production/archer-oauth-cookie
```

SSM 参数值是一整串 SSO cookie 头：`oauth2-token=<值>; oauth2-token.sig=<值>`。
该 cookie 对来自 `oauth.agoralab.co`（有浏览器者登录 `archer.agora.io` 后在该域下导出），
是唯一需要人工维护的凭证。Archer API 使用的 `archer_token_jwt_202003` JWT（24 小时）
由 Worker 自动续期：`GET oauth/authorize`（带 SSO cookie）→ 302 `handleSSO?code=` →
Set-Cookie 新 JWT；全程纯 HTTP，无需 Pilot 二进制、pilot-creds EFS 卷或 pilot-server。
p2-139 起镜像不再安装 Pilot 二进制（下载源无签名且轮换二进制，运行时也不使用）。

SSO 会话失效的特征：authorize 不再返回 302（返回登录页 200）→ Worker 按既有
`enable_failed` 契约降级（escalate + 兜底内部邮件，工单转人工 queue）。恢复方式：
人工重新登录 Archer 后更新 SSM 参数并 force new deployment。cookie 值不得进入
ECS command override、task definition 明文、环境变量清单、日志、shell history、
仓库或发布记录。

正式发布命令会只读回读当前 Production Worker task definition，基于当前最新
revision 生成新 revision，完整保留既有 environment、secret、Graph EFS
volume/mount、role、CPU/memory/network/logging 配置，并保存现有 revision 作为
rollback 目标。Worker 中如出现 Pilot 环境、pilot-creds volume/mount，或缺少
Suspension 收件人 secret，发布会在 register 前 fail closed。

首个新工单验收顺序（同时充当 ECS 侧网络/认证探针，全部使用全新工单）：

1. 非法格式 App ID：只触发只读 GET（check-simple-vendor 前的本地校验直接拒绝，
   零网络调用），公开回复要求正确的 32 位 App ID，Case 保持 open。
2. 查无项目：只触发只读 GET；公开回复要求核对/重发 App ID，Case 保持 open。
3. 有效 App ID：Archer 写后读回为 `status=1, region=2, maxSubscribeLoad=50`；Persona
   发布公开成功回复，Zendesk solved，execution completed，且没有 Enablement 内部邮件。

业务验收只使用全新工单：

1. 有效 App ID：Archer 写后读回为 `status=1, region=2, maxSubscribeLoad=50`；Persona
   发布公开成功回复，Zendesk solved，execution completed，且没有 Enablement 内部邮件。
2. 非法格式：公开回复要求正确的 32 位 App ID，Case 保持 open；客户提交更正值后
   使用新值重新执行。
3. 查无项目：公开回复要求核对/重发 App ID，Case 保持 open；客户提交更正值后使用
   新值重新执行。

失败路径仅在自然发生时观察，不得人为破坏 Archer 凭证。生产验收不得重放或
修改历史 Case。

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
  --publish-record .deployments/releases/<release_id>/publish-record.json \
  --preproduction-evidence .deployments/ecs-deploy-preproduction-<release_id>-real/evidence.json \
  --region <aws-region> \
  --registry-id <aws-account-id>
```

脚本要求本机已有 `aws`与 `crane`，并会使用当前 AWS身份登录 ECR。脚本：

1. 按 Manifest digest从 `supportportal/preproduction`读取原始 OCI manifest。
2. 通过 registry-to-registry copy将原始 manifest及其 layers以同一 role tag写入 `supportportal/production`，不 build。
3. 验证每个 Production digest与 Manifest完全相等。
4. 验证Publish Record与Preproduction完整deploy evidence，并把两者SHA-256写入
   `promotion-record.json`；不修改Release Manifest。

Production task definition使用：

```text
<account>.dkr.ecr.<region>.amazonaws.com/supportportal/production@sha256:<digest>
```

### 首次 Production bootstrap 例外

在 Preproduction repository 尚未建立、且 owner 已单独批准直接发布时，可从同一
Release Manifest 的本地 OCI archive 直接写入 Production：

```bash
./deployment/promote_automation_release.sh \
  --manifest .deployments/releases/<release_id>/release-manifest.json \
  --region us-east-1 \
  --registry-id <aws-account-id> \
  --direct-production
```

该模式在上传前重新验证三个 archive，使用
`skopeo copy --preserve-digests`，并生成
`source_repository=local-oci` 的 Promotion Record。目标 immutable tag 已存在且
digest 相同时按幂等成功处理；digest 不同时立即失败。不得修改 Manifest 来适配
registry digest。Preproduction 建成后的常规 Production release 仍必须走上一节的
同 digest promotion。

首次例外已于 2026-09-04 用于 `r20260904-1f13334`：Promotion Record 记录
`source_repository=local-oci`，三个 Production ECR digest 与 Release Manifest
逐项一致。该记录只证明这一次获批 bootstrap；后续 release 在 Preproduction
建成后必须恢复从 `supportportal/preproduction` 按同一 digest 晋升，除非 owner
再次明确批准新的例外。

## Deploy To Production

Production 只能使用以下命令；不得另写临时 task-definition/ECS 更新命令：

```bash
./deployment/deploy_automation_ecs_release.sh \
  --manifest .deployments/releases/<release_id>/release-manifest.json \
  --promotion-record .deployments/releases/<release_id>/promotion-record.json \
  --check-only
```

`--check-only` 完全只读：它校验 Manifest、Promotion Record、当前 Git commit、
三角色 ECR digest、Terraform 零漂移、源 Prompt Release、现有 task definition
合同、Suspension 收件人 secret 内容与 EC2 backup health；不会同步或激活 Prompt
Release，不会 register task definition，也不会 update ECS service。它仍要求通过环境
提供只读源端 `TICKET_DB_DSN`，避免在无真实源 repository 时产生伪校验。

正式部署还要求显式设置 `DEPLOY_PRODUCTION_APPROVED=1`，并通过环境提供目标端
`PROMPT_RELEASE_TARGET_DSN` 及可选的
`PROMPT_RELEASE_TARGET_SCHEMA`。DSN 值不得作为 argv 参数，不得写入日志、
Manifest 或 Promotion Record。获得单独生产授权后执行同一命令但移除
`--check-only`。

包含 PostgreSQL-only Hermes Case Workflow 的 release 在明确批准 mock 验收时还必须
使用同一正式命令的两个显式门禁：

```bash
./deployment/deploy_automation_ecs_release.sh \
  --manifest .deployments/releases/<release_id>/release-manifest.json \
  --promotion-record .deployments/releases/<release_id>/promotion-record.json \
  --bootstrap-account-schema \
  --hermes-case-workflow-mode mock \
  --check-only
```

`mock` 必须与 `--bootstrap-account-schema` 同时出现，否则发布在任何 AWS 写入前
fail closed。check-only 只验证 migration SecureString 元数据、API Service 网络配置和
一次性 task definition 渲染，不读取 migration DSN 值、不注册 task、不改 schema。
正式模式在更新三个 Service 前，以本 release 的 API OCI digest运行一次
`backend.scripts.automation_ecs_bootstrap bootstrap`；一次性 task复用当前 API 的
IAM、网络和日志配置，仅通过 ECS secret reference注入
`AUTOMATION_DB_MIGRATION_DSN`与`TICKET_DB_MIGRATION_DSN`。成功后注销临时 task
definition；失败则不更新 Service。Hermes mode仅注入 API和Worker，Route不接收；
API发布后必须从 `/health/release.hermes_case_workflow.mode` 回读相同值。

若 deploy 进程在一次性 task 运行期间被中断，`--resume` 会先验证 checkpoint 中的
task definition family 和 task 归属，停止仍在运行的旧 task、注销旧 revision 并清除
旧 marker，然后重新执行幂等 bootstrap；归属不一致时 fail closed，不操作该资源。

该 bootstrap 的 DDL是幂等加法，完成后不会因后续 ECS revision回滚而删除；旧镜像
不读取新增表，因此服务回滚仍安全。默认不传这两个参数时，原有发布行为和
`HERMES_CASE_WORKFLOW_MODE=disabled` 合同保持不变。

部署开始前会执行 AWS identity 与凭据寿命预检。可读取 expiration 时默认要求至少
剩余 2700 秒，并在每次 register/update/wait 边界前重新检查；可通过
`AUTOMATION_AWS_MIN_CREDENTIAL_TTL_SECONDS` 提高门槛。若当前 shell 导出了
`AWS_SESSION_TOKEN`，但 provider 无法返回 expiration，命令会拒绝开始，避免临时
凭据在 rollout 或 rollback 中途失效；此时应恢复使用可刷新的 AWS login/provider，
不得靠降低门槛绕过未知到期时间。

正式部署使用 release-scoped 状态目录：

```text
.deployments/ecs-deploy-<environment>-<release_id>[-<hermes_mode>]/
  checkpoint.json
  <role>.old-arn
  <role>.new-arn
  <role>.verified
  evidence.json
```

首次执行若该目录已存在会 fail closed。确认它属于同一次 release 后，使用相同命令
追加 `--resume`。恢复会重新验证 Manifest 与 Promotion Record SHA-256、Git commit、
region/cluster/service identity、已注册 task definition 内容、ECR digest 和当前运行
task；只有 ECS readback 与目标 revision/digest 完全相同的角色才跳过 update。不得仅凭
本地 `<role>.verified` marker 判断完成。checkpoint 与 evidence 不保存 DSN、AWS
credentials、收件人地址或 secret value。状态目录以 `0700`、文件以 `0600` 创建；成功
后会删除完整 task definition副本和检查日志，只保留恢复/审计所需的 ARN、digest、
Prompt identity与检查结论。失败时这些中间文件以私有权限保留，供同一次 release恢复。

Route 与 Worker 会先完成 update，然后使用一次 ECS stable waiter共同等待；两者 digest
和 heartbeat通过后才更新 API。API 稳定后，公网 health/provenance、CloudWatch错误窗口
和 EC2 backup health作为只读检查并行执行。目标 Prompt sync 后还会从目标数据库执行
一次只读 validate，确认 activation 前置内容可读且 CLI 没有执行 schema initialization。

成功后统一证据写入：

```text
.deployments/ecs-deploy-<environment>-<release_id>[-<hermes_mode>]/evidence.json
```

其中包括 commit、Prompt Release build ref/content fingerprint、三个角色的旧/新 task
definition ARN、期望 digest，以及 Terraform、ECR、收件人合同、heartbeat、public
health、CloudWatch、EC2 backup和 Prompt activation 状态。失败时保留 checkpoint 和
失败证据；若激活尚未开始仍按原顺序回滚，若激活已开始或目标已 active，则保持新栈并
报告 `reconciliation_required`。
回滚命令或 stable waiter 任一失败时，evidence明确记录 `rollback_incomplete` 和
`checks.rollback=failed`，不得把已尝试回滚报告成恢复成功。

Terraform 必须为已校验的 `1.9.8`；本机不在 PATH 时通过
`AUTOMATION_TERRAFORM_BIN=/absolute/path/to/terraform` 指定。零漂移 plan 使用
DynamoDB backend lock，并在 60 秒内无法取得锁时阻断发布。

两个环境的固定执行顺序：

1. 要求目标环境真实 Terraform refresh plan 为 exit `0`；exit `1/2` 都阻断发布。
2. 若显式请求 schema bootstrap，使用目标 API digest运行一次受控 migration task；
   只有 exit 0才继续。全新 Preproduction必须先创建运行表，之后才能持久化 Prompt
   Release candidate。
3. 校验源 Prompt Release，将目标同步为 candidate，并在状态切换前比较 build ref
   与完整 `prompt_key + content_sha256` 指纹；目标本地 version remap 允许存在。
4. 从目标环境三个 service 当前 revision 克隆 task definition，只替换 image、五个
   provenance字段和显式批准的Hermes mode；逐角色注册新revision。`disabled`会同时
   删除Hermes endpoint、API key和callback token引用；active mode缺任一必需secret
   时fail closed。
5. 先更新 Route、Worker，共同等待 stable、核对运行 digest 和最新 heartbeat provenance；
   再更新 API。
6. 核对公网 live/release/ready、Hermes mode、三个运行 digest、CloudWatch 和 EC2 backup。
7. 全部健康后才激活目标 Prompt Release，并立即 validate/readback active 状态。

Prompt 激活前任一步失败，命令按 API/Worker/Route 反序恢复已更新 service 的旧
revision。激活命令一旦开始但结果不确定，不盲目回滚健康新栈或重复激活；命令返回
失败并明确要求 reconciliation，通过目标 Prompt Release readback 决定下一步。

## Terraform Ownership And Zero Drift

`infra/terraform/bootstrap` 只负责一次性创建加密、版本化 S3 state bucket 与
DynamoDB lock table。`infra/terraform/production` 导入并管理稳定资源：
`supportportal/production` ECR、Automation target group、HTTPS priority 10 rule，
以及 API/Route/Worker service 的稳定配置。Task-definition revision 和 service 的
`task_definition` 指针只归正式发布命令所有，Terraform 仅对该字段使用
`ignore_changes`。

Cluster、共享 ALB/listener/ACM/security group/log group/SSM/roles、Graph EFS、Redis
与 Hermes 只作为 data/输入引用；production root 不创建或删除这些共享资源，也不
声明 Pilot、Secrets Manager、task definition、OIDC、S3 release bucket 或 alarms。
完成 remote backend 和逐项 import 后，首次及每次 ECS 发布后的
`terraform plan -detailed-exitcode` 都必须为 `0 add / 0 change / 0 destroy`、exit `0`。

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
