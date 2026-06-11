# Graphiti 安装部署指南

## 环境要求

| 组件 | 最低要求 | 推荐 |
|------|---------|------|
| OS | Linux (glibc ≥ 2.28) / macOS | Ubuntu 22.04+, CentOS 8+ |
| Python | 3.11+ | 3.11 |
| 内存 | 8 GB | 16 GB |
| 磁盘 | 20 GB | 50 GB |
| Docker | 20.10+ | 最新版 |
| GPU | 不需要 | 不需要 |

## 一、安装 Docker

```bash
# Ubuntu
sudo apt update && sudo apt install -y docker.io
sudo systemctl enable docker && sudo systemctl start docker

# CentOS 8+
sudo dnf install -y docker
sudo systemctl enable docker && sudo systemctl start docker

# macOS
brew install --cask docker
```

## 二、部署中间件

### 2.1 Neo4j 图数据库

```bash
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/graphiti123 \
  -v neo4j_data:/data \
  neo4j:5.26
```

验证：
```bash
curl -s http://localhost:7474
# 返回 200 即正常
# 浏览器访问 http://localhost:7474，用户名 neo4j，密码 graphiti123
```

### 2.2 Ollama（Embedding 模型）

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# 拉取中文向量化模型
ollama pull bge-m3:latest

# 验证
curl http://localhost:11434/api/embed -d '{"model":"bge-m3:latest","input":"测试"}'
```

如果 Ollama 装在**另一台机器**上，确保：
- 端口 11434 对外开放
- 配置文件中 `embedding.base_url` 指向该 IP

### 2.3 Docker Tesseract（OCR，可选）

```bash
docker pull tesseractshadow/tesseract4re:latest
```

## 三、安装 Python 环境

### 3.1 安装 uv（Python 包管理器）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.cargo/env
```

### 3.2 克隆代码

```bash
git clone https://gitee.com/mengxy98/cusmem.git
cd cusmem
git checkout simplify
```

### 3.3 安装依赖

```bash
uv sync --extra dev
```

如果遇到 `numpy` 编译错误（GCC 版本太旧），用预编译 wheel：

```bash
uv pip install --python .venv/bin/python3.11 "numpy<2"
uv sync --extra dev
```

### 3.4 安装 PDF 处理依赖

```bash
uv pip install --python .venv/bin/python3.11 pdfplumber pdfminer.six pypdfium2
```

### 3.5 安装 OCR/文档依赖（可选）

```bash
uv pip install --python .venv/bin/python3.11 python-docx Pillow
```

## 四、配置文件

复制并修改 `graphrag_config.yaml`：

```yaml
neo4j:
  uri: "bolt://YOUR_HOST:7687"
  user: "neo4j"
  password: "graphiti123"

llm:
  api_key: "${DEEPSEEK_API_KEY}"
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-chat"

embedding:
  model: "bge-m3:latest"
  base_url: "http://YOUR_HOST:11434/v1/"  # Ollama 地址

schema:
  path: "schemas/gbt25338.yaml"
  mode: "lenient"

pipeline:
  ingest_mode: "append"
  chunk_size: 1200
  chunk_overlap: 100
  num_threads_per_chain: 1
  max_concurrency: 1
  progress: true
```

### 环境变量

```bash
export DEEPSEEK_API_KEY=sk-你的key
export GRAPHRAG_NEO4J_URI=bolt://YOUR_HOST:7687
export GRAPHRAG_NEO4J_PASSWORD=graphiti123
export GRAPHRAG_NUM_THREADS=1
export GRAPHRAG_MAX_CONCURRENCY=1
export GRAPHRAG_SCHEMA_MODE=lenient
```

## 五、验证安装

```bash
cd cusmem

# 测试导入
.venv/bin/python3 -c "import graphiti_core; print('core OK')"
.venv/bin/python3 -c "from graphiti_rag import GraphRAG; print('rag OK')"

# 测试 Neo4j 连接
DEEPSEEK_API_KEY=sk-xxx .venv/bin/python3 -c "
import asyncio,os;os.environ['OPENAI_API_KEY']='ollama'
from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
async def t():
    llm=OpenAIClient(config=LLMConfig(api_key=os.environ['DEEPSEEK_API_KEY'],base_url='https://api.deepseek.com/v1',model='deepseek-chat',small_model='deepseek-chat'))
    g=Graphiti(uri='bolt://localhost:7687',user='neo4j',password='graphiti123',llm_client=llm)
    await g.build_indices_and_constraints()
    print('Neo4j OK')
    await g.close()
asyncio.run(t())
"
```

## 六、使用

### 6.1 录入文档

```bash
DEEPSEEK_API_KEY=sk-xxx .venv/bin/python3 ingest_gbt.py
```

### 6.2 查询知识图谱

```python
# 通过 Neo4j Browser
# 打开 http://YOUR_HOST:7474，输入：
MATCH p=(a:Entity)-[r:RELATES_TO]->(b:Entity) RETURN p LIMIT 30

# 或通过代码
from graphiti_core import Graphiti
g = Graphiti(uri='bolt://localhost:7687', user='neo4j', password='graphiti123', ...)
edges = await g.search(query='转辙机 防护等级', num_results=10)
for e in edges:
    print(f'[{e.name}] {e.fact}')
```

### 6.3 启动 REST API（可选）

```bash
PYTHONPATH=$PWD/server:$PYTHONPATH .venv/bin/uvicorn graph_service.main:app --host 0.0.0.0 --port 8000
```

## 七、故障排查

| 问题 | 解决方案 |
|------|---------|
| Neo4j 连接失败 | 检查端口 7687 是否开放，密码是否正确 |
| Ollama OOM | 减少 `max_concurrency=1`，关闭不用的 Neo4j 容器 |
| numpy 编译失败 (GCC < 9.3) | `uv pip install --python .venv/bin/python3.11 "numpy<2"` |
| PDF 乱码 | 会自动用 Docker tesseract OCR 回退 |
| DeepSeek API 空响应 | 重试自动处理，降低 `max_concurrency` 可减少发生概率 |
| 社区构建超时 | 默认不开启，不影响主流程 |
