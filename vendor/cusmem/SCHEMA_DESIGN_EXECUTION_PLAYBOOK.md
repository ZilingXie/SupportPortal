# Schema 设计落地执行手册

本文档给出一套确定、可执行的流程，用于为任意新领域文本设计知识图谱抽取 schema、提示词、alias 和校验规则。

目标不是讲概念，而是规定：每一步用什么工具、输入是什么、输出是什么、怎么算指标、什么时候进入下一步、失败后怎么修。

## 0. 固定工具栈

这套流程不只依赖当前环境，而是给出一个“标准版 + 增强版”的工具组合。标准版足够轻，适合大多数文本；增强版用于 PDF 结构复杂、表格多、OCR 差、需要更高质量的场景。

### 0.1 标准版工具栈

标准版目标：不重，但能稳定完成 80% 文档的 schema 设计。

推荐安装：

```bash
uv add pymupdf pdfplumber scikit-learn openpyxl pyyaml rapidfuzz jieba
```

固定用途：

```text
PyMuPDF：PDF 快速抽取文本、页面块、坐标、字体信息；也可做页面渲染。
pdfplumber：表格、坐标、单词级抽取；适合检查表格和版面。
scikit-learn：TF-IDF、字符 n-gram、KMeans 聚类。
openpyxl：输出人工审核 Excel。
PyYAML：读写 schema YAML。
rapidfuzz：alias 候选、OCR near miss、实体名相似度。
jieba：中文分词；如果效果不够，再换 HanLP。
Python re / Counter / jsonl：正则模式识别、频次统计、轻量数据管道。
```

### 0.2 增强版工具栈

增强版不是默认上来就用，而是满足触发条件后再加。

```bash
uv add docling
uv add unstructured
uv add sentence-transformers
uv add hanlp
```

OCR 增强工具按机器环境单独安装：

```text
PaddleOCR：中文 OCR、复杂版面、表格/印章/扫描件更优先。
Tesseract：轻量 OCR 兜底，适合简单英文或简单扫描文本。
```

增强工具触发条件：

```text
Docling：PDF/Word/PPT/HTML/XLSX 混合输入，或需要统一文档结构表示时使用。
Unstructured：多格式解析、复杂文档切分、已有 unstructured 工作流时使用。
sentence-transformers：需要语义聚类、alias 语义相似度、跨语言相似度时使用。
HanLP：中文专业领域词语边界明显影响 schema 判断时使用。
PaddleOCR：PDF 是扫描件，或可选文本层质量差，或表格截图/图片很多时使用。
```

### 0.3 工具选择原则

固定原则：

```text
PDF 有高质量文本层：PyMuPDF + pdfplumber。
PDF 表格很多：pdfplumber；复杂表格再加 Docling。
扫描 PDF：pypdfium2 或 PyMuPDF 渲染页面 + PaddleOCR。
多格式文档：Docling 优先；Unstructured 作为多格式解析备选。
中文普通文本：jieba + char n-gram。
中文专业术语密集：HanLP 或自定义词典。
候选 alias：rapidfuzz 先筛，embedding 后验。
主题聚类：TF-IDF + KMeans 先做，语义聚类作为增强。
```

### 0.4 不建议一开始使用的重方案

以下方案不是不能用，而是不应该第一轮就用：

```text
全量文档直接喂 LLM：成本高、不可复现、难定位错误。
全量 embedding 聚类：没清洗前噪声大，结论容易偏。
复杂 OCR 全开：慢，且会把可选文本层已有内容重复识别。
过细的领域本体建模：schema 抽不稳时会放大错误。
```

先用标准版跑出证据，再决定是否上增强版。

## 1. 阶段一：文本抽取

### 1.1 目标

把 PDF、Word、HTML 或纯文本统一转换成 page-level 文本文件。

### 1.2 工具选择

使用固定决策树，不临场拍脑袋。OCR 是兜底增强步骤，不是默认第一步。

#### 1.2.1 第一步：文本层优先

先用 PyMuPDF 做快速文本层探测，再用 pdfplumber 补表格和坐标。

```text
每页字符数 >= 300
中文可读字符比例正常
乱码比例低
章节顺序基本正确
没有大量 (cid:xxx)
=> 使用 PyMuPDF 抽 page text，pdfplumber 抽表格和坐标补充。
```

原因：文本层抽取快、便宜、可复现，不会引入 OCR 识别噪声。OCR 会制造新的错字，例如“阀”识别成“闪”、“辙”识别成“牧/狼/儿”，所以不能默认全开。

#### 1.2.2 第二步：按页质量评分

每一页都要计算质量分，而不是只看全文平均值。

每页记录：

```json
{
  "page": 12,
  "char_count": 1380,
  "cjk_ratio": 0.72,
  "garbled_ratio": 0.01,
  "cid_count": 0,
  "line_count": 42,
  "table_count": 2,
  "image_count": 0,
  "needs_ocr": false,
  "reason": []
}
```

建议指标：

```text
char_count：页面字符数
cjk_ratio：中文可读字符占比
cid_count：(cid:xxx) 出现次数
garbled_ratio：乱码/控制字符/异常符号比例
line_count：非空行数
table_count：表格数量
image_count：图片对象数量
text_order_score：文本顺序是否异常
```

#### 1.2.3 OCR 触发条件

只对问题页 OCR，不默认整本 OCR。

单页满足任意条件，标记 `needs_ocr=true`：

```text
char_count < 80 且页面不是空白页
cid_count > 0
cjk_ratio < 0.2 且文档是中文文档
garbled_ratio > 0.05
页面主要是图片/扫描件
表格截图无法从文本层抽出
文本顺序严重错乱，无法还原语义
```

全文满足以下条件时，才允许进入批量 OCR 模式：

```text
empty_page_ratio > 0.15
avg_chars_per_page < 300
needs_ocr_pages / total_pages > 0.3
扫描页比例 > 0.3
```

#### 1.2.4 OCR 工具选择

```text
中文扫描件 / 表格截图 / 复杂版面：PaddleOCR
简单英文或少量兜底页：Tesseract
只需要页面图片渲染：PyMuPDF 或 pypdfium2
```

推荐默认：

```text
普通 PDF：PyMuPDF + pdfplumber
复杂 PDF：PyMuPDF + pdfplumber + Docling 抽检
扫描 PDF：PyMuPDF 渲染问题页 + PaddleOCR
多格式批处理：Docling 或 Unstructured
```

#### 1.2.5 文本层与 OCR 结果合并

OCR 不是覆盖文本层，而是按页择优合并。

合并规则：

```text
文本层质量合格：保留文本层，不用 OCR。
文本层质量不合格，OCR 质量合格：用 OCR 替换该页。
文本层和 OCR 各有优势：保留两者，但标注 source="text+ocr"，后续去重。
表格文本层合格但正文差：正文用 OCR，表格用 pdfplumber。
OCR 结果短于文本层 50% 且没有更高可读性：拒绝 OCR 结果。
```

每页输出必须保留 provenance：

```json
{
  "page": 12,
  "text": "最终使用文本",
  "source": "text_layer|ocr|text+ocr",
  "text_layer_quality": {...},
  "ocr_quality": {...},
  "ocr_engine": "paddleocr|null"
}
```

#### 1.2.6 OCR 后复检

OCR 后必须重新跑质量检测和噪声检测。

重点检查：

```text
新增的异常短词
高频 near miss
同一术语的多个 OCR 变体
中文 + 标点异常片段
单位/数字被拆开
表格行列被打乱
```

OCR 复检输出：

```text
ocr_suspects.xlsx
alias_candidates.xlsx
page_quality_after_ocr.md
```

只有 OCR 后质量达标，才进入清洗和 chunking。

### 1.3 推荐命令形态

```bash
uv run python tools/schema_profile/extract_text.py \
  --input GBT+25338.1-2019.pdf \
  --output runs/schema_design/pages.jsonl \
  --quality-output runs/schema_design/page_quality.jsonl \
  --engine auto \
  --ocr-mode selective
```

如果当前还没有脚本，就按这个接口新增。不要把抽取结果直接塞给 LLM。先看 `page_quality.jsonl`，确认文本层是否合格、哪些页触发了 OCR、OCR 合并是否可靠。

### 1.4 输出文件

`runs/schema_design/pages.jsonl`

每行结构必须保留 provenance 和质量信息：

```json
{
  "doc_id": "GBT25338",
  "page": 12,
  "text": "最终使用文本",
  "source": "text_layer|ocr|text+ocr",
  "extractor": "pymupdf|pdfplumber|docling|unstructured|paddleocr",
  "char_count": 1380,
  "quality": {
    "cjk_ratio": 0.72,
    "garbled_ratio": 0.01,
    "cid_count": 0,
    "needs_ocr": false,
    "ocr_applied": false
  }
}
```

### 1.5 质量指标

必须计算：

```text
page_count：页数
empty_page_ratio：空页比例
avg_chars_per_page：平均每页字符数
garbled_ratio：乱码字符比例
short_page_count：少于 50 字的页面数
```

建议门槛：

```text
empty_page_ratio <= 0.15
avg_chars_per_page >= 300
乱码字符比例 <= 0.05
```

如果不达标，不进入 schema 设计，先修文本抽取或 OCR。

## 2. 阶段二：清洗与 chunking

### 2.1 目标

把 page 文本转换成适合统计和抽取的 chunk，同时保留页码、章节、标题路径。

### 2.2 工具

```text
Python re：识别章节号、标题、页眉页脚
collections.Counter：识别重复页眉页脚
自定义 chunker：按章节优先，其次按长度切分
```

### 2.3 清洗规则

固定执行：

```text
去除连续空白
统一全角/半角符号
去除重复页眉页脚
保留章节号
保留标准号
保留参数值和单位
保留表格中的文字
不要把数字、单位、编号提前删除
```

页眉页脚识别方法：

```text
统计每页首 2 行和末 2 行
如果某行在 30% 以上页面重复出现，标记为 header/footer 候选
人工确认后加入清洗规则
```

### 2.4 chunk 策略

优先级：

```text
优先按一级/二级章节切
章节太长时再按 1200 到 1800 字切
表格单独保留为 chunk
目录页单独标记，不直接用于 schema 主体判断
```

### 2.5 输出文件

`runs/schema_design/chunks.jsonl`

```json
{
  "doc_id": "GBT25338",
  "chunk_id": "GBT25338-p12-c01",
  "page_start": 12,
  "page_end": 13,
  "section_path": ["5", "5.5", "5.5.7"],
  "section_title": "技术要求",
  "text": "chunk 原文",
  "char_count": 1520,
  "is_table": false,
  "is_toc": false
}
```

### 2.6 质量门槛

```text
chunk 平均长度：800 到 1800 字
超短 chunk 比例：<= 20%
每个 chunk 必须有 page_start
能识别章节的文档，section_path 覆盖率 >= 60%
```

## 3. 阶段三：稳定模式识别

### 3.1 目标

用正则先抓出领域文本中的稳定结构。这一步必须在 LLM 之前做。

### 3.2 工具

```text
Python re
openpyxl 输出 Excel
collections.Counter 统计频次
```

### 3.3 通用正则清单

标准号：

```regex
(?:GB|GB/T|ISO|IEC|EN|ASTM|TB|JT|YD|IEEE)\s*/?\s*[A-Z0-9.\-—]+
```

章节号：

```regex
(?:第\s*[一二三四五六七八九十百0-9]+\s*[章节条])|(?:\d+(?:\.\d+){0,4})|(?:附录\s*[A-ZＡ-Ｚ])
```

数值和单位：

```regex
[<>≤≥=]?[\-−]?\d+(?:\.\d+)?\s*(?:℃|°C|V|kV|A|mA|Hz|kHz|N|kN|Pa|kPa|MPa|mm|cm|m|s|min|h|次|%|Ω|MΩ)
```

等级：

```regex
IP\s*\d{2}|[A-Z]\s*级|V-?\d
```

日期：

```regex
\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/]\d{1,2}[-/]\d{1,2}
```

金额：

```regex
(?:人民币|¥|￥)?\s*\d+(?:\.\d+)?\s*(?:元|万元|亿元)
```

组织：

```regex
[一-龥A-Za-z0-9（）()]+(?:公司|研究院|委员会|协会|中心|大学|集团|部门|机构|实验室)
```

常见关系触发词：

```text
规定、定义、引用、替代、等同、提出、归口、起草、应符合、应满足、应不低于、应不超过、按……进行、适用于、负责、支付、交付、承担、终止、赔偿
```

### 3.4 输出文件

`runs/schema_design/pattern_inventory.xlsx`

工作表：

```text
standards：标准号/引用文件
sections：章节号/标题
numeric_values：数值和单位
ratings：等级
dates：日期
money：金额
organizations：组织
relation_triggers：关系触发词
ocr_suspects：疑似 OCR 异常词
```

每张表字段：

```text
value
count
sample_chunk_id
sample_context
page_start
```

### 3.5 决策规则

```text
高频标准号 -> 候选 Standard
高频章节号 -> 候选 Section 或过滤规则
高频数值单位 -> 候选 Parameter / TechnicalParameter
高频 IP/B级/F级 -> 候选 Rating
高频组织后缀 -> 候选 Organization
高频触发词 -> 候选 edge type 和 prompt 规则
```

## 4. 阶段四：高频词和短语统计

### 4.1 目标

发现文档反复讨论的核心对象。

### 4.2 工具

标准版固定使用：

```text
jieba：中文分词，支持自定义领域词典。
scikit-learn TfidfVectorizer：TF-IDF、字符 n-gram。
Python re：抽标准号、编号、数值单位、英文缩写。
Counter：原始频次。
openpyxl：输出审阅表。
rapidfuzz：相似词、OCR alias 候选。
```

增强版按需使用：

```text
HanLP：中文专业术语分词和实体候选更稳。
sentence-transformers：语义相似度、主题聚类、alias 语义复核。
```

固定策略：

```text
先用 jieba 分词 + TF-IDF。
同时用 char n-gram 防止分词漏掉专业短语。
再用正则 token 保留标准号、参数、单位、英文缩写。
最后用人工审核表决定 ENTITY / RELATION_TRIGGER / ATTRIBUTE / NOISE / ALIAS_CANDIDATE。
```

### 4.3 推荐算法

中文默认用字符 n-gram：

```text
TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 6), min_df=2, max_df=0.85)
```

同时做正则 token：

```text
标准号 token
英文缩写 token
数值单位 token
中文连续名词短语候选
```

### 4.4 输出文件

`runs/schema_design/term_frequency.xlsx`

工作表：

```text
top_char_ngrams：字符 n-gram 高频短语
top_tfidf_terms：TF-IDF 关键词
regex_tokens：正则识别 token
per_section_terms：每章关键词
candidate_object_terms：候选对象词
candidate_noise_terms：候选噪声词
```

字段：

```text
term
freq
doc_freq
tfidf_score
term_type_guess
sample_chunk_id
sample_context
review_decision
schema_candidate
```

### 4.5 人工审核规则

每个高频词标记为：

```text
ENTITY：可做实体
RELATION_TRIGGER：关系触发词
ATTRIBUTE：适合做属性
NOISE：噪声
ALIAS_CANDIDATE：疑似 OCR/别名
UNSURE：需要看更多上下文
```

只有 `ENTITY` 和部分 `ATTRIBUTE` 进入 schema 候选。`RELATION_TRIGGER` 进入关系和 prompt。`NOISE` 进入过滤规则。

## 5. 阶段五：主题聚类和文档主旨

### 5.1 目标

确定文档主要讨论哪些主题，避免 schema 只覆盖局部章节。

### 5.2 工具

```text
scikit-learn TfidfVectorizer
scikit-learn KMeans 或 MiniBatchKMeans
每个 cluster 抽 top terms 和代表 chunk
LLM 对 cluster 摘要命名
```

### 5.3 推荐参数

```text
cluster_count = min(12, max(4, chunk_count // 20))
向量：char n-gram TF-IDF + regex token
每个 cluster 取 top 10 terms
每个 cluster 取 3 个代表 chunk
```

### 5.4 输出文件

`runs/schema_design/topic_clusters.md`

每个主题包含：

```text
cluster_id
chunk_count
top_terms
representative_chunks
LLM_summary
schema_implication
```

示例：

```text
Cluster 3：试验和检测要求
Top terms：试验、绝缘电阻、介质强度、振动、盐雾、检验
Schema implication：需要 TestItem、TechnicalParameter、EnvironmentalCondition；关系需要 HAS_TEST_METHOD、HAS_TEST_CONDITION。
```

## 6. 阶段六：候选 schema 生成

### 6.1 目标

用工具统计结果生成第一版 schema，不直接人工从零写。

### 6.2 输入给 LLM 的内容

只给压缩后的证据：

```text
文档主题摘要
top 50 对象词
top 50 参数/数值模式
top 50 编号/引用模式
top 30 关系触发词
主题聚类摘要
代表性 chunk 10 到 20 个
噪声词候选
OCR alias 候选
```

### 6.3 LLM 任务模板

```text
你是知识图谱 schema 设计专家。请基于以下工具统计结果，设计候选 schema。

要求：
1. 实体类型必须来自高频对象、稳定编号、参数、来源对象或业务关键概念。
2. 不要把普通动词、形容词、泛化词设计成实体。
3. 关系类型必须由关系触发词或样本文本支持。
4. 每个实体类型给出 good examples 和 bad examples。
5. 每个关系类型给出 source_types、target_types、触发词和 bad examples。
6. 标出容易混淆的类型。
7. 标出建议过滤的实体。
8. 输出 YAML 草案。
```

### 6.4 输出文件

```text
runs/schema_design/candidate_schema.yaml
runs/schema_design/candidate_schema_review.md
```

`candidate_schema_review.md` 必须包含：

```text
每个实体类型的来源证据
每个关系类型的来源证据
哪些类型来自高频词
哪些类型来自正则模式
哪些类型来自主题聚类
哪些类型只是 LLM 推断，需要人工确认
```

## 7. 阶段七：人工审核 schema

### 7.1 审核工具

```text
Excel / Markdown 审核表
schemas/*.yaml
graphiti_rag/schema_loader.py 校验
```

### 7.2 审核问题

每个实体类型必须回答：

```text
是否是稳定对象？
是否会作为关系端点？
是否会被用户查询？
是否有跨 chunk 去重价值？
是否有 good/bad examples？
是否会导致大量垃圾实体？
```

每个关系类型必须回答：

```text
文本里是否有明确触发词？
source_types 和 target_types 是否清楚？
是否太细导致抽不稳？
是否太泛导致没语义？
是否和其他关系重叠？
```

### 7.3 通过门槛

```text
每个实体类型至少 3 个 good examples 和 3 个 bad examples
每个关系类型至少 2 个真实文本样例
关系类型总数第一版控制在 8 到 18 个
实体类型总数第一版控制在 6 到 15 个
默认不启用泛滥的 RELATED_TO，除非有明确兜底策略
```

## 8. 阶段八：生成 prompt 规则

### 8.1 目标

把工具统计结果和 schema 审核结果转成抽取 prompt 的稳定规则。

### 8.2 工具

```text
候选 schema YAML
term_frequency.xlsx
pattern_inventory.xlsx
人工审核表
LLM 生成 prompt 草案
人工确认后写入抽取指令或 schema description
```

### 8.3 prompt 必须包含

实体 prompt：

```text
实体类型定义
每类 good examples
每类 bad examples
高频核心对象
高频参数/数值模式
高频编号模式
常见 OCR alias
不要抽的噪声
类型混淆规则
```

关系 prompt：

```text
关系类型定义
source_types / target_types
触发词
每类 good examples
每类 bad examples
端点必须来自实体列表
不能发明端点
如果端点不存在，宁可不输出
```

## 9. 阶段九：小样本试跑

### 9.1 样本选择

不要随机抽。固定选覆盖面样本：

```text
目录 chunk：2 个
定义章节：3 个
主体技术要求：5 到 10 个
表格 chunk：3 到 5 个
引用文件章节：2 个
组织/人员章节：2 个
OCR 差的 chunk：3 个
长句密集 chunk：3 个
编号密集 chunk：3 个
```

总量建议 20 到 50 个 chunk。

### 9.2 输出文件

```text
runs/schema_design/sample_extraction/entities.jsonl
runs/schema_design/sample_extraction/edges.jsonl
runs/schema_design/sample_extraction/rejected_entities.jsonl
runs/schema_design/sample_extraction/rejected_edges.jsonl
runs/schema_design/sample_extraction/sample_quality_report.md
```

### 9.3 必看指标

```text
实体总数
每类实体数量
关系总数
每类关系数量
zero-degree 实体数量和占比
entity-not-found 数量和原因
rejected_entities reason 分布
rejected_edges reason 分布
同名异类型实体数量
平均每 chunk 实体数
平均每 chunk 边数
关系类型覆盖率
```

### 9.4 通过门槛

第一版 schema 小样本建议门槛：

```text
Entity fallback 占比 <= 15%
zero-degree 实体占比 <= 25%
entity-not-found 边占比 <= 10%
关系类型覆盖率 >= 60%
每个核心实体类型至少出现 3 个样例
每个核心关系类型至少出现 1 个样例
明显垃圾实体占比 <= 10%
```

不达标就不要全量跑。

## 10. 阶段十：基于质量报告修正

### 10.1 修正优先级

按这个顺序修：

```text
1. 文本/OCR 问题
2. schema 类型边界
3. prompt good/bad examples
4. alias
5. 过滤规则
6. rejection ledger 二抽反馈
7. 关系粒度
```

### 10.2 常见问题处理表

```text
大量普通名词进图
=> 实体 bad examples 不够，增加过滤规则。

大量 Entity fallback
=> schema 类型不清楚，补类型定义和分类规则。

大量章节号 zero-degree
=> Section 太细，过滤深层章节号，或只保留有标题/有关系章节。

大量 entity-not-found
=> 实体名和边端点不一致，补 official_name、synonyms、alias、rejected edge 二抽。

关系类型覆盖低
=> 关系类型太细或 prompt 没有触发词，合并关系或补触发词。

某关系异常膨胀
=> 关系定义太宽，补 bad examples 和端点约束。

同名异类型多
=> 增加类型 disambiguation 规则，必要时用属性区分。

OCR near miss 高频
=> 进入 alias 审核表，人工确认后加入 alias。
```

## 11. 阶段十一：全量抽取前检查

全量抽取前必须确认：

```text
文本抽取质量达标
chunks.jsonl 完整
candidate_schema.yaml 已人工审核
prompt 规则已更新
alias 已审核
过滤规则已审核
小样本质量报告通过门槛
schema_loader 校验通过
```

建议命令：

```bash
uv run pytest --noconftest tests/test_schema_loader.py tests/test_gbt_schema.py
uv run python ingest_gbt.py --dry-run --sample runs/schema_design/sample_chunks.jsonl
```

如果当前项目没有 `--dry-run`，应优先补 dry-run，而不是直接跑全量。

## 12. 阶段十二：全量抽取后复盘

全量抽取后生成：

```text
runs/schema_design/final_quality_report.md
runs/schema_design/final_entity_distribution.xlsx
runs/schema_design/final_edge_distribution.xlsx
runs/schema_design/final_rejected_entities.xlsx
runs/schema_design/final_rejected_edges.xlsx
runs/schema_design/final_alias_candidates.xlsx
```

必须回答：

```text
哪些实体类型最多？是否合理？
哪些关系类型最多？是否异常膨胀？
哪些类型没有被抽到？
zero-degree 剩余是什么？
rejected reason Top 10 是什么？
alias 是否需要新增？
schema 是否需要拆分或合并类型？
```

## 13. 固定目录结构

建议每次 schema 设计都使用同一目录结构：

```text
runs/schema_design/
  pages.jsonl
  page_quality.jsonl
  page_quality_after_ocr.md
  ocr_suspects.xlsx
  alias_candidates.xlsx
  chunks.jsonl
  corpus_profile.md
  pattern_inventory.xlsx
  term_frequency.xlsx
  topic_clusters.md
  candidate_schema.yaml
  candidate_schema_review.md
  prompt_rules.md
  sample_chunks.jsonl
  sample_extraction/
    entities.jsonl
    edges.jsonl
    rejected_entities.jsonl
    rejected_edges.jsonl
    sample_quality_report.md
  final_quality_report.md
```

这样下一种文本可以复用同一套工具和判断逻辑。

## 14. 必须实现的脚本接口

为了让流程真正落地，建议固定实现以下脚本。脚本名可以调整，但输入输出契约不要变。

### 14.1 extract_text.py

用途：把原始文档转换成 `pages.jsonl`，同时生成按页质量报告。OCR 只对问题页触发。

```bash
uv run python tools/schema_profile/extract_text.py \
  --input data/raw \
  --output runs/schema_design/pages.jsonl \
  --quality-output runs/schema_design/page_quality.jsonl \
  --engine auto \
  --ocr-mode selective
```

`--engine` 可选：

```text
auto：先文本层质量检测，再按页决定是否 OCR
pymupdf：优先文本层，保留页面块和基础坐标
pdfplumber：优先表格、单词坐标和版面信息
docling：复杂文档转换，适合多格式或结构复杂 PDF
unstructured：多格式 partition，适合已有 unstructured 工作流
ocr：强制 OCR，仅用于确认整本文本层不可用的扫描件
```

`--ocr-mode` 可选：

```text
none：不跑 OCR，只输出质量报告
selective：只 OCR needs_ocr=true 的页面，默认推荐
full：全量 OCR，仅当扫描页比例很高时使用
```

`page_quality.jsonl` 每行必须包含：

```json
{
  "doc_id": "GBT25338",
  "page": 12,
  "char_count": 1380,
  "cjk_ratio": 0.72,
  "garbled_ratio": 0.01,
  "cid_count": 0,
  "image_count": 0,
  "table_count": 2,
  "needs_ocr": false,
  "ocr_applied": false,
  "final_source": "text_layer"
}
```

`pages.jsonl` 每行必须包含 provenance：

```json
{
  "doc_id": "GBT25338",
  "page": 12,
  "text": "最终使用文本",
  "source": "text_layer|ocr|text+ocr",
  "extractor": "pymupdf|pdfplumber|docling|unstructured|paddleocr",
  "quality": {...}
}
```

### 14.2 build_chunks.py

用途：清洗文本、识别章节、切 chunk。

```bash
uv run python tools/schema_profile/build_chunks.py \
  --pages runs/schema_design/pages.jsonl \
  --output runs/schema_design/chunks.jsonl \
  --max-chars 1800 \
  --min-chars 400
```

### 14.3 profile_patterns.py

用途：用正则抓稳定模式。

```bash
uv run python tools/schema_profile/profile_patterns.py \
  --chunks runs/schema_design/chunks.jsonl \
  --output runs/schema_design/pattern_inventory.xlsx
```

必须输出工作表：

```text
standards
sections
numeric_values
ratings
dates
money
organizations
relation_triggers
ocr_suspects
```

### 14.4 profile_terms.py

用途：统计高频词、短语、TF-IDF、候选对象词。

```bash
uv run python tools/schema_profile/profile_terms.py \
  --chunks runs/schema_design/chunks.jsonl \
  --patterns runs/schema_design/pattern_inventory.xlsx \
  --output runs/schema_design/term_frequency.xlsx
```

必须同时跑：

```text
jieba tokens
char n-gram
regex tokens
per-section terms
rapidfuzz alias candidates
```

### 14.5 cluster_topics.py

用途：主题聚类，确定文档主旨和子主题。

```bash
uv run python tools/schema_profile/cluster_topics.py \
  --chunks runs/schema_design/chunks.jsonl \
  --terms runs/schema_design/term_frequency.xlsx \
  --output runs/schema_design/topic_clusters.md
```

默认用 TF-IDF + KMeans；如果安装了 sentence-transformers，可以加 `--embedding-model`。

### 14.6 draft_schema.py

用途：把统计结果压缩后交给 LLM，生成候选 schema。

```bash
uv run python tools/schema_profile/draft_schema.py \
  --patterns runs/schema_design/pattern_inventory.xlsx \
  --terms runs/schema_design/term_frequency.xlsx \
  --topics runs/schema_design/topic_clusters.md \
  --sample-chunks runs/schema_design/sample_chunks.jsonl \
  --output runs/schema_design/candidate_schema.yaml \
  --review runs/schema_design/candidate_schema_review.md
```

### 14.7 run_sample_extraction.py

用途：固定样本试跑。

```bash
uv run python tools/schema_profile/run_sample_extraction.py \
  --schema runs/schema_design/candidate_schema.yaml \
  --chunks runs/schema_design/sample_chunks.jsonl \
  --output runs/schema_design/sample_extraction
```

### 14.8 analyze_extraction_quality.py

用途：生成质量报告，决定是否进入全量。

```bash
uv run python tools/schema_profile/analyze_extraction_quality.py \
  --entities runs/schema_design/sample_extraction/entities.jsonl \
  --edges runs/schema_design/sample_extraction/edges.jsonl \
  --rejected-entities runs/schema_design/sample_extraction/rejected_entities.jsonl \
  --rejected-edges runs/schema_design/sample_extraction/rejected_edges.jsonl \
  --output runs/schema_design/sample_extraction/sample_quality_report.md
```

质量报告必须给出结论：

```text
PASS：可以进入全量
FIX_SCHEMA：先修 schema
FIX_PROMPT：先修 prompt
FIX_ALIAS：先修 alias
FIX_TEXT_EXTRACTION：先修文本/OCR
```

## 15. 最小可落地版本

如果时间有限，至少做这 7 步：

```text
1. PyMuPDF/pdfplumber 抽 pages.jsonl，并生成 page_quality.jsonl
2. 只对质量差页面选择性 OCR，合并后复检
3. re + Counter 生成 pattern_inventory.xlsx
4. sklearn char n-gram + jieba 生成 term_frequency.xlsx
5. LLM 基于统计结果生成 candidate_schema.yaml
6. 选 20 个 chunk 小样本抽取并生成 sample_quality_report.md
7. 根据 rejected/zero-degree/entity-not-found 修 schema、prompt、alias
```

这就是最低可用闭环。

## 16. 工具资料依据

工具选型基于以下官方资料和项目定位：

```text
PyMuPDF：用于 PDF 文本抽取、页面对象、布局和渲染。
官方文档：https://pymupdf.readthedocs.io/en/latest/recipes-text.html

Docling：用于多格式文档转换，支持统一文档表示和 PDF 等格式。
官方文档：https://docling-project.github.io/docling/

Unstructured：用于多格式 partition，适合已有 unstructured 文档处理工作流。
官方文档：https://docs.unstructured.io/open-source/core-functionality/partitioning

PaddleOCR：用于中文 OCR、复杂版面、扫描文档和表格场景。
官方文档：https://www.paddleocr.ai/main/en/index/index.html
```

这些工具不是全部必装。标准版优先轻量可操作；只有当文本抽取质量、表格结构、扫描件或多格式输入不达标时，才引入增强工具。

## 17. 最终执行原则

固定顺序：

```text
先工具统计
再 LLM 归纳
再人工审核
再小样本验证
再质量报告修正
最后全量抽取
```

不要跳过工具统计直接写 schema。不要跳过小样本直接全量跑。不要让 LLM 的输出直接入库，必须经过规则校验和 rejection ledger。
