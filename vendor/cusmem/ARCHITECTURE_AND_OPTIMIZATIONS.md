# Graphiti 知识图谱提取管线：架构设计与优化总结

> **受众**：产品经理 / 技术负责人  
> **日期**：2026-06-08  
> **覆盖版本**：v1 → v11  
> **适用场景**：GB/T 25338.1-2019 铁路道岔转换设备标准文档（60 页中文 PDF）

---

## 一、产品要解决的问题

我们有一段 60 页的铁路信号标准文档（GB/T 25338.1-2019），需要把它变成**结构化知识图谱**，让 AI 能理解：

- 标准里有**哪些产品**（转辙机、电机、锁闭装置……）
- 产品有**哪些技术参数**（转换力、动作电流、绝缘电阻……）
- 参数对应**什么数值和条件**（转换力 ≥ 2.5kN，在 160V 电压下测量）
- 这些知识点之间的**关系**（标准 → 规定 → 产品 → 有属性 → 参数）

### 核心挑战

这本质上是让 LLM（DeepSeek）把非结构化 PDF 文本转化为结构化图数据（节点 + 边），但 LLM 天生会犯以下错误：

| LLM 会犯的错 | 表现 | 后果 |
|---|---|---|
| **过度提取** | 把纯数字 "100"、"2.5" 当成实体 | 垃圾节点污染图谱 |
| **提取不足** | 提取了实体但没有提取边 | 孤立节点，信息丢失 |
| **命名不一致** | 同一个 "转辙机"，有时写 "转换锁闭器"、OCR 变体 "转儿机" | 同一实体分裂成多个节点 |
| **幻觉实体名** | LLM 凭空编造一个实体名用于边连接 | 边的端点找不到对应实体，边被丢弃 |
| **OCR 噪声** | 中文 PDF → 文本提取产生乱码："振峰,上且两个共振" | 乱码被当成实体提取 |

我们的管线需要**在所有环节上挡住这些问题**，而不能指望 LLM 一次就做对。

---

## 二、整体流程架构

```
                        ┌─────────────────────────────┐
                        │      GraphRAG.ingest()       │
                        │      (顶层入口)                │
                        └─────────────┬───────────────┘
                                      │
                        ┌─────────────▼───────────────┐
                        │   Pipeline (5 阶段编排)       │
                        │                              │
                        │  Phase 1: Scan 扫描文件       │
                        │  Phase 2: Read 读取文档       │
                        │  Phase 3: Split 文本切块      │
                        │  Phase 4: Extract 逐块提取    │  ← 核心阶段
                        │  Phase 5: Community 社区发现  │  ← optional
                        └─────────────┬───────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                     Phase 4 内部                          │
        │                                                          │
        │  ┌──────────┐   ┌──────────┐   ┌──────────┐             │
        │  │ Chunk 0  │   │ Chunk 1  │   │ Chunk N  │   ...       │
        │  └────┬─────┘   └────┬─────┘   └────┬─────┘             │
        │       │              │              │                    │
        │       ▼              ▼              ▼                    │
        │  ┌──────────────────────────────────────┐                │
        │  │     Graphiti.add_episode(文本块)      │  ← 每条 chunk 独立调用
        │  │                                      │                │
        │  │  ① extract_nodes    (实体提取)       │                │
        │  │  ② resolve_nodes    (实体去重)       │                │
        │  │  ③ extract_edges    (边提取)         │                │
        │  │  ④ resolve_edges    (边去重/摘要)    │                │
        │  │  ⑤ extract_attributes_from_nodes      │                │
        │  │  ⑥ validate_schema  (Schema 硬校验)  │                │
        │  │  ⑦ process/save     (写入 Neo4j)     │                │
        │  └──────────────────────────────────────┘                │
        └──────────────────────────────────────────────────────────┘
                                      │
                        ┌─────────────▼───────────────┐
                        │    Post-Ingestion 后处理     │
                        │                              │
                        │  cleanup_zero_degree_noise() │
                        │  手动/外部调用；当前 Pipeline.run 不自动执行 │
                        └──────────────────────────────┘
```

### Phase 2 细化：PDF 读取与 OCR 兜底

当前代码中的 PDF 读取逻辑在 `graphiti_rag/components.py` 的 `Reader._read_pdf()` 中实现。它不是按页 OCR，也不是默认使用 PaddleOCR。

当前确定流程是：

```text
PDF 原文件
  │
  ├── 1. 文本层抽取
  │      Reader._extract_pdf_text()
  │      优先 pdfminer.high_level.extract_text
  │      失败后 fallback 到 pdfplumber
  │      再失败 fallback 到 PyPDF2
  │
  ├── 2. 表格抽取
  │      Reader._extract_tables()
  │      使用 pdfplumber.extract_tables()
  │      表格会转换为 Markdown table 并追加到正文文本后
  │
  ├── 3. 全文质量判断
  │      Reader._needs_ocr(text)
  │      判断条件：文本为空/过短、包含 (cid:xxx)、中文可读字符比例低
  │
  ├── 4. OCR 兜底
  │      如果 _needs_ocr(text)=true，调用 Reader._ocr_pdf()
  │      _ocr_pdf() 使用 pypdfium2 渲染整本 PDF 页面
  │      再通过 Docker 镜像 tesseractshadow/tesseract4re:latest 跑 Tesseract OCR
  │
  └── 5. OCR 替换逻辑
         如果 OCR 文本存在，且长度大于原文本的 50%，用 OCR 文本替换原文本
         否则保留原文本
```

当前代码没有实现以下能力：

```text
没有 page_quality.jsonl
没有按页 needs_ocr 标记
没有按页选择性 OCR
没有 PaddleOCR
没有文本层/OCR 的按页 provenance 合并
没有 OCR 后实体对齐候选表或疑似错误实体清单自动产物
```

**建议修改为**：
后续可以把当前“全文质量判断 + 整本 OCR”改成“按页质量评分 + 问题页选择性 OCR”。建议每页记录 `char_count`、`cjk_ratio`、`garbled_ratio`、`cid_count`、`image_count`、`table_count`，只对 `needs_ocr=true` 的页面执行 OCR。中文扫描件和复杂表格页建议使用 PaddleOCR；文本层合格的页面保留原文本，OCR 只替换质量差页面，并保留 `source=text_layer|ocr|text+ocr` provenance。

### 关键设计决策

**为什么是 chunk 级处理而不是全文处理？**  
60 页文档一次性丢给 LLM 会让输出质量下降，也不利于定位错误。当前 `graphrag_config.yaml` 的 GB/T 实验配置把文档切成重叠 chunk：`chunk_size=1200`、`chunk_overlap=100`。注意：这是当前配置值；`Config` 类默认值是 `chunk_size=1000`、`chunk_overlap=200`。

**为什么有两阶段提取（second-pass）？**  
第一轮抽取像“先让模型把它看到的东西都捞出来”。问题是，模型可能会漏掉实体、漏掉关系，也可能把关系端点写成一个差一点的名字。比如实体列表里是 `周围空气温度`，关系里却写成 `周围空气温度条件`，这条边就会因为端点对不上被代码丢掉。

二抽不是简单地“再问一遍模型”。当前代码会先用规则把第一轮结果检查一遍，把能看懂的问题整理出来，再交给模型复查。模型第二次看到的不是空白题，而是：当前 chunk 原文、第一轮结果、哪些实体/边被系统拒绝、拒绝原因、有没有候选修正名字。它需要返回**修正后的完整列表**，不是只返回新增项。

当前二抽仍然只发生在**单个 chunk 内**。它不会主动跨 chunk 找关系，也不会扫描最终全图里哪些实体没边。

**建议修改为**：
后续如果要解决跨 chunk 的合法实体缺边问题，应在全量入库后做一次全局扫描：找出最终零度实体，回查它们出现过的 source chunk，再针对这些 chunk 做专项补边。这样二抽看到的是“最终图谱里确实没边的实体”，不是 chunk 里的临时状态。

### 二抽在代码里到底怎么跑

当前有两种二抽：**实体二抽**和**边二抽**。它们都受 `second_pass_extraction=true` 控制，默认模式是 `conditional`，也就是“有问题再二抽”。

**实体二抽：先看实体有没有明显问题**

1. LLM 第一轮从当前 chunk 抽实体。
2. `_validate_extracted_entities()` 先做规则检查：实体类型 ID 是否存在、实体类型是否被排除、名字是否为空、是不是该被过滤。
3. 检查结果会分成两类：
   - `valid_entities`：能继续往后走的实体。
   - `rejected_entities`：被规则拒绝的实体，并记录原因和 `fixable`。
4. 只要出现可修复的拒绝实体，或者有效实体数量低于 `second_pass_min_entities`，就触发实体二抽。当前默认 `second_pass_min_entities=2`。
5. `refine_extracted_entities()` 会把当前文本、实体类型定义、第一轮实体、被拒绝实体一起发给 LLM。Prompt 明确要求：`fixable=false` 的实体不要恢复；`fixable=true` 的实体只能在当前文本有依据时修正名称或类型后保留。
6. 二抽返回后，代码还会再跑一遍 `_validate_extracted_entities()`。没通过的还是会被丢掉。
7. 如果二抽调用失败，代码不会中断整个流程，会回退使用第一轮有效实体。

一个直观例子：模型把 `动作电流` 抽出来，但类型 ID 写错了。规则发现这个类型 ID 不存在，会把它记成可修复问题。二抽时模型可以把它改回 schema 里真实存在的 `TechnicalParameter`。

**边二抽：先验边，再决定要不要补救**

1. 边抽取发生在实体抽取之后，LLM 只能用当前实体列表里的名字做关系端点。
2. LLM 第一轮输出边后，`_validate_extracted_edges()` 立刻检查 source/target 是否能在实体列表中找到。这里会用实体名索引、`official_name`、`synonyms` 和 fuzzy match 辅助判断；当前代码已经删除硬编码 OCR 替换，不会再把未观察到的错字自动改成另一个实体名。
3. 检查通过的进入 `valid_edges`；检查失败的进入 `rejected_edges`。拒绝记录里会带上原因、是否可修复、候选 source/target。
4. 边二抽的触发条件有两条：
   - 有 `fixable_rejected_edges`：比如端点名字差一点，但代码找到了候选实体。
   - 验证后仍有实体在当前 chunk 内没有任何边，并且 `should_refine_edges()` 判断需要补救。默认 `second_pass_min_edges=1`；如果有效边少于这个值，也会触发。
5. `refine_extracted_edges()` 会把当前文本、实体列表、关系类型定义、第一轮有效边、无关系实体、可修复拒绝边一起发给 LLM。Prompt 里写得很明确：关系端点必须逐字匹配实体列表里的 `name`；如果没有文本依据，就删除，不要硬补。
6. 二抽返回后，代码再跑一遍 `_validate_extracted_edges()`。所以二抽不能绕过校验；端点对不上、fact 为空、没有合法端点的边仍然进不了后续流程。
7. 如果边二抽失败，代码同样回退到第一轮有效边。

一个直观例子：第一轮边写成 `5.5.7 -> 周围空气温度条件`，但实体里没有 `周围空气温度条件`，只有 `周围空气温度`。如果 fuzzy match 找到候选，拒绝账本会把这条边标成可修复。二抽时模型会看到候选端点，并被要求只在原文支持时改成实体列表里的准确名字。

**二抽最关键的点**

- 它不是“放宽规则”，而是“把规则发现的问题交给模型复查”。
- 它返回完整列表，不是补丁；所以二抽也可以删除第一轮里不靠谱的结果。
- 它只看当前 chunk，不看最终全局图。最终图里的零度实体，还需要后处理或全局补边解决。
- 它不会信任模型的第二次输出；第二次输出仍然要过同一套代码校验。

---

## 三、四层质量防线（核心设计）

这是我们在 v7-v11 期间逐步建立和完善的质量控制体系。

```
┌──────────────────────────────────────────────────────┐
│                    第 1 层：实体提取 Prompt            │
│  控制：什么东西该被提取为实体                           │
│  问题：裸数字、裸单位、OCR 残片被当成实体提取           │
│  手段：收紧规则、添加反例、移除坏示例                    │
│  防线类型：源头预防                                    │
├──────────────────────────────────────────────────────┤
│                    第 2 层：边提取 + Refinement        │
│  控制：实体之间是否产生了足够的边                       │
│  问题：Product 缺边、Section 孤岛、幻觉实体名           │
│  手段：连通性约束(rule 7)、二阶段边提取、拒绝账本        │
│  防线类型：源头预防 + 修正                             │
├──────────────────────────────────────────────────────┤
│                    第 3 层：去重合并                    │
│  控制：同名实体是合并还是分裂成多个                      │
│  问题：OCR 变体 "转儿机"/"转辙机" 分裂、同名异类型重复   │
│  手段：规范名/同义词实体对齐、跨类型合并、语义搜索去重   │
│  防线类型：后修正                                      │
├──────────────────────────────────────────────────────┤
│                    第 4 层：噪声过滤 (Cleanup)          │
│  控制：已入库的零度实体中哪些该删除                      │
│  问题：深层章节号、编目元数据、孤立参数值、OCR 残片       │
│  手段：分类规则 + 显式调用删除                          │
│  防线类型：兜底清理                                    │
└──────────────────────────────────────────────────────┘
```

### 几个容易误解但很关键的点

**1. OCR 错字现在不靠预设替换表解决。**  
本轮代码已经删除硬编码 OCR 替换表：实体创建时不再把文本识别错字直接改成另一个实体名；去重 key 也不再先跑固定替换；边端点校验也不会自动加入未观察到的 OCR 变体。

当前做法是 KAG 风格的**实体对齐**：抽取时保留原文 `name`，同时让模型给出 `official_name` 和 `synonyms`；去重和端点校验时，代码会用 `name / official_name / synonyms` 建索引，再结合 schema 类型、fuzzy match 和二抽拒绝账本判断是否应对齐。这样系统不是提前猜“哪个字会错”，而是在抽取后判断“这些名字是不是同一个实体”。

**建议修改为**：
后续可以继续补实体对齐候选生成：统计高频 entity-not-found、fuzzy near miss、零度 OCR 噪声，把它们整理成候选表：`原文名称`、`候选规范名`、`类型`、`相似度`、`出现 chunk`、`证据句`。确认后写入对齐结果或重新抽取，不把它设计成预设替换规则。

**2. 被丢弃的实体和边，不是直接静默丢掉。**  
当前实体校验会把被拒绝的实体放进 `rejected_entities`，实体二抽时会把这些记录给 LLM 看。当前边校验会把被拒绝的边放进 `rejected_edges`，边二抽时主要把 `fixable=true` 的拒绝边给 LLM 看。也就是说，代码不是“丢了就算了”，而是会把可修复的问题变成二抽输入。

需要注意：`fixable=false` 的内容不会被鼓励恢复。比如空名称、纯幻觉实体、没有文本依据的边，prompt 明确要求不要恢复。

**3. strict mode 不是让模型一定听话。**  
模型输出前，strict mode 管不到它。strict mode 真正发挥作用的位置是在 LLM 输出后、入库前：不在 schema 里的实体类型、边类型、不符合 `edge_type_map` 的边，会被代码过滤。它更像“入库门禁”，不是“生成约束器”。

**4. cleanup 现在不是 pipeline 自动步骤。**  
`cleanup_zero_degree_noise()` 已经能分类和删除部分零度噪声，但当前 `Pipeline.run()` 不会自动调用它。现在要清理，需要外部显式调用，并且最好先用 `delete=False` 看 dry-run 结果。

**5. Prompt 和代码各管一段，不能混在一起理解。**  
Prompt 负责告诉 LLM“应该怎么抽”；代码负责检查“抽出来能不能用”。这套方案能变稳，主要不是因为 prompt 写得更长，而是因为每轮 LLM 输出后都有规则校验、拒绝账本、二抽回看、再次校验。

### 第 1 层详解：实体提取 Prompt

**问题发现（v8）**：当我们修复了 LLM 的 `response_format`（让 DeepSeek 正确输出 JSON）后，LLM 突然变得"太听话"——Prompt 里说"参数数值必须提取为实体"，它就真的把裸数字 `100`、`2.5`、`30` 全部提取为 TechnicalParameter。同时 OCR 噪声如 "振峰,上且两个共振"、"验时间" 也被当成 TechnicalTerm 保留。

**我们的修复（v9）**：
- 把 rule 2 改为：TechnicalParameter 必须是「数值 + 单位」对（如 `2.5kN`、`160V`），明确禁止裸数字和裸单位
- 示例中移除裸 `90%`，改为仅保留 `湿度不大于90%` 作为 EnvironmentalCondition
- 增加 OCR 碎片识别模式

### 第 2 层详解：边提取 + 拒绝账本（最重要的优化）

这是 v10-v11 期间最核心的工作。边提取的流程是：

```
LLM 第一轮提取边
    │
    ▼
_validate_extracted_edges()   ← 确定性验证（不是 LLM）
    │                           检查每条边的 source/target 是否能在已提取实体中
    │                           找到。（精确匹配：name / official_name / synonyms
    │                           索引查 name_to_node；fuzzy match：仅用于生成候选
    │                           和标记 fixable=true，不直接自动解析成有效边）
    │
    ├── 验证通过 ──────────→ valid_edges（进入后续 resolve/schema validation/save 流程）
    │
    └── 验证失败 ──────────→ rejected_edges（进入"拒绝账本"）
                                │
                                ├── fixable=true：找到了近似匹配的候选实体
                                │    例：LLM 写了 "5.5.7 → 周围空气温度条件"
                                │    但实际实体叫 "周围空气温度"（多了一个"条件"）
                                │    候选：source="5.7"（fuzzy match 近似）
                                │
                                └── fixable=false：完全找不到匹配（ghost）
                                     例：LLM 写了 "直流电动机 → 技术标准"
                                     "技术标准" 不是任何已提取实体的名字
                                │
                                ▼
                    should_refine_after_validation？
                    在边端点校验之后判断是否触发二阶段
                        │
                        ├── fixable_rejected_edges > 0  ──→ 触发！  ← v10 新增
                        │    有可修复的端点偏差，直接让 LLM 基于候选端点修正
                        │
                        ├── validated_disconnected > 0  ──→ 继续调用 should_refine_edges()
                        │    should_refine_edges 内部再判断：
                        │      - second_pass_mode == always：触发
                        │      - len(valid_edges) < min_edges：触发
                        │      - 节点数 >= 4 且仍有零边实体：触发
                        │
                        └── 否则不触发，避免每个 chunk 都额外调用 LLM
                                │
                                ▼
                    refine_extracted_edges()   ← LLM 二阶段
                        输入：原始文本 + 验证后有效边 + 可修复拒绝账本 + 当前无关系实体
                        LLM 看到：以下关系被规则拒绝，请只在文本有依据时修正端点后保留
                        输出：修正后的边列表
                                │
                                ▼
                    _validate_extracted_edges() 再校验一次
                        二抽结果仍必须通过确定性端点校验后，才进入后续 resolve/schema validation/save
```

**为什么 v8/v9 的 edge refinement 从不触发（0 次）？**

原来的触发逻辑过度依赖 chunk 内部的 `disconnected_entities`。这个信号有两个局限：

1. 它只能看到当前 chunk 的局部状态，看不到入库、去重、跨 chunk 合并后的最终全局零度实体。
2. 它没有利用端点校验阶段已经发现的 `entity-not-found` / fuzzy near miss 信息。也就是说，即使 LLM 输出了一条“方向上可能正确但端点名称略错”的边，旧逻辑也不会把这条失败边当成二抽触发信号。

跨 chunk 去重和合并确实会影响最终零度率，但不能把它说成唯一根因。更准确地说：旧触发条件缺少“验证后失败边”这个控制信号，因此看不到很多可修复错误。

**v10 的修复**：我们在触发条件中加入了 `fixable_rejected_edges > 0`——只要 LLM 引用了近似匹配但略有偏差的实体名（fuzzy match），就触发二阶段修正。这个改动让 edge refinement 从 0 次变成了 1 次触发。

### 第 3 层详解：去重合并

当多个 chunk 提取了相似但不完全同名的实体时，系统尝试合并：

1. **规范名匹配**：优先使用 `name`、`official_name`、`synonyms` 建索引；同一实体的原文名、规范名、同义词都能命中同一个节点
2. **语义匹配**：用 embedding 计算实体名之间的余弦相似度
3. **LLM 去重**：不确定时调用 LLM 判断是否同一实体
4. **跨类型合并**：同名但被 LLM 赋予了不同类型时，按优先级表选 canonical 类型（如 `动作电流` 同时被标记为 TechnicalTerm 和 TechnicalParameter → 选 TechnicalParameter）

### 第 4 层详解：零度实体清理（Cleanup）

入库后，有些实体没有任何边连接（零度实体）。我们把它们分成五类：

| 分类 | 含义 | 举例 | 处理 |
|------|------|------|------|
| `deep_section` | 深度 ≥3 级的章节号 | `5.5.10`, `7.1.3`, `A.1.2` | **删除** |
| `catalog_metadata` | 编目元数据 | `ICS 45.020`, `2019-05-10` | **删除** |
| `isolated_parameter_value` | 纯数值参数 | `100`, `2.5kN`, `170 ± 5` | **删除**（如带单位） |
| `unit_only_parameter` | 裸单位 | `Hz`, `mm`, `kV` | **删除** |
| `ocr_fragment` | OCR 乱码碎片 | `振峰,上且两个共振`, `验,时间` | **删除**（含中文 + 标点异常） |
| *(以上皆非)* | 合法实体但没边 | `减速器`, `共振频率` | **保留** |

`cleanup_zero_degree_noise(driver, delete=False)` 支持 dry-run，只做分类报告不删除；函数默认 `delete=True`，会删除匹配噪声。当前 Pipeline 不自动调用 cleanup，需要外部显式调用。

**建议修改为**：
把 cleanup 接入 pipeline 的可配置后处理步骤，例如 `auto_cleanup_zero_degree=true` 时在全量 ingest 后自动先 dry-run 生成报告，再根据配置决定是否 delete。

---

## 四、我们做了哪些优化（按时间线）

### v7 及之前：建立基线

- 配置 Schema（gbt25338.yaml）：10 种实体类型 + 15 种边类型
- Schema strict mode：在 `validate_against_schema()` 中做入库前硬校验；它会过滤未知实体类型、未知边类型，以及不符合 `edge_type_map` 的边。它不会约束 LLM 的首轮生成过程，而是在 LLM 输出后做确定性过滤。
- Post-validation 架构：在边提取后、入库前进行确定性验证
- **基线指标**：229 实体，45 零度（19.7%）

### v8：修复 response_format + 增加连通性规则

- 修复了 LLM `response_format` 为 `json_object`（之前未正确设置导致输出不稳定）
- 在边 prompt 中增加 rule 7：要求 Product/TechnicalTerm/TestItem/TechnicalParameter 至少有一条边
- 跨类型去重合并（修复 "动作电流" 同时作为 TechnicalTerm 和 TechnicalParameter 被创建两个节点）
- **v8 结果**：333 实体（↑45%，LLM 过度提取），204 边，73 零度（21.9%）
- **发现问题**：LLM 提取了大量裸数字和 OCR 噪声；edge refinement 0 次触发

### v9：收紧实体 Prompt + 扩展 Cleanup

- 收紧 rule 2：禁止裸数字、裸单位
- 移除 Prompt 中的坏示例
- 扩展 cleanup 模式：增加 OCR 碎片、编目元数据、单位裸名识别
- **v9 未单独跑**，与 v10 合并验证

### v10：拒绝账本系统（关键突破）

这是我们投入最大的优化：

**代码改动**：
1. **`edge_operations.py`**：在 `_validate_extracted_edges` 中增加拒绝账本追踪
   - 对每条被拒绝的边记录 `reason`、`fixable`、`candidate_source`/`candidate_target`
   - `fixable=true` 的判断标准：`_candidate_name_for_miss()` 通过 fuzzy match 找到了近似实体名
   - `_classify_entity_miss()` 将 miss 分为两类：`fuzzy_near_miss`（fuzzy match 找到了近似候选实体名）、`ghost`（完全找不到匹配）

2. **`node_operations.py`**：在 `_validate_extracted_entities` 中增加拒绝账本追踪
   - 对每种拒绝原因记录 `fixable` 状态（如 `invalid_entity_type_id` 是 fixable 的——LLM 可以修正类型 ID）

3. **`extraction_refinement.py`**：扩展 edge refinement 触发条件
   ```python
   # 修改前
   should_refine_edges = disconnected_entities > 0 and ...

   # 修改后
   should_refine = fixable_rejected_edges > 0 OR (disconnected_entities > 0 AND ...)
   ```

4. **Refinement Prompt**：在二阶段的 LLM prompt 中注入"系统拒绝的关系"区块，逐条列出被拒绝的边及其候选目标，让 LLM 判断是否可恢复。

5. **`zero_degree_cleanup.py`**：新增 `cleanup_zero_degree_noise` 函数，实现分类驱动的噪声清理。

**v10 验证结果**：
- 228 实体（↓32% vs v8），218 边，33 零度（14.5%）
- **Edge refinement 首次触发**（1 次 fixable edge：`5.5.7 → 周围空气温度条件`，候选目标 `5.7`）
- 边/实体比从 0.61 升到 0.96
- 7/7 rejection ledger 单元测试通过

### v11：修复 Cleanup Async API

**问题**：`cleanup_zero_degree_noise` 在 Neo4j 5.26+ async driver 下崩溃
- 旧代码使用了同步的 `driver.session()` API，但 async driver 不支持
- 旧代码尝试 `driver.execute_cypher()`，但 Neo4j 5.26+ 的 API 是 `execute_query(query, params={})`

**修复**：
```python
# 修改前
result = await driver.execute_cypher(ZD_QUERY)  # 不存在的方法

# 修复后
result = await driver.execute_query(ZD_QUERY)      # 正确 API
records = [dict(r) for r in result.records]         # 正确取值路径

# Delete 同样修复
await driver.execute_query(DELETE_QUERY, params={'uuid': uuid})  # params 是关键字参数
```

**v11 验证结果**：
- Cleanup 成功移除 8 个 deep_section（5.5.10, 5.7.1, 5.9.2, 7.1.3, 7.2.1, 7.2.2, 7.2.3, 7.2.5）
- 零度率从 13.1% → 9.8%（post-cleanup）
- 端到端管线正常运行（~20 分钟）

### 其他：测试基础设施修复

**`conftest.py`** 中有 `from tests.helpers_test import graph_driver, mock_embedder`——该模块不存在，导致所有 pytest 都无法运行。删除了这个死导入，恢复 41/41 测试通过。

---

## 五、当前数据指标

| 指标 | v7 (基线) | v8 | v10 | v11 (当前) | 趋势 |
|------|----------|-----|------|-----------|------|
| 实体总数 | 229 | 333 | 228 | **222** | ✅ 趋近健康区间 |
| 边总数 | — | 204 | 218 | **207** | ✅ 稳定 |
| 边/实体比 | — | 0.61 | 0.96 | **0.93** | ✅ 健康 (>0.8) |
| 零度率 (pre-cleanup) | 19.7% | 21.9% | 14.5% | **13.1%** | ✅ 持续下降 |
| 零度率 (post-cleanup) | — | — | crash | **9.8%** | ✅ Cleanup 生效 |
| Entity rejections | — | — | — | **0** | ✅ Prompt 有效 |
| Edge rejections | — | — | — | **17** (1 fixable) | — |
| Edge refinement 触发 | — | 0 | 1 | 1 | ✅ 首次突破 |
| Cleanup 移除数 | — | — | crash | 8 | ✅ API 修复 |

### 零度实体构成（post-cleanup，21 个）

```
21 个零度实体
├── 10 个 Product（48%）
│     └── 减速器、外锁闭装置、开闭器、换向器直流电动机、
│         油泵、液压溢流闪、直流电动机、锁闭杆、锁闭表示杆、驼峰用快速转辙机
│     → 合法实体但当前图中没有任何边
│
├── 8 个 TechnicalTerm（38%）
│     └── 共振峰值、共振频率、加速度幅值、动作电流、
│         振动频率范围、耐振性能、转换力、验时间
│     → 其中 "验时间" 是 OCR 碎片，当前 cleanup 未捕获
│     → 其余 7 个是合法术语，但当前图中没有任何边
│
└── 3 个 Section（14%）
      └── 5.7、5.9、表5
      → Section 孤岛，深度 ≤2 不触发 deep_section 规则
```

**建议修改为**：
Product 和 TechnicalTerm 的零度问题应优先从边提取 prompt 处理：对 Product 明确要求连接到 `HAS_COMPONENT`、`HAS_ATTRIBUTE`、`APPLIES_TO` 等关系；对 TechnicalTerm 明确要求连接到 `DEFINES`、`HAS_ATTRIBUTE`、`MEASURED_BY` 等关系。`验时间` 这类短碎片走 cleanup 规则；`液压溢流闪` 这类疑似 OCR 错字应进入实体对齐候选，由规范名、证据句和相似度判断是否对齐到 `液压溢流阀`。

---

## 六、当前架构的关键设计原则

### 1. 确定性验证优先于 LLM 修正

LLM 很贵（API 调用按 token 计费），而且 LLM 修正 LLM 的错误可能会引入新错误。我们的策略是：

- **能代码判断的永远不用 LLM**：实体名匹配、规范名/同义词索引、结构名称校验全部是确定性规则
- **LLM 仅在必要时介入**：fuzzy match 不确定时才调用 LLM 去重（去重阶段），拒绝账本标记为 fixable 的二阶段修正（边修正阶段）
- **好处**：快、便宜、可预测

### 2. 拒绝账本不是日志——它是控制信号

关键设计决策：拒绝账本不只是事后分析的数据，它是运行时控制信号：

```
拒绝账本（Rejection Ledger）
  ├── 运行时作用 ①：触发 edge refinement（fixable > 0 → 二阶段修正）
  ├── 运行时作用 ②：为 refinement prompt 提供上下文
  │     "系统拒绝了以下关系，请判断是否能恢复"
  └── 事后作用 ③：质量分析
        "为什么这 16 条边被拒绝？是否可以通过改进 prompt 预防？"
```

### 3. 零度清理只在最外层做

Cleanup 不在提取循环内部做，而是所有 chunk 处理完毕后统一清理。原因：

- chunk 处理期间某个实体可能是零度的，但后续 chunk 可能为它添加边
- 只在图完全建好后判断"最终零度"

### 4. 三层 API 兼容

`cleanup_zero_degree_noise` 的数据库 API 调用有三层 fallback：
```python
execute_query（Neo4j 5.26+ async GraphDriver）→ execute_cypher（legacy GraphDriver）→ session()（sync driver fallback）
```
这是 best-effort 兼容：优先适配当前 async GraphDriver，同时保留 legacy GraphDriver 和同步 driver 的兜底路径；不是承诺所有 Neo4j driver 形态都无差异支持。

---

## 七、本轮代码更新：从 OCR 替换表改成实体对齐

这轮改动的核心是：**删除硬编码 OCR 替换逻辑，改成 KAG 风格的实体对齐**。

### 为什么要删 OCR 替换表

OCR 错字是事后才知道的，无法提前列全。比如这轮看到一个错字，下一轮可能出现另一个完全没见过的错字。靠维护固定替换表，短期看起来能修几个例子，长期会变成一张不可控的补丁表，还可能把正常术语误改错。

所以现在不再做这种事：

```text
旧逻辑：看到文本识别错字 -> 直接替换成预设标准名
新逻辑：保留原文名；只有 official_name/synonyms 明确给出对齐信号时，才认为它可以对齐到规范实体
```

### 现在代码怎么做

1. **实体创建保留原文名**  
   `_create_entity_nodes()` 不再改写 `node.name`。LLM 抽到什么名字，节点主名就先保留什么名字。

2. **对齐信息放在 attributes 里**  
   如果 LLM 输出 `official_name`，并且它和原文名不同，就写入 `node.attributes.official_name`。如果 LLM 输出 `synonyms`，就写入 `node.attributes.synonyms`。

3. **去重用实体对齐字段**  
   `_dedup_key()` 不再跑 OCR 替换。去重时优先用 `name`、`official_name`、`synonyms` 建候选索引；只有这些字段明确命中，或者后续语义/LLM 去重确认，才合并。

4. **边端点校验用实体索引**  
   `_build_name_to_node_map()` 只把 `name`、`official_name`、`synonyms` 加进端点索引。没有出现在这些字段里的错字，不会被自动映射。

5. **二抽仍然能修可修复问题**  
   如果边端点没找到，但 fuzzy match 找到候选，拒绝账本会记录 `candidate_source` 或 `candidate_target`。二抽时 LLM 会看到这个候选，但必须有当前 chunk 的文本依据，才能改用候选端点。

### 一个例子

如果实体是：

```json
{"name": "转辙机", "synonyms": ["转牧机"]}
```

那么边里写 `转牧机 -> IP54` 可以解析到 `转辙机 -> IP54`。

但如果实体只有：

```json
{"name": "转辙机"}
```

边里写 `转牧机 -> IP54` 就不会被自动修成 `转辙机 -> IP54`。它会进入拒绝账本，等待二抽或后续实体对齐候选审核。

### 本轮代码文件

| 文件 | 改动 |
|------|------|
| `graphiti_core/utils/maintenance/entity_alignment.py` | 新增通用实体对齐 helper：`unique_preserve_order()` |
| `graphiti_core/utils/maintenance/ocr_normalization.py` | 删除硬编码 OCR 替换模块 |
| `graphiti_core/utils/maintenance/node_operations.py` | 实体创建不再改写 `name`，只保存模型给出的 `official_name/synonyms` |
| `graphiti_core/utils/maintenance/dedup_helpers.py` | 去重 key 不再使用 OCR 替换；对齐依赖 `name/official_name/synonyms` |
| `graphiti_core/utils/maintenance/edge_operations.py` | 边端点索引不再自动加入 OCR 变体；只用明确的实体对齐字段 |
| `graphiti_core/prompts/extract_nodes.py` | prompt 文案从 `OCR变体` 改成 `文本识别变体` |
| `tests/test_entity_alignment.py` | 新增实体创建和端点索引的实体对齐测试 |
| `tests/test_dedup_entity_alignment.py` | 新增去重不靠 OCR 替换、只靠对齐字段的测试 |
| `tests/test_rejection_ledger.py` | 更新旧测试：没有 `synonyms` 时不自动把 `转牧机` 修成 `转辙机` |

### 本轮验证

```text
ruff check: passed
pytest -m "not integration": 44 passed
```

---

## 八、已知问题与下一步方向

### 当前未解决的问题

| 问题 | 严重程度 | 根因 | 当前代码状态 |
|------|---------|------|--------------|
| **10 个 Product 零度** | 中 | 边提取阶段没有把所有 Product 都连到其他实体 | 当前只依赖 LLM 按 prompt 抽边；没有 Product 专项补边逻辑 |
| **8 个 TechnicalTerm 零度** | 中 | 边提取阶段没有把所有术语都连到定义、参数、测试项或产品 | 当前只依赖 LLM 按 prompt 抽边；没有 TechnicalTerm 专项补边逻辑 |
| **"验时间" OCR 碎片未拦截** | 低 | 当前 OCR fragment pattern 未覆盖这种短字符串 | 当前 cleanup 会保留它 |
| **"液压溢流闪" 疑似应对齐到 "液压溢流阀"** | 低 | PDF 文本提取/OCR 错误 | 当前不会用硬编码替换强行合并；需要实体对齐候选审核来判断是否应合并 |
| **Cleanup 没有在管线中自动执行** | 中 | 当前 `Pipeline.run()` 没有调用 `cleanup_zero_degree_noise()` | 当前需要外部显式调用 |

**建议修改为**：
1. **边 prompt 重构**：在 prompt 中增加“连通性检查表”。Product 至少要尝试连接到 `HAS_COMPONENT`、`HAS_ATTRIBUTE`、`APPLIES_TO`；TechnicalTerm 至少要尝试连接到 `DEFINES`、`HAS_ATTRIBUTE`、`MEASURED_BY`。这不是盲目造边，仍要求关系必须能在当前 chunk 原文中找到依据。

2. **全局零度专项补边**：在全量 ingest 后扫描最终零度实体，回查它们出现过的 source chunk，只对这些 chunk 做补边二抽。这样能处理“chunk 内看起来没问题，但全局最终仍然零度”的实体。

3. **拒绝账本驱动的 prompt 调整**：定期统计 `rejected_edges` 的 `reason`、`fixable`、`candidate_source`、`candidate_target`。如果某类 `ghost` 或 `fuzzy_near_miss` 高频出现，就把它们整理成 prompt 反例或实体对齐候选，而不是只看日志。

4. **Cleanup 与实体对齐分工**：`验时间` 这类短 OCR 残片加入 cleanup 规则；`液压溢流闪` 这类疑似专有名词错字不要直接写死替换，应进入实体对齐候选，结合 chunk 证据、schema 类型和相似度判断是否对齐到 `液压溢流阀`。cleanup 规则上线前先用 `cleanup_zero_degree_noise(driver, delete=False)` 做 dry-run。

5. **Pipeline 后处理开关**：新增配置项，例如 `auto_cleanup_zero_degree`。关闭时保持当前行为；开启时在全量 ingest 后自动执行 cleanup，最好先产出 dry-run 报告，再按配置删除。

6. **代码库精简**：如果产品形态确定只使用 Neo4j + OpenAI-compatible LLM + BGE/Ollama embedding，可以单独制定精简计划，删除未使用 driver、LLM provider、embedder provider 和搜索配方。但这需要独立评估 API 兼容性和测试覆盖，不能作为当前管线已完成优化来描述。

---

## 九、我们写了哪些文件

### 工具和分析脚本

| 文件 | 用途 |
|------|------|
| `v10_run.py` | v10 管线脚本（带 instrumentation 和拒绝账本追踪的 monkey-patch） |
| `v10_analyze.py` | v10 后处理分析（查询 Neo4j 中的实体/边/零度分布） |
| `v11_run.py` | v11 管线脚本（修复 cleanup API + 完整指标采集 + JSON 输出） |

### 文档

| 文件 | 用途 |
|------|------|
| `QUALITY_MODEL.md` | 四层防线质量模型 + 因果链 + 实验迭代记录 |
| `v10_logs/v10_REPORT.md` | v10 实验报告（228E/218R，零度 14.5%，edge refinement 首次触发） |
| `v11_logs/v11_REPORT.md` | v11 实验报告（cleanup 修复验证，零度 13.1%→9.8%） |
| 本文档 | 面向 PM 的架构设计与优化总结 |

### 代码修改

| 文件 | 改动 | 类型 |
|------|------|------|
| `graphiti_core/utils/maintenance/zero_degree_cleanup.py` | 修复 async API（`execute_cypher`→`execute_query`），新增 `cleanup_zero_degree_noise` 函数 | Bug 修复 + 新功能 |
| `graphiti_core/utils/maintenance/edge_operations.py` | 增加拒绝账本（`rejected_edges`、`fixable`、`candidate` 字段）；边端点索引改为 `name/official_name/synonyms` | 功能增强 |
| `graphiti_core/utils/maintenance/node_operations.py` | 增加实体拒绝账本；实体创建保留原文 `name`，只保存模型给出的 `official_name/synonyms` | 功能增强 |
| `graphiti_core/utils/maintenance/dedup_helpers.py` | 去重 key 删除硬编码 OCR 替换，改用实体对齐字段 | 功能增强 |
| `graphiti_core/utils/maintenance/entity_alignment.py` | 新增通用实体对齐 helper | 新功能 |
| `graphiti_core/utils/maintenance/ocr_normalization.py` | 删除硬编码 OCR 替换模块 | 删除旧逻辑 |
| `graphiti_core/utils/maintenance/extraction_refinement.py` | Edge refinement 触发条件扩展（`fixable_rejected_edges > 0`） | 功能增强 |
| `graphiti_core/prompts/extract_nodes.py` | 把 `OCR变体` 文案改为 `文本识别变体` | Prompt 调整 |
| `conftest.py` | 删除不存在的 `tests.helpers_test` 导入 | Bug 修复 |

---

*文档基于本地代码实现与 v1-v11 管线实验数据整理*
