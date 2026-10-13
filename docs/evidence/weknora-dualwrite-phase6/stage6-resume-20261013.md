# 阶段 6 发布续接（2026-10-13）

代码来源固定为 `c9722399daa347a7a24e0f15fafb8fddaedebe39`，只到 Preproduction。用户已明确允许接管并保留 64 条历史 `human_review`，不清理、不重放；要求使用 Terraform 1.9.8 继续校验。

## 第 1 步补充冻结

完整脱敏回读：`stage6-resume-baseline-20261013.json`。API/Worker/Route 仍为 `146/146/144`，Hermes 为 `45`；均唯一 PRIMARY/COMPLETED、running=1、pending=0，当前服务未指向后来注册但未运行的 `147/147/145`。本次未采用 latest task-definition family 作为运行事实。

- Terraform：本机现存 1.9.8 Linux ARM64 容器遇到 AWS provider 启动握手失败，不计为漂移结论。使用既有 zacbot Linux AMD64 1.9.8，在任务独立目录校验与固定源码逐文件一致的配置；backend/tfvars 使用既有 Preproduction 配置。`init -reconfigure -input=false -lockfile=readonly` exit 0；`plan -detailed-exitcode -input=false -lock-timeout=60s -no-color` exit 0，明确 `No changes`。未 apply，未跳过锁，未修改 lockfile。
- 远端 Terraform 目录：`/home/ubuntu/SupportPortal-tf/stage6-baseline-c9722399-192713`。原始日志仅保存在权限受限目录，不提交资源正文或凭据。
- 历史队列：source intake accepted=132；standalone Summary completed=36、Review completed=36，无 pending/running、无有效 lease。两表各保留 36 个过期 lease 字段，本次不重置。旧 typed Summary failed=1，typed Review=0。
- promotion human_review=64，无有效 lease；delivery=0。64 条 Slack review status 均 null；Slack claim、message、human decision、外部 object ID 均为 0。历史行指纹已保存，后续验证保留不变，不把历史结果计为新阶段验证。
- Prompt 只读回读：active=`pr-78386473bd08`、build_ref=`4ecea0431f1b84490bab4bf51220faeef815e6b2`，42 个 Prompt，合同 validate loaded，scheduled=0。本轮保留现有 Prompt，不改内容。
- n8n MCP 新回读：CSD `6511c2e9-2d68-44f0-b3f6-00e9060bbc36`、Solved `d4c50a10-a379-485f-a5cd-72e19ec31345`、Forward `4ed2c0c3-65ef-42d4-a1bd-442379638e0e`；三者 active 与 draft 相同，12/8/13 nodes。线上快照刷新与发布后回读仍需在步骤 3–4完成，历史 candidate 尚未视作线上已发布版本。

## 第 2 步停止在来源门禁

新 release：`r20261013-c972239-stage6-alloff`。显式 engine=hermes、typed=disabled、agent/persona 保留、Enablement=manual；master=0、七开关=0（包括 `KNOWLEDGE_AI_EXECUTION_ENABLED=0`）、allowlist 为空。未请求 schema bootstrap、Production 或样本重放。

CodeBuild #194：`supportportal-automation-release:8478fad5-cdfe-439b-bba3-40749dc4a470`。正式 pipeline checkpoint 与发布材料位于根区忽略目录 `.deployments/ecs-pipeline-r20261013-c972239-stage6-alloff/`。构建及 Manifest/Publish Record 校验通过，codebuild attempt 为 passed（124.905 秒）。三角色新 digest 已生成，不复用旧 tag：

- API：`sha256:fe28b7ae5c6258a046cb594d6007a6a8cd7870c90f879aed6cfba3a9f264df85`
- Route：`sha256:45cce9cba7fa2cf11be5c04ba607a2c180cf45d14e3c8d04c211c900abdf764a`
- Worker：`sha256:5a69d5413db340ee6b958e125e9d3fc918f61737d54d446203bf595391ad20c0`

构建期间远端 main 推进为 `44b6d1e664d2fe99f1cd210ea054cdfdf511ccda`（PR #1493）。新 main 修改 `backend/automation_ecs_worker.py` 与对应测试：当收到 solved/closed 状态且本地 Account Case 不存在时，返回可审计 ignored；active 状态仍 fail closed。这是实际 Worker 行为变更，不是文档推进。

正式 pipeline 在来源校验时退出 1，原错误为：`origin/main contains post-release runtime changes; build a new release: backend/automation_ecs_worker.py, backend/tests/test_automation_ecs_worker.py`。未进入 preproduction_preflight/deploy，没有注册本次 task definition、没有 update service 或 Prompt activation，故无本次 ECS 回滚动作。三服务重新回读仍为 `146/146/144`、各 running=1/pending=0、唯一 PRIMARY/COMPLETED；Hermes 未改变。

用户固定 `c9722399`，因此本轮不自行换用 `44b6d1e6`，也不绕过来源门禁。需用户决定是否更新发布来源；若允许，先对主线新增行为进行定向核验，再重新冻结并使用另一个 immutable release/checkpoint。旧 alloff 构建材料与失败记录保留，不覆盖、不改称已部署。

停止后独立按三个 immutable tag 查询 ECR：三角色 digest 均与 manifest 一致。再次只读计算 64 条历史 promotion 的 ID/status/content_hash/source_version/updated_at 指纹，与部署前逐值一致；未清理、未重放。详见 `stage6-alloff-stop-20261013.json`。checkpoint/timings/manifest/publish-record 的脱敏原文归档于 `alloff-c9722399-build/`。

## n8n 实时快照证据

三个 fresh published 快照与独立 scoped manifest 保存于 `docs/integrations/n8n/workflows/captures/stage6-20261013-predeploy/`，完整 source/workflow version 一致；各 active/draft 相同，没有 divergent draft。该目录 validator 通过：3 published / 0 drafts / 2 redactions。原历史 active/draft/candidate 均未覆盖，candidate 未登记成已发布。远端未修改、未发布、未执行。

## 后续边界

历史 64 条不得清理、重放或用于假冒本轮两样本证据。仅在完成全关闭发布、快照刷新和 n8n 对照后继续层 1。层 2 必须核对目标样本当前 source_version/hash 与既有 completed task 的关系，不能把旧 completed 记录宣称为新 pending，也不能更改真实 source_version 来规避幂等。

全量 allowlist、WeKnora publish、hybrid search 仍须第 10 步观察结束后重新确认；本记录不释放该人工停点。
