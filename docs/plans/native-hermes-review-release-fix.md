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
