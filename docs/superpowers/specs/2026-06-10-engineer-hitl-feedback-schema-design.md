# Engineer HITL 反馈 Schema 设计

> 状态：阶段 1A 已固化
> 日期：2026-06-10
> 范围：文档与 contract 测试；不新增运行时 API；不重启容器

## 目标

阶段 1A 的目标是先固化 Engineer AI 结构化 HITL 反馈 schema。它为后续反馈 API、Postgres 表、Case Memory Layer 候选生成和 Optimization Lab 评估提供统一数据契约。

阶段 1A 不实现 UI 表单、不写入数据库、不接 Mem0 / agentmemory。它只定义字段、边界和后续接入点。

## 现有系统边界

当前 SupportPortal 已有：

- `support_engineer_cases`：工程师子工单和 investigation 状态。
- `support_engineer_case_messages`：工程师与 Sid 的 investigation 对话。
- `support_engineer_case_events`：工程师子工单事件。
- `support_ticket_agent_events`：client agent run / phase / event trace。
- `/api/engineer/tickets/{ticket_id}/investigation/confirmation`：工程师 approve / revise 客户回复草稿。

这些结构能记录流程动作，但还不能稳定表达“AI 哪些判断对、哪些证据错、工程师如何纠正、是否可沉淀为案例记忆”。阶段 1A 新增的是反馈数据契约，不替代现有 case、message、event 结构。

## 核心原则

1. `approve 不等于 confirmed case`。approve 只表示该次客户回复可以发送，不代表 root cause 已经可作为长期案例记忆。
2. Postgres 是反馈 source of truth。Case Memory Layer 只能消费经确认、脱敏、带安全标签的反馈摘要。
3. 反馈必须能关联 engineer case、client ticket、AI run、evidence packet 和版本字段。
4. 反馈字段优先服务三个后续用途：运行时审计、Case Memory Layer 候选、Optimization Lab eval dataset。
5. 未验证 root cause、AI 草稿、客户原始敏感信息和内部不可外泄细节不能直接进入长期记忆。

## 建议表：support_engineer_hitl_feedback

```text
support_engineer_hitl_feedback
- feedback_id TEXT PRIMARY KEY
- engineer_case_id TEXT NOT NULL REFERENCES support_engineer_cases(engineer_case_id)
- client_ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id)
- run_id TEXT
- message_id TEXT
- evidence_packet_id TEXT
- feedback_type TEXT NOT NULL
- diagnosis_correctness TEXT NOT NULL
- root_cause_correctness TEXT NOT NULL
- evidence_quality TEXT NOT NULL
- citation_quality TEXT NOT NULL
- customer_reply_quality TEXT NOT NULL
- missing_information JSONB NOT NULL DEFAULT '[]'::jsonb
- incorrect_claims JSONB NOT NULL DEFAULT '[]'::jsonb
- corrected_root_cause TEXT
- corrected_solution TEXT
- corrected_customer_reply TEXT
- evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb
- memory_candidate TEXT NOT NULL
- memory_safety TEXT NOT NULL
- memory_notes TEXT
- prompt_version TEXT
- workflow_version TEXT
- tool_policy_version TEXT
- rag_access_policy_version TEXT
- evidence_packet_version TEXT
- created_by TEXT NOT NULL
- created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

## 枚举建议

| 字段 | 允许值 | 含义 |
|---|---|---|
| `feedback_type` | `approve` / `revise` / `reject` / `resolve` / `reopen` | 工程师对本次 AI 输出或流程结果的动作。 |
| `diagnosis_correctness` | `correct` / `partially_correct` / `incorrect` / `not_applicable` | 症状理解和诊断方向是否正确。 |
| `root_cause_correctness` | `confirmed` / `likely` / `incorrect` / `unknown` / `not_applicable` | root cause 是否被工程师确认。 |
| `evidence_quality` | `sufficient` / `partial` / `insufficient` / `wrong` | 证据是否足够支持结论。 |
| `citation_quality` | `correct` / `partial` / `missing` / `wrong` / `not_applicable` | citation 是否正确、可追溯。 |
| `customer_reply_quality` | `sendable` / `needs_edit` / `unsafe` / `not_applicable` | 客户回复是否可发送。 |
| `memory_candidate` | `yes` / `no` / `needs_review` | 是否可以进入 Case Memory Layer 候选流程。 |
| `memory_safety` | `customer_safe` / `internal_only` / `do_not_store` | 后续记忆摘要的安全边界。 |

## JSON 字段结构

### missing_information

```json
[
  {
    "field": "channel_name",
    "reason": "needed_to_reproduce",
    "asked_customer": true
  }
]
```

### incorrect_claims

```json
[
  {
    "claim": "The API always returns 200 for this request.",
    "reason": "contradicted_by_official_doc",
    "correction": "The API can return 4xx when the rule payload is invalid."
  }
]
```

### evidence_refs

```json
[
  {
    "kind": "rag_chunk",
    "source_id": "chunk-123",
    "access_mode": "official_only",
    "customer_safe": true,
    "supports": "corrected_solution"
  },
  {
    "kind": "case_message",
    "source_id": "TK-100-1/msg-3",
    "access_mode": "internal_only",
    "customer_safe": false,
    "supports": "root_cause"
  }
]
```

## 与 Case Memory Layer 的关系

Case Memory Layer 只消费 `memory_candidate=yes` 或经人工 review 后通过的反馈摘要。`memory_safety=do_not_store` 的反馈只能留在 Postgres 审计记录中，不能写入 Mem0、agentmemory 或 Postgres + pgvector 记忆索引。

`memory_candidate=yes` 仍然不是直接写长期记忆。阶段 2 需要单独实现候选摘要、脱敏、安全标签、provider 写入和撤回流程。

## 与 Optimization Lab 的关系

Optimization Lab 可以把结构化反馈转成 eval dataset 样本，用于比较 prompt、workflow 和 tool policy 候选版本。最低需要保留：

- 输入：ticket 摘要、engineer_case_id、AI 输出、evidence_refs。
- 标签：diagnosis_correctness、root_cause_correctness、evidence_quality、citation_quality、customer_reply_quality。
- 纠正：corrected_root_cause、corrected_solution、corrected_customer_reply。
- 版本：prompt_version、workflow_version、tool_policy_version、rag_access_policy_version、evidence_packet_version。

## 接入点

阶段 1B 可以在 backend 增加：

```text
POST /api/engineer/tickets/{ticket_id}/feedback
GET /api/engineer/tickets/{ticket_id}/feedback
```

阶段 1C 可以在 engineer UI 增加轻量反馈表单：

- approve / revise / reject 时收集质量标签。
- 允许工程师补充 corrected root cause、solution、customer reply。
- 允许工程师标记 memory_candidate 和 memory_safety。

## 验收标准

1. 有正式阶段 1A 设计文档记录 `support_engineer_hitl_feedback`。
2. 文档明确 `approve 不等于 confirmed case`。
3. 文档明确 memory_candidate / memory_safety 不会直接写长期记忆。
4. 文档包含 evidence_refs 和版本字段。
5. HTML 计划可追踪阶段 1A 状态，并指向本设计文档。
6. Contract 测试锁住关键字段和边界文字。
