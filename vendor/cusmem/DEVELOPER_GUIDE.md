# Graphiti 开发者指南 — 核心流程篇

> **代码版本**: `simplify` 分支，commit `c1a2026` 附近。此后代码可能继续演进，
> 不确定某个实现细节时以源码为准。

本文档聚焦系统的内部流程和实现细节：图谱怎么建的、去重怎么做的、时间怎么管的、社区怎么检测的。
每个流程标注代码位置（文件:行号），不搬运代码。

## Known Caveats

- **ruff 格式**：`ruff check` 当前通过。新代码提交前应运行 `ruff check && ruff format`。
- **测试环境**：集成测试需要 Neo4j 5.26+、Ollama (bge-m3)、DeepSeek API Key。
  `sentence-transformers` 是可选的（仅 Cross-Encoder 配方需要）。
- **旧数据不会自动升级**：已有图谱中的旧节点不会自动获得 `official_name`/`synonyms` 属性。
  需要重新导入或手工补抽才能享受实体归一化的效果。
- **Upsert 不是重建**：upsert 只跳过未变化 chunk，已写入的 episode 不会自动清理。
  如果 chunk 内容变化，会在 Neo4j 中新增 episode 和实体，旧数据可能残留。

---

## 1. `add_episode()` — 完整的知识提取管道

**入口**: `graphiti_core/graphiti.py:851`

一条文本进入系统后，经过 8 个阶段，最终变成 Neo4j 中的实体节点和关系边。

```
add_episode(text)
  │
  ├── [1] 检索历史
  │   │ graphiti_core/graphiti.py:980-990
  │   └── 查最近 N 条 episode 作为 LLM 上下文（当前 chunk 的前几条）
  │
  ├── [2] 实体提取 (LLM)
  │   │ graphiti_core/utils/maintenance/node_operations.py:71
  │   │ → _extract_nodes_single() 调用 LLM
  │   │ → prompts/extract_nodes.py:83 extract_message()
  │   └── LLM 返回 {"extracted_entities": [...]}
  │       ★ 每个实体现在可以带 official_name + synonyms
  │
  ├── [2a] 同 chunk 内去重
  │   │ graphiti_core/utils/maintenance/node_operations.py:337
  │   └── 同 chunk 内 (name+type) 组合键相同的实体合并，保留 type 标签更多的
  │
  ├── [3] 实体去重（跨 chunk）
  │   │ graphiti_core/utils/maintenance/node_operations.py:627
  │   │ → resolve_extracted_nodes()
  │   │
  │   ├── [3a] 候选搜索: 用新实体名做 embedding 搜索已有实体
  │   │   │ node_operations.py:418
  │   │   └── 语义相似度搜索，找 top-N 相似已有实体
  │   │
  │   ├── [3b] 精确匹配: (标准化名称, 类型) 完全一致 → 直接判重
  │   │   │ dedup_helpers.py:230 _resolve_with_similarity()
  │   │   └── 使用 _dedup_key(name, labels) = (normalized_name, type_tuple)
  │   │       ★ 同时索引 official_name + synonyms 到候选列表
  │   │
  │   ├── [3c] 模糊匹配: MinHash + LSH + Jaccard > 阈值 → 判重
  │   │   └── dedup_helpers.py:255+
  │   │
  │   └── [3d] LLM 判断: 中间地带交给 LLM
  │       │ node_operations.py:467 _resolve_with_llm()
  │       └── prompts/dedupe_nodes.py 中文 prompt
  │           "同名同类型→很可能重复; 同名不同类型→不能判重"
  │
  ├── [4] 边提取 (LLM)
  │   │ graphiti_core/utils/maintenance/edge_operations.py:117
  │   │ → prompts/extract_edges.py:63 edge()
  │   └── LLM 返回 {"edges": [{source_entity_name, target_entity_name,
  │       relation_type, fact, ...}]}
  │   ⚠ 约束: source/target 只能用当前 chunk 提取到的实体名
  │       不在列表里 → 丢弃 (这就是"跨 chunk 引用失败"的根因)
  │
  ├── [5] 边去重 + 冲突检测
  │   │ edge_operations.py:325 resolve_extracted_edges()
  │   │
  │   ├── [5a] 边名解析: 实体名 → UUID (resolve_edge_pointers)
  │   │   └── bulk_utils.py:627
  │   │
  │   ├── [5b] 搜索已有边: BM25 + 语义搜索
  │   │   └── 使用 EDGE_HYBRID_SEARCH_RRF 配方
  │   │
  │   └── [5c] LLM 去重判断: 每条新边并行处理
  │       │ edge_operations.py:623 resolve_extracted_edge()
  │       │ → prompts/dedupe_edges.py:26 resolve_edge()
  │       └── 返回 duplicate_facts(重复) + contradicted_facts(冲突)
  │
  ├── [6] 属性提取 + 摘要
  │   │ node_operations.py:726 extract_attributes_from_nodes()
  │   ├── 自定义 entity_types → LLM 提取属性值
  │   └── 默认 → LLM 生成中文实体摘要
  │
  ├── [6a] Schema 硬校验 ★
  │   │ graphiti_core/graphiti.py:1040 (schema_validate.py 调用点)
  │   └── strict 模式: 过滤非法实体类型、泛型Entity、非法边类型、非法头尾类型组合
  │
  └── [7] 持久化
      │ graphiti_core/graphiti.py:1041 _process_episode_data()
      └── 实体节点 + 关系边 + MENTIONS 边 + Saga 串联
          → add_nodes_and_edges_bulk() → Neo4j 写入
          ★ neo4j_safe_attributes() 在写入前转换复杂属性
```

---

## 2. 去重系统 — 四层防御

全部逻辑集中在两个文件：

| 文件 | 职责 |
|------|------|
| `utils/maintenance/node_operations.py` | 编排: 收集候选→相似度→LLM |
| `utils/maintenance/dedup_helpers.py` | 核心: normalized_existing索引、精确匹配、模糊匹配 |

### 2.1 去重键 (Type-Aware)

**位置**: `dedup_helpers.py:39-55`

```python
_dedup_key(name, labels) → (normalized_name, type_tuple)

# 例:
# "Java" + ["ProgrammingLanguage"] → ("java", ("ProgrammingLanguage",))
# "Java" + ["Location"]            → ("java", ("Location",))
# 两个不同的 key → 不会合并
```

同一 chunk 内的同名合并也用了相同逻辑（`node_operations.py:337`）。

### 2.2 候选索引构建

**位置**: `dedup_helpers.py:202 _build_candidate_indexes()`

对每个已有实体，建立三个索引：
1. **精确匹配索引** — `normalized_existing[(name, type)]` → 实体列表
2. **归一化索引** ★ — 同时用 `official_name` 和 `synonyms` 建索引
3. **模糊匹配索引** — MinHash/LSH 分桶

归一化索引的逻辑：如果一个已有实体的 `attributes` 里有 `official_name: "道岔转辙机"`，那么新实体名 "道岔转辙机" 也能直接命中。

### 2.3 候选搜索

**位置**: `node_operations.py:418 _semantic_candidate_search()`

对每个新实体名生成 embedding，用余弦相似度在 Neo4j 中搜索已有实体。不是 exhaustive search——限制 top-N 个候选（默认 `NODE_DEDUP_CANDIDATE_LIMIT=15`）。

### 2.4 精确匹配

**位置**: `dedup_helpers.py:230 _resolve_with_similarity()`

如果候选列表中恰好有一个实体的 (name, type) 与新实体一致 → 直接判重，不走 LLM。这是最常见的情况，不需要 LLM 调用。

### 2.5 模糊匹配

**位置**: `dedup_helpers.py:255+`

对高熵度的实体名（名称长、token 多），用 MinHash 做模糊匹配。Jaccard 相似度 > 阈值 → 直接判重。

低熵度的名称（如单个字、"Ⅱ"）跳过模糊匹配，交给 LLM。

### 2.6 LLM 判断

**位置**: `node_operations.py:467 _resolve_with_llm()`

以上都不满足的，把新实体和候选列表一起交给 LLM 判断。Prompt 在 `prompts/dedupe_nodes.py`。

---

## 3. 边去重 + 冲突检测 — 时序模型的核心

**位置**: `utils/maintenance/edge_operations.py:325-540`

### 3.1 三步处理

```
新边列表
  │
  ├── 1. 去重 (同 chunk 内)
  │   同源/同目标/同 fact → 只保留一条
  │
  ├── 2. 搜索已有边 (每条新边并行)
  │   ├── get_between_nodes() → 同源目标的已有边
  │   └── search() → BM25 + 语义搜索相似边
  │
  └── 3. LLM 去重判断
      │ prompts/dedupe_edges.py
      └── 返回:
          ├── duplicate_facts:  与哪些已有边重复 (idx 列表)
          └── contradicted_facts: 与哪些已有边冲突 (idx 列表)
```

### 3.2 冲突处理的时序语义

**位置**: `edge_operations.py:744-752`

```python
如果 LLM 判定新边与某已有边冲突 (contradicted):
  → 已有边的 expired_at 被设为当前时间
  → 已有边被移入 invalidated_edges 列表
  → 新边正常写入

返回三个列表:
  resolved_edges:    正常使用的边
  invalidated_edges: 被新信息"推翻"的旧边
  new_edges:         新创建的边 (用于后续 summary 生成)
```

### 3.3 EntityEdge 的时间字段

**位置**: `edges.py:263-277`

| 字段 | 写入时机 | 语义 |
|------|---------|------|
| `created_at` | 边首次写入 Neo4j | 系统记录时间 |
| `valid_at` | LLM 从文本推断 | 事实从何时开始有效 |
| `invalid_at` | LLM 从文本推断 | 事实在何时失效 |
| `expired_at` | 冲突检测设置 | 边在知识图谱中被废弃的时间 |

`valid_at` 和 `invalid_at` 由 LLM 在边提取阶段从文本中推断（如 "Alice 于 2020 年加入公司" → valid_at=2020）。`expired_at` 由系统自动设置（如新信息表明 Alice 已离职 → 旧边 expired_at=now()）。

查询时可以通过时间过滤只看某时刻生效的事实——这是 Graphiti 作为"时序知识图谱"的核心能力。

---

## 4. 社区检测

**位置**: `utils/maintenance/community_operations.py`

### 4.1 批量构建

```python
# graphiti.py:1364 → community_operations.py:216
await g.build_communities()
```

流程：
1. `get_community_clusters()` — 用 Neo4j 的标签传播算法 (LPA) 聚类
2. 对每个 cluster，用 LLM 生成社区摘要 (summarize_pair)
3. 创建 CommunityNode + HAS_MEMBER 边

### 4.2 增量更新

```python
# graphiti.py:1055 — add_episode 时
await g.add_episode(..., update_communities=True)
```

每次添加新 episode 后，对每个新实体调用 `update_community()` (`community_operations.py:325`)：
1. 判断实体属于哪个已有社区
2. 把实体的摘要融合进社区摘要
3. 创建 HAS_MEMBER 边

### 4.3 摘要融合

**位置**: `community_operations.py:141-170`

社区的摘要用 `summarize_pair()` 增量合并：两个摘要 → LLM 融合成一个新的。多层合并一直做到只剩一个摘要。

---

## 5. 搜索管道

**位置**: `search/search.py`

### 5.1 流程

```
search(query)
  │
  ├── embedder.create(query) → 1024 维向量
  │
  └── semaphore_gather() 四路并行:
      ├── edge_search()         # 关系事实
      │   ├── BM25 全文搜索
      │   ├── 余弦相似度 (embedding)
      │   ├── BFS 图遍历 (可选)
      │   └── RRF/MMR/CrossEncoder 融合排序
      │
      ├── node_search()         # 实体节点
      ├── episode_search()      # 原始文本
      └── community_search()    # 社区
```

### 5.2 search vs search_

| 方法 | 返回 | 配方 |
|------|------|------|
| `search()` | `list[EntityEdge]` | 默认 EDGE_HYBRID_SEARCH_RRF |
| `search_()` | `SearchResults(edges, nodes, episodes, communities, scores)` | 默认 COMBINED_HYBRID_SEARCH_RRF |

### 5.3 RRF 排序

**位置**: `search/search_utils.py` 中的 `rrf()` 函数

RRF (Reciprocal Rank Fusion) 是纯数学算法：对每个搜索结果，计算 `1/(k+rank)`（k 默认 60），然后把多个排序列表的分数加到一起。不需要模型，完全免费。

---

## 6. Upsert 幂等机制

**位置**: `graphiti_rag/ingest_state.py` + `graphiti_rag/pipeline.py:279+`

### 6.1 稳定身份

每个 chunk 生成稳定 ID：`md5(document_path)[:12] :: chunk_index :: start_char :: end_char`

内容 hash：文本 normalize（去多余空白）后的 sha256[:16]

Schema hash：entity_types + edge_types 序列化后的 sha256[:12]

### 6.2 判断逻辑

```
upsert 模式:
  for each chunk:
    if state.is_unchanged(chunk_id, content_hash, schema_hash):
      skip  ← 内容没变、Schema 没变
    else:
      extract()  ← 重新处理

append 模式:
  每次都 extract()
```

### 6.3 启用方式

```yaml
# graphrag_config.yaml
pipeline:
  ingest_mode: "upsert"    # 默认是 "append"
```

或环境变量：`GRAPHRAG_INGEST_MODE=upsert`

状态文件固定为 `.graphiti_rag/ingest_state.json`，目前没有 `state_path` 配置项。

### 6.4 重要边界

- Upsert 只**跳过未变化的 chunk**，不删除旧图谱内容。
- 如果 chunk 内容变了，新 episode 会被追加到图谱中，旧 episode **不会自动清理**。
- Schema 变化（`schema_hash` 不匹配）会触发该 chunk 重新处理。
- 失败 chunk 不会被标记完成，下次继续跑。

---

## 7. Schema 硬校验

**位置**: `graphiti_core/schema_validate.py` → `graphiti_core/graphiti.py:1042`

### 7.1 插入点

在 `add_episode()` 内，`extract_attributes_from_nodes()` 之后、`_process_episode_data()` 之前。

**为什么插在这里？** 此时实体和边已经通过 LLM 提取+去重+属性提取，数据最完整，过滤不会丢东西。在此之前过滤会干扰去重（去重需要看完整列表）。

### 7.2 模式控制

当前只要传了 `entity_types` 或 `edge_types`（即配置了 Schema），就会执行校验。`schema_mode='strict'` 执行硬过滤，`schema_mode='lenient'` 跳过所有过滤。
`schema_mode` 已经通过 Config → Pipeline → Extractor → `add_episode(schema_mode=...)` 链路接入。

### 7.3 过滤规则 (strict 模式)

```
实体:
  ✗ 只有 'Entity' 标签 (泛型实体)
  ✗ 标签不在 entity_types 定义中
  ✓ 通过

边:
  ✗ 边名称不在 edge_types 定义中
  ✗ 边的头尾实体类型组合不在 edge_type_map 中
  ✓ 通过

lenient 模式: 不做任何过滤
```

---

## 8. Neo4j 属性安全

**位置**: `graphiti_core/helpers.py:136-168`

### 8.1 为什么需要

Neo4j 节点/关系属性只接受：`str, int, float, bool, list[str], list[int], list[float], list[bool]`

LLM 提取的属性可能包含：
- 嵌套 dict → json.dumps() → string
- None 值 → 跳过不写
- datetime → .isoformat() → string
- 混合类型 list → json.dumps() → string

### 8.2 调用点

三个写入路径都需要经过安全转换：
1. `nodes.py:570` — `EntityNode.save()` 单条写入
2. `bulk_utils.py:186` — 批量写入的节点属性展开
3. `bulk_utils.py:220` — 批量写入的边属性展开

### 8.3 为什么不用 JSON 序列化整个 attributes

`neo4j_safe_attributes()` 不做整体序列化，而是按值逐一转换。因为 Neo4j 的全文索引和向量索引需要直接访问基础类型属性——如果整体序列化，BM25 和余弦搜索就失效了。

---

## 9. 实体归一化

**位置**: `prompts/extract_nodes.py` (模型) + `utils/maintenance/node_operations.py:314+` (存储) + `utils/maintenance/dedup_helpers.py:210+` (索引)

### 9.1 流程

```
LLM 提取:
  name: "转辙机"             ← OCR 识别的原文
  official_name: "道岔转辙机"  ← 规范名
  synonyms: ["switch machine", "转和机", "转国机"]  ← 别名+OCR变体

存储 (node_operations.py:314):
  → node.name = "转辙机"           (主名，不变)
  → node.attributes.official_name = "道岔转辙机"
  → node.attributes.synonyms = ["switch machine", "转和机", "转国机"]

索引 (dedup_helpers.py:210):
  → normalized_existing[("转辙机", type)] = [node]
  → normalized_existing[("道岔转辙机", type)] = [node]  ← 归一化索引
  → normalized_existing[("switch machine", type)] = [node]  ← 同义词索引
  → normalized_existing[("转和机", type)] = [node]  ← OCR 变体索引
```

新实体无论用哪个名字来查，都能命中同一个已有实体。

### 9.4 当前限制

这是 **"alias-aware dedup"**，不是完整的 **"canonical rename"**：
- `official_name` 和 `synonyms` 被存入 `node.attributes` 并参与候选索引
- 但 `node.name`（主名称）**不会被自动替换**为 `official_name`
- 如果原始名是 OCR 乱码（如 "转糙机"），即使记录了 `official_name="转辙机"`，节点主名仍然是 "转糙机"
- 已有图谱中的旧节点如果没有 `official_name/synonyms` 属性，不会自动获得这些字段——需要重新导入或补抽

---

## 10. 关键文件速查

| 想了解 | 看这个 |
|--------|--------|
| add_episode 完整流程 | `graphiti_core/graphiti.py:851-1100` |
| 实体提取+类型上下文 | `utils/maintenance/node_operations.py:71-150` |
| 去重核心 (精确+模糊) | `utils/maintenance/dedup_helpers.py:202-278` |
| 去重编排 (候选+LLM) | `utils/maintenance/node_operations.py:407-600` |
| 去重 Prompt | `prompts/dedupe_nodes.py` |
| 归一化存储 | `utils/maintenance/node_operations.py:314-330` |
| 归一化索引 | `utils/maintenance/dedup_helpers.py:210-218` |
| 边提取 | `utils/maintenance/edge_operations.py:117-230` |
| 边去重+冲突 | `utils/maintenance/edge_operations.py:325-540` |
| 时序模型 (valid_at 等) | `graphiti_core/edges.py:263-277` |
| 社区检测 | `utils/maintenance/community_operations.py:141-352` |
| Schema 硬校验 | `graphiti_core/schema_validate.py` |
| Schema 校验插入点 | `graphiti_core/graphiti.py:1040` |
| Neo4j 安全属性 | `graphiti_core/helpers.py:136-168` |
| 安全属性调用点 | `nodes.py:570` + `bulk_utils.py:186,220` |
| 搜索编排 | `search/search.py:98-230` |
| 搜索配方 | `search/search_config_recipes.py` |
| Upsert 机制 | `graphiti_rag/ingest_state.py` + `graphiti_rag/pipeline.py:279+` |
| OCR 三层解析 | `graphiti_rag/components.py:149-220` |
| 实体提取 Prompt (中文) | `prompts/extract_nodes.py:83-116` |
| 边提取 Prompt (中文) | `prompts/extract_edges.py:63-110` |
