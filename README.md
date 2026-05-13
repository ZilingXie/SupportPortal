# SupportPortal

[English](README.md) | [中文](README.zh-CN.md)

SupportPortal is an AI-assisted technical support platform for managing customer questions, automated answers, engineer collaboration, and operational visibility in one workflow.

Unlike a traditional ticketing system that mainly records and routes issues, SupportPortal is designed to help a support team:

1. Turn every customer question into a trackable ticket.
2. Use routing and RAG to answer supported technical questions when evidence is sufficient.
3. Escalate uncertain or troubleshooting-heavy cases to engineers with useful context.
4. Let engineers review, assist, or take over complex cases.
5. Give managers and operators visibility into ticket history, runtime events, RAG evidence, and benchmark quality.

## Current Status

The current POC has validated the end-to-end support loop:

1. A customer submits a question from the client surface.
2. The system creates or updates a ticket.
3. The agent classifies the request, retrieves evidence when appropriate, and drafts a response.
4. Cases with insufficient evidence or troubleshooting requirements are escalated to engineers.
5. Engineers can provide guidance or take over the conversation.
6. Dashboards expose ticket state, timelines, runtime events, RAG evidence, and benchmark diagnostics.

The project is ready for the next validation phase: real support scenarios, stability checks, operational metrics, and production-readiness work.

## Core Capabilities

### Client Support Flow

- Customer questions automatically create tickets.
- The system routes small talk, non-Agora questions, Agora non-technical questions, and Agora technical questions differently.
- Technical questions can be answered through the RAG workflow when retrieved evidence is sufficient.
- Troubleshooting questions can collect missing information before escalation.
- Client conversations support interruption and resend behavior for the same ticket, while different tickets can wait for AI responses concurrently.
- Client and Engineer share a rich text composer with markdown-safe rendering.

### Engineer Collaboration

- Escalated tickets enter the engineer task pool.
- Engineers can work in managed mode, where they provide guidance and AI replies to the customer.
- Engineers can also take over the conversation directly.
- Engineer investigations follow a ticket lifecycle and can return reviewed drafts to the customer.

### Ticket Dashboard

- The dashboard shows all tickets, ticket details, timelines, and live event streams.
- Ticket details include token usage summaries by ticket family.
- Ticket details expose client agent runtime summaries and recent agent events.
- RAG replies can expand retrieval plans, execution rounds, and final evidence.

### RAG Dashboard

- The RAG dashboard can sync local benchmark datasets.
- It can run benchmark sessions and compare run/session diagnostics.
- It shows query understanding, candidate funnels, judge disagreement, token usage, and provider/model breakdowns.
- It supports live and benchmark case replay, sample review, and result export.

### RAG and Knowledge

- Engineers can upload knowledge for ingestion.
- The system uses hybrid retrieval, reranking, metadata pruning, and context-budgeted evidence compression.
- Query expansion can use dictionaries, LLM expansion, and PRF.
- The benchmark workflow provides layered diagnostics and failure attribution.
- Token usage is tracked by provider/model and is prepared for a future usage ledger.

## User Surfaces

For local development, the default single-host stack exposes:

1. Client: [http://localhost:8080/client/](http://localhost:8080/client/)
2. Engineer: [http://localhost:8080/engineer/](http://localhost:8080/engineer/)
3. Ticket Dashboard: [http://localhost:8080/dashboard/](http://localhost:8080/dashboard/)
4. RAG Workbench: [http://localhost:8080/dashboard/rag/](http://localhost:8080/dashboard/rag/)
5. Health Check: [http://localhost:8080/health](http://localhost:8080/health)

An online deployment is available. Contact the project maintainer for the deployment entry points and account information.

## Local Run Guide

### Prerequisites

1. Podman and `podman-compose` are installed.
2. The Podman machine has been initialized.

### Start the Single-Host Stack

```bash
cd /Users/xieziling/Desktop/personal_proj/SupportPortal
cp .env.example .env 2>/dev/null || true
cp .env.local.example .env.local 2>/dev/null || true

# Rootless Podman uses port 8080 locally.
# Ensure .env.local contains: NGINX_HOST_PORT=8080

podman machine start
export PODMAN_COMPOSE_PROVIDER=podman-compose

# Official local single-host entry point:
# with .env.local enabled, this starts local_lightweight + local Postgres/pgvector.
bash scripts/workflow/restart_single_host_stack.sh --use-local-env

# Confirm the official deployment stack and build provenance.
bash scripts/workflow/inspect_single_host_stack_mode.sh
```

Notes:

1. The official local single-host stack is `deployment`. If `deploymentlw` appears, clean it with `bash scripts/workflow/cleanup_single_host_aux_stack.sh`.
2. The restart script pins the running image to the current root `main` `app_build.ref`, so old checkouts do not continue processing new tickets.
3. `restart_single_host_stack.sh` is the recommended entry point. Without `--use-local-env`, it reads only `.env` and defaults to `full + remote DB`.
4. For local development, use `bash scripts/workflow/restart_single_host_stack.sh --use-local-env` to layer `.env.local` and run `local_lightweight + local DB`.
5. To debug against a remote/RDS database, use `bash scripts/workflow/restart_single_host_stack.sh --use-local-env --db remote`.
6. `restart_single_host_lightweight_stack.sh` and `restart_single_host_local_stack.sh` remain compatibility wrappers.

### Common Commands

```bash
# Inspect status
bash scripts/workflow/inspect_single_host_stack_mode.sh

# Follow service logs
podman-compose \
  -f deployment/docker-compose.single-host.yml \
  -f deployment/docker-compose.single-host.local-lightweight.yml \
  -f deployment/docker-compose.single-host.local-db.yml \
  logs -f api rag_api rag_worker ws_gateway worker_query worker_aux nginx local_postgres

# Stop the local stack
podman-compose \
  -f deployment/docker-compose.single-host.yml \
  -f deployment/docker-compose.single-host.local-lightweight.yml \
  -f deployment/docker-compose.single-host.local-db.yml \
  down
```

## Applying Code Changes Locally

1. After changing `backend/`, `ui/client-ui/`, `ui/engineer-ui/`, or `ui/dashboard-ui/`:

```bash
bash scripts/workflow/restart_single_host_stack.sh --use-local-env
bash scripts/workflow/inspect_single_host_stack_mode.sh
```

2. After changing only Nginx config in `deployment/nginx/supportportal.conf`:

```bash
podman-compose -f deployment/docker-compose.single-host.yml restart nginx
```

3. After changing `.env.local` or local DB/RAG config:

```bash
bash scripts/workflow/restart_single_host_stack.sh --use-local-env
```

4. After changing `.env` while still using the remote DB lightweight path:

```bash
bash scripts/workflow/restart_single_host_stack.sh --use-local-env --db remote
```

## Troubleshooting

1. `localhost refused to connect` while `/health` succeeds:
   - You may be visiting `http://localhost/client` on port 80.
   - Use `http://localhost:8080/client/`.

2. `rootlessport cannot expose privileged port 80`:
   - Rootless Podman cannot bind port 80.
   - Use `NGINX_HOST_PORT=8080` locally.

3. `podman compose` falls back to `docker-compose`:
   - Run `export PODMAN_COMPOSE_PROVIDER=podman-compose`.

4. `pip` SSL or timeout flakiness during builds:
   - Retry the failing build command.
   - The Dockerfile includes install retry logic.

5. Source code has changed, but runtime behavior looks stale:
   - Run `bash scripts/workflow/inspect_single_host_stack_mode.sh`.
   - If it reports an auxiliary stack or build provenance mismatch, clean `deploymentlw` and restart from the root `main` workspace.

6. Host-side ingestion or diagnostics need to write to local pgvector:
   - Wrap the command with `bash scripts/workflow/run_with_local_db_env.sh -- <command>`.
   - The helper exports a host DSN using `127.0.0.1:${LOCAL_POSTGRES_HOST_PORT}` while containers continue using `local_postgres:5432`.

## Project Layout

```text
backend/        # FastAPI backend, workers, ws gateway, repositories, services
ui/
  client-ui/    # Client support UI
  engineer-ui/  # Engineer task and investigation UI
  dashboard-ui/ # Ticket Dashboard and RAG Workbench
deployment/     # Compose, Nginx, systemd, and EC2 deployment assets
docs/           # Architecture, product, RAG, deployment, and change-log docs
benchmarks/     # Local benchmark datasets
scripts/        # Workflow, ingestion, benchmark, and verification scripts
```

## Key Documents

- Business architecture and three-surface flow: [docs/support_system_architecture.md](docs/support_system_architecture.md)
- EC2 deployment guide: [docs/deploy_single_host_ec2.md](docs/deploy_single_host_ec2.md)
- Canonical feature list: [docs/feature_list.md](docs/feature_list.md)
- RAG retrieval chain: [docs/rag_retrieval_chain.md](docs/rag_retrieval_chain.md)
- RAG change log: [docs/rag_change_log.md](docs/rag_change_log.md)
- Prompt/model change log: [docs/prompt_change_log.md](docs/prompt_change_log.md)
- UI design source of truth: [design.md](design.md)

## Official Documentation Ingestion

The repository includes a manual script for discovering Agora English documentation, downloading Markdown files, and uploading them to the configured knowledge ingestion endpoint.

```bash
python scripts/fetch_and_upload_agora_docs.py
```

For local rebuilds, pass the local API explicitly:

```bash
python scripts/fetch_and_upload_agora_docs.py \
  --api-base-url http://localhost:8080 \
  --limit 3 \
  --download-workers 8
```

Notes:

1. `local_knowledge/official/raw/` is rebuilt on each run.
2. Use `--api-base-url http://localhost:8080` during local rebuilds to avoid uploading to a remote environment by accident.
3. The script writes a download and ingestion report to `local_knowledge/official/raw/_sync_report.json`.
4. `local_knowledge/` is ignored by git and should remain a local generated artifact.

## RAG Configuration Summary

The default embedding path uses SiliconFlow BGE M3 Embedding:

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

The online retrieval chain is:

```text
vector + true BM25 + RRF + metadata prune + rerank
```

Only chunks with `index_role='primary'` are recalled online. Canonical document structure is stored in `support_knowledge_documents`; chunk run and trace data is stored in `support_knowledge_chunk_runs` and `support_knowledge_chunk_traces`.

Technical documents can be written by `n8n` into `support_knowledge_source_documents`, then ingested locally:

```bash
python scripts/ingest_local_knowledge_sources.py --source-system n8n --knowledge-type technical
```

## Intent Routing

Customer messages are routed before entering the RAG workflow:

1. `small_talk`: greeting, weather, or casual chat; rejected as out of support scope.
2. `non_agora`: non-Agora question; rejected as out of scope.
3. `agora_non_technical`: Agora-related but not technical; handled through OpenAI Responses API web search.
4. `agora_technical`: Agora technical question; handled through the RAG workflow.

Relevant environment variables:

```env
INTENT_ROUTER_MODEL=gpt-4o-mini
INTENT_ROUTER_TIMEOUT_SECONDS=3.0
INTENT_ROUTER_CONFIDENCE_THRESHOLD=0.7
OPENAI_WEB_SEARCH_MODEL=gpt-5
OPENAI_WEB_SEARCH_TIMEOUT_SECONDS=12.0
```

## Local-First Benchmark Workflow

RAG benchmark datasets use local `benchmarks/*.json` files as the source of truth. Dataset tables mirror those files for the `/dashboard/rag/` Data Supply page and audit flow.

```bash
# Mirror the local benchmark catalog into dataset tables.
./.venv/bin/python scripts/sync_local_benchmarks.py

# Run a benchmark directly from a local dataset file.
./.venv/bin/python scripts/run_rag_benchmark.py \
  --dataset benchmarks/agora_rag_testset_100_mixed_en.json \
  --experiment-id agora_mixed_en_local
```

Notes:

1. `scripts/run_rag_benchmark.py` accepts `--dataset`; `--dataset-id` and `--suite` are deprecated.
2. `Data Supply -> Benchmark Supply -> Sync Local Benchmarks` mirrors local files to dataset tables but does not change the benchmark execution entry point.
3. Benchmark files use NDJSON, with one case per line, and follow an explicit route-aware contract.

## POC Gaps and Next Focus

The POC has not yet completed the production-readiness work needed for a broad rollout. The next phase should focus on:

1. File and image upload for complex support conversations.
2. Streaming answer output.
3. Answer quality and knowledge hit-rate improvement.
4. Stability, load, and queue-depth validation.
5. Operational metrics and alerting for response latency, escalation rate, retrieval quality, and service health.
