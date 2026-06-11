# Graphiti vs KAG — 知识图谱构建 Pipeline 对比

## 一、定位不同

| | Graphiti | KAG (OpenSPG) |
|---|---|---|
| **目标** | AI Agent 的对话记忆系统 | 企业知识图谱构建平台 |
| **输入** | 对话/消息流 (短文本) | 文档 (PDF, DOCX, MD, CSV, JSON...) |
| **输出** | 时序知识图谱 (实体+关系+时间) | SPG 语义属性图 (schema 对齐) |
| **Schema** | 灵活，不需要预定义 (默认只有 Entity) | **严格的 SPG Schema** (类型、属性、关系全部预定义) |
| **存储** | Neo4j | SPG Server (自研图数据库) |
| **规模** | 95 py 文件 / 2 万行 | 736 py 文件 / ~10 万行 |
| **来源** | Zep (美国) | 蚂蚁集团 OpenSPG (中国) |

## 二、Pipeline 对比

### Graphiti Pipeline

```
add_episode(text)  ← 单次对话
  │
  ├── retrieve_episodes()        取历史上下文
  ├── extract_nodes()            LLM提取实体 (schema-flexible)
  ├── resolve_extracted_nodes()  去重 (embedding + LLM)
  ├── extract_edges()            LLM提取关系
  ├── resolve_extracted_edges()  边去重 + 冲突检测
  ├── extract_attributes()       实体摘要
  └── save()                    写入 Neo4j
```

**特点**：
- 每次处理一条消息，增量更新
- 去重依赖 embedding 相似度 + LLM 判断
- 不需要预定义 schema
- 关注时序（双时序模型）

### KAG Pipeline

```
Document  ← PDF/Markdown/Word/CSV...
  │
  ├── Scanner              文件扫描 (CSVScanner, DirectoryScanner, ...)
  ├── Reader               文档解析 (DocxReader, PDFReader, MarkdownReader...)
  ├── Splitter             文本分块 (LengthSplitter, SemanticSplitter, OutlineSplitter)
  ├── Extractor ─────┬── ChunkExtractor       提取文档块
  │                  ├── OutlineExtractor      提取大纲结构
  │                  ├── SchemaFreeExtractor   无 schema 提取
  │                  ├── SchemaConstraintExtractor  有 schema 约束提取
  │                  ├── AtomicQueryExtractor  原子查询提取
  │                  ├── TableExtractor        表格提取
  │                  └── SummaryExtractor      摘要提取
  ├── Aligner ───────┬── SPGAligner           对齐到 SPG schema
  │                  └── KAGAligner           合并子图
  ├── Mapping ───────┬── SPGTypeMapping        类型映射
  │                  ├── SPOMapping            S-P-O 三元组映射
  │                  └── RelationMapping       关系映射
  └── Writer ────────┬── KGWritter             写入图数据库
                     └── MemoryGraphWriter     写入内存图
```

**特点**：
- 批量处理文档，一站式 pipeline
- **SPG Schema 必须预定义**（类型、属性、关系全部声明好）
- 多种 Extractor 组合使用
- Aligner 负责将非结构化提取结果对齐到预定义 schema

## 三、核心差异

### 3.1 Schema 策略

**Graphiti**：Schema-flexible
```python
# 不需要预先定义类型
await g.add_episode(episode_body="小明在腾讯工作")

# 可选：自定义类型（Pydantic）
class Person(BaseModel): role: str | None
class Company(BaseModel): industry: str | None
await g.add_episode(..., entity_types={'Person': Person, 'Company': Company})
```

**KAG**：Schema-required（SPG）
```yaml
# 必须预先定义 SPG Schema
types:
  - name: Person
    properties: [name, age, role]
  - name: Company
    properties: [name, industry, location]
relations:
  - name: worksAt
    subjectType: Person
    objectType: Company
```

```python
# 提取时必须遵守 schema
extractor = SchemaConstraintExtractor(types=spg_types, relations=spg_relations)
```

### 3.2 去重策略

| | Graphiti | KAG |
|---|---|---|
| **方式** | embedding 相似度 + LLM 判断 | Schema 对齐 + 实体链接 |
| **触发** | 每条 episode 摄入时 | 知识写入时 (Writer 阶段) |
| **粒度** | 实体级别 | 三元组级别 |
| **类型感知** | 刚加入 (之前不支持) | 原生支持 (Schema 定义好了) |

### 3.3 提取方式

**Graphiti**：单一 LLM 联合提取
```
一段对话 → 一个 LLM Prompt → {entities: [...], edges: [...]}
```

**KAG**：多 Extractor 流水线
```
文档 → ChunkExtractor → 分块
     → OutlineExtractor → 大纲
     → SchemaFreeExtractor → 自由实体
     → SchemaConstraintExtractor → 约束实体
     → TableExtractor → 表格
     → AtomicQueryExtractor → 原子查询
     → SummaryExtractor → 摘要
     ↓
  Aligner 合并 → Mapping 映射 → Writer 写入
```

### 3.4 存储模型

**Graphiti**：时序图
```cypher
(:Entity {name, name_embedding, summary, created_at})
(:Episodic {content, valid_at, created_at})
(:Entity)-[:RELATES_TO {fact, fact_embedding, valid_at, invalid_at}]->(:Entity)
```
核心是 `valid_at/invalid_at`（事实的生命周期），支持"过去某时他不在腾讯"这样的时序查询。

**KAG**：SPG (Semantic Property Graph)
```
:Person {name, age, role}
:Company {name, industry}
(:Person)-[:worksAt {startDate}]->(:Company)
```
核心是类型系统和属性约束，Schema 严格定义了每种类型的属性和允许的关系。

### 3.5 增量更新

| | Graphiti | KAG |
|---|---|---|
| **设计** | 原生支持增量 (每条消息一个 episode) | 批量构建为主 |
| **时序** | 双时序 (valid_at + created_at) | 无 |
| **冲突检测** | 有 (dedupe + contradict) | 无 (重新构建覆盖) |

### 3.6 搜索

| | Graphiti | KAG |
|---|---|---|
| **方法** | 混合搜索 (BM25 + 语义 + BFS + RRF) | SPG 查询 (DSL) |
| **配置** | 17 种配方 | Schema 预定义 |
| **时序过滤** | 支持 (按 valid_at 过滤) | 不支持 |

## 四、使用场景

| 场景 | 推荐 |
|------|------|
| AI Agent 对话记忆 | **Graphiti** (增量、时序、灵活) |
| 企业文档知识库 | **KAG** (Schema 定义、文档解析强) |
| 多轮对话时序分析 | **Graphiti** |
| 行业知识图谱 (金融/法律/医疗) | **KAG** (SPG Schema 强约束) |
| 快速原型 | **Graphiti** (零配置) |
| 复杂文档解析 | **KAG** (PDF/Word/Markdown/表格) |

## 五、架构对比图

```
Graphiti (对话驱动)              KAG (文档驱动)

一条消息 ──→ LLM ──→ 知识图谱      一份文档 ──→ 多Extractor ──→ SGPGraph
    ↓                              ↓
增量更新 + 去重                    批量构建 + Schema对齐
    ↓                              ↓
混合搜索 + 时序过滤                DSL查询 + 推理
```

## 六、总结

Graphiti 和 KAG 虽然都叫"知识图谱构建"，但面向完全不同的场景：

- **Graphiti** 是为 AI Agent 设计的实时记忆系统，强调增量、去重、时序、灵活性
- **KAG** 是企业级知识图谱平台，强调 Schema 规范、文档解析、类型系统

两者最大的设计哲学差异在于 **Schema 策略**：Graphiti 默认不需要 Schema（后置分类），KAG 强制要求 Schema（前置约束）。这决定了它们的复杂度——KAG 的代码量是 Graphiti 的 5 倍，大部分花在 Schema 管理和多格式文档处理上。
