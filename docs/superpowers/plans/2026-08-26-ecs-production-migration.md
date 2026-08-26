# Automation Production 从 EC2 迁移到 ECS 宏观计划

Recommendation: Sol medium for implementation. 架构和迁移边界已经收敛；实施会同时涉及运行时隔离、不可变发布、AWS 基础设施、Production 验收和 n8n 受控切换，适合由 Sol medium 按阶段门禁执行。

状态：`stage_0_complete`

- Project Task：`p1-53`
- Project Phase：`phase-1`
- Module：`platform-delivery`
- Function：`ecs-environment-migration`
- 范围：仅迁移阶段 1
- 目标：在 `support.stellarix.space/automation/production` 建立 ECS Production，并长期保留 EC2 `/production` 作为 n8n 可切回的 backup

## 目标和范围

建立一套独立、production-safe 的 ECS Automation Production，通过 n8n 筛选的测试 Case 完成真实验收，然后将新的 Production Case 从 EC2 `/production` 受控切换到 ECS `/automation/production`。两条路径共用 `support.stellarix.space`，EC2 `/production` 长期保留为 backup。

现有 EC2 `/production` 保持不变并继续运行。ECS 不接管或重放已经进入 EC2 的任务；回滚只把后续新 Case 的 n8n 目标切回 `/production`。

本阶段包含：

1. Production-safe 的 Route、API、Worker、RAG API 和 RAG Worker 镜像。
2. 基于 ECR immutable digest 的构建发布链和可审计 release manifest。
3. 当前所需的共享 AWS 基础以及 ECS Production Terraform。
4. Shared ALB 路径路由、dark deploy、受控 Production 验收、n8n 切换、回滚和 EC2 backup 健康门禁。

本阶段不包含：

1. ECS Preproduction；它属于迁移阶段 2。
2. EC2 Staging；它属于迁移阶段 3。
3. 将 EC2 中的在途任务复制、迁移或重放到 ECS。
4. 恢复已退役的 EC2 split orchestration；`/automation/production` 只作为新的 ECS 路径建立。
5. 改变 Automation 业务规则、Case 资格、回复内容或副作用策略。

## 已验证的当前状态

执行者在进入对应步骤前需要低成本复核以下事实：

1. `docs/project/tasks/p1-53.json` 是 canonical 进度记录，顺序已经确定为 Production、Preproduction、Staging。
2. `deployment/build_automation_release.sh` 只构建本机镜像并记录本机 image ID，不 push registry，也不产生 ECR digest。
3. `backend/Dockerfile.automation` 已从 Production API 镜像物理删除 `backend.main`、rerun/reset 模块、测试代码和测试 UI。
4. `backend/worker.py` 仍从 `backend.main` 导入运行对象，因此当前完整 `APP_RUNTIME_IMAGE` 不能视为 production-safe Worker 镜像。
5. `backend/automation_production_runtime.py` 不提供 rerun/reset，但 `/health` 尚未报告 Prompt Release 和 release manifest provenance。
6. `deployment/deploy_automation_production_blue_green.sh` 是单机 Docker Compose + Nginx 流程；当前 Worker 门禁只证明容器短时间稳定，不是真实应用 heartbeat。
7. `backend/scripts/prompt_release.py` 已具备 Prompt Release 校验和同步能力，可以复用于 ECS bootstrap。
8. 仓库目前没有本计划需要的 Terraform、ECR push、ECS deployment 或 GitHub Actions AWS release 实现。
9. 已退役的 EC2 split runtime 和网络必须保持退役；当前 EC2 `/production` 独立运行到正式切换。
10. Stage 0 已确认 AWS account `891612554546`、Region `us-east-1`；SupportPortal RDS、`zacBot` EC2 和全部现有 subnets 都位于 default VPC `vpc-0125f57b2ec2f0423`。
11. 当前六个 subnet 全部是 public subnet并直连 Internet Gateway；没有 private subnet 或 NAT Gateway。用户选择当前测试阶段成本优先，不建设 NAT 和多副本应用层。
12. `stellarix.space` DNS 由 Cloudflare 管理；`support.stellarix.space` 当前直接解析到 `zacBot`。AWS 没有该域名 ACM certificate，也没有 GitHub OIDC provider/role。
13. 用户确认 ECS 使用 `/automation/production`，EC2 `/production` 长期保留为 backup；新路径按现有 `/production` request body/业务语义兼容，n8n live workflow 在 ECS 上线后再验证。

## 目标架构

```text
n8n Production workflow
        |
Cloudflare DNS + ACM
        |
Public ALB + blue/green target groups
        |-- `/automation/production*` -> ECS target group
        `-- default（包含 `/production*`）-> EC2 target group

ECS API Service，desired count 1
        |-- Automation Production API
        `-- 同 release 的 Route sidecar，通过 localhost 通信

ECS Worker Service，desired count 1
        |-- production-safe Worker image
        `-- Redis heartbeat + structured heartbeat logs

ECS RAG API + RAG Worker，desired count 1
        `-- 私有服务发现，不依赖 EC2 runtime

共享依赖
        |-- 现有 RDS VPC + 独立 Production schema/runtime identity
        |-- ElastiCache Redis/Valkey + 独立 queue/channel namespace
        |-- Secrets Manager
        |-- 加密 EFS access point，保存可变 Graph token cache
        |-- CloudWatch logs、metrics、dashboard 和 alarms
        |-- immutable ECR repositories + image scanning
        `-- versioned S3 release manifest storage
```

ALB 按 AWS 要求位于两个 AZ 的 public subnets。当前成本优先阶段的 ECS Tasks 使用现有 public subnets并分配 public IP 取得外网出口，不创建 NAT Gateway；API ingress 只允许来自 ALB security group，Worker/RAG 不允许公网 ingress，服务间使用 VPC private address/service discovery。该取舍降低当前测试成本，但不是高可用拓扑；未来进入正式容量阶段时再单独评估 private subnets、NAT 和多副本。

## 发布单位和不变量

发布单位是 immutable release manifest，不是正在运行的容器，也不是可覆盖的 image tag。Manifest 至少记录 source commit、Prompt Release ID、Route/API/Worker/RAG digests、schema revision、Terraform revision、release ID 和 build time。

必须保持以下不变量：

1. Production 镜像物理排除 rerun/reset 和测试 UI；feature flag 或隐藏按钮不能代替物理隔离。
2. ECS Production 不依赖 EC2 上的应用、Worker、Redis 或 RAG 容器。
3. 同一个 ECS Task 中的 API 和 Route sidecar 来自同一 manifest。
4. 长期 Tasks 只使用不具备 DDL 权限的 runtime database credential。
5. Schema bootstrap 使用独立凭据和一次性 ECS Task。
6. ECR tag immutable；部署和 provenance 使用 digest。
7. ECS `RUNNING` 或 ALB healthy 不能单独证明 Worker ready；Worker 必须提供可验证 heartbeat。
8. 本地 `published_at` 不等于 Zendesk 客户可见，必须 external readback。
9. `outcome_unknown` 不自动重试。
10. 切换和回滚只改变新 Case 的入口；已被某个环境接收的 Case 留在该环境完成或进入 Human Review。
11. ECS 使用独立 `supportportal_production` schema、Redis 和 queue/channel；EC2 backup 保持原 schema和本地 Redis，两个环境的 worker 不得争抢同一任务。
12. EC2 `/production` 长期保留，不属于阶段 1 的退役对象；它与 ECS 共享 RDS instance，因此不是数据库容灾方案。

## 实施前置条件

Stage 0 已确认：

1. AWS account `891612554546`，Region `us-east-1`，当前操作者通过 `CSEGROUP` 具备 bootstrap authority。
2. RDS VPC `vpc-0125f57b2ec2f0423`、RDS security group `sg-0e9c3bd50e371fbf4`，以及六个可用 public subnets。
3. 当前无 private subnet/NAT；成本优先阶段采用 public-IP egress + 最小 security groups。
4. DNS owner 为 Cloudflare；后续申请 `support.stellarix.space` ACM certificate并用 Cloudflare DNS validation，切换时将现有 A record 改为 ALB alias/CNAME 方案。
5. GitHub repository 为 `ZilingXie/SupportPortal`；OIDC provider和 roles 尚未创建，由后续 Terraform/bootstrap实现，不使用长期 AWS access key。
6. Production secret values 已存在于当前运行配置；迁移只记录 names和 owner，不在 manifest、GitHub 或日志中暴露值。专用 runtime/DDL DSN 需要按最小权限创建。
7. 14 天 EC2 CPU 平均约 4.7%、峰值约 71%；初始采用 API `0.5 vCPU/1 GiB`、Worker `0.5 vCPU/1 GiB`、RAG API `1 vCPU/2 GiB`，均 desired count 1并在切流前压测。

## 实施阶段

### Stage 0：AWS 和发布链 Preflight（完成）

已完成 AWS、VPC/RDS、subnet/route、DNS/ACM、IAM/OIDC、现有服务、secret names、EC2 负载和接口契约盘点，并收敛为 shared ALB、EC2 backup、public-subnet 单副本的成本优先架构。n8n live workflow identity按用户决定延后到 ECS endpoint 上线后的受控验收前；Stage 0 只定义 OIDC/role边界，资源由后续 Terraform/bootstrap创建。

可观察结果：VPC、subnets、security groups、DNS owner、secret来源、n8n URL切换契约和 IAM role职责都有唯一答案。

放行门禁：已通过。Live n8n workflow ID 和 Cloudflare DNS实际变更分别是 Stage 5、Stage 4 的执行门禁，不阻塞 Stage 1 runtime实现。

### Stage 1：Production-safe Runtime Boundary

拆除 Worker 对 `backend.main` 的依赖，建立只包含必要模块的 production-safe Worker 和 RAG roles，并保留 Production API 现有的 rerun/reset 物理隔离。新增 `/automation/production/account` 和所需 `/automation/production/api/*` compatibility routes：保持现有 `/production` request body/业务语义，仅在新路径增加统一的 `X-N8n-Request-Token` 鉴权。

把 schema 创建从长期运行的启动路径移出；在 health/provenance 中补充 build、Prompt Release、manifest 和 resource identity；为每个 Worker Task 增加 Redis heartbeat 和结构化日志。

可观察结果：Production API、Route、Worker、RAG API 和 RAG Worker 都能独立于完整应用镜像启动并报告一致 provenance；现有 n8n payload只替换 prefix并增加 token即可调用新入口。

放行门禁：filesystem、imports、OpenAPI 和 UI 检查证明没有 rerun/reset；compatibility contract tests覆盖 intake、Zendesk sync和Slack integration；runtime credential 无法执行 DDL；Worker heartbeat freshness 可独立于 ECS 状态测量。

### Stage 2：Immutable Build And Release Pipeline

将本机 release builder 扩展为 GitHub Actions OIDC workflow，使用 Buildx 构建 Linux AMD64 镜像，push 到 ECR，等待 image scan，并基于 ECR digest 写入 immutable release manifest。Manifest 保存在启用版本管理的 S3；本机构建仅保留开发测试用途。

可观察结果：一个干净 commit 和一个 Prompt Release ID 只产生一份可审计 manifest，部署时无需 rebuild。

放行门禁：tag 无法覆盖；所有 digest 可从 ECR 解析；build/scan 失败不会产出 deployable manifest；GitHub 不保存长期 AWS access key。

### Stage 3：Terraform ECS Production Foundation

建立当前最小所需 Terraform：remote state、ECR、IAM/OIDC、network attachments、ALB listeners/target groups、API Service 的 CodeDeploy blue/green、ECS cluster/services/task definitions、单节点 ElastiCache、Secrets Manager references、EFS、CloudWatch 和 ACM。Cloudflare record/validation由明确的外部 DNS步骤管理，不创建 Route53 hosted zone。

不创建 Preproduction 或 Staging。复用现有 default VPC/public subnets，但 ECS Production 使用独立 schema identity、Redis endpoint/namespace、queue/channel、secrets和logs。Public ALB 的 ECS path指向 `/automation/production*`，default target group继续指向 EC2，保留 `/production` backup。

可观察结果：Terraform 能 dark deploy ECS Production，且不会修改或重启现有 EC2 `/production`。

放行门禁：plan 不删除、停止或替换 EC2；ALB跨两个 AZ，应用 desired count 1；ECS public IP只用于 egress，API ingress只接受 ALB security group；长期 Tasks 不获取 DDL credential。

### Stage 4：Dark Deployment

从一个 approved manifest 部署。先运行一次性 schema bootstrap Task，再同步 Prompt Release，然后依次启动 RAG services、Worker services 和 green API task set。先确认 ALB 的 EC2 default target和 ECS path target均健康，再在 Cloudflare完成 ACM validation并把 `support.stellarix.space` 从 EC2 A record切到 ALB；正式 n8n Production intake仍使用 `/production`，因此业务继续进入 EC2。

可观察结果：同一域名下 `/production` 仍由 EC2处理，`/automation/production` 已由 ECS处理，但正常 Production Case尚未切换到 ECS。

放行门禁：schema/Prompt 同步成功；Task 健康；Worker heartbeat 新鲜；ALB两组 targets healthy；切 DNS前后现有 `/health`、`/production` 和其他默认路径保持正常；ECS health 中 commit和 Prompt Release与 manifest一致；相关 CloudWatch alarms无异常。

### Stage 5：受控 Production 验收

通过 n8n 只将明确选择的测试 Case 发送到 ECS endpoint，并分别验证 Case intake/idempotency、lifecycle、异步 jobs、persisted AI messages、delivery ledger、Zendesk status/ownership/comment visibility、必要的 email/Slack，以及证明客户可见副作用的 Zendesk external readback。

可观察结果：测试 Case 完全通过 ECS-only stack 结束，并具有一致 provenance 和外部可见证据。

放行门禁：全部测试 Case 有预期 terminal evidence；不存在未解决的 `outcome_unknown`、重复副作用、过期 heartbeat、Task restart 或 critical alarm。

### Stage 6：Production Cutover And EC2 Drain

ECS endpoint上线后确认唯一的 n8n Production workflow/version，发布一个将 destination 从 `https://support.stellarix.space/production` 改为 `https://support.stellarix.space/automation/production` 的版本。新前缀保持 `/production` request body和业务语义，并要求 `X-N8n-Request-Token`；测试 workflow 可以保留为 unpublished，但两条正式 Production workflow 绝不能同时 active。

切换后立即确认新 Case 只被 ECS 接收。EC2 `/production` runtime和Worker保持运行作为独立 backup，但 n8n 不再向其投递新 Case。

可观察结果：所有新 Case 只进入 ECS 一次；旧 EC2 任务继续完成，没有复制或重放。

放行门禁：记录 active n8n version；首批切换后 Case 具有 ECS provenance；EC2 不再收到新 intake；两个环境均无重复外部副作用。

### Stage 7：观察、回滚演练和 Backup 保留

EC2 至少保留 24 小时观察窗口。使用正常流量检查 alarms、latency、Task stability、Worker heartbeat、queue depth、delivery 和 external readback，并在不重放 Case 的前提下演练回滚。

回滚是发布上一版 n8n workflow，使后续新 Case 回到 EC2。已经进入 ECS 的 Case 继续在 ECS 完成。外部结果不明确时停止并进入 reconciliation，不做自动重试。

观察窗口通过后不停止 EC2 `/production`。持续对 backup health和build provenance做低频检查；任何回滚都只接收后续新 Case，不能重放 ECS 已接收任务。

## 部署和回滚契约

ECS 不会通过重命名晋升一个正在运行的容器。它会从 immutable digests 启动新 Task set，完成健康验证，切换 ALB traffic，再排空旧 Task set。

首次迁移时 ECS queue 为空，并与 EC2 queue 隔离，因此 ECS Worker 可以在第一条 ECS Case 到达前完全 ready。后续 ECS 原地发布需要先明确并验证 queue compatibility window；这个要求不能被解释为在迁移阶段 1 提前建立 Preproduction。

Cloudflare 将 `support.stellarix.space` 从 EC2 A record切到 ALB后，ALB default rule继续把 `/production` 和其他现有路径转发到 EC2，只有 `/automation/production*` 进入 ECS。DNS切换和 n8n路径切换是两个独立门禁；回滚只改变后续 Case 的 n8n destination，不能跨 database/schema复制已完成或在途 Case。

## 相关文件和入口

- `docs/project/tasks/p1-53.json`：canonical scope、status、next action 和 evidence。
- `backend/Dockerfile.automation`：当前镜像 roles 和 Production 物理隔离。
- `backend/automation_production_runtime.py`：Production API 和 health contract。
- `backend/route_service.py`：无副作用 Route service 和 health contract。
- `backend/worker.py`：需要从 `backend.main` 拆出的 Worker 入口。
- `backend/rag_api.py`、`backend/rag_worker.py`：RAG runtime roles 和 Prompt Release 初始化。
- `backend/scripts/prompt_release.py`：Prompt Release validate/sync primitive。
- `deployment/build_automation_release.sh`：当前 local-only release builder。
- `deployment/deploy_automation_production_blue_green.sh`：需要保留安全语义的历史单机流程，不是 ECS 实现基础。
- `deployment/bootstrap_automation_production_schema.sh`：需要适配到一次性 ECS Task 的 schema contract。
- `backend/tests/test_build_automation_release.py`、`backend/tests/test_automation_production_runtime_contract.py`、`backend/tests/test_production_blue_green_behavior.py`、`backend/tests/test_prompt_versioning.py`、`backend/tests/test_prompt_versioning_postgres.py`：现有相关 contract tests。

如果仓库当前惯例要求不同的文件组织，执行者可以做等价调整，但不得改变上述 runtime、release、security、cutover 和 acceptance contracts。

## 验证

每个阶段运行最窄且能直接证明结果的检查。最终实现至少需要：

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest \
  backend.tests.test_build_automation_release \
  backend.tests.test_automation_production_runtime_contract \
  backend.tests.test_production_blue_green_behavior \
  backend.tests.test_split_environment_deployment \
  backend.tests.test_prompt_versioning

rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest \
  backend.tests.test_prompt_versioning_postgres

rtk terraform fmt -check -recursive
rtk terraform validate
rtk git diff --check
rtk python3 scripts/generate_project_overview.py --check
```

基础设施和 live gates 还必须证明：ECR tag 不可覆盖且 digest 可解析；image scan 达标；ECS Services 达到 desired count 1；ALB 的 ECS和EC2 target均在切流前 healthy；Worker heartbeat 新鲜；`/health` provenance 匹配 manifest；CloudWatch 告警生效；受控 Case 具备完整内部证据和 Zendesk readback；切换没有双 intake 或重复副作用；回滚只改变后续 Case。

## 验收标准

以下条件全部满足后，迁移阶段 1 才算完成：

1. Production-safe Route、API、Worker、RAG API、RAG Worker 镜像来自同一干净 commit，并以 immutable ECR digest 保存。
2. Production 镜像物理排除 rerun/reset，Worker 不再依赖完整应用镜像。
3. Release manifest 绑定 commit、Prompt Release、全部 image digests、schema revision 和 Terraform revision。
4. ECS Production 与 EC2 runtime resources 隔离；成本优先阶段 Tasks desired count 1并以 public IP出站，所有 ingress由最小 security groups限制，不能直接从公网访问应用端口。
5. Schema 和 Prompt Release 在应用 ready 前完成；长期 Tasks 没有 DDL credential。
6. Worker heartbeat、ALB health、build provenance、logs 和 alarms 形成可观察部署门禁。
7. 受控测试 Case 通过 external Zendesk readback 以及所需 email/Slack evidence。
8. 唯一 n8n Production workflow 将新 Case 只发送到 `/automation/production`，并可受控切回 `/production`。
9. ECS和EC2使用隔离 schema/Redis/queue，既有任务不迁移、不重放且没有重复副作用。
10. 回滚已验证，24 小时观察门禁通过，EC2 `/production` 继续作为健康 backup保留。

## 执行暂停条件

遇到以下任一事实时，执行者必须在受影响的变更前暂停并确认：

1. 实际 RDS、VPC、subnet、Cloudflare DNS 或 ACM certificate 与 Stage 0 盘点存在实质差异。
2. n8n Production intake 无法在不造成重复或漏事件的情况下维持唯一 active destination。
3. Production-safe Worker 或 RAG 镜像无法在不改变业务行为的情况下拆分。
4. ECS 切换后仍必须依赖某个 EC2 application container。
5. 必要外部副作用无法安全验证或处于 `outcome_unknown`。
6. Terraform 计划删除、停止或破坏现有 EC2 `/production` backup，或要求 ECS runtime依赖 EC2 application container。
