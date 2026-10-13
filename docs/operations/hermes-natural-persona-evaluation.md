# Hermes Persona 合成评估

验证日期：2026-10-13。维护入口：[natural_persona_eval.py](../../scripts/testing/natural_persona_eval.py)，合同与边界见 [rev v3 计划](../plans/hermes-enablement-reply-delivery-natural-persona-v1.1.md)。

使用项目根 `.venv`，从目标 task definition 只读获取 `AGENT_MODEL_ID`，记录 task revision 和读取时间；不要使用 extractor profile 或擅自换模型。API key 使用现有安全环境传入，不写入命令输出或证据。

```sh
AGENT_MODEL_ID=<target-persona-model> /absolute/project/.venv/bin/python \
  -m scripts.testing.natural_persona_eval \
  --model-config-source '<timestamp and target task definition>' \
  --output /absolute/path/model-evaluation.json
```

载体使用真实 InMemory store/repository、snapshot builder、canonical basis resolver，并初始化代码 Prompt runtime。每个样本保存完整请求和未经删改的原始正文，在称呼投影前验证，再执行实际翻译与安全处理。判断逻辑先以故障负控自检，不以期望关键词向模型提示答案。

评估是离线适配器；不是线上 Hermes session 或真实工单业务验证。`returned_model_id`、源 HEAD 和关键源码 hash 必须可核对。记录每次修复后的新运行，不从失败结果中挑选一轮冒充全部通过。自然度另读 raw/final 正文，写出事实、漏项、重复追问、承诺和表达评审依据；不能只凭自动计数结案。

`rerun_automated_account_cases.py` 使用固定的 `persona-instruction-opener-v2` 审批 catalog。catalog 变化会改变新操作 fingerprint；历史签名操作失配须停止，不自动改 pin 或重签。评估本身不授权执行 rerun。
