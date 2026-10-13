# Hermes Enablement Reply Delivery and Natural Persona v1.1

当前修订：**v3.1，R2-F1 投递时间水位修复**。任务 `p2-198`，Draft PR [#1485](https://github.com/ZilingXie/SupportPortal/pull/1485)。

## 版本与门禁

- 原基线：`42a187ae884015b833277b7b4e23d624a647663b`。
- 修复起点：`378cc6fb6fd716c858777b4f13f6a7338626bb36`。
- 集成 main：`9418f7f1aa2d3a474f0ef12a3596d6a0afda5ac1`；保留其 fixed-task Investigation 人工复核门禁、发布保护和 `p2-199`。
- 分支/worktree：`codex/hermes-natural-persona` / `.worktrees/hermes-natural-persona`；完整最终 HEAD 以 PR 描述和交接消息为准。
- 状态：实现与执行者验证完成，**独立验收待执行**。不 finalize、不合并、不部署，不重放/重开 14009 或 14027，不向真实 Slack/Zendesk/relay 发送。
- Zendesk footer、签名、营销内容和 Agent profile 保持原边界。

## 本轮 R2-F1 投递确认修复（v3.1）

独立验收在 `6f285d2bfac018216cb44f0e557cf0c5a1ce2c04` 发现：内部附件标记剔除后，纯文本 worker 回读未携带时间水位；audit 解析器也仅在存在附件时检查时间。10:00 创建的 delivery 可错误绑定 09:00 相同正文历史评论，未发送本轮回复就释放 relay。分类：implementation / test-carrier。其他已关闭 finding 与模型自然度非阻断观察保留。

- Hermes 回读始终传入当前 delivery.created_at；缺失时直接保持门禁，不自动重发。
- audit 在提供 not_before 时独立执行时间检查；旧评论、缺失/无法解析/无时区时间不确认。附件 ID/name/size 校验与 Zendesk footer 匹配保持。
- 真实 worker + audit 解析器 + InMemory ledger/request，仅 mock 外部 audit 获取与客户写入边界：manual/auto 两流程 × 九种时间组合；旧评论零写入、ledger outcome_unknown、邮件 awaiting_public_reply、request gated；有效当轮评论绑定正确 ID、单次释放、重放单条投影。
- 反向验证分别撤回调用水位和解析时间门：两项均由行为断言检出，见 [本轮负控](../evidence/hermes-natural-persona-v3/audit-watermark-negative-controls.json)。
- 当前 main 新增仅为 p2-199 发布证据/登记，已集成；Overview 冲突仅通过生成器解决。没有新增运行行为。
- 本轮相关回归 **62 passed、9 skipped、33 subtests**；worker Hermes delivery 矩阵 **4 passed + 4 subtests**。9 skips 是既有附件/通知的隔离 PostgreSQL 场景，完整结果/复现命令见 [水位验证](../evidence/hermes-natural-persona-v3/audit-watermark-verification.json)。本轮不复跑 PG 或真实模型，保留前轮未受影响证据。
- 额外宽泛 worker 回归仍检出原有 14 项 ownership eligibility 失败（186 passed、9 skipped、25 subtests，含其他测试）；不把它作为全绿。独立验收仍待执行。

## 前轮合同与证据（v3，未受影响证据保留）

| Finding | 机制与真实入口 | 本轮证据 | 执行者状态 |
| --- | --- | --- | --- |
| 14027 native 回归 | `tool_execute_automation_action` → canonical basis → `tool_save_reply_draft` → publication → prepare → ledger → `_deliver_hermes_zendesk_comment` → next customer turn | `test_hermes_native_enablement_regression.py`：无 App ID、残留 workflow、实际 manual workflow 完成；False 不要求 job/不退队，True 携带精确 job | 已提交验证，待独立验收 |
| R2-F1 | 真实 manual 与 auto fixture；auto 调用 `create_enablement_relay_request`；实际 worker readback 后 repository 释放 | InMemory request 首次唯一赢家、重复无赢家、邮件 not_applicable、dispatch_pending；PG worker 投影后 4 线程仅 1 赢家、重复 1 条投影；PG manual 独立 email_released/pending；身份负例 | 已提交验证，待独立验收 |
| R2-F2 | `_claim_automation_ownership_before_work` → 实际内部升级/持久化/告警组装；外部 ownership/Zendesk/Graph 模拟 | 真实告警含 phase/code/reason/ticket/case/job/attempt/turn；真实已存在 run 或 not_started；实际 processor 只提交 Route 模型请求，拒绝后无 Work/Persona/delivery | 已提交验证，待独立验收 |
| R2-F3 | 固定 `persona-instruction-opener-v2` 审批 catalog | 39 tests + 55 subtests：固定批准 hash、新 schema、旧 precise/signature、漂移/未发布/额外 Persona；历史签名操作 catalog 失配零 POST，baseline/manifest/pointer 字节不变 | 已提交验证，待独立验收 |
| R2-F4 | 完整共享 v6 contract/manual + sid-precise v2；真实 `build_case_snapshot`/canonical basis/validator/greeting/translation | [完整模型证据](../evidence/hermes-natural-persona-v3/model-evaluation.json)、[原文评审](../evidence/hermes-natural-persona-v3/natural-text-review.md)；9 样本，7 项载体负控；自动安全与文本评审分开 | 已提交验证，待独立验收 |

### 已修复的真实链路问题

1. Hermes delivery 的草稿 UUID 与 PG 消息数值 ID 不同。兼容投影现在持久化 `hermes_delivery_message_id`；manual/auto delivered-readback 查询仅对 `source=hermes` 允许该关联，仍要求同 case、ticket、assistant、job、public、delivered。
2. PG 读取消息会把 metadata 展开到顶层。worker 同时识别展开和嵌套形式，重放不再重复投影。
3. `_hermes_compatibility_relay_required` 属于服务端控制标记，和 job 标记一样从发送附件及审计附件中剔除；不作为 Slack 文件下载。
4. request 的 ticket、Zendesk ticket、workflow job 和存在时的 workflow request identity 必须一致；错误关联不释放。
5. ownership gate 在 run 启动前明确记录 `not_started`，有 run 时使用实际 turn run ID。
6. 评估实际发现旧 Investigation contract 会凭“还需分析”授权“团队正在继续调查”。已改为仅允许权威结论明确记录的活动；最终模型证据对应这次 Prompt 修正。

### 模型与自然表达证据边界

实际只读读取 Preproduction worker task definition `supportportal-preproduction-worker:143` 的 `AGENT_MODEL_ID=gpt-6.1-sol`；Persona 使用运行时 `STANDARD_PHASE_REASONING_EFFORT=medium`。评估使用同模型和推理配置，经 Responses API 的离线适配器执行，不启动真实 Hermes case run。返回模型 ID、完整请求、源 HEAD/文件 hash、原始输出、称呼投影和最终语言正文均存入证据。

原七样本保留，增加独立费用解释（Billing 主题走已存在的 Investigation 链，仓库没有独立 `billing` Work route）及真实 `account_verification` 场景。期望判定不进入模型输入。相同 system SHA 合理：共享完整合同，方向/路由/客户/basis 在上下文中。

英文草稿称呼由服务端唯一投影为 `Hi <Name>,` / `Hi Customer,`；非英文最终称呼经过既有翻译路径本地化，未私自改写翻译合同。无可靠姓名仍保留 Customer 兜底。

此处证明代码快照和合成模型行为，**不证明线上 v6 Prompt Release 或 sid-precise v2 published 内容已生效**。自然度由原文评审说明，validator 通过不等于自然度通过。

## 可重现验证

使用项目根 `.venv/bin/python`。测试 PostgreSQL 是任务专用容器，监听 `127.0.0.1:15438`，每个用例独立 schema 并清理；不用远端数据库。DSN 由测试环境传入，skip 不算证据。

```sh
RUN_POSTGRES_INTEGRATION=1 TICKET_DB_DSN=<isolated-test-dsn> ../../.venv/bin/python -m pytest \
  backend/tests/test_hermes_customer_reply_basis.py backend/tests/test_hermes_zendesk_agent_tools.py \
  backend/tests/test_hermes_fraud_reply_style.py backend/tests/test_hermes_email_execution.py \
  backend/tests/test_hermes_tool_failure_handoff.py backend/tests/test_hermes_zendesk_agent.py \
  backend/tests/test_hermes_native_enablement_regression.py backend/tests/test_worker_compatibility_scope.py \
  backend/tests/test_enablement_auto_postgres.py backend/tests/test_rerun_automated_account_cases.py \
  backend/tests/test_ownership_gate_alert_context.py backend/tests/test_natural_persona_eval.py \
  backend/tests/test_native_hermes_fixed_task_review.py backend/tests/test_native_hermes_release_guard.py -q
```

结果：**291 passed + 75 subtests**，没有 PG skip。独立 PG 套件 **5 passed + 5 subtests**；Hermes worker delivery 矩阵 **4 passed + 4 subtests**。

反向验证：`scripts/testing/verify_hermes_repair_negative_controls.py --output <json>`，在可销毁源码副本上修改，任务源码不变。七项均由行为断言检出：删除 scope flag、按 route 推断、未确认投递就释放、漏建真实 request、重复释放、删除 gate phase、删除 gate reason。见 [负控输出](../evidence/hermes-natural-persona-v3/negative-controls.json)。收集/import 错误不算检出。

完整 worker 在分支和 main 都为 **153 passed、14 failed、25 subtests**；每个失败/子测试及断言消息见 [worker-baseline.json](../evidence/hermes-natural-persona-v3/worker-baseline.json)。14 项逐项相同，均在旧 Investigation fixture 的 ownership eligibility 断言失败；不把新增失败纳入基线。

编译、JSON、Overview、diff 和 merge-tree 结果在 PR 交接中登记。旧登记的 helper/mail pending/7 个简化样本不作为本轮关闭证据。

## 后续停点

提交同一 Draft PR 的完整 HEAD，等待一次独立验收。匹配通过后才按原发布流程 finalize，并重新读取 Preproduction release/task/config/Prompt，发布候选代码和 v6 Prompt Release。新工单验证缺 App ID → 正常追问 → ownership 保持 → 补充 → 后续处理；实际 relay 保留人工审批。代码、部署、真实业务分别验收，Production 单独授权。
