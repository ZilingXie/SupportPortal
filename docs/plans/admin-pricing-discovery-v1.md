# admin-pricing-discovery-v1

修订：v1；基线：`434ee3565e49544d562a8afaa3aa48f589419702`；Review gate：none。

目标：为 Account Admin usage 增加模型价格表、usage 行价格快照、助手查价和用户确认状态。RAG 继续以 `excluded_by_admin_policy` 排除；历史 usage 标记 `legacy_unpriced`，不回填；本轮不部署、不写 Production。

关键合同：

- C1：usage 写入同一事务冻结单价、来源和成本；无价为 `NULL`，不伪造 0。
- C2：查价必须精确模型匹配并带来源 URL；歧义或失败保持 `unpriced`，并记录错误。
- C3：确认价格只影响后续 usage，不改历史快照。
- C4：Admin 聚合按 usage 快照计算，混合未定价模型显示 `partial`。
- C5：RAG 永不进入 Admin 费用统计。
- C6：provider/model 冷却期内查价至多一次，并发写入幂等。
- C7：Admin 读路径继续使用只读 `ECS_PRODUCTION_ADMIN_DSN`；查价结果只能经独立的 `ECS_PRODUCTION_PRICING_WRITE_DSN` 写入价格表，写连接缺失或与读连接相同则 fail closed。

范围：migration、InMemory/PostgreSQL repository、usage capture、Admin reader/API、后台查价任务、Admin UI、确认价格命令和定向合同测试。外部搜索复用已有 OpenAI Web Search；缺少配置时保持 `unpriced`。

停止点：出现价格档位/别名歧义、需要回写历史/RAG、发现非本任务工作区变更，或需要 Production 发布时暂停。独立价格写连接已由用户确认，但凭据创建、授权和部署仍需单独执行与验收。
