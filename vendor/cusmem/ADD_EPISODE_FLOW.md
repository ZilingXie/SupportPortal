# Graphiti `add_episode` 完整流程详解

## 流程总览

```
add_episode(text)
  │
  ├── Phase 1: 预处理
  │   ├── 参数校验 (entity_types / group_id)
  │   ├── 检索历史 episodes 作为上下文
  │   └── 创建 EpisodicNode
  │
  ├── Phase 2: 实体提取 + 去重
  │   ├── extract_nodes()              LLM 提取实体
  │   ├── _collapse_exact_duplicates   精确去重 (同 episode 内)
  │   └── resolve_extracted_nodes()    跨 episode 去重
  │       ├── _collect_candidate_nodes  搜索相似已有实体
  │       ├── _resolve_with_similarity  embedding 相似度匹配
  │       └── _resolve_with_llm         LLM 最终去重判断
  │
  ├── Phase 3: 边提取 + 去重
  │   ├── extract_edges()              LLM 提取关系
  │   ├── resolve_edge_pointers()      修正临时 UUID → 正式 UUID
  │   └── resolve_extracted_edges()    边去重
  │       ├── 搜索已有边 (BM25 + 语义)
  │       └── resolve_extracted_edge() LLM 去重/冲突判断
  │
  ├── Phase 4: 属性提取
  │   ├── _extract_entity_attributes()  自定义类型属性
  │   └── _extract_entity_summaries()   实体摘要
  │
  ├── Phase 5: 持久化
  │   ├── _process_episode_data()
  │   │   ├── build_episodic_edges()    MENTIONS 边
  │   │   ├── _get_or_create_saga()     Saga 查找/创建
  │   │   ├── NEXT_EPISODE 边           episode 串联
  │   │   └── add_nodes_and_edges_bulk() 批量写入 Neo4j
  │   └── update_community()            增量社区 (可选)
  │
  └── Phase 6: 返回
      └── AddEpisodeResults { episode, nodes, edges, communities }
```

## 详细步骤

### Phase 1: 预处理 (`graphiti.py:851-899`)

**文件**：`graphiti_core/graphiti.py`
**方法**：`Graphiti.add_episode()`

```python
# 1.1 参数校验
validate_entity_types(entity_types)       # 验证 entity_types 格式
validate_excluded_entity_types(...)      # 验证排除类型不冲突

# 1.2 group_id 处理
if group_id is None:
    group_id = get_default_group_id(driver.provider)  # 默认 'main'
else:
    validate_group_id(group_id)           # 只允许字母数字+连字符

# 1.3 数据库切换 (多租户)
if group_id != driver._database:
    driver = driver.clone(database=group_id)  # 切换到对应 Neo4j database

# 1.4 检索历史 episodes
previous_episodes = await retrieve_episodes(...)  # 最近 N 条作为 LLM 上下文
    └── Cypher: MATCH (e:Episodic) WHERE e.valid_at <= $ref_time ... LIMIT N

# 1.5 创建 Episode 节点
episode = EpisodicNode(
    name=name, content=episode_body, source=source,
    source_description=source_description,
    created_at=now, valid_at=reference_time, group_id=group_id,
)
```

**调用文件**：
- `graphiti_core/helpers.py:68` — `get_default_group_id()`
- `graphiti_core/helpers.py:136` — `validate_group_id()`
- `graphiti_core/utils/maintenance/graph_data_operations.py:67` — `retrieve_episodes()`
- `graphiti_core/nodes.py` — `EpisodicNode`

---

### Phase 2: 实体提取 + 去重 (`graphiti.py:902-912`)

#### 2.1 `extract_nodes()` (`graphiti_core/utils/maintenance/node_operations.py:105`)

```
extract_nodes(clients, episode, previous_episodes, entity_types, ...)
  │
  ├── _build_entity_types_context()
  │     构造 ENTITY TYPES 字符串 → 默认 "Entity (default type)"
  │
  ├── _extract_nodes_single()  ←── 核心：LLM 调用
  │   │
  │   ├── prompt_library.extract_nodes.extract_message(context)
  │   │     └── graphiti_core/prompts/extract_nodes.py:83
  │   │         系统提示 + 7000+ 字符提取规则 + NEGATIVE EXAMPLES
  │   │
  │   ├── llm_client.generate_response(messages, response_model=ExtractedEntities)
  │   │     └── graphiti_core/llm_client/openai_base_client.py:155
  │   │         调用 DeepSeek API → 返回 {"extracted_entities": [...]}
  │   │
  │   └── ExtractedEntities(**llm_response)
  │         Pydantic 校验 → list[ExtractedEntity]
  │
  └── _create_entity_nodes()
        ExtractedEntity → EntityNode 对象
        生成 name_embedding (调用 embedder.create())
```

**LLM 输入/输出示例**：
```json
// 输入 prompt
// 文本: "小明是软件工程师，在腾讯工作，喜欢打篮球"

// 输出
{
  "extracted_entities": [
    {"name": "小明", "entity_type_id": 0, "episode_indices": [0]},
    {"name": "腾讯", "entity_type_id": 0, "episode_indices": [0]},
    {"name": "打篮球", "entity_type_id": 0, "episode_indices": [0]}
  ]
}
```

#### 2.2 `_collapse_exact_duplicate_extracted_nodes()` (`node_operations.py:336`)

同 episode 内完全同名的实体去重（原地合并，不需 LLM）。

#### 2.3 `resolve_extracted_nodes()` (`node_operations.py:627`)

```
resolve_extracted_nodes(clients, extracted_nodes, episode, ...)
  │
  ├── _collect_candidate_nodes()
  │     对每个新实体，在 Neo4j 中搜索相似已有实体
  │     └── node_similarity_search() → embedding 余弦相似度 > 阈值
  │
  ├── _build_candidate_indexes()
  │     构建 (新实体, 候选) 配对索引
  │
  ├── _resolve_with_similarity()
  │     embedding 余弦相似度 > 0.9 → 直接判定为重复
  │     相似度 < 阈值 → 判定为新实体
  │
  ├── _resolve_with_llm()
  │     中间地带：调用 LLM 判断
  │     └── prompt_library.dedupe_nodes.nodes(context)
  │         └── graphiti_core/prompts/dedupe_nodes.py:117
  │             对比新实体 vs 已有实体 → 返回 duplicate_candidate_id 或 -1
  │
  └── _commit_resolution()
        合并结果 → uuid_map (临时UUID → 已有UUID)
        更新 EntityNode 的 uuid/name/summary
```

**去重决策流程**：
```
新实体 embedding 与已有实体对比
  ├── cos > 0.9       → 自动判重
  ├── cos < 0.3       → 自动新实体
  └── 0.3 ≤ cos ≤ 0.9 → LLM 判断
```

**调用文件**：
- `graphiti_core/utils/maintenance/dedup_helpers.py:192` — `_build_candidate_indexes()`
- `graphiti_core/utils/maintenance/dedup_helpers.py:220` — `_resolve_with_similarity()`
- `graphiti_core/utils/maintenance/node_operations.py:467` — `_resolve_with_llm()`
- `graphiti_core/prompts/dedupe_nodes.py` — 去重 prompt

---

### Phase 3: 边提取 + 去重 (`graphiti.py:918-928`)

#### 3.1 `extract_edges()` (`graphiti_core/utils/maintenance/edge_operations.py:117`)

```
extract_edges(clients, episode, extracted_nodes, previous_episodes, edge_types, ...)
  │
  ├── prompt_library.extract_edges.edge(context)
  │     └── graphiti_core/prompts/extract_edges.py:94
  │          "You are an expert fact extractor..."
  │          ENTITIES 列表 + ENTITY TYPES + FACT TYPES
  │
  ├── llm_client.generate_response(messages, response_model=ExtractedEdges)
  │     └── 返回 {"edges": [{"source_entity_name": "...", ...}]}
  │
  └── ExtractedEdges(**llm_response)
        每个 Edge 绑定到具体 entity UUID
```

**LLM 输入/输出示例**：
```json
// 输入: ENTITIES = [{"name": "小明"}, {"name": "腾讯"}, {"name": "打篮球"}]
// 文本: "小明是软件工程师，在腾讯工作，喜欢打篮球"

// 输出
{
  "edges": [
    {"source_entity_name": "小明", "target_entity_name": "腾讯",
     "relation_type": "WORKS_AT",
     "fact": "小明在腾讯工作，是一名软件工程师",
     "episode_indices": [0]}
  ]
}
```

#### 3.2 `resolve_edge_pointers()` (`graphiti_core/utils/bulk_utils.py:627`)

将 LLM 返回的实体名映射到解析后的 UUID：
```
source_entity_name: "小明" → uuid_map["小明"] → 实际 UUID
target_entity_name: "腾讯" → uuid_map["腾讯"] → 实际 UUID
```

#### 3.3 `resolve_extracted_edges()` (`graphiti_core/utils/maintenance/edge_operations.py:325`)

```
resolve_extracted_edges(clients, edges, episode, nodes, previous_episodes, ...)
  │
  ├── create_entity_edge_embeddings()
  │     每条新边生成 fact_embedding
  │
  ├── semaphore_gather()  ←── 每条边并行处理
  │   └── resolve_extracted_edge()
  │       │
  │       ├── edge.get_between_nodes()     查找同源/目标的已有边
  │       ├── search()                      BM25 + 语义搜索相似边
  │       │   └── EDGE_HYBRID_SEARCH_RRF
  │       │
  │       └── llm_client.generate_response()
  │           └── prompt_library.dedupe_edges.resolve_edge(context)
  │               └── graphiti_core/prompts/dedupe_edges.py:43
  │                   判断 duplicate_facts / contradicted_facts
  │
  └── 返回 (resolved_edges, invalidated_edges, new_edges)
```

**调用文件**：
- `graphiti_core/utils/maintenance/edge_operations.py:623` — `resolve_extracted_edge()`
- `graphiti_core/prompts/dedupe_edges.py` — 边去重 prompt
- `graphiti_core/edges.py:1038` — `create_entity_edge_embeddings()`
- `graphiti_core/search/search.py:98` — `search()`

---

### Phase 4: 属性提取 (`graphiti.py:934-939`)

#### 4.1 `extract_attributes_from_nodes()` (`node_operations.py:726`)

```
extract_attributes_from_nodes(clients, nodes, episode, edges, entity_types, ...)
  │
  ├── 自定义 entity_types → _extract_entity_attributes()
  │     用 LLM 提取 Pydantic model 定义的属性值
  │     └── prompt_library.extract_nodes.extract_attributes(context)
  │
  └── 默认 → _extract_entity_summaries_batch()
        用 LLM 生成实体摘要
        └── prompt_library.extract_nodes.extract_entity_summaries_from_episodes(context)
```

#### 4.2 Embedding 生成

```python
# 在 node_operations.py:737
await create_entity_node_embeddings(embedder, nodes)
# → graphiti_core/nodes.py:1113

# 在 edge_operations.py:333 (resolve_extracted_edges 内)
await create_entity_edge_embeddings(embedder, edges)
# → graphiti_core/edges.py:1038
```

每个 EntityNode 的 `name_embedding` 和 EntityEdge 的 `fact_embedding` 在此阶段生成。

**调用文件**：
- `graphiti_core/utils/maintenance/node_operations.py:783` — `_extract_entity_attributes()`
- `graphiti_core/utils/maintenance/node_operations.py:833` — `_extract_entity_summaries_batch()`
- `graphiti_core/nodes.py:1113` — `create_entity_node_embeddings()`

---

### Phase 5: 持久化 (`graphiti.py:941-971`)

#### 5.1 `_process_episode_data()` (`graphiti.py:555-612`)

```
_process_episode_data(episode, nodes, edges, now, group_id, saga, ...)
  │
  ├── build_episodic_edges()
  │     创建 MENTIONS 边 (Episode → EntityNode)
  │     └── graphiti_core/utils/maintenance/edge_operations.py:52
  │         每个实体一条 MENTIONS 边
  │
  ├── Saga 处理
  │   ├── _get_or_create_saga()
  │   │     └── Cypher: MATCH (s:Saga {name, group_id}) → 不存在则 CREATE
  │   │         └── graphiti_core/graphiti.py:217
  │   │
  │   ├── _saga_get_previous_episode_uuid()
  │   │     └── Cypher: MATCH (s:Saga)-[HAS_EPISODE]->(e:Episodic) ... ORDER BY valid_at DESC
  │   │         └── graphiti_core/graphiti.py:265
  │   │
  │   ├── 创建 HAS_EPISODE 边 (Saga → Episode)
  │   │     └── graphiti_core/driver/neo4j/operations/has_episode_edge_ops.py:44
  │   │
  │   └── 创建 NEXT_EPISODE 边 (Episode_N → Episode_N+1)
  │         └── graphiti_core/driver/neo4j/operations/next_episode_edge_ops.py:44
  │
  └── add_nodes_and_edges_bulk()
        批量写入 Neo4j (单事务)
        └── graphiti_core/utils/bulk_utils.py:128
            ├── CREATE 新 EntityNode
            ├── CREATE 新 EntityEdge (RELATES_TO)
            ├── MERGE 已有 EntityNode (更新 summary)
            ├── MARK EXPIRED 被 invalidated 的已有边
            ├── CREATE MENTIONS 边
            └── CREATE EpisodicNode
```

**Cypher 示例**（Neo4j 写入）：
```cypher
// 新实体
CREATE (n:Entity {uuid: $uuid, name: $name, group_id: $group_id,
                   name_embedding: $embedding, created_at: $now, ...})

// 新边
MATCH (src:Entity {uuid: $source}), (tgt:Entity {uuid: $target})
CREATE (src)-[e:RELATES_TO {uuid: $uuid, name: $name, fact: $fact,
         fact_embedding: $embedding, ...}]->(tgt)

// 失效旧边
MATCH ()-[e:RELATES_TO {uuid: $uuid}]->()
SET e.expired_at = $now
```

#### 5.2 `update_community()` (可选, `graphiti.py:947-958`)

```
update_community(driver, llm_client, embedder, entity)
  └── graphiti_core/utils/maintenance/community_operations.py:325
      ├── determine_entity_community()    实体归属到哪个社区
      ├── summarize_pair()                LLM 融合实体摘要 + 社区摘要
      └── 更新 CommunityNode + HAS_MEMBER 边
```

---

### Phase 6: 返回 (`graphiti.py:973-991`)

```python
AddEpisodeResults(
    episode=episode,             # EpisodicNode
    episodic_edges=episodic_edges,  # MENTIONS + HAS_EPISODE + NEXT_EPISODE
    nodes=hydrated_nodes,        # list[EntityNode]
    edges=entity_edges,          # list[EntityEdge]
    communities=communities,     # list[CommunityNode] (仅 update_communities=True 时)
    community_edges=community_edges,
)
```

---

## LLM 调用统计

一次 `add_episode` 最少调用 LLM **3 次**，最多可到 **N+3 次**：

| 步骤 | LLM 调用 | 说明 |
|------|---------|------|
| 实体提取 | 1 次 | `extract_nodes` prompt |
| 实体去重 | 0~N 次 | 仅相似度在中间范围时调用 LLM |
| 边提取 | 1 次 | `extract_edges` prompt |
| 边去重 | 0~M 次 | 每条边可能调用 LLM 去重 |
| 属性/摘要 | 1 次 | 批量摘要 prompt |
| 社区检测 | 0~K 次 | `update_communities=True` 时每个实体 LLM 调用 |

---

## 关键文件索引

| 文件 | 函数/方法 | 作用 |
|------|----------|------|
| `graphiti_core/graphiti.py:851` | `add_episode` | 编排入口 |
| `graphiti_core/graphiti.py:217` | `_get_or_create_saga` | Saga 查找/创建 |
| `graphiti_core/graphiti.py:265` | `_saga_get_previous_episode_uuid` | 串联上一个 episode |
| `graphiti_core/graphiti.py:502` | `_extract_and_resolve_edges` | 边提取编排 |
| `graphiti_core/graphiti.py:555` | `_process_episode_data` | 持久化编排 |
| `graphiti_core/graphiti.py:654` | `_extract_and_dedupe_nodes_bulk` | 节点去重编排 |
| `graphiti_core/utils/maintenance/node_operations.py:105` | `extract_nodes` | 实体提取入口 |
| `graphiti_core/utils/maintenance/node_operations.py:244` | `_extract_nodes_single` | LLM 实体提取 |
| `graphiti_core/utils/maintenance/node_operations.py:627` | `resolve_extracted_nodes` | 实体去重入口 |
| `graphiti_core/utils/maintenance/node_operations.py:467` | `_resolve_with_llm` | LLM 去重判断 |
| `graphiti_core/utils/maintenance/node_operations.py:726` | `extract_attributes_from_nodes` | 属性/摘要提取 |
| `graphiti_core/utils/maintenance/edge_operations.py:117` | `extract_edges` | 边提取 |
| `graphiti_core/utils/maintenance/edge_operations.py:325` | `resolve_extracted_edges` | 边去重 |
| `graphiti_core/utils/maintenance/edge_operations.py:623` | `resolve_extracted_edge` | 单条边去重 |
| `graphiti_core/utils/maintenance/edge_operations.py:52` | `build_episodic_edges` | MENTIONS 边构建 |
| `graphiti_core/utils/maintenance/community_operations.py:325` | `update_community` | 增量社区 |
| `graphiti_core/utils/bulk_utils.py:128` | `add_nodes_and_edges_bulk` | 批量 Neo4j 写入 |
| `graphiti_core/utils/bulk_utils.py:627` | `resolve_edge_pointers` | 实体名→UUID |
| `graphiti_core/prompts/extract_nodes.py:83` | `extract_message` | 实体提取 prompt |
| `graphiti_core/prompts/extract_edges.py:94` | `edge` | 边提取 prompt |
| `graphiti_core/prompts/dedupe_nodes.py:117` | `nodes` | 实体去重 prompt |
| `graphiti_core/prompts/dedupe_edges.py:43` | `resolve_edge` | 边去重 prompt |
