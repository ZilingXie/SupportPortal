# WeKnora 独立部署（阶段一：独立部署与 Web 可用）

本文件是 WeKnora 并行建设阶段一的部署 Runbook 与事实记录。计划名称：WeKnora 并行建设计划（阶段一：独立部署与 Web 可用）；任务号 `p2-188`（Function `weknora-standalone-deployment`）。

- 目标入口：<https://supportcenter.stellarix.space/dashboard/weknora/>
- 阶段一边界：独立部署与 Web 可用性验证，仅非业务测试文档。n8n 接入、Summary/Review 治理恢复、Hermes 切换、历史数据迁移均属后续阶段。
- 部署基线（read-only，2026-10-07T12:18Z，账户 891612554546 / us-east-1）见下文「只读基线」。

## 只读基线（2026-10-07T12:18Z）

### 域名与 ALB

- `supportcenter.stellarix.space` → `supportportal-production-alb-1001190104.us-east-1.elb.amazonaws.com`（CNAME 由外部 DNS 管理，Zone 不在 Route53；仅 `support.agora.io`、`cselearning.club`、`preproduction.supportportal.local` 在本账户 Route53）。
- 唯一 ALB `supportportal-production-alb`（VPC `vpc-0125f57b2ec2f0423`，SG `sg-0fba25adcbdf00ac9`）；443 listener `...:926cb6cec22a5cff`，证书 ACM `5348b5fc-c6e1-4912-ac60-824dbe67a2a5` = `supportcenter.stellarix.space`（ISSUED）。
- 443 现有规则（隔离基线）：`/automation/production`(10→production-tg)、`/automation/preproduction`(20→preproduction-tg)、`/v1`(101→hermes prod TG)、`/dashboard/hermes`(102)、`/dashboard/memory`(103)、`/auth/password-login`(104)，102-104 → `supportportal-preprod-ui-tg2`（目标 `172.31.32.231:8080` healthy = preproduction hermes 任务 ENI 的 ui-proxy 容器）。`/dashboard/weknora` 未占用；本部署新增规则优先级 **105**。

### 集群与服务（隔离验收基线）

| 集群 | 服务 | 状态 | task definition（基线） |
| --- | --- | --- | --- |
| supportportal-production（FARGATE/FARGATE_SPOT） | api / route / worker / hermes | ACTIVE 1/1 | api:46 / route:40 / worker:44 / hermes:3 |
| supportportal-preproduction（无 capacity provider） | api / route / worker / hermes | ACTIVE 1/1 | api:108 / route:107 / worker:108 / hermes:39 |

Preproduction 任务全部 FARGATE、公网子网 `assignPublicIp=ENABLED`、SG `sg-0845c28285f5909f3`（`supportportal-preproduction-ecs`）。镜像：api/route/worker 用 `supportportal/preproduction` ECR digest（123b1fbc…/530cc009…/ad6b8e44…），hermes 用 `supportportal/hermes` ECR 多容器（memory-core e4c0f4e6…、hermes e473e5ed…、memory-panel d3e9f9a3…、ui-proxy 284be593…、knowledge ee2334a9…）。

### 网络 / 存储 / 其他

- VPC `vpc-0125f57b2ec2f0423`（默认 VPC）：6 个公网子网（us-east-1a `subnet-0c07dc4f65d7af52b`、1b `subnet-0d7cb079536f8c2da`、1c `subnet-0ceade521cfdabeb2`、1d `subnet-00c72fbdeb1424eab`、1e `subnet-04e42d06d21174428`、1f `subnet-09d938544707f61d1`），全部 mapPublicIpOnLaunch；单路由表 `rtb-04449d61f9c17c19d` `0.0.0.0/0→igw-0246a0889648aa00f`，**无 NAT**（出网一律公网 IP）。
- RDS 仅 `n8n-postgres-db`（postgres 17.9 db.t4g.micro，共享 Automation 库，**不适用** WeKnora——需要 ParadeDB 的 `pg_search`/`vector`/`pg_trgm` 扩展）。
- EFS 仅 `supportportal-production-graph-cache`（不共用）。
- ECR：`supportportal/{production,preproduction,hermes,build-cache}`（无 weknora）。
- S3：无 weknora 桶（新增专属桶，不触碰既有桶）。
- CloudMap 私有命名空间：`preproduction.supportportal.local`（新增 `weknora.supportportal.local`）。
- SSM 参数前缀：`/supportportal/preproduction`(36)、`/supportportal/production`(14)（新增 `/supportportal/weknora`）。
- 无 ASG / EC2 容量提供程序（ParadeDB 需新建专属 ECS EC2 容量）。
- EC2 实例清单含 `ragflow-kb`（t3.xlarge，running）——**不使用**（计划明确排除）。

## 源码基线

- fork 仓库：`~/Desktop/personal_proj/WeKnora`，分支 `supportportal-write-contract`，基线 commit `79c4b2aaf23a8eb359db465f5eff214bb988242e`（Round-14：versioned migrations PG 000116 + SQLite 000035），工作区干净，无 remote。
- 部署形态对应 compose 五件套：`frontend`（nginx:80 → app:8080）、`app`（Go，:8080，`/health`）、`docreader`（gRPC :50051）、`postgres`（`paradedb/paradedb:v0.22.6-pg17`）、`redis`（`redis:7.0-alpine`，appendonly）。
- 迁移：fresh 库走 `AUTO_MIGRATE` + `migrations/versioned`（000000–000116）；`migrations/paradedb/00-init-db.sql` 是旧库引导路径，不用于 fresh 部署。
- S3 存储：`STORAGE_TYPE=s3` 且 AK/SK 留空时走 AWS 默认凭据链（task role）。
- 已知源码缺口（fork 侧修复，见「fork 适配提交」）：`sslmode=disable` 硬编码 3 处；前端子路径仅 SPA 层就绪。

## 部署设计（阶段一）

| 部分 | 方案 |
| --- | --- |
| 集群 | 新建专属集群 `supportportal-weknora`（FARGATE + FARGATE_SPOT + 专属 EC2 容量提供程序） |
| frontend | ECS Fargate 服务 `weknora-frontend`（0.5 vCPU/1GB），挂新 TG `supportportal-weknora-frontend-tg`（ip:80），ALB 443 优先级 105 `/dashboard/weknora*` |
| app | ECS Fargate 服务 `weknora-app`（2 vCPU/4GB），CloudMap `app.weknora.supportportal.local` |
| docreader | ECS Fargate 服务 `weknora-docreader`（2 vCPU/4GB），CloudMap `docreader.weknora.supportportal.local`（gRPC 仅内网） |
| PostgreSQL | `paradedb/paradedb:v0.22.6-pg17`（ECR 副本），ECS EC2 服务 `weknora-paradedb`，专属容量实例 t3.xlarge（root EBS 100GB gp3），docker volume `paradedb-data` 持久化，CloudMap `paradedb.weknora.supportportal.local` |
| Redis | `redis:7.0-alpine`（ECR 副本），ECS EC2 服务 `weknora-redis`，docker volume `redis-data`（appendonly），CloudMap `redis.weknora.supportportal.local` |
| 文档对象 | 专属私有 S3 桶 `supportportal-weknora-docs-891612554546-us-east-1`（app task role 访问，默认凭据链） |
| 备份 | 专属桶 `supportportal-weknora-backup-891612554546-us-east-1`（pg_dump 基础备份，验收「备份恢复」用） |
| 发布与日志 | ECR `supportportal/weknora`（IMMUTABLE+扫描）、日志组 `/ecs/supportportal/weknora`、SSM `/supportportal/weknora/*`（SecureString 不入 Terraform）、CodeBuild 专属构建项目 |
| Web 入口 | 仅新增 `/dashboard/weknora` 与子路径 listener 规则；frontend 容器即专属代理（nginx 前缀 location + API/文件/流式转发） |
| 认证 | WeKnora 自身登录；`DISABLE_REGISTRATION=true`；bootstrap 管理员后关闭公开注册 |
| TLS | ParadeDB 服务端 TLS（自签 CA，verify-ca）；app↔DB 连接 `DB_SSLMODE=verify-ca` + CA 文件注入（fork 可配置化）；Redis/DocReader 仅内网+SG，不暴露公网 |

不使用 ragflow-kb；不借用 Hermes 数据卷/数据库；不修改现有 SupportPortal/AgentMemory/Hermes 路径与配置。

### 数据持久化与恢复边界（阶段一口径）

- 任务重建不丢数据：ParadeDB/Redis 数据在专属容量实例的 docker volume（root EBS）上，任务重启/重建数据保留（验收项）。
- 容量实例替换（ASG 仅 min=max=1，实例不健康时才替换）：root EBS 不自动迁移，数据以备份恢复为准——这是阶段一已知限制，备份恢复路径必须验证（验收项）。
- 迁移：单实例 app（desiredCount=1，maxPercent=100/minHealthy=0）+ `AUTO_MIGRATE`，避免多实例并发迁移。

### 费用估算（us-east-1 按需，月度，2026-10 牌价）

| 项 | 规格 | 估算 |
| --- | --- | --- |
| EC2 容量实例 | t3.xlarge（4vCPU/16GB） | ≈ $122 |
| root EBS | 100GB gp3 | ≈ $8 |
| Fargate app | 2 vCPU/4GB 常驻 | ≈ $72 |
| Fargate docreader | 2 vCPU/4GB 常驻 | ≈ $72 |
| Fargate frontend | 0.5 vCPU/1GB 常驻 | ≈ $18 |
| ALB | 复用现有（新增 1 规则+TG） | ≈ $0–5 |
| ECR/S3/CloudWatch/SSM | 测试规模 | < $10 |
| CodeBuild | general.large 按需（偶尔构建） | ≈ $1–3/次 |
| 合计（稳态） |  | **≈ $300/月** |

## 实施顺序与状态

1. [x] 只读基线核对（本文档）
2. [x] WeKnora 专属 Terraform（新根 `infra/terraform/weknora/`；foundation 46 资源 + 5 服务两次 apply，均为 add-only）
3. [x] fork 适配提交（分支 `supportportal-weknora-deploy`：3b0c8d6d 子路径+DB TLS → 797d6321 静态 alias 修复 → 5c38e217 upstream 动态解析）
4. [x] 固定 commit 源码归档 → S3（release-evidence 桶 weknora/src/source.tar.gz，版本化+sha256）；CodeBuild `supportportal-weknora-image-build` 构建五镜像 → ECR `supportportal/weknora`
5. [x] 基础镜像 ECR 副本（paradedb v0.22.6-pg17 / redis 7.0-alpine，随构建转推）；SSM `/supportportal/weknora/*` 12 参数（含 DB TLS CA/证书）
6. [x] 部署（data → docreader → app → frontend 依赖序；ALB 105 规则已接；公网入口 HTTP 200）
7. [x] 验收表逐项验证 + 备份恢复演练（文档闭环/异常表现两项待模型凭据，见下）
8. [x] 记录与交接（任务 JSON、运维文档、发布清单）

### 阶段一验收表状态（R3 修复轮后，2026-10-08；口径按 R2 独立验收收窄）

R2 独立验收结论=**未通过**（四项发现：前端文件请求/重登录子路径缺口、发布脚本回滚误判成功、数据守卫默认可被回退、恢复验证证据不足+清理误删风险）。R3 修复内容见下文「R3 修复记录」。

| 验收项 | 状态 | 证据口径（按 R2 验收收窄） |
| --- | --- | --- |
| 构建可复现 | ✅（未重建复验） | 固定源码归档（commit+VersionId+sha256）的 CodeBuild 成功记录 + 三组件 ECR digest 与 :3 定义一致（独立验收方核对）；未做过"同一归档重跑构建"的复现实验 |
| 访问控制 | ✅（登录沿用执行方证据） | 未授权知识库请求 401、注册关闭配置经独立回读核实；管理员登录成功证据来自执行方自测 |
| Web 路由 | 🔧 R3 修复待复验 | 首页/静态/深层 200 与 API 代理正常（R2 核实）；但受保护文件请求与 token 失效重登录原落域名根路径（R2 P1 发现）——R3 已修（fork 714065ba），:4 部署后复验 |
| 文档闭环 | ⏸ | 待模型凭据后执行：上传→处理→检索→回读 |
| 异常表现 | ⏸ | 同上 |
| 持久化 | 部分 | 账号/PGDATA 保留已证（守卫启动通过+任务重建后登录数据完好）；知识对象与索引的持久化验证待文档闭环后补 |
| 备份恢复 | 部分 | dump 存在、可恢复、账号数据保留（R2 认可部分）；知识检索与文件回读未证明——R3 已补基线清单对比与知识断言（--expect-knowledge），文档闭环后执行 |
| 现有测试隔离 | ✅ | R2 核实当前服务与入口回读正常；历史全过程无影响依赖部署前后证据（R1 基线 vs R2 终检对照，见 R2 验收记录） |

模型凭据（LLM + embedding，OpenAI 兼容）为文档闭环/异常表现两项的唯一前置；已停在准备阶段（p2-188 blockers）。

### R3 修复记录（2026-10-08，响应 R2 独立验收四项发现）

1. **前端子路径缺口（P1）**：受保护文件请求（protectedFileAccess 四条 URL）、token 失效重登录与 TenantInfo 两处登出跳转（/login）、租户切换落地页（/platform/knowledge-bases）、多模态测试原生 fetch 五处全部改为经 `api-base`（新增 `getRouterBase()` 与测试 override 缝隙，`setApiBaseURLOverrideForTests`）。回归：`frontend/src/utils/subpathPrefix.test.mjs` 7 用例（子路径登录跳转/文件 URL 前缀/根部署回归/检测正则契约），全套 397 tests 396 pass 1 skip。npm test 只发现 `.test.mjs`（63 个）——`.test.ts` 不在套件内，新测试按 mjs 约定落位；根 tsconfig 补 paths 供 tsx 解析 `@/` 别名。
2. **发布脚本回滚误判（P1）**：`wait_stable` 重写为绑定目标 task definition + rolloutState + desired/running/pending 计数；PRIMARY 回退旧定义或 rolloutState=FAILED 即失败退出。回归：`deployment/weknora/tests/run_deploy_tests.sh` 15 项（正常完成/仍在部署持续等待/回滚失败且不触达公网检查/FAILED 失败/守卫默认开/initial-bootstrap 显式关/恢复清理不误删/两轮容器名唯一）。
3. **数据守卫默认回退（P1）**：`register_weknora_task_definitions.sh` 守卫默认改为开启，仅 `--initial-bootstrap` 显式允许空库；运行时回归 `run_pgdata_guard_runtime_test.sh`（真实 paradedb 镜像：空卷+守卫=拒绝且零写入；显式初始化成功；守卫对既有数据放行）。
4. **恢复验证与清理（P1 缺口+P2）**：backup 脚本新增备份前基线计数清单（`<dump>.manifest.json` 落 S3，含 sourceTaskDefinition）；restore 脚本对比基线计数（不一致即失败）、`--expect-knowledge` 改为精确标题+块数+全文回读、输出明确分层（dbLevelVerified/countsMatchBaseline/knowledgeReadBack/appLevelRetrievalVerified=false 注明应用层检索另验）；清理改为唯一目录+唯一容器名+仅删除本轮自建资源，名称冲突即拒绝。
5. **基线备注**：Preproduction Hermes 验收时点为 :41（R2 报告时点 :40 亦为并行线部署，均非本任务改动）；根区另一线程的 hermes 手册未提交编辑仍在（本任务未触碰）。

R3 撤回 R2 报告中"基础设施和脚本不适用自动化测试"的表述：本轮已为发布判定/守卫/清理补齐针对性回归（stub 边界用例），前端子路径补 7 用例。

## 部署实录（2026-10-07）

### 发布链（可复现构建）

| 项 | 值 |
| --- | --- |
| 源码归档 v1 | commit 3b0c8d6d…，S3 版本 TggfxXbx…，sha256 2b869a76…，构建 d3bc598c SUCCEEDED |
| 源码归档 v2 | commit 797d6321…，S3 版本 w0GWxG0n…，sha256 3cd17029…，构建 1620e085 SUCCEEDED |
| 源码归档 v3 | commit 5c38e217…，S3 版本见 /tmp 或发布记录，构建 SUCCEEDED（22:18 前后） |
| ECR 镜像（v1 digest 示例） | app b8434444… / frontend 8d55d777… / docreader b800aa1c… / base-paradedb 2727f84a… / base-redis 56e4f286… |
| 任务定义 | v1=:1（开放注册，bootstrap）→ v2=:2 → v3=:3（注册关闭 + REQUIRE_EXISTING_PGDATA=true） |
| CodeBuild | BUILD_GENERAL1_LARGE，单次全量 ~15-25 分钟，~$1.5-2.5/次 |

镜像 tag=commit 前缀（app-/frontend-/docreader-），base-* 固定 tag；ECR IMMUTABLE + 生命周期各保 3。

### 事件与修复记录

1. **CodeBuild DOWNLOAD_SOURCE AccessDenied**：S3 版本化源下载需要 `s3:GetObjectVersion`（非仅 GetObject）——已在 terraform iam.tf 修复并增量 apply。
2. **app 首任务 DNS 竞态**：terraform 一次性创建五服务时，app 先于 paradedb 的 CloudMap 注册启动 → `lookup paradedb.weknora.supportportal.local: no such host` 退出，第二任务自愈。正式链路用 `deploy_weknora_services.sh` 依赖序部署可避免。
3. **frontend nginx 静态服务 500（内部重定向循环）**：URL 前缀 ≠ 磁盘布局，root 把 `/prefix/index.html` 映射到不存在路径 → try_files 回退自循环。修复=静态 location 全部改 alias（797d6321），本地 podman+真实 dist 双模式验证后上线。
4. **`/dashboard/weknora` 301 scheme 降级**：nginx absolute_redirect 默认拼 `$scheme://`（容器内为 http）→ 修复=redirect include 中 `absolute_redirect off`，Location 保持相对路径（https 得以保留）。
5. **assets 404**：alias 替换的是被匹配的 location 前缀——`location /prefix/assets/` 的 alias 必须指向物理 `…/html/assets/`。
6. **app 任务重建后 API 502（upstream IP 缓存）**：nginx 在配置加载时解析一次 proxy_pass 主机名，app 任务换 ENI 后仍打旧 IP。修复=resolver(DNS_RESOLVER 变量，默认 127.0.0.11 保持上游 compose 契约) + `set $app_upstream` 运行时变量 + 全部代理 location 统一 `rewrite 剥前缀 + proxy_pass 变量`（5c38e217）。ECS 侧 frontend 任务注入 DNS_RESOLVER=169.254.169.253。
7. **macOS bsdtar 无 --transform**：归档脚本改 staging 目录方式追加 WEKNORA_COMMIT_INFO。
8. **管理员 bootstrap**：注册开放态下经公网真实路径注册 `weknora-admin@stellarix.space`（密码存 SSM `/supportportal/weknora/admin_password`），app 重启后 bootstrap 自动提权（日志：promoted user f4fbfdb0… to system admin）。注册模式经 `PUT /api/v1/admin/settings/auth.registration_mode=invite_only` 即时关闭，任务定义 :3 同步 DISABLE_REGISTRATION=true。
9. **AWS login 会话过期**（22:2x）：`aws login` 需浏览器交互恢复——恢复后继续 :3 注册与部署。
10. **备份通道改道**：原设计 SSM 端口转发→paradedb 任务 ENI 不可达（ECS awsvpc 任务网络过滤 host→task ENI 流量，即便 data 组自引用放行）。改为 SSM `docker exec` 容器内 localhost pg_dump（localhost trust）→ `docker cp` → 预签名 PUT 上传（本机 CLI 的 `s3 presign` 仅支持 GET，SigV4 PUT 用 python3 标准库签名）。

### 启动验证（v2 时点）

- 五服务 ACTIVE 1/1；app `/health` 200 连续；CloudMap paradedb/redis/docreader/app 各 1 实例 HEALTHY。
- 公网矩阵（v2）：`/dashboard/weknora`→301(https 保留)、`/`→200、`/config.js`→200、`/tdesign-icons/…`→200、hash 资产→200、深层路由→200、`POST /api/v1/auth/login` 空 body→400（经 app 校验）。
- 隔离复检：ALB 规则 10/20/101-104 与两集群 8 服务 task def 均未变（仅新增 105）。

### 已知限制（阶段一）

- frontend nginx 依赖 CloudMap 解析 app；:2 及以前版本在 app 任务重建后需重启 frontend（:3 起动态重解析根治）。
- 容量实例替换（ASG min=max=1，仅不健康时触发）不自动迁移 root EBS 上的 docker volume——数据以备份恢复为准（备份脚本+恢复演练见验收）。
- docreader 与 app 拆分后无共享卷：`/tmp/docreader` 图片直传回退路径不可用（主链路不写盘，compose 注释确认）；chat 内图片回显如有异常归因于此。
- redis 无 TLS（VPC 内 + SG 限制 + requirepass）；ParadeDB TLS=verify-ca 自签 CA。
