# Investigation 主动调查与能力收敛 v1

修订：R1。状态：实现完成，等待独立复审。

当前待复审版本：SupportPortal `c7c03c4119c5a2e2a3449c22dd6ca5facdeed151`；Hermes deploy `fcad33db9ee60cf1050be1687c764c81bff57f2b`。

代码 HEAD、Hermes deploy HEAD 和证据文件必须在独立验收前保持可复现；不合并、不部署、不发送真实 Slack/Zendesk/Argus/Zac 请求。

## 合同

- C1：固定 Investigation work run 由服务端生成 `supportportal-investigation-v1` profile，隔离 `supportportal-investigation`、`hermes-zac-engineering-handoff`，只有可信工程师 close 条件才开放 `support-close-case`；Automation/Cron 不共享该 profile。
- C2：频道无 callId/时间时由后端注入当前回合前 30 天窗口，标注 initial search range；Argus 空结果、鉴权/超时、截断不混淆，最多三次搜索。
- C3：`support_zac_consult` 后端持有凭据，按同一 opaque user/idempotency key 保存请求；`response.completed` 才完成，未知只用原键恢复一次，过期结果只归档。
- C4：现有固定 route、多轮上下文、人工审阅、客户草稿和 Slack 限制不变。

## 证据

SupportPortal 专项 7 passed；组合定向回归 24 passed、55 deselected；PG 专项未执行（无 DSN），记录为 waiting-for-evidence。Hermes fresh source 验证命令为 `python3 build/investigation-proactive-v1/verify.py <fresh-source>`，输出 `patch_apply=passed profile=passed compile=passed`，并得到 `ARGUS_RESULTS [True, True, True, 'argus_channel_search_budget_exhausted']`。验收基线 source HEAD 为 `a49f64b47d4b547c584e5c776f1f6180ff696dd6`。

## 未覆盖

未调用真实 Argus/Zac，未发送客户或 Slack 消息，未部署 Preproduction；PG 未执行，真实业务验证未授权，等待独立复审线程。
