# Investigation 主动调查与能力收敛 v1

修订：R1。状态：实现完成，等待独立验收。

代码 HEAD、Hermes deploy HEAD 和证据文件必须在独立验收前保持可复现；不合并、不部署、不发送真实 Slack/Zendesk/Argus/Zac 请求。

## 合同

- C1：固定 Investigation work run 由服务端生成 `supportportal-investigation-v1` profile，隔离 `supportportal-investigation`、`hermes-zac-engineering-handoff`，只有可信工程师 close 条件才开放 `support-close-case`；Automation/Cron 不共享该 profile。
- C2：频道无 callId/时间时由后端注入当前回合前 30 天窗口，标注 initial search range；Argus 空结果、鉴权/超时、截断不混淆，最多三次搜索。
- C3：`support_zac_consult` 后端持有凭据，按同一 opaque user/idempotency key 保存请求；`response.completed` 才完成，未知只用原键恢复一次，过期结果只归档。
- C4：现有固定 route、多轮上下文、人工审阅、客户草稿和 Slack 限制不变。

## 证据

专项 5 passed；固定 Investigation 回归 4 passed、PG 4 skipped（无 DSN）；Hermes 相关 focused run 159 passed，另有一项既存 follow-up handoff 测试基线失败，未归因本改动。Hermes :45 新鲜源码提取后的 overlay `patch_apply/profile/compile` 均 passed。

## 未覆盖

未调用真实 Argus/Zac，未发送客户或 Slack 消息，未部署 Preproduction，等待独立验收线程复审。
