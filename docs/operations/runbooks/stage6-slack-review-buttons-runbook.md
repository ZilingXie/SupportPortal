# stage6-slack-review-buttons 执行手册

分支 `codex/stage6-slack-review-buttons`（HEAD 5dd31b41），目标：知识审查 Slack 通知改简洁格式 + Approve/Reject 按钮，7 条在库 human_review 通知重发为可点击形态。

前置阻塞（2026-10-23 记录）：本机 VPN（MacPacketTunnel 127.0.0.1:1082）上游断开，GitHub/AWS/n8n MCP 全部不可达。网络恢复后按序执行。

## 1. 推送 + PR

```
git push -u origin codex/stage6-slack-review-buttons
# Draft PR → main（squash-only）
```

验证口径：`backend/tests/test_knowledge_slack_buttons.py` 13 项 + stage4/5/hermes_knowledge/engineer_slack 共 151 绿；
`test_automation_ecs_api.py` 2 个存量失败（reply_basis_unavailable，main 同败，他线问题，与本次无关）。

## 2. 合并 + Preproduction 部署

- `scripts/workflow/finalize_task_to_main.sh`（或 PR 合并后从根 main 触发发布管线）。
- 发布注意：knowledge_stage 配置若变会改 checkpoint identity；本次是纯代码变更，flags 按最近一次发布的 checkpoint identity 复刻（见 ecs-pipeline-env-and-resume-gotchas 记忆）。
- 部署后运行验证：dashboard knowledge review API + worker 日志无异常。

## 3. n8n 交互流变更（`[slack]Handle Action|Preprod`）

现状（active 快照 FKv8vtZBQk6tH4Gt）：Webhook → Parse Slack Action → Target Team And Channel → Has Thread TS（根消息无 thread_ts，knowledge 按钮点击在此被丢弃）→ …。

变更（通过 n8n MCP update_workflow，先刷新快照再改，改后回读+`validate_workflow_snapshots.py`）：

1. **Parse Slack Action**（code 节点）输出增加：
   `action_value_raw: String(action.value || '').trim()`（按钮原始 value=promotion_id）。
2. 新 IF 节点 **Knowledge Action**：
   `{{ ['knowledge_approve','knowledge_reject'].includes($json.action) }}`
   接在 `Target Team And Channel`(true) 之后；false → 原 `Has Thread TS`。
3. 新 HTTP 节点 **Send Knowledge Action To SupportPortal**（复用 V3 的 URL
   `https://supportcenter.stellarix.space/automation/preproduction/api/integrations/slack/hermes-cases/actions`
   与 httpHeaderAuth 凭据），body：
   ```json
   {"interaction_id":..., "action":..., "value": action_value_raw, "slack_user_id":...}
   ```
4. 新 HTTP 节点 **Consume Knowledge Action**（POST response_url，replace_original=true，
   原始 blocks + context 行；文本取 SP 响应 `response_text`，缺省
   `"Decision recorded by <@slack_user_id>."`）。

SP 端已实现（本分支）：knowledge 动作分派在 environment/ticket 校验之前；operator 服务端验证；superseded 拒 409；重复点击返回 already=ok。

顺序约束：先部署 SP（步骤 2）再发布 n8n 变更；n8n 先行也无害（旧 SP 对未知 action 422，按钮尚未出现）。

## 4. 7 条通知重发（新格式+按钮）

delivered 是状态机终态（C3），重发=显式维护动作。SQL（Preproduction，TICKET_DB_SCHEMA=supportportal_preproduction）：

```sql
UPDATE weknora_promotions
SET slack_review_status='queued',
    slack_review_owner_token=NULL,
    slack_review_claimed_at=now() - interval '1 hour',
    updated_at=now()
WHERE status='human_review'
  AND slack_review_status='delivered'
  AND slack_thread_ts IS NOT NULL AND slack_thread_ts <> '';
-- 影响行数须 = 7；否则停下核对
```

worker Phase 0 崩溃恢复自动认领过期 queued → 线程内重发（新格式+按钮，event_id 带 :resend 后缀避开 client_msg_id 去重）。

前置：promotion ID 清单在重发前先 SELECT 确认（7 条、全部 human_review、各自 thread_ts 非空）。

## 5. 用户点击验证

- Reject 路径：promotion → rejected，deliveries 全部 retired/invalidated，零 WeKnora 写入，audit 行保留 operator email。
- Approve 路径：promotion → queued，resolution={action:new, content, title}，进入完整外部写合同（幂等键+版本保护），WeKnora 写入后 readiness 回读。
- 原消息被 replace_original 更新为已决策状态（按钮消失防重复点击）。

## 6. 收口

- p2-197 登记（evidence：test+deployment+document）。
- 若 UI/行为登记需要：同 Task 追加，不新建 Function。
- CodeSight 刷新：`npx codesight --wiki`（根 main）。
