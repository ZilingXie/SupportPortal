# Investigation Slack 文件管理 R2 运维

核验日期：2026-10-14。文件启用链路仍仅有本地代码/隔离数据库/补丁构建检查；关闭状态 Preproduction 已部署并完成执行验证、待独立部署验收。[合同](../plans/investigation-case-files-r2.md)。

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
