# p2-163 skill 行为验收证据（round 3，隔离一次性运行）

三个场景各由一个独立真实代理（隔离 subagent）运行：代理自行读取并遵循
worktree 内新版 SKILL.md，面对合成收件（inbox.md：指定 Task A + 同 AppID
关联 Task B）与 mock 服务（status_server.py 本地只读端点 + pilot shim 记录
每次调用、拒绝非 dry-run 写），自主决定查询/继续/暂停。提示词仅给环境与
"按 skill 执行"，不引导结果。

## 结果汇总

| 场景 | 状态服务 | 代理行为（自决策） | pilot 调用 | 决策 |
| --- | --- | --- | --- | --- |
| s1 旧申请 cancelled | NEW=dispatched/open；OLD=cancelled/solved | 先后查询 NEW 与 OLD → 按表行1排除 OLD → 仅对 NEW 进入预检 | 3 次只读（appid/status/open --dry-run），零写 | 继续至第一次审批门槛后暂停等待审批 |
| s2 双有效同 AppID | NEW=dispatched/open；OLD=dispatched/pending(valid) | 查询两者 → 命中表"两个不同申请均有效"行 | **零次（预检前暂停）** | 暂停并报告两者（request_id+工单），给出三选一建议 |
| s3 端点 503 | 全部 503 | 反复核实（多次 NEW、OLD、探针、健康路径）确认为全局不可读 | **零次（预检前停止）** | 暂停并报告"任何一步无法核实→不进入开通流程" |

## 文件

- `report-s1.md` / `report-s2.md` / `report-s3.md`：三个代理的最终报告（步骤、
  查询返回、决策与理由、下一步动作）。
- `harness/`：mock 状态服务、pilot shim、合成收件与指定任务申请——可复跑。
- `queries-s3.log` / `pilot-s3.log`：s3 的服务端查询与 pilot 调用日志快照
  （s1/s2 日志在场景切换时被清空未存档；s1/s2 的调用序列以两份报告的
  "执行记录"章节为准，s2 报告明确记录"未调用任何 pilot 命令"）。

## 与既有 pytest 的关系

`test_enablement_relay_inbox_binding_scenarios.py` 定位为文档检查（章节存在
与关键词在位）+ 底层可执行回归（预检/审批 digest 绑定/执行时门禁），不构成
行为验收；行为证据以本目录的一次性隔离运行为准。
