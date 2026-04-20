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

# 官方本地单机重启路径
bash scripts/workflow/restart_single_host_lightweight_stack.sh

# 检查官方 deployment 栈和 build provenance 是否一致
bash scripts/workflow/inspect_single_host_stack_mode.sh
```

说明：
1. 官方本地单机栈只有 `deployment`；如果看到 `deploymentlw`，先执行 `bash scripts/workflow/cleanup_single_host_aux_stack.sh`。
2. 重启脚本会把运行镜像固定到当前根 `main` 的 `app_build.ref`，避免旧 checkout 继续处理新 ticket。

### 访问地址
1. 客户端: [http://localhost:8080/client/](http://localhost:8080/client/)
2. 工程师端: [http://localhost:8080/engineer/](http://localhost:8080/engineer/)
3. 管理端（Ticket Dashboard）: [http://localhost:8080/dashboard/](http://localhost:8080/dashboard/)
4. RAG Workbench: [http://localhost:8080/dashboard/rag/](http://localhost:8080/dashboard/rag/)
5. 健康检查: [http://localhost:8080/health](http://localhost:8080/health)

### 常用命令

```bash
# 状态
bash scripts/workflow/inspect_single_host_stack_mode.sh

# 日志
podman-compose -f deployment/docker-compose.single-host.yml logs -f api ws_gateway worker nginx

# 停止
podman-compose -f deployment/docker-compose.single-host.yml down
```

## 更新代码后如何生效

1. 修改了 `backend/`、`ui/client-ui/`、`ui/engineer-ui/`、`ui/dashboard-ui/`：

```bash
bash scripts/workflow/restart_single_host_lightweight_stack.sh
bash scripts/workflow/inspect_single_host_stack_mode.sh
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

5. 源码已经更新，但线上行为像旧逻辑：
   - 先跑 `bash scripts/workflow/inspect_single_host_stack_mode.sh`。
   - 如果脚本报 auxiliary stack 或 build provenance mismatch，先清理 stray `deploymentlw` 并从根 `main` 重新执行官方重启脚本。

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
ui/
  client-ui/   # 客户端 UI（含 next-prototype/ 历史原型）
  engineer-ui/ # 工程师端 UI
  dashboard-ui/# 管理端 UI（`/dashboard/` + `/dashboard/rag/`）
deployment/    # compose 与 nginx 配置
docs/          # 文档
```

## Agora 官方文档抓取与端点入库

仓库提供了一个手动运行的脚本，用于：
1. 从 Agora 英文站点发现官方文档 URL。
2. 下载对应的 Markdown 文件到 `local_knowledge/official/raw/`。
3. 默认把下载得到的 `.md` 文件上传到 `https://support.stellarix.space` 的官方文档端点。
4. 由 RAG 端点完成规范化、`primary/shadow` 双轨切片、BGE Large 向量化和落库。

运行方式：

```bash
python scripts/fetch_and_upload_agora_docs.py
```

常用参数：

```bash
python scripts/fetch_and_upload_agora_docs.py \
  --api-base-url http://localhost:8080 \
  --limit 3 \
  --download-workers 8
```

说明：
1. `local_knowledge/official/raw/` 每次运行都会先全量重建。
2. 本地重建时显式传 `--api-base-url http://localhost:8080`，避免误把官方文档上传到远端环境。
3. 运行结束后会在 `local_knowledge/official/raw/_sync_report.json` 写入下载和 ingestion 结果汇总。
4. `local_knowledge/` 已加入 `.gitignore`，作为本地生成产物保留。

## Local Embedding / Dual-Track Chunking

默认向量化配置已经切到 SiliconFlow BGE M3 Embedding：

```env
EMBEDDING_PROVIDER=siliconflow
EMBEDDING_MODEL_ID=BAAI/bge-m3
EMBEDDING_BATCH_SIZE=16
SILICONFLOW_API_KEY=...
SILLICONFLOW_KEY=...
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_EMBEDDING_DIMENSIONS=1024
PGVECTOR_TABLE=docagent_chunks_bge_m3_1024
PGVECTOR_DIM=1024
KNOWLEDGE_BM25_BACKFILL_ON_INIT=true
PRIMARY_CHUNK_STRATEGY=markdown_header_v1
SHADOW_CHUNK_STRATEGY=semantic_qwen3_v1
SHADOW_CHUNK_ENABLED=true
LOCAL_KNOWLEDGE_ROOT=local_knowledge
```

说明：
1. `support_knowledge_documents` 继续保存 canonical 文档结构和 `primary` 统计。
2. `support_knowledge_chunk_runs` / `support_knowledge_chunk_traces` 会额外记录双轨切片过程数据，供后续优化使用。
3. 在线检索链路为 `vector + true BM25 + RRF + metadata prune + rerank`，并且只会召回 `index_role='primary'` 的 chunk。详细说明见 [docs/rag_retrieval_chain.md](/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/rag_retrieval_chain.md)。
4. 技术文档推荐由 `n8n` 直接写入 `support_knowledge_source_documents`，再执行 `python scripts/ingest_local_knowledge_sources.py --source-system n8n --knowledge-type technical` 做本地增量入库。
5. `KNOWLEDGE_BM25_BACKFILL_ON_INIT` 默认应保持 `true`；仅在像官方文档全量重建这种 deferred-BM25 replay 场景下，才临时设置为 `false`，避免 `repository.initialize()` 在 worker 启动前先触发整库 BM25 backfill。

## Intent Routing

客户消息在进入 RAG 前会先做问题范围识别：
1. `small_talk`：闲聊/天气/问候，直接拒答。
2. `non_agora`：非 Agora 问题，直接拒答。
3. `agora_non_technical`：Agora 相关但非技术问题，走 OpenAI Responses API 的 web search。
4. `agora_technical`：Agora 技术问题，继续走现有 RAG 链路。

相关环境变量：

```env
INTENT_ROUTER_MODEL=gpt-4o-mini
INTENT_ROUTER_TIMEOUT_SECONDS=3.0
INTENT_ROUTER_CONFIDENCE_THRESHOLD=0.7
OPENAI_WEB_SEARCH_MODEL=gpt-5
OPENAI_WEB_SEARCH_TIMEOUT_SECONDS=12.0
```

## Local-First Benchmark Workflow

RAG benchmark 现在以本地 `benchmarks/*.json` 文件为唯一事实来源，dataset tables 只作为镜像库存给 `/dashboard/rag/` 的 `Data Supply` 页面和审计流程使用。

常用命令：

```bash
# 1. 将本地 benchmark catalog 镜像到 dataset tables
./.venv/bin/python scripts/sync_local_benchmarks.py

# 2. 从本地 benchmark 文件直接运行 benchmark
./.venv/bin/python scripts/run_rag_benchmark.py \
  --dataset benchmarks/agora_rag_testset_100_mixed_en.json \
  --experiment-id agora_mixed_en_local
```

说明：
1. `scripts/run_rag_benchmark.py` 只接受 `--dataset`；`--dataset-id` 和 `--suite` 已停用。
2. `Data Supply -> Benchmark Supply` 的 `Sync Local Benchmarks` 只负责把本地 benchmark 文件镜像到 dataset tables，不会改变 benchmark 运行入口。
3. benchmark 文件当前使用 NDJSON，每行一个 case，并且已经升级为显式 route-aware contract。
