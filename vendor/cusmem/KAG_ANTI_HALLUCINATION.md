# KAG 减少 LLM 幻觉的全部技术手段

## 总览

KAG（OpenSPG）通过**5 层防御体系**减少 LLM 知识提取中的幻觉：

| 层级 | 机制 | 阶段 | 解决的问题 |
|------|------|------|---------|
| 1 | **Schema 约束** | 提取前 | LLM 编造不存在的类型 |
| 2 | **七元组归一化** | NER 阶段 | 实体名歧义、同义词混乱 |
| 3 | **实体的标准化** | NER 之后 | 缩写/俗称/别名归一化 |
| 4 | **PostProcessor 后处理** | 写入前 | 无效数据过滤、实体链接 |
| 5 | **领域本体 + 结构化输出** | 全流程 | 分类层级引导、格式强制 |

这 5 层从不同角度约束 LLM 的输出，形成"防御纵深"。下面逐一详解。

---

## 第一层：Schema 约束

### 原理

KAG 在提取前会从 SPG Server 加载项目的**预定义 Schema**，包含：
- 允许的实体类型及其属性定义
- 允许的关系类型及头尾实体类型约束
- 属性的值类型（string/float/integer/boolean）

提取时，Schema 被注入 prompt 作为 `$schema` 变量：

```python
# knowledge_unit_ner.py
self.schema = SchemaClient(host_addr=..., project_id=...).extract_types(...)
self.template = Template(self.template).safe_substitute(
    schema=json.dumps(self.schema, ensure_ascii=False)
)
```

### Prompt 约束写法

```
### schema
[
   { "entity_type": "Person", "properties": {"name": "str", "role": "str"} },
   { "entity_type": "Organization", "properties": {"name": "str", "industry": "str"} }
]

实体必须严格符合schema中的预定义类型。如果实体类型不存在，则返回空列表。
```

### 两种提取模式

| 提取器 | Schema 使用方式 | 文件 |
|--------|---------------|------|
| `SchemaFreeExtractor` | 参考 Schema，`category` 可自由选择 | `schema_free_extractor.py` |
| `SchemaConstraintExtractor` | **强制约束**，超出 Schema 的类型不允许 | `schema_constraint_extractor.py` |

### 效果

```
无 Schema: LLM 提取 "Java" → type="Program" (不存在)
有 Schema: LLM 提取 "Java" → type="ProgrammingLanguage" (必须在 schema 中)
```

### 对你的启示

你的 `ingest_gbt.py` 里 9 种 `ENTITY_TYPES` 就在做类似的事。KAG 的额外价值是 Schema 还约束了属性类型——如果 `TechnicalParameter` 的 `数值` 属性约束为 `str`，LLM 就不会编造 `float` 值。

---

## 第二层：七元组实体归一化

### 原理

KAG 的 NER prompt 不只是提取 name + type，而是提取 7 个字段：

```json
{
  "名称": "语妄",                           // 原文出现的名称
  "类型": "Symptom",                         // 类别
  "领域本体": "医学 -> 精神科 -> 谵妄",      // 分类层级链
  "解释": "患者出现的言语混乱症状",          // 描述
  "标准名": "谵妄",                          // ← 归一化后的标准名
  "同义词": ["说胡话", "胡言乱语"]            // ← 所有别名
}
```

```python
# knowledge_unit_ner.py — process_zh()
if "标准名" in response.keys():
    ret["officialName"] = response["标准名"]
if "同义词" in response.keys():
    ret["synonyms"] = response["同义词"]
```

### 为什么七个字段比两个字段更抗幻觉

| 字段 | 抗幻觉作用 |
|------|----------|
| `标准名` | 把俗称/缩写统一映射到规范名称，后续去重时用标准名而非原名 |
| `同义词` | 显式列出别名，去重阶段可直接匹配别名 |
| `领域本体` | 强制 LLM 理解实体的分类层级，减少误分类 |
| `解释` | 描述文本帮助消歧（两个同名实体看解释区分） |

### 示例：文档中出现了 "wto"、"WTO"、"世贸组织"、"世界贸易组织"

```
NER 结果:
  名称: "wto"        → 标准名: "世界贸易组织", 同义词: ["WTO", "世贸组织"]
  名称: "WTO"        → 标准名: "世界贸易组织", 同义词: ["wto", "世贸组织"]
  名称: "世贸组织"    → 标准名: "世界贸易组织", 同义词: ["WTO"]
  名称: "世界贸易组织" → 标准名: "世界贸易组织", 同义词: []

去重阶段: 四个实体的标准名相同 → 合并为一个
```

### 对你的启示

你的 Graphiti 目前提取实体时只有 `name + entity_type_id`。加上"标准名"和"同义词"两个字段可以显著减少同名异义和同义异名的幻觉。改动不大——只需要在 `ExtractedEntity` Pydantic model 里加两个可选字段。

---

## 第三层：实体标准化（单独的 STD prompt）

### 原理

NER 提取完成后，**再调一次 LLM 做标准化**。这次用的是专用 prompt (`std.py`)：

```
输入: passage + named_entities = [
  {"name": "NYC", "category": "Location"},
  ...
]

输出: [
  {"name": "NYC", "category": "Location", "official_name": "New York City"},
  ...
]
```

### 为什么需要单独一步

NER 阶段的 LLM 主要关注"从文本中找到实体"，标准名可能不准确。单独的标准化步骤让 LLM 专注于"把已知实体映射到标准名"，分工明确，幻觉率更低。

### 防御性设计

```python
# 防止 LLM 漏掉某些实体（代码补全）
entities_with_offical_name = set()
for entity in standardized_entity:
    merged.append(entity)
    entities_with_offical_name.add(entity["name"])

# 如果 LLM 没返回某实体的标准名，原样保留
for entity in entities:
    if "name" in entity and entity["name"] not in entities_with_offical_name:
        entity["official_name"] = entity["name"]
        merged.append(entity)
```

### 医学领域版本

KAG 的 `prompts/medical/std.py` 对医学术语的标准化提示词有额外强化：

```
医学实体标准化要求:
1. 优先使用 ICD-10/ICD-11 标准术语
2. 中英文名称统一映射到中文标准名
3. 同类药物使用通用名而非商品名
```

---

## 第四层：PostProcessor 后处理

### 三步清理流程 (`kag_postprocessor.py`)

```
sub_graph → filter_invalid_data() → similarity_based_link() → external_graph_based_link()
```

### 4.1 无效数据过滤

```python
def filter_invalid_data(self, graph: SubGraph):
    valid_nodes = []
    for node in graph.nodes:
        if not node.id or not node.label:
            continue                          # ① 丢弃无名无类型的节点
        if node.label not in self.schema:
            node.label = self.format_label(OTHER_TYPE)  # ② 不在schema中的标注为Other
        valid_nodes.append(node)

    valid_edges = []
    for edge in graph.edges:
        if edge.label:
            valid_edges.append(edge)           # ③ 丢弃无名边

    return SubGraph(nodes=valid_nodes, edges=valid_edges)
```

### 4.2 基于相似度的实体链接

```python
def similarity_based_link(self, graph, property_key="name"):
    for node in graph.nodes:
        vector = node.properties.get("name_vector")   # 实体名的embedding
        if vector:
            similar_nodes = self._search_client.search_vector(
                query_vector=vector, topk=1, ...     # 查最相似的已有实体
            )
            for item in similar_nodes:
                score = item["score"]
                if score >= similarity_threshold:     # 默认 0.9
                    # 添加相似边：当前节点 → 已有节点
                    graph.add_edge(node.id, ..., "SimilarEdge", item["node"]["id"])
```

**效果**：与新提取的实体相似度 ≥ 0.9 的已有实体，自动建立链接，减少重复实体创建。

### 4.3 外部图谱链接

如果用户提供了外部图谱（如企业内部的供应商名录、产品目录），PostProcessor 会把提取结果与外部图谱做匹配：

```python
def external_graph_based_link(self, graph):
    if self.external_graph:
        self._entity_link(graph, property_key, labels=self.external_graph.get_allowed_labels())
```

### 对你的启示

Graphiti 的去重（`resolve_extracted_nodes`）是在**提取过程中**用 embedding + LLM 做去重。KAG 把这个步骤放到了**提取后**的 PostProcessor 中，好处是：
1. 不打断提取流程
2. 可以用 Schema 做类型过滤
3. 外部图谱链接独立可插拔

---

## 第五层：领域本体 + 结构化输出

### 5.1 领域本体链

KAG 要求 LLM 为每个实体输出领域本体链：

```json
{
  "名称": "中共当阳市委党校",
  "领域本体": "教育 -> 高等教育 -> 党校教育"
}
```

**作用**：引导 LLM 在分类时思考层级关系，减少误分类。例如"党校"是"教育机构"而非"政府机构"。

### 5.2 知识单元独立提取

KAG 的 `knowledge_unit.py` 把长文本分解为独立的知识单元，每个单元自包含：

```json
{
  "2019年全国火电发电量": {
    "内容": "2019年全国火电发电量51654亿千瓦时，同比增长1.9%",
    "知识类型": "事实性知识",
    "领域本体": "能源统计 -> 电力生产 -> 发电方式分类 -> 火力发电",
    "核心实体": "火电发电量,同比增长率,2019年",
    "关联问": ["2019年全国火电发电量是多少？", "火电发电量增速变化趋势"],
    "扩展知识点": ["2018年全国火电发电量","火力发电方法"]
  }
}
```

这种设计把长文本拆成原子事实，每个事实独立可验证，减少跨语句幻觉。

### 5.3 三元组实体约束

```python
# triple.py 的 entity_list 约束
{
    "entity_list": [
        {"name": "烦躁不安", "category": "Symptom"},
        {"name": "镇静药", "category": "Medicine"},
    ],
    "instruction": "每个三元组应至少包含entity_list实体列表中的一个，但最好是两个命名实体。"
}
```

允许三元组包含一个列表外的实体（比 Graphiti 宽松），但至少有一个必须在列表内——这样既防止完全游离的幻觉，又不会像 Graphiti 那样因为严格的 `name_to_node` 校验而丢弃有效边。

---

## 总结：KAG 防御体系 vs Graphiti

| 层级 | KAG 方案 | Graphiti 现状 | 可借鉴？ |
|------|---------|-------------|---------|
| Schema 约束 | SPG Server 预定义类型+属性 | `entity_types` 字典 + docstring | ✅ Schema 可用性 |
| 实体归一化 | 七元组 (标准名+同义词) | 仅 name + entity_type_id | ✅ 最有价值 |
| 实体标准化 | 独立 STD prompt | 靠 embedding+LLM 去重 | ✅ 可加入 |
| 后处理过滤 | 无效数据过滤 + 相似度链接 | `resolve_extracted_nodes` | 已做到 |
| 领域本体 | 分类层级链 | 无 | ✅ docstring 可模拟 |
| 三元组约束 | 至少一个实体在列表中 | 必须两个都在列表 | ✅ 松绑可减少丢边 |

**最值得借鉴的三个点**：
1. **七元组中的标准名+同义词** — 改动最小、收益最大
2. **三元组松绑** — 允许边包含一个列表外实体，减少"Target entity not found"丢弃
3. **无效数据过滤** — 简单的防御性代码
