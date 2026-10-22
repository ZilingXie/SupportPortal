# Investigation Slack 文件管理 R2 运维

核验日期：2026-10-14。文件启用链路仍仅有本地代码/隔离数据库/补丁构建检查；关闭状态 Preproduction 已部署并通过关闭状态及无附件 synthetic 范围的独立验收。[合同](../plans/investigation-case-files-r2.md)。

2026-10-20 启用前证据轮：[计划与证据](../plans/investigation-case-files-r2-preprod-file-enable.md)。当前线上 `r20261019-21c6eed` 维持全关闭（三入口 live 422、Map 0、Hermes 无文件 Skill/toolset）。两个启用前置事实：①`source-baseline.json` 对本地 agent-infra（bc9a0f7d）仍四文件 MATCH，但 live 插件已含 p2-203 的 `supportportal_investigation`/`support_zac_consult`——overlay 的插件目录 COPY 会覆盖 live 差异，构建前必须先把 bundle 插件输入重定到 p2-203 世代源并重新验收；②启用轮还需让行并发发布车辆、收口陈旧非终态行（991000183-1/13872/13904）、并随显式发布参数恢复 `HERMES_CASE_WORKFLOW_MODE`。

2026-10-14 关闭状态发布门禁修复：[发布计划与证据](../plans/investigation-case-files-r2-preprod-closed.md)。配置保留检查只将缺失文件开关与显式 `0` 视为等价；涉及 `1` 的状态改变、模型、凭据引用及其他配置改变仍拒绝。真实 renderer/CLI 回归与旧逻辑逆向验证见该计划；补丁验收不等于 schema 016 或线上发布已通过。

## 启用顺序

独立代码验收通过后，使用正式 immutable release/Preproduction pipeline；保留 schema、Prompt、健康检查与回滚门禁。

1. 正式 schema bootstrap 升级 coordination revision 至 `automation-ecs-016`，创建 `investigation_case_files` 唯一约束和 binding `zac_session_id`。使用既有迁移身份/grants；从实际 API/worker 身份检查 Map 读写权限，不用只读 DSN假成功。
2. 发布 SupportPortal API/worker，先保持 `INVESTIGATION_CASE_FILES_ENABLED=0`。renderer 默认新增值为0；已有值保留。正式 shell 显式参数为 `--investigation-case-files-enabled` / `--investigation-case-files-disabled`，互斥且记录于 checkpoint 身份。启用仅允许 Preproduction。
3. 回读云端当前 Hermes base image digest、gateway/plugin source、实际 overlay及有效 profile。与 [source baseline](../../deployment/hermes-case-files/source-baseline.json)逐项对照；该文件仅记录本地构建输入，不是 live 证据。保留现有 close/tool能力，不能用 canonical plugin 覆盖 live差异。
4. 在隔离构建树执行 `deployment/hermes-case-files/prepare.py <tree>`。先实际 `git apply --check`，drift失败不得强行覆盖；需要针对新输入复核并重新验收受影响合同。使用 `Dockerfile.overlay` 和不可变 `HERMES_BASE_IMAGE`构建新镜像，记录新digest。
5. 从实际 profile 回读快照，执行 `stage_profile.py <snapshot> <isolated-output> --effective-toolsets <readback tools...>`。通过既有 Hermes profile init/drop 发布 config 与 Skill；新 toolset为 `supportportal_files`。不能凭镜像包含Skill就宣称profile已安装。
6. Hermes 镜像与profile均核验后，在 PP启用开关。HERMES_AGENT_API_TOKEN复用为私有 scope签名，API/worker应一致；不新增secret。有 pending/running未知提交时不要切换开关/签名key，否则重试请求体或scope校验可能变化；先收口/核对回读。回滚关闭时文件API显式拒绝。
7. 使用获准测试case与合成文件，从实际 Slack @Hermes、云端工具、Skill列举/直接加载、审批→Zendesk worker入口验证C1-C7，记录确切版本、namespace、turn/request/session、Map、Slack ID和Zendesk回执。没有获准case或Zac真实协议时对应项保持waiting-for-evidence。

现有 n8n 可信事件字段足够，本轮不修改。若后续证据要求修改，先保存发布版与 divergent draft快照、validate，更新后独立回读，禁止保存execution/pin data/凭据。

## 故障与回滚

pending不是成功；available必须验证实际thread share。unavailable记录删除/权限/来源错误。预留Slack file ID后结果未知，先回读该ID；不可清账本或盲目重传。公开回复未知沿用Zendesk audits精确回读。

50 MiB/file、工程师消息最多10文件；文本按行读取/搜索、ZIP指定文本成员最大2MiB。图片/PDF/加密日志只提供下载，缺专用工具明确不支持。下载/worker使用受控临时文件；客户转运和发布兼容bytes接口仍可能占用单文件内存，不声称所有上传全程流式。

关闭功能并恢复上一版运行产物/profile；保留新增表、Map、交付账本与已上传Slack文件，不降schema或删除记录。新功能测试不得替代p2-192既有发布与真实业务门禁，Production单独授权。

源码：[Map](../../backend/services/investigation_case_file_store.py)、[管理层](../../backend/services/investigation_case_files.py)、[工具 API](../../backend/automation_ecs_api.py)、[运行编排](../../backend/services/automation_hermes_agent.py)、[云端补丁](../../deployment/hermes-case-files/gateway.patch)、[发布脚本](../../deployment/deploy_automation_ecs_release.sh)、[部署入口测试](../../backend/tests/test_investigation_case_files_deploy.py)。

2026-10-14 关闭状态运行证据：release `r20261014-7800b8ee-files-closed`，schema 016，API/Worker 显式 `0`、Route 缺省 `0`；Hermes 镜像/profile 保留且无文件 Skill/toolset。synthetic 14057 无附件审批投递、intake 幂等和 solved→closed 同步均有实际回读。[证据](../plans/investigation-case-files-r2-preprod-closed-evidence.json)。本轮未安装 overlay、未修改 n8n、未启用文件或 Zac；原 R1 门禁保留。

2026-10-20 v2 启用轮：Preproduction 文件功能已开启（release `r20261020-0bd735e-case-files-enable-r2`，开关=1，Hermes 含 `supportportal_files` toolset 与 `support-case-files` skill）。代表性闭环与负向证据见 [v2 计划与证据](../plans/investigation-case-files-r2-preprod-file-enable.md)。要点：①overlay 采用三方合并（live gateway × td46 插件 × R2 文件能力），脚本入库 `deployment/hermes-case-files/v2-merge/`，重建时须以现行 live 镜像与含 p2-203 能力的插件源为底；②case_task investigation 方向已随 PR #1524 提升到 binding.direction（老镜像 era 的 pending 工单不回填）；③工单在 reply turn 完成后可能被自动 solve，审批投递须在此前完成或以反馈文本保持 open。

2026-10-20 B1 修复：Hermes td54（镜像 `7df5f3a3`）起，Investigation profile 与文件 scope 同时绑定时 `support-case-files` 可列出可加载（`_supportportal_listing_visible` 共享谓词 + `file_skill_visible`）；重建 overlay 用 `deployment/hermes-case-files/v2-merge/merge_b1_fix.py`，验证用 `verify_b1.py`（含双 scope 组合矩阵）。

2026-10-21 R2-V2-B1-REPRO 修复轮（独立复验未通过后）：v2-merge 四脚本（merge_b1_fix / merge_overlay_v2 / verify_b1 / verify_merged_overlay）去除全部机器路径，输入/输出改 CLI 参数（`CF_*`/`SUPPORTPORTAL_ROOT` 环境变量回退，SupportPortal 根默认取脚本所在检出，缺参 fail-closed）；重建程序与命令固化于 [v2-merge/README](../../deployment/hermes-case-files/v2-merge/README.md)。可复现性证据：以保留的现行 live 输入重建，重建树与 td54 构建树及部署镜像 `7df5f3a3` 内 12 个 overlay 文件逐字节一致，本地重建镜像与 td54 镜像 ID/rootfs 层逐位一致且容器内功能探针通过；干净目录冒烟测试（合成锚点输入、子进程、清空环境）入库 `test_hermes_case_files_v2_merge_scripts.py`。v2 三方合并链同样参数化并复跑 `MERGE_OK`+`VERIFY_OK`（其现行输出含 B1 的 `file_skill_visible` 助手，与 td49 时代树在该文件上的差异为预期）。

2026-10-22 Hermes metadata readiness：[v3计划](../plans/investigation-case-files-hermes-metadata-readiness.md)。main已有description后端合同而live td54为旧bridge/Skill时，以已回读的live immutable digest为base，通过 `Dockerfile.overlay-metadata` 只覆盖两个文件，保留gateway/context/profile。代码验收后才发布Hermes，部署前核对TD无并发变化、其他容器/secret/EFS/network配置不变；`probe_metadata.py --require-description`须以真实镜像/线上Python运行并核对JSON marker和hash，不能用Exec wrapper exit0代替成功。无session/run/backend工具调用，真实传输仍需独立验收。
