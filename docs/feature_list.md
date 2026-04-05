# SupportPortal 主功能清单

本文件是 SupportPortal 的唯一主功能清单。

维护规则：
- 只记录主功能，不记录 UI 微调、文案小改、工单状态等小改动、纯 bugfix、测试或重构。
- 每条功能只写一句话，尽量简短，不写原因、实现细节、文件路径或验证信息。
- 跨端同一主功能要在所有相关分类重复记录，文案保持一致，不能写“同上”。
- 主功能完成后，要把对应条目从相关分类的 `未完成` 移到 `已完成`。
- 分类顺序固定为 `Client 端`、`Engineer 端`、`Ticket Dashboard`、`RAG Dashboard`、`RAG`。

## Client 端

### 已完成
- 客户提问会自动生成工单。
- 系统会识别 Agora 范围并分流。
- 系统会用 RAG 自动答复技术问题。
- 证据不足时会转工程师处理。
- 查询扩展会用词典、LLM 和 PRF 优化技术检索。
- 新会话会先选择产品，并按产品加载对应的 support prompt。
- 排查型问题会先向客户补齐必要信息，再自动创建工程师工单。
- 客户工单处理支持 main agent 调度 route、RAG 和 review 子 agent。

### 未完成
- 对话支持上传图片和 txt/log/md 文件。
- 对话支持流式输出。

## Engineer 端

### 已完成
- 升级工单会进入工程师任务池。
- 工程师可切换托管与接管模式。
- 证据不足时会转工程师处理。
- 调查中工单会按工程师 ticket 生命周期流转。
- 工程师审核草稿后会回传客户。
- 排查型问题会先向客户补齐必要信息，再自动创建工程师工单。

### 未完成
- 对话支持上传图片和 txt/log/md 文件。
- 对话支持流式输出。

## Ticket Dashboard

### 已完成
- Dashboard 可查看全量工单列表。
- Dashboard 可查看工单详情与时间线。
- Dashboard 的 ticket detail 可查看按工单 family 聚合的 token 用量摘要。
- Dashboard 可跟踪实时事件流。
- Dashboard 的 ticket detail 可查看 client agent runtime 摘要与最近 agent events。

### 未完成
- 待补充。

## RAG Dashboard

### 已完成
- Dashboard 可同步本地 benchmark 数据集。
- Dashboard 可发起 benchmark 运行并查看会话。
- Dashboard 可按 benchmark run 和 session 查看诊断分布与对比结果。
- Dashboard 的 Overview 可查看 benchmark token 汇总与 provider/model 明细。
- Dashboard 可复盘 live 与 benchmark case。
- Dashboard 可查看 query-understanding、候选漏斗和 judge 分歧诊断。
- Dashboard 可评审样本并导出结果。

### 未完成
- 待补充。

## RAG

### 已完成
- 工程师可上传知识入库。
- 系统会做混合检索与重排召回。
- 查询扩展会用词典、LLM 和 PRF 优化技术检索。
- 系统会按上下文预算压缩证据再生成技术答案。
- 系统会按 provider/model 统计 RAG token，并支持 future-ready usage ledger。
- 系统会输出 benchmark 分层诊断与失败归因。
- 证据不足时会转工程师处理。
- 系统已具备本地 benchmark 评测链路。
- 新会话会先选择产品，并按产品加载对应的 support prompt。
- 排查型问题会先向客户补齐必要信息，再自动创建工程师工单。
- 客户工单处理支持 main agent 调度 route、RAG 和 review 子 agent。

### 未完成
- 待补充。
