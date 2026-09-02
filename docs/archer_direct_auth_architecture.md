# Archer 直连鉴权架构（可复用参考）

- 首次实现：PR #1023（2026-09-02，任务 p2-134）；redirect host 白名单加固：2026-09-02
- 生产状态：r20260902-46370fa 起上线（ECS worker:17），Mac 与 ECS Fargate 双侧端到端探针通过
- 代码锚点：`backend/services/archer_direct_client.py`（凭证与传输）、`backend/services/enablement_archer_executor.py`（结果归一与脱敏）
- 部署/轮换 runbook：`docs/deploy_automation_ecs_release.md`「Enablement Archer Worker 发布门禁」章节

## 1. 适用场景

当一个**内网 Web 系统**满足以下条件时，可复用本架构让 ECS 容器无头调用它的 HTTP API：

1. 系统本身不提供 service account / API key，唯一登录方式是浏览器 SSO；
2. SSO 会话以 cookie 形式存在（可从浏览器人工导出一次）；
3. 系统的短命会话凭证（JWT/cookie）可以通过**纯 HTTP 重定向链**用长命 SSO 会话换取，无 JS、无人工；
4. 容器侧只需要调用 API，不需要渲染页面。

不满足条件 3（换凭证必须跑 JS 或人工点确认）时，此架构不可用，需另找 service credential。

## 2. 认证模型（全部经真实请求实测）

| 层级 | 凭证 | 形态 | 实测寿命 | 存放 |
|---|---|---|---|---|
| 根凭证 | `oauth2-token` + `oauth2-token.sig` | `oauth.agoralab.co` 域的 SSO 会话 cookie 对（Laravel 签名会话） | 未确定（周级；到期即降级，见 §6） | SSM SecureString `/supportportal/production/archer-oauth-cookie`，任务启动时注入 env `ARCHER_OAUTH_COOKIE` |
| 短命凭证 | `archer_token_jwt_202003` | Archer 域 cookie，值为 JWT（claims: email/displayName/id/exp） | 24 小时 | 仅进程内存缓存，永不落盘 |

实测要点：调 Archer API 只需短命 JWT 一个 cookie；换 JWT 只需根凭证 cookie 对；换发链全程无 JS、无人工。

## 3. 架构

### 3.1 凭证分发链（每一跳的信任边界）

```
人的浏览器（登录 SSO）
  → 人工导出 oauth2-token / .sig（唯一人工环节）
  → aws ssm put-parameter（SecureString/KMS；值经 $(cat file) 传入，不进 shell history）
  → ECS task definition secrets[]（只存参数 ARN，无明文）
  → 任务启动时 execution role 拉取并注入容器 env
  → 进程读 os.environ（与 zendesk/RAGFlow 等既有 secret 同机制）
```

### 3.2 无头续期链（`obtain_archer_jwt`）

```
① GET oauth.agoralab.co/oauth/authorize?...redirect_uri=<callback>/handleSSO
   Cookie: 根凭证对（只发给 SSO 这一台）；禁跟随重定向
   ← 302 Location: https://<callback-host>/api/v1/handleSSO?code=<一次性code>
② GET 该 Location（不带任何 cookie —— code 一次性且不与秘密同传）
   ← 302 + Set-Cookie: archer_token_jwt_202003=<新 JWT>
③ 校验 JWT exp（已过期拒收）；缓存 {value, exp}；距 exp < 300s 自动重走 ①-③
```

安全要点：根凭证从始至终只发给 oauth.agoralab.co；Location 校验 = 路径包含（`/api/v1/handleSSO` + `code=`）+ 绝对 URL + **回调 host 白名单**（`ARCHER_SSO_CALLBACK_HOST`，与 authorize 常量里的 redirect_uri host 绑定，其余 host 一律 `ArcherCredentialError`）；容器重启即重走链，无状态恢复问题。

### 3.3 调用层（`DirectArcherClient.call`）

- 每请求带 JWT cookie + 仿浏览器头；超时可 env 调（`ARCHER_HTTP_TIMEOUT_SECONDS`，默认 60s）。
- **401 → 强制续期一次 → 重试一次**；二次 401 才报错（有界，不循环）。
- 纯读形状适配：HTTP 400「项目不存在」→ `{"data": null}`；`{elements, totalSize}` 信封 → 裸列表。
- TLS 默认校验对端证书，无降级。

## 4. 设计决策记录（复用时对照）

| 决策 | 理由 | 边界/放弃条件 |
|---|---|---|
| 短命 JWT 不落盘、只存内存 | 重启即免费重换，凭证面最小 | 无 |
| JWT 不做签名校验 | 仅作回发 bearer，本地不基于 claims 做授权决策；伪造自愈于 401 强制续期 | 一旦本地开始依赖 claims 做决策（按 email 授权、按 exp 长周期调度）必须验签 |
| 续期链 redirect 只做包含性校验 + host 白名单 | 纵深防御：该跳无秘密可泄，但一行 host 校验关闭"被引去任意地址"整类风险 | 若 authorize 的 redirect_uri 换域名，需同步改 `ARCHER_SSO_CALLBACK_HOST` |
| 不做启动期凭证强校验 | 避免凭证问题放大为整个 worker 起不来；凭证错只影响单 handler | 凭证健康靠发布门禁一次性探针 + 运行期 escalation 信号（事后） |
| 所有失败统一 `enable_failed`（fail-closed） | 无静默成功路径；客户不可见错误细节 | SSO 会话失效的主动告警暂缺（可选：每日廉价 302 探测） |
| 报错文案白名单化（只含 HTTP 状态码） | 防登录页 HTML/响应体带出信息 | 无 |
| detail 三重脱敏（App ID/secret 键值/控制字符+截断） | 进入 Zendesk 备注/日志前统一收敛 | 无 |
| 根凭证用个人 SSO 会话 | 当前唯一可行（无 service credential） | 合规灰区：审计归属=该自然人；长期应换 service credential |

## 5. 失效契约（fail-closed）

任何一环失败——SSO 会话过期（authorize 返回 200 登录页而非 302）、redirect 异常、无 Set-Cookie、JWT 已过期、API 4xx/5xx/非 JSON/超时/网络错——统一收敛为 `enable_failed`：escalate（Zendesk 私有备注含脱敏原因 + 路由回原 queue）+ 兜底内部邮件照发 + execution 终态 `human_review`。无自动重试（外部副作用不可盲重试）。**escalation 事件即凭证健康信号**：SSO 失效后每张相关工单都会走人工路径且备注写明 session expired，不会静默丢功能。

## 6. 复用 Checklist（套到下一个内网系统）

1. **阶段 0 探针（只读，Mac）**：浏览器登录后用 curl 携带会话 cookie 请求授权端点，确认 302 → 回调 → Set-Cookie 全链可脚本化；确认最小 cookie 集；确认短命凭证寿命与 exp 字段。
2. **传输层**：仿 `archer_direct_client.py` 写 client——NoRedirect opener、302+Location 校验（含 host 白名单）、exp 拒收过期、内存缓存+提前续期、401 单次续期重试、报错只含状态码。
3. **分发层**：SSM SecureString + task definition `secrets[]` + env 注入（复用 execution role 既有 `parameter/supportportal/production/*` 通配，零 IAM 改动）。
4. **归一层**：仿 executor 做 outcome 归一（成功/输入非法/查无/失败）+ 脱敏 + fail-closed 升级链。
5. **测试**：全 mock `_request`，零真实外呼；覆盖续期链每个失败分支、401 重试边界、错误文案不含凭证。
6. **发布门禁**：部署时先跑一次性探针 task（SSM→续期→只读 GET）再 rollout；验收顺序=非法输入（零网络）→ 查无（只读）→ 有效（写入）。
7. **运维守则**：凭证轮换=浏览器重登→更新 SSM→force new deployment；记录根凭证寿命未知这一事实与恢复步骤。

## 7. 已知限制

- SSO 会话（根凭证）寿命未实测，到期只能事后从 escalation 信号发现；恢复需人工重登+更新 SSM。
- 根凭证为个人会话，所有自动写入在 Archer 的审计归属为该自然人（Track 1 争取 service credential 中）。
- AWS 出口 IP 未被 WAF 拦属运营事实而非契约；WAF 策略变化即降级为 `enable_failed` 契约路径。
