# 工具驱动的知识图谱抽取 Schema 设计方法

本文档总结一套通用方法：当换成另一类文本时，如何借助统计工具、规则工具和大模型，设计出稳定可复用的知识图谱抽取 schema、提示词和校验流程。

核心观点：

```text
工具发现规律
LLM 归纳结构
人确定边界
规则负责校验
二抽负责修正
报告驱动迭代
```

schema 不应该靠人看几页文档后直接拍脑袋写出来。更稳的方式是先让工具分析整批文档的语言结构、主题、高频词、短语、数值、编号、共现关系和失败日志，再让大模型基于这些证据归纳候选 schema，最后由人审核边界，并通过小样本抽取结果持续修正。

## 1. 总体流程

推荐流程如下：

```text
文档集合
-> 文本清洗
-> 文档画像
-> 高频词/短语统计
-> 特殊模式识别
-> 共现关系分析
-> 候选实体和关系发现
-> LLM 生成候选 schema
-> 人工审核 schema
-> 小样本抽取验证
-> 质量报告分析
-> 修 schema / prompt / alias / 过滤规则
-> 全量抽取
```

这套流程的重点不是一次性写出完美 schema，而是建立一个可迭代闭环。每一轮抽取结果都会产生新的证据，包括 zero-degree 实体、entity-not-found、rejected entities、rejected edges、OCR alias 候选和关系类型覆盖情况。这些证据反过来指导下一轮 schema 和提示词修改。

## 2. 阶段一：文档画像

拿到一种新文本后，第一步不是直接抽图谱，而是做文档画像。

需要统计：

```text
文档总数
总页数
chunk 数量
平均 chunk 长度
章节结构
标题分布
目录模式
表格比例
OCR 质量
高频词
高频短语
高频数值
高频单位
高频编号
高频组织名
高频人名
高频关系触发词
```

以标准文本为例，工具可能发现：

```text
高频词：转辙机、试验、规定、动作、电动、锁闭、绝缘、电流、电压、防护等级
高频短语：电动转辙机、动作电流、绝缘电阻、转换时间、摩擦联接器、快速转辙机
高频数值：IP54、IP66、24 V、50 Hz、1000 次、-40 ℃、70 ℃
高频编号：GB/T 25338、GB/T 2828.1、5.2、5.5、5.5.7
高频关系触发词：应符合、应满足、应不低于、规定、引用、替代、由……起草
```

这些结果说明，标准文本里大概率需要 `Standard`、`Section`、`Product`、`TechnicalTerm`、`TechnicalParameter`、`Rating`、`TestItem`、`EnvironmentalCondition`、`Organization`、`Person`。

如果换成合同文本，工具可能发现：

```text
高频词：甲方、乙方、付款、交付、违约、期限、合同、服务、费用
高频短语：付款期限、违约责任、服务内容、保密义务、交付成果
高频数值：30 日、10 万元、5%、一年
高频关系触发词：应支付、应交付、承担、终止、赔偿、保密
```

此时 schema 应该转向 `Party`、`Contract`、`Obligation`、`PaymentTerm`、`Deadline`、`Deliverable`、`LiabilityClause`、`ConfidentialityClause`。


## 3. 高频词不能直接等于实体

高频词只是线索，不是实体类型。工具统计出来的词需要进一步分类：

```text
对象词：适合成为实体
动作词：适合成为关系触发词
数值词：适合成为参数、等级或属性
编号词：适合成为标准、章节、条款或过滤规则
噪声词：适合进入停用词或过滤规则
OCR 异常词：适合进入 alias 候选
```

标准文本中的例子：

```text
转辙机 -> Product
动作电流 -> TechnicalParameter 或 TechnicalTerm，取决于上下文
绝缘电阻 -> TestItem 或 TechnicalParameter，取决于上下文
IP54 -> Rating
GB/T 2828.1 -> Standard
5.5.7 -> Section，或者作为深层章节号过滤
应符合 -> 关系触发词
规定 -> 关系触发词
转牧机 -> OCR alias 候选
```

不要把词频最高的词全部建成节点。schema 设计要回答的是：这个词是否是稳定对象？是否会被复用？是否能作为关系端点？用户是否会单独查询它？

## 4. 推荐工具链

基础工具：

```text
Python
pandas
正则表达式
jieba / pkuseg / HanLP
TF-IDF
n-gram 统计
共现矩阵
embedding 聚类
LLM 总结
人工审核
```

可选增强工具：

```text
KeyBERT：关键词抽取
BERTopic：主题聚类
DuckDB：统计查询
spaCy / HanLP：实体候选发现
Neo4j / graph analytics：图谱质量分析
Excel：人工审阅候选词和错误清单
```

工具分工：

```text
统计工具：发现文本反复出现什么
正则工具：抓编号、数值、单位、章节号
分词工具：发现候选词和短语
embedding 工具：把同类概念聚在一起
LLM：归纳类型、解释语义、生成 schema 草案
规则代码：决定最终能不能入库
```

LLM 不应该是第一步。LLM 应该吃工具产出的证据。

## 5. 特殊模式识别

很多领域文本都有稳定模式。应该用正则或结构化规则先抓出来。

标准文本常见模式：

```text
标准编号：GB/T 25338.1-2019、GB/T 2828.1
章节编号：5.2、5.5.7、附录 A
等级：IP54、IP66、B级、F级、V-2
参数：24 V、50 Hz、1000 次、-40 ℃
组织：有限公司、研究院、标准化委员会
人名：主要起草人列表
```

合同文本常见模式：

```text
金额：人民币 10 万元、¥100,000
期限：30 日内、2026 年 6 月 30 日前
主体：甲方、乙方、丙方
义务触发词：应、必须、负责、承担
违约触发词：逾期、违约、赔偿、解除
```

医学指南常见模式：

```text
疾病名称
症状
药品名称
剂量：10 mg、每日 2 次
禁忌症
推荐等级
证据等级
```

这些模式会直接影响实体类型和属性设计。

## 6. 共现关系分析

共现分析用于发现候选关系。

要统计：

```text
某个核心对象附近经常出现哪些词？
某个参数经常和哪些对象共同出现？
某个等级经常和哪些产品共同出现？
某个编号经常出现在哪类句子中？
某些触发词左右两边通常是什么实体？
```

标准文本示例：

```text
产品 + IP 等级 + “应不低于” => HAS_RATING
章节 + 参数 + “规定” => SPECIFIES
标准 + 标准 + “引用” => REFERENCES
标准 + 组织 + “起草” => DRAFTED_BY
产品 + 测试项目 + “按……进行” => HAS_TEST_METHOD
```

合同文本示例：

```text
Party + 金额 + “应支付” => REQUIRES_PAYMENT
Party + 交付物 + “应交付” => HAS_OBLIGATION
Obligation + 日期 + “前” => HAS_DEADLINE
违约行为 + 赔偿条款 => BREACH_TRIGGERS
```

关系类型应该来自文本反复表达的语义动作，而不是人为列一堆理论上可能存在的关系。

## 7. 让 LLM 基于统计证据生成候选 schema

不要直接把整篇 PDF 丢给大模型问“帮我设计 schema”。应该先把工具分析结果整理成结构化摘要。

示例输入：

```json
{
  "document_theme": "城市轨道交通转辙机技术标准",
  "top_terms": ["转辙机", "试验", "动作电流", "绝缘电阻", "IP54"],
  "top_phrases": ["电动转辙机", "动作杆动程", "摩擦联接器"],
  "numeric_patterns": ["IP54", "24 V", "50 Hz", "1000 次"],
  "reference_patterns": ["GB/T 25338", "GB/T 2828.1", "5.5.7"],
  "relation_triggers": ["应符合", "应满足", "规定", "引用", "起草"],
  "sample_sentences": [
    "转辙机外壳防护等级应不低于 IP54。",
    "动作电流应符合表 1 的规定。",
    "本标准由全国轨道交通电气设备与系统标准化技术委员会提出。"
  ]
}
```

让 LLM 输出：候选实体类型、good examples、bad examples、候选关系类型、source_types、target_types、容易混淆的类型、建议过滤的噪声、可能的 OCR alias、建议的提示词规则。

LLM 输出后，人再审核：这个实体类型是否业务需要？关系是否太细或太泛？类型是否会导致大量垃圾节点？哪些类型应该 strict？哪些类型应该允许 fallback？哪些实体应该只做属性而不是节点？


## 8. 实体类型设计原则

实体类型可以按三层设计。

第一层：主对象。

```text
标准文本：Standard、Product、Section
合同文本：Contract、Party、Clause
医学文本：Disease、Drug、Symptom、Guideline
论文文本：Method、Dataset、Metric、Task
```

第二层：约束对象。

```text
标准文本：TechnicalParameter、Rating、EnvironmentalCondition、TestItem
合同文本：Obligation、PaymentTerm、Deadline、LiabilityClause
医学文本：Dosage、Contraindication、ClinicalRecommendation
```

第三层：来源对象。

```text
Standard
Section
Organization
Person
Document
Regulation
Reference
```

判断某个词是否应该成为实体，可以看：

```text
它是否会被多个地方引用？
它是否可以作为关系的起点或终点？
用户是否可能单独搜索它？
它是否需要和其他对象做对比？
它是否有自己的属性、编号、别名或定义？
它是否可能跨 chunk 重复出现，需要去重？
```

## 9. 关系类型设计原则

关系类型不要太细，也不要太泛。

太细会导致 LLM 难以稳定命中：

```text
HAS_MINIMUM_VOLTAGE
HAS_MAXIMUM_VOLTAGE
HAS_RATED_VOLTAGE
HAS_WORKING_VOLTAGE
```

这些通常可以先收敛为：

```text
SPECIFIES
HAS_ATTRIBUTE
```

太泛会导致图谱没有语义：

```text
RELATED_TO
```

如果所有关系都叫 RELATED_TO，后续检索、问答、统计和推理都会变弱。

更好的方式是中等粒度：

```yaml
DEFINES:
  description: "文档或章节定义某个术语或对象"

SPECIFIES:
  description: "文档、章节或主体规定参数、条件、等级或要求"

HAS_COMPONENT:
  description: "主体包含某个组成部分"

HAS_RATING:
  description: "主体具有某个等级"

REFERENCES:
  description: "文档、章节或条款引用另一个文档或章节"

DRAFTED_BY:
  description: "文档由组织或人员起草"
```

关系设计必须包含：

```text
description
source_types
target_types
good_examples
bad_examples
触发词
容易混淆的关系
```

## 10. 属性设计原则

属性适合放实体自身信息，不适合承载核心关系。

适合做属性：

```text
official_name
synonyms
document_number
standard_number
unit
value
abbreviation
role
definition
```

适合做节点和边：

```text
会被多个实体共享的信息
需要单独检索的信息
需要连接其他实体的信息
需要跨 chunk 去重的信息
```

例子：

```text
“GB/T 25338.1-2019”可以是 Standard 的 standard_number。

“IP54”最好是 Rating 节点，而不只是 Product 的属性，因为多个产品可能都有 IP54，也可能有章节规定 IP54。

“24 V”可以是 TechnicalParameter 节点，因为它可能被产品、章节、试验条件共同引用。
```

经验规则：

```text
如果这个信息以后需要连接别的实体，就做节点和边。
如果它只是实体自己的编号、别名、单位、说明，就做属性。
```

## 11. Prompt 也要由工具结果驱动

提示词不应该只有通用规则。它应该包含本批文档的统计发现。

实体抽取 prompt 可以加入：

```text
本批文档的高频核心对象包括：
转辙机、电动转辙机、快速转辙机、动作杆、锁闭杆、摩擦联接器、直流电动机。

本批文档的高频参数包括：
动作电流、转换时间、绝缘电阻、额定电压、频率、温度、湿度、试验次数。

本批文档的高频等级包括：
IP54、IP55、IP66、B级绝缘、F级绝缘、V-2阻燃。

常见 OCR 变体：
转牧机、转狼机、转儿机通常可能是“转辙机”，但必须结合上下文判断。
```

关系抽取 prompt 可以加入：

```text
当文本出现“应符合、应满足、应不低于、规定”时，优先考虑 SPECIFIES、HAS_RATING、HAS_ATTRIBUTE。

当文本出现“按 GB/T xxx 进行”时，优先考虑 REFERENCES 或 HAS_TEST_METHOD。

当文本出现“由……起草”时，优先考虑 DRAFTED_BY。

关系端点必须使用实体列表中的 name，不能自己发明端点。
```

这比纯手写 prompt 更稳定，因为它来自真实文档统计。


## 12. Rejection Ledger 与二抽

大模型不能直接决定最终入库。正确分工是：

```text
大模型负责提出候选
规则负责判断能不能入库
rejection ledger 记录为什么被拒绝
二抽根据拒绝记录修正可修复项
二抽结果再次经过规则校验
```

实体拒绝记录示例：

```json
{
  "name": "动作杆动程",
  "entity_type_id": 99,
  "reason": "invalid_entity_type_id",
  "fixable": true,
  "instruction": "只能改成 schema 中存在的 entity_type_id"
}
```

边拒绝记录示例：

```json
{
  "source_entity_name": "转辙机",
  "target_entity_name": "IP54",
  "relation_type": "HAS_RATING",
  "reason": "source_not_found",
  "candidate_source": "电动转辙机",
  "fixable": true
}
```

不可修复项示例：

```json
{
  "name": "",
  "reason": "empty_name",
  "fixable": false,
  "instruction": "不要恢复空名称实体"
}
```

不可修复项不是让模型恢复，而是明确告诉模型不要再输出。

## 13. Alias 维护方法

alias 需要维护，但不应该完全靠人工猜。

推荐三层机制：

```text
第一层：人工维护高频、确定的 OCR 错误。
例如：转牧机 -> 转辙机，转狼机 -> 转辙机，转儿机 -> 转辙机。

第二层：自动记录 near miss。
例如边端点“转辙机”找不到，但已有实体“电动转辙机”，系统记录候选，但不自动合并。

第三层：把可修复项交给二抽。
让模型结合原文判断是否应该使用候选实体。
```

alias 入库原则：

```text
高频
确定
上下文一致
人工审核过
```

不要把所有相似词都自动归一，否则会误合并。

## 14. 小样本试跑

不要一上来跑全量。先选 20 到 50 个代表性 chunk：

```text
正文 chunk
表格 chunk
目录 chunk
定义章节
技术要求章节
试验章节
引用文件章节
起草单位章节
OCR 质量差的 chunk
长句多的 chunk
编号密集的 chunk
```

每轮试跑后生成质量报告。重点看：

```text
实体数量是否异常膨胀
实体类型分布是否合理
边数量是否过少
关系类型覆盖是否合理
zero-degree 实体有哪些
entity-not-found 有哪些
rejected entities 的原因分布
rejected edges 的原因分布
同名异类型实体有哪些
OCR alias 命中率
每个 chunk 的边密度
```

## 15. 用质量报告反推 schema

常见问题和对应调整：

```text
大量普通名词进图
=> 实体定义太宽，补 bad examples 和过滤规则。

大量 Entity fallback
=> schema 类型不够明确，或 prompt 没教模型分类。

大量 zero-degree Section
=> 章节实体太细，考虑过滤深层章节号，或只保留有标题章节。

大量 entity-not-found
=> 实体抽取和关系抽取命名不一致，需要 official_name、synonyms、alias 或 rejected edge 二抽。

某类关系一直抽不到
=> 关系定义不贴近文本表达，或关系粒度太细。

IS_PART_OF / CONTAINS 异常膨胀
=> 层级关系 prompt 太宽，需要限制只抽明确包含关系。

同名异类型实体很多
=> 类型边界不清，需要增加 disambiguation 规则。

OCR near miss 高频出现
=> 人工审核后加入 alias。
```

## 16. 推荐产物

每换一种新文本，建议先生成这些文件：

```text
01_corpus_profile.md
文档画像：主题、结构、chunk 数、章节模式、OCR 质量。

02_term_frequency.xlsx
高频词、高频短语、TF-IDF、每章分布。

03_candidate_schema.md
候选实体类型、关系类型、例子、反例。

04_extraction_quality_report.md
试跑后的实体分布、边分布、失败原因、zero-degree 分析。

05_schema_revision_plan.md
基于数据的 schema / prompt / alias / 过滤规则修改计划。
```

这样 schema 有证据链，而不是经验判断。

## 17. 通用 schema 起始模板

下面是一个可复用的起始模板。实际项目应根据文档画像和统计结果调整。

```yaml
schema:
  mode: strict
  description: "面向某类文本的知识图谱 schema"

entity_types:
  Document:
    description: "文档、标准、合同、指南、论文、报告等整体文件"
    properties:
      official_name:
        type: string
      document_number:
        type: string
      synonyms:
        type: list[string]

  Section:
    description: "章节、条款、段落、附录或编号"
    properties:
      section_number:
        type: string
      title:
        type: string
      document:
        type: string

  Subject:
    description: "文本讨论的核心对象，例如产品、疾病、项目、设备、合同主体"
    properties:
      official_name:
        type: string
      category:
        type: string
      synonyms:
        type: list[string]

  Requirement:
    description: "要求、规则、义务、建议、限制或判断标准"
    properties:
      requirement_text:
        type: string
      scope:
        type: string

  Parameter:
    description: "数值参数、金额、时间、比例、温度、频率、次数等"
    properties:
      parameter_name:
        type: string
      value:
        type: string
      unit:
        type: string

  Condition:
    description: "适用条件、环境条件、前提条件、触发条件"
    properties:
      condition_name:
        type: string
      value:
        type: string
      scope:
        type: string

  Organization:
    description: "机构、公司、部门、发布方、监管方、执行方"
    properties:
      official_name:
        type: string
      role:
        type: string
      synonyms:
        type: list[string]

  Person:
    description: "自然人、作者、负责人、起草人、专家"
    properties:
      official_name:
        type: string
      role:
        type: string
      affiliation:
        type: string

edge_types:
  CONTAINS:
    description: "文档包含章节，章节包含子章节"
    source_types: ["Document", "Section"]
    target_types: ["Section"]

  DEFINES:
    description: "文档或章节定义某个对象、术语或要求"
    source_types: ["Document", "Section"]
    target_types: ["Subject", "Requirement"]

  SPECIFIES:
    description: "文档、章节或主体规定某个参数、条件或要求"
    source_types: ["Document", "Section", "Subject"]
    target_types: ["Parameter", "Condition", "Requirement"]

  APPLIES_TO:
    description: "要求、参数或条件适用于某个主体"
    source_types: ["Requirement", "Parameter", "Condition"]
    target_types: ["Subject"]

  REFERENCES:
    description: "文档、章节或要求引用另一个文档或章节"
    source_types: ["Document", "Section", "Requirement"]
    target_types: ["Document", "Section"]

  RESPONSIBLE_FOR:
    description: "组织或人员对某个文档、要求、任务或主体负责"
    source_types: ["Organization", "Person"]
    target_types: ["Document", "Requirement", "Subject"]

  RELATED_TO:
    description: "有明确文本依据但无法归入更具体类型的关系"
    source_types: ["Subject", "Requirement", "Parameter", "Condition"]
    target_types: ["Subject", "Requirement", "Parameter", "Condition"]
```

## 18. 最终原则

schema 设计要追求：

```text
抽得稳定
查得出来
错了能定位
下一轮能修
```

不要追求理论完整。理论完整但抽不稳的 schema 没有工程价值。

真正可复用的方法是：

```text
文档统计 -> 候选 schema -> 小样本抽取 -> 质量报告 -> 修正规则 -> 全量抽取
```

换成另一种文本，也照这个结构做。先用工具理解文本，再让 LLM 基于证据归纳，再用规则和报告把结果压稳。
