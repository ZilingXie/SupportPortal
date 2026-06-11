# Graphiti 精简版 — 最终技术总结

## 版本

`simplify` 分支，base commit: `9eea6ab`。对比基线为原始 `graphiti` main 分支。

## 一、代码规模

| | 原始 | 精简 |
|------|------|------|
| Python 文件 | 273 | ~130 |
| 代码行数 | ~57,000 | ~21,000 |
| 数据库驱动 | 4 (Neo4j/FalkorDB/Kuzu/Neptune) | 1 (Neo4j) |
| LLM 客户端 | 5 (OpenAI/Anthropic/Gemini/Groq/Azure) | 1 (OpenAI 协议) |
| Embedder | 4 | 1 (OpenAI 协议 → Ollama) |
| Cross-Encoder | 3 | 1 (BGE, 可选) |
| 搜索配方 | 17 (保留全部) | 17 |
| 服务层 | MCP Server + REST API | REST API (保留) |

## 二、架构

```
┌─────────────────────────────────────────────┐
│                graphiti_rag                  │
│  Scanner → Reader → Splitter → Extractor     │
│         (OCR)  (表格)   ↑          ↓          │
│                     graphiti_core            │
│                add_episode()                 │
│                search() / search_()          │
│                build_communities()           │
└─────────────────────────────────────────────┘
         ↓                          ↓
      graphrag_config.yaml     Neo4j (bolt)
                                   ↓
                            Neo4j Browser
```

## 三、strick vs lenient 对比结论

| 指标 | strict | lenient |
|------|--------|---------|
| Entity | 142 | **175** |
| Edge | 76 | **183** |
| Community | 78 | 48 |
| 技术参数 | 1 | 16 |
| 泛型实体 | 0 | 13 |
| 环境条件 | 0 | 0 |
| 丢失边 | 57% | — |

**结论：默认用 lenient。** strict 删 13 个泛型实体 → 级联丢 107 条边（183→76）。
数据文件：`strict_complete.xlsx`、`lenient_complete.xlsx`。

## 四、新增功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 中文 Prompt | ✅ | 全部 7 个 prompt 汉化 |
| 类型感知去重 | ✅ | `_dedup_key(name, type)` |
| 实体归一化 | ✅ | official_name + synonyms |
| OCR PDF 解析 | ✅ | Docker tesseract chi_sim+eng |
| YAML Schema | ✅ | schemas/gbt25338.yaml |
| Neo4j 属性安全 | ✅ | neo4j_safe_value/attributes |
| Upsert 幂等 | ✅ | ingest_state.json |
| Schema 硬校验 | ✅ | strict/lenient 两种模式 |
| 社区重构 | ✅ | one-shot profile + 代表选择 |
| 社区 timeout | ✅ | 600s timeout + best-effort |
| 批量 LPA | ✅ | 1 次 Cypher 替代 N 次 |
| 配置文件 | ✅ | graphrag_config.yaml + 环境变量 |

## 五、已知限制

1. 社区不是真正后台任务（同步 600s timeout）
2. EnvironmentalCondition 分类不稳定（LLM 随机）
3. 没有 Pydantic response_model（社区 profile 靠 .get() 兜底）
4. 跨 chunk 实体引用会丢边

## 六、部署

见 `DEPLOYMENT.md`。最简部署：

```bash
# 1. Docker 跑 Neo4j
docker run -d -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/graphiti123 neo4j:5.26

# 2. 拉 Ollama + bge-m3
curl -fsSL https://ollama.com/install.sh | sh
ollama pull bge-m3:latest

# 3. 装 Python
git clone https://gitee.com/mengxy98/cusmem.git && cd cusmem
uv sync --extra dev

# 4. 配置
export DEEPSEEK_API_KEY=sk-xxx
vi graphrag_config.yaml  # 改 neo4j.uri 和 embedding.base_url

# 5. 录入
.venv/bin/python3 ingest_gbt.py

# 6. 查询
curl -s http://localhost:7474  # Neo4j Browser
```

## 七、文档索引

| 文档 | 内容 |
|------|------|
| `DEVELOPER_GUIDE.md` | 核心流程详解（add_episode/去重/搜索/时序） |
| `IMPROVEMENTS.md` | 查询效果提升报告（去重/OCR/Prompt/类型系统） |
| `KAG_ANTI_HALLUCINATION.md` | KAG 5 层防御体系分析 |
| `SCHEMA_MODE_REPORT.md` | strict vs lenient 对比 |
| `COMMUNITY_COMPARISON.md` | 完整实体/边源数据 |
| `COMMUNITY_PERF_REPORT.md` | 社区构建性能分析 |
| `CHANGELOG.md` | 代码演进日志 |
| `CODEBASE_GUIDE.md` | 代码入口和结构 |
| `ADD_EPISODE_FLOW.md` | add_episode 全部流程 |
| `GRAPHITI_VS_KAG.md` | Graphiti vs KAG pipeline 对比 |
| `STRICT_VS_LENIENT.md` | 图谱质量对比 |
| `DEPLOYMENT.md` | 安装部署指南 |
