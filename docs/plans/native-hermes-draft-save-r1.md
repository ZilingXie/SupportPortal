# native Hermes Investigation 草稿保存修复

修订：R1。任务：p2-200，Function：engineer-investigation-reply；关联 p2-191、p2-199。

## 基线、授权与边界

实施基线：`4ecea0431f1b84490bab4bf51220faeef815e6b2`，根 main 干净。
工作区：`.worktrees/native-hermes-draft-save-r1`，分支：`codex/native-hermes-draft-save-r1`。
用户明确实施 R1；本任务按正常验证/finalize/Preproduction 流程推进，无新增独立验收门禁。
沿用 ECS Preproduction 技术验收及本地官方栈豁免。禁止重试/重放 #14031、真实 Slack/Zendesk
业务写入、客户发送、Production、n8n 变更。没有新模型、队列、表或迁移。

## 当前合同

| 合同 | 实现与证据 |
| --- | --- |
| C1 字段识别 | 同一逐位置匹配器服务 collected reask 与 unauthorized request；只排除明确技术标签中的 name。个人姓名、混合请求继续拒绝。技术标签不能授权个人姓名。 |
| C2 完整长度 | greeting 和既有服务端引用投影后校验完整文本，1200 通过、1201 拒绝；超长不调用最终 guardrail、不截断、不保存。普通 Automation 保留原校验输入和策略。 |
| C3 一次改写 | 仅 investigation_reply 的 Persona 使用 turn.work_result.draft_save；case→binding→turn 行锁内最多两次候选校验。拒绝在事务返回后才转 HTTP 422。相同已保存文本回读，不同文本冲突；未知结果要求回读。 |
| C4 真实错误 | 安全诊断持久化 turn/run/attempt/code/actual_chars/max_chars/rewrite flags。无草稿时使用 last_error；从未保存才 missing_draft；run 失败/中断和未知 guardrail 不伪装成校验失败。没有真实接管。 |
| C5 审批身份 | source_turn_id、附件、case revision 保留；成功仍 awaiting_approval，审批前 delivery 为零。重复 Prepare 的既有冲突合同不变。 |

次数约束指后端候选校验次数，不保证模型内部生成次数。native Persona 输入给出完整预算，
收到 rewrite_allowed=true 只在当前 run 改写一次；不启动第二模型或新调查回合。
工具回调先于 start-run 回执时，真实 run ID 暂不可知；回执登记使用原子 JSON 更新补齐
已有诊断及候选状态，最终日志使用回读后的真实 ID，不编造 ID、不重置额度。

## 可复现验证

项目 Python：根 `.venv/bin/python`。PostgreSQL：独立本地测试库
`postgresql:///p2200_native_draft_test`，每个用例建立 UUID schema 并清理；不连接业务库。

```bash
RUN_POSTGRES_INTEGRATION=1 TICKET_DB_DSN=postgresql:///p2200_native_draft_test \
  /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q \
  backend/tests/test_native_hermes_draft_save.py \
  backend/tests/test_hermes_customer_reply_basis.py \
  backend/tests/test_hermes_zendesk_agent_tools.py \
  backend/tests/test_native_hermes_fixed_task_review.py \
  backend/tests/test_hermes_zendesk_agent.py \
  backend/tests/test_hermes_zendesk_agent_postgres.py \
  backend/tests/test_automation_persona.py
/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python \
  scripts/verify_native_hermes_draft_reverse.py
```

真实路径：Account RouteWorker 固定 Investigation→Work 审核停车→Prepare→同一个 Persona
run→真实 HTTP 保存 API→真实 PostgreSQL→awaiting_approval。仅模型、最终外部 guardrail、
Slack/Zendesk 边界 mock。覆盖并发同内容单草稿、两次拒绝持久化后重建 PG store、第三次
合规仍阻断、未知 guardrail、保存 commit 后回执丢失及回读、过期 revision、source/附件、
1200/1201（greeting/换行/中文/引用）、通知最终原因。

反向脚本仅在子进程内载入基线 validator 或去掉 cap，不重写任何工作区文件；分别要求
unauthorized_field_request 与第三次返回 200 的预期回归断言失败。项目 venv 缺依赖不算检出。

扩展检查发现 8 项 main 预存失败（同环境未修改基线已复现），未放宽或跳过原断言：
API 两项旧用例缺少服务端 Work basis；followup handoff 六项受既有副作用/生命周期合同影响。
完整节点与实际结果见 JSON 证据。native guardrail 旧用例同步为本次明确合同：拒绝即零草稿，
具体错误持久化及通知；不是删除安全断言。

## 发布和运行边界

先完成代码及记录，再正式 pipeline 构建并部署 Preproduction。保留部署时 live Prompt、
hermes engine、agent/persona、typed workflow、Enablement、知识治理开关，全部门禁仍启用。
同一环境有在途发布则让行，不竞争 ECS/Terraform。release evidence 必须 complete，并独立
对账公网 provenance、服务 revision/digest、Prompt 与配置。

`native-hermes-draft-image-probe.py` 在发布 worker image 内运行两条合成场景，使用内存 store
和 mock 外部边界；业务写入为零。只证明部署代码入口，不代替 PostgreSQL/真实模型/真实
Slack/Zendesk业务验收。正式 pipeline complete 后再运行，保留日志和 task 证据。

真实业务样本仍未授权：不恢复 #14031，不点击 Approve & send。若未来授权恢复，需要先核实
安全入口，不能改库、清空错误或重置 Prepare 绑定。涉及新迁移、route/审批语义、runtime 协议
或门禁变更时暂停对应动作。当前结果统一维护在
[native-hermes-draft-save-r1-evidence.json](native-hermes-draft-save-r1-evidence.json)。
