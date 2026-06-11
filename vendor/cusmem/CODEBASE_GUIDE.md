# Graphiti 简化版 — 代码指南

## 项目概述

Graphiti 是一个时序知识图谱框架，专为 AI Agent 设计。核心能力：
- **摄入**：从文本/对话中提取实体和关系，去重后存入图数据库
- **检索**：混合搜索（语义向量 + 关键词 BM25 + 图遍历 + RRF 融合）
- **时序**：双时序模型，跟踪事件发生时间和系统记录时间

## 架构总览

```
用户请求
  │
  ├── REST API ──→ server/graph_service/         ← FastAPI 服务层
  │                   ├── routers/ingest.py        POST /messages
  │                   ├── routers/retrieve.py      POST /search
  │                   └── zep_graphiti.py          ZepGraphiti 封装
  │
  └── 核心库 ──→ graphiti_core/
                    ├── graphiti.py              主入口 Graphiti 类
                    ├── nodes.py / edges.py      数据模型
                    ├── driver/                  Neo4j 图数据库操作
                    ├── llm_client/              LLM 客户端 (OpenAI协议)
                    ├── embedder/                Embedding 向量化
                    ├── search/                  混合搜索 + 17种配方
                    ├── prompts/                 LLM 提取/去重提示词
                    ├── cross_encoder/           BGE 重排序
                    ├── utils/                   工具函数
                    ├── tracer.py                OpenTelemetry 链路追踪
                    └── telemetry/               遥测事件
```

## 目录结构

```
graphiti_core/
├── graphiti.py              # ★ 主入口：Graphiti 类，add_episode / search / build_communities
├── nodes.py                 # 数据模型：EntityNode, EpisodicNode, CommunityNode, SagaNode
├── edges.py                 # 数据模型：EntityEdge, CommunityEdge, EpisodicEdge 等
├── graphiti_types.py        # GraphitiClients 聚合类型
├── helpers.py               # 工具函数：semaphore_gather, validate_group_id 等
├── errors.py                # 异常类：EdgeNotFoundError, NodeNotFoundError 等
├── decorators.py            # handle_multiple_group_ids 装饰器
├── graph_queries.py         # Neo4j Cypher 查询生成（索引/全文搜索）
│
├── driver/                  # ── 图数据库层 ──
│   ├── driver.py            # GraphDriver 抽象基类
│   ├── neo4j_driver.py      # Neo4j 驱动实现
│   ├── query_executor.py    # QueryExecutor 接口
│   ├── record_parsers.py    # 数据库记录 → Python 对象
│   ├── operations/          # 抽象操作接口
│   └── neo4j/operations/    # Neo4j 具体实现 (CRUD + Search)
│
├── llm_client/              # ── LLM 层 ──
│   ├── client.py            # LLMClient 抽象基类
│   ├── config.py            # LLMConfig 配置类
│   ├── openai_client.py     # OpenAI 协议客户端 → DeepSeek / Ollama
│   ├── openai_base_client.py# 基类：generate_response / retry 逻辑
│   ├── openai_generic_client.py
│   ├── token_tracker.py     # Token 用量追踪
│   ├── cache.py             # LLM 响应缓存
│   └── errors.py            # RateLimitError
│
├── embedder/                # ── 向量化层 ──
│   ├── client.py            # EmbedderClient 抽象基类
│   └── openai.py            # OpenAI 协议 → Ollama bge-m3
│
├── cross_encoder/           # ── 重排序层 ──
│   ├── client.py            # CrossEncoderClient 抽象基类
│   └── bge_reranker_client.py # BGE Reranker (本地免费)
│
├── search/                  # ── 搜索层 ──
│   ├── search.py            # 搜索编排：edge/node/episode/community 四路并行
│   ├── search_utils.py      # 底层实现：similarity_search, fulltext_search, bfs, RRF, MMR
│   ├── search_config.py     # SearchConfig / EdgeSearchConfig / Reranker 枚举
│   ├── search_config_recipes.py  # ★ 17种预置搜索配方
│   ├── search_filters.py    # 搜索结果过滤
│   └── search_helpers.py    # 辅助函数
│
├── prompts/                 # ── LLM 提示词 ──
│   ├── extract_nodes.py     # 实体提取 + 摘要 prompt
│   ├── extract_edges.py     # 关系提取 prompt
│   ├── extract_nodes_and_edges.py # 联合提取 prompt
│   ├── dedupe_nodes.py      # 实体去重 prompt
│   ├── dedupe_edges.py      # 关系去重 prompt
│   ├── summarize_nodes.py   # 节点摘要 prompt
│   ├── summarize_sagas.py   # Saga 摘要 prompt
│   ├── lib.py               # PromptLibrary 统一入口
│   └── models.py            # Message / PromptVersion 类型
│
├── utils/                   # ── 工具层 ──
│   ├── maintenance/
│   │   ├── node_operations.py    # extract_nodes / resolve_extracted_nodes
│   │   ├── edge_operations.py    # extract_edges / resolve_extracted_edges
│   │   ├── community_operations.py # build_communities / update_community
│   │   ├── combined_extraction.py   # 联合提取
│   │   ├── dedup_helpers.py      # 去重辅助
│   │   ├── graph_data_operations.py # retrieve_episodes / clear_data
│   │   └── attribute_utils.py    # 属性提取
│   ├── bulk_utils.py        # 批量摄入 + 去重
│   ├── datetime_utils.py    # 时间工具
│   └── text_utils.py        # 文本截断
│
├── tracer.py                # OpenTelemetry 链路追踪 (Tracer / NoOpTracer)
├── telemetry/               # 遥测事件
├── models/                  # Neo4j DB 查询辅助
└── migrations/              # 数据库迁移
```

## 数据模型

### 核心节点

| 类型 | 文件 | 说明 |
|------|------|------|
| `EntityNode` | nodes.py | 实体节点（人、组织、地点、事物）|
| `EpisodicNode` | nodes.py | 原始对话/事件记录 |
| `CommunityNode` | nodes.py | 聚合的实体社区 |
| `SagaNode` | nodes.py | 叙事线（会话线程）|

### 核心边

| 类型 | 文件 | 说明 |
|------|------|------|
| `EntityEdge` | edges.py | 实体间关系（RELATES_TO）|
| `EpisodicEdge` | edges.py | Episode→Entity 提及（MENTIONS）|
| `CommunityEdge` | edges.py | 社区成员（HAS_MEMBER）|
| `HasEpisodeEdge` | edges.py | Saga→Episode |
| `NextEpisodeEdge` | edges.py | Episode→Episode 时序链 |

### Episode 类型

```python
EpisodeType.message  # 对话消息（默认）
EpisodeType.text     # 纯文本
EpisodeType.json     # JSON 数据
EpisodeType.humeur   # Humeur 平台
```

## 核心 API

### 初始化

```python
from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

g = Graphiti(
    uri='bolt://localhost:7687',
    user='neo4j',
    password='password',

    # LLM (DeepSeek)
    llm_client=OpenAIClient(config=LLMConfig(
        api_key='sk-xxx',
        base_url='https://api.deepseek.com/v1',
        model='deepseek-chat',
    )),

    # Embedding (Ollama bge-m3)
    embedder=OpenAIEmbedder(config=OpenAIEmbedderConfig(
        embedding_model='bge-m3:latest',
        base_url='http://localhost:11434/v1/',
        embedding_dim=1024,
    )),

    # 可选：Cross-Encoder 重排序
    cross_encoder=BGERerankerClient(),

    # 可选：OpenTelemetry 追踪
    tracer=otel_tracer,
    trace_span_prefix='myapp.graphiti',
)
```

### 摄入 (add_episode)

```python
from datetime import datetime, timezone
from graphiti_core.nodes import EpisodeType

result = await g.add_episode(
    name='conv-001',
    episode_body='张伟在字节跳动工作，他是一名后端工程师，喜欢打羽毛球。',
    source_description='chat',
    reference_time=datetime.now(timezone.utc),
    source=EpisodeType.message,

    # 可选参数
    group_id='my-tenant',             # 多租户分区
    entity_types={'Person': Person},  # 自定义实体类型 (Pydantic)
    edge_types={'WORKS_AT': Edge},    # 自定义边类型
    saga='用户对话',                   # 叙事线
    update_communities=True,          # 同步更新社区
    custom_extraction_instructions='',# LLM 提取指令
)
# result.nodes, result.edges, result.episode
```

### 搜索 (search / search_)

```python
# 简单搜索 — 默认 EDGE_HYBRID_SEARCH_RRF
edges = await g.search(query='谁喜欢打羽毛球', num_results=10)

# 高级搜索 — 17种配方任选
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_CROSS_ENCODER
from graphiti_core.search.search_filters import SearchFilters

results = await g.search_(
    query='工程师',
    config=COMBINED_HYBRID_SEARCH_CROSS_ENCODER,
    group_ids=['my-tenant'],
    center_node_uuid='some-node-uuid',
    search_filter=SearchFilters(),
)
# results.edges, results.nodes, results.episodes, results.communities
```

### 批量摄入 (add_episode_bulk)

```python
from graphiti_core.utils.bulk_utils import RawEpisode

result = await g.add_episode_bulk(
    bulk_episodes=[
        RawEpisode(name='ep1', content='...', source_description='...',
                   source=EpisodeType.message, reference_time=now),
        RawEpisode(name='ep2', content='...', source_description='...',
                   source=EpisodeType.message, reference_time=now),
    ],
)
```

### Saga 叙事线

```python
# 自动创建 Saga（首次使用时）
ep1 = await g.add_episode(..., saga='学习记录')
ep2 = await g.add_episode(..., saga='学习记录',
                           saga_previous_episode_uuid=ep1.episode.uuid)
# 自动创建 NEXT_EPISODE 边串联

# Saga 摘要
saga = await g.summarize_saga(saga_id='...')
```

### 社区检测

```python
# 批量构建社区
communities, community_edges = await g.build_communities()

# 增量更新（摄入时）
await g.add_episode(..., update_communities=True)
```

### 其他

```python
# 直接添加三元组
await g.add_triplet(source_node=..., edge=..., target_node=...)

# 检索历史 episodes
episodes = await g.retrieve_episodes(reference_time=now, last_n=10)

# 清空数据
from graphiti_core.utils.maintenance.graph_data_operations import clear_data
await clear_data(g.driver)

# 关闭连接
await g.close()
```

## REST API 端点

```bash
cd server/ && uvicorn graph_service.main:app --reload
```

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/messages` | 摄入消息 |
| POST | `/search` | 搜索事实 |
| POST | `/get-memory` | 从消息组合搜索 |
| GET  | `/entity-edge/{uuid}` | 获取指定边 |
| GET  | `/episodes/{group_id}` | 获取 episodes |
| POST | `/entity-node` | 创建实体节点 |
| DELETE | `/entity-edge/{uuid}` | 删除边 |
| DELETE | `/group/{group_id}` | 删除组 |
| DELETE | `/episode/{uuid}` | 删除 episode |
| POST | `/clear` | 清空数据库 |

## 搜索配方说明

所有配方位于 `graphiti_core/search/search_config_recipes.py`，由两个维度组合：

**搜索范围**：`Edge` / `Node` / `Episode` / `Community`

**重排算法**：

| 算法 | 说明 | 成本 |
|------|------|------|
| RRF | 倒数排名融合 | 免费 |
| MMR | 最大边际相关性 | 免费 |
| node_distance | 图距离排序 | 免费 |
| episode_mentions | 引用次数排序 | 免费 |
| cross_encoder | BGE 模型精排 | 需要模型 |

## 运行测试

```bash
# 全功能测试
export DEEPSEEK_API_KEY=sk-xxx
python3 test_all.py

# 快速演示
python3 run.py
```

## 技术栈

| 组件 | 选择 |
|------|------|
| 图数据库 | Neo4j 5.26+ |
| LLM | DeepSeek (OpenAI协议) |
| Embedding | Ollama bge-m3 (1024维) |
| 重排序 | BGE Reranker v2-m3 |
| 链路追踪 | OpenTelemetry (可选) |

## 数据流

```
add_episode(text)
  │
  ├── retrieve_episodes()         获取历史 context
  ├── extract_nodes()             LLM 提取实体
  ├── resolve_extracted_nodes()   去重 (embedding + LLM)
  ├── extract_edges()             LLM 提取关系  
  ├── resolve_extracted_edges()   边去重 + 搜索冲突
  ├── extract_attributes()        属性提取
  ├── _process_episode_data()     保存 + Saga 串联
  └── update_community()          增量社区 (可选)

search(query)
  │
  ├── embedder.create()           查询向量化
  ├── semaphore_gather()          四路并行:
  │   ├── edge_search()           BM25 + 语义 + BFS
  │   ├── node_search()           BM25 + 语义 + BFS
  │   ├── episode_search()        BM25
  │   └── community_search()      BM25 + 语义
  └── RRF / MMR / CrossEncoder   结果融合 + 重排
```
