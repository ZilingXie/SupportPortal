# 完整的 chunk 处理流程

每个 chunk 在 `add_episode()` 内部依次走 7 步。chunk 按配置并发处理（`graphrag_config.yaml` 中 `max_concurrency` 控制，当前值为 1，即串行）。
Neo4j 是全局共享的——当 A 已完成第 ⑦ 步写入后，后续处理的 chunk 在 resolve 阶段就能搜到 A 入库的实体。并发时（max_concurrency > 1）此顺序无稳定保证。

```
                                文档 PDF
                                   │
                    ┌──────────────┴──────────────┐
                    │  Pipeline (graphiti_rag)     │
                    │                              │
                    │  Phase 1: Scan  扫描文件      │
                    │  Phase 2: Read  读取文档      │
                    │  Phase 3: Split 文本切块      │
                    │  Phase 4: Extract 逐块提取    │  ← chunk 并发，每个 chunk 走下面流程
                    │  Phase 5: Community 社区发现  │
                    └──────────────────────────────┘
                                   │
            ╔══════════════════════╧══════════════════════╗
            ║         add_episode(chunk N 文本)           ║
            ║          每个 chunk 独立走完 7 步             ║
            ╚══════════════════════╤══════════════════════╝
                                   │
    ┌──────────────────────────────┼──────────────────────────────┐
    │  ① extract_nodes            │  chunk 内                     │
    │                              │                              │
    │  ┌─ LLM 一抽 ──────────────┐ │  输入: chunk 文本             │
    │  │                          │ │  + entity_types 定义         │
    │  │  _extract_nodes_single() │ │  + previous_episodes         │
    │  │                          │ │                              │
    │  └──────────┬──────────────┘ │                              │
    │             ▼                │                              │
    │  ┌─ 确定性校验 ─────────────┐ │  纯代码,不调 LLM             │
    │  │                          │ │                              │
    │  │  _validate_extracted_    │ │  □ empty_name → reject      │
    │  │  entities()              │ │  □ invalid_entity_type_id   │
    │  │                          │ │    → reject, fixable=true   │
    │  │  只看当前 chunk 的实体     │ │  □ entity_type_excluded     │
    │  │                          │ │    → reject                 │
    │  │  输出: valid_entities    │ │                              │
    │  │        rejected_entities │ │                              │
    │  └──────────┬──────────────┘ │                              │
    │             │                │                              │
    │    ┌──── 是否触发二抽？ ────┐ │                              │
    │    │                        │ │                              │
    │    │  fixable_rejected > 0  │ │  有可修复的拒绝              │
    │    │   OR                    │ │                              │
    │    │  should_refine_entities│ │  mode='always'               │
    │    │  (mode, entities, min) │ │  或数量 < second_pass_min    │
    │    │                        │ │  默认 min=2                  │
    │    └────┬───────┬───────────┘ │                              │
    │         │YES    │NO           │                              │
    │         ▼       │             │                              │
    │  ┌─ LLM 二抽 ──┐│             │                              │
    │  │              ││             │  输入: chunk 文本             │
    │  │  refine_     ││             │  + 一抽 valid + rejected     │
    │  │  extracted_  ││             │  LLM 根据拒绝原因修正        │
    │  │  entities()  ││             │                              │
    │  │              ││             │  输出: 修正后的完整实体列表   │
    │  └──────┬───────┘│             │                              │
    │         │        │             │                              │
    │         ▼        │             │                              │
    │  ┌─ 确定性校验 ──┐│             │  同一套规则再过一遍           │
    │  │  再跑一次      ││             │  保证二抽结果不绕过校验      │
    │  └──────┬───────┘│             │                              │
    │         │        │             │                              │
    │         └────┬───┘             │                              │
    │              ▼                 │                              │
    │  ┌─ 转 EntityNode ───────────┐ │                              │
    │  │  _create_entity_nodes()   │ │                              │
    │  │  _collapse_exact_         │ │  chunk 内同名去重             │
    │  │  duplicate_extracted_     │ │  同 name 同 labels → 合并    │
    │  │  nodes()                  │ │                              │
    │  └───────────────────────────┘ │                              │
    │                                │                              │
    │  输出: extracted_nodes         │  纯 chunk 内产物              │
    └────────────────┬───────────────┴──────────────────────────────┘
                     │
    ┌────────────────┼──────────────────────────────────────────────┐
    │  ② resolve_nodes (实体对齐)    │  跨 chunk                     │
    │                                │                              │
    │  resolve_extracted_nodes()     │  对 chunk N 的每个新实体：     │
    │                                │                              │
    │  ┌─ embedding 语义搜索 ────────┐ │  搜 Neo4j 已有实体            │
    │  │  _collect_candidate_nodes  │ │  余弦相似度 >= 阈值           │
    │  └────────────────────────────┘ │                              │
    │             │                   │                              │
    │  ┌─ LLM 去重 ─────────────────┐ │  不确定时才调 LLM            │
    │  │  _resolve_with_llm()       │ │  判断是否同一实体             │
    │  └────────────────────────────┘ │                              │
    │             │                   │                              │
    │  ┌─ 确定新节点 / 复用旧节点 ──┐ │  如果是新实体 → 新建 UUID    │
    │  │  _collect_resolved_nodes   │ │  如果是重复 → 复用已有 UUID  │
    │  └────────────────────────────┘ │                              │
    │                                │                              │
    │  实体对齐的数据来源             │  LLM 在实体提取时输出：        │
    │  ┌───────────────────────────┐ │  name (保留原文)              │
    │  │ 实体提取 prompt 要求 LLM   │ │  official_name (规范名)       │
    │  │ 为每个实体输出:            │ │  synonyms (同义词/简称/变体)  │
    │  └───────────────────────────┘ │                              │
    │  name / official_name         │  这三个字段会写入节点属性，     │
    │  / synonyms 写入节点属性后，   │  参与实体对齐（embedding 搜索  │
    │  在边提取阶段由                │  + LLM 去重）。不依赖硬编码   │
    │  _build_name_to_node_map()    │  OCR 替换表                     │
    │  用这三个字段构建端点索引       │                              │
    │                                │                              │
    │  输出: nodes (已决定复用已有    │  有了 Neo4j UUID              │
    │        UUID 或创建新 UUID)     │                              │
    │        uuid_map (name→uuid)    │  真正写入 Neo4j 在第 ⑦ 步    │
    └────────────────┬───────────────┴──────────────────────────────┘
                     │
    ┌────────────────┼──────────────────────────────────────────────┐
    │  ③ extract_edges             │  chunk 内                      │
    │                               │                               │
    │  extract_edges()              │                               │
    │                               │                               │
    │  ┌─ LLM 一抽 ───────────────┐ │  输入: chunk 文本              │
    │  │                           │ │  + 实体列表 (name + labels)   │
    │  │  prompt_library.extract_  │ │  + edge_types 定义            │
    │  │  edges.edge(context)      │ │  输出: 所有边 (LLM 原始)      │
    │  └──────────┬────────────────┘ │                               │
    │             ▼                  │                               │
    │  ┌─ 确定性校验 ───────────────┐ │  纯代码                       │
    │  │                            │ │                               │
    │  │  _validate_extracted_      │ │  □ source_not_found           │
    │  │  edges()                   │ │    → _resolve_node_name()     │
    │  │                            │ │      查 name_to_node          │
    │  │  只看当前 chunk             │ │      (name + official_name   │
    │  │  的 name_to_node            │ │       + synonyms 索引)       │
    │  │                            │ │      精确匹配，无 OCR 替换   │
    │  │  输出: valid_edges         │ │      → fixable=有候选?       │
    │  │        rejected_edges      │ │  □ target_not_found           │
    │  │        connected_names     │ │    同上                       │
    │  │                            │ │  □ self_edge                  │
    │  │                            │ │    source.uuid == target.uuid │
    │  │                            │ │    → fixable=false            │
    │  │                            │ │                               │
    │  │  查到时: 端点名规范化       │ │  edge.source_entity_name      │
    │  │  匹配成功 → 替换为          │ │  → source_node.name           │
    │  │  source_node.name 或        │ │                               │
    │  │  target_node.name           │ │                               │
    │  └──────────┬─────────────────┘ │                               │
    │             │                   │                               │
    │    ┌──── 是否触发二抽？ ───────┐ │                               │
    │    │                           │ │                               │
    │    │  fixable_rejected > 0     │ │  有可修复的拒绝边             │
    │    │   OR                       │ │                               │
    │    │  (validated_disconnected  │ │  验证后仍有实体没边            │
    │    │   > 0                      │ │  AND mode='always'            │
    │    │   AND should_refine_edges │ │  或 edge 数量 < min           │
    │    │   (mode, edges, nodes,    │ │                               │
    │    │    min))                  │ │                               │
    │    └────┬───────┬──────────────┘ │                               │
    │         │YES    │NO              │                               │
    │         ▼       │                │                               │
    │  ┌─ LLM 二抽 ──┐│                │  输入: chunk 文本             │
    │  │              ││                │  + 一抽 valid + disconnected │
    │  │  refine_     ││                │  + fixable_rejected_edges    │
    │  │  extracted_  ││                │  LLM 看到候选端点后修正      │
    │  │  edges()     ││                │  输出: 修正后的完整边列表    │
    │  └──────┬───────┘│                │                               │
    │         │        │                │                               │
    │         ▼        │                │                               │
    │  ┌─ 确定性校验 ──┐│                │  同一套规则再过一遍           │
    │  │  再跑一次      ││                │                               │
    │  └──────┬───────┘│                │                               │
    │         │        │                │                               │
    │         └────┬───┘                │                               │
    │              ▼                    │                               │
    │  输出: edges_data                 │  纯 chunk 内产物              │
    └────────────────┬──────────────────┴──────────────────────────────┘
                     │
    ┌────────────────┼─────────────────────────────────────────────────┐
    │  ④ resolve_edges               │  跨 chunk                       │
    │                                  │                                │
    │  resolve_extracted_edges()       │  对 chunk N 的每条边：          │
    │                                  │                                │
    │  ┌─ 确定性匹配 ─────────────────┐ │  搜 Neo4j 已有同名边          │
    │  │  _find_candidate_edges       │ │  同 source+target+name       │
    │  └──────────────────────────────┘ │                                │
    │             │                     │                                │
    │  ┌─ LLM 去重 / 矛盾解决 ────────┐ │  边已存在 → 去重              │
    │  │  _resolve_with_llm()         │ │  矛盾 → 判定哪个生效期        │
    │  │                              │ │  (valid_at / invalid_at)     │
    │  └──────────────────────────────┘ │                                │
    │             │                     │                                │
    │  ┌─ 时间戳/属性提取 ────────────┐ │  从边文本中提取 valid_at      │
    │  │  extract_dates()             │ │  invalid_at                   │
    │  └──────────────────────────────┘ │                                │
    │                                  │                                │
    │  输出: resolved_edges             │  已去重、已解决矛盾的边         │
    │        invalidated_edges          │  被新边替代的旧边              │
    │        new_edges                  │  全新的边                      │
    └────────────────┬──────────────────┴──────────────────────────────┘
                     │
    ┌────────────────┼─────────────────────────────────────────────────┐
    │  ⑤ extract_attributes          │  chunk 内                       │
    │                                  │                                │
    │  extract_attributes_from_nodes() │  对 chunk N 的每个新实体：      │
    │                                  │                                │
    │  ┌─ LLM 提取结构化属性 ─────────┐ │  从文本中提取：               │
    │  │                              │ │  summary, 自定义属性          │
    │  └──────────────────────────────┘ │                                │
    │                                  │                                │
    │  输出: hydrated_nodes             │  补充了属性的实体              │
    └────────────────┬──────────────────┴──────────────────────────────┘
                     │
    ┌────────────────┼─────────────────────────────────────────────────┐
    │  ⑥ validate_against_schema     │  纯代码, chunk 粒度的最后门禁    │
    │                                  │  但看到的数据已跨 chunk 去重    │
    │  if entity_types or edge_types:  │                                 │
    │                                  │  ┌─ 实体过滤 ─────────────────┐ │
    │  mode='lenient' → 跳过           │  │ □ labels 只有 ['Entity']   │ │
    │                                  │  │   → 丢弃                   │ │
    │  mode='strict' → 硬过滤          │  │ □ labels 不匹配任何定义类型 │ │
    │                                  │  │   → 丢弃                   │ │
    │                                  │  └────────────────────────────┘ │
    │                                  │                                │
    │                                  │  ┌─ 边过滤 ───────────────────┐ │
    │                                  │  │ □ edge.name 不在定义中     │ │
    │                                  │  │   → 丢弃                   │ │
    │                                  │  │ □ source/target 实体       │ │
    │                                  │  │   已在实体过滤中被丢弃     │ │
    │                                  │  │   → 丢弃                   │ │
    │                                  │  │ □ (src_type, tgt_type)     │ │
    │                                  │  │   不在 edge_type_map 中    │ │
    │                                  │  │   → 丢弃                   │ │
    │                                  │  └────────────────────────────┘ │
    │                                  │                                │
    │  丢弃不产生拒绝账本，不触发二抽    │  没有回头路                    │
    └────────────────┬──────────────────┴──────────────────────────────┘
                     │
    ┌────────────────┼─────────────────────────────────────────────────┐
    │  ⑦ _process_episode_data       │  写入 Neo4j                     │
    │                                  │                                │
    │  ┌─ 创建/更新实体节点 ──────────┐ │                                │
    │  │  save_nodes()               │ │                                │
    │  ├──────────────────────────────┤ │                                │
    │  │ 创建边                       │ │                                │
    │  │  save_edges()               │ │                                │
    │  ├──────────────────────────────┤ │                                │
    │  │ 创建 Episode → Entity 的边   │ │                                │
    │  │  save_episodic_edges()      │ │                                │
    │  ├──────────────────────────────┤ │                                │
    │  │ 社区更新（可选）              │ │                                │
    │  │  update_community()         │ │                                │
    │  └──────────────────────────────┘ │                                │
    └───────────────────────────────────┴──────────────────────────────┘

                                    ║
                                    ║  ← 所有 chunk 处理完毕
                                    ▼

    ┌──────────────────────────────────────────────────────────────────┐
    │  Post-Ingestion（当前手动调用）                                    │
    │                                                                   │
    │  cleanup_zero_degree_noise(driver, delete=True)                   │
    │                                                                   │
    │  ┌─ 扫描全图零度实体 ──────────┐                                   │
    │  │  MATCH (n:Entity)           │  全局扫描，不按 chunk             │
    │  │  WHERE degree = 0           │                                   │
    │  └─────────────────────────────┘                                   │
    │             │                                                      │
    │  ┌─ classify_zero_degree_entity() ─┐                               │
    │  │  □ deep_section → 删除          │                               │
    │  │  □ catalog_metadata → 删除      │                               │
    │  │  □ unit_only_parameter → 删除   │                               │
    │  │  □ isolated_parameter_value → 删除│                             │
    │  │  □ ocr_fragment → 删除          │                               │
    │  │  □ (以上皆非) → 保留            │                               │
    │  └─────────────────────────────────┘                               │
    └──────────────────────────────────────────────────────────────────┘
```

## 两种校验的关键区别

| | 确定性校验 | Schema 硬校验 | Cleanup |
|---|---|---|---|
| **在哪个步骤** | ①③ (extract 内部) | ⑥ | Post-Ingestion |
| **作用域** | chunk 内 | chunk 级,但数据已全局去重 | 全局全图 |
| **产生拒绝账本** | ✅ | ❌ | N/A |
| **能触发二抽** | ✅ fixable → 触发 | ❌ | ❌ |
| **能跨 chunk 感知** | ❌ | ✅ (实体已去重) | ✅ |
| **检查什么** | 名字/ID 合法性 | 类型组合合法性 | 零度实体是否噪声 |
