# Enablement Archer 模式切换与 E3 复测 · v1

基线：`main@ee6b8e94659a36218b1d90b0a90149db42c519fa`。
工作区：`.worktrees/enablement-archer-mode-switch`，分支：`codex/enablement-archer-mode-switch`。

用户已要求切换 Preproduction 到 Archer 并运行 E3，业务运行失败立即停止。
Mac 本地登录和执行由现有自动流程处理，不作为测试发起端门槛；两次真实审批仍保留。
本局部执行任务未新增独立代码验收停点，按正常 PR/squash 流程验证与发布。

## 当前事实与修复

运行 release `r20261014-7800b8ee-files-closed` 的 API/Worker 显式为 manual。
首次正式 check-only 在共享配置校验拒绝 manual→archer，服务未变，未创建 E3 工单。
不能移除 Knowledge 配置参数：那会把当前 Knowledge 开关改为缺省关闭。

| 合同 | 实施与验证 | 状态 |
| --- | --- | --- |
| C1 显式切换 | 仅 api/worker 显式请求且渲染精确匹配的合法 mode 可变；默认值、route 差异仍拒绝 | 真实入口测试通过 |
| C2 其余配置保持 | Knowledge 参数保留；模型、secret 引用、文件开关和其他共享配置仍严格比较，错误不输出值 | 正负例通过；check-only 通过 |
| C3 发布身份 | mode 进入 Preflight 与 checkpoint；新源码、新 immutable release，保留全部部署门禁 | 正式发布及独立回读通过 |
| C4 业务验证 | 仅 Preproduction 新工单 E3；失败即停；本地审批与独立回读保留 | 第三轮等待超时，已停止；完整业务未通过 |

验证入口：`backend/tests/test_enablement_mode_release_guard.py` 使用真实 renderer→CLI、真实
shell 参数解析/共享配置校验函数、真实 Preflight/checkpoint 生成函数。存量 renderer、共享配置、
文件关闭发布与 pipeline 套件回归；shell 语法和 Overview 校验。

代码变更已通过 PR #1500 合入 `main@48bca0c9f86c9342b37b5478cb3a86aceee56acc`。
五套定向回归 `241 passed`（mode guard 23 项）；shell 语法、Overview、diff 校验通过。
CodeGraph/CodeSight 已从 root main 刷新。

新 immutable release：`r20261014-48bca0c9-e3-archer`，Prompt 保持 `pr-88298d4e0a3f`。
CodeBuild 与正式 check-only 已通过。新鲜发布前回读确认 API/Route/Worker 为 154/150/153、
API/Worker 为 manual；Knowledge master/intake/summary-review 保持开启，其余知识投递关闭。
证据：根区 `.deployments/r20261014-48bca0c9-e3-archer/`。

## 最终运行结果（2026-10-14 UTC）

09:59 正式发布成功，API/Route/Worker 为 155/151/154；API/Worker 实际 mode 为 archer。
三服务健康、heartbeat、CloudWatch、Provider、Prompt、digest 及 Terraform 前后零漂移通过。
独立回读逐项比较发布前后环境变量和 secret 引用，除 provenance 与所请求 mode 外均一致。

E3 新工单为 14061（AC-14061）。首轮缺 App ID 追问、第二轮知识回答含 references 与实际投递、
automation ownership 保持、零额外 relay 申请，共六项检查通过。
10:13:58 第三轮 `wait_reply_intent(enablement_appid_invalid)` 等待 published job 超时，
场景以 exit 2 停止，没有发送第四轮消息、提交 Relay 申请、执行 Archer 开通或重跑。

停止后只读证据：第三轮 turn 已 completed；Hermes draft 的 delivery 已 delivered，
comment `54579393894932`；案例仍 automation/pending，原因 `appid_invalid_format`。
兼容 job 的 intent 正确，但状态仍 `persona_v8_queued`，owner 为 `hermes_persona`、
`hermes_compatibility_only=true`；Relay request 数为 0。
这证明 E3 的 job 状态等待条件与本次实际 Persona 投递记录不一致，不能据此声称客户未收到回复，
也不能将第三轮或完整业务验收记为通过。本轮按失败即停边界收口，不修等待器、不回放工单。

当前开放项 E3-F1：另行对齐测试载具与 Hermes 当前回合真实投递身份，保留内容、安全、
时间水位及兼容 relay 门禁，再安排新工单完整复测。未到达的 Relay/开通/终态仍未验证。
可复核汇总：[evidence.json](../evidence/enablement-archer-mode-switch/evidence.json)。

部署顺序：合并→新源码正式 CodeBuild→check-only→正式部署→读回 API/Worker archer、
Hermes、文件关闭及 Knowledge 保持→运行 E3。禁止覆盖旧 release/checkpoint。
若正式发布或业务步骤失败，保存失败证据并停止，不降级/自动重发/弱化断言。

运行证据位于根区忽略目录 `.deployments/e3-full-20261014/`（首次探测与失败检查）及
后续独立新 release 目录。未改 Production、footer、旧失败工单和文件启用门禁。
