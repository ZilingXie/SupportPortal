# Investigation 文件 Hermes metadata readiness

计划名称：`investigation-case-files-slack-ingress-metadata-v1` / Hermes metadata readiness。修订：**v4（2026-10-22，方案已独立验收；实现完成，待完整代码独立验收）**。

目标：以受控 EFS 单 Skill 发布补齐 `HERMES-METADATA-01`，使 live bridge 的 description schema 与实际 skill_view 返回内容属于同一已审查版本。本轮不执行真实文件测试。

## 授权、角色与门禁

- 人类在部署失败及恢复独立验收后回复“确认”，选择“受控更新 EFS 中单个 support-case-files Skill，保留备份与回滚；先修订方案并独立验收，再执行”。这解除单文件 EFS 内容发布的范围阻断，不是方案/代码/部署验收通过。
- 继续同一工作区 `.worktrees/hermes-case-files-metadata-readiness-v1`、分支 `codex/hermes-case-files-metadata-readiness-v1` 和 Draft [PR #1548](https://github.com/ZilingXie/SupportPortal/pull/1548)。root main 不编辑。
- 顺序：**v4 方案独立验收 → 实现工具与验证 → 完整 HEAD 独立代码验收 → 正式合并 → Preproduction Hermes/EFS 发布 → 独立部署/readiness 验收**。方案通过后实施无需再次询问相同授权；代码通过前不得合并或修改云端。独立验收回交线程 `01a1349a-c8e9-7183-829d-6b158e735eb5`。
- 允许的云端副作用仅为：Hermes 镜像 rollout/必要回滚；专用 Preproduction EFS 内一个 Skill 文件的原子替换、同文件备份和任务发布记录/临时文件。保留原 mode/UID/GID。可以使用现有 ECS Exec，不增加 AWS/IAM/网络资源或凭据。
- 不改 loader、gateway、context helper、plugin 注册、其他 Skill、profile/config/toolset、API/Route/Worker、n8n、schema 或 Production。不创建 case/session/run、不调用业务文件工具、不上传 Slack、不发送 Hermes/Zendesk/Zac 业务消息、不 replay。
- **当前未解决的人类选择：无。** 后续若需要修改 loader/其他 profile 文件、权限/资源或真实业务边界，才暂停并直接问人类。备份与目标文件的操作在上述已批准范围内。

## 已验证基线与历史

- main `11ef78565fdef3d55b8efbb20580c1fc475c36ff`；当前修订前证据 HEAD `3686d8da673d4b9e4c2c62befd3072f5affa4bf7`，两工作区干净。代码 #1547 已独立验收于 `d6ed18b9b03157adb23f5f7f39bfe5393445972e` 并合入 main。
- v3 独立结论：**部署未通过；恢复通过**。TD55 镜像/schema/hash 正确，但实际 skill_view 内容缺 short_description。随后恢复 TD54，五容器 HEALTHY、ALB healthy、API174/Route169/Worker172 不变；Terraform after-rollback exit 0，无 apply。失败和恢复证据保留在 [机器记录](investigation-case-files-hermes-metadata-readiness-evidence.json)，不改写为通过。
- 2026-10-22 14:15 北京时间新鲜只读回读：account `891612554546`，region `us-east-1`，IAM `Zac`；Hermes TD54，running=1、pending=0、单一 COMPLETED deployment，无并行 rollout。
- 当前 Hermes digest `sha256:7df5f3a325465ee07847ecd5c97874f0abf3e06fd1d3306c0fd5e7686f5e44b0`。待恢复的新镜像已 immutable 发布：`sha256:a4ba1d1f2ddd50994ef6c1bceaafc89246690a51c2bdb9af8f47efd493ba304d`，原 release `hermes-20261022-11ef7856-metadata-v1`，镜像源码仍为 `11ef7856…`。
- 实际 Skill 路径为 `/opt/data/skills/support-case-files/SKILL.md`，resolved path 相同；EFS FS `fs-0ded23be6872d82da`、AP `fsap-068e530f8cd61c854`，AP root `/supportportal-preproduction-hermes-home`，非 Production 根。现有文件非 symlink、UID/GID 10000、mode 0644、1937 bytes，父目录可写，目标与 /opt/data 同设备。
- 原文件 SHA-256：`7379499fc8412b4a52cd5ad82a31f74ae580ae02c8577ef4eb2a7c16910ebc02`。新文件为仓库 `deployment/hermes-case-files/skills/support-case-files/SKILL.md`，SHA-256：`bce133eb503042e75f67b7d512877fa4a2efccc2af75766c2fe15a00b330154a`。不重新编辑这份已审查 Skill。
- main 后端已支持 description 对 short_description 的 Unicode casefold 字面子串过滤；不新增 alias，不变更算法。v3 实际 probe 只检查 bundled Skill 和 load success，未检查返回内容，是本次补齐对象。

## 设计与真实入口

1. 修复 [probe_metadata.py](../../deployment/hermes-case-files/probe_metadata.py)：保留实际 register/schema/check_fn、skills_list/skill_view、profile/context 入口；记录实际返回的 `_source_path`、resolved path 和原始 content SHA-256。在最终 `--require-description` 模式同时检查 schema、实际返回内容含 short_description、返回内容与发布文件逐字节相等、路径为批准的 EFS 文件。只存在 bundled 文件或 list/view success 不足。
2. 增加一个窄范围发布 CLI `deployment/hermes-case-files/publish_skill.py`，生产入口固定目标路径和单 Skill 名称；默认 check-only，apply/rollback 必须显式动作、release ID、前后预期 hash。候选输入只允许审查后的打包字节，不从 URL/模型参数选路径，不读取凭据。
3. 不调用现有 `stage_profile.py` 的整份 config/Skill 复制：本轮不改变 config/toolset。复用现有 ECS Exec 通道、Python 文件 I/O、同文件系统原子 rename；不增加新发布服务。
4. 新 v4 发布记录引用现有 immutable 镜像与 Skill bytes，并分别记录 `image_source_commit=11ef7856…`、已合并的 v4 工具源码 SHA、原/目标 TD、EFS AP、文件前后 hash。**不将旧镜像伪装成 v4 工具提交构建的新镜像，不改写旧 release/manifest/checkpoint。** 因镜像内两资产未变，不为文档/探针另构建无变化镜像。
5. 每次发布创建唯一任务记录目录 `/opt/data/.supportportal-skill-releases/support-case-files/<release-id>/`；仅保存该 Skill 的原始 bytes、stat 与不含凭据的操作记录。目录不可复用为另一输入。发布用同一目标父目录内临时文件；备份目录不在 skills 搜索树内。

代表性路径：已有实际 Skill loader（EFS 旧文件）→ 检出与镜像的差异 → 备份/单文件发布 → 同一个真实 skill_view 返回新 bytes 与 EFS source path → 组合 scope/profile 正向、无 scope/persona 反向。只绑定进程内无后端权限的合成 scope，不建立真实 session/run。

## 文件发布与恢复合同

- check-only 严格只读，不创建锁、目录、备份、临时文件或操作记录；锁与备份仅在显式 apply/rollback 后创建。
- 变更前确认服务/任务/AP/路径、无其他发布归属；要求固定目标为普通文件、无 symlink/路径逃逸，原 hash/stat 符合基线，候选 bytes hash 为审查值。保留相邻内容与 config 的预检 hash；不递归发布 profile。
- 发布器使用目标对应的固定锁文件 `/opt/data/.supportportal-skill-releases/support-case-files/publish.lock` 做非阻塞独占 flock；同一发布器的重入被拒。**该锁只约束遵循协议的发布者，不冒称对任意外部写者实现文件 CAS。** 执行者拥有本次发布窗口，ECS/AP/目标 hash 发生异动即停止；不得覆盖已知外来变更。
- 写入备份与候选、flush/fsync、核对 bytes/hash/mode/UID/GID，写 prepared 记录；候选放在目标同一父目录，提交前再次读原 hash/stat；不一致就 abort。使用 os.replace 作为内容提交点，随后目录 fsync 与 readback；不原地截断/覆盖目标，不让读者观察半文件。
- 单文件操作记录状态固定为 prepared、committed、outcome_unknown、rolled_back、ownership_conflict；写记录也用临时文件+rename，但文件提交与记录不是一个跨文件事务，必须用目标 bytes 回读消解中间态。相同 release ID 重入先核对备份、输入与当前文件：prepared/outcome_unknown 且当前=新 hash 时补确认 committed，不再次替换；prepared/outcome_unknown 且当前=原 hash 时允许原输入继续 apply；committed 且当前=新 hash 时只读确认；rolled_back 不重新激活，返回已回滚，后续发布使用新的 release ID。无记录且当前=原 hash 才能创建本次备份；无记录但当前=新 hash，不反向猜测其归属或伪造备份。其他状态/当前值矛盾或第三种值、备份不一致一律 fail closed。
- rename 或 ECS Exec 返回不确定时记 outcome_unknown，先从新的只读请求核对目标和操作记录，不盲目重跑；旧值表示未完成，新值表示已提交待验证，第三种值表示归属冲突。不能靠 wrapper exit0 认定成功。
- rollback 也受同一锁、身份与 hash 检查约束：只有当前=本次新 hash 才用保留的备份经同目录临时文件原子恢复；当前=旧 hash 时只读确认；其他值停止并问人类。恢复 bytes/stat/hash 后再确认服务恢复。保留所有备份和失败记录，不清理历史发布。

## 合同矩阵

| 合同 | v4 可观察通过条件 | 层次与失败结果 |
| --- | --- | --- |
| HMR-C1 | bridge 实际 handler → HTTP API 的 description/大小写/无命中保留；禁额外参数 | 代码；沿用未受影响证据，受影响入口定向回归 |
| HMR-C2 | 四旧过滤器及其与 description 的交集保留 | 代码；不改匹配算法 |
| HMR-C3 | 无 scope、persona、错误 session/伪 capability/其他 case 被真实入口拒绝 | 代码 + 只读镜像 probe；最终直接 skill_view 负向也必须拒绝 |
| HMR-C4 | candidate image + 真实 loader + 模拟 EFS 旧 Skill 时在 actual_served_skill 检查失败；发布后原输入通过；局部失败及重复发布可恢复 | 本地真实文件/loader/CLI；不以字符串检查替代入口 |
| HMR-C5 | live schema、image/sidecar digest、实际 EFS source/resolved path、返回内容/文件/仓库三者 hash 一致；profile隔离、Gateway health/toolsets、ALB/五容器/Terraform通过；其他服务/config不变 | 部署；任何一项失败，恢复 EFS旧 bytes + TD54并读回；未知不宣称成功 |
| HMR-C6 | 最终独立 deployment/readiness 通过前 ready_for_real_file_tests=false；全轮不造 case/session/run、不调业务工具 | 独立门禁；旧 R1/业务/Automation live/Zac边界保留 |

状态矩阵：baseline（旧镜像+旧 Skill）→ image_staged（新镜像健康，仅镜像阶段通过，不是 readiness 成功）→ skill_prepared → skill_committed_pending_verification → verified_pending_independent_acceptance。失败转恢复；结果未知先读回；外来值转 ownership_conflict，不覆盖。只有独立部署验收可关闭 HERMES-METADATA-01。

## 实现、验证与发布步骤

1. **方案验收已通过。** 指定线程已独立验收 v4 方案，绑定 `3128b65db88fbc57f9f951165dca2671553f49d5`。允许本地实现；完整代码独立验收通过前不合并 #1548、不改云端。
2. 方案通过后在本分支实现 publisher 与 strengthened probe，增加 `backend/tests/test_hermes_case_files_skill_publish.py`。使用真实临时文件系统、实际 CLI/loader；只 mock 外部 AWS 通道。测试旧 snapshot 被实际返回内容门拒绝、新 snapshot 通过；错误路径/hash、备份失败、锁竞争、rename前失败原文件完整、rename后结果未知通过原 operation读回、重复apply不重复替换、受控rollback恢复原 bytes/stat；第三方内容冲突不得覆盖。
3. 用已有 candidate immutable image 禁网运行真实 Skill loader，将临时旧 Skill fixture 放到其正常 skills路径（模拟 EFS），必须在 actual_served_skill 检查失败；以本地 publisher 更新同一 fixture后重跑原 probe通过。故障只在本地，线上不刻意损坏文件。保留 fresh run ID、镜像 digest、stdout/stderr/exitcode 与断言，缺 marker 一律失败。
4. 定向验证命令：`PYTHON_DOTENV_DISABLED=1 TICKET_DB_DSN=postgresql://test/test .venv/bin/python -m pytest -q backend/tests/test_hermes_case_files_skill_publish.py backend/tests/test_hermes_case_files_metadata_bridge.py backend/tests/test_investigation_case_files.py backend/tests/test_hermes_case_files_v2_merge_scripts.py backend/tests/test_investigation_case_files_deploy.py`；另执行实际 candidate 容器故障/恢复探针、Skill quick validation、py_compile、JSON、Overview、feature list、diff check。外部 DSN skips 保留、不算数据库保证证明。新代码完整 HEAD 独立验收，先前 #1547 的无影响证据继续保留。
5. 代码通过后按正式 finalizer 合并。执行前再次冻结 AWS身份/region、Hermes旧TD与健康、AP根/文件hash/stat/真实加载路径、无并发 rollout/发布归属、其他三服务与 config、Terraform zero-drift。基线改变先评估整合影响；不借旧证据覆盖新输入。
6. 只更新 Hermes：复用批准的 a4ba1d1f… immutable digest，clone当前TD且仅改image；不假定仍可直接使用TD55，记录实际新revision。保持现有部署策略、roles/secrets/network/mount/其他容器配置。等所有容器健康并回读schema/image/assets；这是 image_staged 阶段，真实Skill就绪尚未通过。失败则只回滚镜像，EFS尚未动。
7. 在新任务上再次 check-only、取得备份、按发布合同 apply 一个 EFS Skill。完整执行强化probe（实际返回内容/source/hash、list+direct-view隔离）、Gateway仅GET health/toolsets、ALB/容器健康、窄范围启动错误检查、其他三角色/config不变和Terraform zero-drift。缓存不得靠猜测：直接新请求核对实际返回；若长驻Gateway仍旧，不通过业务消息刷新，不擅自改变loader；停下评估现有Hermes重启是否足够，涉及范围外行为再问人类。
8. 任一发布后门禁失败，按文件合同恢复旧Skill，再恢复原TD54并读回（若执行时旧TD已变，使用预检实际旧TD，不伪称TD54）。如果Skill恢复发生外来冲突，不覆盖，停止后续自动镜像切换并报告人类；不扩大权限“修复”。未知提交先读回。保留 release/backup/失败 evidence，不修改其他服务。
9. 发布结果、确切 source/image/TD/EFS前后/served hashes、所有检查及失败恢复写回同一机器证据；代码PR合并后另建 Draft部署证据PR，主动交回指定线程最终验收。执行器不自行开放真实文件测试。最终通过后才由后续获准计划使用测试case，仍不自动执行。

## 证据与当前停止点

- 既有代码：#1547 / reviewed d6ed18b9… / merged 11ef7856…；32 passed、17 skipped、2 subtests。其证据不等同 v4 新发布器或实际Skill检查通过。
- 历史部署：v3 TD55 failed；TD54 recovery passed（独立审查 #1548 / 3686d8da…）；所有旧 artifact `.deployments/hermes-metadata-readiness-v1` 保留。
- v4 方案：指定线程独立验收通过，绑定 `3128b65db88fbc57f9f951165dca2671553f49d5`；本地实现已完成，**完整代码独立验收待执行，EFS未修改、未再次部署，ready_for_real_file_tests=false**。当前 full HEAD 以 #1548 API 回读及交接为准。
- 新实现：[单 Skill publisher](../../deployment/hermes-case-files/publish_skill.py)、实际返回内容 probe、[真实 loader 容器回归及复现说明](../../deployment/hermes-case-files/README.metadata.md)。固定 Preproduction ECS 任务/路径/审查 hashes，默认只读；真实临时文件、进程锁竞争、备份/属性/未知提交/回滚意图与第三方冲突通过定向测试。
- 定向 suite：55 passed、17 skipped、4 个既有 FastAPI 弃用警告、2 subtests passed。外部数据库等 skips 不作为线上/数据库保证证明。
- 最终禁网容器回归：`.deployments/hermes-metadata-readiness-v4/container-attempt3/`，exit0，唯一 `METADATA_EFS_LOCAL_PASSED` marker。固定 image ID `faec191f…`，真实 loader 在旧 snapshot、错误 hash、错误 path 的指定门禁失败；单文件发布后原 probe 通过，重复 apply 不替换 inode，rollback 恢复旧 bytes 后再次拒绝旧 metadata。工具源 hashes 与完整结果纳入机器记录。
- 上述仅证明本地代码/真实镜像 loader；live EFS AP、发布持久性、Gateway/服务健康和最终 readiness 仍需合并后云端回读及独立验收。镜像 source `11ef7856…` 与 v4 工具源码独立记录，不改旧 release。
- 单一机器记录：[readiness evidence](investigation-case-files-hermes-metadata-readiness-evidence.json)。旧 R1、启用文件 C1–C7 未覆盖路径、人类上传、Automation live 反向、Zac真实协议、完整知识业务门禁继续保留。
