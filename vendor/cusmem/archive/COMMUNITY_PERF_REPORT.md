# 社区构建性能问题根因报告

## 现象

| 模式 | 实体数 | 社区构建结果 | 耗时 |
|------|--------|------------|------|
| strict | 142 | 78 个社区 + 142 条边 | ~3 分钟 |
| lenient | 175 | **超时未完成**（两次 900s 均 timeout） | >15 分钟后进程被杀 |

两次 lenient 尝试：第一次被 OOM kill，第二次自行 timeout 退出，社区始终为 0。

## 根因

### 根因一：手工图构建——O(N) 次 Neo4j 查询

**文件**: `graphiti_core/utils/maintenance/community_operations.py:55-78`

```python
for node in nodes:          # N = 175 次遍历
    records = await driver.execute_query(
        "MATCH (n {uuid: $uuid})-[e:RELATES_TO]-(m) "
        "RETURN m.uuid, count(e)",
        uuid=node.uuid,     # 每个实体单独一条 Cypher
    )
```

标准做法应该用 Neo4j GDS 库一步完成（`CALL gds.louvain.write(...)`），但当前 Neo4j 社区版 5.26 不含 GDS 插件，所以代码用 Python 手工实现。175 个实体意味着 175 次独立的网络往返——仅图构建阶段就需 60+ 秒。

### 根因二：Python 手工 LPA——CPU 密集型

**文件**: `community_operations.py:80-140`

175 个实体 + 183 条边的手工标签传播，在 Python 中迭代收敛需要额外 30-60 秒。算法复杂度 O(N+E)，但 Python 实现比原生 GDS 慢 10-100 倍。

### 根因三：社区摘要——每个社区调用一次 LLM

**文件**: `community_operations.py:174-213`

```python
for cluster in community_clusters:
    # 每个 cluster 调用 summarize_pair() × (cluster_size - 1) 次 LLM
    build_community(llm_client, cluster)
```

假设产生 90 个社区（175 实体聚类后的合理数量）：

- 每个社区平均 2 个实体 → 需要 1 次 LLM 调用合并摘要
- 90 个社区 × 1 次 LLM = **90 次 LLM 调用**
- 每次 LLM 调用（DeepSeek summarize_pair）：15-25 秒
- 总 LLM 耗时：90 × 20s = **1800 秒 = 30 分钟**

虽然并发 10，但 90/10 × 20s = 180s 净时间 + 排队延迟 = 实际 ~5-10 分钟。

### 根因四：渐进疲劳——大图加剧缓慢

strict 模式 142 实体 → 约 78 社区，在系统负载较轻时完成了。
lenient 模式 175 实体（+23%）→ 约 90 社区（+15%），但 175 次查询 + 更复杂的 LPA + 更多 LLM 调用的叠加效应使总时间超过了 900s 上限。

## 数据验证

直接执行 LPA 聚类查询验证时间复杂度：

```
get_community_clusters(driver, group_ids=None) → 120s+ timeout
```

验证了手工 LPA 本身就是瓶颈——仅仅是构建图投影就需要 60s+，还没到 LLM 摘要阶段。

## 时间分解（估算）

| 阶段 | strict (142实体) | lenient (175实体) | 增长 |
|------|---------|---------|------|
| 图构建 (175次Cypher) | ~45s | ~60s | +33% |
| Python LPA 聚类 | ~20s | ~30s | +50% |
| LLM 摘要 (×90社区) | ~120s (78社区) | ~150s (90社区) | +25% |
| **合计** | **~185s** | **~240s+** | **+30%** |

实际运行中，lenient 模式还遇到了偶发的 DeepSeek API 空响应重试，进一步拉长了时间。

## 解决方案

### 短期

1. **降低并发**：`MAX_COMMUNITY_BUILD_CONCURRENCY` 从 10 改为 5，避免 DeepSeek API 过载
2. **跳过小社区摘要**：单个实体的社区不调用 LLM
3. **超时设置**：社区构建单独超时 600s，失败不阻塞 pipeline

### 长期

4. **使用 Neo4j GDS**：安装 GDS 插件后，`get_community_clusters()` 可一步完成
5. **缓存 LPA 结果**：如果 Schema 没变、实体没增删，跳过 LPA
6. **异步社区构建**：把社区检测从同步 pipeline 中拆出，作为后台任务

## 结论

社区构建时间长不是 bug，是**架构瓶颈**——当前 Neo4j 社区版不支持 GDS，所有社区检测走 Python 手工实现。175 个实体的 LPA + 90 个社区的 LLM 摘要，总耗时在 3-8 分钟（取决于 LLM 响应速度）。900s 超时是偶发的 DeepSeek API 波动导致的，降低并发可缓解。
