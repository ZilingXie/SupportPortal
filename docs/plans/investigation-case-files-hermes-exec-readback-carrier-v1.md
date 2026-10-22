# Hermes ECS Exec readback carrier v1

计划名称：`investigation-case-files-slack-ingress-metadata-v1` / Hermes Exec readback carrier。修订：**v1**。

目标是修复并验证 Hermes Preproduction metadata readiness 的 ECS Exec 回读载具。上一次 v4 部署的 image/EFS/served-content 检查通过，但合并 health/toolsets 的 ECS Exec wrapper 只返回 health marker 后 EOF；该部署已恢复 TD54 和旧 EFS Skill。本阶段只修复载具，不开始真实文件测试。

授权范围：新增只读验证脚本、测试和证据；后续在代码独立验收通过后，才可按既有授权使用全新 release 重新做 Preproduction Hermes/EFS readiness。继续排除 SupportPortal API/Route/Worker、n8n、Production、case/session/run、Slack/Zendesk/Zac 业务调用。

合同：

| ID | 要求 | 本地验证 |
| --- | --- | --- |
| ERC-1 | health 与 toolsets 分离为两个 ECS Exec 会话 | `run_split_readbacks` 实际为两个命令并分别写记录 |
| ERC-2 | 每个会话记录 stdout/stderr/退出码/超时/EOF/唯一 marker/JSON | `health.json`、`toolsets.json` schema 与测试 |
| ERC-3 | 缺 marker、重复 marker、半截 JSON、EOF、非零、超时 fail closed | 7 个负向场景覆盖；主入口返回非零 |
| ERC-4 | 两者完整成功才报告通过 | success entry test 及 `GATEWAY_READBACK_PASSED` |
| ERC-5 | 失败不修改 EFS、任务定义或业务数据 | 载具只有 ECS Exec readback；本阶段未执行云端写入 |

代表性实现是 [gateway_readback.py](../../deployment/hermes-case-files/gateway_readback.py)。它不依赖 wrapper exit 0，不把 health 部分结果提升为整体成功，并保留失败原始输出用于独立复核。

验证：`backend/tests/test_hermes_gateway_readback.py`，8 passed；py_compile 与 `git diff --check` 通过。代码独立验收前不合并、不部署。代码通过后使用全新 release ID，在稳定 TD54 上先做两个独立只读会话，随后才决定是否进入新的 Hermes/EFS readiness 发布；本阶段 `ready_for_real_file_tests=false`。
