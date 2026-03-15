# SupportPortal AI 优化方案：择优吸收 `e-comm_agent` 能力

## Summary
- 保留 `SupportPortal` 作为主系统，不把 `e-comm_agent` 整体并入或并行上线。
- 分析结论是：`SupportPortal` 现在已经更强于工单流转、异步 worker、WebSocket 同步和基于引用的 pgvector RAG；`e-comm_agent` 更值得借鉴的是能力抽象方式，而不是整套运行形态。
- 这一轮按“AI 问答质量优先 + 中等改造”推进，只吸收 3 类高价值能力：统一 AI 服务层、知识文件上传/索引、轻量级问题路由与补充提问。
- 明确不在这一阶段引入 `LangGraph`、`GraphRAG`、`Neo4j`、公网搜索客服回答、完整用户认证迁移。

## Key Changes
- `AI runtime`
  - 新增统一配置层和 `LLMServiceFactory`，统一管理模型选择、超时、重试和日志；覆盖 RAG 回答、情绪回复、工程师交接摘要、工单总结。
  - 第一阶段默认仍用 OpenAI，但接口设计成可扩展到 DeepSeek/Ollama，不改业务调用方。
  - 把当前 `build_answer(...) -> tuple[...]` 改成类型化返回 `AnswerDecision`：
    - `kind`: `answer | clarify | escalate`
    - `message`
    - `confidence`
    - `sources`
    - `citations`
    - `reason`
  - 参考 `e-comm_agent` 的路由思想，但不引入 LangGraph，改成轻量三段式决策：
    - `answer`：现有知识库可支撑有引用回答
    - `clarify`：用户信息不足，先追问关键信息
    - `escalate`：证据不足或必须人工介入
  - 新增事件类型 `ticket_ai_clarification_requested`；客户端按普通 AI 回复展示，工程师端和 dashboard 按显式事件展示。
- `Knowledge ingestion`
  - 在现有后端内新增知识入库接口，而不是再起独立 AI 服务：
    - `POST /api/engineer/knowledge/ingestions`：上传文件并创建索引任务
    - `GET /api/engineer/knowledge/ingestions`：查看任务列表
    - `GET /api/engineer/knowledge/ingestions/{ingestion_id}`：查看单个任务状态
  - 新增 Postgres 表 `support_knowledge_ingestions` 记录上传文件、状态、错误信息、来源元数据。
  - 复用当前 `PGVECTOR_TABLE` 约定；若表不存在则按 `rag_qa` 当前假设的结构创建：`id`, `content`, `source_path`, `h1`, `h2`, `h3`, `source_url`, `embedding`。
  - 复用现有 Redis worker 机制，新增 `knowledge_ingest` 任务类型；不引入 GraphRAG 索引流水线。
  - 第一阶段支持 `pdf`、`txt`、`md`、`docx`；分块规则采用“标题优先 + 固定窗口重叠”，保证当前引用 UI 仍然可用。
- `Observability and safety`
  - 加入 HTTP 请求日志中间件，并为 API、worker、ws gateway 统一结构化日志；每条日志附带 `request_id/ticket_id/task_id`。
  - 记录 AI 决策类型、模型名、耗时、回退原因到事件流，便于后续调优。
  - 不直接复用 `e-comm_agent` 当前的 Redis 语义缓存实现；若本轮加缓存，只缓存确定性辅助输出，如工单总结/工程师交接摘要，并使用普通 Redis key，不做 `KEYS + 全量向量扫描`。
- `UI adjustments`
  - 保持现有客户提单接口和工单流转接口不变。
  - 工程师端和 dashboard 增加知识入库状态展示，以及 `ticket_ai_clarification_requested` 事件展示。
  - 保持当前 citation 字段契约不变，新增知识文件仍输出现有前端已消费的 `source_path/source_url/heading/chunk_id`。

## Test Plan
- 单元测试
  - `AnswerDecision` 三类分流：可回答、需补充信息、需升级人工。
  - 统一 AI factory 的配置解析和调用路径。
  - `pdf/txt/md/docx` 文件解析与分块。
  - 日志上下文是否正确带出 `request_id/ticket_id/task_id`。
- 集成测试
  - 上传知识文件，进入索引队列，验证 chunk 写入 `PGVECTOR_TABLE`，再验证 `answer_with_rag` 能引用新内容。
  - 提交信息不足的问题，验证系统先追问而不是直接升级工程师。
  - 提交知识库无法覆盖的问题，验证仍然进入当前人工接管路径。
  - 验证现有 WebSocket 同步和 worker 防重/防过期逻辑不回归。
- 回归检查
  - 现有 `/api/tickets/query`、工程师动作接口、summary/citation 结构保持兼容。
  - 客服回答链路不走公网搜索。
  - 不新增 Neo4j、LangGraph Studio、GraphRAG 运行依赖。

## Assumptions
- `SupportPortal` 继续作为唯一生产应用，`e-comm_agent` 只作为设计参考，不作为并入运行的子系统。
- 本轮目标是提升 AI 回答质量，接受中等规模后端改造，但避免重型基础设施。
- 第一阶段默认 provider 仍是 OpenAI，多模型能力只做抽象，不做上线切换。
- `e-comm_agent` 的完整用户/JWT/会话体系这一轮不迁移。
- `e-comm_agent` 中存在明显实验性或不稳定实现，按“借鉴思想、重写实现”处理，不直接搬代码：
  - `IndexingService` 存在未定义 `config_mapping`
  - `SearchService` 有吞异常逻辑
  - 认证配置存在 token 路径不一致
  - `LangGraph/GraphRAG/Neo4j` 组合对当前仓库来说过重
