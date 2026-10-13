# 阶段 6 合并后 Preproduction 基线冻结（2026-10-13）

状态：**停止在第 1 步，未生成 release、未部署、未修改 n8n、未重投样本。**

来源代码：`c9722399daa347a7a24e0f15fafb8fddaedebe39`。采集为只读；服务/任务定义、健康、SSM 元数据和知识 promotion 使用目标账号 `891612554546`、区域 `us-east-1`。完整脱敏 JSON 见同目录 `stage6-postmerge-baseline-20261013.json`。

## 已确认

- `supportportal-preproduction-api`：arn:aws:ecs:us-east-1:891612554546:task-definition/supportportal-preproduction-api:146，running=1、pending=0；PRIMARY rollout 均为 COMPLETED。release=r20261013-4ecea04，build=4ecea0431f1b84490bab4bf51220faeef815e6b2，prompt=pr-78386473bd08，digest=sha256:8bbebbce46f8872d01508712745af4e4766ed51d76fe691a7939d7963511fead。
- `supportportal-preproduction-worker`：arn:aws:ecs:us-east-1:891612554546:task-definition/supportportal-preproduction-worker:146，running=1、pending=0；PRIMARY rollout 均为 COMPLETED。release=r20261013-4ecea04，build=4ecea0431f1b84490bab4bf51220faeef815e6b2，prompt=pr-78386473bd08，digest=sha256:9587425beadc871b6b49d92f2f832b407b8cc83e070bbbfdb3107c99f36a8b48。
- `supportportal-preproduction-route`：arn:aws:ecs:us-east-1:891612554546:task-definition/supportportal-preproduction-route:144，running=1、pending=0；PRIMARY rollout 均为 COMPLETED。release=r20261013-4ecea04，build=4ecea0431f1b84490bab4bf51220faeef815e6b2，prompt=pr-78386473bd08，digest=sha256:d7d3b117c147c61df4cf0f2fa87dfbf8c4bd8ebd44e330b5135e0e3befed3118。
- Route 当前 `AUTOMATION_CASE_ENGINE=hermes`；API/Worker 当前 `HERMES_CASE_WORKFLOW_MODE=disabled`。
- API `/health/live`、`/health/release`、`/health/ready` 返回 200/ok；release provenance 为 `r20261013-4ecea04`、`4ecea0431f1b84490bab4bf51220faeef815e6b2`、`automation-ecs-015`、`pr-78386473bd08`。
- API/Worker 已启用 `KNOWLEDGE_SOURCE_INTAKE_ENABLED=1` 与 `KNOWLEDGE_SUMMARY_REVIEW_ENABLED=1`，allowlist 为 `csd_issue:CSD-79923,csd_issue:CSD-79994`；其余当前回读的 delivery/Slack/dual-write 开关为 0。
- promotion 只读统计：`human_review=64`；`queued=0`、`active=0`、`accepted=0`、`failed=0`、`outcome_unknown=0`、`invalidated=0`、`rejected=0`。两个目标样本的历史 promotion 存在，不能视为新鲜样本。
- Preproduction SSM 参数元数据已回读；只记录名称、类型、版本和更新时间，不读取或保存 secret value。
- n8n 实时 MCP 回读：CSD active/current version `6511c2e9…`、12 nodes；Solved `d4c50a10…`、8 nodes；Forward `4ed2c0c3…`、13 nodes。三条均 active，当前详情显示 active 与 draft 相同；投递地址为 Preproduction source endpoint，凭据仅记录引用名称/ID。未调用 publish/update。

## 阻断与未知

- 计划要求新全关闭基线，但当前线上仍是旧 release `r20261013-4ecea04` 且 intake/summary_review/allowlist 已打开；与目标 `main@c9722399` 和“master/全部知识开关/AI execution/allowlist 全关闭”不一致。
- 当前 promotion/human-review backlog 与目标样本历史记录的归属、是否允许清理或隔离，尚未得到新的业务 reconciliation；本轮不能把历史记录重置为零，也不能直接重投。
- Terraform executable 不在 PATH，未能执行 zero-drift refresh plan；不能用静态 Terraform 文件替代实时 drift 证据。
- `/automation/preproduction/v1/health/*` 是错误路径并返回 401；正确路径为 `/automation/preproduction/health/*`，已据此完成只读回读。

## 停止点

按计划第 1 步的 fail-closed 要求，停止在“实时基线不符合目标且存在历史队列/样本状态、Terraform drift 尚未核对”。未创建新 release ID、未构建镜像、未注册 task definition、未 update ECS service、未调用 n8n publish/update、未调用样本 intake。继续操作前需要先完成当前线上 release/配置/队列的 reconciliation，并补齐 Terraform zero-drift 证据。
