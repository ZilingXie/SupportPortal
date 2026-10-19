# stage6-weknora-agent-bridge r1 — 步 0 实时基线冻结（2026-10-19，全程只读）

工作树：.worktrees/stage6-weknora-agent-bridge（分支 codex/stage6-weknora-agent-bridge，自 main 5bfbd86a 创建）。

## 运行态

| 项 | 值 |
|---|---|
| release | **r20261019-15f3602-argus-zac-r1**（APP_BUILD_REF=15f3602c，今日 11:52 由并行线部署） |
| api | task def **:159**，digest sha256:2388e4fa…，服务运行 :159（1/1） |
| worker | task def **:158**，digest sha256:f8d474d9…，服务运行 :158（1/1） |
| route | task def **:155**，digest sha256:11fb8e8b…，服务运行 :155（1/1） |
| hermes | task def **:46** |
| Prompt release | **pr-88298d4e0a3f**（三角色一致；已从 pr-9737f67803f1 更换） |
| AGENT_MODEL_ID | gpt-6.1-sol |
| HERMES_CASE_WORKFLOW_MODE | disabled（api/worker） |

治理开关（api+worker 一致）：master=**1**、intake=**1**、summary_review=**1**、worker/delivery×2/slack=**0**、allowlist=csd_issue:CSD-79923,csd_issue:CSD-79994；**并行线新增第七开关 KNOWLEDGE_AI_EXECUTION_ENABLED=0**（我的步 2 渲染器时代不存在——步 2 实现须基于现行 main 重读 renderer/runtime）。注意：mode=disabled 下 standalone 建任务门仍关。

## SSM（知识面 8 参数，版本不变）

agent-memory-wiki-base-url/-service-id/-team-id（v1）、knowledge-slack-workspace-domain（v1）、weknora-base-url/knowledge-base-id（v1）、weknora-api-token（SecureString v1）、weknora-api-contract-json（**v4**）。

## DB 计数（supportportal_preproduction）

intakes=**292**（10-13 后持续自然到达）、summaries=**42**（+6）、reviews=36、promotions=64（全 human_review，历史保留）、deliveries=**0**、v23 Slack 六列在位。

## n8n（与 10-13 修复版一致，无新 draft）

CSD active=6511c2e9、Solved active=d4c50a10、Forward Thread active=4ed2c0c3（均 versionId==activeVersionId）。

## Terraform 1.9.8 refresh-only plan（preproduction）

exit=2：**唯一漂移=三个 aws_ecs_service.account 的 task_definition 指针**（state api:118/route:117/worker:118 → 实际 :159/:155/:158）；无任何其他资源属性变化——发布流任务定义指针滞后为已知形态，非基础设施配置漂移。

## 并行发布者与凭证

- 无进行中的发布进程（ps 零命中）；最近发布=今日 11:52（argus-zac-r1）。
- 并行线工作树 ~/.codex/worktrees/stage6-preprod-release（分支 codex/stage6-preprod-baseline，HEAD f10b28d4 "docs: pause stage6 continuation after Hermes rollout failure"，**零脏文件**）——其自述"Hermes rollout 失败后暂停"。今天 11:52 的发布来自其后续（argus-zac r1，即 main 15f3602c/PR#1511 的正式部署）。
- AWS：identity 正常（891612554546/Zac/login 可刷新 provider），无 OAuth 限流迹象。
- 我的旧 origin/codex/stage6-deploy 分支保留（止于 dbcae60a），工作树已清。

## 停止条件评估

| 条件 | 结果 |
|---|---|
| 仍有并行发布 | **无**（零进程；最近发布已完成 4+ 小时） |
| release 与 task definition 不一致 | **一致**（三角色 APP_BUILD_REF/release id/digest 同属 r20261019-15f3602，服务运行=最新 revision） |
| Terraform 有漂移 | **非配置漂移**（仅 task def 指针滞后，已知发布形态） |
| AWS OAuth 限流未恢复 | **正常** |
| 线上状态无法证明属于当前发布 | **可证明**（三方一致：task def env ↔ 服务运行 revision ↔ .deployments/ecs-pipeline-r20261019-15f3602-argus-zac-r1） |

**结论：五项停止条件全部不触发，步 0 通过，可进步 1。**
