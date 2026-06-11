# Graphiti GB/T 25338 知识图谱抽取链路更新总结

日期：2026-06-07  
对象：`GB/T 25338.1-2019` 标准文档抽取与 Graphiti 知识图谱构建流程  
读者定位：产品经理、业务负责人、技术方案评审人员

## 1. 这轮工作解决了什么问题

这一路优化的目标，不只是“让大模型多抽一点”，而是把标准文档抽取做成一条可解释、可诊断、可持续调优的知识图谱构建流程。

最初的问题可以概括成五类：

1. **抽取不稳定**：同一个文档不同轮次结果波动明显，部分 chunk 会因为字段格式错误失败。
2. **类型覆盖不完整**：`Person`、`Organization`、`EnvironmentalCondition` 等类型早期经常抽不到。
3. **OCR 噪声多**：PDF 识别后会把 `转辙机` 识别成 `转儿机`、`转攻机`、`转狼机` 等，导致同一个东西变成多个节点。
4. **关系看起来不对**：早期报告误把 Neo4j 的物理边 `RELATES_TO` 当成语义边，误判“语义关系类型全没抽出来”。后来确认真正语义类型在边属性 `r.name` 里。
5. **孤立节点多**：很多实体没有任何关系边，社区也因此出现大量单实体社区。

现在的流程已经具备以下能力：

- 按 schema 抽取实体和关系。
- 对抽取结果做条件二次审校。
- 对 OCR 错字做归一化。
- 对 LLM 返回的坏字段做防护。
- 对关系端点做校验和失败分类。
- 对社区做结构化摘要。
- 对 zero-degree entity 做归因分析，区分“该过滤的噪声”和“真正需要补边的高价值实体”。

## 2. 关键概念先说明

### 2.1 什么是实体

实体就是图谱里的节点。

在这个文档里，实体包括：

- 标准：`GB/T 25338.1—2019`
- 产品：`转辙机`
- 技术术语：`摩擦联接器`
- 技术参数：`工作电流`、`动作时间`
- 环境条件：`低温环境`、`湿热环境`
- 测试项目：`振动试验`、`绝缘耐压试验`
- 章节：`第5章`、`5.4`
- 组织：`天津铁路信号有限责任公司`
- 人员：`张辉`

### 2.2 什么是关系

关系就是两个实体之间的边。

例如：

```text
GB/T 25338.1—2019 --DRAFTED_BY--> 天津铁路信号有限责任公司
转辙机 --HAS_RATING--> IP54
第5章 --SPECIFIES--> 工作电流
GB/T 25338.1—2019 --REFERENCES--> GB/T 4208—2017
```

Neo4j 里实体间边的物理类型统一叫 `RELATES_TO`。这只是数据库存储方式，不代表语义关系都叫 `RELATES_TO`。

真正的语义类型保存在边属性 `r.name` 中，例如：

```json
{
  "name": "SPECIFIES",
  "fact": "第5章规定转辙机的工作电流、动作时间等主要参数。"
}
```

所以统计语义关系时要看 `r.name`，不能看 `type(r)`。

### 2.3 什么是 chunk

chunk 是把 PDF 文本切成的小段。当前配置大致是：

```yaml
chunk_size: 1200
chunk_overlap: 100
```

意思是：每段大约 1200 个字符，相邻段之间保留 100 个字符重叠，避免句子被切断后上下文丢失。

### 2.4 什么是 zero-degree entity

zero-degree entity 是没有任何实体关系边的节点。

例如图里有一个节点：

```text
5.10.1
```

但它没有任何：

```text
(:Entity)-[:RELATES_TO]->(:Entity)
```

那它就是 zero-degree entity。

这类节点不一定都是错的。有些是应该过滤的章节号或元数据，有些是真正漏抽了关系的高价值实体。

## 3. 代码一路以来优化了什么

### 3.1 Schema 和 Prompt 强化

更新文件：

- `schemas/gbt25338.yaml`
- `graphiti_core/prompts/extract_nodes.py`
- `graphiti_core/prompts/extract_edges.py`

优化内容：

1. 增加标准文档需要的实体类型。
2. 增加标准文档需要的关系类型。
3. 在 prompt 中明确告诉大模型：标准文档里哪些文字应该抽成什么类型。

强化后的实体类型包括：

- `Standard`
- `Product`
- `TechnicalTerm`
- `TechnicalParameter`
- `EnvironmentalCondition`
- `Rating`
- `TestItem`
- `Section`
- `Organization`
- `Person`

强化后的关系类型包括：

- `DRAFTED_BY`
- `PROPOSED_BY`
- `REFERENCES`
- `SPECIFIES`
- `HAS_ATTRIBUTE`
- `HAS_RATING`
- `HAS_TEST_METHOD`
- `HAS_TEST_CONDITION`
- `IS_PART_OF`
- `BELONGS_TO_SERIES`

例子：

原文：

```text
本文件由国家铁路局提出并归口。
本文件起草单位：天津铁路信号有限责任公司。
主要起草人：张辉、郝丽娜。
```

现在应该抽出：

```text
GB/T 25338.1—2019 --PROPOSED_BY--> 国家铁路局
GB/T 25338.1—2019 --DRAFTED_BY--> 天津铁路信号有限责任公司
GB/T 25338.1—2019 --DRAFTED_BY--> 张辉
GB/T 25338.1—2019 --DRAFTED_BY--> 郝丽娜
```

效果：早期部分类型经常为 0，现在 `10/10` 类实体连续多轮都有抽取结果。

### 3.2 条件二抽

更新文件：

- `graphiti_core/utils/maintenance/extraction_refinement.py`
- `graphiti_core/utils/maintenance/node_operations.py`
- `graphiti_core/utils/maintenance/edge_operations.py`
- `graphiti_rag/config.py`
- `graphiti_rag/config_loader.py`
- `graphrag_config.yaml`

#### 3.2.1 为什么需要二抽

第一轮抽取是让大模型直接从 chunk 里抽实体和关系。问题是：大模型有时会漏掉明显实体，或者抽到一些泛化、不准确的关系。

二抽不是重新跑完整流程，而是让大模型看到：

1. 当前 chunk 原文。
2. 第一轮已经抽出的结果。
3. schema 定义。
4. 需要重点检查的问题。

然后要求它输出“修正后的完整结果”。

可以理解为：

```text
第一轮：让大模型做初稿
第二轮：让大模型对初稿做审校
```

#### 3.2.2 什么叫“条件二抽”

条件二抽的意思是：不是每个 chunk 都二抽，只有符合条件时才二抽。

这样做是为了控制成本和噪声：

- 如果第一轮结果已经足够好，就不二抽。
- 如果第一轮明显抽得太少，才二抽。
- 如果关系校验后发现实体断连，才二抽。

配置示例：

```yaml
second_pass_extraction: true
second_pass_mode: conditional
second_pass_min_entities: 2
second_pass_min_edges: 1
```

含义：

- 开启二抽。
- 使用条件模式。
- 如果一个 chunk 第一轮实体少于 2 个，触发实体二抽。
- 如果一个 chunk 第一轮有效关系少于 1 条，触发关系二抽。

#### 3.2.3 实体二抽怎么工作

实体二抽关注的是“实体有没有漏”。

第一轮结果示例：

```json
{
  "extracted_entities": [
    {"name": "转辙设备", "entity_type_id": 0}
  ]
}
```

二抽 prompt 会把第一轮结果和原文一起发给大模型，让它审校。

二抽后可能变成：

```json
{
  "extracted_entities": [
    {
      "name": "转辙机",
      "entity_type_id": 2,
      "official_name": "转辙机",
      "synonyms": ["转辙设备"]
    },
    {
      "name": "IP66",
      "entity_type_id": 5
    }
  ]
}
```

产品视角看，这一步的价值是：把“抽得太少、抽得太泛”的 chunk 再审一遍，避免核心实体漏掉。

#### 3.2.4 关系二抽怎么工作

关系二抽关注的是“实体之间有没有漏关系”。

早期流程是：

```text
第一轮关系抽取
-> 判断是否需要二抽
-> 如果需要则二抽
-> 最后校验关系端点
```

后来发现这个顺序有问题。因为有些边在最后校验端点时会被丢弃。也就是说，第一轮看起来有边，但校验后可能没边。

现在流程改成：

```text
第一轮关系抽取
-> 先校验 source/target 是否真的存在
-> 丢弃端点不存在的边
-> 再看哪些实体经过校验后没有有效边
-> 如有必要，再做关系二抽
-> 二抽结果再次校验
```

例子：

实体列表里有：

```text
转辙机
动接点
第5章
```

第一轮边结果：

```text
动接点 --IS_PART_OF--> 转牧机
```

但实体列表里没有 `转牧机`。端点校验后这条边会被丢弃。

如果丢弃后 `动接点` 没有任何有效边，系统会把 `动接点` 放进“待重点检查实体”列表，让大模型二次审校：

```text
当前无关系的实体：动接点
请检查当前文本中这些实体是否应有关系。
```

如果原文有依据，二抽可能返回：

```text
动接点 --IS_PART_OF--> 转辙机
```

v7 实测中，这个 post-validation 二抽触发为 0。原因不是架构错，而是当前数据中被校验丢掉边的实体通常还有其他有效边，不会在 chunk 内变成 disconnected。这个架构仍然应该保留，因为它覆盖了真实边界场景。

### 3.3 Pydantic 字段形状防护

更新文件：

- `graphiti_core/utils/maintenance/attribute_utils.py`

问题：大模型有时会给字符串字段返回对象。

错误示例：

```json
{
  "product_type": {
    "value": "铁路信号设备",
    "confidence": 0.98
  }
}
```

但 schema 期望的是：

```json
{
  "product_type": "铁路信号设备"
}
```

以前这种错误会导致 Pydantic 校验失败，整个 chunk 可能失败。

现在处理方式：如果字段是字符串，但 LLM 返回了对象或数组，就转成紧凑字符串保存。

效果：

```text
v1: 11/12 chunks 成功
v2 以后: 12/12 chunks 成功
```

### 3.4 OCR 归一化

更新文件：

- `graphiti_core/utils/maintenance/ocr_normalization.py`
- `graphiti_core/utils/maintenance/dedup_helpers.py`
- `graphiti_core/utils/maintenance/node_operations.py`
- `graphiti_core/utils/maintenance/edge_operations.py`

OCR 问题示例：

```text
转辙机 -> 转儿机 / 转攻机 / 转狼机 / 转牧机
静接点 -> 王接点
额定 -> 祝定
```

现在维护了一组 alias：

```python
('转儿机', '转辙机')
('转攻机', '转辙机')
('转狼机', '转辙机')
('转牧机', '转辙机')
('王接点', '静接点')
('祝定', '额定')
```

使用位置有三处：

1. **创建实体前归一化**

```text
可挤型转攻机 -> 可挤型转辙机
```

2. **去重时归一化**

```text
转攻机 和 转辙机 使用同一个 dedupe key
```

3. **关系端点匹配时归一化**

如果 LLM 抽出：

```text
转攻机 --HAS_RATING--> IP54
```

但实体列表里是：

```text
转辙机
IP54
```

系统可以把 `转攻机` 映射回 `转辙机`，避免边被丢弃。

同时，原始 OCR 名会保留到 `synonyms`，用于审计：

```json
{
  "name": "可挤型转辙机",
  "official_name": "可挤型转辙机",
  "synonyms": ["可挤型转攻机"]
}
```

### 3.5 边端点校验和失败分类

更新文件：

- `graphiti_core/utils/maintenance/edge_operations.py`

关系必须连接到已经抽出的实体。否则这条边不能入库。

端点匹配现在会检查：

- 实体名 `name`
- 官方名 `official_name`
- 同义词 `synonyms`
- OCR 归一化后的名称

如果仍然找不到，会写分类日志。

分类包括：

- `ghost`：疑似幻觉或实体漏抽。
- `fuzzy_near_miss`：和已有实体存在子串关系。
- `ocr_variant`：OCR 归一化发生了，但还是找不到。
- `ocr_variant+fuzzy`：OCR 归一化后和已有实体有近似关系。

例子：

```text
Target entity not found [fuzzy_near_miss] edge=SPECIFIES name=5.5 normalized=5.5
```

产品视角看，这个日志的价值是：以前只知道“边丢了”，现在知道边为什么丢。

### 3.6 社区构建优化

更新文件：

- `graphiti_core/utils/maintenance/community_operations.py`

社区优化分三部分。

第一，单实体社区不调用 LLM。

```text
如果社区只有 1 个实体，直接生成基础社区，不浪费 LLM。
```

第二，有边的 singleton 会合并。

早期出现过这种情况：

```text
GB/T 25338.1—2019 有很多关系边，但社区算法仍把它放在单实体社区。
```

现在如果 singleton 有 `RELATES_TO` 邻居，就会合并到邻近社区。

第三，多实体社区使用结构化 profile。

现在社区会保存：

```json
{
  "name": "转辙机标准技术参数与测试条件社区",
  "summary": "...",
  "topics": ["转辙机工作环境条件", "振动耐久试验与参数"],
  "key_entities": ["GB/T 25338.1—2019", "转辙机", "振动试验"]
}
```

这使社区结果更适合用于检索、问答和产品展示。

### 3.7 DeepSeek / OpenAI 接口兼容

更新文件：

- `graphiti_core/llm_client/openai_client.py`

问题：DeepSeek 使用 OpenAI 兼容接口，但不支持 OpenAI 的 Responses API。

现在逻辑是：

```text
如果 base_url 是 api.openai.com，则可以走 Responses API。
如果是 DeepSeek 等非 OpenAI provider，则直接走 Chat Completions。
```

这样避免了不必要的接口失败。

## 4. 当前完整流程和每步例子

### 步骤 1：读取 PDF 文本

输入是标准 PDF，例如：

```text
GBT+25338.1-2019.pdf
```

系统先把 PDF 转成文本。由于 PDF 可能是扫描件或版式复杂，文本中会有 OCR 噪声。

例子：

```text
原本应为：转辙机
OCR 后可能变成：转攻机、转狼机、转牧机
```

输出：一整份标准文档文本。

### 步骤 2：切 chunk

系统把长文档切成多个小段。

当前配置大致是：

```yaml
chunk_size: 1200
chunk_overlap: 100
```

例子：

```text
chunk 3 可能包含第 5.4 节技术要求。
chunk 4 可能包含第 5.5 节试验要求。
相邻 chunk 之间会重叠一小段文字，避免上下文被切断。
```

输出：多个 chunk。

### 步骤 3：加载 schema

系统读取：

```text
schemas/gbt25338.yaml
```

schema 告诉系统：

- 可以抽哪些实体类型。
- 可以抽哪些关系类型。
- 哪些实体类型之间允许建立哪些关系。

例子：

```text
Standard 可以 DRAFTED_BY Organization
Standard 可以 REFERENCES Standard
Section 可以 SPECIFIES TechnicalParameter
Product 可以 HAS_RATING Rating
```

输出：实体类型定义、关系类型定义、关系签名约束。

### 步骤 4：第一轮实体抽取

大模型读取当前 chunk 和 schema，抽出实体。

原文例子：

```text
转辙机应能在 -40 ℃～+70 ℃ 环境条件下正常工作。
```

第一轮可能抽出：

```text
转辙机 -> Product
-40 ℃～+70 ℃ -> EnvironmentalCondition
正常工作 -> TechnicalTerm
```

输出：第一轮实体列表。

### 步骤 5：条件实体二抽

如果第一轮实体太少，系统会触发实体二抽。

例子：

第一轮只抽到：

```text
转辙设备
```

二抽看到原文后修正为：

```text
转辙机 -> Product
IP66 -> Rating
湿度不大于 90% -> EnvironmentalCondition
```

输出：审校后的实体列表。

### 步骤 6：OCR 名称归一化

实体入库前先做 OCR alias 替换。

例子：

```text
可挤型转攻机 -> 可挤型转辙机
王接点 -> 静接点
祝定转换力 -> 额定转换力
```

输出：规范化后的实体名，同时保留原始 OCR 名到 `synonyms`。

### 步骤 7：创建 EntityNode

系统把实体变成图节点。

例子：

```json
{
  "name": "可挤型转辙机",
  "labels": ["Entity", "Product"],
  "attributes": {
    "official_name": "可挤型转辙机",
    "synonyms": ["可挤型转攻机"]
  }
}
```

输出：待入库实体节点。

### 步骤 8：实体去重

系统检查当前实体是否已经存在。

去重会参考：

- 规范化后的名称
- 实体类型
- `official_name`
- `synonyms`
- OCR alias

例子：

```text
转攻机
转辙机
```

经过 OCR 归一化后都指向：

```text
转辙机
```

因此会合并。

输出：去重后的实体节点。

### 步骤 9：构建 MENTIONS 边

每个 chunk 会和它提到的实体建立 `MENTIONS` 边。

例子：

```text
chunk-5 --MENTIONS--> 转辙机
chunk-5 --MENTIONS--> IP54
chunk-5 --MENTIONS--> 工作电流
```

这类边不是业务语义边，而是“这个文本片段提到了这个实体”。

输出：文本片段到实体的引用边。

### 步骤 10：第一轮关系抽取

大模型读取当前 chunk 和实体列表，抽实体之间的关系。

原文：

```text
GB/T 25338.1—2019 规范性引用文件包括 GB/T 4208—2017。
```

输出关系：

```text
GB/T 25338.1—2019 --REFERENCES--> GB/T 4208—2017
```

原文：

```text
转辙机外壳防护等级应不低于 IP54。
```

输出关系：

```text
转辙机 --HAS_RATING--> IP54
```

### 步骤 11：关系端点校验

抽出的关系必须连接到实体列表中的节点。

如果 LLM 输出：

```text
转牧机 --HAS_RATING--> IP54
```

但实体列表里没有 `转牧机`，系统会尝试 OCR 归一：

```text
转牧机 -> 转辙机
```

如果 `转辙机` 存在，这条边可以保留并改成：

```text
转辙机 --HAS_RATING--> IP54
```

如果仍找不到，就丢弃并记录日志。

例子：

```text
Target entity not found [ghost] edge=SPECIFIES name=4.90 normalized=4.90
```

输出：通过校验的有效关系、被丢弃的无效关系、失败分类日志。

### 步骤 12：校验后关系二抽

系统根据“校验后的有效边”判断是否需要关系二抽。

例子：

实体列表：

```text
转辙机
动接点
第5章
```

第一轮关系：

```text
动接点 --IS_PART_OF--> 转牧机
```

校验后发现 `转牧机` 不存在，这条边被丢弃。此时如果 `动接点` 没有其他有效关系，系统会提示大模型重点检查：

```text
当前无关系的实体：动接点
请检查当前文本中这些实体是否应有关系。
```

如果原文有依据，二抽可能补出：

```text
动接点 --IS_PART_OF--> 转辙机
```

v7 实测这类场景触发为 0，但流程是正确的，保留用于防边界问题。

### 步骤 13：创建 EntityEdge

通过校验的关系会变成实体边。

例子：

```json
{
  "source": "GB/T 25338.1—2019",
  "target": "GB/T 4208—2017",
  "name": "REFERENCES",
  "fact": "GB/T 25338.1—2019规范性引用文件包括GB/T 4208—2017。"
}
```

注意：Neo4j 物理边仍是 `RELATES_TO`，语义类型存在 `name` 属性。

### 步骤 14：边去重、属性抽取、时间戳处理

系统会检查是否已经有相同事实的边。

例子：

```text
GB/T 25338.1—2019 --REFERENCES--> GB/T 4208—2017
```

如果之前已经存在同样事实，就复用或合并，不重复创建。

如果关系类型有结构化属性，也会抽属性。字段形状错误时会走 dict/list 到 string 防护。

输出：最终可保存的边。

### 步骤 15：写入 Neo4j

实体和边写入图数据库。

实体例子：

```text
(:Entity:Product {name: "转辙机"})
```

关系例子：

```text
(:Entity {name:"转辙机"})-[:RELATES_TO {name:"HAS_RATING"}]->(:Entity {name:"IP54"})
```

### 步骤 16：社区检测

系统根据实体之间的关系，把实体聚成社区。

例子：

```text
转辙机
IP54
工作电流
振动试验
GB/T 25338.1—2019
```

这些实体关系密集时，可能组成一个“转辙机技术要求社区”。

### 步骤 17：connected singleton 合并

如果某个实体被社区算法分成 singleton，但它其实有关系邻居，就合并到邻近社区。

例子：

```text
GB/T 25338.1—2019 有 58 条关系边，但被算法单独分成 singleton。
```

现在会把它合并回相关社区。

### 步骤 18：CommunityProfile 结构化

多实体社区会生成结构化 profile。

例子：

```json
{
  "name": "转辙机标准技术参数与测试条件社区",
  "summary": "该社区围绕转辙机标准、技术参数和试验条件展开。",
  "topics": [
    "转辙机工作环境条件",
    "振动耐久试验与参数"
  ],
  "key_entities": [
    "GB/T 25338.1—2019",
    "转辙机",
    "振动试验"
  ]
}
```

### 步骤 19：zero-degree 分析

最后分析没有任何关系边的实体。

v7 结果：

```text
45 / 229 zero-degree entities
```

分析发现它们不是同一种问题，而是多种来源：

- 深层章节号
- 文档元数据
- 参数值误提
- OCR 残留
- 同名异类型重复
- 真正缺边的高价值实体

这一步的价值是：不再盲目给所有孤立点补边，而是先判断哪些该删、哪些该合并、哪些才该补边。

## 5. 数据变化

主要版本指标：

| 指标 | v1 | v2 | v3 | v4 | v7 |
|---|---:|---:|---:|---:|---:|
| Chunk 成功率 | 11/12 | 12/12 | 12/12 | 12/12 | 12/12 |
| 入库实体 | 271 | 255 | 284 | 286 | 229 |
| 入库语义边 | 148 | 171 | 223 | 212 | 182 |
| 边类型覆盖 | 13/15 | 12/15 | 14/15 | 13/15 | 12/15 |
| 社区数 | 109 | 99 | 71 | 89 | - |
| Singleton | 103 | 93 | 63 | 81 | 45 |
| Zero-degree | - | - | 63 | 81 | 45 |

需要注意：实体数、边数不是越多越好。后期实体数下降，可能是因为噪声过滤、去重更强；边数下降也可能是无效边减少。判断质量要看：类型覆盖、有效关系、zero-degree 结构、社区可读性。

## 6. Zero-degree 最新归因

`zero_degree_analysis.md` 显示，v7 有：

```text
45 / 229 zero-degree entities
```

分类结果：

| 分类 | 数量 | 处置建议 |
|---|---:|---|
| 深层章节号叶子节点 | 16 | 过滤深层章节号，或只保留有边 Section |
| OCR 噪声残留 | 3 | 补 alias |
| 文档元数据/编目 | 4 | ICS/S 从 prompt 排除 |
| 参数值误提 | 2 | 强化 entity prompt |
| 抽样方案抽象概念 | 3 | 低优先级 |
| OCR 损坏专有名词 | 2 | 补 alias |
| 同名异类型重复 | 4，2 对 | 修同名跨类型 dedupe |
| 真遗漏高价值实体 | 9 | prompt 强化或专项补边 |

关键结论：

- 约 `48.9%` zero-degree 可以通过过滤或排除解决，不需要补边。
- 真正高价值、应该补边的只有 9 个。
- 不应对所有 zero-degree 统一补边，否则会给章节号、元数据、孤立参数硬造关系。

高价值缺边实体包括：

```text
GB/T 25338
动作杆动程
动接点
快速型
换向器直流电动机
摩擦联接器
直流电动机
绝缘电阻
驼峰用快速转辙机
```

## 7. 剩余问题

### 7.1 深层章节号过多

例如：

```text
5.10.1
5.11.2
6.3.1
7.1.3
表4
```

这些多数是索引型节点。如果没有边，检索价值较低。

建议：只保留有边的深层 `Section`，或只抽顶层/中层章节。

### 7.2 元数据和参数值误提

例如：

```text
ICS 45.020
S 61
1.5 MQ
25 MQ
```

建议在实体 prompt 和后处理中过滤。

### 7.3 同名异类型重复

发现两对：

```text
动作电流: TechnicalTerm / TechnicalParameter
振动耐久试验: TestItem / TechnicalParameter
```

建议做 schema-specific type priority：

```text
动作电流 -> TechnicalParameter 优先
振动耐久试验 -> TestItem 优先
```

不要全局无脑合并所有同名异类型实体。

### 7.4 高价值实体缺边

部分 Product / TestItem / TechnicalTerm 仍没有关系边。

建议只针对高价值类型做专项补边：

- `Product`
- `TestItem`
- `TechnicalTerm`
- `TechnicalParameter`

并要求必须有文本依据。

## 8. 下一步建议

优先级：

1. 实体过滤：排除 ICS/S 编号、孤立数值+单位；深层 Section 如果无边则过滤。
2. 补 OCR alias：`闪 -> 阀`、`透明单 -> 透明罩`。
3. 同名异类型合并：对参数名和试验名使用 schema-specific type priority。
4. 高价值实体专项补边：只针对 Product / TestItem / TechnicalTerm / TechnicalParameter，必须有文本依据。
5. 跑下一轮 v8：目标是 zero-degree 从 45 降到约 20，同时观察是否引入新噪声边。

## 9. 一句话总结

这一路优化已经把系统从“单轮抽取 + 粗糙入库”推进到“schema 约束 + 条件二抽 + OCR 归一 + 校验后补救 + 社区结构化 + zero-degree 诊断”的完整 GraphRAG ingestion pipeline。

现在最大的问题不再是抽不到东西，而是要更精细地区分：哪些实体应该过滤，哪些实体应该合并，哪些实体才值得补边。
