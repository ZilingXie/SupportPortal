# SupportPortal 单机部署指南（本地 Podman + EC2 Docker）

## 1. 部署架构

```mermaid
flowchart LR
    Browser["Client / Engineer / Admin Browser"] --> Nginx["Nginx"]
    Nginx --> API["Support API (FastAPI)"]
    Nginx --> WSG["WS Gateway (FastAPI)"]

    API --> Redis["Redis (Task Queue + Event Bus)"]
    API --> Postgres["PostgreSQL / pgvector"]

    Worker["Worker"] --> Redis
    Worker --> Postgres
    Worker --> LLM["OpenAI API"]

    Redis --> WSG
    WSG --> Browser
```

核心说明：
1. API 快速写入工单并返回，耗时 AI 处理走 Worker 异步链路。
2. WebSocket 推送由独立 `ws_gateway` 处理，避免与 API 耦合。
3. Redis 同时承载队列与事件总线。

---

## 2. 本地部署（Podman）

### 2.1 前置条件
1. 安装 Podman。
2. 安装 `podman-compose`。
3. 首次使用 Podman Machine：

```bash
podman machine init
podman machine start
```

### 2.2 环境变量

在项目根目录创建 `.env`：

```bash
cd /Users/xieziling/Desktop/personal_proj/SupportPortal
cp .env.example .env
```

最小配置建议：

```bash
OPENAI_API_KEY=sk-xxxx
ASYNC_QUERY_ENABLED=true
NGINX_HOST_PORT=8080
```

说明：
1. 本地 rootless Podman 推荐用 `8080`，避免 80 端口权限问题。
2. 如果未配置 `OPENAI_API_KEY`，RAG/LLM 能力会降级。

### 2.3 启动

```bash
cd /Users/xieziling/Desktop/personal_proj/SupportPortal
export PODMAN_COMPOSE_PROVIDER=podman-compose

podman-compose -f deployment/docker-compose.single-host.yml build api
podman-compose -f deployment/docker-compose.single-host.yml up -d
```

### 2.4 访问
1. 客户端: [http://localhost:8080/client/](http://localhost:8080/client/)
2. 工程师端: [http://localhost:8080/engineer/](http://localhost:8080/engineer/)
3. 管理端: [http://localhost:8080/dashboard/](http://localhost:8080/dashboard/)
4. 健康检查: [http://localhost:8080/health](http://localhost:8080/health)

### 2.5 运维命令

```bash
# 查看服务状态
podman-compose -f deployment/docker-compose.single-host.yml ps

# 查看日志
podman-compose -f deployment/docker-compose.single-host.yml logs -f api ws_gateway worker nginx

# 停止服务
podman-compose -f deployment/docker-compose.single-host.yml down
```

### 2.6 常见问题

1. 报错 `rootlessport cannot expose privileged port 80`：
   - 原因：rootless Podman 不能绑 80。
   - 处理：`.env` 中设置 `NGINX_HOST_PORT=8080`。

2. 报错 `Dockerfile not found in .../deployment/backend/Dockerfile`：
   - 原因：compose 相对路径基准错误。
   - 处理：使用当前仓库内最新 `deployment/docker-compose.single-host.yml`。

3. `podman compose` 实际调用了 docker-compose：
   - 处理：`export PODMAN_COMPOSE_PROVIDER=podman-compose`。

4. 浏览器 `ERR_CONNECTION_REFUSED`：
   - 先检查 `podman-compose ... ps`。
   - 使用带端口 URL，比如 `http://localhost:8080/client/`。

---

## 3. EC2 部署（Docker）

说明：EC2 上使用 Docker，不使用 Podman。

### 3.1 准备 EC2
1. Ubuntu 22.04/24.04。
2. 安全组开放：
   - `22`（管理 IP）
   - `80`（HTTP）
   - `443`（HTTPS）

### 3.2 安装 Docker

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

重新登录后继续。

### 3.3 拉取代码与环境配置

```bash
git clone <你的仓库地址> SupportPortal
cd SupportPortal
cp .env.example .env
```

`.env` 至少配置：
1. `OPENAI_API_KEY`
2. `NGINX_HOST_PORT=80`
3. AWS Postgres：`TICKET_DB_DSN`、`PGVECTOR_DSN`
4. `TICKET_DB_SCHEMA=supportportal`
5. `PGVECTOR_SCHEMA=supportportal`
6. `PGVECTOR_TABLE=docagent_chunks_qwen3_1024`
7. `PGVECTOR_DIM=1024`

如果工单库和向量库共用同一个 AWS Postgres，部署脚本会在缺少 `TICKET_DB_DSN` 时自动复用 `PGVECTOR_DSN`；但生产环境仍建议两个字段都明确写入 `.env`。

### 3.4 启动服务

```bash
docker compose -f deployment/docker-compose.single-host.yml up -d --build
docker compose -f deployment/docker-compose.single-host.yml ps
```

验证：

```bash
curl http://127.0.0.1/health
```

### 3.5 域名接入
1. 在 DNS 配置 `A` 记录：
   - `support.stellarix.space -> <EC2 公网 IP>`
2. 验证：

```bash
curl -I http://support.stellarix.space
```

---

## 4. 更新发布流程

### 4.1 本地 Podman

```bash
cd /Users/xieziling/Desktop/personal_proj/SupportPortal
podman-compose -f deployment/docker-compose.single-host.yml build api
podman-compose -f deployment/docker-compose.single-host.yml up -d api ws_gateway worker
```

### 4.2 EC2 Docker

```bash
cd ~/SupportPortal
git pull
docker compose -f deployment/docker-compose.single-host.yml build api
docker compose -f deployment/docker-compose.single-host.yml up -d api ws_gateway worker nginx
```

### 4.3 EC2 一键部署脚本（推荐）

仓库内已提供脚本：

```bash
cd ~/SupportPortal
chmod +x deployment/deploy_ec2.sh
./deployment/deploy_ec2.sh
```

脚本会执行：
1. 拉取最新代码（`git fetch/pull`，默认当前分支）。
2. 重启容器（`docker compose down` + `up -d --build`）。
3. 健康检查（`http://127.0.0.1:<NGINX_HOST_PORT>/health`）。
4. 外网检查（默认 `https://support.stellarix.space/health`）。
5. 使用仓库根目录下的 `.deploy_ec2.lock` 避免并发部署。

常用参数：

```bash
# 指定分支
./deployment/deploy_ec2.sh --branch main

# 指定域名
./deployment/deploy_ec2.sh --domain support.stellarix.space

# 只重启，不拉代码
./deployment/deploy_ec2.sh --skip-pull

# 部署后跟随日志
./deployment/deploy_ec2.sh --logs
```

---

### 4.4 EC2 每日自动部署 + 失败邮件告警

仓库已提供以下资产：
1. 自动调度 wrapper：`scripts/ops/auto_deploy_ec2.sh`
2. systemd service：`deployment/systemd/supportportal-auto-deploy.service`
3. systemd timer：`deployment/systemd/supportportal-auto-deploy.timer`
4. 环境变量模板：`deployment/systemd/auto-deploy.env.example`

自动调度 wrapper 会执行：
1. 获取 `origin` 最新 refs。
2. 比较本地 `HEAD` 和 `origin/main`。
3. 如果远端有新提交，调用 `deployment/deploy_ec2.sh --branch main --domain support.stellarix.space` 做完整部署。
4. 如果没有新提交，只做内外网健康检查，不重启容器。
5. 任何步骤失败都会尝试调用 Amazon SES 发失败邮件。

#### 4.4.1 前置条件

1. EC2 上的部署仓库保持在干净的 `main`。
2. 已安装 AWS CLI，并且 `aws sesv2 send-email` 可用。
3. `DEPLOY_ALERT_FROM` 已在 SES 对应 region 完成验证。
4. SES 账号已退出 sandbox；如果还没退出，收件邮箱也必须先验证。
5. EC2 已绑定允许发 SES 邮件的 IAM role。

建议的最小 IAM policy：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ses:SendEmail"
      ],
      "Resource": "*"
    }
  ]
}
```

#### 4.4.2 安装配置

先准备 `/etc/supportportal/auto-deploy.env`：

```bash
sudo install -d -m 0755 /etc/supportportal
sudo cp deployment/systemd/auto-deploy.env.example /etc/supportportal/auto-deploy.env
sudo nano /etc/supportportal/auto-deploy.env
```

至少填写：
1. `DEPLOY_BRANCH=main`
2. `DEPLOY_DOMAIN=support.stellarix.space`
3. `DEPLOY_ALERT_TO=<你的收件邮箱>`
4. `DEPLOY_ALERT_FROM=<SES 已验证发件地址>`
5. `DEPLOY_AWS_REGION=<SES 所在 region>`

安装 systemd unit。下面的命令会把默认仓库路径 `/opt/supportportal/SupportPortal` 和默认用户 `ubuntu` 替换成当前机器的真实值：

```bash
sudo sed \
  -e "s#/opt/supportportal/SupportPortal#${HOME}/SupportPortal#g" \
  -e "s#User=ubuntu#User=${USER}#g" \
  deployment/systemd/supportportal-auto-deploy.service \
  | sudo tee /etc/systemd/system/supportportal-auto-deploy.service >/dev/null

sudo cp deployment/systemd/supportportal-auto-deploy.timer /etc/systemd/system/supportportal-auto-deploy.timer
sudo systemctl daemon-reload
sudo systemctl enable --now supportportal-auto-deploy.timer
```

默认 timer 使用：

```ini
OnCalendar=*-*-* 19:00:00 UTC
```

这等价于每天北京时间 03:00。如果你的 EC2 已经把系统时区切到 `Asia/Shanghai`，也可以把 timer 改成：

```ini
OnCalendar=*-*-* 03:00:00 Asia/Shanghai
```

#### 4.4.3 手动触发与状态检查

```bash
# 立刻执行一次
sudo systemctl start supportportal-auto-deploy.service

# 看最近一次运行状态
sudo systemctl status supportportal-auto-deploy.service

# 看下一次触发时间
systemctl list-timers supportportal-auto-deploy.timer

# 看日志
journalctl -u supportportal-auto-deploy.service -n 200 --no-pager
```

#### 4.4.4 故障排查

1. 如果 service 直接失败，先看：

```bash
journalctl -u supportportal-auto-deploy.service -n 200 --no-pager
```

2. 如果日志提示 `Deploy checkout must be clean`：
   - 说明部署仓库有未提交或未清理的改动，先恢复到干净 `main`。

3. 如果日志提示 `Another deployment or auto health check is already running`：
   - 说明有手动部署或另一轮自动任务正在执行。
   - 两个脚本共享 `.deploy_ec2.lock`，等上一轮完成后再重试。

4. 如果部署成功但没有收到邮件：
   - 检查 `DEPLOY_ALERT_FROM` 是否已在对应 region 验证。
   - 检查 EC2 IAM role 是否有 `ses:SendEmail` 权限。
   - 检查 SES 是否仍处于 sandbox。

5. 如果 systemd 没有按时触发：
   - 用 `systemctl list-timers supportportal-auto-deploy.timer` 检查下次触发时间。
   - 确认 `Persistent=true` 已生效；实例关机错过时间窗后，开机应自动补跑一次。

---

## 5. HTTPS 建议

生产建议二选一：
1. EC2 前挂 ALB + ACM（推荐，证书托管）。
2. EC2 本机 Nginx + Let's Encrypt。
