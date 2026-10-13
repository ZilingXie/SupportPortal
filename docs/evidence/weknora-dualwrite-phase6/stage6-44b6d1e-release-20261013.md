# 阶段 6 获批来源发布与并行发布停点

计划：WeKnora 并行双写与人工治理链（阶段二），阶段 6，r4-stage6-repair-A 后合并发布续接。用户已同意将发布来源从 c9722399 更新为 `44b6d1e664d2fe99f1cd210ea054cdfdf511ccda`（PR #1493）；环境仍仅 Preproduction。保留历史 64 条 human_review，不清理、不重放。全量来源、WeKnora publish 和 hybrid search 授权未扩大。

## 来源与冻结

新增 Worker 行为及 native/release 定向真实测试：`backend/tests/test_automation_ecs_worker.py`、`backend/tests/test_native_hermes_release_guard.py`、`backend/tests/test_automation_ecs_release_pipeline.py`，72 passed。原 F1–F6 实现未改变。44b6d1e 与 c9722399 的 Preproduction Terraform 配置无差异。

Terraform 使用既有 Linux AMD64 1.9.8，带锁 plan 返回 exit 0 / No changes；未 apply、未绕过锁、未修改 lockfile。时间与结果见 `stage6-44b6d1e-terraform-20261013.json`。此为发布前独立 plan，不能代替未完成的正式管线后续门禁。

完整部署前冻结见 `stage6-44b6d1e-alloff-before.json`；停止后回读见 `stage6-44b6d1e-refrozen-20261013.json`。API/Worker/Route 为 146/146/144，Hermes 为 45；四服务均单一 COMPLETED deployment、running=1、pending=0。按 service 实际 task definition ARN 回读，没有用 family latest 冒充运行状态。

## 构建结果

新 immutable release：`r20261013-44b6d1e-stage6-alloff`。CodeBuild #195：`supportportal-automation-release:4504c59c-e95a-4d28-80df-e2ae4babd01a`，AWS 回读 SUCCEEDED / COMPLETED。正式 checkpoint 的 codebuild 阶段 passed，120.32 秒。Manifest、publish record 已校验；停止后独立 ECR 回读三角色 digest 均与 manifest 一致。脱敏原文归档于 `alloff-44b6d1e-build/`。

checkpoint 明确绑定 engine=hermes、typed=disabled、master=0、七知识开关=0（包括 AI execution）、allowlist 空，保留 Prompt `pr-78386473bd08`。构建通过不等于部署完成。

## 并行发布停止

正式管线处于 `preproduction_preflight --check-only` 时，检测到另一条本机运行的发布管线 `r20261013-44b6d1e-status-r2`，同样指向 Preproduction 和 44b6d1e，但 Prompt 为 `pr-9737f67803f1`。这是同时运行的发布进程证据；停止时 ECS 尚未出现并行 rollout，不能声称另一发布已经上线。

按原计划“发现并行发布立即停止”，仅停止本任务进程及其子进程，工具会话退出 143；没有停止、修改或接管另一任务。checkpoint 只记录 codebuild passed，preflight 未完成，不补写为 passed。未进入本任务 ECS 激活，未修改 n8n，未重投样本。

停止后队列：source intake accepted=133；standalone Summary/Review 各 completed=36、无 pending/running/有效 lease；历史 typed Summary failed=1；promotion human_review=64；delivery=0。64 条历史记录指纹保持 `f0b4671c72d72a77a2f71756f7acd8ef0d274915fce95304bf14e81df953787d`，Slack review status 均 null。两表各 36 个过期 lease 字段仍保留。完整停点材料见 `stage6-44b6d1e-parallel-stop-20261013.json`。

## 恢复条件

需要明确两条发布的先后与环境所有权；另一发布停止或完成后，重新冻结 ECS、Prompt、Terraform、SSM、n8n 和知识队列，并核对共享配置与历史指纹。只有基线稳定、来源及 release identity 仍匹配时才能显式 resume；不重复创建同名 immutable tag、不跳过门禁。步骤 3–10 尚未执行，本记录不宣称运行或业务验收完成。
