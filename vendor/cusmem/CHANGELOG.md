# 代码演进日志

## 概览

从硬编码精简版到可配置 Schema 版，核心变化围绕三点：**Neo4j 写入安全化**、**Schema 外部化**、**属性提取兼容**。

## 一、Neo4j 属性安全写入

### 问题

YAML Schema 定义了带属性的实体类型（`Standard` 有 `standard_number`、`publication_date` 等字段），LLM 提取属性时返回嵌套 Map。Neo4j 节点属性只接受基础类型或基础类型数组，嵌套 Map 写入时抛出：

```
Property values can only be of primitive types or arrays thereof.
Encountered: Map{standardNumber -> String("GB/T 25338.1—2019"), ...}
```

### 方案

在两个写入路径前统一做类型安全转换。

**新增函数** (`graphiti_core/helpers.py:136-168`)：

```python
def neo4j_safe_value(value: Any) -> Any:
    """Convert any value to a Neo4j-safe type."""
    if value is None:           return None
    if isinstance(value, (str, int, float, bool)): return value
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, list):
        # 全是基础类型 → 原样保留；否则 JSON序列化
        if all(isinstance(x, (str,int,float,bool)) or x is None for x in value):
            return [x for x in value if x is not None]
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)

def neo4j_safe_attributes(attributes: dict) -> dict:
    """过滤 None 值，序列化复杂类型"""
    result = {}
    for k, v in (attributes or {}).items():
        safe = neo4j_safe_value(v)
        if safe is not None:
            result[k] = safe
    return result
```

**调用点**：

| 文件 | 位置 | 改动 |
|------|------|------|
| `graphiti_core/nodes.py:570` | `EntityNode.save()` | `node.attributes` → `neo4j_safe_attributes()` |
| `graphiti_core/utils/bulk_utils.py:186` | `add_nodes_and_edges_bulk_tx()` 节点写入 | 同上 |
| `graphiti_core/utils/bulk_utils.py:220` | `add_nodes_and_edges_bulk_tx()` 边写入 | 同上 |

**效果**：

```
改动前: Entity:0, Edge:0 (12/12全部失败)
改动后: Entity:178, Edge:168 (12/12全部成功)
```

---

## 二、YAML Schema 外部化

### 问题

上一版的实体/边类型和领域描述硬编码在 `ingest_gbt.py` 的 Python 类中，每次修改需要改代码。

### 方案

将 Schema 移到 YAML 文件，运行时加载。

**新增文件**：

| 文件 | 行数 | 职责 |
|------|------|------|
| `graphiti_rag/schema_loader.py` | 225 | YAML→Pydantic 模型构建 |
| `schemas/gbt25338.yaml` | 205 | 领域 Schema 定义 |
| `tests/test_schema_loader.py` | 57 | Schema 加载测试 |

**Schema 结构** (`schemas/gbt25338.yaml`)：

```yaml
entity_types:
  Standard:
    description: "标准/规范：GB/T、IEC、ISO 等标准编号及其名称"
    ontology: "铁路信号 -> 标准体系 -> 标准"
    properties:
      standard_number:   { type: string, description: "标准编号" }
      publication_date:  { type: string, description: "发布日期" }
      synonyms:          { type: list[string], description: "别名" }
  Product:
    description: "产品/设备：转辙机、电机、锁闭装置等"
    ontology: "铁路信号 -> 转辙设备 -> 产品"
    properties:
      official_name: { type: string, description: "规范产品名称" }
      product_type:  { type: string, description: "产品类别" }
  # ... 共 8 种实体类型

edge_types:
  REFERENCES:
    description: "标准引用标准"
    subject_type: Standard
    object_type: Standard
  # ... 共 13 种关系类型
```

**schema_loader 核心逻辑** (`graphiti_rag/schema_loader.py:40-68`)：

```python
def load_graph_schema(path: str | Path) -> LoadedSchema:
    raw = _load_mapping(path)
    entity_types = {
        type_name: _build_model(type_name, spec)    # YAML→Pydantic
        for type_name, spec in raw['entity_types'].items()
    }
    edge_types = {
        type_name: _build_model(type_name, spec, is_edge=True)
        for type_name, spec in raw['edge_types'].items()
    }
    edge_type_map = _build_edge_type_map(raw['edge_types'])
    return LoadedSchema(entity_types, edge_types, edge_type_map, raw)
```

**_build_model** (`graphiti_rag/schema_loader.py:91-100`)：

```python
def _build_model(type_name, spec, *, is_edge=False):
    # 1. 从 properties 构建 Pydantic Field
    fields = {name: (type, Field(description=desc))
              for name, desc in spec.get('properties', {}).items()}
    # 2. 创建 Pydantic model，docstring=description
    return create_model(type_name, __base__=BaseModel,
                        __doc__=spec.get('description', ''), **fields)
```

**配置集成** (`graphiti_rag/config_loader.py:82-91`)：

```python
if config.schema_path:
    loaded_schema = load_graph_schema(config.schema_path)
    config.entity_types = loaded_schema.entity_types   # 注入 Config
    config.edge_types = loaded_schema.edge_types
    config.edge_type_map = loaded_schema.edge_type_map
```

### 配置链路

```
graphrag_config.yaml          # schema.path + schema.mode
  → config_loader.load_config()
    → schema_loader.load_graph_schema(path)
      → LoadedSchema(entity_types, edge_types, edge_type_map)
        → config.entity_types / config.edge_types / config.edge_type_map
          → Pipeline.__init__ → Extractor.__init__
            → add_episode(entity_types=..., edge_types=...)
```

### Config 新增字段 (`graphiti_rag/config.py`)

```python
schema_path: str | None = None          # Schema 文件路径
schema_mode: str = 'strict'             # 模式: strict / lenient
edge_type_map: dict | None = None       # 边类型映射
```

### ingest_gbt.py 简化

```
上一版:  entities=ENTITY_TYPES硬编码字典
当前版:  config = load_config()  # 自动从 YAML 加载
```

---

## 三、调用链路变化

### 属性写入链路

```
add_episode()
  → extract_attributes_from_nodes()     # LLM 提取属性
    → node.attributes = {嵌套Map}        # LLM 返回的原始属性
  → EntityNode.save()
    → neo4j_safe_attributes(attrs)       # ★ 新增：类型安全转换
      → json.dumps(dict) → string       # 嵌套Map → JSON字符串
      → 过滤 None                       # 移除空值
    → Cypher execute_query()             # 写入 Neo4j
```

### Schema 注入链路

```
graphrag_config.yaml
  → config_loader.load_config()
    → schema_loader.load_graph_schema()
      → Pydantic create_model(docstring=description, fields=properties)
    → config.entity_types = {name: PydanticModel, ...}
  → GraphRAG(config)
    → Pipeline(config, graphiti)
      → Extractor(entity_types=config.entity_types, edge_types=config.edge_types)
        → add_episode(entity_types=self.entity_types, edge_types=self.edge_types)
          → _build_entity_types_context(entity_types)  # 注入 LLM prompt
            → type_model.__doc__ → prompt 中的分类描述
            → type_model.model_fields → 触发属性提取
```

---

## 四、文件清单

| 文件 | 状态 | 行数 | 说明 |
|------|------|------|------|
| `graphiti_core/helpers.py` +33 | 新增函数 | 156 | `neo4j_safe_value` + `neo4j_safe_attributes` |
| `graphiti_core/nodes.py` | 修改 | 570 | save() 调用 neo4j_safe_attributes |
| `graphiti_core/utils/bulk_utils.py` | 修改 | 186,220 | 批量写入调用 neo4j_safe_attributes |
| `graphiti_rag/schema_loader.py` | **新增** | 225 | YAML→Pydantic 构建器 |
| `schemas/gbt25338.yaml` | **新增** | 205 | 领域 Schema |
| `tests/test_schema_loader.py` | **新增** | 57 | 测试 |
| `graphiti_rag/config.py` | 修改 | +3 | `schema_path`/`schema_mode`/`edge_type_map` |
| `graphiti_rag/config_loader.py` | 修改 | +20 | Schema 自动加载逻辑 |
| `graphiti_rag/pipeline.py` | 修改 | +1 | Extractor 传 edge_type_map |
| `graphiti_rag/components.py` | 修改 | +1 | Extractor 接受 edge_type_map |
| `ingest_gbt.py` | 精简 | -150 | 移除硬编码 ENTITY_TYPES |

---

## 五、效果对比

| 指标 | 上一版（硬编码） | 当前版（YAML Schema + 安全写入） |
|------|---------|------|
| 实体/关系定义 | `ingest_gbt.py` 内硬编码 | `schemas/gbt25338.yaml` 外部配置 |
| Neo4j 写入 | 嵌套 Map 直接写入 → 全部失败 | 类型安全转换 → 12/12 成功 |
| Schema 配置入口 | `ingest_gbt.py` 代码 | `graphrag_config.yaml` + 环境变量 |
| 关系类型映射 | 无 | `edge_type_map`：头尾实体类型→允许的边 |
| 属性提取 | LLM 返回嵌套对象→Neo4j拒绝 | LLM 返回嵌套对象→JSON 序列化→Neo4j接受 |
