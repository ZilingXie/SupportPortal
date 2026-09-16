# n8n 工作流运维目录

本目录纳管个人项目 **Zac Xie**（`sbTCwK2d5D7GOKQb`）拥有的非归档工作流，实例为 [n8n.stellarix.space](https://n8n.stellarix.space)。范围按项目所有权筛选，不把当前账号能看到的其他项目工作流计入。纳管指在 SupportPortal 中维护用途、关系和排错知识，不迁移 n8n 项目。

## 命名规则与目录结构

2026-09-16 完成统一改名与目录整理：28 个工作流全部改用 `[domain]Action[|Qualifier]` 命名，并归入六个同级文件夹。子流程调用（Execute Sub-workflow）与错误处理（errorWorkflow）均为 workflow ID 引用，改名与移动不影响调用链；节点、连接、凭据、Webhook 路径与发布版本保持原样，`[slack]Route Support` 的未发布草稿保留未发布。

- 领域用小写短词：`case`、`slack`、`kb`、`review`、`ops`、`content`、`notify`；动作用简短英文。
- 只有需要区分来源、环境或变体时才加 `|Qualifier`；统一用 `Case` 不用 `Ticket`；不使用随意编号、`fixed`、`V3`，不把启用状态写入名称。
- 旧名对照见下表；历史背景与已知问题记录在 description 和 [启用流程说明](./active-workflows.md)。

| 文件夹 | 数量 | 内容 |
| --- | ---: | --- |
| `01 - Cases` | 4 | Zendesk 工单/评论/状态对 SupportPortal 各环境的接入与同步 |
| `02 - Slack` | 4 | Slack 侧入口：建单路由、线程转交、按钮操作、交接通知 |
| `03 - KB` | 2 | CSD 与已解决工单的知识入库 |
| `04 - Review` | 4 | 工单质检（随机/长龄/满意度）及共享评审子流程 |
| `05 - Ops` | 2 | 错误上报与错误链路测试 |
| `99 - Inactive` | 12 | 未启用的历史/原型流程，集中收纳 |

### 旧名对照

| 旧名 | 新名 | ID |
| --- | --- | --- |
| new_case_2_supporportal_prod | [case]Intake\|ECS Route | 1am2EuuDMV3RUwsJ |
| commen_sync_ecs_production | [case]Sync Comments | zc2ndUDqDAS0uX1Y |
| status_sync_automation_production | [case]Sync Status | 03B6AvcrOgRkWlUc |
| new_case_2_supporportal_staging | [case]Intake\|EC2 Staging | qFSNOmYXr97N2UGX |
| 2_slack - SupportPortal Account Handoff -> Slack | [slack]Notify Handoff | 208nrQNRfpkSkQhM |
| Slack_zen_Bot | [slack]Route Support | kyiA0QuiVx6JJ03i |
| NonAutomate_to_slack_fixed | [slack]Forward Thread\|Prod | r1HIW8UNuCabiOPn |
| Slack Interaction to SupportPortal - Consume Button V3 | [slack]Handle Action\|Preprod | FKv8vtZBQk6tH4Gt |
| CSD_2_KB | [kb]Build\|CSD | GgDxPEWtW7ltT5BW |
| ticket_2_KB | [kb]Build\|Solved Cases | MM3Z3T469Eru3Q1I |
| case_review_subflow | [review]Case\|Subflow | b1Unpl6miABzcTmZ |
| random_case_review | [review]Random | vvyPwdWXvJENN1zm |
| longage_case_review | [review]Aged | G7snyHhdBCnIpbJV |
| negative_case_review | [review]Rating | WT43uQ1i8SPsYRJi |
| error_handle | [ops]Error Alert | dV5vNA6l1MbDMHZt |
| test_error | [ops]Error Test\|Zendesk | 3zJvu5KQFZIoOoqu |
| AI_KB_Generator+RAG | [kb]Build\|RAG Prototype | bKpMsD2NpSQ5o7eZ |
| auto_post | [content]Post News\|X | IK9w81CtoBo85iRa |
| backup | [ops]Zendesk Trigger\|Stub | lctKEGZv1N3rXptP |
| Case_RCA_Report | [review]RCA Report | RN9XgncbUCar7Zi7 |
| Cx_sentiment | [review]Sentiment | OeyYrNC7dQh4ubM7 |
| gmail_2_dc | [notify]Gmail to Discord | 88eWLCeMJdfS0cz4 |
| KB_newletter | [kb]Newsletter | pwBG2SFM9zqNLR5s |
| My workflow 7 | [slack]Form Test | ptIiaodSBLcBDNJ3 |
| new_case_2_supporportal_account | [case]Intake\|EC2 Account | sBK38TckqpgKVr3B |
| new_case_2_supporportal_preproduction | [case]Intake\|EC2 Preprod | IuEGIeBDINorbX6E |
| new_case_2_supporportal_production | [case]Intake\|EC2 Prod | ruBKqA3h9StocJby |
| new_zen_2_sup | [case]Intake\|EC2 Account Original | uEJEyu6hQJa2S2ph |

## 核对范围与状态

2026-09-16 完成配置核对：28 个工作流，**16 个启用、12 个未启用，28 个均已开启 MCP**。同日完成统一改名、目录整理和 5 处 description 中的工作流名引用更新，全量回读通过；文件夹计数 4/4/2/4/2/12，抽查确认发布版本与节点图未受影响。以下是该日快照，后续操作前须刷新。

| 标记 | 含义 | 不能据此推断 |
| --- | --- | --- |
| 启用 / active | n8n 中的启用状态 | 最近执行成功、所有画布节点都可达 |
| MCP 已开启 | 流程可通过 MCP 读取/使用；本次 28 个详情均读取成功 | 已经执行过，或支持任意一步的暂停、恢复与重跑 |
| description 已同步 | 16 个启用流程的本地描述与 n8n 回读逐字一致 | 流程已重新发布或运行逻辑已修复 |
| 未启用 | 保留的草稿/历史配置 | 手动执行没有外部写入 |

改名当日只修改 workflow 名称、文件夹归属和 5 处 description 引用；没有触发执行或发布草稿。未启用流程的 n8n description 保持为空，本页补充其配置用途。

## 启用工作流（16）

用途以**已发布节点图及可达路径**为准。详细入口、依赖、实际环境与恢复注意事项见 [启用流程说明](./active-workflows.md)。

| 工作流（n8n 链接） | 与 SupportPortal 的关系 | 用途 | 本地详情 |
| --- | --- | --- | --- |
| [[slack]Notify Handoff](https://n8n.stellarix.space/workflow/208nrQNRfpkSkQhM) | 直接接入 / Slack 通知 | SupportPortal 交接确认事件经 PostgreSQL 去重后通知 Slack。 | [运维说明](./active-workflows.md#w-208nrQNRfpkSkQhM) |
| [[review]Case\|Subflow](https://n8n.stellarix.space/workflow/b1Unpl6miABzcTmZ) | 配套 / 支持质检 | 获取工单，由 AI 摘要后在 Slack 等待人工评审并记录结果。 | [运维说明](./active-workflows.md#w-b1Unpl6miABzcTmZ) |
| [[case]Sync Comments](https://n8n.stellarix.space/workflow/zc2ndUDqDAS0uX1Y) | 直接接入 / 评论同步 | 按 Case 归属同步旧 Production，以及 ECS 两个环境的评论事件。 | [运维说明](./active-workflows.md#w-zc2ndUDqDAS0uX1Y) |
| [[kb]Build\|CSD](https://n8n.stellarix.space/workflow/GgDxPEWtW7ltT5BW) | 知识入库 / CSD | 已解决 CSD Bug 经筛选生成 Zendesk 草稿并导入 Memory Wiki。 | [运维说明](./active-workflows.md#w-GgDxPEWtW7ltT5BW) |
| [[ops]Error Alert](https://n8n.stellarix.space/workflow/dV5vNA6l1MbDMHZt) | 配套 / 故障上报 | 关联流程失败时生成简短摘要并上报 n8n 状态页。 | [运维说明](./active-workflows.md#w-dV5vNA6l1MbDMHZt) |
| [[review]Aged](https://n8n.stellarix.space/workflow/G7snyHhdBCnIpbJV) | 配套 / 长龄工单质检 | 工作日 06:00 抽取一条创建超过 30 天的未解决工单评审。 | [运维说明](./active-workflows.md#w-G7snyHhdBCnIpbJV) |
| [[review]Rating](https://n8n.stellarix.space/workflow/WT43uQ1i8SPsYRJi) | 配套 / 满意度质检 | 跳过 GOOD 满意度事件，其余进入 Negative 人工评审。 | [运维说明](./active-workflows.md#w-WT43uQ1i8SPsYRJi) |
| [[case]Intake\|ECS Route](https://n8n.stellarix.space/workflow/1am2EuuDMV3RUwsJ) | 直接接入 / ECS 新工单 | 补全新工单资料，按公司名单分流到 ECS Preproduction/Production。 | [运维说明](./active-workflows.md#w-1am2EuuDMV3RUwsJ) |
| [[case]Intake\|EC2 Staging](https://n8n.stellarix.space/workflow/qFSNOmYXr97N2UGX) | 直接接入 / 历史 Staging | 仍启用的旧 EC2 Staging 工单入口，需核对目标可用性。 | [运维说明](./active-workflows.md#w-qFSNOmYXr97N2UGX) |
| [[slack]Forward Thread\|Prod](https://n8n.stellarix.space/workflow/r1HIW8UNuCabiOPn) | 直接接入 / Slack 消息 | 把内部 Slack 线程中的人工 @提及转交 ECS Production Engineer Case。 | [运维说明](./active-workflows.md#w-r1HIW8UNuCabiOPn) |
| [[review]Random](https://n8n.stellarix.space/workflow/vvyPwdWXvJENN1zm) | 配套 / 随机质检 | 工作日 06:00 按每位已配置负责人抽一条未评审工单。 | [运维说明](./active-workflows.md#w-vvyPwdWXvJENN1zm) |
| [[slack]Handle Action\|Preprod](https://n8n.stellarix.space/workflow/FKv8vtZBQk6tH4Gt) | 直接接入 / Slack 操作 | 消费 Slack 按钮操作，交给 ECS Preproduction Hermes 并移除按钮。 | [运维说明](./active-workflows.md#w-FKv8vtZBQk6tH4Gt) |
| [[slack]Route Support](https://n8n.stellarix.space/workflow/kyiA0QuiVx6JJ03i) | 上游接入 / Slack 建单 | Slack 支持请求创建 Zendesk 工单；内部线程消息转交 SupportPortal。 | [运维说明](./active-workflows.md#w-kyiA0QuiVx6JJ03i) |
| [[case]Sync Status](https://n8n.stellarix.space/workflow/03B6AvcrOgRkWlUc) | 直接接入 / 状态联动 | 旧 EC2 双环境状态同步；持续 404 的 ECS 分支已于 2026-09-16 移除。 | [运维说明](./active-workflows.md#w-03B6AvcrOgRkWlUc) |
| [[ops]Error Test\|Zendesk](https://n8n.stellarix.space/workflow/3zJvu5KQFZIoOoqu) | 配套 / 运维测试 | 聊天输入工单号后查询 Zendesk，失败时验证共享错误处理。 | [运维说明](./active-workflows.md#w-3zJvu5KQFZIoOoqu) |
| [[kb]Build\|Solved Cases](https://n8n.stellarix.space/workflow/MM3Z3T469Eru3Q1I) | 知识入库 / Zendesk | SOLVED 工单经去重和 AI 筛选后生成 KB 草稿并提交知识库。 | [运维说明](./active-workflows.md#w-MM3Z3T469Eru3Q1I) |

## 未启用工作流（12）

以下说明来自草稿配置，代表设计用途，不代表当前在自动运行；均保留未启用状态，集中收纳于 `99 - Inactive`。

| 工作流（n8n 链接） | 用途与边界 |
| --- | --- |
| [[kb]Build\|RAG Prototype](https://n8n.stellarix.space/workflow/bKpMsD2NpSQ5o7eZ) | 手动固定工单的知识库/RAG 原型：Zendesk 评论与作者 → AI KB → PGVector 相似度查询；无相似内容时写入全文和分节向量。重复分支向 Slack 请求批准，但批准后的后续链路未接通；错误处理引用旧归档流程。 |
| [[content]Post News\|X](https://n8n.stellarix.space/workflow/IK9w81CtoBo85iRa) | 手动检索新闻并由模型生成 X/Twitter 帖子的发布流程；部分图片节点禁用，另一条检索/上传链与触发器断开。手动运行仍可能对外发布，与 SupportPortal 无直接接口关系。 |
| [[ops]Zendesk Trigger\|Stub](https://n8n.stellarix.space/workflow/lctKEGZv1N3rXptP) | 仅含两个互不连接的 Zendesk 触发节点，是配置占位；没有数据备份链路。 |
| [[review]RCA Report](https://n8n.stellarix.space/workflow/RN9XgncbUCar7Zi7) | 手动读取固定 Zendesk 工单，整理并脱敏评论，由 AI 生成 RCA/HTML 后经 Gmail 发送；属于报告原型，手动执行可能发邮件。 |
| [[review]Sentiment](https://n8n.stellarix.space/workflow/OeyYrNC7dQh4ubM7) | Zendesk 评论 Webhook → 作者条件判断 → 内部情绪服务、评论及 AI 分析 → Google Sheets/执行元数据；历史情绪分析流程。 |
| [[notify]Gmail to Discord](https://n8n.stellarix.space/workflow/88eWLCeMJdfS0cz4) | 配置为每分钟轮询 Gmail，经模型摘要后交给两个 Discord 消息节点；个人通知工具，无直接 SupportPortal 接口。 |
| [[kb]Newsletter](https://n8n.stellarix.space/workflow/pwBG2SFM9zqNLR5s) | 配置为周二 09:00 汇总过去七天 Zendesk 文章，生成摘要、作者和内外部标签并发送 HTML 邮件；没有文章时另发提示。未指定工作流时区，应核对实例时区。 |
| [[slack]Form Test](https://n8n.stellarix.space/workflow/ptIiaodSBLcBDNJ3) | 手动发送 Slack sendAndWait 表单并更新消息的人工交互试验。 |
| [[case]Intake\|EC2 Account](https://n8n.stellarix.space/workflow/sBK38TckqpgKVr3B) | 历史新工单标准化后提交 support.stellarix.space/account；优先级/公司判断旁支不拦截主提交。 |
| [[case]Intake\|EC2 Preprod](https://n8n.stellarix.space/workflow/IuEGIeBDINorbX6E) | 历史新工单标准化后提交旧 EC2 /automation/preproduction/v1/cases；不是当前 ECS 域名。 |
| [[case]Intake\|EC2 Prod](https://n8n.stellarix.space/workflow/ruBKqA3h9StocJby) | 历史新工单标准化后提交旧 EC2 /automation/production/v1/cases；与启用中的 [case]Intake\|ECS Route 不同。 |
| [[case]Intake\|EC2 Account Original](https://n8n.stellarix.space/workflow/uEJEyu6hQJa2S2ph) | 更早的新工单标准化与组织补全流程，提交旧 /account；含未连接请求节点和禁用 Webhook。 |

## 主要调用关系

- Zendesk 新工单 → `[case]Intake|ECS Route` → ECS Preproduction / Production；`[case]Intake|EC2 Staging` 仍启用，目标需另行核对。
- Zendesk 评论 → `[case]Sync Comments` → 旧 Production 与 ECS 两个环境；状态变化 → `[case]Sync Status` → 旧主栈与旧 Production（ECS 不经此流程，2026-09-16 移除了持续 404 的 ECS 分支）。
- Slack → `[slack]Route Support` → Zendesk 建单，或 `[slack]Forward Thread|Prod` → ECS Production；按钮交互另由 `[slack]Handle Action|Preprod` 发往 ECS Preproduction。
- SupportPortal 交接确认 → `[slack]Notify Handoff` → Slack。
- `[review]Random` / `[review]Aged` / `[review]Rating` → `[review]Case|Subflow` → Slack 人工评审及记录。
- CSD / 已解决 Zendesk 工单 → 各自知识流程 → KB 草稿和 SupportPortal 知识入口；关联流程失败 → `[ops]Error Alert` → n8n 状态页。

## 日常查看与排错

1. 先按个人项目 ID 查询清单，确认目标 ID、启用状态、MCP 标记和更新时间。通过 n8n UI 拖拽编辑；AI 通过 MCP 查看和协助修改同一份流程。
2. 区分已发布图与草稿。生产执行按已发布图理解；手动测试的草稿可能不同。`[slack]Route Support` 存在未发布修改，编辑前先对比两版。
3. 打开具体 execution，记录 workflow ID、execution ID、对应版本、触发时间、失败节点及父子流程关联；检查节点输入、输出和错误。再沿 Zendesk 工单号、事件 ID 或 SupportPortal 关联记录查接收结果。不能仅凭 HTTP 请求成功断言业务处理完成。
4. 修改节点前确认授权范围和目标环境。重试前查清外部写入、去重记录和人工等待状态；本目录不把整轮重跑当作单步恢复。具体执行是否支持从失败处重试/继续，须以该版本 n8n 提供的操作和该次 execution 数据为准。
5. `sendAndWait` 是流程中已设计的人工等待点，应使用原等待入口恢复。开启 MCP 不会自动为任意节点添加暂停/恢复能力；新增检查点需作为流程变更单独实施。
6. 改动后回读受影响字段并核对实际发布版本。执行验证另按授权进行；在运行前说明可能产生的建单、发消息、知识入库或其他业务写入。

仍未处理的配置差异见 [待核对配置](./active-workflows.md#configuration-findings)。本次没有全面查询执行历史，未评估各流程近期成功率，也未建立定时监控。

## 文档维护与来源

- n8n 是流程图、节点参数、草稿/发布版本和执行记录的事实来源；本目录维护用途、项目关系、环境与运维注意事项。
- 新增或改名工作流时遵循本页命名规则并更新旧名对照表；用途发生变化时，同时更新 n8n description 与 [启用流程说明](./active-workflows.md) 中的“n8n description”，并回读确认一致。启停、归属变化时更新本页分组和核对日期。
- 实时核对使用项目过滤后的 `search_workflows` 与 `get_workflow_details`；必要时再读取指定 execution。MCP 可用标记的常规维护优先使用 n8n 界面。
- [环境矩阵](../environments.md) 区分 ECS 与旧 EC2 路由；[Nginx 配置](../../../deployment/nginx/supportportal.conf) 是仓库配置证据，不能代替线上请求结果。
- [运维总索引](../README.md) 说明维护方式；[AGENTS.md](../../../AGENTS.md) 仍是授权与执行规则入口。手工资料保存在本目录，CodeSight 继续维护生成的代码地图。
- 不保存原始工作流导出、凭据、Webhook 密钥、客户文本或私人联系信息。业务执行调查的脱敏证据应注明时间和执行 ID，避免把暂态结果当长期健康结论。
