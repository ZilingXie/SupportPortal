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
| C1 显式切换 | 仅 api/worker 显式请求且渲染精确匹配的合法 mode 可变；默认值、route 差异仍拒绝 | 待测试 |
| C2 其余配置保持 | Knowledge 参数保留；模型、secret 引用、文件开关和其他共享配置仍严格比较，错误不输出值 | 待测试 |
| C3 发布身份 | mode 进入 Preflight 与 checkpoint；新源码、新 immutable release，保留全部部署门禁 | 待测试/发布 |
| C4 业务验证 | 仅 Preproduction 新工单 E3；失败即停；本地审批与独立回读保留 | 未开始 |

验证入口：`backend/tests/test_enablement_mode_release_guard.py` 使用真实 renderer→CLI、真实
shell 参数解析/共享配置校验函数、真实 Preflight/checkpoint 生成函数。存量 renderer、共享配置、
文件关闭发布与 pipeline 套件回归；shell 语法和 Overview 校验。

部署顺序：合并→新源码正式 CodeBuild→check-only→正式部署→读回 API/Worker archer、
Hermes、文件关闭及 Knowledge 保持→运行 E3。禁止覆盖旧 release/checkpoint。
若正式发布或业务步骤失败，保存失败证据并停止，不降级/自动重发/弱化断言。

运行证据位于根区忽略目录 `.deployments/e3-full-20261014/`（首次探测与失败检查）及
后续独立新 release 目录。未改 Production、footer、旧失败工单和文件启用门禁。
