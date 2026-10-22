# Investigation 文件 Hermes metadata readiness

计划名称：`investigation-case-files-slack-ingress-metadata-v1` / Hermes metadata readiness。修订：v3（2026-10-22）。本轮目标是解除 `HERMES-METADATA-01` 的运行产物缺口；代码与部署分别独立验收，真实文件传输不在本轮范围。

## 基线与授权

- SupportPortal main `7a1c52231e4bea3184393a065c4f93a111cea840`；相对已发布 `a08be077` 只有 p2-209 登记/Overview 文档变化。metadata/bot 代码 #1540/#1544 已独立验收并合并。
- 上轮部署/迁移/配置/n8n 回读已由指定线程独立通过：[证据 PR #1545](https://github.com/ZilingXie/SupportPortal/pull/1545)，HEAD `a37757f062458ba9c3f6bd7928d99fd7314ba2f1`。不将其“通过”扩大到真实文件就绪。
- 新阶段工作区 `.worktrees/hermes-case-files-metadata-readiness-v1`，分支 `codex/hermes-case-files-metadata-readiness-v1`；独立 Draft PR，待代码验收后才可合并/部署，最终回交 `01a1349a-c8e9-7183-829d-6b158e735eb5`。
- 仅允许更新 Preproduction Hermes bridge/Skill 的镜像产物。无新 schema、AWS/IAM/网络/队列资源或凭据，不改 SupportPortal API/Route/Worker、n8n、Production；不创建 case、上传文件、发送 Hermes/Zendesk/Zac 业务消息或 replay。

## 已核实缺口与实现

main 早已支持 list 的 `description` 入参及其对 `short_description` 的 Unicode casefold 子串过滤；线上 td54 的 bridge/Skill 仍旧。新代码只补 schema 的参数解释、Skill 来源/检索合同、真实入口回归和可复现只读探针。`short_description` 是返回 metadata，不能作为另一个模型参数传入；不新增与后端不一致的 alias。

2026-10-22 新鲜只读回读确认 td54/image `sha256:7df5f3a325465ee07847ecd5c97874f0abf3e06fd1d3306c0fd5e7686f5e44b0`：list schema 仅四旧参数、Skill 无 short_description。**context helper hash 实际与 main 相同**（`fecab9f2…`），不为旧报告中的疑点覆盖它。工作 scope + Investigation profile 组合下文件 Skill 可列出/加载，profile-only/persona 拒绝。

最小 overlay 使用 [Dockerfile.overlay-metadata](../../deployment/hermes-case-files/Dockerfile.overlay-metadata)，以该 live immutable digest 为父镜像，只 COPY bridge 与 Skill 两文件；保留 gateway、插件注册、B1 context、技能门禁、Investigation profiles 及全部其他资产。不重跑旧三方合并，不更改 EFS profile。

## 合同与验证

| 合同 | 实际入口与通过条件 | 证据层 |
| --- | --- | --- |
| HMR-C1 | `register()` 提供 description string、禁额外参数；handler → HTTP tool API 对受控 metadata 命中/不命中，TLS/tls/STRASSE 大小写通过 | 隔离测试 |
| HMR-C2 | source/extension/purpose/introduced_turn_id 单项及与 description 的交集保留 | 隔离测试 |
| HMR-C3 | 无 scope、persona、错误 session 在 HTTP 前拒绝；伪签名与其他 case Map ID 经真实 API 拒绝，拒绝前无文件下载 | 隔离测试 |
| HMR-C4 | 两文件 overlay；候选容器禁网执行真实 Skill list/view/profile；缺 description 的旧 schema 故障在桥接层被检出 | 容器/反向探针 |
| HMR-C5 | 仅 Hermes 服务更新；新 TD/image digest、旧新 asset hashes、live list schema、Skill/profile 可见性、健康；其他三角色及配置保持原基线 | 部署后只读回读，尚待执行 |
| HMR-C6 | 主线程独立验收后才判断可开始真实文件测试；本轮零 case/run/业务工具/传输样本 | 独立门禁 |

真实入口测试：[test_hermes_case_files_metadata_bridge.py](../../backend/tests/test_hermes_case_files_metadata_bridge.py)。仅 mock bridge HTTP 网络边界，转发到实际 FastAPI handler 和存储过滤，不 stub 过滤/签名/case 校验。无需变更数据库合同；此轮未重做 PG 保证，已有外部边界门控 skips 如实披露。

可复现探针：[probe_metadata.py](../../deployment/hermes-case-files/probe_metadata.py)。使用部署镜像内真实 bridge `register()`、skills_list/skill_view 和 profile/context；只在独立进程绑定无后端权限的合成 scope，不建立 session/run，不调用业务文件工具，不渲染 Skill 内 shell。线上不创建/改写 EFS Skill 或 profile。ECS Exec wrapper 退出0不作成功证据，必须解析 JSON marker、断言和真实 hashes。

## 顺序与回滚

1. 定向测试、旧 schema 故障、禁网候选容器探针；冻结源码和两目标文件 hashes，Draft PR 交独立代码验收。
2. 通过后按仓库 finalize 流程合并；核对合入文件与验收版本一致，从合入的 source 复核/发布全新 immutable Hermes image。保留旧 td54/image 和所有证据。
3. 重新核对目标身份/account/region、无并行 Hermes rollout、当前 TD 未改变。clone 现行 Preproduction Hermes TD，仅改 hermes container image 为新 digest，保持其他容器、环境/secret引用、EFS/network/roles/healthcheck 全等；注册新 revision 并更新该一个服务。
4. 等服务收敛、所有容器健康；按 HMR-C5 回读。探针或健康失败时停止后续动作，恢复先前 Hermes TD54 并回读收敛；不改 SupportPortal/n8n/Production，不通过业务样本补证。
5. 新旧版本、测试/回读、阻断与就绪判断提交同一 PR/证据记录，主动交回指定线程做独立部署验收。代码验收/执行者探针不自行释放最终 readiness。

## 当前状态与边界

部署尚未执行。单一机器证据见 [readiness evidence](investigation-case-files-hermes-metadata-readiness-evidence.json)。旧 R1、启用文件业务 C1-C7 中未覆盖路径、人类上传、Automation live 反向、Zac 协议和完整知识业务门禁保留；本轮 readiness 不证明真实 Slack/Zendesk 文件交付成功或技术内容正确。
