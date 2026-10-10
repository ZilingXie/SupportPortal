# WeKnora 并行双写与人工治理链（阶段二）

- 计划名称：WeKnora 并行双写与人工治理链（阶段二）
- 修订：r1（2026-10-10 登记入仓，正文为用户批准的计划原文）
- 任务登记：p2-194（function：weknora-dualwrite-governance，module：rag-knowledge）
- 工作分支：codex/weknora-dualwrite-phase2（基线 main d958c66f）
- 阶段 0 冻结基线：[docs/evidence/weknora-dualwrite-phase2/stage0-baseline.md](../evidence/weknora-dualwrite-phase2/stage0-baseline.md)
- 阶段状态：阶段 0 已通过独立验收；阶段 1 首轮验收未通过（五项阻断：互锁单向/目标开关旁路/AM 定向缺版本校验/终态未要求双成功/intake 未叠主开关），修复轮已全部修复并补回归（276 passed/0 failed 含 PG 集成），任务改号 p2-194（并行线程占用 p2-193），第二轮验收未通过（三阻断：批准绕过双目标收口/改文后哈希未更新/静态 schema 镜像未同步），修复轮 2 已全部修复并补回归（408 passed/0 failed 含 PG 集成与镜像守卫），停在 Draft PR #1453 待第三轮独立验收；阶段 2-6 未实施

## 目标

在 Preproduction 建立以下链路：

```text
n8n 获取来源快照
  → SupportPortal 接收并持久化
  → Summary
  → AI Review
  → 自动双写或进入 Slack 人工 Review
  → SupportPortal 分别写入 AgentMemory 和 WeKnora
```

阶段二期间：

- Hermes 继续使用 AgentMemory；
- AgentMemory 和 WeKnora 并行共存；
- SupportPortal 是唯一双写编排者；
- n8n 只负责来源快照；
- 不迁移历史 AgentMemory 内容；
- 不退休 AgentMemory；
- 不切换 Hermes 主知识库；
- 不触碰 Production；
- 不影响当前 Preproduction 测试。

## 一、阶段 0：基线和合同冻结

先只读核对并记录：

1. 两条 n8n 工作流的 active/draft 版本：

   - CSD：`GgDxPEWtW7ltT5BW`
   - Solved：`MM3Z3T469Eru3Q1I`

2. 当前 Hermes Preproduction：

   - task definition；
   - AgentMemory 配置；
   - WeKnora 配置；
   - 现有治理开关；
   - 当前知识读取路径。

3. 现有 SupportPortal 来源接口：

   - `POST /automation/preproduction/v1/knowledge/sources`
   - `GET /automation/preproduction/v1/knowledge/sources/{task_id}`

   现有实现位于 [backend/automation_ecs_api.py](../../backend/automation_ecs_api.py)。

4. 当前人工 Review 能力：

   - promotion 查询接口；
   - promotion decision 接口；
   - Slack 线程通知和命令解析；
   - 当前 `HERMES_KNOWLEDGE_WORKFLOW_ENABLED` 状态。

5. 只读收敛 p2-182、p2-183、p2-186 的历史登记，明确哪些是历史实现、哪些是当前有效合同。

阶段 0 的输出是一个冻结的基线记录，不做运行态变更。

## 二、阶段 1：SupportPortal 双写编排

### 1. 候选记录

建立候选级记录，至少保存：

- `source_type`
- `source_id`
- `source_version`
- 来源快照哈希
- `candidate_id`
- `candidate_type`
- Summary session/run
- Review session/run
- AI 决策
- AI 理由
- 完整候选正文
- 内容哈希
- 重复判断证据
- 目标对象和 `base_version`
- Slack channel/thread
- 当前候选状态
- 操作人和人工决定
- 创建、更新时间

候选级唯一键：

```text
source_type + source_id + source_version + candidate_type + content_hash
```

重复投递只能复用原候选，不能生成第二条写入任务。

### 2. 两个独立 delivery

AgentMemory 和 WeKnora 不能共用一个成功状态。每个候选分别记录：

- `target = agent_memory | weknora`
- `queued`
- `active`
- `accepted`
- `failed`
- `outcome_unknown`
- `invalidated`
- 外部对象 ID；
- 外部版本；
- 幂等键；
- 请求回执；
- 回读结果；
- 错误分类；
- attempt 次数。

一个目标成功、另一个目标失败时，只补失败目标。

### 3. 自动双写条件

第一版只有同时满足以下条件才自动双写：

- AI Review 决策为 `new`；
- 质量证据完整；
- 没有重复对象；
- AgentMemory 读取成功；
- WeKnora 读取成功；
- 来源版本仍然有效；
- 候选正文和哈希校验通过。

自动写入仍然必须经过 SupportPortal 的双目标 delivery worker。

### 4. 必须人工 Review 的情况

以下候选不自动写入：

- `merge`
- `replace`
- `supplement`
- 重复判断不确定；
- 检索失败；
- AI 证据不足；
- 目标版本发生变化；
- 目标对象无法回读；
- 双写一侧失败；
- 写入超时且结果未知；
- 候选内容或来源版本冲突。

明确完全重复的候选可以记录 `no_change`，不产生写入。

## 三、阶段 2：n8n 改为 source-only

CSD 和 Solved 两条链保留：

- 工单/Jira 获取；
- 评论分页；
- 完整性校验；
- 来源字段整理；
- 原始快照投递；
- SupportPortal 回执读取；
- 失败告警和有限重试。

n8n 删除或断开：

- AI 质量筛选；
- 本地重复判断；
- AgentMemory 直写；
- WeKnora 直写；
- Wiki create；
- raw/write；
- ingest；
- 旧知识库主链。

快照合同至少包含：

- schema version；
- 来源类型；
- 来源 ID；
- 来源版本；
- 抓取时间；
- 标题；
- 描述；
- 完整评论；
- 状态；
- 来源 URL；
- 分页完整性；
- 快照哈希；
- 幂等键。

n8n 发布前必须保存 active/draft 快照、manifest，并运行现有 workflow snapshot 校验器。

## 四、阶段 3：Summary → Review → 人工治理

### Summary

SupportPortal 根据来源快照生成结构化 Summary，保留：

- 问题背景；
- 现象；
- 原因；
- 解决方案；
- 适用范围；
- 限制条件；
- 来源证据；
- 不确定点。

### AI Review

Review 阶段读取：

- Summary；
- 原始来源快照；
- AgentMemory 检索结果；
- WeKnora 检索结果；
- 候选内容；
- 版本和重复证据。

Review 输出必须明确：

- `new`
- `no_change`
- `supplement`
- `replace`
- `merge`
- `human_review`

Review 失败或证据不足时进入人工 Review，不得降级成自动 `new`。

## 五、阶段 4：Slack 人工 Review

人工入口按你的决定确定为：

**Slack 线程为主，页面/API 作为补充。**

### 有工单线程的候选

SupportPortal 在原有 Slack 线程中发送：

- 来源；
- AI 判断；
- 重复证据；
- 完整候选正文；
- 目标对象；
- `base_version`；
- promotion ID；
- Review 原因。

人工回复：

```text
@bot knowledge reject
```

或：

```text
@bot knowledge approve new <完整正文>
```

定向操作：

```text
@bot knowledge approve merge target=<object_id> base_version=<version> <完整正文>
```

`merge`、`replace`、`supplement` 必须携带目标对象和 Review 依据的 `base_version`。

### source-only 候选

CSD/Solved 可能没有既有工单线程。SupportPortal 需要在指定的知识审核频道创建根消息，并保存：

- Slack channel ID；
- Slack thread timestamp；
- promotion ID；
- source ID；
- candidate ID。

后续在该线程中完成审核。

### Slack 安全边界

- Slack 回复必须带 bot mention；
- 回复必须绑定当前线程；
- 多个候选同时待审时拒绝模糊命令，转页面/API；
- 操作人从已验证的 Slack 身份取得；
- 不信任客户端任意提交的 `operator` 字段；
- Slack 通知失败不能丢失候选，队列仍可通过页面/API访问。

现有 Slack 逻辑位于 [backend/services/engineer_slack.py](../../backend/services/engineer_slack.py)。

## 六、阶段 5：页面/API 补充能力

SupportPortal 页面至少提供：

- 待审核列表；
- 来源快照；
- AI Summary/Review；
- 重复检索结果；
- 完整候选正文；
- 目标对象和版本；
- Slack 线程链接；
- AgentMemory delivery 状态；
- WeKnora delivery 状态；
- 错误和回读结果；
- 人工决定和审计记录。

现有 API：

```text
GET  /automation/preproduction/v1/knowledge/promotions?status=human_review
POST /automation/preproduction/v1/knowledge/promotions/{promotion_id}/decision
```

页面/API 是以下场景的正式补充入口：

- Slack 没有投递；
- source-only 候选没有线程；
- 多候选无法通过 Slack 命令消歧；
- 查看超过 Slack 展示长度的完整正文；
- 处理双写部分失败；
- 处理 `outcome_unknown`；
- 重新确认版本冲突。

## 七、阶段 6：Preproduction 发布顺序

按以下顺序执行：

1. SupportPortal 数据结构和双写代码部署，所有开关关闭；
2. 验证来源接口、候选记录和审计记录；
3. 发布 n8n source-only 草稿；
4. 验证 n8n 只投递快照，不直接写任一知识库；
5. 只开启 Summary/Review，不开启外部写入；
6. 验证人工 Review Slack 通知和页面/API 回读；
7. 使用非业务样本验证人工 reject/approve；
8. 开启双写 worker；
9. 先对小范围来源 allowlist 开启；
10. 取得完整双写证据后，再扩大到 CSD/Solved 全量来源。

Hermes 在整个阶段二仍然读取 AgentMemory。

## 八、验证矩阵

必须覆盖：

| 场景 | 预期 |
|---|---|
| 新建、质量足够、无重复 | AgentMemory 和 WeKnora 都成功 |
| 明确重复 | `no_change`，零写入 |
| `merge` | 进入 Slack Review，批准后双写 |
| `replace` | 进入 Slack Review，要求目标和版本 |
| `supplement` | 进入 Slack Review，要求目标和版本 |
| AI 证据不足 | 进入人工 Review |
| AgentMemory 已成功、WeKnora 失败 | 只补 WeKnora |
| WeKnora 已成功、AgentMemory 失败 | 只补 AgentMemory |
| 外部超时 | `outcome_unknown`，先回读 |
| 重复 webhook | 不产生第二个候选或对象 |
| 来源版本过期 | 拒绝旧候选写入 |
| Slack 通知失败 | 队列仍可从页面/API处理 |
| source-only 候选 | 自动创建 Slack 审核线程 |
| 多候选同线程 | Slack 命令拒绝歧义，转页面/API |
| worker 重启 | 租约恢复，不重复成功目标 |
| Hermes 现有调查 | AgentMemory 读取无回归 |

证据必须包含：

- n8n execution ID；
- source receipt；
- Summary/Review session 和 run；
- candidate ID；
- 内容哈希；
- Slack channel/thread；
- 人工决定；
- 两个目标的独立状态；
- AgentMemory 外部 ID/回读；
- WeKnora 外部 ID/回读；
- 错误和补偿记录。

## 九、回滚和停止条件

阶段二必须提供独立开关：

- 来源接收开关；
- Summary/Review 开关；
- 双写 worker 开关；
- AgentMemory delivery 开关；
- WeKnora delivery 开关；
- Slack 通知开关。

出现以下情况立即停止扩大范围：

- 任一目标出现重复对象；
- delivery 状态与外部回读不一致；
- 旧版本覆盖新版本；
- `outcome_unknown` 被盲目重写；
- n8n 仍然存在直写节点；
- Hermes AgentMemory 读取回归；
- Slack 决策无法绑定唯一候选；
- 审计记录缺失。

回滚时关闭新治理和双写消费，保留候选和审计数据，不删除 AgentMemory 或 WeKnora 数据。必要时只恢复 Preproduction 的旧 AgentMemory 链路。

## 十、阶段二完成条件

阶段二只有在以下条件全部满足后才算完成：

1. 两条 n8n 链均只投递 source snapshot；
2. SupportPortal 成为唯一双写编排者；
3. 新建高质量无重复候选能够双写成功；
4. `merge/replace/supplement` 均进入 Slack 人工 Review；
5. source-only 候选能够创建 Slack 审核线程；
6. 页面/API 能处理 Slack 失败和异常候选；
7. 两个目标的部分失败和超时恢复有效；
8. Hermes 继续稳定读取 AgentMemory；
9. WeKnora Web 和检索链持续正常；
10. Preproduction 观察期内无重复写入、旧版本覆盖或审计缺失。

阶段二完成后，先保持 AgentMemory 与 WeKnora 共存观察。历史迁移、Hermes 切换和 AgentMemory 退休另立后续计划，不能在阶段二中顺带执行。
