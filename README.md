# SupportPortal

SupportPortal 是一个技术支持工单系统，包含三端：
1. 客户端（`/client/`）
2. 工程师端（`/engineer/`）
3. 管理员端（`/dashboard/`）

当前仓库已落地单机可运行架构：
1. `api`：FastAPI（REST + 静态页面托管）
2. `ws_gateway`：独立 WebSocket 网关
3. `worker`：异步任务处理（RAG/AI 查询）
4. `redis`：任务队列 + 事件总线
5. `postgres`：工单存储（可扩展 pgvector）
6. `nginx`：统一入口反向代理

## 本地运行（Podman）

### 前置条件
1. 已安装 Podman + `podman-compose`
2. 已初始化并启动 podman machine

### 启动步骤

```bash
cd /Users/xieziling/Desktop/personal_proj/SupportPortal
cp .env.example .env 2>/dev/null || true

# 本地 rootless Podman 默认使用 8080
# 确保 .env 中有：NGINX_HOST_PORT=8080

podman machine start
export PODMAN_COMPOSE_PROVIDER=podman-compose

# 首次或镜像大改动时建议重建 api 镜像
podman-compose -f deployment/docker-compose.single-host.yml build api
podman-compose -f deployment/docker-compose.single-host.yml up -d
```

### 访问地址
1. 客户端: [http://localhost:8080/client/](http://localhost:8080/client/)
2. 工程师端: [http://localhost:8080/engineer/](http://localhost:8080/engineer/)
3. 管理端: [http://localhost:8080/dashboard/](http://localhost:8080/dashboard/)
4. 健康检查: [http://localhost:8080/health](http://localhost:8080/health)

### 常用命令

```bash
# 状态
podman-compose -f deployment/docker-compose.single-host.yml ps

# 日志
podman-compose -f deployment/docker-compose.single-host.yml logs -f api ws_gateway worker nginx

# 停止
podman-compose -f deployment/docker-compose.single-host.yml down
```

## 更新代码后如何生效

1. 修改了 `backend/`、`client_ui/`、`engineer_ui/`、`dashboard/`：

```bash
podman-compose -f deployment/docker-compose.single-host.yml build api
podman-compose -f deployment/docker-compose.single-host.yml up -d api ws_gateway worker
```

2. 只修改了 Nginx 配置（`deployment/nginx/supportportal.conf`）：

```bash
podman-compose -f deployment/docker-compose.single-host.yml restart nginx
```

3. 修改了 `.env`：

```bash
podman-compose -f deployment/docker-compose.single-host.yml up -d --force-recreate api ws_gateway worker nginx
```

## 常见问题

1. `localhost refused to connect` 但 `health` 正常：
   - 通常是访问了 `http://localhost/client`（80端口）而不是 `8080`。
   - 请使用带端口地址，如 `http://localhost:8080/client/`。

2. `rootlessport cannot expose privileged port 80`：
   - rootless Podman 不能绑定 80。
   - 本地使用 `NGINX_HOST_PORT=8080`。

3. `podman compose` 调到 `docker-compose`：
   - 执行 `export PODMAN_COMPOSE_PROVIDER=podman-compose`。

4. `pip` 相关 SSL/timeout 抖动：
   - 重试 `podman-compose ... build api`。
   - 当前 Dockerfile 已加入安装重试逻辑。

## EC2 部署（Docker）

EC2 上继续使用 Docker（不是 Podman）。
详细步骤见：
- [docs/deploy_single_host_ec2.md](/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/deploy_single_host_ec2.md)

## 架构文档

- 业务架构与三端交互：
  [docs/support_system_architecture.md](/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/support_system_architecture.md)

## 项目目录

```text
backend/       # FastAPI backend + worker + ws gateway
client_ui/     # 客户端 UI
engineer_ui/   # 工程师端 UI
dashboard/     # 管理端 UI
deployment/    # compose 与 nginx 配置
docs/          # 文档
```
