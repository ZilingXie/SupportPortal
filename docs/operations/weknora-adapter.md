# WeKnora 适配层（知识/记忆受控写入）

源码核对日期：2026-09-30（任务 p2-181，分支 `codex/weknora-adapter`）。本页描述配置契约与探针流程；线上是否启用以目标环境 SSM/env 只读回读为准。

## 定位

SupportPortal 是 WeKnora 知识与记忆的**唯一写入方**：Hermes Summary/Review 产出结构化候选（`knowledge`/`memory`/`skill`），由 SupportPortal 的 WeKnora Adapter 校验后写入、回读并记录版本。Review Agent 不直接写 WeKnora；Skill 候选自消费桥（p2-181/p2-182）起作为**仅人工复核**的 promotion 记录进入本管线（`candidate_type='skill'`，decision 只允许 `no_change`/`human_review`，适配器对其零读写），原始终终决策保留在 `candidate_payload` 供技能维护者审计。旧 Hermes `/v1/promotions` 投递与 n8n 直写 Tencent Memory 的迁移是后续独立步骤，不在本页范围。

## 数据与状态

- 任务表 `support_weknora_promotions`（ticket-storage schema，版本随 `_TICKET_SCHEMA_VERSION` 演进；候选级幂等与 skill 枚举自 v16 起同时生效）：lineage 全字段（case/ticket/investigation/Summary、Review session 与 run、Slack thread）+ 状态机 `queued/active/accepted/failed/outcome_unknown/human_review/invalidated`。
- 幂等是**候选级**：`(source_type, source_id, source_version, candidate_type, content_hash)` 唯一约束；promotion_id 由同一组字段加 content_hash 确定性生成。同一事件重放只产生一行任务；同一来源版本的不同候选（不同内容）各自成行、不会互相吞掉。消费桥的 `source_type='hermes_knowledge_review'`、`source_id='<review_id>:<candidate_id>'`、`source_version=<report content_hash>`（按报告内容寻址）。
- 入队来源：(1) 旧 close 候选路径（`hermes_case_promotion`，仅在知识管线未激活时运行——知识管线激活后该默认候选路径让位）；(2) 消费桥——Review 任务完成事务内原子入队（`hermes_knowledge_workflow.build_weknora_promotions_from_review_report`），worker 先跑知识排水再跑 promotion 排水，同一轮即可衔接。
- 失效：`reopen_hermes_case` 在同一事务将 `queued/active` 任务置为 `invalidated`。
- `outcome_unknown` 不自动重试（禁止盲写），人工核对后用 `requeue_weknora_promotion` 复位；复位后 worker 重跑时 Adapter **先对已记录的 weknora_object_id 做回读核对**（内容/版本一致→直接 accepted；对象不存在→按原幂等键正常写入；不一致→human_review），不会对已知对象二次创建。
- 回读证明写入：Adapter 的回读必须核对**对象一致、内容一致、版本一致**（receipt 与回读版本都存在时），任何不一致按 `readback_failed` 记 `outcome_unknown`，不算 accepted。
- 版本保护：`replace`/`merge` 必须携带 Review 依据的 `base_version`（缺失=候选不完整，拒绝写入）；`supplement` 若带 `base_version` 同样参与比对。目标当前版本与依据不一致一律 human_review，绝不覆盖。

## 配置键（全部 fail-closed，缺省即不启用）

| 键 | 用途 |
| --- | --- |
| `WEKNORA_BASE_URL` / `WEKNORA_API_TOKEN` | 服务地址与凭证 |
| `WEKNORA_API_CONTRACT_JSON` | 探针固定的 API 契约 JSON：每个操作 `{method,path}`（path 支持 `{placeholder}` 动态对象路径，占位符取请求体字段并 URL 编码），及可选字段名映射（`object_id_key`、`version_key`、`results_key`、`content_key`、`idempotency_key_field`、`base_version_field`、`identity_field`、`tenant_field`）。object id 归一在配置键之后自动回退 `object_id`→`id`（官方记忆 API 返回 `id`） |
| `WEKNORA_AUTH_HEADER_NAME` / `WEKNORA_AUTH_SCHEME` | 认证头名与 scheme（默认 `Authorization` / `Bearer`） |
| `WEKNORA_KNOWLEDGE_BASE_ID` | 知识库 ID（知识操作必需） |
| `WEKNORA_MEMORY_IDENTITY` | 共享 Hermes 服务身份（记忆操作必需；未固定时 memory 候选转 human_review，不写全局记忆） |
| `WEKNORA_TENANT_ID` / `WEKNORA_TIMEOUT_SECONDS` | 租户注入与超时（默认 30s） |
| `WEKNORA_PROMOTION_ENABLED` | 总门禁（`1`），要求契约已固定；未启用时 close 流程零变化 |

## Contract probe（启用前置条件）

计划要求 API 版本、认证方式、字段名以 Preproduction 实测固定，不按文档猜测：

```bash
WEKNORA_BASE_URL=... WEKNORA_API_TOKEN=... \
WEKNORA_API_CONTRACT_JSON='{"health":{"method":"GET","path":"/health"}}' \
python3 scripts/weknora/probe_weknora_contract.py
```

脚本默认只发只读发现请求（`/`、`/v3/api-docs`、`/swagger.json` 等，可用 `WEKNORA_PROBE_EXTRA_PATHS` 扩充），输出 JSON 报告；据实回复填写 `WEKNORA_API_CONTRACT_JSON` 后再次运行确认 `health=ok`。**health 通过不证明写入能力**——启用写入前必须补目标版本的写能力证据：

```bash
WEKNORA_PROBE_WRITE_CAPABILITIES=1 WEKNORA_KNOWLEDGE_BASE_ID=<一次性探针专用知识库> \
WEKNORA_BASE_URL=... WEKNORA_API_TOKEN=... WEKNORA_API_CONTRACT_JSON='<已固定契约>' \
python3 scripts/weknora/probe_weknora_contract.py
```

`write_capabilities` 段对同一探针对象依次验证 create→回读内容一致→同幂等键重建返回同对象（服务端去重）→条件更新→**过期 base_version 被拒**（若服务端接受过期版本会显式 warning：版本保护仅客户端侧）。全部 verified 才满足"写入、幂等、版本冲突能力成立"的启用证据；探针证据（去凭证）记录到 `docs/project/tasks/p2-182.json`，之后才可开 `WEKNORA_PROMOTION_ENABLED`。

## 相关源码

- `backend/services/weknora_client.py` — HTTP client、错误分类（auth/not_found/conflict/timeout/transport/invalid_response/not_configured）
- `backend/services/weknora_promotion_adapter.py` — 决策映射与失败模型
- `backend/repositories/weknora_promotion_repository.py` — 任务表/租约/失效/复位
- `backend/worker.py` `_drain_weknora_promotions` — 领取与完成（租约 120s）
