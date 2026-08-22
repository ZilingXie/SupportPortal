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

Route token 仍由运维配置在 `.env`，每个环境使用不同值；例如可在 EC2 上分别执行 `openssl rand -hex 32` 生成三个 token，再填入 `ROUTE_STAGING_SERVICE_TOKEN`、`ROUTE_PREPRODUCTION_SERVICE_TOKEN` 和 `ROUTE_PRODUCTION_SERVICE_TOKEN`。不要把 token 放进 release manifest 或提交到 Git。

## 3. 验收顺序

- Staging：确认 `/v1/capabilities` 允许 rerun/reset，且 `zendesk=false`。
- Preproduction：只使用 allowlisted ticket，确认 ownership/status 和 internal comment，`public=false`。
- Production：确认 rerun/reset 不存在；使用受控 ticket 分别验证 `comment_visibility=internal` 和 `comment_visibility=external`，并核对 Zendesk readback 与 delivery ledger。

## 4. 回滚

部署成功后，`deploy_ec2.sh` 会在 `.deployments/<environment>.manifest` 保存当前和 previous image pointer。回滚时不要再次传入 release：

```bash
./deployment/deploy_ec2.sh --environment staging --rollback
```

回滚只影响指定 split Compose project；Production 回滚仍需要现场的生产批准。

## 5. 迁移兼容

未传 `--release` 时，脚本仍接受 `.env` 中的六个 digest image 变量，供已有主机迁移和紧急恢复使用；这条兼容路径仍可能执行 Compose pull。新发布流程应始终在目标 EC2 上使用 release builder 和 `--release`。
