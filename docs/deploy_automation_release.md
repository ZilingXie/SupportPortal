# Automation Release Promotion

三套 split Automation 环境使用 release manifest 管理镜像版本，不要求操作人员手工填写六个 image pointer。本流程针对单台 EC2：EC2 在本机 build 镜像，本机 Compose 运行镜像，不依赖应用远端 registry。

数据库不需要为 split 环境新建 PostgreSQL 实例或数据库。部署脚本默认使用现有项目 DSN：staging/preproduction 使用 `TICKET_DB_DSN`，production 使用 `PRODUCTION_TICKET_DB_DSN`；三个环境分别写入独立表 `automation_executions_staging`、`automation_executions_preproduction`、`automation_executions_production`，不会共享 `automation_executions`。schema 仍分别为 `supportportal_staging`、`supportportal_preproduction`、`supportportal_production`。若 `.env` 显式设置对应的 `AUTOMATION_*_DB_DSN`，显式值优先，但部署脚本不会把解析出的 DSN 回写到 `.env`。

## 1. 构建 Release

在将要运行 SupportPortal 的 EC2 上，从干净的目标 commit 执行：

```bash
./deployment/build_automation_release.sh \
  --release-id release-20260822-001
```

脚本只构建三个 Dockerfile role：

- `route`：Staging、Preproduction、Production 复用同一个本地 image tag 和 image ID。
- `automation`：Staging 和 Preproduction 复用同一个包含 rerun 的本地 image tag 和 image ID。
- `production`：Production 使用独立 role 镜像，镜像中不包含 rerun/reset 执行面。

三次本地 build 和 image ID 读取都成功后，脚本生成：

```text
.deployments/releases/release-20260822-001.env
```

该文件只保存 release 元数据、六个本地 image pointer 和对应 image ID，不保存 registry 密码或 Zendesk 凭据。不要把该 manifest 或本机镜像复制到另一台主机；当前流程的发布边界是同一台 EC2。

## 2. 晋升环境

builder 已在 EC2 本机生成 manifest 后，使用 `--branch` 让部署脚本同步目标 commit，再逐环境部署：

```bash
./deployment/deploy_ec2.sh --branch main --environment staging --release release-20260822-001
./deployment/deploy_ec2.sh --branch main --environment preproduction --release release-20260822-001
DEPLOY_PRODUCTION_APPROVED=1 \
  ./deployment/deploy_ec2.sh --branch main --environment production --release release-20260822-001
```

`--release` 会加载 manifest，检查目标本地 image tag 的 image ID 与 manifest 一致，然后由 Compose 使用本地镜像启动选定环境。它不会执行 `docker compose pull`、不会重新 build 镜像，也不会修改 `.env`。如果镜像不存在或 tag 已被覆盖，部署会在创建 automation 网络和修改 Compose 之前失败。

split deployment 现在遵循 `--branch` 和 `--skip-pull`：默认会检查工作树、fetch 目标分支并执行 fast-forward pull；使用 `--skip-pull` 时，调用方必须自行保证工作树已经处于目标 commit。

每次 split deployment 还会检查官方 nginx 容器是否已加入 `supportportal_automation_edge`。缺失时脚本使用 `docker network connect` 幂等接入，不重建或重启 nginx；nginx 未运行或接入失败时，脚本会在启动目标 split 服务前明确失败。这样先于 split 环境启动的旧 nginx 也能通过 Docker DNS 解析 `automation_staging`、`automation_preproduction` 和 `automation_production`。

automation 网络不再以 `--internal` 创建：Route 容器需要出站访问 LLM API，Automation 容器需要出站连接项目数据库。部署脚本发现既存网络是 internal 时会 fail closed；此时先停止对应 split compose project，执行 `docker network rm <network>` 删除该网络，再重新部署让其以出站可用的形式重建。

Route token 仍由运维配置在 `.env`，每个环境使用不同值；例如可在 EC2 上分别执行 `openssl rand -hex 32` 生成三个 token，再填入 `ROUTE_STAGING_SERVICE_TOKEN`、`ROUTE_PREPRODUCTION_SERVICE_TOKEN` 和 `ROUTE_PRODUCTION_SERVICE_TOKEN`。不要把 token 放进 release manifest 或提交到 Git。

三个环境共用同一个执行 token：`.env` 的 `n8n_request_token`（与主栈 n8n 集成、旧 Zendesk 同步端点同源同值，部署脚本会校验其必填；原先独立的三个 `AUTOMATION_*_EXECUTION_TOKEN` 已废弃删除）。Automation 的 `/v1/cases`、rerun、reset、reconcile、executions 与登录换取的执行端点都要求 `X-N8n-Request-Token: <n8n_request_token>` 头；token 缺失或错误时执行请求返回 401，未在 `.env` 配置时所有执行请求都会被拒绝。三个 Automation UI 各自提供 Execution token 输入框（输入值即该 token），输入值保存在浏览器 localStorage。

## 3. Production Automation 蓝绿发布

`/automation/production` 的新版本使用独立 candidate Compose project。candidate 必须从同一台 EC2 上已生成的 release manifest 启动；脚本会校验 route/automation image ID，复用现有 `supportportal-automation-production` project 的 production Redis、DB schema/table、queue 和 event channel，不会创建第二套 production Redis。

执行前确认当前 `main` 已包含 runtime upstream mount，并设置 production 批准门：

```bash
DEPLOY_PRODUCTION_APPROVED=1 \
  ./deployment/deploy_automation_production_blue_green.sh \
  --release release-20260822-001
```

流程顺序为：candidate route/automation readiness -> Nginx `nginx -t` -> runtime upstream 原子替换 -> graceful reload -> `/automation/production/health` -> 旧 route/automation drain 360 秒并 stop。脚本使用与 `deploy_ec2.sh` 相同的 `.deploy_ec2.lock`，不会和普通部署并发运行；旧 candidate 的 Compose override 会保存在 `.deployments/automation-production-blue-green/`，以便 rollback 重新启动。

如果切换后的 through-Nginx health 失败，脚本会自动恢复旧 upstream 并 stop candidate。Nginx graceful reload 本身失败时也会恢复旧指针并尝试重新加载旧配置；恢复失败会以非零退出并要求立即检查入口层。手工回滚时，脚本会从旧 candidate 的 Compose override 反推出 release，重新加载对应 manifest 的 route image 和生产资源身份，因此可在新的 shell 进程中可靠启动 drain 后已停止的旧 candidate：

```bash
DEPLOY_PRODUCTION_APPROVED=1 \
  ./deployment/deploy_automation_production_blue_green.sh --rollback
```

首次运行如果发现现有 Nginx 容器没有 `/etc/nginx/runtime` 挂载，脚本只重建 Nginx 容器来安装该挂载，不重建 `/production`、API 或 worker 容器。Nginx upstream 使用 server-scope variable，因此未启用 production profile 时不会在 Nginx 启动阶段解析 `automation_production` hostname。

## 4. 验收顺序

- Staging：确认 `/v1/capabilities` 允许 rerun/reset，且 `zendesk=false`；带执行 token 提交一个 case，确认链路执行成功且无任何 Zendesk 出站。
- Preproduction：在 `.env` 配置 `PREPRODUCTION_ZENDESK_SIDE_EFFECTS_ENABLED=1` 和 `PREPRODUCTION_TARGET_TICKET_STATUS`（如 `pending`）并 recreate 容器后，只使用 allowlisted ticket（`PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST`：逗号分隔工单号；`*` 放行全部、过滤交给上游；空拒绝全部），确认 ownership/status 和 internal comment，`public=false`。
- Production：确认 rerun/reset 不存在；p2-109 起 intake 走旧栈语义（分类 → 内部邮件/追问 reply job → 延迟 public 回复，无即时 comment 副作用，`comment_visibility` 不再必填）。先跑 `bootstrap_automation_production_schema.sh` 确保 `supportportal_production` 全套表已建、`automation_production_worker` 运行，再使用受控 ticket 验证 reply job 的 Zendesk public 回复与内部邮件送达。
- 三个开关（`*_ZENDESK_SIDE_EFFECTS_ENABLED`）默认为 0、`AUTOMATION_TARGET_TICKET_STATUS` 默认为空；未显式开启时真实执行会以 `zendesk_side_effects_not_enabled` 或 `automation_target_ticket_status_missing` fail closed，不会写 Zendesk。

## 5. 回滚

部署成功后，`deploy_ec2.sh` 会在 `.deployments/<environment>.manifest` 保存当前和 previous image pointer。回滚时不要再次传入 release：

```bash
./deployment/deploy_ec2.sh --environment staging --rollback
```

回滚只影响指定 split Compose project；Production 回滚仍需要现场的生产批准。

## 6. 迁移兼容

未传 `--release` 时，脚本仍接受 `.env` 中的六个 digest image 变量，供已有主机迁移和紧急恢复使用；这条兼容路径仍可能执行 Compose pull。新发布流程应始终在目标 EC2 上使用 release builder 和 `--release`。

## 7. 本地开发部署（podman）

本地（非 EC2）验证 split 环境改动时，不需要走 EC2 release 流程：

```bash
scripts/workflow/start_local_split_environments.sh              # 构建并启动三环境
scripts/workflow/start_local_split_environments.sh --skip-build # 未改代码时快速重启
```

脚本行为与安全默认：

- 从**当前工作树**构建三个 role 镜像（task worktree 里可直接验证未提交改动；脏工作树的镜像 tag 会带 `-wip` 后缀），build marker 为当前 HEAD。
- 前置条件：root `.env` 有 `TICKET_DB_DSN`。三个 `AUTOMATION_*_EXECUTION_TOKEN` 缺失时自动生成并追加到 root `.env`。`PRODUCTION_TICKET_DB_DSN` 缺失时跳过本地 production 环境。
- 每个环境一个独立 compose project（与 EC2 同名），官方本地栈重启不会波及 split 容器。
- 入口是一个**专用本地 nginx**（默认 `http://localhost:18080/automation/*`，端口可用 `SUPPORTPORTAL_LOCAL_SPLIT_PORT` 覆盖）：官方 nginx 配置硬编码了 Docker 嵌入式 DNS（`resolver 127.0.0.11`），在 podman 下变量 upstream 会全部 502，因此本地 split 栈自带静态 upstream 的独立入口。
- **本地 Zendesk 副作用默认关闭**（fail-closed）且 preproduction allowlist 默认为空：本地执行到 side-effect 阶段会被显式拒绝而不是写 Zendesk。需要本地真实写入时再在 `.env` 显式配置 `*_ZENDESK_SIDE_EFFECTS_ENABLED=1`、`*_TARGET_TICKET_STATUS` 与 allowlist。
- 本地 staging/preproduction 与 EC2 共用同一 RDS 的 execution 表（append-only 记录，按 request_id 幂等），本地验证产生的记录同样落库可查。
- 启动后自动验证：三环境 `/health` 200、未授权 POST 401。
