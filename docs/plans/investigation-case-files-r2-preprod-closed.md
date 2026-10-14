# Investigation 文件关闭状态下的 Preproduction 发布

计划名称：`investigation-case-files-r2-preprod-closed`；修订：v2（修复 PP-CLOSED-GATE-01）。

状态：PP-CLOSED-GATE-01 完整提交独立代码验收通过，PR #1498 已合并；Preproduction schema 016 与三角色发布完成，关闭状态及无附件 synthetic workflow 执行验证通过。关闭状态发布与无附件 synthetic C1-C6 独立验收通过（文档 HEAD `e237d5366a6792318d4a054238acd11c7c6112b2`）；不宣称整体文件功能或原 R1 门禁通过。

## 本轮边界与后续执行

原基线为 `85e02d44ed8ddce946d2022803efe2aa10784ad6`。用户已同意先提交门禁补丁及回归测试，对完整 HEAD 独立验收，合并后从新提交重建 immutable release。基线不作为只能发布旧提交的限制。

沿用已批准方案 A：正式 CodeBuild 构建，再使用正式 `deployment/deploy_automation_ecs_release.sh`，显式传入 `--investigation-case-files-disabled`；不绕过 schema、Prompt、Terraform、健康或回滚门禁。pipeline 基线只有启用参数，本修复不扩展它。

发布范围为 Preproduction API/Route/Worker 与 add-only schema `automation-ecs-016`。保留现有 case engine、Hermes workflow、Prompt、模型、token、知识治理配置；文件开关为 `0`。不部署 Hermes overlay、文件 Skill/toolset，不调用 Zac 文件，不发送 Slack/Zendesk 文件，不改 n8n、不部署 Production、不重放真实客户。

已执行的发布流程：完整 HEAD 验收通过后复核无并行发布并冻结线上配置；构建新的三角色 manifest/publish record；执行 check-only；通过后正式 migration task、服务发布和独立 readback；使用获准 synthetic case 验证已有无附件功能及关闭状态。任何门禁失败停止后续；若服务已更新，恢复上一稳定 immutable release，文件开关保持关闭，保留 schema 016 和全部证据。

## PP-CLOSED-GATE-01

- 分类：implementation（发布门禁误判）；状态：独立验收通过（完整 HEAD `d4b1ba82a241bd76fa18c75d9f9468c0c4dcdfdc`），合并提交 `7800b8eeb573ac97196413b880a4d44cf38a150e`。
- 实际入口：底层正式 deploy 的 check-only → `verify-knowledge-shared-configuration`。
- 触发：旧 API/Worker 定义缺少 `INVESTIGATION_CASE_FILES_ENABLED`，渲染器补齐 `0`。
- 原因：运行时缺省关闭，但保留检查按配置键逐字比较，把等价关闭状态判为配置变化。
- 修复：仅在比较映射中将缺失环境变量归一为 `0`，不更改运行时开关、渲染器或其他配置。已开启状态切换仍拒绝。
- 影响范围：API/Route/Worker 共用的配置比较入口；秘密引用、模型和其他字段仍进入原有比较。
- 载具：真实 renderer → 真实 CLI；逆向脚本临时恢复旧比较后，API/Worker 两项须在指定门禁以断言失败检出，并在 finally 恢复源码。

## 当前证据与验收合同

旧 release `r20261014-85e02d44-files-closed` 已构建成功，CodeBuild `4e183115-5541-498a-aa9d-b8da272a67f2`。三角色 manifest/publish record 对账通过；check-only 在上述配置门禁失败。该旧 release 的迁移和 ECS 更新未执行；新提交另建 release 后已迁移和发布，见下文。旧 manifest、digest、checkpoint 保留，不能改写成新提交。

下表执行验证结果已通过独立验收，范围仅为关闭状态发布与无附件 synthetic C1-C6。

| 合同 | 必须证明的结果 | 当前状态 |
| --- | --- | --- |
| C1 发布来源 | 新提交、manifest、ECR 三角色 digest 完全一致 | 通过：新 release `r20261014-7800b8ee-files-closed`，三角色实际 digest 与 manifest 一致 |
| C2 Schema | 实际数据库 016、表/字段/唯一约束及运行身份读写权限 | 通过：schema 016、唯一约束、zac_session_id 与运行身份权限实际回读 |
| C3 文件关闭 | 开关 0，API/工具 fail closed，无 Map、Slack/Zendesk/Zac 文件副作用 | 通过：API 六入口 422 case_files_disabled；Skill list/view 无文件 Skill；live plugin 无文件工具；Map 0 |
| C4 Hermes 既有能力 | 当前路由、Prompt、draft/审批/status-sync、幂等真实入口回读 | 通过：synthetic 14057 两回合、冻结草稿、单次无附件回执、409 与 intake replay、solved→closed |
| C5 健康 | live/ready/release、heartbeat、CloudWatch、Terraform 与回滚门禁 | 通过：正式门禁 passed，末次 health 与当前 heartbeat 一致；SLO 超时如实保留 |
| C6 外部边界 | 无真实 Slack/Zendesk/Zac 文件调用，不扩大范围 | 通过（本轮关闭范围）：未部署 overlay/Skill、未操作文件或 Zac、未修改 n8n |

本修复定向命令：

```sh
python -m pytest -q backend/tests/test_case_files_closed_release_guard.py backend/tests/test_stage6_repair_contract.py backend/tests/test_automation_ecs_deploy.py
python scripts/testing/verify_case_files_closed_guard_reverse.py
git diff --check
python scripts/generate_project_overview.py --check
python scripts/verify_feature_list.py
```

定向测试 `170 passed`，4 个既有 FastAPI 弃用警告。开关状态转换、其他配置保护、API/Worker renderer-to-CLI 均覆盖；不涉及数据库保证，因此不以 PostgreSQL/云端测试替代或夸大本修复证据。逆向验证在旧逻辑下精确检出 API/Worker 两项门禁拒绝，恢复源码后两项重跑通过。`git diff --check`、Project Overview、feature list 检查通过；完整提交标识由 PR HEAD 交接绑定。

独立代码验收已绑定 PR #1498 完整 HEAD `d4b1ba82a241bd76fa18c75d9f9468c0c4dcdfdc`；已按授权完成合并、重建、Preproduction 发布。Hermes 文件、Slack/Zendesk C1–C7、Zac 协议和 p2-192 原 R1 发布/真实业务门禁继续分别保留。

## 2026-10-14 发布与 synthetic 执行证据

[当前证据](investigation-case-files-r2-preprod-closed-evidence.json)绑定运行源码完整提交 `7800b8eeb573ac97196413b880a4d44cf38a150e`。API/Route/Worker task definition 分别为 154/150/153，Hermes 45 未改变。API/Worker 显式关闭；Route 未设置、按运行时缺省关闭。Prompt 保留 `pr-88298d4e0a3f`，并非重新生成。

获准 synthetic 工单 14057：邮件真实入口→Investigation work→review→persona draft→受控测试审批→Zendesk 单条公开回复（回执 54577789232788、零附件）→原 intake 重投不新增对象→Zendesk solved 经既有状态同步成为 SupportPortal closed。审批前无支持投递；正文匹配冻结内容加既有签名。草稿 API 仍显示 queued，下游交付账本为 delivered，不能将二者混称。调查未取得权威 SDK 4.6.3 文档，回复显式保留不确定性；本轮证明流程而非该 SDK 答案正确性。

正式部署总耗时 1040.742 秒、超过 900 秒 SLO；硬门禁均通过，rollback 为 not_started，未演练回滚。首次网络故障发生于迁移前，同一新 release 核对 checkpoint 后 resume 成功；失败证据保留。旧 release 的 manifest/digest/checkpoint 没有改写。

本轮已完成独立验收，关闭 `PP-CLOSED-ACCEPTANCE` 门禁；下一步仅收口文档 PR 和任务工作区。原 R1 发布复审/真实业务、启用文件链路及 Zac 文件协议仍等待各自证据；本轮不启用文件、不扩大业务写入或 Production 范围。

验收后收口差异仅登记用户转交的独立结论和对应门禁状态，运行证据、合同及剩余门禁保持原样；原文档验收绑定上述完整 HEAD，后续状态登记提交不冒称已经被独立重审。
