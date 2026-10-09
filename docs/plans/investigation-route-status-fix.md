# Investigation 路由固定与状态同步修复计划（Ticket 13923）

当前合同：v1.1。任务：p2-190。执行者：本线程；来源线程 01a11961-20c6-7630-9e82-a8dafc51117e。普通实施计划，无新独立验收等待。以下为执行合同与唯一 current evidence record；原始完整交接见 [合同原文](investigation-route-status-fix-contract-v1.1.md)。

## 已确定合同

- C1：真实 case 首次有效分类 Investigation 后客户 comment 不再 route。原 intake/handoff 事务创建 investigation_feedback/work turn，保持 customer 原始 event/execution/comment/snapshot 身份；启动 pending turn 时同条件规范化以覆盖排队窗口。继承首次有效 direction/reason，可审计 inherited。实际 human 接管/终结票不恢复，paused 协作可继续。首次 Automation 后来 Investigation 不 sticky；adhoc/初始未知/Automation原合同保持。不增加分类LLM/框架。work 的 routine policy escalation 返回可继续协作结果；真实 terminal failure走原失败机制。
- C2：trigger_comment_id 定位本轮原文/author/source time，以不可信引用投到原 binding channel/thread并关联turn；不重复全history，不创建替代thread。namespace+ticket+comment身份，复用C4窄投递机制；缺thread明确留证。无persona/客户自动回复，保留Prepare draft/Approve & send。
- C3：原生Hermes合法接管携带可核验binding/turn上下文，不能全局放宽handler。备注、回队列、ownership release、取消pending reply、通知owner独立结果；skipped/failed/unknown如实到detail与邮件。保留failure_reason/job/attempt及技术/政策区别，已确认动作不重做，保留Production-only Zendesk handoff。
- C4：n8n Sync Status刷新active/draft脱敏快照/验证后只加PP GET executions membership -> ticket.updated intake，复用现有API/凭据；非归属不投，Production新增分支若有保持disabled，现有非目标分支不变。event用真实audit/event ID或namespace+ticket+source updated_at+status，源字段非法失败，不能伪造历史。SP真实status processing查native绑定，包括human/solved/closed；原thread英文prior->current+source time+link，无LLM/回复。ticket repo原状态事务内锁行接受变化并写support_idempotency_records intent；scope含env/namespace，key含ticket/source/status，保存event/execution/prior/current/target。原processing lease/fence条件claim pending/确证未发送failed->sending；同key并发/confirmed/sending/unknown不覆盖。重试unchanged仍恢复intent。post timeout/crash或回执落库失败留unknown不自动重发，无确切identity不能确认。newer同status推进native水位、older不倒退；旧Engineer合同保持，无新表/迁移/通用框架。
- C5：可信工程师当前消息去bot mention后trim，只认大小写/尾句点等价close the case；不认否定/引用/客户history。入口验证team/channel/thread/ticket/actor/event身份，turn/execution JSON存授权+revision。tool_close_case/support_close_case参数只有turn_id；server查real非adhoc/current active/revision/授权/env/sideeffects/工单归属，幂等按env+ticket+Slack event。仅status solved PUT，无客户回复；disabled明确not_executed、已solved/closed幂等、unknown先回读不盲重试。成功明确终态并取消旧draft/fence，以C4状态同步收尾。SP+hermes-deploy版本化plugin/schema/work toolset+实际可加载skill，PP EFS AP确认与Production disjoint；镜像从实时核验digest基底overlay保留SQLite修复及已有插件。

## 授权与限制

授权本范围代码/测试/文档/plugin/skill、正常合并、必要PP构建发布验证、对应n8n PP分支更新/发布/回读。Production应用/Hermes/n8n业务分支不授权。禁止恢复/重开/重放真实13923、历史批量replay、客户回复、新DB表/迁移/云资源/队列。业务验证仅既有官方隔离PP fixture，未找到授权fixture则如实留样本缺口。新增实质决策按AGENTS直接问用户并停依赖动作。

## Findings

| ID | 核验问题 | 当前状态 |
| --- | --- | --- |
| F-R1 | 客户续轮normal重新route | 本地真实入口验证通过、SP PP 已正式发布；自然业务样本未取得 |
| F-H1 | 合法native接管inactive_handler跳过 | 合法 native 上下文进入统一 chain，旧未知 handler 仍拒绝；待自然样本 |
| F-A1 | 接管告警固定成功与skipped矛盾 | 独立 action 状态送达真实 builder/notifier，各结果矩阵本地通过 |
| F-S1 | n8n status缺ECS intake | PP 分支已发布，active=draft 及非目标 graph 回读通过；自然状态样本未取得 |
| F-S2 | status notifier缺native binding | 真实 worker + 隔离 PG intent/claim/rollback/crash 验证通过，SP PP 已正式发布 |

## 基线与证据

- 2026-10-08：SP root main f9388ee5c5baebeab7a0f1501cedd96e70c6ed6e clean；按官方脚本fetch/pull后同SHA建立.worktrees/investigation-route-status-fix / codex/investigation-route-status-fix，初始clean。其他工作区不动。
- hermes-deploy root clean codex/p2-188-session-storage @475917f3da8641c22ed9519ec031b148a38ad90f；旧归档保留，不直接编辑。
- 交接n8n历史版本与execution仅调查时snapshot，不证明当前运行。Ticket13923历史snapshot为solved，未查询/重放/修改真实票。incident已证实conversation_follow_up接管，不新增SQLite RCA。
- 实施前运行基线：SP r20261008-07cac14（API :111 / Route :110 / Worker :112），Hermes :44；历史发布 evidence complete。Prompt checkpoint 为 pr-43cee390c4b7，已由线上 /health/release 与当前 API task definition 回读核对。后续合并与发布事实见下方当前记录。

## 验证与完成次序

C1/C2代表路径 -> C3 -> C4及隔离PG -> C5 SP/plugin/skill -> prompt/operations/progress/Overview更新 -> 修复前失败/后通过检测 + 定向回归 -> 正常finalize -> SP PP -> Hermes PP -> n8n PP -> 层间version/digest/tool/skill/graph回读 -> 官方隔离fixture或如实样本缺口 -> root cleanup。完整行为矩阵沿用用户交接四.4：真实入口、身份/fence/并发、Automation/实际human/terminal边界、各handoff状态、PG rollback/崩溃/claim/水位/unknown、可信工程师close链与权限负例。禁止用计数或mock宣称PG原子性，不以steady替代pipeline complete。

## 2026-10-08 当前代码与定向证据

验证环境：本任务工作区；解释器为 root `.venv/bin/python`；专用临时 PostgreSQL `p2190`，随机独立 schema 自动清理，不使用运行数据库。所有 Zendesk/Slack/Graph/Hermes/provider 边界用 mock，不产生真实业务消息。

| 合同 | 真实入口/机制 | 已观察行为与证据 | 后续边界 |
| --- | --- | --- | --- |
| C1 | customer intake → RouteWorker → claimed Hermes processor，Memory/PG | 补频道/感谢/请求关闭仅 1 个 work run，无 route/persona，session 与 event/comment/execution 不变；排队窗口按首次分类规范化；human/terminal 不恢复；首次 Automation 后来 Investigation 不 sticky | PP 自然续轮样本 |
| C2 | claimed customer processor → 原 thread notifier | 长客户原文完整一次投递、不可信引用、无 close authority；禁止创建 root；调查结果回原 thread | 未对真实 Slack 发测试消息 |
| C3 | native terminal handoff → escalation → 实际 notifier/builder | sent/queued、inactive skip、PP skip、failed、unknown 的各步骤如实；保留 job/attempt/failure reason；非 native 未知 handler 不放宽 | 真实接管自然样本 |
| C4 | ticket.updated intake → route system → AutomationWorker + ticket repository | status 与 intent 同 PG 事务 rollback；提交后发送前崩溃重领原 job；并发 claim 唯一；confirmed 不重发；sending/unknown 不重发；明确失败原 job 恢复；同状态较新水位推进、较旧不同状态忽略；human/solved/closed 均回原 thread，无 LLM/回复 | SP/n8n PP 已发布，真实状态自然样本待取得 |
| C5 | authenticated engineer HTTP message → work job → API close tool；真实 plugin register/forward | 严格命令；客户/引用/否定/stale/跨环境拒绝；只 PUT solved，一次 GET 回读；timeout 立即回读，unknown 不盲重试；confirmed receipt 后本地失败可恢复且零新 PUT；work progress 保留 server authority/feedback | SP/Hermes/n8n PP、实际 toolset/skill_view/hash 回读通过；未关闭任何真实测试票 |

关键补查检出：首轮 classification 已写入、work run POST 回执未返回时 turn.phase 仍旧值；仅用 phase=work 的政策保护会漏过。本轮以已持久化首次/当前 Investigation direction 与实际 human/escalation 事实保护，FakeHermesClient 在 POST 返回前通过真实 tool 触发该窗口。初始未分类/Automation/human 不受此条件影响。

原 job 定时恢复检出 InMemory store 认领未检查 available_at；已与既有 PG 的 available_at<=NOW 合同对齐，明确未发送拒绝不会立即忙重试。未修改生产 schema/DDL。

- 最新新增路径 + worker/terminal handoff 回归：`pytest -q test_investigation_route_status_fix.py test_native_status_worker.py test_hermes_close_case.py test_automation_ecs_worker.py test_hermes_tool_failure_handoff.py`（路径均在 backend/tests/），115 passed，隔离 PG 已启用。
- 最新 notifier/handoff/n8n + Hermes/API/PG 与 route 回归：145 passed / 1 skipped（仅 Memory 不适用 SQL rollback），最后源修改后运行。
- 之前相关真实 API/worker/Slack/告警/Hermes/PG 回归 290 passed / 1 skipped / 5 subtests passed；随后补查 72 passed。这些是各次验证范围，不累计宣称独立用例数。最后两个源修改后定向回归上述 115 passed。
- 修复前可检出：临时 pytest plugin 分别关闭 inheritance、恢复 worker 提前 external_started，同两用例各 1 failed；去故障同两用例 2 passed。临时注入文件不进入发布。
- Hermes close plugin：canonical 与保持当前 :44 route schema 的 runtime overlay 两源，18 passed。skill quick_validate passed；镜像内实际 skill_view 已通过，work 注册/schema/参数拒绝、原 route schema 保留均通过。skill hash 7bad479e58edf9317c3092e2811ef1efc8381bfd73d57daa6f43cd5e0250c88e。
- n8n：Sync Status 13 nodes valid（唯一 warning 为既有 get_case_info 硬编码鉴权，已脱敏，未扩大修复）；Slack Forward 9 nodes valid/no warnings；源时间与 event ID JS 行为测试 2 passed。快照检查 15 published / 1 divergent draft / 53 redactions passed。

## 发布基底与版本约束

Hermes overlay 固定当前 :44 `sha256:a9f342c08e3a10091b4dbaa8e3ba6bb0c6035412b26e3e627c364e3910e17e8d`，保留五容器及 SQLite/session 修复。实时镜像内插件原始 hash `55bea1629034f194d8851c6f84ba0f68142bcec8adff9e154929a9ed2cacae2b`；与归档 canonical 的 route schema 不同。runtime overlay 在该实测模块上仅加入 close schema/handler/toolset，不把归档 v3 schema 强化带上线。新 runtime hash `bbab36cd476c270fb3662d326fe4b4be527b584d1df8ba6dc43d0cdbf48bb59b`；两源 close 合同同测。Hermes PR 披露 475917f 归档相对 main 的四个 SQLite 归档文件，不算本任务新实现。

2026-10-08 已读 PP Hermes EFS AP 与 Production :3 AP 完全 disjoint，Production 服务 1/1/0。发布前再次核对目标服务与 digest，技能只写 PP hermes-home AP，沿用既有 skillsdrop family。禁止新表/迁移/云资源；短暂 release task/revision 使用既有发布机制。

n8n 准备基线：Status 03B6AvcrOgRkWlUc active=draft 6cfb5781-11bf-4596-bd99-64c092969b92；Comments zc2ndUDqDAS0uX1Y active=draft 30a88a17-aea8-44f2-a7f6-7d603daf68f3；Forward r1HIW8UNuCabiOPn active=draft ddf01d26-7ecf-4c93-a806-ff1729ea0230。上游 Route Support kyiA0QuiVx6JJ03i published ba9403ed-0989-4319-9661-d021df3b20e4 / divergent draft c41c09db-3924-40ed-9d48-f51c571e584e 仅只读，不改/不发布。

## 2026-10-08 合并与 PP 发布当前记录

- SP [PR #1437](https://github.com/ZilingXie/SupportPortal/pull/1437) 已 squash 合并。实现 HEAD `999ed469ce2f36a9e31b9be8ded1888bb8e8cc73`；运行源/main `435811e4139786a843a45c7dfa50d7bec808af62`。finalize 在刷新 main 后重跑三组核心测试，84 passed；CodeGraph/CodeSight 已同步。
- Hermes [PR #33](https://github.com/ZilingXie/heremes-deploy/pull/33) 已 squash 合并。实现 HEAD `9ee8781b01110f41efcf6765f60d9cd97899c47d`；构建源/main `e5c2f1192c1f5f96eb6a1b5895eee775bd180a67`。canonical/runtime close 与 ECS 检查：34 passed / 5 subtests passed；跨仓既有 CodeGraph 已同步。
- SP 正式 PP pipeline `r20261008-435811e` 在 `2026-10-08T13:36:02Z` 完成，checkpoint 的 CodeBuild/preflight/deploy 均 passed，部署 evidence 为 `status=complete`。API `:112` / Route `:111` / Worker `:113` 的 runtime digest 与发布记录一致，heartbeat/public health/Provider/CloudWatch/Terraform pre/post zero-drift/Prompt 均通过。Prompt 保持 `pr-43cee390c4b7`，既有 schema 已匹配并跳过 bootstrap，无新 DDL。原始审计 evidence 位于 `.deployments/ecs-pipeline-r20261008-435811e/preproduction-deploy/evidence.json`。
- 发布恢复披露：第一次使用 root 源库验证当前 PP Prompt 失败，尚未构建/部署；改用既有 PP 源角色后 CodeBuild `06a919e6-b3ff-4fd2-9261-11723ac10c35` 成功，但旧 output 目录阻止收尾。使用正式 `--resume` 从版本化 S3 恢复验证后的 manifest/publish record，没有删除或覆写不可变证据。恢复运行总耗时 1070.845s（其中 ECS 等待 442.603s），超过 900s 目标；门禁均通过，不把超时目标写成故障或隐去。
- Hermes 新镜像由合并源码构建并 ECR 回读，tag `hermes-20261008-p2190-e5c2f119`，digest `sha256:0d75c8dba574b567ee05a5060b3779da31e06d2c9796a14b8c6c5ccad23d5c99`。已请求 PP `:44 → :45`，注册前证明完整 TD 只有 hermes.image 变化。首次注册因显式空 tags 被 ECS 拒绝（CLI 输入校验会放行）；回读保持 :44 后省略空字段完成注册，未改服务合同。
- n8n 操作前四条 workflow 的 active/draft 版本重新回读，仍与准备基线相同。刷新 5 个脱敏快照，检查 15 published / 1 divergent draft / 53 redactions 通过；上游 Route Support 的分歧草稿保持不动。
- 业务 fixture 边界：已检查官方 `scripts/testing/preproduction` 的 PP-I1 和 `docs/operations/testing.md`；PP-I1 会创建真实票并投 Slack，不符合本任务隔离条件。未找到本次状态/关闭链可用的官方隔离 fixture，不执行真实13923或真实Slack测试，保留自然样本缺口。
- 本地官方栈检查 `rtk proxy bash scripts/workflow/inspect_single_host_stack_mode.sh` exit 1：`Official single-host stack is not running (missing deployment_api_1).` 默认 remote-db/lightweight 和 local-db compose 都开启真实 reply poller，不能把它们当作无业务副作用探针。已直接请求用户确认本任务采用 ECS-only 技术验收还是另行设计零业务写入本地验证；未答复前不豁免该完成规则、不启动本地栈、不声称整体完成。
- 本任务临时隔离 PG 已停止并清理，临时 reverse fault injection 和 ECR authfile 已移除；任务工作区与不可变 release 审计保留。

### 已完成的 Hermes 与 n8n 技术回读

脱敏可复核物料：[PP evidence](investigation-route-status-pp-evidence.json)。该物料记录技术部署与图/工具检查，不表示自然业务闭环已经发生。

- Hermes `:45` 在 `2026-10-08T13:50:48Z` ECS rollout COMPLETED；实际 task `93bda8af0fcb4ae7b333ac3f230ff240` 为 1/1/0、五容器 HEALTHY，目标 digest 与 ECR 一致，其他四容器 digest 不变，ALB target healthy。CLI waiter 先返回 Max attempts exceeded；后续服务与 task 直接回读证实完成，保留超时记录，不将 waiter 本身写成成功。Production Hermes 仍 `:3 / 1/1/0`，AP disjoint 再次核对。
- 单项技能投放沿用 `supportportal-preproduction-hermes-skillsdrop:2`，task `c7634adcfd6649808c01fbaf92c1a727` exit 0；只复制 support-close-case，user/entrypoint/IAM/单 PP AP 挂载不变。实际 EFS hash 与源一致，实际 skill_view 返回成功；没有启动批量旧投放命令。
- ECS Exec 在运行中的 Hermes 容器 GET `/v1/health`、`/v1/toolsets`、`/v1/skills` 均 200；work enabled/configured=true 且包含 support_close_case，技能列表可见且实际 skill_view 成功。runtime plugin hash `bbab36cd476c270fb3662d326fe4b4be527b584d1df8ba6dc43d0cdbf48bb59b`；skill hash `7bad479e58edf9317c3092e2811ef1efc8381bfd73d57daa6f43cd5e0250c88e`。
- Status active=draft=`80985a3f-fefd-420b-90e5-5ea25a21bde7`，13 nodes；9 个既有节点逐项一致，新增 PP membership/intake 图与已准备图一致，两新 HTTP 节点实际 credential references 回读一致，无自动替换凭据。
- Forward active=draft=`a3c2add4-c3f5-44f0-8ba2-c273c552f595`，9 nodes；只有既有 PP Send Hermes Message.body 变化，其他字段/节点/连接未变。首次 setNodeParameter path=body 被 MCP 拒绝，version 未变；改为 updateNodeParameters、replace=false 后成功。版本化 patch 已改成实际可执行格式，测试仍直接执行同一 JS，2 passed。
- Comments active=draft=`30a88a17-aea8-44f2-a7f6-7d603daf68f3` 未变；上游 Route Support published=`ba9403ed-0989-4319-9661-d021df3b20e4` / draft=`c41c09db-3924-40ed-9d48-f51c571e584e`，两图均与操作前一致，未发布其分歧草稿。
- 发布后 SDK graph validation 均 valid=true。与准备阶段不同，最新校验另报 HTTP/subworkflow 输出字段推断警告：Status 7 项（既有 hardcoded authorization 1 项 + 字段推断 6 项），Forward 字段推断 6 项。HTTP/subworkflow 未提供静态输出 schema；新增 ticket.id 来自既有 Zendesk ticket GET，executions 明确由 SP `/v1/cases/{id}/executions` 返回（API 源码 989–996），Input 是既有 caller 的透传合同，真实 JS 身份/时间测试已通过。未为消除启发式警告加入 pinData 或改非目标分支；真实执行样本仍缺失，不能据 SDK valid 宣称完整业务通过。

## 收尾状态

- 用户于 2026-10-09 确认采用 ECS Preproduction 技术验收，并豁免本地官方栈重启；该决定解除本任务的本地运行门禁，不改变真实业务写入限制。
- p2-190 已登记为 `done`。SP/Hermes/n8n 的合并、Preproduction 发布、版本/digest/tool/skill/graph 回读和定向回归作为技术验收证据保留。
- 自然业务样本仍未取得：不重放 Ticket 13923，不发送客户回复或真实 Slack 测试消息；该边界不被记录为业务 PASS。
