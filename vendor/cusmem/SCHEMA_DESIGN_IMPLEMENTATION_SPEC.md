# Schema 设计自动化：实现规格补充

> 本文档是 `SCHEMA_DESIGN_EXECUTION_PLAYBOOK.md` 的配套实现规格。  
> Playbook 说了"做什么"，本文档说"怎么做"——精确到可以直接写代码的粒度。

---

## 0. 程序总入口

### 0.1 命令行接口

```bash
uv run python -m tools.schema_design \
  --input data/raw/ \                    # 输入目录（PDF/Word/HTML/纯文本）
  --output runs/schema_design/ \          # 输出目录
  --llm api_key=$DEEPSEEK_API_KEY \       # LLM 配置
  --llm base_url=https://api.deepseek.com \
  --llm model=deepseek-chat \
  --mode auto \                           # auto | interactive（需要人工审核时暂停）
  --skip-stages ""                        # 可跳过已完成的阶段，如 "1,2"
```

### 0.2 主控制器伪代码

```python
class SchemaDesignPipeline:
    """编排 12 个阶段，管理状态和人工审核点。"""
    
    def __init__(self, input_path: Path, output_dir: Path, llm_cfg: LLMConfig, mode: str):
        self.input = input_path
        self.out = output_dir
        self.llm = llm_cfg
        self.mode = mode  # 'auto' | 'interactive'
        self.state = PipelineState.load(output_dir / 'pipeline_state.json')
    
    def run(self) -> dict:
        # 13 个阶段与 Playbook 的对应关系：
        #   Playbook 1-6  → Stage1-6   (文本→统计→schema 草案)
        #   Playbook 7    → Stage7     (人工审核)
        #   Playbook 8    → Stage8     (生成 prompt)
        #   Playbook 9    → Stage9     (小样本试跑)
        #   Playbook 10   → Stage10    (质量修正)
        #   Playbook 11   → Stage11    (全量前检查)
        #   Playbook 12   → Stage12+13 (全量抽取 + 复盘)
        # Playbook 的"全量抽取后复盘"在实现中拆为 Stage13 独立阶段。
        stages = [
            Stage1_TextExtraction(self),
            Stage2_CleaningAndChunking(self),
            Stage3_PatternRecognition(self),
            Stage4_TermFrequency(self),
            Stage5_TopicClustering(self),
            Stage6_SchemaGeneration(self),
            Stage7_HumanReview(self),       # 人工审核点
            Stage8_PromptGeneration(self),
            Stage9_SampleExtraction(self),
            Stage10_QualityFix(self),
            Stage11_PreflightCheck(self),
            Stage12_FullExtraction(self),
            Stage13_FinalQualityReport(self),
        ]
        for stage in stages:
            if self.state.is_completed(stage.name):
                continue
            result = stage.execute()
            self.state.mark_completed(stage.name, result)
            if stage.requires_human_review and self.mode == 'interactive':
                self._wait_for_human_approval(stage)
        self._emit_schema_config()
        return self._emit_final_config()
    
    def _emit_schema_config(self) -> dict:
        """输出主产物 schema_config.yaml：可直接被 graphiti_rag/schema_loader.py 加载。"""
        schema = self.state.get('candidate_schema')
        write_yaml(self.out / 'schema_config.yaml', schema)
        return schema

    def _emit_final_config(self) -> dict:
        """输出附属包装配置 final_config.yaml，引用 schema_config.yaml 并携带 prompt/filter/alignment。"""
        return {
            'schema': {'path': str(self.out / 'schema_config.yaml'), 'mode': 'strict'},
            'prompts': self.state.get('prompt_rules'),
            'entity_alignment': self.state.get('entity_alignment'),
            'filters': self.state.get('filters'),
            'quality_report': self.state.get('final_quality_report'),
        }


class PipelineState:
    """持久化每个阶段的完成状态和输出产物路径。"""
    
    def __init__(self, state_path: Path):
        self.path = state_path
        self.data = self._load()  # {stage_name: {'completed': bool, 'outputs': {...}, 'hash': str}}
    
    def is_completed(self, stage_name: str) -> bool:
        """检查阶段是否已完成。用输入文件 hash 判断是否需要重跑。"""
        entry = self.data.get(stage_name, {})
        if not entry.get('completed'):
            return False
        # 检查该阶段的输入产物是否被后续阶段修改
        return self._inputs_unchanged(stage_name, entry.get('input_hashes', {}))
    
    def mark_completed(self, stage_name: str, result: dict):
        self.data[stage_name] = {
            'completed': True,
            'outputs': result.get('output_files', {}),
            'metrics': result.get('metrics', {}),
            'input_hashes': self._compute_input_hashes(stage_name),
            'timestamp': datetime.now().isoformat(),
        }
        self._save()
```

### 0.3 状态文件 schema (`pipeline_state.json`)

```json
{
  "version": "1.0",
  "pipeline_run_id": "run_20260608_143000",
  "input_path": "data/raw/GBT+25338.1-2019.pdf",
  "stages": {
    "stage1_text_extraction": {
      "completed": true,
      "outputs": {
        "pages_jsonl": "runs/schema_design/pages.jsonl",
        "page_quality_jsonl": "runs/schema_design/page_quality.jsonl"
      },
      "metrics": {
        "page_count": 60,
        "empty_page_ratio": 0.03,
        "avg_chars_per_page": 1380,
        "garbled_ratio": 0.01,
        "ocr_pages": 0
      },
      "input_hashes": {
        "input_file": "sha256:abc123..."
      },
      "timestamp": "2026-06-08T14:30:00"
    }
  }
}
```

---

## 阶段一：文本抽取（精确规格）

### 1.1 函数签名

```python
def extract_text(
    input_path: Path,
    output_dir: Path,
    engine: str = 'auto',          # 'auto' | 'pymupdf' | 'pdfplumber' | 'docling' | 'unstructured'
    ocr_mode: str = 'selective',   # 'none' | 'selective' | 'full'
    ocr_engine: str = 'paddleocr', # 'paddleocr' | 'tesseract'
) -> ExtractTextResult:
    ...
```

### 1.2 分步骤算法

#### Step 1：打开文档，按页抽取文本层

```python
def _extract_text_layer_pymupdf(pdf_path: Path) -> list[PageText]:
    """用 PyMuPDF 逐页抽取文本。返回每页的文本、块、坐标信息。"""
    import fitz  # pymupdf
    doc = fitz.open(str(pdf_path))
    pages = []
    for i, page in enumerate(doc):
        # get_text("dict") 返回按块组织的文本，保留坐标
        text_dict = page.get_text("dict")
        blocks = text_dict.get("blocks", [])
        
        # 提取纯文本（按阅读顺序）
        text = page.get_text("text")
        
        # 统计字符
        char_count = len(text.strip())
        cjk_chars = sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
        cjk_ratio = cjk_chars / max(char_count, 1)
        
        # 检测乱码：cid 标记、控制字符、异常符号
        cid_count = text.count('(cid:')
        garbled_chars = sum(1 for c in text if _is_garbled_char(c))
        garbled_ratio = garbled_chars / max(char_count, 1)
        
        # 统计对象
        image_count = len(page.get_images())
        line_count = len(text.strip().split('\n'))
        
        pages.append(PageText(
            page_number=i + 1,
            text=text,
            char_count=char_count,
            cjk_ratio=cjk_ratio,
            cid_count=cid_count,
            garbled_ratio=garbled_ratio,
            image_count=image_count,
            line_count=line_count,
            blocks=blocks,
        ))
    doc.close()
    return pages


def _is_garbled_char(c: str) -> bool:
    """判断是否为乱码/异常字符。"""
    # Unicode 私有区
    if 0xE000 <= ord(c) <= 0xF8FF:
        return True
    # 替换字符
    if c == '�':
        return True
    # 控制字符（保留 tab/newline）
    if ord(c) < 0x20 and c not in ('\t', '\n', '\r'):
        return True
    # 孤立的组合标记
    if 0x0300 <= ord(c) <= 0x036F:
        return True
    return False
```

#### Step 2：用 pdfplumber 补表格

```python
def _extract_tables_pdfplumber(pdf_path: Path) -> dict[int, list[TableData]]:
    """逐页提取表格，转为 Markdown table 字符串。"""
    import pdfplumber
    tables_by_page = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages):
            page_tables = []
            for table in page.extract_tables():
                if not table or len(table) < 2:
                    continue  # 跳过多余短表
                markdown = _table_to_markdown(table)
                page_tables.append(TableData(
                    markdown=markdown,
                    row_count=len(table),
                    col_count=len(table[0]) if table else 0,
                ))
            if page_tables:
                tables_by_page[i + 1] = page_tables
    return tables_by_page


def _table_to_markdown(table: list[list[str | None]]) -> str:
    """将二维数组转为 Markdown 表格字符串。"""
    rows = []
    for i, row in enumerate(table):
        cells = [cell.strip() if cell else '' for cell in row]
        rows.append('| ' + ' | '.join(cells) + ' |')
        if i == 0:
            rows.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
    return '\n'.join(rows)
```

#### Step 3：按页质量评分

```python
def _score_page_quality(page: PageText, tables: list[TableData] | None) -> PageQuality:
    """对单页质量打分，决定是否需要 OCR。"""
    reasons = []
    needs_ocr = False
    
    # 规则 1：字符数太少
    if page.char_count < 80:
        reasons.append('low_char_count')
        needs_ocr = True
    
    # 规则 2：有 cid 标记（文本层损坏）
    if page.cid_count > 0:
        reasons.append('cid_present')
        needs_ocr = True
    
    # 规则 3：中文文档但中文字符比例过低
    if page.cjk_ratio < 0.2 and page.char_count > 80:
        reasons.append('low_cjk_ratio')
        needs_ocr = True
    
    # 规则 4：乱码比例高
    if page.garbled_ratio > 0.05:
        reasons.append('high_garbled_ratio')
        needs_ocr = True
    
    # 规则 5：主要是图片
    if page.image_count > 3 and page.char_count < 200:
        reasons.append('image_heavy')
        needs_ocr = True
    
    # 规则 6：文本顺序严重错乱
    if _detect_text_order_anomaly(page):
        reasons.append('text_order_anomaly')
        needs_ocr = True
    
    return PageQuality(
        page_number=page.page_number,
        char_count=page.char_count,
        cjk_ratio=page.cjk_ratio,
        garbled_ratio=page.garbled_ratio,
        cid_count=page.cid_count,
        image_count=page.image_count,
        line_count=page.line_count,
        table_count=len(tables) if tables else 0,
        needs_ocr=needs_ocr,
        reasons=reasons,
    )


def _detect_text_order_anomaly(page: PageText) -> bool:
    """检测文本阅读顺序是否异常。
    
    启发式：如果行号与块坐标的 y 顺序不一致，
    或者文本中出现大量错位的章节号，判定为异常。
    """
    if not page.blocks:
        return False
    # 简化：检查 blocks 的 y 坐标是否单调递增
    y_positions = [b['bbox'][1] for b in page.blocks if 'bbox' in b and len(b['bbox']) >= 4]
    if len(y_positions) < 3:
        return False
    # 逆序对比例 > 30% 则判定异常
    inversions = sum(1 for i in range(len(y_positions) - 1) if y_positions[i] > y_positions[i + 1])
    return inversions / len(y_positions) > 0.3
```

#### Step 4：选择性 OCR

```python
def _run_selective_ocr(
    pdf_path: Path,
    quality_by_page: dict[int, PageQuality],
    text_pages: dict[int, PageText],
    ocr_engine: str,
) -> dict[int, OcrResult]:
    """只对 needs_ocr=true 的页面执行 OCR。"""
    ocr_results = {}
    
    # 判断是否需要进入批量 OCR 模式
    total = len(quality_by_page)
    needs_ocr_count = sum(1 for q in quality_by_page.values() if q.needs_ocr)
    
    if needs_ocr_count == 0:
        return ocr_results
    
    for page_num, quality in quality_by_page.items():
        if not quality.needs_ocr:
            continue
        
        if ocr_engine == 'paddleocr':
            ocr_text = _ocr_page_paddleocr(pdf_path, page_num)
        elif ocr_engine == 'tesseract':
            ocr_text = _ocr_page_tesseract(pdf_path, page_num)
        else:
            raise ValueError(f"Unknown OCR engine: {ocr_engine}")
        
        ocr_quality = _score_ocr_quality(ocr_text)
        ocr_results[page_num] = OcrResult(
            page_number=page_num,
            text=ocr_text,
            engine=ocr_engine,
            quality=ocr_quality,
        )
    
    return ocr_results


def _ocr_page_paddleocr(pdf_path: Path, page_num: int) -> str:
    """用 PaddleOCR 对单个 PDF 页面执行 OCR。
    
    步骤：
    1. 用 pypdfium2 或 PyMuPDF 渲染页面为 300 DPI 图片
    2. 调用 PaddleOCR 识别
    3. 按阅读顺序拼接文本行
    """
    from paddleocr import PaddleOCR
    import fitz
    
    ocr = PaddleOCR(lang='ch', use_angle_cls=True, show_log=False)
    doc = fitz.open(str(pdf_path))
    page = doc[page_num - 1]
    
    # 渲染为图片（300 DPI）
    mat = fitz.Matrix(300 / 72, 300 / 72)
    pix = page.get_pixmap(matrix=mat)
    img_data = pix.tobytes("png")
    
    # OCR 识别
    result = ocr.ocr(img_data, cls=True)
    doc.close()
    
    if not result or not result[0]:
        return ''
    
    # 按 y 坐标排序后拼接
    lines = []
    for line in result[0]:
        text = line[1][0]
        confidence = line[1][1]
        bbox = line[0]
        lines.append((bbox[0][1], text, confidence))  # y 坐标用于排序
    
    lines.sort(key=lambda x: (x[0], x[1]))  # 按 y 坐标、再按文本排序
    return '\n'.join(text for _, text, _ in lines)


def _ocr_page_tesseract(pdf_path: Path, page_num: int) -> str:
    """用 Tesseract 对单个 PDF 页面执行 OCR。
    
    当前项目已使用 Docker 镜像 tesseractshadow/tesseract4re:latest。
    保留此路径作为轻量兜底。
    """
    import subprocess
    import fitz
    
    doc = fitz.open(str(pdf_path))
    page = doc[page_num - 1]
    pix = page.get_pixmap(dpi=300)
    
    # 写临时文件
    tmp_img = Path(f'/tmp/ocr_page_{page_num}.png')
    pix.save(str(tmp_img))
    
    # 调用 tesseract
    result = subprocess.run(
        ['tesseract', str(tmp_img), 'stdout', '-l', 'chi_sim+eng'],
        capture_output=True, text=True, timeout=60,
    )
    doc.close()
    tmp_img.unlink(missing_ok=True)
    return result.stdout


def _score_ocr_quality(text: str) -> OcrQuality:
    """对 OCR 输出文本做质量评分，用于后续合并决策。"""
    char_count = len(text.strip())
    if char_count == 0:
        return OcrQuality(char_count=0, cjk_ratio=0, garbled_ratio=1.0, usable=False)
    cjk_chars = sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
    cjk_ratio = cjk_chars / max(char_count, 1)
    garbled_chars = sum(1 for c in text if _is_garbled_char(c))
    garbled_ratio = garbled_chars / max(char_count, 1)
    usable = char_count >= 80 and garbled_ratio <= 0.10
    return OcrQuality(
        char_count=char_count,
        cjk_ratio=cjk_ratio,
        garbled_ratio=garbled_ratio,
        usable=usable,
    )
```

#### Step 5：文本层与 OCR 合并

```python
def _merge_text_and_ocr(
    page_num: int,
    text_page: PageText,
    ocr_result: OcrResult | None,
    table_data: list[TableData] | None,
) -> FinalPage:
    """按规则合并文本层和 OCR 结果，保留 provenance。"""
    
    # 规则 1：没有 OCR 结果，直接使用文本层
    if ocr_result is None:
        final_text = text_page.text
        source = 'text_layer'
    
    # 规则 2：OCR 结果显著短于文本层（< 50%），拒绝 OCR
    elif len(ocr_result.text) < len(text_page.text) * 0.5:
        final_text = text_page.text
        source = 'text_layer'  # OCR rejected
    
    # 规则 3：文本层质量问题，用 OCR 替换
    elif text_page.garbled_ratio > 0.05 or text_page.cid_count > 0:
        final_text = ocr_result.text
        source = 'ocr'
    
    # 规则 4：两者各有优势，合并（OCR 补充文本层缺失部分）
    else:
        final_text = text_page.text  # 默认保留文本层
        source = 'text_layer'  # OCR 作为补充但不替换
    
    # 追加表格
    if table_data:
        table_md = '\n\n'.join(t.markdown for t in table_data)
        final_text = final_text + '\n\n' + table_md
    
    return FinalPage(
        doc_id='',  # 由调用者填充
        page=page_num,
        text=final_text,
        source=source,
        char_count=len(final_text.strip()),
        extractor='pymupdf' if source == 'text_layer' else ocr_result.engine,
        quality=TextQuality(
            cjk_ratio=text_page.cjk_ratio,
            garbled_ratio=text_page.garbled_ratio,
            cid_count=text_page.cid_count,
        ),
    )
```

#### Step 6：OCR 后复检

```python
def _post_ocr_audit(
    pages: list[FinalPage],
    output_dir: Path,
) -> PostOcrAudit:
    """OCR 后重新检测异常短词和高频 near miss。"""
    import re
    from rapidfuzz import fuzz
    
    # 1. 提取所有长度 2-6 的中文片段
    short_chinese = []
    for page in pages:
        for match in re.finditer(r'[一-鿿]{2,6}', page.text):
            short_chinese.append(ShortTerm(
                term=match.group(),
                page=page.page,
                context=_extract_context(page.text, match.start(), match.end()),
            ))
    
    # 2. 统计频次，标记低频 + 形态异常的
    from collections import Counter
    term_freq = Counter(t.short_term for t in short_chinese)
    
    suspects = []
    entity_alignment_candidates = []
    
    for term_info in short_chinese:
        freq = term_freq[term_info.term]
        if freq == 1:
            # 低频 + 含异常标点 → 可疑 OCR 碎片
            if re.search(r'[,，.。!！;；]', term_info.term):
                suspects.append(term_info)
        
        # 3. 找相似词对（编辑距离 1-2 的 term 对）
        for other_term in term_freq:
            if term_info.term >= other_term:
                continue
            sim = fuzz.ratio(term_info.term, other_term)
            if 75 <= sim < 100 and len(term_info.term) >= 3 and len(other_term) >= 3:
                entity_alignment_candidates.append(EntityAlignmentCandidate(
                    term1=term_info.term,
                    term2=other_term,
                    similarity=sim,
                    freq1=term_freq[term_info.term],
                    freq2=term_freq[other_term],
                ))
    
    return PostOcrAudit(suspects=suspects, entity_alignment_candidates=entity_alignment_candidates)


def _extract_context(text: str, start: int, end: int, window: int = 40) -> str:
    """提取匹配位置周围的上下文。"""
    ctx_start = max(0, start - window)
    ctx_end = min(len(text), end + window)
    prefix = '...' if ctx_start > 0 else ''
    suffix = '...' if ctx_end < len(text) else ''
    return prefix + text[ctx_start:ctx_end] + suffix
```

### 1.3 输出文件精确 Schema

#### `pages.jsonl` (每行)

```json
{
  "doc_id": "GBT25338",
  "page": 12,
  "text": "5.5.7 周围空气温度\n转辙机及外部配套电缆应能...",
  "source": "text_layer",
  "extractor": "pymupdf",
  "char_count": 1380,
  "quality": {
    "cjk_ratio": 0.72,
    "garbled_ratio": 0.01,
    "cid_count": 0
  },
  "tables": [
    {
      "markdown": "| 项目 | 要求 |\n| --- | --- |\n| 绝缘电阻 | ≥25MΩ |",
      "row_count": 3,
      "col_count": 2
    }
  ]
}
```

#### `page_quality.jsonl` (每行)

```json
{
  "doc_id": "GBT25338",
  "page": 12,
  "char_count": 1380,
  "cjk_ratio": 0.72,
  "garbled_ratio": 0.01,
  "cid_count": 0,
  "image_count": 0,
  "line_count": 42,
  "table_count": 2,
  "needs_ocr": false,
  "ocr_applied": false,
  "ocr_engine": null,
  "final_source": "text_layer",
  "reasons": []
}
```

### 1.4 阶段一质量门槛（精确值）

```python
STAGE1_QUALITY_THRESHOLDS = {
    'empty_page_ratio': 0.15,     # 空页比例 ≤ 15%
    'avg_chars_per_page': 300,    # 平均每页 ≥ 300 字符
    'garbled_ratio': 0.05,        # 乱码比例 ≤ 5%
    'needs_ocr_ratio': 0.30,      # 需要 OCR 的页面 ≤ 30%（超了触发警告）
}
```

---

## 阶段二：清洗与 Chunking（精确规格）

### 2.1 函数签名

```python
def build_chunks(
    pages_jsonl: Path,
    output_dir: Path,
    max_chars: int = 1800,
    min_chars: int = 400,
    overlap_chars: int = 100,
) -> BuildChunksResult:
    ...
```

### 2.2 分步骤算法

#### Step 1：页眉页脚检测

```python
def _detect_headers_footers(pages: list[dict]) -> tuple[set[str], set[str]]:
    """统计每页首 2 行和末 2 行，找出重复率 >= 30% 的行。"""
    from collections import Counter
    
    first_lines = Counter()
    last_lines = Counter()
    
    for page in pages:
        lines = page['text'].strip().split('\n')
        if len(lines) >= 2:
            first_lines[lines[0].strip()] += 1
            first_lines[lines[1].strip()] += 1
        if len(lines) >= 4:
            last_lines[lines[-1].strip()] += 1
            last_lines[lines[-2].strip()] += 1
    
    n = len(pages)
    threshold = max(3, n * 0.3)  # 至少 3 页
    
    headers = {line for line, cnt in first_lines.items() if cnt >= threshold and len(line) > 3}
    footers = {line for line, cnt in last_lines.items() if cnt >= threshold and len(line) > 3}
    
    return headers, footers
```

#### Step 2：章节边界检测

```python
import re

# 章节号正则（中文 + 阿拉伯数字）
SECTION_PATTERNS = [
    # 第X章、第X节、第X条
    re.compile(r'^(第\s*[一二三四五六七八九十百0-9]+\s*[章节条])'),
    # 数字章节：1、1.2、1.2.3
    re.compile(r'^(\d+(?:\.\d+){0,4})\s'),
    # 附录A、附录B
    re.compile(r'^(附录\s*[A-ZＡ-Ｚ])'),
    # 前言、引言、范围、规范性引用文件、术语和定义
    re.compile(r'^(前言|引言|范围|规范性引用文件|术语和定义|符号|参考文献)\s*$'),
]

def _find_section_boundaries(text: str) -> list[SectionBoundary]:
    """在文本中定位所有章节边界。返回 (行号, 章节号, 标题) 列表。"""
    boundaries = []
    lines = text.split('\n')
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        for pattern in SECTION_PATTERNS:
            m = pattern.match(line)
            if m:
                section_num = m.group(1)
                # 剩余部分作为标题
                title = line[m.end():].strip()
                boundaries.append(SectionBoundary(
                    line_index=i,
                    section_number=section_num,
                    title=title,
                ))
                break
    return boundaries
```

#### Step 3：按章节优先切分

```python
def _split_by_sections(
    pages: list[dict],
    headers: set[str],
    footers: set[str],
    max_chars: int,
    min_chars: int,
    overlap_chars: int,
) -> list[Chunk]:
    """先按章节切，超长再按长度切。"""
    chunks = []
    chunk_id_counter = 0
    
    for page in pages:
        doc_id = page['doc_id']
        page_num = page['page']
        text = page['text']
        tables = page.get('tables', [])
        
        # 清洗
        text = _clean_text(text, headers, footers)
        
        # 找章节边界
        boundaries = _find_section_boundaries(text)
        
        if not boundaries:
            # 没有章节边界：按长度切
            sub_chunks = _split_by_length(text, max_chars, min_chars, overlap_chars)
        else:
            # 有章节边界：按章节切，超长章节再按长度切
            sub_chunks = []
            for i, boundary in enumerate(boundaries):
                start_line = boundary.line_index
                end_line = boundaries[i + 1].line_index if i + 1 < len(boundaries) else len(text.split('\n'))
                
                lines = text.split('\n')
                section_text = '\n'.join(lines[start_line:end_line])
                
                if len(section_text) <= max_chars:
                    sub_chunks.append((section_text, boundary.section_number, boundary.title))
                else:
                    # 超长章节再按长度切
                    length_chunks = _split_by_length(section_text, max_chars, min_chars, overlap_chars)
                    for j, lc in enumerate(length_chunks):
                        label = f"{boundary.section_number}.{j + 1}" if boundary.section_number else ''
                        sub_chunks.append((lc, label, boundary.title))
        
        # 为每个 sub-chunk 分配元数据
        section_path = _compute_section_path(boundaries)
        for text_segment, section_label, section_title in sub_chunks:
            is_toc = _is_table_of_contents(text_segment)
            is_table = _contains_table(text_segment, tables)
            
            chunks.append(Chunk(
                doc_id=doc_id,
                chunk_id=f"{doc_id}-p{page_num:02d}-c{chunk_id_counter:03d}",
                page_start=page_num,
                page_end=page_num,
                section_path=section_path,
                section_title=section_title,
                text=text_segment,
                char_count=len(text_segment),
                is_table=is_table,
                is_toc=is_toc,
            ))
            chunk_id_counter += 1
    
    return chunks


def _clean_text(text: str, headers: set[str], footers: set[str]) -> str:
    """清洗文本：去页眉页脚、统一空白、统一符号。"""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # 跳过页眉页脚
        if stripped in headers or stripped in footers:
            continue
        # 跳过纯空白
        if not stripped:
            cleaned.append('')
            continue
        # 统一全角/半角
        stripped = _normalize_punctuation(stripped)
        cleaned.append(stripped)
    # 合并连续空行
    return '\n'.join(_collapse_empty_lines(cleaned))


def _normalize_punctuation(text: str) -> str:
    """统一全角/半角符号。"""
    replacements = {
        '，': ',', '。': '.', '！': '!', '？': '?',
        '（': '(', '）': ')', '：': ':', '；': ';',
        '＂': '"', '＇': "'", '　': ' ',
        '–': '-', '—': '--',  # en-dash, em-dash
        '‘': "'", '’': "'",   # 弯引号
        '“': '"', '”': '"',
    }
    for full, half in replacements.items():
        text = text.replace(full, half)
    return text


def _collapse_empty_lines(lines: list[str]) -> list[str]:
    """合并连续空行：最多保留 1 个空行。"""
    result = []
    prev_empty = False
    for line in lines:
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue
        result.append(line)
        prev_empty = is_empty
    return result


def _split_by_length(text: str, max_chars: int, min_chars: int, overlap: int) -> list[str]:
    """按长度切分文本。优先在段落/句子边界处切。"""
    if len(text) <= max_chars:
        return [text] if len(text) >= min_chars else []
    
    chunks = []
    pos = 0
    while pos < len(text):
        end = min(pos + max_chars, len(text))
        
        if end < len(text):
            # 在窗口内找最佳切分点（段落边界 > 句号 > 换行）
            search_start = max(pos + min_chars, end - 200)
            cut = _find_best_cut_point(text, search_start, end)
            if cut:
                end = cut
        
        chunk = text[pos:end].strip()
        if len(chunk) >= min_chars:
            chunks.append(chunk)
        
        pos = end - overlap if end < len(text) else end
    
    return chunks


def _find_best_cut_point(text: str, start: int, end: int) -> int | None:
    """在 [start, end) 范围内找到最佳切分点。优先级：空行 > 句号 > 分号 > 逗号 > 空格。"""
    search = text[start:end]
    # 优先级 1：段落边界（两个连续换行）
    for pos in range(len(search) - 1, -1, -1):
        if search[pos:pos + 2] == '\n\n':
            return start + pos + 1
    # 优先级 2：句号/问号/感叹号
    for pos in range(len(search) - 1, -1, -1):
        if search[pos] in '。.!！?？':
            return start + pos + 1
    # 优先级 3：分号
    for pos in range(len(search) - 1, -1, -1):
        if search[pos] in ';；':
            return start + pos + 1
    # 优先级 4：换行
    for pos in range(len(search) - 1, -1, -1):
        if search[pos] == '\n':
            return start + pos + 1
    return None


def _is_table_of_contents(text: str) -> bool:
    """判断是否是目录页。启发式：有密集的点线 + 页码。"""
    dotted_line_pattern = re.compile(r'\.{4,}|\…{2,}')
    page_num_pattern = re.compile(r'\d{1,3}\s*$', re.MULTILINE)
    
    lines = text.strip().split('\n')
    dotted_count = sum(1 for l in lines if dotted_line_pattern.search(l))
    page_ref_count = sum(1 for l in lines if page_num_pattern.search(l))
    
    return dotted_count >= 5 or (page_ref_count >= 5 and len(lines) <= 80)


def _contains_table(text: str, tables: list[dict]) -> bool:
    """判断文本 chunk 是否包含表格。"""
    if not tables:
        return False
    for table in tables:
        if table['markdown'] in text:
            return True
    return False


def _compute_section_path(boundaries: list[SectionBoundary]) -> list[str]:
    """计算章节层级路径。"""
    return [b.section_number for b in boundaries if b.section_number]
```

### 2.3 输出文件精确 Schema

#### `chunks.jsonl` (每行)

```json
{
  "doc_id": "GBT25338",
  "chunk_id": "GBT25338-p12-c003",
  "page_start": 12,
  "page_end": 14,
  "section_path": ["5", "5.5", "5.5.7"],
  "section_title": "周围空气温度",
  "text": "5.5.7 周围空气温度\n转辙机及外部配套电缆应能在...",
  "char_count": 1520,
  "is_table": false,
  "is_toc": false
}
```

### 2.4 阶段二质量门槛

```python
STAGE2_QUALITY_THRESHOLDS = {
    'avg_chunk_chars': (800, 1800),   # 平均 chunk 长度在此区间
    'short_chunk_ratio': 0.20,        # < 400 字符的 chunk 比例 ≤ 20%
    'section_path_coverage': 0.60,    # 有章节路径的 chunk 比例 ≥ 60%
    'toc_chunks': 'skip',             # 目录 chunk 跳过（不参与后续抽取）
}
```

---

## 阶段三：稳定模式识别（精确规格）

### 3.1 函数签名

```python
def profile_patterns(
    chunks_jsonl: Path,
    output_dir: Path,
) -> PatternInventory:
    ...
```

### 3.2 精确正则模式

```python
# 每一组正则都必须返回 (matched_value, match_start, match_end) 的列表。
# 每个匹配项都要提取上下文（前后各 60 字符）。

PATTERNS = {
    'standards': {
        'patterns': [
            # GB/T 25338.1-2019, ISO 9001:2015, IEC 60529
            re.compile(
                r'\b(?:GB|GB/T|GB/Z|ISO|IEC|EN|ASTM|TB|JT|YD|IEEE|'
                r'DL|JB|QB|HG|SH|SY|NB|CJJ|CJ|JG|JGJ)\s*/?\s*[A-Z0-9.\-—]+'
            ),
            # 标准号后带年份：25338.1-2019
            re.compile(r'\b\d{4,6}(?:\.\d+){0,2}[—\-]\d{2,4}\b'),
        ],
        'exclude_patterns': [
            # 不是标准号的 4 位数字
            re.compile(r'^\d{4}$'),  # 纯年份
        ],
    },
    
    'sections': {
        'patterns': [
            # 第X章、第X节
            re.compile(r'第\s*[一二三四五六七八九十百0-9]+\s*[章节条]'),
            # 数字多级：5.5.7、A.1.2
            re.compile(r'\b(?:[A-Z]|\d+)(?:\.\d+){1,4}\b'),
            # 附录
            re.compile(r'附录\s*[A-ZＡ-Ｚ]'),
        ],
    },
    
    'numeric_values': {
        'patterns': [
            # 带单位的数值（核心模式）
            re.compile(
                r'[<>≤≥=]?[\-−]?\d+(?:\.\d+)?\s*'
                r'(?:℃|°C|V|kV|mV|A|mA|μA|Hz|kHz|MHz|'
                r'N|kN|MN|mN|Pa|kPa|MPa|GPa|'
                r'mm|cm|m|km|μm|nm|'
                r's|ms|min|h|'
                r'次|%|Ω|MΩ|kΩ|mΩ|'
                r'W|kW|MW|mW|'
                r'g|kg|t|mg|'
                r'L|mL|'
                r'r/min|rpm|m/s|m/s²|mm/s|'
                r'dB|dBm|dBμV|'
                r'N·m|N·m/s)'
            ),
            # 范围：170 ± 5、170 ~ 200
            re.compile(r'\d+(?:\.\d+)?\s*[±~～]\s*\d+(?:\.\d+)?'),
            # 百分比：≥90%
            re.compile(r'[<>≤≥]\s*\d+(?:\.\d+)?\s*%'),
            # 纯数字（在表格中可能是参数值）
            re.compile(r'^\d+(?:\.\d+)?$', re.MULTILINE),
        ],
    },
    
    'ratings': {
        'patterns': [
            # IP 等级
            re.compile(r'\bIP\s*\d{2}[A-Z]?\b'),
            # X 级
            re.compile(r'[A-Z]\s*级'),
            # V-0, V-1
            re.compile(r'\bV-?\d\b'),
            # 绝缘等级
            re.compile(r'(绝缘等级|防护等级|防火等级|防爆等级)\s*[：:]\s*[A-Z0-9]+'),
        ],
    },
    
    'dates': {
        'patterns': [
            # 中文日期
            re.compile(r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日'),
            # ISO 日期
            re.compile(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}'),
            # 发布日期：2019-05-10
            re.compile(r'(?:发布|实施|实施|废止|生效)日期\s*[：:]\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}'),
        ],
    },
    
    'organizations': {
        'patterns': [
            re.compile(
                r'[一-鿿A-Za-z0-9（）()]{2,30}'
                r'(?:公司|研究院|委员会|协会|中心|大学|集团|部门|机构|实验室|'
                r'设计院|工程局|铁路局|标准化技术委员会)'
            ),
        ],
    },
    
    'persons': {
        'patterns': [
            # 主要起草人：张三、李四
            re.compile(r'(?:主要起草人|起草人|主审|审核|批准)\s*[：:]\s*([^。\n]{5,200})'),
        ],
    },
    
    'relation_triggers': {
        'patterns': [
            # 规定类
            re.compile(r'(规定|确定|明确|限定|界定|约定|指定)'),
            # 引用类
            re.compile(r'(引用|参见|参照|依照|按照|根据|依据|遵循|符合|满足)'),
            # 替代类
            re.compile(r'(替代|代替|取代|废除|废止)'),
            # 等同类
            re.compile(r'(等同|等效|等效于|修改采用|非等效)'),
            # 提出/归口/起草
            re.compile(r'(提出|归口|起草|参编|主编|负责起草)'),
            # 应有/不应
            re.compile(r'(应符合|应满足|应不低于|应不超过|应达到|不应低于|不应超过|不宜)'),
            # 进行/执行
            re.compile(r'(按[^。]{0,20}进行|按[^。]{0,20}执行|按[^。]{0,20}试验)'),
            # 组成/包含
            re.compile(r'(由[^。]{0,30}组成|包括|包含|含|分为|分成)'),
            # 适用于
            re.compile(r'(适用于|不适用于|用于|应用于)'),
        ],
    },
}

def _extract_patterns(chunks: list[dict], pattern_name: str) -> list[PatternMatch]:
    """对给定的 chunk 列表运行一组正则，返回所有匹配项。
    
    返回字段：value, count, sample_chunk_id, sample_context, page_start, 
              match_start, match_end, pattern_used, is_excluded
    """
    from collections import Counter
    
    config = PATTERNS[pattern_name]
    include_patterns = config['patterns']
    exclude_patterns = config.get('exclude_patterns', [])
    
    matches = []
    seen_values = Counter()
    
    for chunk in chunks:
        text = chunk['text']
        for pat in include_patterns:
            for m in pat.finditer(text):
                value = m.group(0).strip()
                
                # 检查是否应排除
                excluded = any(ep.match(value) for ep in exclude_patterns)
                
                # 提取上下文
                ctx_start = max(0, m.start() - 60)
                ctx_end = min(len(text), m.end() + 60)
                context = text[ctx_start:ctx_end]
                
                seen_values[value] += 1
                
                matches.append(PatternMatch(
                    value=value,
                    sample_chunk_id=chunk['chunk_id'],
                    sample_context=context,
                    page_start=chunk['page_start'],
                    match_start=m.start(),
                    match_end=m.end(),
                    pattern_used=pat.pattern[:80],  # 前 80 字符用于调试
                    is_excluded=excluded,
                ))
    
    # 按频次降序排列，去重保留频次
    unique_matches = []
    seen = set()
    for m in sorted(matches, key=lambda m: (-seen_values[m.value], m.value)):
        if m.value in seen:
            continue
        seen.add(m.value)
        m.count = seen_values[m.value]
        unique_matches.append(m)
    
    return unique_matches
```

### 3.3 输出 Excel 精确列定义

```python
# pattern_inventory.xlsx — 每个工作表共享以下列
PATTERN_INVENTORY_COLUMNS = [
    ('A', 'value', 'str', '匹配到的值'),
    ('B', 'count', 'int', '在全文档中出现的次数'),
    ('C', 'sample_chunk_id', 'str', '示例 chunk ID'),
    ('D', 'sample_context', 'str', '前后各 60 字符的上下文'),
    ('E', 'page_start', 'int', '起始页码'),
    ('F', 'match_start', 'int', '匹配位置（字符偏移）'),
    ('G', 'pattern_used', 'str', '命中的正则表达式（前 80 字符）'),
    ('H', 'is_excluded', 'bool', '是否被排除规则命中'),
    ('I', 'review_decision', 'str', '人工审核标记（初始为空）'),
]

PATTERN_SHEET_NAMES = [
    'standards', 'sections', 'numeric_values', 'ratings',
    'dates', 'organizations', 'persons', 'relation_triggers',
    'ocr_suspects',  # 从 post_ocr_audit 填充
]
```

---

## 阶段四：高频词和短语统计（精确规格）

### 4.1 函数签名

```python
def profile_terms(
    chunks_jsonl: Path,
    pattern_inventory_xlsx: Path,
    output_dir: Path,
    ngram_range: tuple[int, int] = (2, 6),
    min_df: int = 2,
    max_df: float = 0.85,
    top_n: int = 500,
) -> TermFrequencyResult:
    ...
```

### 4.2 精确算法

```python
import jieba
from sklearn.feature_extraction.text import TfidfVectorizer
from collections import Counter

def _extract_token_frequencies(chunks: list[dict]) -> dict:
    """从所有 chunk 中提取多种 token 的频次统计。"""
    
    # 1. jieba 分词 token
    jieba_tokens = Counter()
    for chunk in chunks:
        words = jieba.cut(chunk['text'])
        for w in words:
            w = w.strip()
            if len(w) >= 2 and not _is_stopword(w):
                jieba_tokens[w] += 1
    
    # 2. char n-gram（捕获专业短语）
    all_texts = [chunk['text'] for chunk in chunks]
    vectorizer = TfidfVectorizer(
        analyzer='char_wb',
        ngram_range=(2, 6),
        min_df=2,
        max_df=0.85,
        token_pattern=None,  # char_wb 不需要
    )
    tfidf_matrix = vectorizer.fit_transform(all_texts)
    feature_names = vectorizer.get_feature_names_out()
    
    # 取 top TF-IDF terms
    tfidf_scores = {}
    for i, term in enumerate(feature_names):
        col = tfidf_matrix.getcol(i)
        tfidf_scores[term] = float(col.sum())
    
    # 3. 正则 token（调用阶段三的结果，不再重复提取）
    # 4. 每章关键词（按 section_path 分组）
    section_terms = _extract_per_section_terms(chunks, vectorizer)
    
    return {
        'jieba_tokens': jieba_tokens,
        'char_ngrams': tfidf_scores,
        'section_terms': section_terms,
    }


def _is_stopword(word: str) -> bool:
    """常见中文停用词。"""
    STOPWORDS = {
        '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
        '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着',
        '没有', '看', '好', '自己', '这', '他', '她', '它', '们', '那', '些',
        '所', '为', '所以', '因为', '但是', '然而', '而且', '或者', '如果',
        '虽然', '可以', '这个', '那个', '什么', '怎么', '哪', '为什么',
        '该', '其', '中', '之', '与', '及', '或', '对', '从', '被', '把',
        '让', '向', '以', '等', '等等', '其他', '其它', '各', '每', '某',
        '本', '此', '者', '啊', '吧', '吗', '呢', '嗯', '哦',
        '应', '应能', '不应', '不应', '不宜', '可', '不可', '不能',
        '当', '当时', '当前', '将', '已', '已经', '曾', '曾经',
        '最大', '最小', '至少', '最多', '不小于', '不大于',
        '符合', '满足', '要求', '规定', '按', '按照', '根据', '遵照',
        '通常', '一般', '正常', '特殊', '尤其',
        '标准', '本文件', '本部分', '本标准', '本规范', '本文',
        '包括', '包含', '但', '除', '除非', '除外',
    }
    return word in STOPWORDS or len(word) < 2


def _classify_term(
    term: str,
    freq: int,
    tfidf_score: float,
    pattern_type: str | None,
    is_numeric: bool,
    is_chinese: bool,
) -> str:
    """自动分类一个 term：ENTITY / RELATION_TRIGGER / ATTRIBUTE / NOISE / UNSURE。

    注意：ENTITY_ALIGNMENT_CANDIDATE 不由本函数产出——它由 OCR 后复检阶段
    的 rapidfuzz 相似词对生成，直接写入 entity_alignment_candidates sheet。
    
    分类规则（优先级从高到低）：
    1. 纯数字/数值模式 + 无中文 → NOISE（除非有单位，则可能是 ATTRIBUTE）
    2. 匹配关系触发正则 → RELATION_TRIGGER
    3. 中文名词短语 + 高频 + 无触发词特征 → ENTITY
    4. 中文名词短语 + 低频 + 形态异常 → NOISE
    5. 英文缩写 + 高频 → ENTITY
    6. 不确定 → UNSURE
    """
    # 规则 1：纯数值/单位
    if is_numeric and not is_chinese:
        return 'ATTRIBUTE'  # 参数值，作为属性而非实体
    
    # 规则 2：关系触发词
    if pattern_type == 'relation_triggers':
        return 'RELATION_TRIGGER'
    
    # 规则 3：高频中文名词短语 → 候选实体
    if is_chinese and freq >= 3 and len(term) >= 2:
        # 排除纯动词和形容词
        if not _looks_like_noun(term):
            return 'ATTRIBUTE'
        return 'ENTITY'
    
    # 规则 4：低频短词
    if freq < 3 and len(term) <= 3:
        return 'NOISE'
    
    # 规则 5：英文缩写
    if not is_chinese and len(term) <= 10 and freq >= 3:
        return 'ENTITY'
    
    return 'UNSURE'


def _looks_like_noun(term: str) -> bool:
    """启发性判断 term 是否像名词短语。"""
    # 以常见名词后缀结尾
    noun_suffixes = [
        '机', '器', '器', '件', '装置', '设备', '系统', '机构',
        '电', '压', '流', '力', '度', '率', '值', '量', '数',
        '法', '方式', '方法', '条件', '要求', '规则', '标准', '规范',
        '试验', '检验', '测试', '测量', '检测',
        '器', '仪', '表', '计', '阀', '门', '杆', '轴', '轮',
    ]
    return any(term.endswith(s) for s in noun_suffixes)
```

### 4.3 输出 Excel 精确列定义

```python
TERM_FREQUENCY_SHEETS = {
    'top_char_ngrams': [
        ('A', 'term', 'str'),
        ('B', 'freq', 'int'),
        ('C', 'doc_freq', 'int'),
        ('D', 'tfidf_score', 'float'),
        ('E', 'term_type_guess', 'str'),       # ENTITY|RELATION_TRIGGER|ATTRIBUTE|NOISE|ENTITY_ALIGNMENT_CANDIDATE|UNSURE
        ('F', 'sample_chunk_id', 'str'),
        ('G', 'sample_context', 'str'),
        ('H', 'review_decision', 'str'),        # 人工审核：确认/修改分类
        ('I', 'schema_candidate', 'str'),        # 候选 schema 类型名
    ],
    'top_tfidf_terms': '同上列结构',
    'regex_tokens': '同上列结构 + pattern_type 列',
    'per_section_terms': [
        ('A', 'section_path', 'str'),
        ('B', 'term', 'str'),
        ('C', 'section_freq', 'int'),
        ('D', 'global_freq', 'int'),
        ('E', 'tfidf_score', 'float'),
        ('F', 'is_section_specific', 'bool'),    # 仅在本章节高频、其他章节低频
    ],
    'candidate_object_terms': '同 top_char_ngrams',
    'candidate_noise_terms': [
        ('A', 'term', 'str'),
        ('B', 'freq', 'int'),
        ('C', 'reason', 'str'),                  # low_freq|abnormal_chars|too_short|verb|adj
        ('D', 'sample_context', 'str'),
        ('E', 'review_decision', 'str'),          # 确认噪声/恢复为实体
    ],
    'entity_alignment_candidates': [
        ('A', 'term1', 'str'),
        ('B', 'term2', 'str'),
        ('C', 'similarity', 'float'),             # rapidfuzz ratio: 0-100
        ('D', 'freq1', 'int'),
        ('E', 'freq2', 'int'),
        ('F', 'sample_context_1', 'str'),
        ('G', 'sample_context_2', 'str'),
        ('H', 'review_decision', 'str'),           # confirm_same_entity|not_same_entity|text_recognition_variant
        ('I', 'official_name', 'str'),             # 如果确认同一实体，写规范名称；不强制改写原文 name
        ('J', 'synonyms', 'list[str]'),            # 写入节点 attributes，用于实体对齐
    ],
}
```

---

## 阶段五：主题聚类（精确规格）

### 5.1 函数签名

```python
def cluster_topics(
    chunks_jsonl: Path,
    term_frequency_xlsx: Path,
    output_dir: Path,
    cluster_count: int | None = None,
    embedding_model: str | None = None,  # 增强版：sentence-transformers
) -> TopicClusters:
    ...
```

### 5.2 精确算法

```python
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import MiniBatchKMeans
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

def _cluster_chunks(
    chunks: list[dict],
    cluster_count: int | None,
    terms: dict,  # 阶段四的输出
) -> TopicClusters:
    """对 chunk 做主题聚类。"""
    
    # 确定 cluster 数量
    if cluster_count is None:
        n = len(chunks)
        cluster_count = max(4, min(12, n // 20))
    
    # 向量化：char n-gram TF-IDF + 正则 token 加权
    texts = [c['text'] for c in chunks]
    vectorizer = TfidfVectorizer(
        analyzer='char_wb',
        ngram_range=(2, 5),
        min_df=2,
        max_df=0.85,
        max_features=5000,
    )
    X = vectorizer.fit_transform(texts)
    
    # 聚类
    kmeans = MiniBatchKMeans(
        n_clusters=cluster_count,
        random_state=42,
        n_init=3,
        batch_size=100,
    )
    labels = kmeans.fit_predict(X)
    
    # 对每个 cluster：提取 top terms + 代表 chunk
    clusters = []
    for cluster_id in range(cluster_count):
        mask = labels == cluster_id
        cluster_chunks = [chunks[i] for i in range(len(chunks)) if mask[i]]
        
        if not cluster_chunks:
            continue
        
        # Top terms：该 cluster 中 TF-IDF 均值最高的词
        cluster_X = X[mask]
        mean_tfidf = np.array(cluster_X.mean(axis=0)).flatten()
        top_indices = mean_tfidf.argsort()[-15:][::-1]
        top_terms = [vectorizer.get_feature_names_out()[i] for i in top_indices]
        
        # 代表 chunk：选择离 cluster 中心最近的 3 个
        center = kmeans.cluster_centers_[cluster_id]
        distances = cosine_similarity(cluster_X, center.reshape(1, -1)).flatten()
        closest_idx = distances.argsort()[-3:][::-1]
        representative_chunks = [cluster_chunks[i] for i in closest_idx]
        
        clusters.append(ClusterInfo(
            cluster_id=cluster_id,
            chunk_count=len(cluster_chunks),
            top_terms=top_terms,
            representative_chunks=representative_chunks,
        ))
    
    return TopicClusters(clusters=clusters, vectorizer=vectorizer)
```

### 5.3 LLM 摘要 Prompt

```python
TOPIC_SUMMARY_PROMPT = """你是一位领域文档分析专家。请分析以下主题聚类结果，为每个主题命名并推断其对 schema 设计的影响。

## 文档概况
{corpus_overview}

## 主题聚类

{cluster_details}

## 要求

为每个主题提供：
1. **主题名称**：简洁的中文名称（3-8 字）
2. **schema 影响**：这个主题暗示需要哪些实体类型和关系类型

输出 JSON：
```json
{{
  "topics": [
    {{
      "cluster_id": 0,
      "name": "试验和检测要求",
      "summary": "该主题主要讨论产品的试验方法、检测项目和验收标准",
      "schema_implication": {{
        "entity_types": ["TestItem", "TechnicalParameter", "Rating"],
        "edge_types": ["HAS_TEST_METHOD", "HAS_TEST_CONDITION", "SPECIFIES"],
        "rationale": "频繁出现试验项目名称和参数数值"
      }}
    }}
  ]
}}
```
```

---

## 阶段六：候选 Schema 生成（精确规格）

**当前实现**: Stage 6 优先使用 LLM（DeepSeek via OpenAI 兼容 API）根据统计证据生成 entity_types + edge_types。无 LLM 时回退到规则模板。LLM prompt 传入统计证据：模式识别结果、词频/TF-IDF 分析、主题分布；LLM 返回 JSON 格式的 entity_types + edge_types + disambiguations + suggested_filters。

### 6.1 函数签名

```python
def draft_schema(
    pattern_inventory_json: Path,
    term_frequency_json: Path,
    output_dir: Path,
    *,
    llm: LLMClient | None = None,
    topic_md: Path | None = None,
) -> StageResult:
    """Generate candidate schema. Uses LLM when available, falls back to rule-based template."""
    ...
```

LLM 未配置时（`llm=None`）调用 `_build_default_schema()` 生成 3 实体类型 + 2 边类型的模板，用统计证据填充 examples。LLM 可用时调用 `_build_llm_schema()` 将统计证据压缩为 prompt → LLM 返回 JSON → 规范化后写入 `candidate_schema.yaml`。

### 6.2 样本 Chunk 选择算法（未来增强）

```python
def _select_sample_chunks(chunks: list[dict], n: int = 30) -> list[dict]:
    """按覆盖面选样本 chunk，不是随机抽。
    
    固定配额：
    - 目录/前言：2 个
    - 术语和定义章节：3 个
    - 主体技术要求（占比最大）：10 个
    - 表格 chunk：5 个
    - 引用文件章节：2 个
    - 组织/人员信息：2 个
    - 高密度编号段落（章节号/参数值密集）：3 个
    - 文本层质量较差（garbled_ratio > 0.02 或 cjk_ratio < 0.5）：3 个
    """
    selected = []
    used = set()
    
    def _pick(candidates, k, label):
        nonlocal selected, used
        for c in candidates:
            if c['chunk_id'] in used:
                continue
            selected.append(c)
            used.add(c['chunk_id'])
            if len([s for s in selected if s.get('stratum') == label]) >= k:
                break
    
    # 1. 目录 chunk
    toc = [c for c in chunks if c.get('is_toc')]
    _pick(toc, 2, 'toc')
    
    # 2. 术语和定义
    terms = [c for c in chunks if _has_section_keyword(c, ['术语', '定义'])]
    _pick(terms, 3, 'definitions')
    
    # 3. 引用文件
    refs = [c for c in chunks if _has_section_keyword(c, ['规范性引用', '参考文献'])]
    _pick(refs, 2, 'references')
    
    # 4. 组织/人员
    orgs = [c for c in chunks if _has_section_keyword(c, ['起草', '归口', '提出'])]
    _pick(orgs, 2, 'organizations')
    
    # 5. 表格
    tables = [c for c in chunks if c.get('is_table')]
    _pick(tables, 5, 'tables')
    
    # 6. 高编号密度
    dense = [c for c in chunks if _count_pattern_matches(c['text'], r'\d+(?:\.\d+){2,}') >= 3]
    _pick(dense, 3, 'dense_numbering')
    
    # 7. 主体技术要求（填充剩余配额）
    body = [c for c in chunks if not c.get('is_toc') and '技术要求' in c.get('section_title', '')]
    if not body:
        body = [c for c in chunks if c['chunk_id'] not in used]
    remaining = n - len(selected)
    _pick(body, remaining, 'body')
    
    # 为 selected 添加 stratum 标签（在上面的 _pick 中已设置）
    return selected[:n]


def _has_section_keyword(chunk: dict, keywords: list[str]) -> bool:
    text = chunk.get('section_title', '') + ' ' + chunk['text']
    return any(kw in text for kw in keywords)


def _count_pattern_matches(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text))
```

### 6.3 喂给 LLM 的压缩证据格式

```python
def _build_llm_context(
    patterns: PatternInventory,
    terms: TermFrequencyResult,
    topics: TopicClusters,
    sample_chunks: list[dict],
) -> str:
    """将统计证据压缩为 LLM 友好格式。严格控制 token 量。"""
    
    lines = []
    
    # 1. 文档主题摘要（~500 tokens）
    lines.append('## 文档主题')
    for t in topics.llm_summaries:
        lines.append(f"- {t['name']}: {t['summary']}")
    
    # 2. Top 50 对象词（ENTITY 类的 term）(~300 tokens)
    lines.append('\n## Top 50 候选对象词（高频名词短语）')
    entity_terms = [t for t in terms.top_terms if t.term_type_guess == 'ENTITY'][:50]
    for t in entity_terms:
        lines.append(f"- {t.term} (频次: {t.freq})")
    
    # 3. Top 50 参数/数值模式（~300 tokens）
    lines.append('\n## Top 50 参数/数值模式')
    for p in patterns.numeric_values[:50]:
        lines.append(f"- {p.value} (频次: {p.count})")
    
    # 4. Top 30 编号/引用模式
    lines.append('\n## Top 30 编号/引用')
    for p in patterns.standards[:30] + patterns.sections[:30]:
        lines.append(f"- {p.value} (频次: {p.count})")
    
    # 5. Top 30 关系触发词
    lines.append('\n## Top 30 关系触发词')
    for p in patterns.relation_triggers[:30]:
        lines.append(f"- {p.value} (频次: {p.count})")
    
    # 6. 主题聚类摘要
    lines.append('\n## 主题聚类')
    for t in topics.llm_summaries:
        lines.append(f"- [{t['name']}] {t['summary']} (覆盖 {t['chunk_count']} 个 chunk)")
    
    # 7. 代表性 chunk（10-20 个）—— 每个截前 800 字符
    lines.append('\n## 代表性文档片段')
    for i, c in enumerate(sample_chunks[:20]):
        excerpt = c['text'][:800]
        lines.append(f"\n### 片段 {i+1} (页码 {c['page_start']}, 章节 {c.get('section_title', '未知')})")
        lines.append(excerpt)
    
    # 8. 噪声词候选
    noise_terms = [t for t in terms.top_terms if t.term_type_guess == 'NOISE'][:20]
    if noise_terms:
        lines.append('\n## 候选噪声词（建议过滤）')
        for t in noise_terms:
            lines.append(f"- {t.term} (原因: {t.noise_reason})")
    
    # 9. 实体对齐候选（同义词引导候选）
    alignment_candidates = getattr(terms, 'entity_alignment_candidates', [])[:20]
    if alignment_candidates:
        lines.append('\n## 候选同义词对（经人工审核后写入 synonym_guidance）')
        for a in alignment_candidates:
            lines.append(f"- {a.term1} ↔ {a.term2} (相似度: {a.similarity}%, 频次: {a.freq1}/{a.freq2})")
            lines.append(f"  上下文1: {a.context1}")
            lines.append(f"  上下文2: {a.context2}")
    
    return '\n'.join(lines)
```

### 6.4 Schema 生成 LLM Prompt（完整模板）

```python
SCHEMA_DRAFT_PROMPT = """你是知识图谱 schema 设计专家。你的任务是基于**工具统计证据**（而非凭空想象）来设计候选 schema。

## 设计原则

1. 实体类型必须能从高频对象词、稳定编号、参数模式或统计证据中找到支撑
2. 不要把普通动词、形容词、泛化词设计成实体类型
3. 关系类型必须能从关系触发词或样本文本中找到支撑
4. 每个类型都要有来源证据标注
5. 标记出 LLM 推断但没有直接统计证据的类型（需要人工确认）

## 统计证据

{llm_context}

## 输出要求

输出一个 YAML 文件，结构必须直接兼容当前 `graphiti_rag/schema_loader.py`。也就是说，`entity_types` 和 `edge_types` 必须是映射结构，不是列表结构；实体类型名和关系类型名就是 YAML key。`type_id` 不写入 schema 文件，后续由 Graphiti 在运行时为实体类型分配编号。

```yaml
# 元信息：供 schema 设计工具审计使用，schema_loader 会保留在 raw 中
meta:
  generated_from: "tool_statistics"
  total_chunks: {n_chunks}
  cluster_count: {n_clusters}
  evidence_summary: "基于工具统计、主题聚类和样本文本生成"

schema:
  mode: strict
  description: "当前文档集合的知识图谱 schema"

entity_types:
  Standard:
    description: "标准/规范文件，如 GB/T 25338.1-2019、IEC 60529"
    ontology: "标准体系 -> 标准"
    evidence: "高频标准号匹配、规范性引用段落和标题页样本共同支持"
    good_examples:
      - "GB/T 25338.1-2019"
      - "IEC 60529"
      - "GB/T 2828.1-2012"
    bad_examples:
      - "GB/T"
      - "2019"
      - "标准"
    properties:
      official_name:
        type: string
        description: "标准规范名称；如果原文只是简称，可在这里写规范名称"
      standard_number:
        type: string
        description: "标准编号"
      synonyms:
        type: list[string]
        description: "同义词、简称、文本识别变体；用于实体对齐，不用于强制改写 name"

  Product:
    description: "产品、设备、部件或装置"
    ontology: "领域对象 -> 产品设备"
    evidence: "高频名词短语、产品描述段落、组成关系触发词共同支持"
    good_examples:
      - "转辙机"
      - "外锁闭装置"
      - "ZD6 型电动转辙机"
    bad_examples:
      - "产品"
      - "设备"
      - "相关装置"
    properties:
      official_name:
        type: string
        description: "规范产品名称"
      product_type:
        type: string
        description: "产品类别或设备类型"
      synonyms:
        type: list[string]
        description: "同义词、简称、型号简写、文本识别变体"

edge_types:
  REFERENCES:
    description: "标准或规范文件引用另一个标准或规范文件"
    source_types: [Standard]
    target_types: [Standard]
    trigger_words: ["引用", "参见", "按照", "符合", "依据"]
    evidence: "标准号与引用触发词共现"
    good_examples:
      - source: "GB/T 25338.1-2019"
        target: "IEC 60529"
        fact: "文档引用 IEC 60529 的防护等级定义"
    bad_examples:
      - source: "转辙机"
        target: "GB/T 25338.1-2019"
        reason: "产品和标准之间通常不是 REFERENCES，应按语义选择 SPECIFIES 或 HAS_TEST_METHOD"

  SPECIFIES:
    description: "标准或产品规定某项技术参数、环境条件或等级"
    source_types: [Standard, Product]
    target_types: [TechnicalParameter, EnvironmentalCondition, Rating]
    trigger_words: ["规定", "应满足", "应符合", "应达到", "要求"]
    evidence: "规定类触发词与参数值、等级、环境条件共现"
    good_examples:
      - source: "转辙机"
        target: "周围空气温度 -40°C~+70°C"
        fact: "文档在 5.5.7 中规定了转辙机适用的周围空气温度范围；5.5.7 只作为 provenance"
    bad_examples:
      - source: "5.5.7"
        target: "转辙机"
        reason: "章节号、条款号和目录项只作为 provenance/metadata，不作为实体或关系端点"

disambiguations:
  - types: [TechnicalParameter, TechnicalTerm]
    rule: "如果名称包含数值+单位，优先归为 TechnicalParameter；如果是概念性术语，归为 TechnicalTerm"
    examples:
      - name: "动作电流"
        should_be: TechnicalParameter
        because: "在标准中作为参数出现，有具体数值和测量条件"
      - name: "共振频率"
        should_be: TechnicalTerm
        because: "如果文本是在定义概念，而不是给出具体值，就按术语处理"

suggested_filters:
  - filter: "文档结构"
    pattern: "^(第.*[章节条]|[A-Z]?\\d+(?:\\.\\d+)+)$"
    action: "章节号、条款号和目录项只作为 chunk provenance/metadata，不作为实体或关系端点"
    example: "5.5.10, 7.1.3, A.1.2"

  - filter: "编目元数据"
    pattern: "ICS|CCS|发布日期|实施日期"
    action: "在 entity prompt 中明确排除，已经入库的零度节点由 cleanup 删除"

needs_human_review:
  - item: "TechnicalParameter 和 EnvironmentalCondition 的边界"
    issue: "两者都会出现数值和单位，必须通过上下文区分"
    review_rule: "产品固有技术指标归 TechnicalParameter；使用、储存、试验环境归 EnvironmentalCondition"
  - item: "低频但重要的专业部件"
    issue: "低频词不等于噪声，产品部件、测试项目、标准编号需要结合上下文审核"
    review_rule: "如果它会作为关系端点或用户查询对象，保留为实体类型或实体候选"
```

## 附加要求

1. entity_types 总数控制在 6-15 个，edge_types 总数控制在 8-18 个
2. 不要定义泛化的 RELATED_TO 关系（除非作为兜底，且必须标注为 LLM 推断）
3. 每个类型的设计决策都要有"为什么"（evidence 字段）
4. 标注哪些类型是"工具统计直接支持的"、哪些是"LLM 基于统计推断的"
5. 输出完整的 YAML，不要省略、不要写 "..."
"""
```

### 6.5 Schema YAML 校验规则

```python
def validate_candidate_schema(yaml_path: Path) -> SchemaValidationResult:
    """校验候选 schema 的结构合法性。在人工审核前自动执行。

    当前项目的 schema_loader 要求 entity_types / edge_types 是 mapping：
    entity_types.Product.description，而不是 [{name: Product}] 列表。
    """
    import yaml

    with open(yaml_path, encoding="utf-8") as f:
        schema = yaml.safe_load(f) or {}

    errors = []
    warnings = []

    entity_types = schema.get("entity_types") or {}
    edge_types = schema.get("edge_types") or {}

    if not isinstance(entity_types, dict):
        errors.append("entity_types 必须是 mapping，不能是 list")
        entity_types = {}
    if not isinstance(edge_types, dict):
        errors.append("edge_types 必须是 mapping，不能是 list")
        edge_types = {}

    if len(entity_types) < 6:
        warnings.append(f"实体类型仅 {len(entity_types)} 个，建议 >= 6")
    if len(entity_types) > 15:
        warnings.append(f"实体类型 {len(entity_types)} 个，建议 <= 15")
    if len(edge_types) < 8:
        warnings.append(f"边类型仅 {len(edge_types)} 个，建议 >= 8")
    if len(edge_types) > 18:
        warnings.append(f"边类型 {len(edge_types)} 个，建议 <= 18")

    entity_names = set(entity_types)
    for name, spec in entity_types.items():
        if not str(name).isidentifier():
            errors.append(f"实体类型名不合法: {name}")
        if not isinstance(spec, dict):
            errors.append(f"实体类型 {name} 的定义必须是 mapping")
            continue
        if not spec.get("description"):
            errors.append(f"实体类型 {name} 缺少 description")
        if len(spec.get("good_examples", [])) < 3:
            warnings.append(f"实体类型 {name} 的 good_examples 少于 3 个")
        if len(spec.get("bad_examples", [])) < 3:
            warnings.append(f"实体类型 {name} 的 bad_examples 少于 3 个")
        properties = spec.get("properties") or {}
        if properties and not isinstance(properties, dict):
            errors.append(f"实体类型 {name} 的 properties 必须是 mapping")

    for name, spec in edge_types.items():
        if not str(name).isidentifier():
            errors.append(f"边类型名不合法: {name}")
        if not isinstance(spec, dict):
            errors.append(f"边类型 {name} 的定义必须是 mapping")
            continue
        for field in ["description", "source_types", "target_types"]:
            if field not in spec:
                errors.append(f"边类型 {name} 缺少 {field}")
        for src in spec.get("source_types", []):
            if src not in entity_names:
                warnings.append(f"边类型 {name} 的 source_type {src} 不在实体类型列表中")
        for tgt in spec.get("target_types", []):
            if tgt not in entity_names:
                warnings.append(f"边类型 {name} 的 target_type {tgt} 不在实体类型列表中")

    all_triggers = []
    for spec in edge_types.values():
        if isinstance(spec, dict):
            all_triggers.extend(spec.get("trigger_words", []))
    duplicate_triggers = [t for t, c in Counter(all_triggers).items() if c > 1]
    if duplicate_triggers:
        warnings.append(f"以下触发词在多个边类型中重复: {duplicate_triggers}。需要在 conflict_resolution 中写清楚边界。")

    return SchemaValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        entity_type_count=len(entity_types),
        edge_type_count=len(edge_types),
    )
```

---

## 阶段七：人工审核（交互点）

### 7.1 审核界面

在 `--mode interactive` 时，程序在此暂停，输出审核文件后提示用户。

```python
def _wait_for_human_approval(self, stage: Stage) -> None:
    """暂停并等待人工审核完成。"""
    print(f"""
{'=' * 60}
阶段 {stage.number} 完成：{stage.name}

请审核以下文件：
  - {stage.outputs['candidate_schema_yaml']}
  - {stage.outputs['candidate_schema_review_md']}
  - {stage.outputs['term_frequency_xlsx']}（工作表：candidate_object_terms）

审核项目：
  1. 每个实体类型是否有 >= 3 个 good examples 和 bad examples？
  2. 每个关系类型是否有 >= 2 个真实文本样例？
  3. 是否有类型边界模糊的？（见 candidate_schema_review.md 的 disambiguations 部分）
  4. 是否有应该合并/拆分的类型？

操作：
  1. 直接修改 YAML 文件
  2. 在 Excel 的 review_decision 列标记确认/修改
  3. 完成后，在此终端输入 'done' 继续
{'=' * 60}
""")
    while input('> ').strip().lower() != 'done':
        print("输入 'done' 继续...")
```

### 7.2 审核检查表自动生成

```python
def _generate_review_checklist(
    schema: dict,
    terms: TermFrequencyResult,
    patterns: PatternInventory,
) -> str:
    """为人工审核生成结构化检查表。

    schema 使用当前 schema_loader 兼容的 mapping 结构：
    schema["entity_types"]["Product"]，不是实体类型列表。
    """
    lines = ["# Schema 审核检查表
"]

    lines.append("## 实体类型审核
")
    for entity_name, spec in schema["entity_types"].items():
        lines.append(f"### {entity_name}
")
        lines.append("- [ ] **是否是稳定对象？** 在文档中被反复讨论，而不是一次性描述词")
        lines.append(f"  - 统计证据：{spec.get('evidence', '无')}")
        lines.append("- [ ] **是否会作为关系端点？** 至少一个边类型的 source_types 或 target_types 引用了它")
        lines.append("- [ ] **是否会被用户查询？** 用户会按这类对象检索或问答")
        lines.append("- [ ] **是否有跨 chunk 去重价值？** 同一对象跨页出现时需要合并")
        lines.append(f"- [ ] **good examples >= 3 ?** {len(spec.get('good_examples', []))} 个")
        lines.append(f"- [ ] **bad examples >= 3 ?** {len(spec.get('bad_examples', []))} 个")
        lines.append("- [ ] **是否会导致大量垃圾实体？** bad examples 是否覆盖常见误抽")
        lines.append("")

    lines.append("## 关系类型审核
")
    for edge_name, spec in schema["edge_types"].items():
        lines.append(f"### {edge_name}
")
        lines.append(f"- [ ] **文本中有明确触发词？** {spec.get('trigger_words', [])}")
        lines.append(f"- [ ] **source_types 清楚？** {spec.get('source_types', [])}")
        lines.append(f"- [ ] **target_types 清楚？** {spec.get('target_types', [])}")
        lines.append("- [ ] **是否太细导致抽不稳？** 关系语义如果只差一点点，LLM 会混用")
        lines.append("- [ ] **是否太泛导致没语义？** 如果大量边都落到一个类型，说明定义太宽")
        lines.append("- [ ] **和其他关系有重叠？** 如果端点组合相同，必须写 conflict_resolution")
        lines.append("")

    lines.append("## 实体对齐审核
")
    lines.append("- [ ] 同义词引导只写入 official_name/synonyms，不强制改写实体 name")
    lines.append("- [ ] 文本识别变体必须来自统计证据或人工审核，不能维护事先猜测的替换表")
    lines.append("- [ ] 如果两个词只是相似但不是同一对象，review_decision 必须标为 not_same_entity")

    return '\n'.join(lines)
```

---

## 阶段八：生成 Prompt 规则（精确规格）

### 8.1 实体提取 Prompt 模板

```python
ENTITY_EXTRACTION_PROMPT_TEMPLATE = """你是一位专业的知识图谱实体提取专家。请从以下文档片段中提取实体。

## 实体类型定义

{entity_type_definitions}

## 提取规则

{rules}

## 实体对齐引导（填写 official_name 和 synonyms，不改写 name）

如果当前文本中的实体名称是简称、别称或文本识别变体：
1. name 必须保留当前文本里实际出现的写法；
2. official_name 填规范名称；
3. synonyms 填同义词、简称、英文名、文本识别变体；
4. 如果没有证据证明两个名称是同一对象，official_name 填 null，synonyms 填空数组。

这些字段会写入节点 attributes，并被 _build_name_to_node_map() 用作边端点解析索引。它不是硬编码替换表，也不会在入库前强制把 A 改成 B。

{synonym_guidance}

## 重要提醒

**以下内容不要提取为实体**：
{excluded_items}

## 文档片段

{chunk_text}

## 输出要求

以 JSON 格式输出提取的实体列表。每个实体包含：
- name: 实体名称，必须保留文档中出现的原文写法
- entity_type_id: 实体类型 ID，必须使用运行时提供的类型编号
- summary: 一句话总结这个实体在文档中的含义
- official_name: 规范名称；没有明确规范名时填 null
- synonyms: 同义词、简称、英文名、文本识别变体；没有则填 []

```json
{{
  "extracted_entities": [
    {{
      "name": "GB/T 25338.1-2019",
      "entity_type_id": 1,
      "summary": "铁路道岔转换设备标准",
      "official_name": null,
      "synonyms": []
    }}
  ]
}}
```
"""

def _build_entity_type_definitions(entity_type_context: list[dict]) -> str:
    """将 Graphiti 运行时实体类型上下文格式化为 prompt 文本。

    schema 文件里没有 type_id；type_id 是 Graphiti 根据 entity_types mapping 在运行时生成的。
    因此这里接收的是已有抽取代码使用的 entity_type_context。
    """
    sections = []
    for et in entity_type_context:
        type_name = et["entity_type_name"]
        type_id = et["entity_type_id"]
        description = et.get("description", "")
        good_examples = et.get("good_examples", [])
        bad_examples = et.get("bad_examples", [])
        properties = et.get("properties", {})

        section = f"""### {type_name} (entity_type_id={type_id})
**定义**: {description}

**Good examples**（应该提取）:
{chr(10).join(f"  - {ex}" for ex in good_examples)}

**Bad examples**（不应提取）:
{chr(10).join(f"  - {ex}" for ex in bad_examples)}
"""
        if properties:
            section += f"
**属性**: {', '.join(properties)}"
        sections.append(section)

    return '\n\n'.join(sections)


def _build_entity_rules(schema: dict, patterns: PatternInventory) -> str:
    """基于统计证据构建提取规则。"""
    rules = [
        "1. 实体名称必须来自当前文本，不能凭空编造名称",
        "2. name 保留原文写法；official_name/synonyms 只用于实体对齐，不用于强制改名",
        "3. TechnicalParameter 必须是完整的数值+单位或参数名+数值，禁止裸数字和裸单位作为实体",
        "4. 如果一个概念只是上层实体的属性，不单独提取为实体",
        "5. 对于表格，每一行先判断是否有可命名对象，再判断参数值是否需要成为 TechnicalParameter",
        "6. 如果类型有歧义，使用 schema.disambiguations 中的规则",
    ]

    noise_terms = [t for t in patterns.ocr_suspects if t.count >= 2]
    if noise_terms:
        noise_list = ", ".join(f""{t.value}"" for t in noise_terms[:10])
        rules.append(f"7. 以下疑似文本识别噪声不要提取为实体: {noise_list}")

    return '\n'.join(rules)


def _build_excluded_items(schema: dict, patterns: PatternInventory) -> str:
    """构建排除列表。"""
    items = []
    items.append("- 编目元数据: ICS 编号、CCS 分类号、发布日期、实施日期")
    items.append("- 章节号、条款号和目录项只作为 provenance/metadata，不作为实体或关系端点")
    items.append("- 裸数字、裸单位、页码、目录点线、孤立标点、句子片段")

    for f in schema.get("suggested_filters", []):
        items.append(f"- {f.get('description', f.get('filter', ''))}")

    return '\n'.join(items)
```

### 8.2 边提取 Prompt 模板

```python
EDGE_EXTRACTION_PROMPT_TEMPLATE = """你是一位专业的知识图谱关系提取专家。请从以下文档片段中提取实体之间的关系。

## 实体列表（只能从以下实体中选择关系端点）

{entity_list}

## 关系类型定义

{edge_type_definitions}

## 提取规则

1. 关系的 source 和 target 必须**逐字匹配**上述实体列表中的 name 字段
2. 如果找不到匹配的实体作为端点，宁可不输出该关系，也不要编造实体名
3. 关系类型只能从上述定义中选择
4. 每个关系的事实描述（fact）必须基于当前文本，不能臆测
5. 以下类型的关系优先建立连接：
   - Product 必须有 HAS_ATTRIBUTE 或 HAS_RATING 或 HAS_COMPONENT 边
   - TechnicalParameter 必须有 SPECIFIES 或 HAS_ATTRIBUTE 边
   - TechnicalTerm 必须有 DEFINES 边

## 常见错误（不要犯）

{common_mistakes}

## 文档片段

{chunk_text}

## 输出要求

以 JSON 格式输出。每条边包含 source_entity_name, target_entity_name, relation_type, fact：

```json
{{
  "edges": [
    {{
      "source_entity_name": "5.5.7",
      "target_entity_name": "周围空气温度",
      "relation_type": "SPECIFIES",
      "fact": "5.5.7 规定了转辙机适用的周围空气温度范围为 -40°C~+70°C"
    }}
  ]
}}
```
"""

def _build_entity_list(name_to_node: dict) -> str:
    """构建实体列表（供边 prompt 使用）。"""
    lines = []
    for name, node in name_to_node.items():
        entity_type = node.labels[0] if hasattr(node, 'labels') and node.labels else 'Entity'
        summary = getattr(node, 'summary', '') or ''
        lines.append(f"- [{entity_type}] **{name}**" + (f": {summary[:100]}" if summary else ''))
    return '\n'.join(lines)


def _build_edge_type_definitions(edge_types: dict[str, dict]) -> str:
    """将 mapping 结构的 edge_types 格式化为 prompt。"""
    sections = []
    for edge_name, et in edge_types.items():
        sources = ', '.join(et.get('source_types', []))
        targets = ', '.join(et.get('target_types', []))
        triggers = ', '.join(et.get('trigger_words', []))

        section = f"""### {edge_name}
**定义**: {et.get('description', '')}
**允许的起点**: {sources}
**允许的终点**: {targets}
**触发词**: {triggers}

**Good examples**:
{chr(10).join(f'  - {ex.get("source", "")} → {ex.get("target", "")}: {ex.get("fact", "")}' for ex in et.get('good_examples', []))}

**Bad examples**:
{chr(10).join(f'  - {ex}' for ex in et.get('bad_examples', []))}
"""
        sections.append(section)

    return '\n\n'.join(sections)


def _build_common_mistakes(schema: dict) -> str:
    """基于已知的常见错误模式生成警告。"""
    mistakes = [
        '- **关系端点是幻觉**：LLM 把"技术标准""相关产品"这类泛称当做实体名，但实体列表中并没有它们。如果找不到精确匹配，不要输出该关系。',
        '- **关系类型用错**：REFERENCE 只用于标准间引用；产品应该用 SPECIFIES 或 HAS_ATTRIBUTE。',
        '- **纯数字作为端点**：不要用 "100"、"2.5" 作为关系端点。这些是参数值，不是实体。',
    ]
    return '\n'.join(mistakes)
```

---

## 阶段九：小样本试跑（精确规格）

### 9.1 函数签名

```python
def run_sample_extraction(
    schema_yaml: Path,
    prompt_rules: dict,
    sample_chunks_jsonl: Path,
    output_dir: Path,
    graphiti_config: dict,  # 传递给 Graphiti 的配置
) -> SampleExtractionResult:
    ...
```

### 9.2 执行逻辑

```python
async def _run_sample_extraction(
    schema_yaml: Path,
    prompt_rules: dict,
    sample_chunks: list[dict],
    output_dir: Path,
    graphiti_config: dict,
) -> SampleExtractionResult:
    """对样本 chunk 执行抽取，收集质量指标。

    当前代码已经支持：
    - load_graph_schema(schema_yaml) 把 YAML mapping 转成 Graphiti 需要的 Pydantic 类型；
    - Graphiti.add_episode(..., entity_types=..., edge_types=..., edge_type_map=...);
    - custom_extraction_instructions 把阶段八生成的额外规则注入现有 prompt。

    当前接入方式是 custom_extraction_instructions：阶段八生成的规则被压缩后注入现有 prompt。
    完整 prompt 替换不作为本规格的默认实现路径。
    """
    loaded = load_graph_schema(schema_yaml)
    custom_instructions = _build_custom_extraction_instructions(prompt_rules)

    graphiti = Graphiti(
        uri=graphiti_config["neo4j_uri"],
        user=graphiti_config["neo4j_user"],
        password=graphiti_config["neo4j_password"],
        llm_client=OpenAIClient(...),
        embedder=OpenAIEmbedder(...),
    )
    await graphiti.build_indices_and_constraints()

    all_entities = []
    all_edges = []
    all_rejected_entities = []
    all_rejected_edges = []

    for chunk in sample_chunks:
        result = await graphiti.add_episode(
            name=f"sample_{chunk['chunk_id']}",
            episode_body=chunk["text"],
            source_description=f"Page {chunk['page_start']} of {chunk['doc_id']}",
            reference_time=datetime.now(),
            entity_types=loaded.entity_types,
            edge_types=loaded.edge_types,
            edge_type_map=loaded.edge_type_map,
            custom_extraction_instructions=custom_instructions,
        )

        all_entities.extend(result.nodes)
        all_edges.extend(result.edges)

        # 收集该 chunk 的拒绝记录。真实来源是提取函数内部的
        # _validate_extracted_entities / _validate_extracted_edges 返回的
        # validation result（含 reason、fixable、candidate 等字段）。
        # 当前 Graphiti 未把 rejected 列表暴露到 add_episode 返回值中，
        # 需要新增 collector 透传；_read_* 函数从 chunk 临时属性读取。
        all_rejected_entities.extend(_read_rejected_entities_for_chunk(chunk))
        all_rejected_edges.extend(_read_rejected_edges_for_chunk(chunk))

    _save_jsonl(output_dir / "entities.jsonl", all_entities)
    _save_jsonl(output_dir / "edges.jsonl", all_edges)
    _save_jsonl(output_dir / "rejected_entities.jsonl", all_rejected_entities)
    _save_jsonl(output_dir / "rejected_edges.jsonl", all_rejected_edges)

    report = _generate_sample_quality_report(
        entities=all_entities,
        edges=all_edges,
        rejected_entities=all_rejected_entities,
        rejected_edges=all_rejected_edges,
        schema=loaded.raw,
    )

    await graphiti.close()

    return SampleExtractionResult(
        entities=all_entities,
        edges=all_edges,
        report=report,
    )


def _build_custom_extraction_instructions(prompt_rules: dict) -> str:
    """把阶段八生成的规则压缩进当前 add_episode 已支持的字段。"""
    parts = []
    for key in ["entity_rules", "edge_rules", "synonym_guidance", "excluded_items"]:
        value = prompt_rules.get(key)
        if value:
            parts.append(f"## {key}\n{value}")
    return '\n\n'.join(parts)


def _read_rejected_entities_for_chunk(chunk: dict) -> list[dict]:
    """获取当前 chunk 的实体拒绝记录。

    真实数据来源：node_operations 中 _validate_extracted_entities() 返回的
    _EntityValidationResult.rejected_entities 列表。当前 Graphiti 的
    add_episode() 没有把这个列表暴露到返回值中，因此本工具需要一个小改动：
    在 extract_nodes 中将 rejected_entities 挂到 chunk 的临时属性上，
    或者新增一个 collector 把 validation result 透传出来。

    在实现 collector 之前，本函数从 chunk 的临时属性读取（需要预先注入）：
    """
    records = chunk.get('_rejected_entities')
    if records is None:
        return []
    return [
        {
            'name': r.get('name', ''),
            'reason': r.get('reason', ''),
            'fixable': r.get('fixable', False),
            'chunk_id': chunk.get('chunk_id', ''),
        }
        for r in records
    ]


def _read_rejected_edges_for_chunk(chunk: dict) -> list[dict]:
    """获取当前 chunk 的边拒绝记录。

    真实数据来源：edge_operations 中 _validate_extracted_edges() 返回的
    _ValidationResult.rejected_edges 列表。与实体同理，需要 collector 透传。
    """
    records = chunk.get('_rejected_edges')
    if records is None:
        return []
    return [
        {
            'source': r.get('source_entity_name', ''),
            'target': r.get('target_entity_name', ''),
            'relation': r.get('relation_type', ''),
            'reason': r.get('reason', ''),
            'fixable': r.get('fixable', False),
            'candidate_source': r.get('candidate_source', ''),
            'candidate_target': r.get('candidate_target', ''),
            'chunk_id': chunk.get('chunk_id', ''),
        }
        for r in records
    ]
```

### 9.3 质量指标计算公式

```python
def _generate_sample_quality_report(
    entities: list, edges: list,
    rejected_entities: list, rejected_edges: list,
    schema: dict,
) -> SampleQualityReport:
    """生成小样本质量报告。所有指标都有精确的计算公式。"""
    
    n_entities = len(entities)
    n_edges = len(edges)
    n_chunks = len({e.get('chunk_id', '') for e in entities})
    
    # 1. 实体类型分布
    entity_type_dist = Counter()
    for e in entities:
        for label in e.get('labels', ['Entity']):
            if label != 'Entity':
                entity_type_dist[label] += 1
    
    # 2. Entity fallback 占比
    entity_fallback = sum(1 for e in entities if e.get('labels') == ['Entity'])
    fallback_ratio = entity_fallback / max(n_entities, 1)
    
    # 3. 边类型分布
    edge_type_dist = Counter(e.get('name', 'RELATES_TO') for e in edges)
    
    # 4. 零度实体（在样本范围内）
    entity_names_with_edges = set()
    for e in edges:
        entity_names_with_edges.add(e.get('source_entity_name', ''))
        entity_names_with_edges.add(e.get('target_entity_name', ''))
    zero_degree = [e for e in entities if e['name'] not in entity_names_with_edges]
    zero_degree_ratio = len(zero_degree) / max(n_entities, 1)
    
    # 5. Entity-not-found 边占比
    entity_not_found = sum(
        1 for r in rejected_edges 
        if r.get('reason') in ('source_not_found', 'target_not_found')
    )
    enf_ratio = entity_not_found / max(n_edges + len(rejected_edges), 1)
    
    # 6. 边/实体比
    edge_entity_ratio = n_edges / max(n_entities, 1)
    
    # 7. 每 chunk 平均实体数和边数
    avg_entities_per_chunk = n_entities / max(n_chunks, 1)
    avg_edges_per_chunk = n_edges / max(n_chunks, 1)
    
    # 8. 关系类型覆盖率（schema 中定义的边类型有多少被实际用到）
    defined_edge_types = set((schema.get('edge_types') or {}).keys())
    used_edge_types = set(edge_type_dist.keys())
    edge_type_coverage = len(used_edge_types & defined_edge_types) / max(len(defined_edge_types), 1)
    
    # 9. 同名异类型实体
    name_to_types = {}
    for e in entities:
        name_to_types.setdefault(e['name'], set()).update(
            l for l in e.get('labels', []) if l != 'Entity'
        )
    cross_type_duplicates = {n: ts for n, ts in name_to_types.items() if len(ts) > 1}
    
    # 10. 拒绝原因分布
    entity_reject_reasons = Counter(r['reason'] for r in rejected_entities)
    edge_reject_reasons = Counter(r['reason'] for r in rejected_edges)
    
    # 11. 结论
    conclusion = _determine_conclusion(
        fallback_ratio=fallback_ratio,
        zero_degree_ratio=zero_degree_ratio,
        enf_ratio=enf_ratio,
        edge_type_coverage=edge_type_coverage,
        garbage_ratio=None,  # 从 entity names 中检测
    )
    
    return SampleQualityReport(
        entity_count=n_entities,
        edge_count=n_edges,
        entity_type_distribution=dict(entity_type_dist),
        edge_type_distribution=dict(edge_type_dist),
        entity_fallback_ratio=fallback_ratio,
        zero_degree_ratio=zero_degree_ratio,
        entity_not_found_ratio=enf_ratio,
        edge_entity_ratio=edge_entity_ratio,
        avg_entities_per_chunk=avg_entities_per_chunk,
        avg_edges_per_chunk=avg_edges_per_chunk,
        edge_type_coverage=edge_type_coverage,
        cross_type_duplicates=cross_type_duplicates,
        entity_reject_reasons=dict(entity_reject_reasons),
        edge_reject_reasons=dict(edge_reject_reasons),
        conclusion=conclusion,
    )
```

### 9.4 通过门槛决策树

```python
SAMPLE_QUALITY_THRESHOLDS = {
    'entity_fallback_ratio': 0.15,       # Entity fallback ≤ 15%
    'zero_degree_ratio': 0.25,            # 零度实体 ≤ 25%
    'entity_not_found_ratio': 0.10,       # entity-not-found 边 ≤ 10%
    'edge_type_coverage': 0.60,           # 关系类型覆盖率 ≥ 60%
    'garbage_entity_ratio': 0.10,         # 明显垃圾实体 ≤ 10%
    'edge_entity_ratio': (0.5, None),     # 边/实体比 ≥ 0.5（None 表示无上限）
}

def _determine_conclusion(
    fallback_ratio: float,
    zero_degree_ratio: float,
    enf_ratio: float,
    edge_type_coverage: float,
    garbage_ratio: float | None,
) -> str:
    """决策树：PASS / FIX_SCHEMA / FIX_PROMPT / FIX_ENTITY_ALIGNMENT / FIX_TEXT_EXTRACTION。
    
    按优先级判断（先判断最严重的）：
    """
    # 1. 如果 entity-not-found 比例过高 → 先检查是否文本质量导致的
    if enf_ratio > 0.15 and garbage_ratio and garbage_ratio > 0.10:
        return 'FIX_TEXT_EXTRACTION'  # 文本质量差导致 LLM 产生幻觉
    
    # 2. 如果零度率 > 35% → schema 或 prompt 有严重问题
    if zero_degree_ratio > 0.35:
        if fallback_ratio > 0.20:
            return 'FIX_SCHEMA'  # Entity fallback 太高 → 实体类型定义不够清晰
        else:
            return 'FIX_PROMPT'  # 实体 OK 但缺边 → 边提取规则不够强
    
    # 3. 如果 entity-not-found 高但零度率 OK → 实体对齐问题
    if enf_ratio > 0.10:
        return 'FIX_ENTITY_ALIGNMENT'  # LLM 在边端点写了近似名称，但该名称没有进入 name/official_name/synonyms 索引
    
    # 4. 如果关系覆盖率 < 40% → schema 边类型定义太细
    if edge_type_coverage < 0.40:
        return 'FIX_SCHEMA'  # 合并或删除用不上的边类型
    
    # 5. 全部通过阈值
    if (fallback_ratio <= SAMPLE_QUALITY_THRESHOLDS['entity_fallback_ratio'] and
        zero_degree_ratio <= SAMPLE_QUALITY_THRESHOLDS['zero_degree_ratio'] and
        enf_ratio <= SAMPLE_QUALITY_THRESHOLDS['entity_not_found_ratio'] and
        edge_type_coverage >= SAMPLE_QUALITY_THRESHOLDS['edge_type_coverage']):
        return 'PASS'
    
    # 6. 不满足 PASS 但也不命中上面的严重问题 → 综合判断
    issues = []
    if fallback_ratio > 0.15:
        issues.append(('schema', f'Entity fallback {fallback_ratio:.1%}'))
    if zero_degree_ratio > 0.25:
        issues.append(('prompt', f'Zero-degree {zero_degree_ratio:.1%}'))
    if enf_ratio > 0.10:
        issues.append(('entity_alignment', f'Entity-not-found {enf_ratio:.1%}'))
    
    # 按最多的 issues 类别
    from collections import Counter
    issue_types = Counter(t for t, _ in issues)
    dominant = issue_types.most_common(1)[0][0]
    return {'schema': 'FIX_SCHEMA', 'prompt': 'FIX_PROMPT', 'entity_alignment': 'FIX_ENTITY_ALIGNMENT'}[dominant]
```

---

## 阶段十：基于质量报告修正（精确规格）

### 10.1 修正策略映射表

```python
FIX_STRATEGIES = {
    'FIX_TEXT_EXTRACTION': {
        'priority': 1,
        'description': '文本抽取质量问题',
        'actions': [
            '回阶段一：对 needs_ocr=true 的页面重新 OCR（换 PaddleOCR）',
            '检查 OCR 后是否引入了新的异常短词',
            '如果问题页面集中在表格区域，单独对表格用 pdfplumber 重抽',
            '重新生成 pages.jsonl → chunks.jsonl → 重跑阶段三到九',
        ],
    },
    
    'FIX_SCHEMA': {
        'priority': 2,
        'description': 'Schema 类型定义不清晰或粒度不合适',
        'actions': [
            '检查 entity_fallback 高的原因：是否存在边界模糊的类型？',
            '实体类型过多（>15）→ 合并语义相近的类型',
            '实体类型过少（<6）→ LLM 被迫使用 Entity fallback → 拆分类型',
            '边类型覆盖低 → 检查哪些边类型 0 出现 → 合并或删除',
            '检查 rejected_entities 的 reason 分布 → 如果大量 invalid_entity_type_id → 补充类型说明',
            '更新 candidate_schema.yaml 后重新生成 prompt',
        ],
        'update_files': ['candidate_schema.yaml', 'prompt_rules'],
    },
    
    'FIX_PROMPT': {
        'priority': 3,
        'description': 'Prompt 规则不够强或 good/bad examples 不足',
        'actions': [
            '零度率高 → 加强边 prompt 的连通性约束规则',
            '特定类型的实体缺边（如 Product 零度多）→ 为这类实体增加强制边规则',
            '垃圾实体多 → 补充 bad examples 和过滤规则',
            '某类关系异常膨胀 → 补充 bad examples 和端点约束',
            '更新 prompt 规则后重跑小样本',
        ],
        'update_files': ['prompt_rules'],
    },
    
    'FIX_ENTITY_ALIGNMENT': {
        'priority': 4,
        'description': '实体名变体没有进入 official_name/synonyms，导致边端点解析失败',
        'actions': [
            '从 rejected_edges 中提取 fixable=true 且 reason 含 fuzzy_near_miss 的记录',
            '对比拒绝边的 source/target 名称与 candidate 名称，生成 entity_alignment_candidates',
            '人工确认后写入 synonym_guidance；它只指导 LLM 输出 official_name/synonyms，不强制改写 name',
            '更新 custom_extraction_instructions 后重跑小样本',
        ],
        'update_files': ['entity_alignment/synonym_guidance', 'prompt_rules'],
    },
}
```

### 10.2 自动修复建议生成

```python
def _generate_fix_suggestions(
    report: SampleQualityReport,
    schema: dict,
    rejected_entities: list,
    rejected_edges: list,
) -> list[FixSuggestion]:
    """基于质量报告自动生成具体的修复建议。"""
    suggestions = []
    
    # 1. Entity fallback 分析
    if report.entity_fallback_ratio > 0.15:
        fallback_entities = [e for e in report.entities if e.get('labels') == ['Entity']]
        # 分析 fallback 实体的共性
        common_patterns = _find_common_patterns(fallback_entities)
        suggestions.append(FixSuggestion(
            priority=1,
            type='schema',
            issue=f'Entity fallback 占比 {report.entity_fallback_ratio:.1%}',
            detail=f'共 {len(fallback_entities)} 个实体被标记为泛型 Entity。常见模式: {common_patterns}',
            action='为这些模式增加专门的实体类型，或合并现有类型的定义以覆盖它们',
        ))
    
    # 2. 零度实体分析
    if report.zero_degree_ratio > 0.25:
        zd_by_type = Counter()
        for e in report.zero_degree_entities:
            for label in e.get('labels', ['Entity']):
                if label != 'Entity':
                    zd_by_type[label] += 1
        for entity_type, count in zd_by_type.most_common(5):
            suggestions.append(FixSuggestion(
                priority=2,
                type='prompt',
                issue=f'{entity_type} 类型有 {count} 个零度实体',
                detail=f'这些实体被提取了但没有边连接',
                action=f'在边 prompt 中增加规则：所有 {entity_type} 必须有至少一条边（或标注为合法孤立）',
            ))
    
    # 3. Entity-not-found 分析
    enf_edges = [e for e in rejected_edges if e.get('reason') in ('source_not_found', 'target_not_found')]
    if enf_edges:
        # 提取高频的缺失实体名
        missing_names = Counter()
        for e in enf_edges:
            if e['reason'] == 'source_not_found':
                missing_names[e.get('source_entity_name', '')] += 1
            if e['reason'] == 'target_not_found':
                missing_names[e.get('target_entity_name', '')] += 1
        
        for name, count in missing_names.most_common(10):
            # 检查是否有候选
            candidates = [e.get('candidate_source') or e.get('candidate_target') for e in enf_edges 
                         if e.get('source_entity_name') == name or e.get('target_entity_name') == name]
            candidate = next((c for c in candidates if c), None)
            if candidate:
                suggestions.append(FixSuggestion(
                    priority=3,
                    type='entity_alignment',
                    issue=f'边端点 "{name}" 找不到实体（{count} 次）',
                    detail=f'候选实体: "{candidate}"',
                    action=f'在 synonym_guidance 中记录：实体候选 "{candidate}" 的 synonyms 包含 "{name}"；不强制改写 name',
                ))
            else:
                suggestions.append(FixSuggestion(
                    priority=4,
                    type='prompt',
                    issue=f'边端点 "{name}" 找不到实体也无候选（{count} 次）',
                    detail='LLM 可能产生了幻觉实体名',
                    action='在边 prompt 的 bad examples 中增加此名称，要求 LLM 不要使用不存在的实体名',
                ))
    
    # 4. 关系类型覆盖分析
    defined_types = set((schema.get('edge_types') or {}).keys())
    used_types = set(report.edge_type_distribution.keys())
    unused = defined_types - used_types
    if unused:
        suggestions.append(FixSuggestion(
            priority=5,
            type='schema',
            issue=f'{len(unused)} 个边类型在样本中未出现: {unused}',
            detail='可能定义太细或文档中不存在此类关系',
            action='考虑删除或合并这些边类型；或检查 trigger_words 是否太窄',
        ))
    
    return suggestions
```

---

## 阶段十一：全量抽取前检查（CheckList）

### 11.1 自动检查函数

```python
def preflight_check(
    state: PipelineState,
    output_dir: Path,
) -> PreflightResult:
    """全量抽取前的自动化检查。全部通过才允许进入阶段十二。"""
    
    checks = []
    
    # Check 1：文本质量
    stage1 = state.data.get('stage1_text_extraction', {})
    metrics = stage1.get('metrics', {})
    checks.append(CheckItem(
        id='text_quality',
        name='文本抽取质量达标',
        passed=(
            metrics.get('empty_page_ratio', 1.0) <= 0.15 and
            metrics.get('avg_chars_per_page', 0) >= 300 and
            metrics.get('garbled_ratio', 1.0) <= 0.05
        ),
        detail=f"空页率={metrics.get('empty_page_ratio', '?')}, "
               f"每页均字={metrics.get('avg_chars_per_page', '?')}, "
               f"乱码率={metrics.get('garbled_ratio', '?')}",
    ))
    
    # Check 2：chunks 完整
    chunks_path = output_dir / 'chunks.jsonl'
    checks.append(CheckItem(
        id='chunks_exist',
        name='chunks.jsonl 存在且非空',
        passed=chunks_path.exists() and chunks_path.stat().st_size > 0,
        detail=str(chunks_path),
    ))
    
    # Check 3：schema 已审核
    stage7 = state.data.get('stage7_human_review', {})
    checks.append(CheckItem(
        id='schema_reviewed',
        name='候选 schema 已通过人工审核',
        passed=stage7.get('completed', False) and stage7.get('review_approved', False),
        detail='在 interactive 模式下需输入 done 确认',
    ))
    
    # Check 4：小样本通过门槛
    stage9 = state.data.get('stage9_sample_extraction', {})
    sample_metrics = stage9.get('metrics', {})
    conclusion = sample_metrics.get('conclusion', 'UNKNOWN')
    checks.append(CheckItem(
        id='sample_quality',
        name=f'小样本质量报告通过（结论: {conclusion}）',
        passed=conclusion == 'PASS',
        detail=f"Entity fallback={sample_metrics.get('entity_fallback_ratio', '?')}, "
               f"Zero-degree={sample_metrics.get('zero_degree_ratio', '?')}, "
               f"ENF={sample_metrics.get('entity_not_found_ratio', '?')}",
    ))
    
    # Check 5：prompt 规则已更新
    stage8 = state.data.get('stage8_prompt_generation', {})
    checks.append(CheckItem(
        id='prompts_ready',
        name='Prompt 规则已生成并更新',
        passed=stage8.get('completed', False),
        detail='prompt_rules.yaml 应包含 entity_prompt 和 edge_prompt',
    ))
    
    # Check 6：同义词引导已审核
    checks.append(CheckItem(
        id='synonym_guidance_reviewed',
        name='实体同义词引导已审核',
        passed=stage7.get('synonym_guidance_reviewed', False),
        detail='审核 entity_alignment_candidates 并确认同义词对，写入 synonym_guidance',
    ))
    
    # Check 7：schema YAML 结构合法
    schema_path = output_dir / 'candidate_schema.yaml'
    if schema_path.exists():
        validation = validate_candidate_schema(schema_path)
        checks.append(CheckItem(
            id='schema_valid',
            name='Schema YAML 结构校验',
            passed=validation.valid,
            detail=f'Errors: {len(validation.errors)}, Warnings: {len(validation.warnings)}',
        ))
    
    all_passed = all(c.passed for c in checks)
    
    return PreflightResult(
        passed=all_passed,
        checks=checks,
        can_proceed=all_passed,
        blocking_issues=[c for c in checks if not c.passed],
    )
```

---

## 阶段十二：全量抽取（精确规格）

阶段十二只负责使用审核通过的 `schema_config.yaml` 以及包装配置中的 prompt_rules、entity_alignment 和 filters 跑完整文档。它不再修改 schema。所有质量统计和复盘写入阶段十三。

核心输入：

```text
schema_config.yaml
final_config.yaml
chunks.jsonl
prompt_rules.yaml
```

核心输出：

```text
entities.jsonl
edges.jsonl
rejected_entities.jsonl
rejected_edges.jsonl
zero_degree_entities.jsonl
cleanup_result.json
```

---

## 阶段十三：全量抽取后复盘（精确规格）

### 13.1 复盘报告自动生成

```python
def generate_final_report(
    entities: list,
    edges: list,
    rejected_entities: list,
    rejected_edges: list,
    zero_degree_entities: list,
    cleanup_result: dict,
    schema: dict,
    output_dir: Path,
) -> FinalReport:
    """全量抽取后自动生成复盘报告。"""
    
    # 1. 实体类型分布（排序）
    entity_type_dist = Counter()
    for e in entities:
        for label in e.get('labels', []):
            if label != 'Entity':
                entity_type_dist[label] += 1
    
    # 2. 边类型分布（排序）
    edge_type_dist = Counter(e.get('name', 'RELATES_TO') for e in edges)
    
    # 3. 零度实体分解（按类型 + 按 cleanup 分类）
    zero_degree_breakdown = _classify_zero_degree_entities(zero_degree_entities)
    
    # 4. 拒绝原因 Top 10
    entity_reject_top = Counter(r['reason'] for r in rejected_entities).most_common(10)
    edge_reject_top = Counter(r['reason'] for r in rejected_edges).most_common(10)
    
    # 5. 未出现的类型
    defined_entity_types = set((schema.get('entity_types') or {}).keys())
    defined_edge_types = set((schema.get('edge_types') or {}).keys())
    missing_entity_types = defined_entity_types - set(entity_type_dist.keys())
    missing_edge_types = defined_edge_types - set(edge_type_dist.keys())
    
    # 6. 异常膨胀的类型
    overrepresented_edges = [
        (name, cnt) for name, cnt in edge_type_dist.items()
        if cnt > len(edges) * 0.30  # 占比超过 30%
    ]
    
    # 7. 新增实体对齐候选（从 rejected_edges 的 fixable 记录提取）
    new_entity_alignment_candidates = _extract_entity_alignment_candidates_from_rejections(rejected_edges)
    
    report = {
        'summary': {
            'total_entities': len(entities),
            'total_edges': len(edges),
            'edge_entity_ratio': len(edges) / max(len(entities), 1),
            'zero_degree_count': len(zero_degree_entities),
            'zero_degree_ratio': len(zero_degree_entities) / max(len(entities), 1),
            'cleanup_removed': sum(cleanup_result.values()),
            'entity_rejections': len(rejected_entities),
            'edge_rejections': len(rejected_edges),
        },
        'entity_type_distribution': dict(entity_type_dist),
        'edge_type_distribution': dict(edge_type_dist),
        'zero_degree_breakdown': zero_degree_breakdown,
        'cleanup_breakdown': cleanup_result,
        'entity_reject_reasons_top10': entity_reject_top,
        'edge_reject_reasons_top10': edge_reject_top,
        'missing_types': {
            'entity_types': list(missing_entity_types),
            'edge_types': list(missing_edge_types),
        },
        'overrepresented_edges': overrepresented_edges,
        'new_entity_alignment_candidates': new_entity_alignment_candidates,
        'schema_improvement_suggestions': _generate_schema_improvements(
            missing_entity_types, missing_edge_types,
            overrepresented_edges, zero_degree_breakdown,
        ),
    }
    
    return FinalReport(**report)
```

### 13.2 复盘必须回答的问题（自动回答）

```python
def _generate_schema_improvements(
    missing_entity_types: set[str],
    missing_edge_types: set[str],
    overrepresented_edges: list[tuple[str, int]],
    zero_degree: dict,
) -> list[str]:
    """自动生成 schema 改进建议。"""
    suggestions = []
    
    # 哪种实体类型最多？是否合理？
    # （在报告中的 entity_type_distribution 已包含）
    
    # 哪些类型没被抽到？
    if missing_entity_types:
        suggestions.append(f'未出现的实体类型: {missing_entity_types}。考虑从 schema 中删除或修改定义。')
    if missing_edge_types:
        suggestions.append(f'未出现的边类型: {missing_edge_types}。考虑删除或扩大触发词范围。')
    
    # 异常膨胀的关系
    for name, cnt in overrepresented_edges:
        suggestions.append(f'关系类型 "{name}" 出现 {cnt} 次，占比过高。建议：增加 bad examples 和端点约束，或拆分类型。')
    
    # 零度剩余分析
    for category, entities in zero_degree.items():
        if len(entities) > 5:
            suggestions.append(f'{category} 类零度实体 {len(entities)} 个。建议在边 prompt 中针对性加强连通性规则。')
    
    return suggestions
```

---

## 附录 A：最终输出文件

全流程完成后，主产物是一个可直接被 `graphiti_rag/schema_loader.py` 加载的 schema 文件：

```text
runs/schema_design/schema_config.yaml
```

`schema_config.yaml` 只包含 schema 本身和 schema 设计审计元信息，不嵌入 prompt、运行配置、实体对齐运行参数或全量抽取报告。它的结构与当前 `schemas/gbt25338.yaml` 兼容：

```yaml
meta:
  generated_from: "tool_statistics"
  evidence_summary: "基于工具统计、主题聚类、小样本试跑和质量修正生成"

schema:
  mode: strict
  description: "当前文档集合的知识图谱 schema"

entity_types:
  Standard:
    description: "标准/规范文件"
    properties:
      official_name:
        type: string
        description: "规范名称"
      synonyms:
        type: list[string]
        description: "同义词、简称、文本识别变体"

edge_types:
  REFERENCES:
    description: "标准或规范文件引用另一个标准或规范文件"
    source_types: [Standard]
    target_types: [Standard]

suggested_filters:
  - filter: "文档结构"
    action: "章节号、条款号和目录项只作为 chunk provenance/metadata，不作为实体或关系端点"
```

辅助产物仍然保留，便于审计和后续接入现有 ingest：

```text
runs/schema_design/final_config.yaml        # 包装配置：引用 schema_config.yaml，并附带 prompt/filter/alignment
runs/schema_design/prompt_rules.yaml        # 阶段八/十生成的抽取规则
runs/schema_design/final_quality_report.md  # 阶段十三复盘报告
runs/schema_design/pipeline_state.json      # 阶段状态和输入 hash
```

`final_config.yaml` 不是 schema 主产物；它用于把 schema、prompt 规则、实体对齐和过滤策略传给后续 GraphRAG ingest 配置层。

---

## 附录 B：依赖安装清单

```bash
# 标准版（默认）
uv add pymupdf pdfplumber scikit-learn openpyxl pyyaml rapidfuzz jieba pypdfium2

# 增强版（按需）
uv add paddleocr          # 中文 OCR
uv add sentence-transformers  # 语义聚类/实体对齐
uv add hanlp              # 专业中文分词
uv add docling            # 复杂文档转换
```

---

## 附录 C：与现有代码的关系

本文档描述的自动化流程不要求修改 graphiti_core 的核心提取逻辑。它是在现有 Graphiti/GraphRAG 之上的一个**前置工具层**：

```
                    ┌──────────────────────────┐
                    │  Schema 设计自动化工具     │  ← 本文档描述的内容
                    │  (tools/schema_design/)   │
                    │                          │
                    │  输入: PDF/Word/纯文本     │
                    │  输出: schema_config.yaml  │
                    │        + 审计/辅助产物       │
                    └──────────┬───────────────┘
                               │
                               │ schema_config.yaml
                               ▼
                    ┌──────────────────────────┐
                    │  现有管线（不变）           │
                    │                          │
                    │  GraphRAG.ingest()        │
                    │    → Pipeline.run()       │
                    │      → Extractor.extract()│
                    │        → Graphiti.add_    │
                    │          episode()        │
                    │                          │
                    │  四层质量防线（不变）       │
                    │  拒绝账本（不变）          │
                    │  Cleanup（不变）           │
                    └──────────────────────────┘
```

现有代码中需要接入的配置点：
1. `schema_config.yaml` 是主 schema 文件，直接交给 `load_graph_schema()` 生成 `entity_types`、`edge_types`、`edge_type_map`。
2. `final_config.yaml` 是附属包装配置，引用 `schema_config.yaml`，并拆出 prompt_rules、entity_alignment、filters。
3. prompt_rules 不替换完整 prompt，而是压缩为 `custom_extraction_instructions` 传给 `Graphiti.add_episode()`。
4. entity_alignment 写入 `synonym_guidance`，指导 LLM 输出 `official_name` 和 `synonyms`；边端点解析继续使用 `name / official_name / synonyms` 索引。
5. filters 分两路使用：实体排除规则进入 `custom_extraction_instructions`，零度清理规则进入 cleanup 配置。
