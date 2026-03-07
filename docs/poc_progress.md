# 本地 POC 进展追踪

## 使用说明
1. 本文件用于跟踪阶段状态、测试结果、阻塞项与闸门决策。
2. 阶段状态仅允许使用：`Not Started | In Progress | Blocked | Ready for Gate | Passed | Failed`。
3. 测试用例 ID 仅允许使用：`P{阶段号}-T{序号}`。
4. 所有证据路径遵循：`artifacts/phase-{n}/...`。
5. 任何阶段未通过闸门时，禁止进入下一阶段。

## 阶段状态看板
| Phase | Name | Owner | Planned Start | Planned End | Current Status | Gate Decision Time | Notes |
|---|---|---|---|---|---|---|---|
| 1 | 本地环境与骨架 | Codex | 2026-03-04 | 2026-03-04 | In Progress | - | 已创建后端与三端独立目录 |
| 2 | 问答与情绪核心 | Codex | TBD | TBD | Not Started | - | - |
| 3 | 实时通知链路 | Codex | TBD | TBD | Not Started | - | - |
| 4 | 三端 UI 可用性 | Codex | TBD | TBD | Not Started | - | - |
| 5 | 本地验收与报告 | Codex | TBD | TBD | Not Started | - | - |

## 每日进展日志
| Date | Phase | Status | What Changed | Evidence Path | Next Step | Owner |
|---|---|---|---|---|---|---|
| 2026-03-04 | P1 | In Progress | 完成 `backend/`、`client_ui/`、`engineer_ui/`、`dashboard/` 骨架与最小 API/WS 联通 | `artifacts/phase-1/init/` | 执行 P1-T1~P1-T3 并登记结果 | Codex |
| 2026-03-05 | P4 | In Progress | `client_ui` 按 `ui_for_client` 信息架构重构为登录/聊天主页/会话详情/会话历史四视图，保留与本地后端 API 联通 | `artifacts/phase-4/client-ui-redesign/` | 浏览器回归并执行 P4-T1~P4-T3 | Codex |
| 2026-03-05 | P2 | In Progress | 接入 LangChain RAG：客户提问优先走 PostgreSQL(pgvector) 检索+LLM 回答，失败时自动回退 FAQ | `artifacts/phase-2/rag-integration/` | 配置 `OPENAI_API_KEY` 与 `PGVECTOR_DSN` 后执行检索命中验证 | Codex |
| 2026-03-05 | P2 | In Progress | RAG 回答风格升级为 DocAgent 同款：结构化 JSON（answer/key_steps/citations/insufficient_evidence）+ citation 校验 + 严格重试 | `artifacts/phase-2/rag-answer-style/` | 增加前端 citations 展示并做回归验证 | Codex |
| 2026-03-07 | P4 | In Progress | 客户端输入交互升级：`Enter` 发送、`Shift+Enter` 换行；发送中按钮切换为停止图标，可中断生成并编辑刚发送消息后重发 | `artifacts/phase-4/client-input-interaction/` | 浏览器手工验证停止与编辑重发链路 | Codex |
| 2026-03-07 | P1 | In Progress | 新增 PostgreSQL 工单持久化仓储层（tickets/messages/events），后端接口读写已切换为 DB，并保留无 DSN 的内存回退模式 | `artifacts/phase-1/ticket-db-persistence/` | 按 P1/P2 闸门补齐自动化测试与证据归档 | Codex |
| 2026-03-07 | P3 | In Progress | 管理端新增历史事件接口 `/api/dashboard/events`（从 PostgreSQL 读取），工程师端与管理端状态栏显示 `Storage: postgres/memory` 便于核验数据源 | `artifacts/phase-3/dashboard-db-events/` | 浏览器验证 admin 首屏历史事件与实时事件叠加显示 | Codex |

## 阻塞与处理
| Blocker ID | Date | Phase | Description | Impact | Mitigation | Owner | Status |
|---|---|---|---|---|---|---|---|
| B-001 | YYYY-MM-DD | P2 | 示例：模型下载失败 | 阻断 P2-T2 | 切换镜像源并重试 | TBD | Open |

## 测试结果登记表
说明：下表列名不可修改，用于阶段闸门判定与审计追踪。

| Case ID | Objective | Command/Action | Expected | Actual | Result | Evidence |
|---|---|---|---|---|---|---|
| P1-T1 | Python 版本检查 | `python --version` | `3.10.x` | TBD | Not Started | `artifacts/phase-1/p1-t1/` |
| P1-T2 | 依赖导入检查 | `python -c "import fastapi,chromadb,transformers,torch"` | 无异常 | TBD | Not Started | `artifacts/phase-1/p1-t2/` |
| P1-T3 | 服务健康检查 | `GET /health` | `200` | TBD | Not Started | `artifacts/phase-1/p1-t3/` |
| P2-T1 | FAQ 准确率评估 | 100 问题评估脚本 | `>=85%` | TBD | Not Started | `artifacts/phase-2/p2-t1/` |
| P2-T2 | 高情绪触发评估 | 50 情绪样本评估 | `100%` | TBD | Not Started | `artifacts/phase-2/p2-t2/` |
| P2-T3 | 复杂工单抽测 | 10 工单回归 | 响应结构完整 | TBD | Not Started | `artifacts/phase-2/p2-t3/` |
| P3-T1 | WS 连接稳定性 | 长连接观察 | 无异常断开 | TBD | Not Started | `artifacts/phase-3/p3-t1/` |
| P3-T2 | 推送延迟测试 | 事件注入 + 计时 | `<=30s` | TBD | Not Started | `artifacts/phase-3/p3-t2/` |
| P3-T3 | 广播一致性 | 状态更新广播 | 双端一致 | TBD | Not Started | `artifacts/phase-3/p3-t3/` |
| P4-T1 | 三端路由可访问 | 访问 `/client` `/engineer` `/dashboard` | `200` | TBD | Not Started | `artifacts/phase-4/p4-t1/` |
| P4-T2 | 首屏加载时间 | 浏览器性能采样 | `<=2s` | TBD | Not Started | `artifacts/phase-4/p4-t2/` |
| P4-T3 | UI 规范一致性 | 按 `docs/agent.md` checklist 核对 | 无 P0/P1 偏差 | TBD | Not Started | `artifacts/phase-4/p4-t3/` |
| P5-T1 | 全量回归 | P1-P4 关键用例复测 | 全通过 | TBD | Not Started | `artifacts/phase-5/p5-t1/` |
| P5-T2 | 报告完整性 | 检查 `local_test_report.md` | 指标+证据齐全 | TBD | Not Started | `artifacts/phase-5/p5-t2/` |
| P5-T3 | 闭环演示 | 提问->告警->处理->看板更新 | 全流程成功 | TBD | Not Started | `artifacts/phase-5/p5-t3/` |

## 闸门决策记录（Pass/Fail + 时间 + 责任人）
| Phase | Decision | Decision Time | Decision Owner | Required Cases | Failed Cases | Rework Action | Recheck Time |
|---|---|---|---|---|---|---|---|
| P1 | Pending | - | TBD | P1-T1,P1-T2,P1-T3 | - | - | - |
| P2 | Pending | - | TBD | P2-T1,P2-T2,P2-T3 | - | - | - |
| P3 | Pending | - | TBD | P3-T1,P3-T2,P3-T3 | - | - | - |
| P4 | Pending | - | TBD | P4-T1,P4-T2,P4-T3 | - | - | - |
| P5 | Pending | - | TBD | P5-T1,P5-T2,P5-T3 | - | - | - |

## 阶段推进规则
1. 阶段进入 `Ready for Gate` 前，必须补齐该阶段全部测试记录（含 Evidence）。
2. 任一必测用例 `Failed`，阶段决策必须为 `Failed`，并记录修复动作。
3. 阶段决策为 `Passed` 后，下一阶段状态才能从 `Not Started` 切换到 `In Progress`。
4. 若出现跨阶段影响，需将受影响阶段回退为 `In Progress` 并补回归记录。

## 发布决策记录
| Release ID | Date | Scope | Gate Summary | Decision | Owner | Notes |
|---|---|---|---|---|---|---|
| LOCAL-POC-R1 | YYYY-MM-DD | P1-P5 | 待填充 | Pending | TBD | 待阶段全部通过后更新 |
