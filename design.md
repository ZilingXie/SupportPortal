# SupportPortal Design Language

## 1. 状态与适用范围
1. 本文件是 SupportPortal 前端设计语言的唯一规范源，适用于 `ui/` 下新增或重构的所有界面。
2. 本规范基于以下样例归纳：
   - `ui/stitch_client_ui/`
   - `ui/stitch_dashboard_ui/admin_ops/`
   - `ui/stich_engineer_ui/engineer_ticket_pool/`
   - `ui/stich_engineer_ui/active_ticket_detail/`
3. 当前强制适用范围：
   - `/client`
   - `/engineer`
   - `/dashboard`
   - `/dashboard/rag/`

## 2. Creative North Star
1. 设计北极星：`The Intelligent Concierge`。
2. 目标不是做“机械数据库式后台”，而是做“AI 协同、信息有策展感、运维有控制感”的工作台。
3. 页面气质关键词：
   - Editorial authority
   - Calm control
   - AI-guided, not AI-noisy
   - Premium operational workspace
4. 视觉落地原则：
   - 用层次和留白表达结构，不靠密集实线分割。
   - 用色彩语义区分 `Stability` 和 `Action`。
   - 让 AI 相关模块像“覆盖在系统之上的透镜”，而不是普通卡片。

## 3. Design Tokens

### 3.1 Color
| Token | Value | Usage |
|---|---|---|
| `--surface` | `#f6f9ff` | 页面主背景 |
| `--surface-bright` | `#f6f9ff` | 明亮工作区背景 |
| `--surface-container-low` | `#eff4fb` | 一级卡片背景 |
| `--surface-container` | `#eaeef5` | 二级容器背景 |
| `--surface-container-high` | `#e4e9ef` | 嵌套模块背景 |
| `--surface-container-highest` | `#dee3ea` | 最深层嵌套背景 |
| `--surface-lowest` | `#ffffff` | 需要最高可读性的局部面 |
| `--primary` | `#006493` | 高意图 CTA、关键 AI 行为 |
| `--primary-container` | `#00b0ff` | AI 强强调色、渐变终点 |
| `--primary-fixed` | `#cae6ff` | AI 气泡、提示背景 |
| `--secondary` | `#4c56af` | 导航、结构性框架色 |
| `--secondary-fixed` | `#e0e0ff` | citation/source truth 标签 |
| `--tertiary-container` | `#00b7cc` | resolved / positive / healthy 辅助语义色 |
| `--ink` | `#171c21` | 主文本 |
| `--ink-soft` | `#3e4851` | 次文本 |
| `--ink-muted` | `#6e7882` | 标签、辅助信息 |
| `--danger` | `#ba1a1a` | 高风险 / 错误 / 告警 |
| `--warning` | `#9f5d12` | 处理中 / 风险上升 |
| `--success` | `#006875` | resolved / healthy / positive 文本与图标 |
| `--ghost-border` | `rgba(110,120,130,0.16)` | 可感知但不抢眼的边界 |

### 3.2 Surface Philosophy
1. 使用 `Nested Depth`：
   - 页面背景在 `surface`
   - 一级卡片在 `surface-container-low`
   - 内嵌块逐步上移到 `surface-container-high` / `highest`
2. `No-Line Rule`：
   - 禁止使用高对比度 `1px solid` 实线来切页面层级。
   - 若必须给边界，使用 `ghost border` 或 tonal shift。
3. `Glass Rule`：
   - 仅 AI 透镜式模块、浮层、摘要卡允许使用半透明 + blur。
   - 普通业务表单和数据列表不使用泛滥玻璃效果。

### 3.3 Typography
| Token | Recommendation | Usage |
|---|---|---|
| `font-headline` | `Manrope` | `display` / `headline` / KPI / 核心标题 |
| `font-body` | `Inter` | 正文、表格、表单、说明 |
| `display-xl` | `48-64px / 0.95` | 首页主标题、强品牌标题 |
| `headline-lg` | `32-40px / 1.0` | 页面主 section 标题 |
| `headline-md` | `24-30px / 1.05` | 卡片标题、关键模块标题 |
| `body-md` | `15-17px / 1.55` | 正文 |
| `label-sm` | `12-13px / 1.2` | 元数据、状态标签、字段标题 |
| `eyebrow` | `12px uppercase` | 小节前导标签、氛围说明 |
1. `Manrope` 只给真正需要“编辑感”和“权重感”的层级。
2. `Inter` 负责可读性，不要把页面全部换成 headline 字体。
3. 禁止回退到无意图的默认 UI 字体组合，如纯 `Arial` / 纯系统栈。

### 3.4 Spacing / Radius / Shadow
| Token | Value | Usage |
|---|---|---|
| `space-1` | `4px` | 极小间距 |
| `space-2` | `8px` | 紧凑标签/控件间距 |
| `space-3` | `12px` | 常规内部间距 |
| `space-4` | `16px` | 模块基础内边距 |
| `space-5` | `24px` | 卡片标准内边距 |
| `space-6` | `32px` | 关键 section 间距 |
| `space-7` | `48px` | 英雄区或大块留白 |
| `radius-sm` | `12px` | 输入、chip、紧凑控件 |
| `radius-md` | `18px` | 内嵌卡片 |
| `radius-lg` | `24px` | 主卡片 |
| `radius-xl` | `30px` | 大面板、hero 容器 |
| `shadow-soft` | soft only | 普通浮层卡片 |
| `shadow-float` | medium only | hero / glass / elevated CTA |
1. 深阴影只留给“真正浮起来”的元素。
2. 嵌套卡片优先用背景明度差，不要层层阴影。

## 4. Layout Principles
1. `Intentional Asymmetry`：
   - 不要求左右完全对称。
   - 重点信息区可以更宽，辅助区更窄。
2. `Breathing Zones`：
   - 关键 AI 摘要、主要 KPI、事件区周围必须有明显留白。
3. `Whitespace over Dividers`：
   - 列表和表格优先靠卡片分组与垂直间距，而不是密集横线。
4. `Architectural Frame`：
   - 导航、标题栏、结构性外框使用 `secondary` 系，而不是所有地方都刷亮蓝。
5. `AI Voice`：
   - `primary-container` 只给 AI 关键 CTA、AI 建议、AI 强提醒，不做全局默认蓝。

## 5. Component Rules

### 5.1 Button
1. `Primary Button`
   - 默认使用 `primary -> primary-container` 渐变。
   - 仅用于高意图操作。
2. `Ghost Button`
   - 半透明浅底 + ghost border。
   - 用于刷新、次级操作、局部面板行为。
3. 点击反馈：
   - 允许轻微 `translateY` 或 `scale`。
   - 不能让动画阻塞交互。

### 5.2 Status Pill / Chip
1. 必须同时表达文字和颜色，不能只靠颜色。
2. 推荐语义：
   - danger: `urgent`, `high risk`, `failed`
   - warning: `waiting`, `investigating`, `attention`
   - cool/secondary: `managed`, `queued`, `source`
   - tertiary: `resolved`, `healthy`
3. 统一使用全圆角 pill，不使用尖角状态块。
4. Support ticket 状态语义固定为：
   - `open`: 浅绿色
   - `communicating`: 冷蓝浅底
   - `escalated`: 淡红色
   - `investigating`: 浅橙 / 桃色底
   - `resolved`: 淡灰色
5. case card 的 surface 和对应 status pill / chip 必须使用同一语义色系，禁止 badge 和卡片背景使用互相冲突的状态色。
6. `client` 侧 sidebar 中的 compact history row 是唯一例外：允许保留深色 rail 语境，不强制切成浅色 status surface，但其状态 badge 仍必须遵守统一 palette。

### 5.3 Card
1. 主卡片用大圆角。
2. 内容型卡片采用 tonal surface，不用厚边框。
3. hero / AI 摘要卡允许玻璃感与更强阴影。
4. `/client` 和 `/engineer` 中的 case card 必须支持按状态切换 surface，并在 `hover`、`focus`、`active` 状态下保持原有状态色调，只做轻微提亮或 ring 强化，不能洗回纯白默认卡片。

### 5.4 Table / List
1. 数据区优先使用“卡片化列表”而不是传统高密度网格线表格。
2. 真正需要表格时：
   - 表头清晰
   - 行高不低于 `44px`
   - 提供 `empty/loading/error`
3. 不得出现满屏细边框分割的“旧式后台表格”。

### 5.5 Chat Bubble / Citation
1. AI bubble 使用 `primary-fixed` 或轻度 tinted surface。
2. User/Human bubble 使用 `surface-container-high`。
3. `Citation Chip` 使用 `secondary-fixed`，尺寸小但可点击。
4. citation 可以轻微浮出气泡边界，营造“AI lens”感。

### 5.6 AI Summary / Toggle / Workbench Controls
1. AI 摘要模块应像策略提示，而不是普通公告。
2. `AI Managing` 这类状态切换，必须清晰标明当前模式。
3. 过滤器区要稳定、可扫描，不可做成嘈杂的工具栏。

## 6. Surface Guidance

### 6.1 Client UI
1. 更强调温和、清晰、可提问。
2. AI 回复区可以带 citation、建议动作、状态进度。
3. 高情绪提示要克制，不制造额外焦虑。

### 6.2 Engineer UI
1. 更强调控制感、优先级、证据链。
2. 工单池与工单详情都应保留“编辑感 + AI 辅助感”。
3. 人工接管和 AI 管理状态必须一眼可见。

### 6.3 Ticket Dashboard (`/dashboard`)
1. 固定 KPI 名称：
   - `Today Ticket Volume`
   - `Resolution Rate`
   - `Sentiment Alerts`
2. 必有实时事件流，且按时间倒序显示。
3. 必须存在前往 `/dashboard/rag/` 的明确入口，但该入口不能盖过 ticket ops 本身。
4. 页面重点是：
   - queue health
   - live ticket signal
   - operator summary
   - escalation awareness

### 6.4 RAG Dashboard (`/dashboard/rag/`)
1. `/dashboard/rag/` 必须完整遵守本文件的色彩、字体、层级、圆角、阴影和 `No-Line Rule`，不再保留独立视觉例外。
2. 当前公共一级 taxonomy 固定为：
   - `scorecard`
   - `routing`
   - `retrieval`
   - `generation`
   - `data-supply`
   - `diagnosis`
   - `review`
3. `Scorecard` 是默认首页，必须同时呈现 `Routing / Retrieval / Generation / Business` 四层结果，而不是只放一个总分。
4. 任何 `Baseline / Candidate` 对比控件都必须把 `candidate` 选中的 `benchmark_version` 作为比较边界：`baseline` 下拉只能展示同版本 run；如果当前版本没有可切换的替代 baseline，控件必须禁用并显示明确说明，禁止静默回退。
5. 当 `Baseline / Candidate` 控件需要解释比较边界时，说明文案必须使用共享 footnote，置于两个 selector 下方，避免因为左右文案长度不同造成控件视觉错位。
6. `Data Supply` 必须在同一页面里拆成两个清晰 panel：
   - `Benchmark Supply`
   - `Knowledge Supply`
7. `Benchmark Supply` 的事实来源必须是本地 `benchmarks/*.json` 文件；dataset tables 只作为镜像和审计库存，不作为 benchmark 运行入口。
8. `Benchmark Supply` 的主 CTA 必须是 `Sync Local Benchmarks`，语义是“把本地 benchmark catalog 镜像到 dataset tables”，而不是“在线生成新 benchmark 数据集”。
9. `Benchmark Supply` 需要明确展示本地 benchmark catalog、最近 sync 结果和镜像后的 dataset inventory，避免把它设计成 dataset factory 表单。
10. 兼容页名 `experiments / datasets / knowledge-supply / production-signals` 只允许作为路由或 API alias，不得继续作为主导航文案。
11. `Routing / Retrieval / Generation` 页必须优先提供 case explorer，而不是只给 summary sample card。explorer 需要按 `错误 -> 正确` 的顺序展示，并允许在当前页直接打开详情。
12. `/dashboard/rag/` 允许并鼓励使用两种可复用工作台模式：
   - `Collapsible Case Explorer`
   - `Centered Case Detail Modal`

### 6.5 RAG Explorer Patterns
1. `Collapsible Case Explorer`
   - 适用页面：`routing`、`retrieval`、`generation`、`diagnosis`
   - 容器使用主卡片 surface，不额外叠加高对比实线分割。
   - section header 由标题、说明、计数、折叠控件组成。
   - `Routing Errors`、`Routing Correct` 默认展开。
   - `Retrieval Errors`、`Retrieval Correct` 默认展开。
   - `Generation Errors`、`Generation Correct` 默认展开。
   - `Legacy Compare Lists` 默认折叠。
   - explorer 列表优先展示问题文本、预期契约、实际契约，不复用泛化 sample marketing card。
2. `Centered Case Detail Modal`
   - 适用页面：`routing / retrieval / generation` 的快速详情。
   - 桌面端使用居中宽 modal，最大宽度需明显大于普通表单弹窗；移动端退化为全屏。
   - modal backdrop 必须可点击关闭，并支持 `Esc` 关闭、focus trap、body scroll lock。
   - modal 内容必须按纵向 section 编排，不允许重新做成密集 debug table。
   - footer 允许保留一个次级动作，进入 `Diagnosis` 全页。
3. `Shared Detail Surface`
   - `Routing` modal 和 `Diagnosis` 全页必须复用同一套 detail section 文案与顺序。
   - 推荐固定顺序：
     - 顶部摘要
     - Route Contract
     - Answer
     - Failure And Policy
     - Evidence / Trace
     - Judge / Quality
   - benchmark-only 信息在 live query 详情里必须自动隐藏，而不是显示空表格。
   - 所有标题、route bucket、tooling profile、document id、chunk id 这类长且可能无空格的字符串，必须支持 `wrap-anywhere`，禁止在 detail surface、definition grid、chip、modal header 中横向溢出或压穿容器。
4. `Diagnosis` 布局
   - 使用单列详情页，不再使用三栏并排压缩布局。
   - 顶部 chooser 使用堆叠式 collapsible sections。
   - detail sections 通过纵向间距分段，不依赖左右分栏去硬塞证据表和答案块。

## 7. States, Motion, Accessibility
1. 必须覆盖：
   - `loading`
   - `empty`
   - `error`
   - `success`
2. 所有关键控件最小点击目标 `44x44px`。
3. 所有键盘焦点必须可见，不允许移除 focus ring。
4. 状态流、事件流、toast 等动态内容使用 `aria-live="polite"`。
5. 正文对比度最低 `4.5:1`。
6. 动效原则：
   - 轻量
   - 不阻塞输入
   - 不制造 layout shift
   - exit 动画快于 enter 动画

## 8. Do / Don’t
### Do
1. 用不对称网格引导注意力。
2. 用背景层级和留白做结构。
3. 用 Manrope + Inter 建立明确层级。
4. 让 AI 模块看起来像“操作建议”，不是普通说明块。

### Don’t
1. 不要把所有按钮都刷成亮蓝。
2. 不要回到传统白卡片 + 灰边框 + 系统字体后台。
3. 不要大面积使用高对比度实线分割。
4. 不要把所有页面都做成完全一样的模板。

## 9. Implementation Rules
1. 任何新增 UI、页面改版、组件重构，都必须先对齐本文件再落代码。
2. 若现有实现需要新增 token、组件规则或页面级例外，先更新本文件，再改实现。
3. `docs/agent.md` 只是旧 UI 规范链接的遗留跳转文件，不是 Agent 指令文件，也不再单独维护规范正文。
4. `P4-T3` 的 UI 规范一致性核对，以本文件为准。

## 10. P4-T3 Checklist
1. 是否使用本文件定义的色彩、字体、圆角、阴影和层级思路。
2. 是否遵守 `No-Line Rule`，避免高对比度实线分区。
3. 主 CTA 是否克制且保留给高意图动作。
4. 状态是否同时具备颜色与文字。
5. 是否完整覆盖 `loading / empty / error / success`。
6. 是否保留明确焦点样式、键盘可达、`aria-live` 动态区域。
7. `/dashboard` 是否保留三项固定 KPI 与倒序事件流。
8. 新增页面是否与本文件一致，而不是引入新的无关视觉系统。
