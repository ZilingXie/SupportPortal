# SupportPortal Phase 1 视频分镜说明

用于你自己看，不建议直接粘到剪映。英文 `video_script.md` 才是给剪映图文成片/TTS 用的短脚本。

| 幕 | 时间 | 时长 | 素材 | 画面在做什么 | 剪映操作 |
|---|---:|---:|---|---|---|
| 第 01 幕 | 00:00-00:05 | 5s | `phase1_video/1-intro.jpeg` | 开场标题和核心问题：如何在客户看到前确保 support 回复质量。 | 图片主轨，轻微放大。字幕突出 `reply quality`。 |
| 第 02 幕 | 00:05-00:10 | 5s | `phase1_video/1-intro.jpeg` | 继续停留在问题上，引出“客户反馈时已经太晚”。 | 同一张图切一刀，继续慢推近。 |
| 第 03 幕 | 00:10-00:18 | 8s | `phase1_video/bad-case-support-failure-demo.mp4` | 播放 bad case 前半：客户黑屏，工程师快速但武断地说相机坏了。 | 放入 mp4，保留动画节奏。 |
| 第 04 幕 | 00:18-00:28 | 10s | `phase1_video/bad-case-support-failure-demo.mp4` | 播放 bad case 后半：客户追问证据和 workaround。 | 继续 mp4，加红色关键词：`no proof`、`no workaround`。 |
| 第 05 幕 | 00:28-00:36 | 8s | `https://support.stellarix.space/roadmap/phase1.html#reply-quality` 截图 | 展示传统 support 的质量风险卡片。 | 截 bad-case risk cards，停留 Quality risk / Customer trust breaks。 |
| 第 06 幕 | 00:36-00:45 | 9s | 同上 | 解释痛点：回复质量、manager 可见性、事后复盘、回复不及时。 | 横向平移或逐个高亮四个痛点。 |
| 第 07 幕 | 00:45-00:55 | 10s | `phase1_video/2-why-now.png` | 展示 Zendesk 续费约 73k USD/year。 | 慢慢放大到 `73k`。 |
| 第 08 幕 | 00:55-01:05 | 10s | `phase1_video/2-why-now.png` | 展示更大的问题：feature、workflow、data mining、quality analytics 定制空间不足。 | 高亮 `Custom` 或 customization 区域。 |
| 第 09 幕 | 01:05-01:15 | 10s | `phase1_video/3-big-pic.png` | 展示客户入口不变：Customer / Zendesk 层。 | 高亮 Customer -> Zendesk。 |
| 第 10 幕 | 01:15-01:25 | 10s | `phase1_video/3-big-pic.png` | 展示 SupportPortal Core：routing、assignment、guardrail、approve、dashboard。 | 镜头下移到 SupportPortal Core。 |
| 第 11 幕 | 01:25-01:35 | 10s | `https://support.stellarix.space/assignment` 截图 | 展示 engineer 如何接工单。 | 截 `/assignment`，轻微平移过工单列表和详情。 |
| 第 12 幕 | 01:35-01:45 | 10s | `phase1_video/4-admin.png` 或 `https://support.stellarix.space/assignment/admin` | 展示 manager 如何配置 assignment：班次、规则、fallback。 | 鼠标/高亮扫过 shift、rules、manual override。 |
| 第 13 幕 | 01:45-01:53 | 8s | `phase1_video/showcase-guardrail-demo.mp4` | Guardrail demo 前半：工程师写 bad draft，AI 拒绝。 | 放 mp4，保留右侧进度。 |
| 第 14 幕 | 01:53-02:05 | 12s | `phase1_video/showcase-guardrail-demo.mp4` 或 `phase1_video/7-show-case.png` | Guardrail demo 后半：补充 Web SDK log：`[websdk] no input frame received`，然后生成保守 customer draft。 | 继续 mp4，最后停一下 safe draft。 |
| 第 15 幕 | 02:05-02:16 | 11s | `phase1_video/12-dashboard.png` | 展示 Dashboard：管理从事后 review 变成实时质量控制。 | 慢慢平移 Dashboard 指标。 |
| 第 16 幕 | 02:16-02:25 | 9s | `phase1_video/12-dashboard.png` | 展示新指标：guardrail pass rate、agent interaction count、first-contact resolution。 | 逐个加关键词字幕。 |
| 第 17 幕 | 02:25-02:43 | 18s | `https://support.stellarix.space/account` 截图 | 展示 account/billing case 可以自动处理，不需要 engineer in the loop。 | 截 `/account`，平移表单、分类、状态。 |
| 第 18 幕 | 02:43-02:55 | 12s | `https://support.stellarix.space/roadmap/phase1.html#rnd-investigation` 截图 | 展示 R&D Agent 如何通过证据判断不是异常断开，而是 `Client.unpublish`。 | 高亮 `Client.unpublish` 和 conclusion。 |
| 第 19 幕 | 02:55-03:00 | 5s | `phase1_video/11-phase1-closing.png` | 结尾：AgentRelay 产品化/协议化，agent 代表个人或团队。 | 最后一帧稳定停留，不要快切。 |

## 素材清单

- `phase1_video/1-intro.jpeg`
- `phase1_video/bad-case-support-failure-demo.mp4`
- `phase1_video/2-why-now.png`
- `phase1_video/3-big-pic.png`
- `https://support.stellarix.space/assignment`
- `phase1_video/4-admin.png`
- `https://support.stellarix.space/assignment/admin`
- `phase1_video/showcase-guardrail-demo.mp4`
- `phase1_video/7-show-case.png`
- `phase1_video/12-dashboard.png`
- `https://support.stellarix.space/account`
- `https://support.stellarix.space/roadmap/phase1.html#rnd-investigation`
- `phase1_video/11-phase1-closing.png`
