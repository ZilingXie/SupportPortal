# 阶段 4 证据 — Slack 人工 Review（p2-195）

- 计划名称：WeKnora 并行双写与人工治理链（阶段二），修订 r2-stage4
- 任务：p2-195；分支 codex/weknora-dualwrite-phase4；基线 main 8d235da5（含 bcbda671 阶段 0-3）
- 交付时间：2026-10-11；全代码层交付，零运行态变更

## 合同对照

| 合同 | 实现 | 测试 |
|---|---|---|
| C1 根线程 | knowledge_slack_review.deliver：source-only（无 slack lineage）→ 根消息（event_type=knowledge_review_required 已入 _ROOT_EVENT_TYPES）；message_ts 落 slack_thread_ts+slack_review_message_ts+slack_channel_id | test_source_only_candidate_creates_root_thread_and_binding（断言 thread_ts=None+绑定落库+状态仍 human_review） |
| C2 工单线程 | 有绑定 → build_engineer_case_thread_event 回原线程；绑定频道≠配置频道 → failed(thread_channel_mismatch) 零发送 | test_case_bound_candidate_replies_to_its_thread / test_case_bound_channel_mismatch_refuses_send |
| C3 幂等 | event_id=f"knowledge-review:{promotion_id}"（确定性）；delivered 为通知终态（InMemory+PG 同语义），重试返回 already_delivered 不再发；outcome_unknown 重试复用同一 event id | 两个幂等测试（重试后 posts==1；超时恢复后 event id 集合唯一） |
| C4 身份 | resolve_slack_operator：ENGINEER_SLACK_ACCESS_TOKEN → GET users.info；校验 ok/返回 id 一致/非空邮箱；bot 自身拒绝；五类失败（user_not_found/slack_api_failed/empty_email/id_mismatch/bot_message）→ SlackOperatorResolutionError → 决策拒绝零状态变更 | 五个失败类+成功+decide 持久化身份列 |
| C5 mention | 入站要求 raw_text（n8n Draft 新转发字段）包含 <@bot>（bot_user_id 优先取载荷、回落 env）；缺 raw_text 或缺 mention → 422 拒绝；命令前缀判定在剥除 mention 后的文本上 | 缺 raw_text / 提及他 bot 两测试 |
| C6 线程绑定 | 按 channel_id+thread_ts 匹配待审 promotion；错误频道（403）/错误线程（404）拒绝 | test_wrong_thread_finds_no_candidate + 频道校验（expected_channel） |
| C7 消歧 | 0 个 → 404；>1 个 → 409 转 API | 多候选同线程测试 |
| C8 决策 | reject→rejected；approve→decide_weknora_promotion（哈希重算/冲突拒绝/generation 检查/双目标 delivery 状态机不变） | 定向 approve 测试断言 queued+target+base_version+身份列 |
| C9 定向 | target/base_version 缺失 → 422 且零 delivery（promotion 保持 human_review） | missing_target 断言 |
| C10 主开关 | deliver 入口先查 governance+notify 开关 → skipped 零发送零状态；既有 decide fail-closed 409 保留 | 开关关闭测试 |

## 持久化（v23）

六字段（human_decided_by_email / human_decided_slack_user_id / slack_review_event_id / slack_review_status / slack_review_message_ts / slack_review_failure_code）：
- InMemory：WEKNORA_PROMOTION_FIELDS + normalize 默认 + decide 身份列 + 通知状态机两方法；
- PostgreSQL：ALTER ADD COLUMN IF NOT EXISTS ×6（增量）、decide UPDATE 增列、mark/complete 状态机（delivered 终态：mark 的 WHERE slack_review_status IS DISTINCT FROM 'delivered'）；
- 静态镜像 ticket_storage.sql 同步；_TICKET_SCHEMA_VERSION=2026-single-ai-managed-v23-knowledge-slack-review；兼容链含 v18-v22；版本守卫测试更新；
- PG 集成测试：v23 建表、状态机、delivered 终态拒重排、身份列持久化。

## API 收紧

decision 端点：客户端 operator 一律 422（"operator must not be supplied"）；操作人=_request_operator_principal（dashboard session → "dashboard:admin"，否则 "automation-api"）。存量测试断言同步（supplied_operator 422 用例+detail 期望 automation-api）。

## n8n Forward Thread Draft（步骤 9）

- 改前回读：线上 active=b02f3ed5（仓库快照过期，已按规程先行刷新 active 快照+manifest，validator 通过）；
- Draft 变更（1 op，setNodeParameter /body）：Send Hermes Message 转发载荷新增 raw_text: String(input.text || '') 与 bot_user_id: 'U08RVQSJQF2'（text 保持剥除 mention 的既有行为，向后兼容）；
- 回读：active b02f3ed5 未变、draft=4ed2c0c3 分歧、raw_text/bot_user_id 在载荷、validationWarnings=[]；
- 快照：drafts/r1HIW8UNuCabiOPn.draft.json + manifest（draftRedactionCount=0）；validator=15 published+4 drafts+56 redactions 退出码 0；
- C0 合同注意：本链线上版本与仓库旧快照不一致系他轮变更未刷新快照所致，已如实刷新（非本任务变更范围）。

## 验证汇总

- 阶段 4 专项：23 passed（test_stage4_slack_review.py）
- 全套 17 套件：**481 passed / 0 failed**（RUN_POSTGRES_INTEGRATION=1 隔离 PG，含 v23 集成 7 项）
- compileall / git diff --check / Overview --check / snapshot validator 全过
- 真实 Slack 业务消息：零发送（全部 FakeSlackPoster/stub）；真实发送证据=waiting-for-evidence（阶段 6 授权后补齐）
- 未开治理开关、未部署、未触碰 Production

## 边界说明

- 测试载体固定 promotion_id/source_id/candidate_id/event_id/channel_id/thread_ts/slack_user_id；成功场景同时断言 Slack payload、promotion 持久化字段、身份列、绑定、状态、重试前后消息数量；
- delivery 补写行为沿用阶段 1 状态机（approve 后只补 failed/unknown 目标），阶段 4 未改动（由既有 test_knowledge_dual_write 覆盖）。
