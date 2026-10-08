请实施以下已获用户授权的计划。用户在来源线程明确要求“你做完计划就交给 codex://threads/01a11b2e-e390-78b2-b621-fc72fc58e1be 实施计划”。本条是自包含执行交接，不要只回复能力或再次索要已经给出的实施授权。

计划名称：Investigation 路由固定与状态同步修复计划（Ticket 13923）
当前合同：v1.1（把已向用户说明的 v1 补齐为执行契约，功能范围不扩大）
执行模式：普通“实施计划”。用户没有要求本新计划实现后返回独立验收；不要自行新增验收等待。按仓库正常验证、finalize、Preproduction 发布与运行验证完成工作。如果你发现本任务另有仍有效的明确人工验收门禁，以其为准并披露证据。
接收线程：运行测试，01a11b2e-e390-78b2-b621-fc72fc58e1be，local，SupportPortal 根目录。

一、用户已决定的产品契约与授权

1. 首次有效分类为 Investigation 的真实 Zendesk case，后续客户消息禁止重新 route。继续使用原 case history、Hermes session 和原 Slack thread，与工程师协作调查。
2. 不增加快速意图判断 LLM、第二分类器或通用路由框架。Automation route 保持现状。
3. 客户补日志、频道、感谢、结束语、请求关闭，均作为客户消息传给工程师。即使 AI 理解错误，工程师仍能看到该轮客户原文。感谢或客户请求关闭不会直接执行关闭，也不会作为普通 policy handoff 转到 human team。
4. 工程师明确发出 close the case，Hermes 可以通过可执行 skill/tool 把绑定工单标记 solved。Zendesk closed 留给既有平台生命周期。不得顺便添加客户回复。
5. 真正终态技术失败继续走既有失败/接管机制；正常 awaiting_investigation_review 是协作等待，不是接管告警。缺日志、需要工程师讨论是调查进展/阻塞项。
6. 修复 Zendesk 状态变化未进入原 Slack thread 的问题，保留 n8n 可视工作流与 execution。
7. 授权包含本范围代码、测试、文档、插件/skill 的版本化实现，正常合并、必要的 Preproduction 构建发布验证，以及对应 n8n Preproduction 分支的更新/发布/回读。Production 应用、Production Hermes、n8n Production 分支业务行为不在本次授权内；共享 workflow 的非目标分支原样保留。
8. 不恢复/重开/重放真实 Ticket 13923，也不批量 replay 历史工单，不给客户发送回复。不修改 Production，不新增数据库表/迁移、云资源、额外队列或编排平台。若既有机制不能满足而需扩大这些范围，按 AGENTS.md 直接向用户提出具体选择并暂停相关依赖动作。

二、已核验基线和证据（区分源代码与时间点运行记录）

SupportPortal：
- /Users/xieziling/Desktop/personal_proj/SupportPortal
- 根 main 干净，跟踪 origin/main
- 本轮核验 HEAD=f9388ee5c5baebeab7a0f1501cedd96e70c6ed6e
- 其他 .worktrees 和 .deployments 均属其他任务，不借用、不清理。
- 此 SHA 是调查基线，不是用户要求固定发布的 pin。开始时刷新 main，检查新增集成影响；无关推进不是阻断。

Hermes 工具部署仓：
- /Users/xieziling/Desktop/agentRelay/hermes-deploy
- 本轮只读核验 HEAD=475917f3da8641c22ed9519ec031b148a38ad90f，当前 clean，branch=codex/p2-188-session-storage
- 当前分支是已完成旧任务的归档，不在其中直接编辑；按该仓规则建立本任务自己的 workspace/branch，保存 linked PR/commit set。
- 原有 SQLite round-4 修复及部署归档必须保留，不能为加入新工具退回旧 Hermes 基底。

n8n（经 MCP 读取已发布 graph 与 execution；以下是调查时版本，不是用户 pin）：
- [case]Sync Status，workflowId=03B6AvcrOgRkWlUc，active=true，draft=active，version=6cfb5781-11bf-4596-bd99-64c092969b92。现有 9 节点只有旧 EC2 两环境分支，没有 ECS intake/Slack。
- [case]Sync Comments，workflowId=zc2ndUDqDAS0uX1Y，active=true，draft=active，version=30a88a17-aea8-44f2-a7f6-7d603daf68f3。已有 ECS Production/Preproduction 归属检查：
  GET /automation/{env}/v1/cases/{ticket}/executions
  executions 非空才 POST /automation/{env}/v1/intake。
  复用此现有 API/凭据，不新增 membership endpoint。
- 13923 的 status executions 188740/188745/188772/188777/188782 均 success，但两个旧 membership 均 false，没有向 ECS 请求。success 只证明 workflow 正常结束。
- comment execution 188770（2026-10-08T10:02:05.644Z）的 membership 回读：Production executions=[]；Preproduction 含首轮与工程师反馈调查完成，outcome=awaiting_investigation_review。
- comment execution 188778（2026-10-08T10:03:05.594Z）回读 Preproduction execution exec-463d5108fd0a4ad5b46b6bd0470addc1，turn-7af0c5ecffd542969755f4969291a63f，status=human_review，reason=conversation_follow_up。
- status execution 188782（2026-10-08T10:04:06.427Z）的 get_case_info：ticket.id=13923，status=solved，updated_at=2026-10-08T10:04:04Z。这是历史 snapshot，不宣称当前工单状态。
- 以上没有证明新一次 SQLite/LLM 故障，本 incident 的直接证据是 conversation_follow_up 策略接管。此前人工 reconciliation 具体动作只有接力报告，未经本轮独立确认，不写成新实证。

稳定 findings：
F-R1 客户后续消息仍创建 normal turn，route 再分类导致 Investigation 偏移。
F-H1 原生 Investigation 的合法技术接管可能被 inactive_handler 白名单跳过。
F-A1 接管 detail/告警固定宣称已转交、动作 already ran，与真实 skipped 结果矛盾。
F-S1 n8n status workflow 未把 ECS case 的 ticket.updated 送进 ECS。
F-S2 ECS status notifier 只覆盖旧 Engineer Case，遗漏原生 Hermes binding。

三、实际入口与最小实现

C1 Investigation 固定（F-R1）
源码：
- backend/automation_ecs_route_worker.py：ticket.updated 用系统 ticket_status_sync；Hermes 分支调用 store.hand_off_to_hermes_agent。
- backend/services/automation_ecs_store.py：InMemory hand_off_to_hermes_agent（调查行1209附近）；PG（3758附近），当前 comment 无条件 INSERT turn_kind=normal（3902附近）。
- backend/services/automation_ecs_contracts.py：HermesTurnPhase.phases_for，investigation_feedback 仅 work，normal 是 route/work/persona。
- backend/services/automation_hermes_agent.py：process（345附近）、start 前读 binding、phase loop（412附近）、_complete_investigation_review（920附近）。
- backend/services/automation_hermes_tools.py：tool_record_direction 仅 route phase；tool_escalate_human；resolve_awaiting_investigation_turn/continue_hermes_investigation。

实施：
- 用已持久化的首次有效 route 结果确认 initial Investigation；采用现有 binding/turn 历史，无新表/锁定分类器。不能把“最新 direction 恰为 investigation”的首次 Automation case 纳入本约定。
- 在原客户 intake->handoff 的原子事务内，为符合条件的 customer comment 创建现有 investigation_feedback 类型、phase=work、direction=investigation 的 turn。这里仅复用 work-only phase 语义，保留 event_type=comment.created、真实 event_id/execution_id/comment_id、完整 snapshot、原 conversation/session、版本 fencing。
- 不直接调用 create_investigation_feedback_turn 处理 customer webhook：它创建独立 execution、随机 feedback:{turn_id}、将内容归为 reviewer_feedback 并截断4000。客户事件不能伪装工程师反馈，不能丢 intake 幂等身份。
- 继承有效 Investigation direction/reason，给 timeline 留 server inheritance 依据；此轮 route 不被“虚构成功执行”，而应可审计地记录 skipped/inherited。
- 在 start_hermes_agent_turn 对 pending turn 的受锁状态转换处补同一条件检查/规范化，覆盖先排队 normal、随后首轮已确认 Investigation 的窗口。已经运行/已结束 turn 不事后改写。首轮尚无有效分类继续按既有初始分类与 supersede fence，不新增等待队列。
- 保留 accept_intake 的 event 幂等、case_revision、取消旧 turn 与 stale draft；重试/start 后使用最新有效 fence。
- 正常协作等待可继续，即使 binding.status=paused；实际人工接管的 binding（direction=human/escalation 已持久化）以及已终结工单不能因普通 comment 静默恢复。
- 不能用 account automation_status!=human_review_required 作为 sticky 条件：tool_record_direction 当前本来把 Investigation 映射到该旧字段。以原生 binding/turn/lifecycle 事实区分。
- 不能让 work 中的 routine policy escalation 绕过禁止 reroute：固定 Investigation 的 conversation follow_up、缺资料等继续以 investigation progress/blockers 回到原 thread；不调用接管链。处理 public tool_escalate_human 的这类请求时返回可继续协作的明确结果，避免无意义拒绝重试最终制造 hermes_run_failed。真实已记录的终态系统故障仍由已有 orchestrator failure 路径接管。
- Automation 及首轮未知/真正 human 路径的规则、阈值、route prompt 保持原行为。

C2 客户消息和调查结果在原 thread
- backend/services/automation_hermes_agent.py：_ensure_customer_comments_mirrored（1831附近）目前只镜像本地；_event_projection 保留 trigger_comment_id。
- backend/services/engineer_slack.py：post_engineer_slack_event、notify_hermes_investigation_result、notify_hermes_case_opened。
- 该轮触发 customer comment 的原文/作者类型/来源时间必须进入原 binding thread，并与该轮调查结果关联；不只发 AI 摘要。使用 trigger_comment_id 确定当前消息，不重复发送整个历史。
- customer 原文按不可信引用展示，不能成为工程师命令。正文/状态说明保留既有英文 Slack 协作约定。事件身份带 namespace+ticket+comment_id，复用 C4 的窄投递状态机制防 webhook/turn 重试重复。case root/thread 不重新创建；无 binding thread 时明确记缺失，不能发错 thread 或静默成功。
- 保留已有 Prepare draft / Approve & send 的人工客户回复机制；普通 customer continuation 不直接运行 persona/公开回复。

C3 真实接管与如实告警（F-H1/F-A1）
- backend/services/account_human_review_escalation.py：escalate_account_case_to_human_review（331附近），当前 active 只接受 Automation handlers；inactive 返回 skipped_inactive_handler。
- backend/services/automation_hermes_tools.py：_escalate 组织结果及取消回复/park。
- backend/services/automation_hermes_agent.py：_complete_human_direction_turn（1032附近）detail 固定说 the case was transferred。
- backend/services/account_failure_alerts.py：_build_human_takeover_alert（139附近）固定说 waiting for pickup、policy handoff、动作 already ran。
- 原生 Hermes 的合法接管必须携带可查证的 binding/turn 上下文，进入既有统一 chain；不要简单扩大全局 handler 白名单、不要让旧/未知 handler 都被激活。
- 内部备注、回队列、ownership release、pending reply cancellation、owner notification 的每项状态分开保存并用于文案。没有独立 evidence 的项写未确认，不能从 aggregate completed 或 queue 状态推断其他动作。
- 先修 _complete_human_direction_turn 的预先成功叙述，再把结构化 handoff 结果传到最终邮件 builder，避免仅 Detail 真实但固定尾部继续撒谎。
- skipped/failed/outcome_unknown 必须显示未执行/失败/结果未知；保留原始 failure_reason/job/attempt 和技术故障 vs policy takeover 的区分。
- 不重新执行已有确认备注/归属变更；保留既有 Production-only Zendesk handoff 限制，Preproduction skipped_not_production 必须如实。

C4 ticket.updated -> 原生 Hermes Slack（F-S1/F-S2）
A. n8n：
- 先刷新目标 workflow active/draft 和复用来源 graph，按仓库规则归档脱敏快照并跑 scripts/n8n/validate_workflow_snapshots.py。
- 在现有 [case]Sync Status 增加 ECS Preproduction membership->ticket.updated intake 分支；复用 comment workflow 的 GET executions 接口、凭据引用和已存在 intake。非归属票不投递，不跨环境创建 coordination case。Production ECS 分支可以预备为 disabled，不能本轮激活。
- event 身份使用 Zendesk 原事件/audit ID；若 webhook 只有 ticket ID，则固定为 namespace+ticket+真实 source updated_at+observed status，不使用 n8n execution ID、重试处理时间或 $now 生成不同事件。保持同一 snapshot 的 status 与 updated_at。事件只表达当前可观察状态，不虚构 webhook snapshot 没保存的历史中间状态。
- 未知 source timestamp 不伪造源版本；missing/非法字段明确失败。
- n8n SDK/节点/schema 工具按 MCP 必要规范使用；修改后验证、发布、回读 active/draft与版本，保留原 EC2/Production branches、credentials和同日其他修改。

B. SP：
- backend/automation_ecs_api.py 现有 /v1/intake 与 /v1/cases/{ticket}/executions 已存在。
- backend/automation_ecs_worker.py：AccountCaseProcessor status 分支（173附近）调用 sync_account_case_ticket_status。
- backend/services/automation_account_reply_sync.py：sync_account_case_ticket_status（1382附近）只找 get_active_engineer_case，且 automation_status=not_automated 才建旧 status event。
- 原生 hand_off_to_hermes_agent 明确不创建旧 Engineer Case。automation_hermes_case_bindings（coordination namespace+ticket）不同于 support_hermes_case_bindings（engineer_case_id）。
- 旧 support_engineer_slack_events 有 NOT NULL FK engineer_case_id，不能伪造旧 case，把 native status 塞进这张 outbox。
- 把 coordination store/job 的 execution identity 提供给 status 分支，查询该环境已有原生 binding，发送到其现有 slack_channel_id/thread_ts。即使 binding 已进入实际 human 接管，也保留对原 thread 的状态通知；solved/closed 不能被“已关闭”过滤掉。
- 文案为 status changed: prior -> current +真实source time+ticket link，不调用LLM、route/work/persona或生成 customer reply。首次本地 prior 未知时明确 unknown，不编造前状态。
- 复用现有 support_idempotency_records（scope,key,state,response_payload）与原 processing job，增加窄的原生 notification intent/claim 方法，不新增表/通用发送框架。在 repository.update_account_case_zendesk_status 现有事务内，受 status transition 行锁保护，把被接受的变化和该通知的 payload/key、target thread、pending 投递状态一起提交，消除“本地状态成功后进程崩溃通知丢失”的窗口。
- scope 含环境/namespace；key 含ticket+source revision+status；payload 保存原event/execution identity、prior/current/thread与投递结果。同一 job 重试即使 status update 返回 unchanged，也要查已有待发记录并继续处理，而不是跳过通知。
- 用原 processing job lease/fence + 幂等行条件更新 pending(或确证未投递failed)->sending 认领，确认发送后completed/confirmed保存Slack channel/ts。同 key并发、confirmed、sending、outcome_unknown 不可无条件覆盖。普通 record_delivery 是无条件upsert，不是原子claim，不可拿它宣称 exactly-once。
- 明确未发送的错误可按既有processing任务规则恢复，幂等记录保留intent。POST已发但timeout/5xx、sender crash于sending、发送成功但回执落库失败均记/保留outcome_unknown，不能自动重发或记delivered。回读存在确切消息身份才确认；没有身份则留人工处理证据。不自动再次接管 case 来处理单纯状态通知失败。
- 源时间单调：忽略 older snapshot，无新通知；同状态但 source更晚时推进 native 状态水位而不发变化通知，否则随后稍旧不同状态可能倒退。_plan_account_zendesk_status_transition（repository.py:949附近）当前在时间比较前返回unchanged，需在native路径补这个水位保证，并保留旧工程师路径合同。
- 跨 coordination/ticket repo 的外部Slack发送不承诺跨库原子/exactly-once。原子承诺仅为 ticket repo 内状态变化与notification intent；coordination job保存可恢复、可审计结果。

C5 工程师关闭 skill + tool
- backend/services/zendesk_comments.py:update_ticket_status（472附近）已有纯status PUT，校验返回ticket status；异常有outcome_unknown。
- backend/services/automation_hermes_slack_actions.py:handle_slack_hermes_message（169附近）是工程师 thread feedback 入口；API /api/integrations/slack/hermes-cases/messages 用 _require_n8n_request_token，核对team/channel，并由thread查ticket。
- n8n app-mention workflow 本地快照 r1HIW8UNuCabiOPn 向上述messages送team/channel/thread/slack_user_id/text；需live核查该具体workflow后，为Preproduction消息分支补同一inbound Slack event_id/message ts。保留已存在inbound ledger身份与真实性验证，不新建鉴权服务。
- 第一版明确关闭指令仅支持工程师当前消息去掉bot mention并trim后的 close the case（大小写/末尾句号的等价规范化）；不做模糊意图判断。否定、引用、客户文本、旧history不赋予权限。其他工程师反馈仍原流程。
- 在创建工程师work-only turn时把已验证source_event_id、actor、thread->ticket、case_revision与“本轮明确关闭授权”保存到现有turn/execution JSON；客户turn绝不能带该授权。不是由tool请求正文的source=slack布尔值赋权。
- 增加 tool_close_case 到既有 /v1/agent/tools/{tool_name} dispatcher；插件名可用 support_close_case，参数只有turn_id，ticket由server binding决定。校验real case非adhoc、当前active turn与revision、当前工程师授权、环境和Zendesk sideeffects开关。前置读工单归属/当前status，不能越权操作其他工单或human-owned无授权工单。
- 复用现有delivery/idempotent primitives，用环境+ticket+Slack event ID保存操作；重复tool/event不重复PUT。sideeffects关闭时明确not_executed，不宣称closed。
- 只更新solved，验证实际返回/必要的现有GET回读；已solved/closed幂等返回。PUT结果不确定先核对实际状态，没有确证不盲重试、不报告成功。
- 成功关闭后，该turn以明确终态完成，避免继续跑调查/persona或生成草稿。复用既有取消/草稿stale/fence防旧pending消息继续发送；本地status与Slack通过C4相同同步契约收尾。原有知识治理收尾不重写。
- 新工具的 schema/forwarding 注册位于 /Users/xieziling/Desktop/agentRelay/hermes-deploy/build/supportportal_agent_tools/__init__.py，work/common toolsets配置在同文件；已有真实插件合同测试 test/test_supportportal_agent_tools.py。需要linked SP+deploy仓版本，不能仅改backend却说Hermes可调用。
- 新skill保存为版本化SKILL.md，明确“工程师明确指令->调用support_close_case->检查结果->报告Slack”，客户感谢不触发。不新增一轮审批/意图LLM。以现有 skills toolset/skill_view 加载；Investigations已开启 skills。
- 按现有Preproduction-only skill投放方式操作（build/Dockerfile.hermes-skills-drop 说明EFS /opt/data/skills，docs/ecs-preproduction-hermes.md说明AP隔离），部署前核对实际AP与Production disjoint。不得写共享/Production技能目录。必要插件镜像overlay从当前实际运行且digest核验的Hermes基底构建，保留原SQLite与已投放插件；build source、Dockerfile、skill、checks、digest必须在Git归档。

四、实施顺序、记录与验证

0. 先读最新全局与SupportPortal AGENTS.md、RTK.md、implementation-handoff执行模式；跨仓读取其适用规则。shell用rtk。核对git status/branch/worktrees。在SupportPortal用 scripts/workflow/create_task_worktree.sh investigation-route-status-fix 生成自己的 .worktrees/investigation-route-status-fix / codex/investigation-route-status-fix，根main无tracked编辑。不能改其他任务workspace。
1. 本功能归属现有 automation-execution-loop Function，新建本计划Task（不要覆写已done的p2-189或借用p2-187）。创建前刷新main核对空闲Task ID，避免重复此前撞号；同一Task记录C1-C5、findings、状态/next_action/evidence。执行线程把本v1.1合同原文与最新证据保存在本任务版本化文档/PR，唯一current record；修改合同须说明版本变化，物料无secret/客户原文。
2. 先实现C1+C2代表路径：初始Investigation -> 真实customer comment.created intake -> route worker -> handoff -> claimed agent turn -> work -> 同session/thread协作。验证后实现C3，不顺手改Automation分类。
3. 实现C4 SP端和隔离PG事务/幂等边界，再准备n8n Preproduction分支。实现C5 SP+插件+skill，补真实handler测试。更新prompt/tool行为日志 docs/prompt_change_log.md、相关operations说明及progress，生成Overview --write/--check，不手改data.js。
4. Pre-merge行为验收（是executor必做verification，不新增人工review门禁）：
   - C1 首轮Investigation后分别注入补频道、感谢、请求关闭；route/classify调用数=0，work调用正常，继承真实session/thread，event/execution/comment身份完整，输入仍customer来源，原文工程师可见，正常结果是awaiting_investigation_review而不是human_takeover。
   - 已提前排队normal后首轮分类提交、worker retry/restart、重复event、并发comment/cancel/fence；旧draft stale，旧turn不能继续发布。
   - 首轮Automation、原有Automation followup、初始human、adhoc、实际已human接管、终结case均保留各自边界；首次Automation后来Investigation不得被当作initial Investigation。
   - 工作LLM模拟policy升级不会转交；真正terminal run failure走一次合法native接管，handler不误skip；retryable/outcome_unknown不误接管。首次合法初始route仍不受影响。
   - C3 分别sent/queued、skipped_inactive_handler、skipped_not_production、失败和unknown，邮件subject/body/detail无未经证实成功叙述；真实failure_reason/job/attempt仍可追溯。
   - C4 真实ticket.updated intake->route system->processing job->native binding->fake Slack；open/pending/solved/closed变化、旧Engineer兼容、wrong-env/unknown ticket无创建、无LLM/route/回复调用。
   - 隔离PG证明native状态+pending通知同事务提交；回滚两者都无；“提交状态后发送前崩溃”重试仍发；duplicate/concurrent sender不重复；timestamp newer-same-status推进水位；late snapshot不倒退；confirm/failed/outcome_unknown与发送成功落库失败分别验证，不用mock证明PG原子性。
   - C5 从带可信event ID的工程师入口驱动Hermes tool/API/plugin forwarding链；支持明确close、拒绝客户/引用/否定/adhoc/跨票/stale/disabled sideeffects；一次solve+回读、重复零新增PUT、unknown不谎报；plugin真实schema可注册/work toolset可发现、skill实际可加载。禁止以customer内容构造授权来“通过”。
   - 对F-R1及主要通知丢失修复保留修复前失败/修复后通过的可检出证明。按差异跑窄回归；existing参考文件 test_hermes_zendesk_agent.py、test_hermes_zendesk_agent_postgres.py、test_hermes_tool_failure_handoff.py、test_automation_ecs_route_worker.py、test_automation_ecs_worker.py、test_engineer_slack.py、test_account_failure_alerts.py、test_account_human_review_escalation.py 及deploy仓真实插件测试。不要只给测试总数或弱化断言。
5. 正常finalize通过后按仓库脚本merge/squash、必要CodeSight根main刷新。部署只有一个执行owner（本线程）；检查其他release在途情况，避免争同环境。必须按runbook保持immutable release、Prompt/Schema/image/provenance/provider/health/CloudWatch/Terraform/rollback全门禁，pipeline失败不能用steady代替complete证据。
6. 发布順序：SP Preproduction支持新契约 -> Hermes Preproduction插件/skill（如需镜像重建）-> 发布n8n相应Preproduction分支 -> 回读每层当前version/digest/skill hash/tool registration，并绑定同一个证据链。共享workflow所有非目标分支不变，Production ECS新分支若预备则仍disabled。
7. Post-deploy验证：读active/draft graph一致与execution路径、实际SP/Hermes release/digest、tool availability与skill_view读取。业务写入只用既有官方隔离Preproduction测试fixture；其使用权限/沙箱必须符合runbook，不能把部署授权解释成创建或修改真实客户票、给真实Slack thread发测试消息。无授权测试fixture时报告功能样本缺口，等自然事件，不伪造PASS。
8. 完成后按规则从根执行cleanup，只清本任务workspace；保留versioned合同与evidence。按实际边界报告状态、Task/PR链接、SP/deploy完整HEAD、n8nactive版本、发布证据、关键行为与未覆盖限制。在你自己的线程直接向用户报告，不自动给其他线程发消息。如卡在真实产品/授权变更，直接问用户而不是只在handoff或PR藏问题。

当前本来源线程只完成调查和执行计划，没有代码改动、没有新测试结果、没有修改n8n或部署。请由你开始实施。
