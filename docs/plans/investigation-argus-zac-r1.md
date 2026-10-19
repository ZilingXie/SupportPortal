# Investigation Argus 相对日期与 Zac 咨询修复（R1）

任务：p2-204，Function：engineer-investigation-reply。当前阶段：独立代码验收前。

本轮修复两条已确认问题：Zac consultation 在 Python 3.11 的嵌套 f-string 语法错误，以及客户续轮说“今天/today”后仍只使用 30 天初查窗口。范围限于代码和后续 Preproduction 技术验证；不重放 `#14089`，不发送客户、Slack、Zendesk、Argus 或 Zac 业务消息，不发布 Production。

## 合同

| ID | 合同 | 当前实现 |
|---|---|---|
| C1 | Zac 模块可在 Python 3.11 编译和导入 | 修正 HTTP 状态码局部变量，避免嵌套 f-string 引号语法错误 |
| C2 | Zac 上游 HTTP 500 是结构化失败 | 返回 `state=failed`、`code=zac_http_500`，不转成 `outcome_unknown` 或完成/接管 |
| C3 | 当前客户消息包含 `today`/`今天` 时使用当天有界查询 | 从当前 turn 的最新 customer comment 生成 `relative_date_window`，按 Asia/Shanghai 服务默认时区计算本地零点至当前 turn 时间 |
| C4 | 相对日期证据可追溯且不冒充确认时间 | 输出 `window_basis=relative_customer_date`、`timezone_source=service_default`、`query_priority=targeted_current_turn`；空结果仍要求更精确绝对时间 |
| C5 | 候选安全与原搜索预算保持不变 | Prompt 要求先执行定向窗口，仍最多三次频道查询，多候选/截断/空结果语义不变 |
| C6 | 业务边界不扩大 | 本轮不调用真实 Argus/Zac，不重放历史工单，不发送业务消息 |

## 改动

- `backend/services/zac_consultation.py`：兼容 Python 3.11 的 HTTP 状态码处理。
- `backend/services/investigation_runtime_policy.py`：识别当前 turn 最新客户消息的 `today`/`今天`，生成有界服务默认时区窗口。
- `backend/services/prompts/hermes_support_agent.py`：要求优先执行当前回合定向窗口并声明时区推断边界，版本升至 v6。
- `backend/tests/test_investigation_proactive_v1.py`：补充中英文相对日期、历史消息排除、Zac 500 结构化失败回归。

## 当前证据

- 基线：`origin/main` @ `8df03512910e889e9dbf79c5b79ced4c59b96996`。
- 专项回归：`backend/tests/test_investigation_proactive_v1.py` 13 passed（项目 `.venv`）。
- 相关 Prompt/Agent 回归：18 passed，72 deselected。
- Python 3.11：三个关键模块 `compileall` 和导入通过。
- `git diff --check`：通过。
- 尚未执行：独立代码验收、Preproduction 发布和技术回读、真实业务样本。
