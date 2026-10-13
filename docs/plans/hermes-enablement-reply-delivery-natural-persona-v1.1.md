# Hermes Enablement Reply Delivery and Natural Persona v1.1

当前修订：**v3.2，14037 compatibility scheduler 运行修复**。任务 `p2-198`；原 PR [#1485](https://github.com/ZilingXie/SupportPortal/pull/1485) 已合并，后续 Draft PR 保留同分支并链接原 PR。

## 版本与门禁

- 原基线：`42a187ae884015b833277b7b4e23d624a647663b`。
- 修复起点：`378cc6fb6fd716c858777b4f13f6a7338626bb36`。
- 集成 main：`9418f7f1aa2d3a474f0ef12a3596d6a0afda5ac1`；保留其 fixed-task Investigation 人工复核门禁、发布保护和 `p2-199`。
- 分支/worktree：`codex/hermes-natural-persona` / `.worktrees/hermes-natural-persona`；完整最终 HEAD 以 PR 描述和交接消息为准。
- v3.1 独立代码验收通过 HEAD：`41f27f48ac6e2247d940d2fbd0387fd45c8ad3ce`；已 finalize，合并源码/main：`4ecea0431f1b84490bab4bf51220faeef815e6b2`，CodeGraph 与 CodeSight 已刷新。
- 原代码与 v6 Prompt 已发布 Preproduction：`r20261013-4ecea04` / `pr-78386473bd08`；sid-precise published v2。技术发布成功，业务验收未完成。
- 本轮局部运行修复仅在原 worktree 实施，等待独立验收；**不再次 finalize/部署**，不重开、回放或修写 14037，不强制释放邮件/relay。Production 未发布，真实 relay 执行保留人工审批。
- Zendesk footer、签名、营销内容和 Agent profile 保持原边界。

## 运行修复与证据（v3.2）

发布源码为 `main@4ecea0431f1b84490bab4bf51220faeef815e6b2`。本轮提交集成最新 main `0124173bbc6c818efd89f05de9127684f6133ac4`（#1491 native Investigation 草稿保存），共享保存入口的集成验证单独记录；Preproduction 未随本地集成变化。同分支因原 PR 已合并，提交后续 Draft PR。原 v3.1 时间水位与附件/footer 合同继续保持，Prompt/basis 内容不变。

**R2-F1-runtime-14037（implementation，原兼容身份合同）**：真实新测试工单 14037 缺 App ID 首轮正常追问，补齐后客户确认回复已投递；其兼容 job 被旧 worker 领取，prepare/publish 将 `hermes_compatibility_only=True` 的 job 置为 `manual_attention`。投递成功收尾正确拒绝终态身份，事件 `hermes_enablement_compatibility_release_blocked / enablement_compatibility_job_terminal`，内部邮件仍 awaiting_public_reply。后续观察到工单 closed/solved，不代表邮件释放恢复，也不认定关闭来源。没有改写该真实 job 或回放工单。

修复合同与载具：

| 合同 | 最小机制 | 实际入口与可观察结果 |
| --- | --- | --- |
| C1 创建即归属 Hermes | tool 向 manual/archer workflow 传递 compatibility-only；creator 在第一次 repository save 前设置所有权 | 实际 tool→workflow→save，save spy 立即运行真实 scheduler；marker 已持久化、零 legacy renderer、job 保持活动状态 |
| C2 旧 scheduler 不领取 | InMemory 与 PG claim candidates 在 limit 前排除 JSON boolean true | compatibility job 不领取，普通 legacy control 仍能在 limit=1 被领取，不饥饿 |
| C3 已领取兼容身份不终态化 | prepare 读取当前 payload；prepare/publish 使用既有 Persona queued 状态，claim filter 防止再次处理 | 先领取旧 payload，再持久化 Hermes 所有权，两个真实 worker 入口零 render/发送；manual/auto 的真实 audit parser→ledger→repo 回读只释放一次、单条投影 |
| C4 旧 claim 不能移除所有权 | InMemory lock / PG UPDATE CAS 条件拒绝 marker true→非 true 的 claimed update | 相同 claim fence 仍不能覆盖所有权，携带正确 marker 的恢复更新可成功；terminal/unknown 白名单不变 |

- 最新 main@0124173b 集成：新增 #1491 native Investigation draft-save 测试，真实 memory/PG API 与共享保存入口回归 **308 passed + 48 subtests，无跳过**；五项运行修复负控在集成版本继续检出。原模型证据绑定已发布的 4ecea043，不声称新集成版本已重新执行真实模型。
- 定向核心/native/compatibility/version/publication 回归：**265 passed + 48 subtests，无跳过**；包括本地 PostgreSQL 16 隔离 schema。新 scheduler tests **10 passed**；PG suite **6 passed + 15 subtests**（其中新增十个恢复/调度子场景，含 legacy/v8 状态）。
- 七个负控：遗漏源头所有权、允许 scheduler claim、将恢复置为 manual_attention、忽略最新 payload、stale claim 清除所有权，全部被行为断言检出；不把 import/collection 失败当作检出。
- 本轮新增失败转人工与 publish 竞态：InMemory/隔离 PostgreSQL 均证明旧 payload 不会清除 Hermes marker，不进入 `manual_attention`，不写客户消息；七项负控全部检出。
- 最新 main@c9722399 集成后，真实候选与 `git archive origin/main@0124173b` 五套扩大回归均 **254 passed、20 failed、32 subtests**，逐项失败签名一致（14 worker + 6 legacy intake），见 [最新基线对比](../evidence/hermes-natural-persona-v3/runtime-scheduler-baseline.json)。
- 完整 worker 分支与发布 main 均 **153 passed、14 failed、25 subtests**，14 个失败签名相同。补充 legacy intake/manual/auto/version 四套分支与 main 均 **101 passed、6 failed、7 subtests**；同六个旧 fixture 失败，未弱化断言。
- 复现命令与文件 hash：[本轮验证](../evidence/hermes-natural-persona-v3/runtime-scheduler-verification.json)、[七项负控](../evidence/hermes-natural-persona-v3/runtime-scheduler-negative-controls.json)。仅 synthetic PostgreSQL，监听 `127.0.0.1:55437`；没有使用远端数据库作为测试数据库，无 migration/新状态。

### 已完成的发布与实际业务边界

[脱敏发布记录](../evidence/hermes-natural-persona-v3/preproduction-release.json)：API:146 / Route:144 / Worker:146，镜像 digest/readback、Prompt v6 活化、native Hermes enabled、typed workflow disabled、健康与 Terraform 零 drift 等正式门禁通过，manual Enablement 配置保持。sid-precise published v2 hash 对应已批准内容，footer/profile 未修改。采集时 target healthy=1/draining=1/blocking=0；全 pipeline 1431.914 秒超过 900 秒 SLO（Preproduction 部署阶段为 810.711 秒），没有放宽门禁。

[脱敏真实业务记录](../evidence/hermes-natural-persona-v3/preproduction-business-readback.json)：

- 14037：两轮客户回复确认送达，首轮 automation 保持；补齐后兼容身份终态化阻断邮件释放。当前修复未发布，真实链未复验。
- 14038：Fraud 缺资料追问→客户部分补资料→下一轮实际回复→审核邮件 sent→human_review_required 均已观测；后续 snapshot closed/solved。
- 14039：Suspension 真实回复、审核邮件 sent、closed/solved 已观测；旧观测脚本要求 human_review_required 与 handoff-and-close 合同不一致，不把该脚本整组记为通过。完整 reviewer assignment 未读回。
- 14040：Investigation Work completed、report 记录、零草稿；未审批复核或进入 Persona，不能计为 Investigation 投递证明。
- Invoice/Verification：只证明 SMTP 提交，六分钟内没有 case linking 证据；不重复发信来制造通过。

实际正文可读，但偏正式的诉求复述仍是非阻断自然度观察。数据库 `delivered` 与记录的 Zendesk comment ID 证明各自投递层；不扩张为所有业务流程完成。没有执行真实 relay 或 Production 发布。

## 已验收历史：R2-F1 投递确认修复（v3.1）

独立验收在 `6f285d2bfac018216cb44f0e557cf0c5a1ce2c04` 发现：内部附件标记剔除后，纯文本 worker 回读未携带时间水位；audit 解析器也仅在存在附件时检查时间。10:00 创建的 delivery 可错误绑定 09:00 相同正文历史评论，未发送本轮回复就释放 relay。分类：implementation / test-carrier。其他已关闭 finding 与模型自然度非阻断观察保留。

- Hermes 回读始终传入当前 delivery.created_at；缺失时直接保持门禁，不自动重发。
- audit 在提供 not_before 时独立执行时间检查；旧评论、缺失/无法解析/无时区时间不确认。附件 ID/name/size 校验与 Zendesk footer 匹配保持。
- 真实 worker + audit 解析器 + InMemory ledger/request，仅 mock 外部 audit 获取与客户写入边界：manual/auto 两流程 × 九种时间组合；旧评论零写入、ledger outcome_unknown、邮件 awaiting_public_reply、request gated；有效当轮评论绑定正确 ID、单次释放、重放单条投影。
- 反向验证分别撤回调用水位和解析时间门：两项均由行为断言检出，见 [本轮负控](../evidence/hermes-natural-persona-v3/audit-watermark-negative-controls.json)。
- 当前 main 新增仅为 p2-199 发布证据/登记，已集成；Overview 冲突仅通过生成器解决。没有新增运行行为。
- 本轮相关回归 **62 passed、9 skipped、33 subtests**；worker Hermes delivery 矩阵 **4 passed + 4 subtests**。9 skips 是既有附件/通知的隔离 PostgreSQL 场景，完整结果/复现命令见 [水位验证](../evidence/hermes-natural-persona-v3/audit-watermark-verification.json)。本轮不复跑 PG 或真实模型，保留前轮未受影响证据。
- 额外宽泛 worker 回归仍检出原有 14 项 ownership eligibility 失败（186 passed、9 skipped、25 subtests，含其他测试）；不把它作为全绿。该版本随后在 `41f27f48ac6e2247d940d2fbd0387fd45c8ad3ce` 独立验收通过；当前待验收的是 v3.2 运行修复。

## 前轮合同与证据（v3，未受影响证据保留）

| Finding | 机制与真实入口 | 本轮证据 | 执行者状态 |
| --- | --- | --- | --- |
| 14027 native 回归 | `tool_execute_automation_action` → canonical basis → `tool_save_reply_draft` → publication → prepare → ledger → `_deliver_hermes_zendesk_comment` → next customer turn | `test_hermes_native_enablement_regression.py`：无 App ID、残留 workflow、实际 manual workflow 完成；False 不要求 job/不退队，True 携带精确 job | v3/v3.1 独立代码验收通过；原证据保留 |
| R2-F1 | 真实 manual 与 auto fixture；auto 调用 `create_enablement_relay_request`；实际 worker readback 后 repository 释放 | InMemory request 首次唯一赢家、重复无赢家、邮件 not_applicable、dispatch_pending；PG worker 投影后 4 线程仅 1 赢家、重复 1 条投影；PG manual 独立 email_released/pending；身份负例 | v3/v3.1 独立代码验收通过；原证据保留 |
| R2-F2 | `_claim_automation_ownership_before_work` → 实际内部升级/持久化/告警组装；外部 ownership/Zendesk/Graph 模拟 | 真实告警含 phase/code/reason/ticket/case/job/attempt/turn；真实已存在 run 或 not_started；实际 processor 只提交 Route 模型请求，拒绝后无 Work/Persona/delivery | v3/v3.1 独立代码验收通过；原证据保留 |
| R2-F3 | 固定 `persona-instruction-opener-v2` 审批 catalog | 39 tests + 55 subtests：固定批准 hash、新 schema、旧 precise/signature、漂移/未发布/额外 Persona；历史签名操作 catalog 失配零 POST，baseline/manifest/pointer 字节不变 | v3/v3.1 独立代码验收通过；原证据保留 |
| R2-F4 | 完整共享 v6 contract/manual + sid-precise v2；真实 `build_case_snapshot`/canonical basis/validator/greeting/translation | [完整模型证据](../evidence/hermes-natural-persona-v3/model-evaluation.json)、[原文评审](../evidence/hermes-natural-persona-v3/natural-text-review.md)；9 样本，7 项载体负控；自动安全与文本评审分开 | v3/v3.1 独立代码验收通过；原证据保留 |

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

新 Draft PR 完整 HEAD 独立验收 v3.2 scheduler/ownership 修复；原 #1485 已通过项不受影响。通过后按既有授权 finalize、刷新 CodeSight 并发布修复到 Preproduction，随后使用新工单复验完整 Enablement 链。当前不重新部署、不重开/回放 14037，不强制释放旧 job。真实 relay 执行保留人工审批；Production 单独授权。Invoice/Verification intake、Investigation 复核/Persona 与未取得的 reviewer readback 继续记录为缺少证据。
