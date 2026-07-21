# SupportPortal 线上单一 `.env` 迁移报告

日期：2026-07-21  
适用范围：使用 `deployment/docker-compose.single-host.yml` 和 `deployment/deploy_ec2.sh` 的 EC2 / Docker 单机环境。

## 1. 结论

线上应用运行配置应统一到仓库根目录 `.env`。部署完成后：

1. Docker Compose 只通过根 `.env` 向服务注入应用配置。
2. API 将根 `.env` 只读挂载到 `/run/supportportal/environment-config.env`，Admin Dashboard 只展示配置名，不展示值。
3. `.env.local` 和 `deployment/.env_override` 不再作为运行配置源；确认三轮验证通过后再移出仓库目录。
4. `/etc/supportportal/auto-deploy.env` 必须保留。它只服务于 systemd 定时部署、SES 告警和部署报告，不属于应用 `.env`，不得合并或删除。
5. 不要用 `.env.example` 覆盖线上 `.env`，也不要在终端、工单、PR 或日志中打印真实配置值。
6. 如果线上启用了 systemd auto-deploy，迁移期间必须暂停 timer；当前自动部署链路不会动态生成 build metadata，完成相应代码加固前不要重新启用。

建议把本次操作安排在可回滚的维护窗口，并暂时停用自动部署 timer。

## 2. 当前线上部署能力边界

合入 PR #640 和 #641 后，本地 Podman 重启脚本具备 image-ID 自动回滚，但线上 `deployment/deploy_ec2.sh` 当前只有以下保证：

- `docker compose build` 在 `down` 之前执行，build 失败不会停止旧服务。
- `docker compose up`、内部健康检查或外部健康检查失败时，脚本会返回非零并输出诊断。
- 线上脚本尚未自动恢复上一 image ID。
- 线上脚本尚未自动设置 `APP_BUILD_REF`、`APP_BUILD_TIME` 和唯一 `APP_RUNTIME_IMAGE` tag。

因此本次线上迁移必须在部署前手工保留上一 image ID，并为新部署导出动态 build metadata。不要把静态 `APP_BUILD_REF` 或 `APP_BUILD_TIME` 长期写进 `.env`。

## 3. 变更窗口前检查

以下命令均在生产仓库根目录执行。命令只输出状态、commit、容器和配置名，不应输出配置值。

```bash
cd ~/SupportPortal

# 防止迁移过程中 timer 启动另一轮部署。
sudo systemctl stop supportportal-auto-deploy.timer
sudo systemctl is-active supportportal-auto-deploy.service || true

git status --short --branch
git fetch origin --prune
git rev-parse HEAD
git rev-parse origin/main

docker compose --env-file .env \
  -f deployment/docker-compose.single-host.yml ps

curl -fsS http://127.0.0.1:${NGINX_HOST_PORT:-80}/health
curl -fsS https://support.stellarix.space/health
```

开始前必须满足：

- 生产仓库位于 clean `main`，没有未提交修改。
- 没有正在运行的 `supportportal-auto-deploy.service`。
- 当前内部和外部 `/health` 可访问；记录当前 commit、health build ref 和 storage 状态。
- `docker compose ps -q api` 能返回正在运行的 API container。
- 已确认根 `.env` 存在。

## 4. 建立外部备份和镜像回滚点

备份目录必须在仓库外，权限为 `0700`；备份文件权限为 `0600`。不存在的旧 env 文件跳过即可。

```bash
cd ~/SupportPortal
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="${HOME}/supportportal-env-backup-${timestamp}"
install -d -m 0700 "${backup_dir}"

install -m 0600 .env "${backup_dir}/root.env"
test ! -f .env.local || install -m 0600 .env.local "${backup_dir}/local.env"
test ! -f deployment/.env_override || \
  install -m 0600 deployment/.env_override "${backup_dir}/deployment.env_override"
```

然后基于正在运行的 container 保存上一 image ID。不要只记录可被下一次 build 覆盖的同名 tag。

```bash
compose=(docker compose --env-file .env -f deployment/docker-compose.single-host.yml)
api_container="$("${compose[@]}" ps -q api)"
test -n "${api_container}"

previous_image_id="$(docker inspect --format '{{.Image}}' "${api_container}")"
rollback_tag="localhost/supportportal-app:production-rollback-${timestamp}"
docker image tag "${previous_image_id}" "${rollback_tag}"
```

在整个迁移完成前保留当前 shell、`backup_dir` 和 `rollback_tag`。不要把它们写入仓库文件。

## 5. Gate 1：使用旧配置部署新代码

第一轮只更新代码，不编辑或删除任何真实 env 文件。

```bash
cd ~/SupportPortal
git switch main
git pull --ff-only origin main

# 必须包含单一 env 和同 tag rollback 修复。
git merge-base --is-ancestor fab53340718a HEAD

export APP_BUILD_REF="$(git rev-parse --short=12 HEAD)"
export APP_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export APP_RUNTIME_IMAGE="localhost/supportportal-app:${APP_BUILD_REF}"
export DEPLOY_HEALTH_TIMEOUT_SECONDS=180

docker compose --env-file .env \
  -f deployment/docker-compose.single-host.yml config >/dev/null

./deployment/deploy_ec2.sh \
  --branch main \
  --domain support.stellarix.space \
  --skip-pull
```

Gate 1 验收：

- 内部和外部 `/health.status` 均为 `ok`。
- `app_build.ref` 等于 `APP_BUILD_REF`，不能是 `unknown`。
- `ticket_storage=postgres`、`knowledge_storage=postgres`、`rag_service=ok`。
- Admin 可以登录，其他 Tab 正常。
- `Environment Config` 不为空；如果旧 `.env` 有 `OPENAI_API_KEY` 和 `TICKET_DB_DSN`，页面应能看到这两个名称。
- 浏览器 Network response 和 DOM 不包含任何 `.env` value 或原始 `KEY=value` 行。

Gate 1 失败时不要进入配置迁移，按第 9 节恢复上一镜像。

## 6. 盘点并合并线上配置

先盘点文件中的 key 名，不打印 value：

```bash
python3 - <<'PY'
import re
from pathlib import Path

pattern = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=')
for filename in ('.env', '.env.local', 'deployment/.env_override'):
    path = Path(filename)
    if not path.is_file():
        print(f'{filename}: absent')
        continue
    names = []
    for line in path.read_text(encoding='utf-8').splitlines():
        match = pattern.match(line)
        if match:
            names.append(match.group(1))
    duplicates = sorted({name for name in names if names.count(name) > 1})
    print(f'{filename}: keys={len(set(names))}, duplicate_names={duplicates}')
PY
```

合并规则：

1. 根 `.env` 永远优先；已有 key 不得被旧文件静默覆盖。
2. 只迁移“线上服务真实需要、但根 `.env` 缺失”的 key。不要因为 `.env.example` 中存在 `LOCAL_*` 就把本地 Postgres、pgvector 或 Neo4j 配置复制到线上。
3. 当前代码不读取 `.env.local` 或 `deployment/.env_override`。如果旧文件中的 key 没有生产用途，不迁移。
4. 删除根 `.env` 中持久化的 `APP_BUILD_REF` 和 `APP_BUILD_TIME`；每次部署按 Gate 1 的方式动态导出。
5. 重复 key 必须归一。`AWS_REGION` 如有多项，保留实际生效的最后一项。
6. 不修改 `/etc/supportportal/auto-deploy.env`；其中只保留 `DEPLOY_*`、SES 和报告配置。
7. 编辑完成后执行 `chmod 600 .env`，并通过临时文件 + 原子替换写入，不要边写边让部署脚本读取半成品。

完成后只比较 key 集合和进程内值一致性，不要执行 `cat .env`、`env` 或 `docker inspect` 全量环境输出。

## 7. Gate 2：使用合并后的根 `.env`

旧 `.env.local` 和 `deployment/.env_override` 此时仍保留，但新代码不会读取它们。

```bash
cd ~/SupportPortal
export APP_BUILD_REF="$(git rev-parse --short=12 HEAD)"
export APP_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export APP_RUNTIME_IMAGE="localhost/supportportal-app:${APP_BUILD_REF}"
export DEPLOY_HEALTH_TIMEOUT_SECONDS=180

./deployment/deploy_ec2.sh \
  --branch main \
  --domain support.stellarix.space \
  --skip-pull
```

重复 Gate 1 的全部健康、storage、build provenance 和 Admin names-only 验收。若失败，先恢复 `backup_dir/root.env` 再重启；如果仍失败，再恢复 `rollback_tag`。

## 8. Gate 3：移走旧 env 文件

Gate 2 通过后，不要立即永久删除旧文件，先移动到外部备份目录：

```bash
cd ~/SupportPortal
test ! -f .env.local || mv .env.local "${backup_dir}/retired.env.local"
test ! -f deployment/.env_override || \
  mv deployment/.env_override "${backup_dir}/retired.deployment.env_override"
chmod 600 "${backup_dir}"/* 2>/dev/null || true
```

再次执行 Gate 2 的部署命令和全部验收。Gate 3 证明生产运行时只依赖根 `.env`。

建议观察至少一个完整业务周期或 24 小时。在此期间：

- 保留 `backup_dir` 和 `rollback_tag`。
- 观察 systemd journal、API/RAG/worker 日志、ticket 创建与查询、Admin Environment Config。
- 确认无配置缺失后，再删除外部临时备份和 rollback tag。

```bash
docker image rm "${rollback_tag}"
rm -rf "${backup_dir}"
```

删除备份前必须再次确认根 `.env` 为 `0600`、生产仓库 clean、内部/外部 health 正常。

当前版本不要立即重新启用 `supportportal-auto-deploy.timer`。必须先满足以下任一条件：

1. `deployment/deploy_ec2.sh` 已实现动态 build metadata 和 image-ID 自动回滚，并完成一次手工验收。
2. `scripts/ops/auto_deploy_ec2.sh` 在调用部署脚本前动态导出同等 metadata，并完成失败恢复测试。

条件满足后才能执行：

```bash
sudo systemctl enable --now supportportal-auto-deploy.timer
systemctl list-timers supportportal-auto-deploy.timer
```

## 9. 回滚手册

### 9.1 Build 失败

`deploy_ec2.sh` 在 `down` 前 build。build 失败时旧 container 仍应运行：

1. 不修改 `.env`。
2. 不执行手工 `down`。
3. 检查 build 日志、磁盘空间和 Docker daemon。
4. 确认旧内部/外部 health 仍正常后结束变更窗口。

### 9.2 Compose up 或内部 health 失败

先恢复配置，再恢复镜像：

```bash
cd ~/SupportPortal
install -m 0600 "${backup_dir}/root.env" .env.restore
mv -f .env.restore .env

export APP_RUNTIME_IMAGE="${rollback_tag}"
# 使用变更前记录的 health build ref，而不是当前 commit。
export APP_BUILD_REF="<previous-health-build-ref>"

docker compose --env-file .env \
  -f deployment/docker-compose.single-host.yml down || true
docker compose --env-file .env \
  -f deployment/docker-compose.single-host.yml up -d --no-build
```

随后轮询内部 `/health`，确认上一 build ref、PostgreSQL ticket storage 和 RAG 状态恢复。回滚成功后仍应保留非零部署结果和故障记录，不能把失败部署标成成功。

### 9.3 内部 health 正常但外部 health 失败

不要立即判定应用镜像失败。先检查 DNS、TLS、负载入口、安全组和 Nginx。如果内部接口、build ref 和 storage 均正常，优先恢复外部入口；只有确认新代码或配置导致外部失败时才执行完整回滚。

### 9.4 Environment Config 单独失败

该 Tab 返回 503 不应拖垮其他 Admin 页面。检查：

1. 根 `.env` 是否存在且为 regular file。
2. Docker Compose 是否包含只读挂载 `/run/supportportal/environment-config.env`。
3. API container 是否有读取权限。
4. API response 是否只包含通用错误，日志中不得输出 `.env` 原始行或 value。

如果核心业务健康，仅该 Tab 失败，可以先回退 compose 挂载或修复权限；不要为了一个只读 inventory 页面直接操作数据库。

### 9.5 DB 或 RAG 退化

如果 `ticket_storage`、`knowledge_storage` 或 `rag_service` 退化：

1. 先恢复旧根 `.env`，因为这通常是 DSN/schema/token 配置问题。
2. 旧配置加新镜像仍失败时，再恢复 `rollback_tag`。
3. 不执行数据库 reset、schema drop、RAG backfill 或数据迁移作为回滚动作。

## 10. 最终验收记录模板

上线完成后记录以下非敏感信息：

```text
Production commit:
APP_BUILD_REF:
Previous image ID retained until:
Gate 1 result (new code, old env):
Gate 2 result (consolidated root env):
Gate 3 result (legacy files removed):
Internal health:
External health:
ticket_storage:
knowledge_storage:
rag_service:
Environment Config names count:
Desktop/mobile UI result:
Network/DOM values exposure check:
Auto-deploy timer status / blocker:
Rollback backup deleted at:
Operator:
```

## 11. 后续工程建议

本次迁移可以按上述人工 rollback 安全执行，但线上部署仍有一项阻断 auto-deploy 恢复的代码改进：将本地 `restart_single_host_stack.sh` 的 image-ID rollback 机制移植到 `deployment/deploy_ec2.sh`，并由脚本动态生成 build metadata。完成失败恢复测试后，才能重新启用 systemd auto-deploy，而不是长期依赖人工保存 `rollback_tag`。

## 12. 假设与待确认项

本报告基于仓库当前的 EC2 单机部署方式，假设线上使用 Docker Compose、仓库根 `.env`、`deployment/deploy_ec2.sh` 和可选的 systemd auto-deploy。执行前必须现场确认是否还有 CI/CD 平台、Secrets Manager、额外 compose override、反向代理主机或多实例流量入口在注入配置。

1. **目前最不确定的内容**：无法从本地仓库确认线上是否仍有仓库外的环境注入层，以及真实 container/project 命名是否完全遵循当前 compose。首次操作应以 `docker compose ps -q api` 和 systemd unit 的实际输出为准，不能手写 container 名。
2. **最可能遗漏的内容**：线上可能有另一台实例、负载均衡 target、CI runner 或 timer 同时部署。只暂停当前主机的 timer 不一定能阻止并发变更；维护窗口前必须确认所有部署入口和流量切换所有者。
