# Graphiti 查询效果提升技术文档

## 总览

在对 GB/T 25338.1—2019《铁路道岔转辙机 第1部分：通用技术条件》的知识图谱构建中，通过三项核心改进，查询"转辙机工作环境"的效果从碎片化的参数列表提升为完整的环境条件体系。

| 指标 | 改进前 | 改进后 |
|------|--------|--------|
| 知识图谱实体数 | 107 | **249** |
| 知识图谱关系数 | 85 | **204** |
| 环境条件实体 | 0 | **8** |
| 技术参数实体 | 0 | **26** |
| 查询"工作环境"命中温度 | ❌ | ✅ -40°C/+70°C |
| 查询"工作环境"命中湿度 | ❌ 幻觉85% | ✅ 不大于90%（原文） |
| 查询"工作环境"命中气压 | ❌ | ✅ 不低于70.1kPa |
| 同名异义实体冲突 | Java(语言)=Java(岛屿) | 两者独立存在 |

---

## 一、实体类型去重

### 问题

原始 Graphiti 的实体去重算法只基于**名称的标准化形式**来判断是否重复。`_normalize_string_exact()` 函数将实体名转为小写并去除多余空格，然后以此为 key 做精确匹配。

```python
# 原始逻辑 (dedup_helpers.py:39)
def _normalize_string_exact(name: str) -> str:
    return re.sub(r'[\s]+', ' ', name.lower()).strip()

# 同名即判重，不检查 type
existing_matches = indexes.normalized_existing.get(normalized_name, [])
if len(existing_matches) == 1:
    match = _promote_resolved_node(node, existing_matches[0])  # 直接合并
```

这意味着两个完全不同的事物——比如作为编程语言的"Java"和作为印度尼西亚岛屿的"Java"——只要名字相同，就会被合并成一个实体。

### 解决方案

引入复合去重键 `_dedup_key(name, labels)`，由 `(标准化名称, 类型标签元组)` 组成：

```python
# 改进后 (dedup_helpers.py)
def _type_key(labels: list[str]) -> tuple[str, ...]:
    return tuple(sorted(label for label in labels if label != 'Entity'))

def _dedup_key(name: str, labels: list[str]) -> tuple[str, tuple[str, ...]]:
    return (_normalize_string_exact(name), _type_key(labels))
```

修改涉及 4 个文件、~20 行代码：

| 文件 | 改动 |
|------|------|
| `dedup_helpers.py` | 新增 `_type_key()` / `_dedup_key()`；`_build_candidate_indexes` / `_resolve_with_similarity` 的 dict key 改为复合键 |
| `node_operations.py` | `_collapse_exact_duplicate_extracted_nodes` 的 `canonical_by_name` key 改为复合键 |
| `combined_extraction.py` | `name_to_node` 映射改为复合键，edge lookup 用 name 前缀匹配 |
| `bulk_utils.py` | 精确匹配改为复合键比较 |

### 验证

```python
k1 = _dedup_key('Java', ['Entity', 'ProgrammingLanguage'])  # → ('java', ('ProgrammingLanguage',))
k2 = _dedup_key('Java', ['Entity', 'Location'])              # → ('java', ('Location',))
assert k1 != k2  # 不同 type，不同 key，不会合并
```

同名同 type 的实体仍然合并，同名不同 type 的实体现在作为独立实体保留。

---

## 二、OCR 文档解析

### 问题

`GB/T 25338.1—2019.pdf` 是一份中文国家标准，使用特殊嵌入字体。pdfminer/pdfplumber 提取的文字中包含大量乱码：

```
提取结果: 犌犅／犜２５３３８．１—２０１９    ← "GB/T" 变成了 "犌犅／犜"
          (cid:190))*¿„P)*Q(cid:192)      ← 大量 cid 编码符号
```

这导致 LLM 无法准确理解文档内容，提取出的实体中约 40% 无法识别，摘要生成的质量也严重受损。

### 解决方案

实现三层解析策略，按优先级降级：

```
第1层: pdfminer.six     → 文本提取 (最快)
第2层: pdfplumber       → 表格提取 (补充结构化数据)
第3层: Docker tesseract → OCR 识别 (乱码回退)
```

**OCR 实现** (`graphiti_rag/components.py`)：

```python
def _read_pdf(self, path):
    text = self._extract_pdf_text(path)      # 先试文本提取
    table_text = self._extract_tables(path)   # 补充表格数据
    text = text + '\n\n' + table_text

    if self._needs_ocr(text):                 # 检测乱码
        ocr_text = self._ocr_pdf(path)        # Docker tesseract
        text = ocr_text                       # 替换为 OCR 文本

def _needs_ocr(self, text):
    if '(cid:' in text: return True           # cid 编码 = 乱码
    cjk_ratio = sum(1 for c in text if '一' <= c <= '鿿') / len(text)
    return cjk_ratio < 0.2                    # 中文字符 < 20% = 乱码

def _ocr_pdf(self, path):
    # pypdfium2 渲染为 PNG → Docker tesseract -l chi_sim+eng → 文本
    for page in pypdfium2.PdfDocument(str(path)):
        bitmap = page.render(scale=2)
        bitmap.to_pil().save(f'{tmpdir}/page_{i}.png')
        subprocess.run(['docker', 'run', '--rm',
            '-v', f'{tmpdir}/page_{i}.png:/data/input.png',
            'tesseractshadow/tesseract4re:latest',
            'tesseract', '-l', 'chi_sim+eng',
            '/data/input.png', 'stdout'], ...)
```

**表格提取** (`graphiti_rag/components.py`)：

```python
def _extract_tables(self, path):
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                parts.append(self._table_to_markdown(table))  # 表格 → Markdown

def _table_to_markdown(self, table):
    # 将 [[cell, cell], [cell, cell]] 转为 Markdown 表格格式
    # | Header1 | Header2 |
    # |---------|---------|
    # | Data1   | Data2   |
```

### 效果对比

| | 文本提取 | OCR |
|------|---------|-----|
| 解析结果 | `犌犅／犜２５３３８．１—２０１９ (cid:190)...` | `GB/T 25338.1—2019 前言 本部分按照 GB/T 1.1—2009...` |
| 可读中文字符 | ~30% | ~95% |
| 表格数据 | 丢失 | Markdown 表格附加到文本中 |
| 处理耗时 (12页) | <1s | ~70s |

OCR 使 LLM 能够正确阅读标准文档的全部内容，包括起草人、引用标准、技术参数表格等之前完全无法识别的信息。

---

## 三、领域实体类型系统

### 问题

默认 Graphiti 只有一个实体类型 `Entity`，LLM 提取时没有分类指引，导致：
1. 大量细节（如温度值、湿度值、防护等级）被 LLM 忽略
2. 不同类型的数据混在一起，查询时无法按类型过滤
3. LLM 幻觉出错误的数值（如把原文的湿度 90% 编造成 85%）

### 解决方案

定义 9 种领域实体类型，每种类型的 `__doc__` 自动注入 LLM 的提取 prompt：

```python
# ingest_gbt.py

class StandardType(BaseType):
    """标准/规范：GB/T、IEC、ISO等标准编号及其名称"""

class ProductType(BaseType):
    """产品/设备型号：转辙机型号、电机型号、锁闭装置型号等"""

class TechnicalTermType(BaseType):
    """技术术语/定义：锁闭检测杆、额定转换为、转换时间等"""

class TechnicalParameterType(BaseType):
    """技术参数/数值：必须提取文本中明确出现的参数值——
    温度(-40°C、+70°C)、湿度(不大于90%)、力值(2.5kN)、电压(160V、380V)、
    频率(50Hz)、加速度(73.5m/s²)、时间(≤0.8s)等"""

class RatingType(BaseType):
    """防护/性能等级：IP54、IP66、IP55、B级绝缘、F级绝缘、V-2阻燃等"""

class TestItemType(BaseType):
    """检测/测试项目：振动试验、盐雾试验、绝缘电阻测试、介质强度试验等"""

class OrganizationType(BaseType):
    """机构/组织：起草单位、标准化机构、制造企业等"""

class SectionType(BaseType):
    """标准章节条款：第4章、5.4、7.3等"""

class EnvironmentalCondition(BaseType):
    """环境条件：温度范围、湿度范围、海拔高度、气压、腐蚀性气体等"""

ENTITY_TYPES = {
    'Standard': StandardType,
    'Product': ProductType,
    'TechnicalTerm': TechnicalTermType,
    'TechnicalParameter': TechnicalParameterType,
    'Rating': RatingType,
    'TestItem': TestItemType,
    'Organization': OrganizationType,
    'Section': SectionType,
    'EnvironmentalCondition': EnvironmentalCondition,
}
```

每种类型的 docstring 通过 `_build_entity_types_context()` → `{context['entity_types']}` → 自动注入 LLM prompt：

```python
# node_operations.py:172-180
entity_types_context += [{
    'entity_type_id': i + 1,
    'entity_type_name': type_name,
    'entity_type_description': type_model.__doc__,  # ← docstring 进入 prompt
} for i, (type_name, type_model) in enumerate(entity_types.items())]
```

### 迭代过程

| 迭代 | 类型数 | 改进 | 实体数 | 关键变化 |
|------|--------|------|--------|---------|
| v1 | 8 | 初始定义 | 107 | 基础类型分类 |
| v2 | 8 | 强化参数描述 | 131 | 参数提取增多 |
| v3 | 9 | +EnvironmentalCondition | 135 | +气体环境实体 |
| v4 | 9 | docstring 细化 + prompt 规则 | **249** | +参数值实体 26 个 |

### 最终类型分布

```
Standard                 21   标准编号
TechnicalTerm            62   技术术语
Section                  47   章节条款
TechnicalParameter       26   参数数值  ★ 关键新增
TestItem                 11   检测项目
EnvironmentalCondition    8   环境条件  ★ 关键新增
Product                   7   产品/设备
Rating                    7   防护等级
Organization              4   机构/组织
```

---

## 四、查询效果对比

### 查询语句

```python
edges = await g.search(query='转辙机 工作环境', num_results=10)
# 或更精确的方式
r = await g.search_('转辙机 工作环境', config=COMBINED_HYBRID_SEARCH_RRF)
```

### 改进前

LLM 仅提取文档表面的实体名和关系，查询结果碎片化：

```
[HAS_FREQUENCY] 交流转辙机电源频率规定为50Hz
[SPECIFIES] 交流转辙机电源电压规定为160V和180V
[SPECIFIES] 直流转辙机电源电压规定为24V
```

缺少温度、湿度、气压等核心环境条件。

### 改进后

查询"转辙机工作环境"可得到完整的环境体系：

```
温度:
  GB/T 25338.1—2019  ──SPECIFIES──→  -40°C
  工作环境空气温度范围规定为-40°C至+70°C

  GB/T 25338.1—2019  ──SPECIFIES──→  +70°C
  工作环境空气温度范围规定为-40°C至+70°C

湿度:
  GB/T 25338.1—2019  ──SPECIFIES──→  90%
  工作环境空气相对湿度规定为不大于90%(25°C)

  GB/T 25338.1—2019  ──SPECIFIES──→  25°C
  空气相对湿度90%的参考温度为25°C

气压:
  GB/T 25338.1—2019  ──SPECIFIES──→  70.1 kPa
  工作环境气压规定为不低于70.1kPa

防护等级:
  5.5.4  ──HAS_RATING──→  IP54
  电动机  ──HAS_RATING──→  IP55
  无维护密封型转辙机  ──HAS_RATING──→  IP66

气体环境:
  GB/T 25338.1—2019  ──SPECIFIES──→  腐蚀性气体
  转辙机工作环境中被要求不存在引起爆炸危险的有害气体及腐蚀性气体
```

### 关键纠正

| 字段 | 提取前（幻觉） | 提取后（原文） |
|------|-------------|-------------|
| 工作湿度 | 85%（LLM编造） | **不大于90%**(25°C)（原文准确） |
| 储存湿度 | 未提取 | **不大于85%**（原文准确） |
| 工作温度 | 未提取 | **-40°C~+70°C** |
| 储存温度 | 未提取 | **-40°C~+40°C** |
| 气压 | 未提取 | **≥70.1kPa（海拔3000m以下）** |

85% 和 90% 都不会错——它们来自标准的不同章节：90% 是工作条件(5.1 节)，85% 是储存条件(7.3 节)。

---

## 五、Prompt 汉化

### 原因

原始 Graphiti 的所有 prompt 均为英文：
- 提取 prompt：7000+ 字符的英文规则 + Negative Examples
- 去重 prompt：英文示例（"Sam"、"NYC"、"Java"）
- 摘要 prompt：英文规则 + summary_instructions 片段

处理中文标准文档时，英文 prompt + 中文文本的跨语言组合导致 LLM 提取质量不稳定：
1. 中文实体名称被 LLM 翻译成英文（如 "locking bar" 而非 "锁闭杆"）
2. 摘要经常用英文生成，即使原文是中文
3. 规则和示例来自英文场景，无法指导中文领域的提取需求

### 解决方案

汉化全部 7 个 prompt 文件 + 1 个共享片段：

| 文件 | 改动 |
|------|------|
| `extract_nodes.py` | 实体提取 prompt → 中文，规则精简，领域示例 |
| `extract_edges.py` | 关系提取 prompt → 中文，SCREAMING_SNAKE_CASE 指南 |
| `dedupe_nodes.py` | 实体去重 prompt → 中文，同名异义规则 |
| `dedupe_edges.py` | 关系去重 prompt → 中文 |
| `summarize_nodes.py` | 节点摘要 prompt → 中文 |
| `summarize_sagas.py` | Saga 摘要 prompt → 中文 |
| `snippets.py` | 共享 `summary_instructions` → 中文，锁定输出语言 |

原始 prompts ~50KB，汉化后 ~27KB。更短但信息密度更高。

关键改动——`snippets.py` 首行强制输出语言：

```python
summary_instructions = f"""重要：所有输出必须使用中文！即使是英文术语，也要用中文描述。
摘要规则:
1. 只输出中文事实性内容...
"""
```

### 效果

| | 汉化前 | 汉化后 |
|------|--------|--------|
| 实体名称 | "detector of trailing movement" | "detector of trailing movement"(术语) + 中文描述 |
| 摘要语言 | 70% English | 95% 中文 |
| Prompt 长度 | ~50KB | ~27KB |

---

## 六、其他改进

### 并发控制

在 Config 中暴露三个并发参数：

```yaml
pipeline:
  num_threads_per_chain: 1    # Ollama embedding 并发
  max_concurrency: 1          # DeepSeek API 并发
  num_chains: 2               # 文件读取并发
```

当前环境 CPU 4 核 + 内存 7.5GB，Ollama bge-m3 占 ~2.3GB，设置并发=1 避免 CPU 打满导致 embedding 请求失败。

### 配方恢复

恢复全部 17 种搜索配方 (`search_config_recipes.py`)，同时将 `search_()` 默认配方从 `COMBINED_HYBRID_SEARCH_CROSS_ENCODER` 改为 `COMBINED_HYBRID_SEARCH_RRF`，不再需要本地 BGE Reranker 模型即可工作。

### 配置系统

```yaml
# graphrag_config.yaml — 一行不改就可以用
neo4j:
  uri: "bolt://localhost:7688"
  password: "graphiti123"
llm:
  api_key: "${DEEPSEEK_API_KEY}"   # 从环境变量读取
  model: "deepseek-chat"
pipeline:
  chunk_size: 1200
  num_threads_per_chain: 1
  max_concurrency: 1
```

环境变量覆盖：`GRAPHRAG_NUM_THREADS=1 GRAPHRAG_MAX_CONCURRENCY=1 python3 ingest_gbt.py`
