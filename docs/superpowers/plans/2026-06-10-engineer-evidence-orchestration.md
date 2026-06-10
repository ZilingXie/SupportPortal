# Engineer Evidence Orchestration 实施计划

> **给 agent worker 的要求：** 按任务逐步执行本计划；实施时使用 `superpowers:executing-plans` 和 `superpowers:test-driven-development`。

**目标：** 在新开启工程师调查时，自动附加 Engineer 端 RAG 证据；先查非官网/internal knowledge，仅在需要时追加官网 official fallback。

**架构：** 保持 `search_engineer_evidence()` 作为检索边界。`start_or_refresh_investigation()` 接收内部注入的 evidence builder，将脱敏后的 evidence summary 写入 `engineer_handoff_packet`，再由 `engineer_agent_state` 汇总给 Engineer AI。Internal citation/source 不进入 customer-facing draft。

**技术栈：** Python backend、FastAPI ticket routes、现有 engineer investigation flow、pytest。

---

## 任务 1：Handoff Evidence Payload

**涉及文件：**
- 修改：`backend/services/engineer_evidence_tools.py`
- 修改/新增：`backend/tests/` 下相关测试

- [x] 增加 serializer，将 `EngineerEvidenceSearchResult` 转成紧凑 handoff payload，包含 `internal`、`official_fallback`、`errors` 和 `access_modes`。
- [x] Internal evidence 可以包含 answer summary 和质量诊断，但不能包含 customer-safe citations。
- [x] Official fallback 可以包含 sources/citations，因为官网证据可用于 customer-safe 场景。

## 任务 2：Investigation Flow Hook

**涉及文件：**
- 修改：`backend/services/investigation_flow.py`
- 修改：`backend/services/engineer_agent.py`
- 修改：`backend/tests/test_investigation_flow.py`

- [x] 给 `start_or_refresh_investigation()` 增加可选的 `engineer_evidence_builder` 注入参数。
- [x] 只在新建/opening investigation 时运行 evidence builder。
- [x] 将 payload 写入 `engineer_handoff_packet["engineer_evidence"]`。
- [x] 将 evidence summary 合入 engineer agent 的 known facts / knowledge summary。
- [x] 保持 opening turn 的 `draft_customer_reply` 为空。

## 任务 3：Route Wiring

**涉及文件：**
- 修改：`backend/main.py`

- [x] 在 client RAG 证据不足或显式升级到工程师的 investigation opening 调用点注入 evidence builder。
- [x] Builder 调用 `search_engineer_evidence()`，传入 ticket id、customer id、requester、product、ticket context，以及 handoff packet 中的 client findings。
- [x] 已存在 active investigation 的普通 engineer follow-up 不重新运行 evidence search。

## 任务 4：变更日志与验证

**涉及文件：**
- 修改：`docs/rag_change_log.md`
- 修改：`docs/prompt_change_log.md`
- 如该任务完成一个新的主功能，再修改：`docs/feature_list.md`

- [x] 记录 RAG 数据影响：无 ingestion/schema/backfill 变化。
- [x] 记录 prompt/model 行为影响：Engineer AI opening context 会看到 internal-first evidence summary。
- [x] 运行 targeted tests、`py_compile`、必要时的 feature list 校验，以及 `git diff --check`。
