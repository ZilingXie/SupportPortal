# SupportPortal Project Registry

`docs/project/` 是 SupportPortal 项目计划和进度的 canonical registry。

## 维护边界

- Task 当前状态只修改 `tasks/<task-id>.json`；Phase、Module、Function、Board、Meeting、动态和汇报都是生成视图或层级元数据。
- Meeting 记录决定和讨论，不复制 Task 的 owner、status、next action 或 evidence。
- `done` 必须附带 PR、测试、部署、文档或决定证据；`blocked` 必须说明 blocker。
- `docs/feature_list.md` 继续维护产品能力清单，不承担 Task 进度。
- `generated/` 和 `docs/projectoverview-data.js` 是生成物，不直接手工修改。
- PR 的公开摘要写入 `pr_summaries.json`，不要把 PR body、客户数据、邮箱、日志或 secret 写入公开页面。
- 新能力先归入一个 Function，再把可验收的小功能记录为 Task；运行时行为、用户流程、API、数据模型、配置或业务结果发生变化的功能/修复类工作必须关联唯一 Task。纯文档、测试、规则、开发者脚本和运维变更只有在改变进度记录时才需要重生成 Overview。

## 层级和 ID

- Phase 只有 `phase-1`、`phase-2`、`phase-3`；当前 Phase 1 聚焦 Account Automation、Admin Operations 和 Platform Delivery，其他已登记任务归入 Phase 2，Phase 3 作为后续预留阶段。
- Module 是稳定业务域；Function 是可以单独汇报的能力；Task 是具体可执行、可验收的小功能。
- Function 使用语义 ID，例如 `routing-taxonomy`；Task 使用 `pN-xx`，页面统一显示 `#` 前缀。
- Function 状态从子 Task 派生，不维护第二套手工状态；Task 调整 Phase 时必须保留旧 ID alias。
- `migration_manifest.json` 保存旧 Task ID、Meeting、PR 和历史 hash 的迁移目标。

## 维护命令

```bash
python3 scripts/generate_project_overview.py --refresh-prs --write
python3 scripts/generate_project_overview.py --check
```

开工前先搜索已有 `task_id`；没有对应 Task 时先创建，再开始实现。提交前把 Task 更新为正确状态并保留验证 evidence。
