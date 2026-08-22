# Automation Release Promotion

三套 split Automation 环境使用 release manifest 管理镜像版本，不要求操作人员手工填写六个 image digest。

## 1. 构建 Release

在 Docker 已安装、并且已经完成 registry 登录的构建主机上，从干净的目标 commit 执行：

```bash
./deployment/build_automation_release.sh \
  --registry registry.example/supportportal \
  --release-id release-20260822-001
```

脚本只构建三个 Dockerfile role：

- `route`：Staging、Preproduction、Production 复用同一个 immutable digest。
- `automation`：Staging 和 Preproduction 复用同一个包含 rerun 的 digest。
- `production`：Production 使用独立 digest，镜像中不包含 rerun/reset 执行面。

构建、push 和 digest 读取都成功后，脚本生成：

```text
.deployments/releases/release-20260822-001.env
```

该文件只保存 release 元数据和六个 image pointer，不保存 registry 密码或 Zendesk 凭据。

## 2. 晋升环境

将 release manifest 放到 EC2 仓库的同一路径后，使用 `--branch` 让部署脚本同步目标 commit，再逐环境部署：

```bash
./deployment/deploy_ec2.sh --branch main --environment staging --release release-20260822-001
./deployment/deploy_ec2.sh --branch main --environment preproduction --release release-20260822-001
DEPLOY_PRODUCTION_APPROVED=1 \
  ./deployment/deploy_ec2.sh --branch main --environment production --release release-20260822-001
```

`--release` 会加载 manifest 并导出六个 image pointer，随后由 Compose pull 并启动选定环境。它不会重新 build 镜像，也不会修改 `.env`。

split deployment 现在遵循 `--branch` 和 `--skip-pull`：默认会检查工作树、fetch 目标分支并执行 fast-forward pull；使用 `--skip-pull` 时，调用方必须自行保证工作树已经处于目标 commit。

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

未传 `--release` 时，脚本仍接受 `.env` 中的六个 image 变量，供已有主机迁移和紧急恢复使用。新发布流程应始终使用 release builder 和 `--release`，避免手工编辑 digest。
