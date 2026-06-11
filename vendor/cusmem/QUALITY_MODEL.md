# 知识图谱提取质量模型

基于 v1-v8 八轮 GB/T 25338.1-2019 管线实验的实证总结。
本文档描述提取质量的指标体系、影响因素和调优方向。

---

## 一、核心因果链

```
prompt 规则（entity）
  → 垃圾实体数 → 零度实体数
  → 实体总数 → LLM API 调用次数

prompt 规则（edge rule 7）
  → 每实体边数 → 零度实体数
  → IS_PART_OF 倾向 → 边类型分布

chunk_size + chunk_overlap
  → 上下文完整度 → entity-not-found 次数 → 边丢失率

second_pass_min_edges + _validate_extracted_edges
  → edge refinement 触发率 → 补边效果

dedup (_merge_type_key + ocr_normalization)
  → 同名重复实体数 → 实体总数 → 零度率
```

**一句话**：零度率是最关键的单一指标，它由四层叠加决定：

```
实体提取质量（prompt）→ 边提取覆盖（prompt + chunk）→ 去重合并（dedup）→ 噪声过滤（cleanup）
```

---

## 二、实体维度

### 指标

| 指标 | 含义 | 健康范围（60 页标准文档） |
|------|------|--------------------------|
| 实体总数 | 提取出的 EntityNode 数量 | 200-280 |
| 垃圾实体占比 | 纯数字/裸单位/OCR 残片等无意义实体 | < 10% |
| 实体类型分布 | 各类型（Section/Product/TechnicalTerm/...）的比例 | Section ≈ TechnicalParameter > TechnicalTerm > Product |

### 影响因素

| 因素 | 作用机制 | v8 实证 |
|------|----------|---------|
| **prompt rule 2（entity）** | "参数数值必须提取为实体" → LLM 把裸数字（100、2.5）当 TechnicalParameter | v8 因 `response_format` 修复，LLM 更听话，裸数字激增到 ~30 个 |
| **prompt 示例** | 示例中 "90%" 作为独立 TechnicalParameter → 教 LLM 拆分数值 | v9 已修复：示例中移除裸数值 |
| **schema 类型定义** | 类型越多，LLM 越倾向提实体；TechnicalParameter 定义含 value/unit 字段鼓励提取数值 | strict mode 下 LLM 强制按类型提取 |
| **schema_mode** | strict → LLM 严格分类，减少 Entity 泛型；lenient → 更多 Entity 泛型 |

### v8→v9 改动

- rule 2 改为：TechnicalParameter 必须是「数值+单位」对（如 2.5kN、160V）
- 明确禁止：裸数字（100、2.5、30）、裸单位（Hz、min）、无意义混合（1s、AQL）
- 示例中移除裸 "90%"，改为仅保留 "湿度不大于90%" 作为 EnvironmentalCondition

---

## 三、边维度

### 指标

| 指标 | 含义 | 健康范围 |
|------|------|----------|
| RELATES_TO 边数 | 实体间关系总数 | 180-250 |
| 每实体平均边数 | 边数 / 实体数 | > 0.8 |
| IS_PART_OF 占比 | IS_PART_OF 在所有边类型中的比例 | < 30% |
| edge refinement 触发次数 | 二阶段边提取被触发的 chunk 数 | > 0（至少几个 chunk 触发） |
| entity-not-found 次数 | 边端点无法解析为已知实体的次数 | < 20 |

### 影响因素

| 因素 | 作用机制 | v8 实证 |
|------|----------|---------|
| **prompt rule 7（edge 连通性）** | 要求 Product/TechnicalTerm/TestItem/TechnicalParameter 至少有一条边 | IS_PART_OF 63 条（31%），略高 |
| **chunk_size + overlap** | chunk 太小 → 上下文不足 → LLM 引用的实体不在当前 chunk 实体列表 → entity-not-found | v8 有 32 次 entity-not-found |
| **second_pass_min_edges** | 阈值太高 → edge refinement 从不触发 | v8 中 0 次触发 |
| **_validate_extracted_edges** | 过滤不可解析端点 + 自环边 → drop 掉的边数影响 disconnected 检测 | v8 drop 了部分边但 disconnected 检测在 chunk 级而非全局 |

### 为什么 edge refinement 触发 0 次（v8）及如何修复（v10）

`should_refine_edges` 在**每个 chunk 内**检测 disconnected 实体。v8 中每个 chunk 内大部分实体都有至少一条边（LLM 倾向于在同一 chunk 内连接实体）。真正的问题在于：

1. 跨 chunk 去重合并后，某些实体失去边（重复实体合并导致边归属变化）
2. chunk 级的 disconnected 检测看不到全局 picture

**v10 修复**: 在 `_validate_extracted_edges` 中增加了 `fixable_rejected_edges` 跟踪——当 LLM 引用的实体名与已有实体名 fuzzy match 时，标记为 fixable。Edge refinement 触发条件扩展为：

```
fixable_rejected_edges > 0  OR  disconnected_entities > 0
```

v10 实测: chunk 0 触发 1 次 edge refinement（1 fixable rejected edge: `GB/T 4208一2017 → 外壳防护等级(IP 代码)`, candidate_target="IP 代码"）

---

## 四、连通性（最重要的综合指标）

### 零度实体的来源分解（v10 实测：33 个 → cleanup 后约 19 个）

```
33 个零度实体
├── 14 个（42%）→ cleanup 可自动过滤的噪声
│     ├── deep_section (6): 5.5.10, 7.1.3, 7.2.1, 7.2.2, 7.2.3, 7.2.5
│     ├── ocr_fragment (6): +, A FEL, HE LAR Bh ff HY Ta] PSL EL, Ha eH, Poe ee HOL, 一40 人一十40
│     ├── isolated_parameter_value (1): 2b级
│     └── chart_pattern (1): 表 4
│
├── 11 个（33%）→ Section 孤岛
│     └── 5.1, 5.14, 5.15, 5.16, 5.17, 5.18, 7.2 + 少数 Standard 片段
│
└── 8 个（24%）→ 合法 TechnicalTerm 缺边
      └── 动作杆动程, 包装储运图示标志, 工作电流, 摩擦联接器, 短时工作制, 绝缘等级 等
```

### 零度实体的来源分解（v8 实测：73 个）

```
73 个零度实体
├── 26 个（36%）→ cleanup 可自动过滤的噪声
│     ├── isolated_parameter_value: 裸数字（100、103、2.5...）、"相当于 15" 等 OCR 残值
│     ├── unit_only_parameter: Hz、m/s²、min
│     ├── deep_section: 5.5.10、5.6.3（≥3 级深度）
│     └── ocr_fragment: 振峰,上且两个共振
│
├── ~25 个 → 合法但 LLM 未提取边的实体
│     ├── Product 类（10 个）: S700K-C、交流型转辙机、减速器、快速型、
│     │    开闭器、换向器直流电动机、液压溢流闪、电液转辙机、直流型转辙机、直流电动机
│     └── TechnicalTerm 类: 共振峰、共振振动频率、振动频率范围 等
│
└── ~22 个 → 合法但孤立的 Section/参数名/术语
      ├── Section: 5.1、5.4、5.6、5.17、分类、前言、技术要求 等
      ├── TechnicalParameter: 工作电流、接点压力、接触电阻、试验电压
      └── TechnicalTerm: 通用技术条件、道岔转换设备 等
```

### 零度率趋势

| 版本 | 实体数 | 零度数 | 零度率 | 主要变化 |
|------|--------|--------|--------|---------|
| v7 | 229 | 45 | 19.7% | post-validation 架构 baseline |
| v8 | 333 | 73 | 21.9% | response_format 修复后 LLM 过度提取实体 |
| v9 | — | — | — | 收紧 entity prompt + 扩展 cleanup（未单独跑） |
| v10 | 228 | 33 | 14.5% | rejection ledger + edge refinement 触发修复 |

---

## 五、去重维度

### 指标

| 指标 | 含义 | 健康值 |
|------|------|--------|
| 同名异类型重复数 | 同一个 name 被 LLM 赋予了不同 entity_type 创建的多个节点 | 0 |
| OCR alias 覆盖率 | ocr_normalization.py 能修正的 OCR 变体比例 | 尽可能高 |
| 确定性去重命中率 | 不需要 LLM 调用就能匹配的实体比例 | 越高越好→省 API 调用 |

### 影响因素

| 因素 | 作用机制 |
|------|----------|
| `_merge_type_key()` | 同名不同类时选 canonical 类型（如 动作电流 TechnicalTerm+TechnicalParameter → TechnicalParameter） |
| `_CROSS_TYPE_PRIORITY` | 优先级表决定 canonical 类型 |
| `_PARAMETER_NAME_PATTERNS` / `_TEST_NAME_PATTERNS` | 基于名称语义判断 canonical 类型 |
| `OCR_ALIAS_REPLACEMENTS` | 修正常见 OCR 错误（转儿机→转辙机、透明单→透明罩） |
| `_normalize_string_exact` | 小写 + OCR alias + 空白规范化，用于精确匹配 |

### v8 实证

同名异类型重复：0（v7 有 2 对：动作电流、振动耐久试验，已修复）

---

## 六、管线配置参数表

| 参数 | 作用 | 调大后果 | 调小后果 | 推荐值 |
|------|------|---------|---------|--------|
| `chunk_size` | 单次 LLM 调用文本量 | 超出 context 窗口 / 提取质量下降 | 上下文不足，实体缺边 | 1200 |
| `chunk_overlap` | 相邻 chunk 重叠字符数 | 冗余处理 | chunk 边界实体被截断 | 100 |
| `second_pass_mode` | always=每次二抽 / conditional=按条件 | always→浪费 API | conditional→可能不触发 | conditional |
| `second_pass_min_edges` | 边数低于此值触发 edge refinement | 每次都触发 | 从不触发（v8: 0 次） | 1 |
| `second_pass_min_entities` | 实体数低于此值触发 entity refinement | — | — | 2 |
| `schema_mode` | strict=严格按 schema 类型 / lenient=允许 Entity 泛型 | strict→分类更精确但可能强行分类 | lenient→Entity 泛型增多 | strict |
| `num_chains` | Read+Split 并行线程数 | 更多并行 | 更慢 | 2 |
| `max_concurrency` | Extract 消费者数 | 更多并发→更快但 CPU/API 压力大 | 更慢 | 1 |

---

## 七、四层质量防线

```
第 1 层：实体提取 prompt
  ├── 控制：什么东西被提取为实体
  ├── 典型问题：裸数字、裸单位、OCR 残片
  └── 修复手段：收紧 rule 2、添加负面示例、移除坏示例

第 2 层：边提取 prompt + chunk 策略
  ├── 控制：实体之间是否产生边
  ├── 典型问题：Product 缺边、section 孤岛、entity-not-found
  └── 修复手段：rule 7 连通性约束、edge refinement 二阶段、chunk overlap

第 3 层：去重合并
  ├── 控制：同名实体是合并还是分裂
  ├── 典型问题：同名异类型重复、OCR alias 未生效
  └── 修复手段：cross-type merge 规则、OCR alias 表

第 4 层：后处理噪声过滤（cleanup）
  ├── 控制：已入库的零度实体中哪些该删
  ├── 典型问题：深层章节、编目元数据、孤立参数值、OCR 残片
  └── 修复手段：分类规则 + 自动删除
```

**调优原则**：优先修第 1-2 层（源头预防），第 3-4 层是兜底。

---

## 八、实验迭代记录

| 版本 | 日期 | 实体 | 边 | 零度 | 主要改动 | 关键发现 |
|------|------|------|-----|------|---------|---------|
| v1-v3 | — | — | — | — | Schema + strict mode | 502 错误来自 embedding 服务中断 |
| v4 | — | — | — | — | OCR alias + 连通性规则 | 转辙机变体多 |
| v5 | — | — | — | — | 阈值调优 | threshold >= 1 |
| v6 | — | — | — | — | Post-validation 架构 | 检测点放错位置 |
| v7 | 06-07 | 229 | — | 45 | Post-validation 完成 + 零度分析 | baseline: 19.7% 零度率 |
| v8 | 06-07 | 333 | 204 | 73 | response_format fix + rule 7 + cross-type dedup | 实体激增→零度升到 21.9% |
| v9 | 06-07 | — | — | — | 收紧 entity prompt + 扩展 cleanup | 合并到 v10 一起验证 |
| v10 | 06-07 | 228 | 218 | 33→~19 | rejection ledger + edge refinement fixable 触发 | 零度率 14.5%, edge/entity=0.96, refinement 首次触发 |

---

*文档由 Claude Code 生成 | 数据来源：Neo4j v1-v8 管线实验*
