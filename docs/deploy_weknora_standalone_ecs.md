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
2. [ ] WeKnora 专属 Terraform（新根 `infra/terraform/weknora/`，只含新资源）
3. [ ] fork 适配提交（子路径 + DB SSLMODE 可配置化，基于 79c4b2aa）
4. [ ] 固定 commit 源码归档 → S3；CodeBuild 构建 app/frontend/docreader 镜像 → ECR（digest 记录）
5. [ ] 基础镜像 ECR 副本（paradedb/redis）；SSM 参数创建
6. [ ] 受控迁移与依赖启动顺序（data → docreader → app → frontend → 接入 ALB 105）
7. [ ] 验收表逐项验证 + 备份恢复演练
8. [ ] 记录与交接（任务 JSON、运维文档、发布清单）

模型凭据（LLM + embedding，OpenAI 兼容）在部署前须列清并核对额度；缺失时停在准备阶段（当前为唯一 blocker，见 p2-188）。
