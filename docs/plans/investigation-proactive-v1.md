# Investigation 主动调查与能力收敛 v1

修订：R1。状态：已完成（代码验收与 Preproduction 技术发布通过；真实业务验收未执行）。

当前待复审版本：SupportPortal main `8c151238f83e866fdb28680d5f8e476c858b1187`；Hermes deploy main `17284d3e697260080a28dc9ca9b35455b5f13ad0`。Preproduction release `r20261014-8c15123`，evidence=`complete`。

代码验收已通过并合入 main；Preproduction 已按正式 pipeline 发布并完成技术门禁。真实 Slack/Zendesk/Argus/Zac 请求、客户消息与 Production 仍未执行。

## 合同

- C1：固定 Investigation work run 由服务端生成 `supportportal-investigation-v1` profile，隔离 `supportportal-investigation`、`hermes-zac-engineering-handoff`，只有可信工程师 close 条件才开放 `support-close-case`；Automation/Cron 不共享该 profile。
- C2：频道无 callId/时间时由后端注入当前回合前 30 天窗口，标注 initial search range；Argus 空结果、鉴权/超时、截断不混淆，最多三次搜索。
- C3：`support_zac_consult` 后端持有凭据，按同一 opaque user/idempotency key 保存请求；`response.completed` 才完成，未知只用原键恢复一次，过期结果只归档。
- C4：现有固定 route、多轮上下文、人工审阅、客户草稿和 Slack 限制不变。

## 证据

SupportPortal 专项 7 passed；组合定向回归 24 passed、55 deselected；PG 专项未执行（无 DSN），记录为 waiting-for-evidence。Hermes fresh source 验证命令为 `python3 build/investigation-proactive-v1/verify.py <fresh-source>`，输出 `patch_apply=passed profile=passed compile=passed`，并得到 `ARGUS_RESULTS [True, True, True, 'argus_channel_search_budget_exhausted']`。验收基线 source HEAD 为 `a49f64b47d4b547c584e5c776f1f6180ff696dd6`。

## 发布与验证

- Release：`r20261014-8c15123`，源提交 `8c151238f83e866fdb28680d5f8e476c858b1187`，Prompt `pr-88298d4e0a3f`，Schema `automation-ecs-016`。
- Evidence：`.deployments/ecs-pipeline-r20261014-8c15123/preproduction-deploy/evidence.json`，状态 `complete`。正式门禁包含 CodeBuild、Preproduction preflight/deploy、Prompt、Route/Worker/API、Heartbeat、Collector、Activation、CloudWatch、Provider probe、Terraform zero drift、ECR、EC2 backup、Target Health 与 runtime digest 对账。timings 显示总时长 1336s，超过 900s SLO，因两次失败 preflight 后恢复；正式门禁最终通过，作为非阻断观察记录。
- 只读回读：`/health/live`、`/health/release`、`/health/ready` 均为 `ok`，发布身份与 manifest/evidence 一致。`/health/ready` 返回的历史 heartbeat/provenance 条目未作为本 release 失败。

## 未覆盖

未调用真实 Argus/Zac，未发送客户或 Slack 消息；PG 未执行（无 DSN），真实业务验证未授权。独立验收线程 `codex://threads/01a11961-20c6-7630-9e82-a8dafc51117e` 已返回通过：代码与 Preproduction 技术发布均通过；真实业务与 PG 专项仍未执行。
