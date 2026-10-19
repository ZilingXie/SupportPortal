# Hermes Native Enablement 首轮缺 App ID 回复自然化 v1

## 基线与门禁

- 基线：`main@f666a8cb51109d5e54ea539abb336a4131d024c9`（计划记录的 `5bfbd86a` 已由 main 的无关 Team Agent 文案提交向前推进；本任务在当前干净 main 上实施）。
- 范围：native Hermes Enablement 首轮 `missing_fields=[app_id]` 的 Persona 表达。
- 停止点：独立代码验收前不 finalize、合并、发布 Prompt、部署或重放 14121。

## 合同

- C1：服务端 basis 仍决定缺失字段；只有 App ID 缺失时正文只能请求 App ID。
- C2：正文自然承接客户的开通请求，使用 basis 提供的功能名称，并说明收到 App ID 后继续处理。
- C3：不得声称已提交、已启用、审核完成或添加未经 basis 授权的时间承诺。
- C4：服务端 greeting、翻译、重复 greeting/signature/internal implementation 门禁保持不变。
- C5：tool → basis → Persona → draft → delivery 的 ownership 与幂等行为不变。

## 实施与验证

只调整 `hermes-reply-contract` 的 Enablement missing-App-ID 指令，版本升至 v7；不修改 basis、validator、字段提取、路由、delivery 或模型档位。新增真实 Persona phase assembly 断言，并保留 native regression 的 tool/basis/draft/delivery 测试。离线模型评估使用脱敏样本；若环境不可用，记录 `waiting-for-evidence`，不以静态测试数替代自然度结论。

## 业务边界

不触碰 Prompt catalog、Preproduction/Production、Zendesk、Slack、relay 或历史工单 14121。完成实现和验证后创建 Draft PR，交独立线程验收。
