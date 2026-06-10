# Engineer AI 自进化阶段 0 设计

> 状态：阶段 0 已固化
> 日期：2026-06-10
> 范围：文档类；不改运行时代码；不重启容器

## 目标

阶段 0 的目标是把 Engineer AI 自进化计划的边界、版本字段、知识分层和记忆层抽象固化为后续阶段的 source of truth。

阶段 0 不实现 EvoAgentX、Mem0、agentmemory 或新的后端 API。它只决定后续实现必须遵守的边界。

## 核心决策

1. Client AI 不进入自进化范围，继续使用现有 RAG `official_only` 主链路。
2. Engineer AI runtime 继续使用现有 `engineer_case`、`engineer_agent_state`、`handoff_packet` 和 evidence tools，仍是生产执行和审计 source of truth。
3. EvoAgentX 只作为离线或 shadow 的 Optimization Lab，用来产生 prompt、受限拓扑和 tool-call policy 候选版本，不直接接管线上流程。
4. 记忆层不再写死为 Mem0，统一抽象为 Case Memory Layer。Mem0、agentmemory、Postgres + pgvector 都是候选 provider。
5. RAG 继续承担文档证据层职责；Case Memory Layer 只承担已确认案例、经验、偏好和 selected memory 的召回。
6. 所有自进化候选版本必须经过离线评估、shadow 对比、人工批准和可回滚门禁后才能灰度。

## 系统分层

```text
Client AI
  -> RAG official_only

Engineer AI runtime
  -> Case Memory Layer search
  -> RAG non_official_only
  -> RAG official_only fallback
  -> evidence packet
  -> engineer approval

Optimization Lab
  -> EvoAgentX
  -> eval dataset
  -> CaseMemoryProvider / AgentMemoryProvider POC
  -> candidate prompt / workflow / tool policy versions
```

## Case Memory Layer

Case Memory Layer 是业务抽象，不绑定具体实现。后续阶段只能依赖这个接口语义，不能让业务流程直接依赖某个 provider 的私有数据模型。

### Provider 候选

| Provider | 推荐用途 | 备注 |
|---|---|---|
| Mem0 | SupportPortal 产品运行时 confirmed case memory POC | 更贴近 AI app / customer support runtime memory。 |
| agentmemory | EvoAgentX / Codex / Claude Code 优化实验室记忆 POC | 更贴近 coding agent、工具轨迹、跨 session 开发记忆和审计。 |
| Postgres + pgvector | 保守 fallback 或自研长期方案 | 运维简单，和现有数据源一致，但需要自建 memory operations。 |

### 必备接口

```text
save_confirmed_case(memory)
search_similar_cases(query, filters)
update_case_memory(memory_id, patch)
deprecate_case_memory(memory_id, reason)
export_memory_trace(memory_id)
```

### 写入规则

只有工程师确认后的案例、纠正或复盘摘要可以写入长期记忆。AI 草稿、未验证 root cause、客户原始敏感信息和内部不可外泄细节不得直接写入。

长期记忆必须保留：

- `source_ticket_id`
- `engineer_case_id`
- `confidence`
- `prompt_version`
- `workflow_version`
- `tool_policy_version`
- `memory_provider`
- `memory_schema_version`
- `related_canonical_doc_ids`

## RAG 与记忆边界

| 知识类型 | 归属 | 使用方 |
|---|---|---|
| 官方文档 | RAG `official_only` | Client AI；Engineer AI fallback |
| 内部技术文档 / runbook / troubleshooting docs | RAG `non_official_only` | Engineer AI |
| 工程师确认案例 | Postgres + Case Memory Layer 摘要 | Engineer AI；Optimization Lab |
| 工程师纠正 / 复盘 | Postgres + selected memory | Engineer AI；Optimization Lab |
| 客户偏好 / 历史处理习惯 | Case Memory Layer | Engineer AI |
| 未确认 AI 草稿 / 猜测 | 不进入长期记忆 | 仅短期上下文 |

重复或相似技术文档仍由 RAG ingestion / retrieval 治理，包括 exact dedupe、near-duplicate clustering、canonical document selection、alias metadata、citation 和 conflict review。不要把重复文档问题交给 Case Memory Layer 兜底。

## Engineer Evidence 顺序

```text
1. Case Memory Layer: 查 confirmed cases / selected memory
2. RAG non_official_only: 查内部技术文档、runbook、排障资料
3. RAG official_only fallback: 查官方 API 语义、客户安全引用、官方依据
4. Evidence Packet: 合并案例、内部文档证据和官方引用，标记 internal/customer-safe
5. Draft / Ask: 证据足够则生成待审批草稿；不足则向工程师请求下一项证据
```

## 版本字段

所有后续阶段产生的运行态、反馈样本、评估样本和优化候选版本，都必须能记录这些字段：

| 字段 | 含义 |
|---|---|
| `prompt_version` | Engineer AI prompt 或 prompt bundle 版本。 |
| `workflow_version` | 工程师端 evidence / draft / approval 流程版本。 |
| `tool_policy_version` | 工具 allowlist、调用顺序、fallback 条件和参数模板版本。 |
| `memory_provider` | 当前记忆 provider，例如 `mem0`、`agentmemory`、`postgres_pgvector`。 |
| `memory_schema_version` | Case memory payload schema 版本。 |
| `memory_index_version` | 记忆索引或召回配置版本。 |
| `eval_dataset_version` | 离线评估集版本。 |
| `rag_access_policy_version` | RAG official / non-official access policy 版本。 |
| `evidence_packet_version` | evidence packet 合并和标注 schema 版本。 |

## 锁死边界

这些边界不得由 optimizer 自动修改：

- Client AI 只使用 `official_only` RAG。
- Engineer AI 不能把内部 source detail 直接暴露给客户。
- 工程师审批不可跳过。
- RAG access mode 是服务端拥有的运行时参数，不是客户或前端可控字段。
- Case Memory Layer 不能成为 source of truth；Postgres 仍保存权威工单、反馈、审计和版本记录。
- 相似但冲突的技术文档不能自动 canonical merge。
- 未确认 AI 草稿和未验证 root cause 不能进入长期记忆。

## 阶段 0 验收标准

阶段 0 完成后必须满足：

1. 有正式设计文档记录 Engineer AI only 范围。
2. 有正式设计文档记录 Case Memory Layer provider 抽象。
3. 有正式设计文档记录 RAG 与记忆边界。
4. 有正式设计文档记录版本字段。
5. HTML 追踪计划指向阶段 0 文档，并标记阶段 0 已固化。
6. 文档验证能检索到关键边界文字。

## 后续阶段入口

阶段 1 可以开始实现结构化 HITL 反馈。阶段 1 不需要先选定 Mem0 或 agentmemory，但反馈 schema 必须能支持后续 Case Memory Layer 写入和 Optimization Lab 评估。
