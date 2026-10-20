# Investigation 文件启用前证据轮（Preproduction）

计划名称：`investigation-case-files-r2-preprod-file-enable`；修订：v1；执行日期：2026-10-20。

状态：阶段一（前置证据）、阶段二（本地/隔离验证）与阶段三的只读回读部分完成。未构建或发布 Hermes 镜像、未开启文件开关、未部署、未修改 n8n、未触碰 Production。文件启用阶段停在 `waiting-for-evidence`，恢复事件见下文。

2026-10-20 用户独立验收：**通过（仅限本启用前证据轮）**，验收对象为合并提交 `95b1d7b4` / PR #1521、计划记录、机器证据、任务登记与 Git 状态；确认 Preproduction 关闭状态记录一致、测试与检查属实、p2-192 维持 blocked/waiting-for-evidence。本通过不包含文件传输上线验收，剩余门禁：Zac 真实文件接口证据、获准 synthetic 样本与受控 Slack thread、Hermes overlay 输入重定到含 p2-203 能力的插件源并重新验收、并行车辆 `r20261020-8e8e495` 完成后重新冻结基线、陈旧非终态记录（991000183-1/13872/13904）收口、显式恢复 `HERMES_CASE_WORKFLOW_MODE`、镜像/profile 发布后的 Slack/Zendesk/Zac C1-C7 云端闭环。

## 本轮执行的验证

| 项 | 结果 |
| --- | --- |
| 当前线上 release | `r20261019-21c6eed`（git 21c6eed1），API:161 / Route:157 / Worker:160 单一 PRIMARY deployment，digest 与 manifest 一致；schema `automation-ecs-016`；prompt `pr-88298d4e0a3f` 未变 |
| 文件开关 | API/Worker 显式 `0`，Route 缺省 `0`；`investigation_case_files` Map 计数 0 |
| Hermes 云端能力 | td46（2026-10-15）：profile 无 toolsets/platform_toolsets、`plugins.enabled: []`；69 个 skill 无 `support-case-files`；`/opt/hermes/case-files-skill` 不存在；live plugin 5 个 toolset 无 `supportportal_files`、无文件工具 |
| 关闭态 live 探针 | 当前 release 上 `case_files_list/read/download_stream` 三入口均 422 `case_files_disabled`（intake Bearer 真实入口；disabled 检查先于 capability 校验） |
| 本地主套件 | 隔离 PG `p2192_case_files_test` 重建后 6 个测试文件 `290 passed`（R2 时 289，中间合入净增 1 项，零失败），exit 0 |
| 关闭态配置保留门禁 | `test_case_files_closed_release_guard.py` 38 passed |
| 云端 bundle 校验 | 对 agent-infra（HEAD=bc9a0f7d）一次性树验证五门禁 passed；skill 校验通过；部署脚本 `bash -n` 通过 |
| 并行发布检查 | 发现并发车辆：`r20261020-8e8e495-hermes-appid-pp-a1-r2` 三角色镜像 2026-10-20 09:19-09:20 已推 ECR（CodeBuild 3748aae9，发起人 Zac），**尚未部署**（服务仍 r20261019-21c6eed）；此后无 in-progress 构建 |

机器可读数值与命令见 [证据](investigation-case-files-r2-preprod-file-enable-evidence.json)。DSN 与 intake token 仅经 SSM 读取，未落盘。

## 发现（进入启用阶段前必须处理）

1. **Hermes overlay 覆盖风险**：`source-baseline.json` 四文件哈希对本地 agent-infra（bc9a0f7d）全部 MATCH、patch 可干净应用；但 live 插件已含 p2-203 引入的 `supportportal_investigation` toolset 与 `support_zac_consult`，baseline 树均无（p2-203 记录源 HEAD a49f64b4，本地克隆无此对象）。`Dockerfile.overlay` 会整目录 COPY `supportportal_agent_tools`——按当前输入构建会回退 live 的 zac consult，正是 runbook 禁止的"用 canonical plugin 覆盖 live 差异"。构建前必须先把 bundle 插件输入重定到 p2-203 世代源并重新验收受影响合同。
2. **并发发布车辆**：hermes-appid A1 r2 车辆已建未发。文件启用 release 须在其完成后从新 main 重建，按并发发布惯例让行。
3. **陈旧在途行**：DB 无本周新增在途（账本 pending/sending/outcome_unknown=0），但存在陈旧非终态：queued request `991000183-1`（2026-10-02 synthetic 遗留，从未 claim）、pending runs 工单 13872（10-06 ×2）、outcome_unknown run 工单 13904（10-07）。runbook 要求切换开关/签名 key 前先收口核对。
4. **HERMES_CASE_WORKFLOW_MODE=disabled**：10-19 stage6 七开关全关的后果。代表性文件闭环（Slack→Investigation→审批→Zendesk）需要经显式发布参数恢复该模式，启用轮需一并声明。
5. **Zac 文件接口证据缺失（R2-ZAC 维持 waiting-for-evidence）**：仓库已核实——`zac_consultation.py` 只有 `/v1/responses` SSE 咨询协议；`import_zac_file` 的 acquire seam 合同为 `acquire(session_id, message_id, file_id) -> {data, filename, content_type}`，source='zac' 强制 session_id+source_message_id、operation_id=source_file_id 去重、binding 存 `zac_session_id`。真实文件端点/鉴权/格式/限制/会话关联方式仍未知，需用户提供。C7 不能以 synthetic adapter 冒充通过。

## 停止点与恢复事件

- Zac 真实文件接口与获准样本由用户提供并确认后，才进入 Hermes 镜像构建、profile 发布与开关启用（本计划阶段三剩余部分与阶段四）。
- 并行车辆 `r20261020-8e8e495` 完成部署后，启用轮从新 main 重建 release。
- 启用前收口上述陈旧在途行，并随启用发布显式恢复 `HERMES_CASE_WORKFLOW_MODE`。
- 合成文件样本与受控 Slack 线程需用户批准（沿用获准 synthetic case 渠道）。

## 边界声明

本轮不宣称文件功能任何形式的启用验收；关闭状态与无附件流程在当前 release 上的行为已由 live 探针与本轮测试复核。p2-192 原 R1 发布复审/真实业务门禁不受本轮影响，继续分别保留。

## v2 执行记录（2026-10-20，启用轮）

状态：**执行完成，等待独立验收**。C1-C6 全部通过（live 与离线分层证据），C7 继续 `waiting-for-evidence`。机器可读证据见 [v2 证据](investigation-case-files-r2-preprod-file-enable-evidence-v2.json)。

### 执行摘要

1. **基线冻结**：等待并行车辆 `r20261020-8e8e495` 完成（其 Hermes 经历 td46→td45→td47/48 回滚序列，最终镜像 2cdc5a8a 已把插件退回 p2-203 之前）。
2. **三方合并 overlay**（不再以 baseline 树为底）：gateway 取新镜像（保留 p2-188 failure_reason），skills_tool/插件取 td46 镜像（完整 p2-203 profile 栈含 zac consult），叠加 R2 文件能力；含 `supportportal_skill_profile` 模块与 investigation-profiles。合并树验证五门禁+profile 负向全过；镜像 `a40faeca` 构建推送、td49 部署、EFS 安装 skill。合并脚本入库 [v2-merge](../../deployment/hermes-case-files/v2-merge/)。
3. **两次 SP 发布**：r20261020-dd37c4d（开关=1，知识开关保留 0，422 变为 `file_scope_invalid`）与修复后的 r20261020-0bd735e-r2（api:164/route:160/worker:163，现行）。
4. **修复轮 PR #1524**：case_task 方向提升只写了 automation（13968 时代不对称），investigation 案件 binding.direction 永远停在 pending，Slack 文件门禁因此永不放行——14142/14144 实证。双侧存储对称提升 + 真实入口测试（memory+PG）+326 回归全绿。
5. **代表性文件闭环（14147）**：Map 登记（`14147-engineer-2.txt`，managed 命名含全局序号）→ 模型真实调用 list×1/read×1/search×3 → 标记命中第 6/10/14 行（无截断，与文件逐字一致）→ 草稿冻结快照 → 审批 → Zendesk 公开评论 54778303456916 + 附件 54778318315924（`14147-engineer-2.txt` 1083B）→ 工单保持 open。
6. **live 负向**：伪造/篡改/缺失 capability 全部 422；重复事件身份冲突且 Map/turn 零重复；已解决工单投递被 worker 取消（14145）。

### 必须披露的边界与发现

- **appid 线程交互**：本轮期间并行线程重建 Hermes（最终 td52/`180e2c98`，构建于合并能力之上）；所有 live 测试实际运行于 td52，容器内标记事后复核完好。该线程曾回滚 p2-203 插件，本轮按计划要求以三方合并将其恢复。
- **n8n 传输跳**：工程师文件消息以真实 Slack 文件/消息/线程元数据直调 SP API；唯一可用凭据是工程师 bot token，而 n8n 过滤器按设计排除 bot 事件（R1 已验证该传输跳）。真实人类上传路径未演练。
- **自动 solve 时序**：reply turn 完成后约 4 秒流程自动 solve 工单（14145 实证），人工审批窗口极窄；14147 以反馈文本"保持 open"绕过。此为既有行为，未在本轮修改。
- **SLO**：两次发布总时长分别为 1202.8s 与（r2 略短），超 900s 常规 SLO，硬门禁全过，如实保留。
- 草稿表在交付完成后仍显示 queued（14057 已知显示差异，交付账本为准）。

### 剩余门禁

- C7 Zac 文件：`waiting-for-evidence` 不变（真实文件接口/样本仍缺）。
- Production：未触碰；启用与业务写入仅限 Preproduction。

### 修复轮 R2-V2-B1（2026-10-20 独立验收未通过后）

- **发现**（验收方）：真实 Work scope 同时绑定 `supportportal_skill_profile` 与 `supportportal_file_scope` 时，`skills_list/skill_view` 先命中 profile 分支返回 Investigation profile 结果，`support-case-files` 不在列且加载返回 `skill_profile_denied`；原 `verify_merged_overlay.py` 只分别测两种 scope。
- **修复**：`supportportal_file_context.file_skill_visible(name)`（仅文件技能且仅 work 相位文件 scope 为真）；`skills_list` profile 分支在可见时追加文件技能条目；`skill_view` 与 `_skill_view_with_bump` 对文件技能豁免 profile 短路与 `skill_visible` 门；新增共享谓词 `_supportportal_listing_visible=(skill_visible or file_skill_visible) and skill_allowed` 用于 `_find_all_skills` 三处返回过滤（首次尝试 td53 漏了这三处：view 通过但 list 仍缺，已被 td54 取代）。
- **验证**：离线矩阵 `verify_b1.py` B1_VERIFY_OK（profile-only 不变 / 组合 work 列出并可加载 / 组合 persona 隐藏 / file-only 不变；stub `_find_all_skills` 改为经同一谓词，堵住离线绕过真实过滤的缺口）；bundle 五门禁 + case files/attachments 套件 62/0（隔离 PG）；**td54（镜像 `7df5f3a3`）容器内以真实 `skills_tool`+真实 EFS 技能目录复核**：组合 list 含 `support-case-files`、view 返回真实 SKILL.md 内容，profile-only 与无 scope 负向不变。
- **live 复验 14151**：Map `14151-engineer-4.txt`、list×2/read×5/search×2、标记命中、调查证据显式引用 "support-case-files skill"、审批快照、Zendesk 评论 54779760112148 + 附件 54779773301652、工单 open。C3/C5/C6 重新验证通过。
- **保持开放的边界**：Slack→n8n 人类上传传输跳未演练（bot token 事件按设计被过滤）；Automation 路径反向调用无 live 证据；C7 Zac 文件 waiting-for-evidence。
