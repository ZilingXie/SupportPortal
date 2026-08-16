# SupportPortal Project Registry

`docs/project/` 是 SupportPortal 项目计划和进度的 canonical registry。

## 维护边界

- Task 当前状态只修改 `tasks/<task-id>.json`；Board、Topic、Meeting、动态和汇报都是生成视图。
- Meeting 记录决定和讨论，不复制 Task 的 owner、status、next action 或 evidence。
- `done` 必须附带 PR、测试、部署、文档或决定证据；`blocked` 必须说明 blocker。
- `docs/feature_list.md` 继续维护产品能力清单，不承担 Task 进度。
- `generated/` 和 `docs/projectoverview-data.js` 是生成物，不直接手工修改。
- PR 的公开摘要写入 `pr_summaries.json`，不要把 PR body、客户数据、邮箱、日志或 secret 写入公开页面。
- 运行时行为、用户流程、API、数据模型、配置或业务结果发生变化的功能/修复类工作必须关联唯一 Task；纯文档、测试、规则、开发者脚本和运维变更只有在改变进度记录时才需要重生成 Overview。

## 维护命令

```bash
python3 scripts/generate_project_overview.py --refresh-prs --write
python3 scripts/generate_project_overview.py --check
```

开工前先搜索已有 `task_id`；没有对应 Task 时先创建，再开始实现。提交前把 Task 更新为正确状态并保留验证 evidence。
