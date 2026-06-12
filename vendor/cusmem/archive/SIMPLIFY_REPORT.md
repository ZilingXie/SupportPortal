# Graphiti 精简报告

## 总览

| 指标 | 原始 | 精简后 | 变化 |
|------|------|--------|------|
| 总文件数 | 342 | 233 | **-109 (-32%)** |
| Python 文件 | 247 | 95 | **-152 (-62%)** |
| 代码总行数 | 57,773 | 20,790 | **-36,983 (-64%)** |

## 架构决策：唯一方案

| 组件 | 原支持 | 保留 |
|------|--------|------|
| 图数据库 | Neo4j / FalkorDB / Kuzu / Neptune | **Neo4j** |
| LLM | OpenAI / Anthropic / Gemini / Groq / Azure | **OpenAI 协议** (DeepSeek) |
| Embedding | OpenAI / Gemini / Voyage / Azure | **OpenAI 协议** (Ollama bge-m3) |
| Cross-Encoder | BGE / OpenAI / Gemini | **BGE Reranker** |
| 搜索 | 17 种预置配方 | 1 种 (RRF) |
| 传输层 | MCP Server + REST API | 无 (纯库) |
| 链路追踪 | OpenTelemetry | 无 |
| 可配置项 | 50+ | ~5 |

## 已删除：6 个顶层目录

| 目录 | 文件数 | 说明 |
|------|--------|------|
| `mcp_server/` | 52 | MCP 协议服务 |
| `server/` | 16 | FastAPI REST 服务 |
| `tests/` | 41 | 测试套件 |
| `examples/` | 28 | 示例代码 |
| `spec/` | 1 | 设计文档 |
| `signatures/` | 1 | 类型签名 |
| **合计** | **139** | |

## 已删除：driver 后端 (3 种 × 13~14 文件)

| 驱动 | py 文件 | 说明 |
|------|---------|------|
| `driver/falkordb/` | 13 + falkordb_driver.py | FalkorDB |
| `driver/kuzu/` | 14 + kuzu_driver.py | 嵌入式 Kuzu |
| `driver/neptune/` | 13 + neptune_driver.py | AWS Neptune |
| `driver/graph_operations/` | 1 | 多后端抽象层 |
| `driver/search_interface/` | 1 | 搜索接口抽象 |
| `driver/record_parsers.py` | 1 | 多后端记录解析 |
| **合计** | **~45** | → 只保留 `neo4j/` |

## 已删除：LLM 客户端 (4 种)

| 文件 | 说明 |
|------|------|
| `llm_client/anthropic_client.py` | Claude |
| `llm_client/gemini_client.py` | Gemini |
| `llm_client/groq_client.py` | Groq |
| `llm_client/azure_openai_client.py` | Azure OpenAI |
| `llm_client/gliner2_client.py` | GLiNER |
| `llm_client/cache.py` | LLM 缓存 |
| `llm_client/token_tracker.py` → 重建为简化版 | Token 追踪 |
| `llm_client/errors.py` → 重建为简化版 | 错误类 |
| `llm_client/utils.py` | 工具函数 |

## 已删除：Embedder (3 种)

| 文件 | 说明 |
|------|------|
| `embedder/gemini.py` | Gemini Embedding |
| `embedder/voyage.py` | Voyage AI |
| `embedder/azure_openai.py` | Azure OpenAI |

## 已删除：Cross-Encoder (2 种)

| 文件 | 说明 |
|------|------|
| `cross_encoder/openai_reranker_client.py` | OpenAI 重排 |
| `cross_encoder/gemini_reranker_client.py` | Gemini 重排 |

→ 保留 `bge_reranker_client.py` (本地免费)

## 已删除：其他模块

| 模块 | 说明 |
|------|------|
| `tracer.py` → 重建为 NoOp | OpenTelemetry 追踪 |
| `telemetry/` | 遥测事件 |
| `namespaces/` | 命名空间 API (`graphiti.nodes.entity.save()`) |
| `decorators.py` → 重建为 NoOp | FalkorDB 多 group 装饰器 |
| `graph_queries.py` → 重建为 Neo4j-only | 多后端查询生成 |
| `graphiti_types.py` → 重建 (无 tracer) | GraphitiClients 数据类 |
| `search/search_config_recipes.py` | 17 种搜索配方 |
| `utils/content_chunking.py` | 内容分块 |
| `utils/ontology_utils/entity_types_utils.py` | 实体验证 |

## 被修改的文件

### graphiti.py (-128 行)

- 移除 `tracer`, `trace_span_prefix` 参数
- 移除 `OpenAIRerankerClient` 默认值 → 改为 `None`
- 移除 `NodeNamespace` / `EdgeNamespace` 初始化
- 移除 `_capture_initialization_telemetry()` 和 `_get_provider_type()`
- 移除 `token_tracker` property
- 移除 `Tracer` / `create_tracer` / `capture_event` / `SearchConfig` 等 import
- 硬编码 `DEFAULT_SEARCH_CONFIG` 替代 17 种配方 import
- 内联 `validate_entity_types` 函数

### llm_client/__init__.py

- 移除 `TokenUsage`, `TokenUsageTracker` 导出 → 内联 `RateLimitError`

### llm_client/openai_client.py (-10 行)

- `_create_structured_completion`: 添加 `responses.parse` 失败时的 fallback 到 `_create_completion`
- `_create_completion`: 移除 `response_format={'type': 'json_object'}` (DeepSeek 不兼容)

### llm_client/openai_base_client.py

- `DEFAULT_MODEL`: `gpt-4.1-mini` → `deepseek-chat`
- `DEFAULT_SMALL_MODEL`: `gpt-4.1-nano` → `deepseek-chat`
- `_handle_structured_response`: 支持 ChatCompletion fallback (检测 `response.choices`)

### llm_client/client.py

- 移除 `LLMCache` import → 重建 `cache.py`
- 移除 `ModelSize` import → 从 `config.py` 导入

### 驱动层修复

- `driver/__init__.py`: 从 `from neo4j import` → `from .neo4j_driver import`
- `driver/driver.py`: 移除 `GraphOperationsInterface` / `SearchInterface` import

### 提示词修改 (JSON 格式约束)

- `prompts/extract_nodes.py`: 3 个函数末尾加 OUTPUT FORMAT 约束
- `prompts/extract_edges.py`: `edge()` 函数末尾加 OUTPUT FORMAT 约束
- `prompts/dedupe_nodes.py`: 3 个函数末尾加 OUTPUT FORMAT 约束
- `prompts/dedupe_edges.py`: `resolve_edge()` 末尾加 OUTPUT FORMAT 约束

### utils/maintenance/edge_operations.py

- 替换 `from search_config_recipes import EDGE_HYBRID_SEARCH_RRF` → 内联创建

### 重建的文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `tracer.py` | 35 | NoOp 实现 |
| `graphiti_types.py` | 16 | 移除 `tracer` 字段 |
| `decorators.py` | 13 | NoOp 装饰器 |
| `graph_queries.py` | 90 | 仅 Neo4j 查询 |
| `llm_client/token_tracker.py` | 43 | 简化版 |
| `llm_client/errors.py` | 6 | RateLimitError + RefusalError |
| `llm_client/cache.py` | 10 | NoOp 缓存 |

## 功能保留清单

✅ `add_episode` — 文本摄入 + 实体/边提取
✅ `add_episode_bulk` — 批量摄入
✅ `search` / `search_` — 混合搜索 (语义 + BM25 + RRF)
✅ `build_communities` / `update_community` — 社区检测
✅ `retrieve_episodes` — 获取历史 episodes
✅ Saga 叙事线 — `_get_or_create_saga`
✅ 自定义 entity_types / edge_types — Pydantic schema
✅ `build_indices_and_constraints` — Neo4j 索引管理
✅ BGE Reranker — 本地重排序
✅ 并发控制 — `semaphore_gather` / `max_coroutines`

## 验证结果

```
Episode 1: 小明在腾讯工作，喜欢打篮球和游泳
  实体(5): 小明, 软件工程师, 腾讯, 打篮球, 游泳
  边(4):   IS_A, WORKS_AT, LIKES, LIKES  ✓

Episode 2: 小红也在腾讯，是小明同事
  实体(4): 小红, 腾讯, 小明, 打篮球
  边(3):   WORKS_AT, IS_COLLEAGUE_OF, ENGAGES_IN_ACTIVITY_WITH  ✓

Episode 3: 两人在朝阳区打球后吃火锅
  实体(4): 小明, 小红, 朝阳区球场, 火锅
  边(6):   PLAYED_BASKETBALL_WITH, ATE_WITH, ATE...  ✓

搜索 "谁喜欢打篮球" → 小明喜欢打篮球  ✓
搜索 "腾讯员工"     → 小明、小红都在腾讯  ✓
搜索 "火锅"         → 两人吃了火锅      ✓
```
