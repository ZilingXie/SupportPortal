# 阶段 5 证据 — Knowledge Review 页面/API（p2-196）

- 计划名称：WeKnora 并行双写与人工治理链（阶段二），修订 r2-stage5；基线 main@bb18dd56
- 分支：codex/weknora-dualwrite-phase5；全代码层交付，零运行态变更（不部署/不发布/不开关/零真实 Slack）

## 合同对照（C1-C14）

| 合同 | 实现 | 测试 |
|---|---|---|
| C1 401+no-store | 两个端点 dependencies=[require_dashboard_session]+Cache-Control: no-store | 未登录双端点 401；登录后响应头断言 |
| C2 过滤分页稳定只读 | projection 列表：五过滤+page/page_size≤100，固定排序 created_at,promotion_id | 过滤命中/422 拒未知状态/越界页空集；读前后仓库 JSON 快照一致 |
| C3 持久化 ID 关联 | standalone：promotion.source_id 内嵌 summary task id（rsplit 复原）；case-bound：engineer_case_id 确定性派生 summary/review task id | 详情聚合断言各环节经真实种子 ID 命中 |
| C4 lineage 缺失/冲突 | resolution_state/reason；case 多源=lineage_conflict+linked_intake_ids 不猜测 | 清空 summary 后 source/summary/duplicate 全 unavailable 带原因 |
| C5 缺源显原因 | source_snapshot.state=missing/unavailable+reason，绝不空 payload | 同上 |
| C6 Summary/Review 状态 | available/not_recorded/unavailable/malformed（review_report_missing） | report 置非 dict→malformed |
| C7 重复证据四态 | duplicate_evidence.state + retrieval_surfaces | malformed 用例+正常 retrieval_surfaces 断言 |
| C8 完整正文 | candidate_payload 全文+target/base_version/content_hash | 列表与详情断言 |
| C9 Slack URL | 仅持久化 channel/thread+KNOWLEDGE_SLACK_WORKSPACE_DOMAIN；缺任一=url:null+reason | workspace 配置时精确 URL；未配置→null+slack_workspace_not_configured |
| C10 双目标独立 | deliveries.{target}：status/external_object_id/version/receipt/readback/failure | weknora accepted（readback）与 agent_memory outcome_unknown（failure_code）同详情并陈 |
| C11 审计 | human_audit：decision/decided_at/principal/email/slack_user_id | approve 后投影断言字段 |
| C12 零写操作 | 页面无决策按钮；API 仅 GET；审批在 Slack 线程 | 读前后快照一致+UI 渲染无写控件 |
| C13 安全转义 | 全部经 escapeHtml()/JSON.stringify+escape；链接 noopener noreferrer | UI 模板逐项 escapeHtml（既有模式延续） |
| C14 InMemory/PG 一致 | 同一 projection 服务读共享仓库面 | PG parity：双侧字段级一致含异常态 |

## 交付清单

- backend/services/knowledge_review_projection.py（新）：列表+详情+slack_thread_url
- backend/automation_ecs_api.py：GET /dashboard/api/knowledge/promotions[/{promotion_id}]（Session+no-store）
- ui/automation-ecs-production/app.js：Knowledge Review Tab（视图切换/过滤/列表徽标/详情六分区/Slack 链接/分页）
- ui/automation-ecs-production/styles.css：view-tabs/knowledge-* 状态样式（含 tone-unknown）
- backend/tests/test_stage5_knowledge_review.py（8 项）+ test_stage5_knowledge_review_postgres.py（1 项 parity）

## 代表性路径复现（测试即路径）

管理员登录→列表（status=human_review）→详情→来源快照（payload ticket.id=990001+references.snapshot_hash）→Summary packet→Review 报告（merge）→重复证据（agent_memory.available=true）→Slack 链接（精确 URL）→双 delivery（weknora accepted+readback / agent_memory outcome_unknown+failure_code）→注入缺源/malformed/outcome_unknown→显式 unavailable/malformed/unknown→读前后快照一致（零写入）。

## 验证汇总

- 专项 **8 passed**；PG parity **1 passed**；全套 19 套件 **524 passed / 0 failed**（RUN_POSTGRES_INTEGRATION=1；阶段 0-4 行为不变）
- node --check / compileall / Overview --check / 快照校验（15+4+56）/ git diff --check / feature_list 校验全过
- 存量披露：test_automation_ecs_api 2 失败与 test_production_ui_contract 1 失败经 stash 前后对照确认为 main 既有

## 边界说明

- 无新表/新迁移/新字段（只读投影）；无页面写接口；不发布 n8n；真实证据维持 waiting-for-evidence（阶段 6）
- 事件披露：调试期间一次 cwd 漂移致 stash 误在根区 pop 外来 p2-184 stash→已 checkout HEAD 复原，根区干净复核过


## 修复轮（2026-10-12，首轮验收四阻断后）

| 阻断 | 修复 | 回归 |
|---|---|---|
| B1 Tab 未接入交互 | bindEvents 补齐四组处理器（#tab-knowledge 切换并加载队列、knowledge-filters 提交、data-knowledge-promotion 详情、data-knowledge-page 翻页；此前的插入因一次 python 编辑脚本在编辑链中丢失，本轮重插并验证） | test_knowledge_tab_handlers_are_bound（源码断言 bindEvents 内含全部四组+切换触发加载） |
| B2 InMemory 无合同排序 | 投影层显式 `rows.sort(key=(created_at, promotion_id))`——合同排序由投影保证，不继承仓库返回顺序（InMemory=插入序、PG=索引序，同数据同分页） | 插入序倒置种子（较新先插）断言输出按 created_at 升序 |
| B3 malformed 误标 available | Summary：completed 且 packet 非对象→malformed(summary_packet_missing_or_not_object)；Review/重复证据：report 为空对象（无 decisions 且无任何检索面）→malformed(review_report_empty) | 字符串 packet→malformed；空 report 对象→review+duplicate_evidence 双 malformed |
| B4 Overview 过期 | 重新 --write（5c80251c）并通过 --check | 命令复跑通过 |

验证：专项 **12 passed**（8+4 新回归）；PG parity 1 passed；全套 19 套件 **528 passed / 0 failed**；node --check/compileall/diff-check/Overview --check/快照校验全过。
