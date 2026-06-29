# SupportPortal Phase 1 3 分钟讲解视频逐秒旁白脚本

用途：给 3 分钟讲解视频做配音、剪辑和字幕对齐。语速按中文商务讲解约 240-270 字/分钟设计，实际 TTS 可微调到 0.95x-1.05x。

| 时间 | 画面 | 旁白 |
|---|---|---|
| 00:00-00:15 | `shots/01-why-now.png`，从 Phase 1 标题切到 Why now。 | 今天用三分钟介绍 SupportPortal Phase 1。它不是简单的 Zendesk 平替，也不是再做一个 ticket UI。Phase 1 要证明的是：我们能把 support 的质量检查、调查协作和管理指标放进系统流程里。 |
| 00:15-00:35 | `shots/01-why-now.png`，放大 73k、SLA、Billing、Custom 四个点。 | 现在做这件事有两个原因。第一，Zendesk license 每年七万三千美金，但给我们的仍然是通用 SaaS 能力。第二，更关键的是，Zendesk 的 customization 空间不够，不管是 feature 扩展、内部流程定制，还是数据挖掘和质量分析，都很难满足 support 团队自己的节奏。 |
| 00:35-01:05 | `shots/02-big-picture.png`，展示三层架构图。 | Phase 1 的迁移策略很保守：客户入口先不动，CX 体验也不变。客户仍然从 Zendesk 进入，内部将工单转发到 SupportPortal。我们先把 routing、assignment、AI guardrail、final approve、dashboard 和真实 case replay 跑起来，用 shadow mode 和小流量门禁证明系统可控之后，再考虑扩大切流。 |
| 01:05-01:25 | `shots/03-assignment-admin.png`，展示 `/assignment/admin`。 | Assignment 是 Phase 1 的调度控制面。系统不只是生成回复，还要决定 case 应该给谁处理、是否进入 human review、是否有 SLA risk，以及 routing fallback 怎么兜底。`/assignment/admin` 是 manager 管排班、规则和 manual override 的入口。 |
| 01:25-01:55 | `shots/04-agentrelay-network.png`，切到 AgentRelay 四问。 | AgentRelay 解决的是 agent 协作问题。agent 和 agent 不是互相发散聊天，而是通过 AgentRelay 交换任务、artifact 和 done criteria。为什么不用纯 A2A 协议？因为很多 agent，特别是个人 Agent，没有公网 IP，所以需要 relay 来负责转发、投递、审计和失败兜底。 |
| 01:55-02:20 | `shots/05-rnd-agent-example.png`，展示 R&D / Data Agent 示例截图。 | 为什么 support 需要 agent 之间通信？因为 Support Agent 解决不了的问题，不应该硬猜，而是要找 R&D Agent 或 Data Agent 拿证据。这里的例子是 Agent 接收 RTC 用量问题，运行 discovery query，然后返回用量结论、峰值分析和 evidence artifact。这个形态就是后续 SupportPortal 要接入的调查证据包。 |
| 02:20-02:45 | `shots/06-guardrail-showcase.png`，展示 Guardrail 拒绝低质量回复。 | Showcase 的重点是 AI Guardrail 如何拒绝不完整回复。比如工程师写 “We checked the account. Please try again later.” 这个问题不是语气，而是没有 conclusion、proof、next step，也没有 customer-safe boundary。Guardrail 会直接拒绝，并要求补充明确结论、证据摘要和客户下一步。 |
| 02:45-03:00 | `shots/07-dashboard-roadmap.png`，展示 Dashboard 和三阶段路线图。 | 管理指标也要升级。以前主要看 first response time、second response time 和 SLA。现在还要看 AI guardrail pass rate、与 Agent 交互次数、一次回复解决问题率。Phase 1 先证明系统可控；Phase 2 接真实 evidence tools；Phase 3 才扩大 governed autonomous investigation。 |

## 剪辑备注

- 每个镜头建议加 0.4 秒交叉淡入淡出，不要使用花哨转场。
- 关键英文词可以保留字幕原文：`AI guardrail pass rate`、`AgentRelay`、`evidence artifact`、`customer-safe boundary`。
- 画面节奏优先保持稳定；用轻微 zoom-in 强调 73k、architecture、AgentRelay 四问、Guardrail rejected、Dashboard 指标。
