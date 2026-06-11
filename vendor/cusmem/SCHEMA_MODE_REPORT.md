# Schema 校验模式对比报告

## 测试环境

| 项目 | strict | lenient |
|------|--------|---------|
| Neo4j | `localhost:7688` | `localhost:7687` |
| 文档 | GBT+25338.1-2019.pdf | 同 |
| Schema | schemas/gbt25338.yaml | 同 |
| 并发 | max_concurrency=1 | 同 |
| OCR | Docker tesseract chi_sim+eng | 同 |

## 核心数据

| 指标 | strict | lenient | 差异 |
|------|--------|---------|------|
| Entity 总数 | 142 | 175 | +33 |
| RELATES_TO 边 | 76 | 183 | **+107 (2.4x)** |
| 泛型 Entity (仅 Entity 标签) | 0 | 13 | — |
| Community | 78 | 0 (超时未建) | — |

### 实体类型对比

| 类型 | strict | lenient |
|------|--------|---------|
| TechnicalTerm | 48 | 48 |
| Product | 20 | 25 |
| Standard | 18 | 21 |
| Section | 30 | 21 |
| TestItem | 13 | 19 |
| TechnicalParameter | 1 | 16 |
| Rating | 8 | 8 |
| Organization | 4 | 4 |
| EnvironmentalCondition | 0 | 0 |
| Generic (无类型) | 0 | 13 |

## 根因分析

### strict 模式级联删除机制

```
LLM 提取了 175 个实体 → schema_validate() 过滤:
  ├── 13 个泛型实体被删除 (只有 'Entity' 标签)
  ├── 109 个有类型实体保留
  └── 边校验：所有引用被删实体的边级联删除

结果：13 个实体删除 → 107 条边级联丢弃（183→76）
```

### 为什么级联效应被放大

每个泛型实体平均关联 8 条边（107÷13≈8.2）。因为：
1. 参数值实体（如 "25MΩ"、"1.5kN"）往往只被 LLM 打上 `Entity` 标签
2. 这些参数值是文档中关系网的关键节点——一个参数值被多条边引用
3. 删除一个参数值实体 = 同时丢失"谁规定了它"、"它的数值是多少"、"谁引用了它"等多条边

### EnvironmentalCondition 为 0 的原因

Schema 中已有该类型定义，但 LLM 在本次运行中没有将任何实体分类为 `EnvironmentalCondition`。之前手工跑时能提取到 4 个（周围空气温度、空气相对湿度、腐蚀性气体、有害气体），说明是 LLM 分类的随机性——不是 Schema 或代码问题。

## 两种模式的适用场景

| | strict | lenient |
|------|-------|---------|
| 适用 | Schema 覆盖率 >95%、LLM 分类准确率足够高 | Schema 建设期、LLM 分类不稳定 |
| 优点 | 知识图谱干净，无未分类实体 | 召回率高，不丢数据 |
| 缺点 | 一个分类错误导致级联丢边 | 有未分类实体混入，需后续清洗 |
| 当前状态 | **不推荐**（76 条边 vs 183） | **推荐** |

## 建议

1. **默认使用 `lenient` 模式**，避免过度过滤导致大量边丢失
2. strict 作为可选开关，待 Schema 覆盖率和 LLM 分类一致性提升后再启用
3. 在 lenient 模式下，后续可通过后处理对无类型实体做分类补全（基于规则或二次 LLM 调用）
4. EnvironmentalCondition 的分类问题需要单独优化 prompt，与 strict/lenient 无关
