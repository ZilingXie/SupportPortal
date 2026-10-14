# Investigation 文件关闭状态下的 Preproduction 发布

计划名称：`investigation-case-files-r2-preprod-closed`；修订：v2（修复 PP-CLOSED-GATE-01）。

状态：门禁补丁的拟议差异已获独立审查通过；提交后的完整 HEAD 等待独立验收。尚未迁移、部署或执行本轮业务验收。

## 本轮边界与后续执行

原基线为 `85e02d44ed8ddce946d2022803efe2aa10784ad6`。用户已同意先提交门禁补丁及回归测试，对完整 HEAD 独立验收，合并后从新提交重建 immutable release。基线不作为只能发布旧提交的限制。

沿用已批准方案 A：正式 CodeBuild 构建，再使用正式 `deployment/deploy_automation_ecs_release.sh`，显式传入 `--investigation-case-files-disabled`；不绕过 schema、Prompt、Terraform、健康或回滚门禁。pipeline 基线只有启用参数，本修复不扩展它。

发布范围为 Preproduction API/Route/Worker 与 add-only schema `automation-ecs-016`。保留现有 case engine、Hermes workflow、Prompt、模型、token、知识治理配置；文件开关为 `0`。不部署 Hermes overlay、文件 Skill/toolset，不调用 Zac 文件，不发送 Slack/Zendesk 文件，不改 n8n、不部署 Production、不重放真实客户。

完整 HEAD 验收通过后：复核无并行发布并冻结线上配置；构建新的三角色 manifest/publish record；执行 check-only；通过后正式 migration task、服务发布和独立 readback；使用获准 synthetic case 验证已有无附件功能及关闭状态。任何门禁失败停止后续；若服务已更新，恢复上一稳定 immutable release，文件开关保持关闭，保留 schema 016 和全部证据。

## PP-CLOSED-GATE-01

- 分类：implementation（发布门禁误判）；状态：已实现，等待完整提交独立验收。
- 实际入口：底层正式 deploy 的 check-only → `verify-knowledge-shared-configuration`。
- 触发：旧 API/Worker 定义缺少 `INVESTIGATION_CASE_FILES_ENABLED`，渲染器补齐 `0`。
- 原因：运行时缺省关闭，但保留检查按配置键逐字比较，把等价关闭状态判为配置变化。
- 修复：仅在比较映射中将缺失环境变量归一为 `0`，不更改运行时开关、渲染器或其他配置。已开启状态切换仍拒绝。
- 影响范围：API/Route/Worker 共用的配置比较入口；秘密引用、模型和其他字段仍进入原有比较。
- 载具：真实 renderer → 真实 CLI；逆向脚本临时恢复旧比较后，API/Worker 两项须在指定门禁以断言失败检出，并在 finally 恢复源码。

## 当前证据与验收合同

旧 release `r20261014-85e02d44-files-closed` 已构建成功，CodeBuild `4e183115-5541-498a-aa9d-b8da272a67f2`。三角色 manifest/publish record 对账通过；check-only 在上述配置门禁失败。迁移和 ECS 更新均未执行。旧 manifest、digest、checkpoint 保留，不能改写成新提交。

| 合同 | 必须证明的结果 | 当前状态 |
| --- | --- | --- |
| C1 发布来源 | 新提交、manifest、ECR 三角色 digest 完全一致 | 新 release 待构建；旧 release 仅证明旧版本 |
| C2 Schema | 实际数据库 016、表/字段/唯一约束及运行身份读写权限 | 未执行 |
| C3 文件关闭 | 开关 0，API/工具 fail closed，无 Map、Slack/Zendesk/Zac 文件副作用 | 线上未验证；本补丁仅验证配置门禁 |
| C4 Hermes 既有能力 | 当前路由、Prompt、draft/审批/status-sync、幂等真实入口回读 | 未执行 |
| C5 健康 | live/ready/release、heartbeat、CloudWatch、Terraform 与回滚门禁 | 新 release 未部署 |
| C6 外部边界 | 无真实 Slack/Zendesk/Zac 文件调用，不扩大范围 | 本轮代码修复未操作云端 |

本修复定向命令：

```sh
python -m pytest -q backend/tests/test_case_files_closed_release_guard.py backend/tests/test_stage6_repair_contract.py backend/tests/test_automation_ecs_deploy.py
python scripts/testing/verify_case_files_closed_guard_reverse.py
git diff --check
python scripts/generate_project_overview.py --check
python scripts/verify_feature_list.py
```

定向测试 `170 passed`，4 个既有 FastAPI 弃用警告。开关状态转换、其他配置保护、API/Worker renderer-to-CLI 均覆盖；不涉及数据库保证，因此不以 PostgreSQL/云端测试替代或夸大本修复证据。逆向验证在旧逻辑下精确检出 API/Worker 两项门禁拒绝，恢复源码后两项重跑通过。`git diff --check`、Project Overview、feature list 检查通过；完整提交标识由 PR HEAD 交接绑定。

独立验收绑定 PR 的完整 HEAD，不能把拟议补丁审查视为当前提交的验收。通过后继续已授权的合并、重建、Preproduction 发布；无需另加部署确认。Hermes 文件、Slack/Zendesk C1–C7、Zac 协议和 p2-192 原 R1 发布/真实业务门禁继续分别保留。
