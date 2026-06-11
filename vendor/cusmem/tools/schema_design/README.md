# Schema Design — 知识图谱 Schema 自动化设计工具

## 一句话概括

**你提供候选池（"我大概需要这些类型"），工具扫描文档补证据，大模型从候选池里挑最合适的，然后自动验证能不能抽出来。**

大模型只管"选"，不能"发明"。候选池是边界。

---

## 两种模式

| 模式 | 怎么用 | 大模型干什么 | 什么时候用 |
|------|--------|-------------|-----------|
| **Pool Mode**（推荐） | `--candidate-pool pool.yaml` | 从候选池中选择 | 你知道自己的领域大概有哪些实体/关系 |
| **Legacy Mode** | 不传 `--candidate-pool` | 从文档统计中自动归纳 | 探索陌生文档，不知道有什么类型 |

---

## Pool Mode 流程（7 步）

```
你写的 candidate_pool.yaml（"我大概需要这些类型"）
        │
        ▼
┌──────────────────────────────────────────────┐
│  Step 1: 证据扫描                             │
│  工具拿你候选池里的 example 去文档里搜，        │
│  统计每个候选类型出现了多少次、在哪些段落里      │
│  → candidate_pool_evidence.json              │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│  Step 2: 大模型选择                           │
│  大模型看到：候选池 + 证据统计                  │
│  大模型输出：我选这 18 个实体、16 个关系        │
│  约束：不能选候选池外的类型                     │
│  如果缺类型 → missing_candidate_request       │
│  → selected_schema.yaml                      │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│  Step 3: 自动审核 + 评分                      │
│  检查：选的类型都在候选池里吗？有证据吗？         │
│  评分：pool compliance + evidence coverage    │
│  → confidence_report.json                   │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│  Step 4: 生成抽取 Prompt                      │
│  把选好的 schema 转成大模型能懂的抽取指令       │
│  → prompt_rules.yaml                        │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│  Step 5: 小样本试跑（dry-run）                 │
│  抽 20 个段落，真的调大模型抽一把              │
│  看能不能抽出实体和边                          │
│  → entities.jsonl, edges.jsonl              │
│  → 质量报告：edge/entity 比、长实体名比例等     │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│  Step 6: 自动修正（auto-fix）                  │
│  根据试跑结果自动修：                          │
│  ✓ 可以修：prompt 规则、过滤规则、同义词        │
│  ✗ 不能修：新增候选池外的实体/关系类型           │
│  缺类型 → missing_candidate_request          │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│  Step 7: 全量抽取（接 Graphiti + Neo4j）       │
│  用最终 schema 对所有文档做全量知识图谱抽取     │
│  → 知识图谱入库 Neo4j                         │
└──────────────────────────────────────────────┘
```

---

## 快速开始

### 1. 准备候选池

写一个 `candidate_pool.yaml`，描述你领域里可能有哪些实体和关系：

```yaml
meta:
  domain: 城轨信号智能运维
  goals:
    - 设备关系建模
    - 故障诊断

entity_type_candidates:
  - id: Equipment
    name: 设备
    description: 信号设备、控制设备、检测设备
    examples:
      - 转辙机
      - 信号机
      - 计轴设备
    allowed: true
    priority: high

  - id: FaultMode
    name: 故障模式
    description: 设备可能出现的故障类型
    examples:
      - 表示不一致
      - 通信中断
    allowed: true
    priority: high

relation_type_candidates:
  - id: HAS_FAULT
    name: 存在故障
    description: 设备存在某类故障
    source_candidates: [Equipment]
    target_candidates: [FaultMode]
    trigger_words: [故障, 异常, 失效]
    allowed: true
    priority: high

filter_candidates:
  - id: bare_number
    description: 裸数字不作为实体
```

候选池有四种候选类型：

| 候选类型 | 说明 | 例子 |
|----------|------|------|
| `entity_type_candidates` | 可能的实体类型 | Equipment, FaultMode, Standard |
| `relation_type_candidates` | 可能的关系类型 | HAS_FAULT, LOCATED_AT |
| `attribute_candidates` | 实体的属性 | standard_no, threshold_value, unit |
| `filter_candidates` | 需要过滤的内容 | 裸数字、章节号、OCR 碎片 |

### 2. 运行

```bash
python -m tools.schema_design ./docs/ \
  --output ./runs/my_run \
  --candidate-pool ./candidate_pool.yaml \
  --selection-mode pool
```

参数说明：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--input` | 输入文件或目录 | 必填 |
| `--output` | 输出目录 | 必填 |
| `--candidate-pool` | 候选池 YAML 路径 | 无（legacy mode） |
| `--selection-mode` | pool 或 legacy | 有候选池时自动 pool |
| `--max-fix-rounds` | 自动修正最大轮次 | 3 |
| `--no-gates` | 跳过质量门控 | false |

### 3. 看结果

```bash
ls runs/my_run/
```

关键输出文件：

| 文件 | 说明 |
|------|------|
| `selected_schema.yaml` | 大模型最终选择的 schema |
| `candidate_pool_evidence.json` | 每个候选类型的文档证据 |
| `selection_report.md` | 选择结果报告（为什么选/不选） |
| `missing_candidate_request.json` | 候选池缺少的类型建议 |
| `local_dryrun_entities.jsonl` | 试跑抽出的实体 |
| `local_dryrun_edges.jsonl` | 试跑抽出的关系 |
| `confidence_report.json` | 自动审核置信度 |
| `test_report.md` | 完整测试报告 |

---

## 代码结构

```
tools/schema_design/
├── pipeline.py              # 主流程编排（13 个 Stage）
├── __main__.py              # CLI 入口
│
├── 候选池（Pool Mode 核心）
│   ├── user_candidate_pool.py          # 加载/校验候选池
│   ├── candidate_evidence_profiler.py   # 扫描文档补证据
│   └── schema_selection_from_pool.py   # 大模型从池中选择
│
├── Schema 生成（Legacy Mode）
│   ├── decision_brief.py       # 文档角色分析
│   ├── schema_generation.py    # 大模型自由生成 schema
│   ├── role_tagging.py         # 13 种通用角色分类
│   └── candidate_clustering.py # 角色聚类
│
├── 验证 & 修正
│   ├── static_scorer.py        # 静态评分（pool / legacy 双模式）
│   ├── local_dryrun.py         # 本地小样本试跑
│   ├── auto_fix.py             # 自动修正（pool 模式受约束）
│   ├── entity_normalizer.py    # 实体名规范化
│   ├── rejection_classifier.py # 拒因分类
│   ├── confidence.py           # 置信度评估
│   ├── quality.py              # 质量检查 + preflight
│   ├── schema_critic.py        # 大模型自我审查
│   └── prompt_rules.py         # 抽取 Prompt 生成
│
├── 基础处理
│   ├── text_extraction.py      # 文本提取（PDF/DOCX/TXT）
│   ├── chunking.py             # 文档分块
│   ├── patterns.py             # 正则模式识别
│   └── terms.py                # 词频统计
│
├── 基础设施
│   ├── models.py               # 所有 dataclass
│   ├── state.py                # Pipeline 状态持久化
│   ├── io_utils.py             # 文件读写工具
│   └── llm_client.py           # OpenAI 兼容 LLM 客户端
```

---

## 核心设计原则

### 1. 候选池是边界

```
大模型能做的：从候选池里选、合并、裁剪
大模型不能做的：新增候选池外的类型
如果缺类型 → missing_candidate_request，不自动加
```

### 2. 自动修不能越界

```
auto-fix 能自动改的：prompt 规则、过滤规则、同义词、规范化规则
auto-fix 不能改的：新增候选池外的实体/关系类型
```

### 3. 先试跑再全量

```
小样本 dry-run（20 个 chunk）→ 看质量 → 自动修 → 再试跑 → 达标后全量
```

---

## 真实案例

### 输入

**文档**: `20250107144713395.docx` — 城市轨道交通全自动运行系统运营技术和管理规范（27,000 字）

**候选池**: 28 个实体候选 + 27 个关系候选

### 过程

1. **证据扫描**: 工具在文档中找到 16/28 实体候选的证据，24/27 关系候选的触发词命中
2. **大模型选择**: 从 28 个实体中选 18 个，27 个关系中选 16 个
3. **池合规**: `OCCURS_AT`（时间关系）候选池标记 `allowed: false`，大模型试图选它 → 被拦截，正确拒绝
4. **试跑**: 抽 20 个段落 → 446 个实体 + 188 条边

### 结果

| 指标 | 值 | 说明 |
|------|-----|------|
| 选中实体 | 18/28 | Equipment, Function, DataItem, LineElement... |
| 选中关系 | 16/27 | EXCHANGES_DATA, HAS_COMPONENT, PROVIDES_FUNCTION... |
| Edge/Entity | 0.422 | 每 100 个实体有 42 条边 |
| 活跃边类型 | 14 种 | 分布均匀，没有单一类型垄断 |
| 长实体名比 | 0.4% | 几乎没有"额定转换力为 2.5kN"这种问题 |
| Pool Compliant | ✓ | 越界类型被拦截 |

### 试跑抽出的实体示例

```
信号系统 [SignalSystem]
FAM模式 [OperationMode]
道岔位置 [DataItem]
列车自动运行 [Function]
控制中心行车调度员 [Organization]
降级运行 [Impact]
```

### 试跑抽出的边示例

```
信号系统 EXCHANGES_DATA 道岔位置
信号系统 HAS_COMPONENT 通信接口
信号系统 PROVIDES_FUNCTION 列车自动运行
信号系统 HAS_STATUS 备用
```

---

## 选型指南

| 场景 | 推荐模式 | 说明 |
|------|----------|------|
| 已知领域，有明确 schema 需求 | Pool Mode | 写候选池，大模型帮你选 |
| 探索陌生文档 | Legacy Mode | 工具自动发现候选项，大模型归纳 |
| 多文档融合 | Pool Mode | 同一个候选池扫多个文档，看不同文档选出什么 |
| 快速原型 | Legacy Mode | 不需要准备候选池，直接跑 |
