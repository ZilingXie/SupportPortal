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
