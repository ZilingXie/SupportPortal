# Preproduction native Hermes 配置恢复与发布防回退

修订：R2。任务：p2-199。基线：3fd117e0c571de8f0fce3d2aa4ee59f3e6006ee6。工作区：.worktrees/native-hermes-review-release-fix，分支 codex/native-hermes-review-release-fix。用户指令：实施计划；无新增独立验收门禁。

## 合同

- C1 fixed_task + Investigation 首轮 Work 完成后必须等待调查审核，不运行 Persona；缺调查记录fail-closed。
- C2 Prepare draft接受fixed_task，来源turn/revision/并发/重复点击使用原store事务保护；Automation、旧normal/feedback、ad-hoc边界不变。
- C3 PP发布必须显式engine；hermes必须启用agent；pipeline构建前和deploy观察定义后阻断未授权hermes->legacy降级。
- C4 engine/agent/降级授权位绑定正式checkpoint/preflight/evidence，resume不得改身份。
- C5 正式PP发布与线上readback，保留当前其他任务配置；创建新测试票，审核前零客户投递。不重放14028、不批准客户回复、不发布Production。

## 已核实基线

native r20261013-570aec2成立；后续知识治理r20261013-42a187a切回legacy。失败尝试回滚至已经legacy的Route:141。Task:143/141/143为本轮实施前稳定观察；实时状态必须在部署前再次核验。

## 执行证据

- fixed task内存/PG真实入口：Work停车、唯一续接、缺结果拒绝；新增revision过期与并发点击矩阵。
- 反向验证：暂恢复旧审核类型集合，真实processor在work后提交persona，新断言失败；恢复修复后通过。
- 初轮pipeline专项56 passed/2 skipped（PG未启用）；隔离PG专项21 passed。最终隔离PG与相关入口回归249 passed（无跳过），覆盖客户消息revision过期、并发Prepare唯一续接、真实发布入口遗漏engine拒绝、未授权降级构建前拒绝。
- 代码、正式发布、业务验证分层记录。待发布前不写done。

## 完成边界与发布证据

- 代码PR #1488合入main ef52d04ef0a24149f74bf209ddaf2102f9a0e95d。
- 正式release r20261013-ef52d04 complete；API:145 / Route:143 / Worker:145 单一PRIMARY COMPLETED；三角色运行digest与publish-record逐一一致，公网provenance一致。Route=hermes、typed=disabled；Prompt仍为pr-9737f67803f1。
- 首次Provider探针发生CannotPullContainerError；ECR回读digest ACTIVE且有成功pull，正式回滚succeeded。同release/digest --resume后全部门禁passed。保留失败记录，未认定其底层原因。
- 新票14031经测试邮件/n8n真实入口：固定Investigation任务，唯一fixed_task Work turn完成后awaiting_investigation_review；Slack prepare_draft按钮回读；零Persona、零draft、零投递账本，Zendesk仅首轮客户公开评论。
- 本轮按已有授权以ECS PP技术验收替代本地官方栈。Prepare/Approve未点击；后续客户回复、多轮附件和真实RTC诊断质量仍需单独业务测试。不重放14028、不发布Production。
- 结构化证据：[native-hermes-review-release-evidence.json](native-hermes-review-release-evidence.json)。
