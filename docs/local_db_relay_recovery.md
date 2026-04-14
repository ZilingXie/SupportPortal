# 本地 DB Relay 恢复手册

适用范围：
1. 当前这台 macOS + Podman + Shadowrocket 的本地 SupportPortal 单机环境。
2. `.env` 中的 `TICKET_DB_DSN` / `PGVECTOR_DSN` 通过 `hostaddr=192.168.127.254` 和 `:15433` 依赖宿主机本地 relay。

## 1. 典型症状

出现以下任一现象时，优先怀疑本地 DB relay 或 Shadowrocket 规则：
1. `/health` 退化为：
   - `ticket_storage=memory`
   - `knowledge_storage=unreachable`
   - `rag_service=unreachable`
2. engineer 页面 ticket 列表为空。
3. `deployment_api_1` / `deployment_rag_api_1` 启动后回退到内存仓库。
4. 容器内访问 `host.containers.internal:15433` 或 `192.168.127.254:15433` 失败。

## 2. 根因链路

这次故障的实际根因是两段链路同时依赖正确配置：
1. Shadowrocket 必须让公网 DB 入口走代理，而不是 `DIRECT`。
2. 本地运行栈并不直接连公网 `5432`，而是先连宿主机 `15433` relay，再由 relay 转发到上游 DB `5432`。

当 Shadowrocket 把 DB 目标走成 `DIRECT`，或者宿主机 `15433` relay 丢失时：
1. 宿主机到上游 PostgreSQL 握手会超时或不可用。
2. 容器通过 `hostaddr=192.168.127.254` 连不到健康的 relay。
3. SupportPortal 启动时回退到 `memory` ticket storage。

## 3. 正确的 Shadowrocket 规则

保持以下规则：
1. `3.229.119.182/32 -> PROXY`
2. `n8n-postgres-db.ccpyyi8i03wi.us-east-1.rds.amazonaws.com -> PROXY`
3. `192.168.127.0/24 -> DIRECT`

不要把第 3 条改成 `PROXY`。这是容器访问宿主机 `host.containers.internal` 的本地通道。

## 4. 一键恢复步骤

在仓库根目录执行：

```bash
cd /Users/xieziling/Desktop/personal_proj/SupportPortal

bash scripts/workflow/ensure_local_db_relay.sh
bash scripts/workflow/restart_single_host_lightweight_stack.sh
```

说明：
1. `ensure_local_db_relay.sh` 会在当前 `.env` 明确需要 relay 时，自动检查并补起宿主机 `15433` relay。
2. `restart_single_host_lightweight_stack.sh` 现在也会先自动执行同样的 relay 检查，所以机器重启后通常只需要直接跑官方单机重启脚本。

## 5. 协议级验证方法

### 5.1 验证宿主机 relay

```bash
python3 - <<'PY'
import socket, struct
s = socket.create_connection(("127.0.0.1", 15433), timeout=5)
s.settimeout(5)
s.sendall(struct.pack("!II", 8, 80877103))
print(s.recv(16))
s.close()
PY
```

期望输出以 `b'S'` 开头。

### 5.2 验证容器到宿主机 relay

```bash
podman exec deployment_api_1 sh -lc 'python -u - <<\"PY\"
import socket, struct
for host in ("host.containers.internal", "192.168.127.254"):
    s = socket.create_connection((host, 15433), timeout=5)
    s.settimeout(5)
    s.sendall(struct.pack("!II", 8, 80877103))
    print(host, s.recv(16))
    s.close()
PY'
```

`host.containers.internal` 至少应返回 `b'S'`。

### 5.3 验证应用恢复

```bash
curl http://127.0.0.1:8080/health
curl 'http://127.0.0.1:8080/api/engineer/tickets?status=all'
```

期望：
1. `/health` 返回：
   - `ticket_storage=postgres`
   - `knowledge_storage=postgres`
   - `rag_service=ok`
2. engineer tickets API 返回非空 `tickets`。

## 6. 常见误区

1. `nc -vz host 5432` 成功，不代表 PostgreSQL 协议层一定健康。
   - 这次真正有效的判断是 PostgreSQL `SSLRequest` 是否收到 `b'S'`。
2. 不要把问题直接归因到 engineer UI。
   - 这次 engineer 端看不到 ticket，本质是后端降级到了 `ticket_storage=memory`。
3. 不要删除 `.env` 里的 `hostaddr=192.168.127.254`。
   - 当前本地单机方案就是依赖容器先走宿主机 relay。
