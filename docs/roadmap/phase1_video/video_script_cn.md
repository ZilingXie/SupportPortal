# SupportPortal Phase 1 剪映分镜脚本

用于你自己看，也可以直接按表格把素材和字幕放进剪映。英文 `video_script.md` 是纯旁白；本文件负责每一幕用哪张图、哪段视频、字幕写什么。

| Scene # | 时间段 | 时长 | 使用素材 | 字幕 / 画面文字 | 剪映画面动作 |
|---|---:|---:|---|---|---|
| Scene 01 | 00:00-00:06 | 6s | `phase1_video/1-intro.jpeg` | How do we ensure support reply quality before the customer sees the answer? | 开场标题图，轻微放大，字幕居中停留。 |
| Scene 02 | 00:06-00:13 | 7s | `phase1_video/2- bad-case-support-failure-demo.mp4` | A fast answer can still be a bad answer. | 播放 bad case 动画，保留原节奏。 |
| Scene 03 | 00:13-00:22 | 9s | `phase1_video/3-painpoint.png` | Traditional support finds quality problems too late. | 慢推近 pain point 区域，依次强调 quality、visibility、review、speed。 |
| Scene 04 | 00:22-00:33 | 11s | `phase1_video/4-whynow.png` | Zendesk costs about 73k USD/year, but the bigger issue is limited customization. | 从 cost 移动到 customization / data mining / analytics。 |
| Scene 05 | 00:33-00:45 | 12s | `phase1_video/5-bigpic.png` | Customer UI stays the same. SupportPortal upgrades the internal workflow. | 先高亮 Customer / Zendesk，再高亮 SupportPortal Core。 |
| Scene 06 | 00:45-00:54 | 9s | `phase1_video/6-1-engineer.png` | Engineers still receive and own cases in the assignment workspace. | 轻微横移工单列表和详情区。 |
| Scene 07 | 00:54-01:03 | 9s | `phase1_video/6-2-engineer.png` | Assignment gives the team route, owner, review, SLA risk, and fallback control. | 继续展示 engineer 端，不展示 `/assignment/admin`。 |
| Scene 08 | 01:03-01:13 | 10s | `phase1_video/7-showcase-guardrail-demo.mp4` | AI Guardrail rejects incomplete replies before customers see them. | 播放 guardrail demo，保留 progress 高亮。 |
| Scene 09 | 01:13-01:25 | 12s | `phase1_video/7-showcase-guardrail-demo.mp4` | Proof plus a safe next step turns a risky draft into a reviewable answer. | 继续播放/截到 evidence 与 approval，最后停一下。 |
| Scene 10 | 01:25-01:36 | 11s | `phase1_video/8-dashboard.png` | Move management metrics from traditional SLA to the AI native era. | 左右并排画面，慢慢平移 4 宫格指标。 |
| Scene 11 | 01:36-01:48 | 12s | `phase1_video/9-automation1.png` | Some account cases do not need an engineer in the loop. | 展示 Account Reactivation Request 对话，强调 AI 收集信息。 |
| Scene 12 | 01:48-01:58 | 10s | `phase1_video/10-email.png` | AI can package complete information into an internal request. | 展示内部邮件模拟，轻微放大 subject 和 To。 |
| Scene 13 | 01:58-02:10 | 12s | `phase1_video/11-agentrelay.png` | AgentRelay turns private agents into task collaborators. | 展示 AgentRelay，突出 agent 代表 person / team。 |
| Scene 14 | 02:10-02:22 | 12s | `phase1_video/12-rnd-agent.png` | When support should not guess, it asks an R&D Agent for evidence. | 展示 R&D investigation loop，强调 evidence artifact。 |
| Scene 15 | 02:22-02:44 | 22s | `phase1_video/13-rnd-agent-example.png` | The R&D Agent finds `Client.unpublish`: intentional behavior, not an abnormal disconnection. | 慢慢移动到 conclusion 和 `Client.unpublish` 证据。 |
| Scene 16 | 02:44-03:00 | 16s | `phase1_video/14-closing.png` | Phase 1 proves an AI-native support operating system: safer replies, smarter routing, controlled automation, and agent collaboration. | 结尾图稳定停留，最后 2 秒不要切。 |

## 素材顺序

1. `phase1_video/1-intro.jpeg`
2. `phase1_video/2- bad-case-support-failure-demo.mp4`
3. `phase1_video/3-painpoint.png`
4. `phase1_video/4-whynow.png`
5. `phase1_video/5-bigpic.png`
6. `phase1_video/6-1-engineer.png`
7. `phase1_video/6-2-engineer.png`
8. `phase1_video/7-showcase-guardrail-demo.mp4`
9. `phase1_video/8-dashboard.png`
10. `phase1_video/9-automation1.png`
11. `phase1_video/10-email.png`
12. `phase1_video/11-agentrelay.png`
13. `phase1_video/12-rnd-agent.png`
14. `phase1_video/13-rnd-agent-example.png`
15. `phase1_video/14-closing.png`

## 剪映建议

- 图文成片时，把 `字幕 / 画面文字` 当作每幕主字幕。
- 视频素材 `2- bad-case-support-failure-demo.mp4` 和 `7-showcase-guardrail-demo.mp4` 放主轨，不要再加过多动效。
- 静态截图使用轻微放大或横移即可，避免花哨转场。
- 重点字幕保留英文，方便领导直接看项目术语：`AI Guardrail`、`SupportPortal`、`AgentRelay`、`R&D Agent`。
