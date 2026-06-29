# SupportPortal Phase 1 3 分钟讲解视频 Storyboard

目标：把 `phase1.html` 做成 3 分钟产品讲解视频。整体风格是“真实系统页面 + 关键局部放大 + 稳定旁白”，避免做成纯 PPT。

| Shot | 时间 | 素材 | 画面处理 | 目的 |
|---|---:|---|---|---|
| 01 · Why now | 00:00-00:35 | `shots/01-why-now.png` | 先展示标题，再轻微推近 Why now 的 73k、SLA、Billing、Custom。 | 说明为什么现在要做：Zendesk 成本高，customization 空间不足。 |
| 02 · Big picture | 00:35-01:05 | `shots/02-big-picture.png` | 停在三层架构图：Customer / Zendesk、SupportPortal Core、Future Agent Network。 | 说明 Phase 1 是保守迁移：客户入口不变，内部处理能力升级。 |
| 03 · Assignment admin | 01:05-01:25 | `shots/03-assignment-admin.png` | 展示 `/assignment/admin` 页面，建议放大 engineer/day picker、shift、manual override 或 routing fallback。 | 说明 assignment 是调度控制面，不只是 UI demo。 |
| 04 · AgentRelay network | 01:25-01:55 | `shots/04-agentrelay-network.png` | 展示 AgentRelay 四问和 communication state machine。 | 解释为什么需要 AgentRelay、为什么不用纯 A2A、为什么 agent 之间要通信。 |
| 05 · R&D Agent example | 01:55-02:20 | `shots/05-rnd-agent-example.png`，源图：`docs/roadmap/assets/rnd-agent-query.png`、`docs/roadmap/assets/rnd-agent-result.png` | 左右两张截图轻微推近：query planning -> evidence artifact。 | 证明 R&D/Data Agent 能产出 support 可复用证据，不是“聊天”。 |
| 06 · Guardrail showcase | 02:20-02:45 | `shots/06-guardrail-showcase.png` | 先放大低质量 engineer draft，再切到 `Rejected by AI Guardrail` 和四个检查维度。 | 展示 Guardrail 如何拒绝不完整回复。 |
| 07 · Dashboard roadmap | 02:45-03:00 | `shots/07-dashboard-roadmap.png` | 从 Dashboard 指标切到 Roadmap 三阶段。 | 收束到管理指标升级和阶段推进。 |

## 截图生成说明

- `shots/01-why-now.png`：来自 `docs/roadmap/phase1.html`，覆盖 hero + Why now 区域。
- `shots/02-big-picture.png`：来自 `docs/roadmap/phase1.html#architecture` 附近，覆盖架构图和 assignment 摘要。
- `shots/03-assignment-admin.png`：来自线上或本地 `/assignment/admin`。
- `shots/04-agentrelay-network.png`：来自 `docs/roadmap/phase1.html#agentrelay`。
- `shots/05-rnd-agent-example.png`：来自 `docs/roadmap/phase1.html#agentrelay` 的 R&D Agent example 区域。
- `shots/06-guardrail-showcase.png`：来自 `docs/roadmap/phase1.html#showcase`。
- `shots/07-dashboard-roadmap.png`：来自 `docs/roadmap/phase1.html#dashboard` 和 `#roadmap` 的拼接视角。

## 后期建议

- 字幕直接使用 `voiceover.txt` 分句生成，保留英文关键词。
- 如果 TTS 读英文太机械，可以把 `AgentRelay` 读作 “Agent Relay”，把 `guardrail` 读作 “guard rail”。
- 背景音乐保持极低音量，最好不用强节奏音乐；这个视频的信任感来自真实页面和清晰逻辑。
