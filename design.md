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
   - `/workspace/admin`
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

### 5.7 Shared Rich Composer
1. `/client` 和 `/engineer` 的富文本输入框必须共用同一套 `shared-ui/composer` 实现，禁止在两个应用中长期维护分叉的 toolbar、markdown、selection 和 code-block 编辑逻辑。
2. 共享 composer 的基础 toolbar 动作固定为 `bold`、`italic`、`list`、`code-block`、`attach`，顺序固定，不得为 engineer 再造一套独立按钮体系。
3. `AI Summary` 不是共享 composer 的内建动作；它只能作为 client 专属的可选辅助槽位出现在 toolbar 右侧。
4. 共享 composer 的发送按钮视觉、圆角、阴影、hover / active / focus-visible / disabled 状态必须在 client 与 engineer 两端保持一致；client 的 `stop` 态可以作为例外扩展。
5. 代码块必须在编辑器内自动拥有上下普通输入行，便于继续书写自然文本；这些 spacer line 仅存在于编辑 DOM，不能污染最终 markdown payload。
6. 共享 composer 输出的 markdown subset 只允许安全 inline / list / fenced code 结构；发送后线程展示也必须使用同一套安全渲染器，避免一端富文本、一端原始 markdown 的割裂体验。

### 5.8 Ticket Identity
1. SupportPortal 现在存在两种一等身份：
   - `client ticket`：客户侧工单，ID 形如 `TK-040`
   - `engineer case`：工程师侧 case，ID 形如 `TK-040-1`
2. `client ticket` 和 `engineer case` 必须分开呈现，禁止在 engineer UI 中继续把 client ticket 当作 engineer 工单本身。
3. engineer case title 是创建时冻结的 unresolved-issue snapshot，例如 `black screen issue`：
   - 优先来源于客户最新问题描述、handoff 摘要和 engineer AI 初始理解
   - 不得默认复用 parent client ticket subject
   - 本轮实现中不支持人工编辑和自动改名
4. engineer case header 的信息层级固定为：
   - 主身份：`engineer_case_id`
   - 主标题：`engineer case title`
   - 次级引用：`Client Ticket <ticket_id> · <subject>`
5. client UI 只显示 client ticket 自己的 ID 和标题，不得显示 engineer case suffix、engineer case title、或任何 engineer-only linkage 字段。

## 6. Surface Guidance

### 6.1 Client UI
1. 更强调温和、清晰、可提问。
2. AI 回复区可以带 citation、建议动作、状态进度。
3. 高情绪提示要克制，不制造额外焦虑。
4. client ticket 的标题和 ID 是客户可见的唯一工单身份；即使后台已创建 linked engineer case，client 页面也不得暴露 engineer case 编号或 engineer-only metadata。

### 6.2 Engineer UI
1. 更强调控制感、优先级、证据链。
2. 工单池与工单详情都应保留“编辑感 + AI 辅助感”。
3. 人工接管和 AI 管理状态必须一眼可见。
4. engineer pool 和 detail 必须以 `engineer case` 为主身份，而不是 parent client ticket：
   - pool card primary kicker 使用 `engineer_case_id`
   - primary title 使用 `engineer case title`
   - `Client Ticket <ticket_id> · <subject>` 作为次级引用信息
5. engineer detail 的 `Customer Timeline` 必须继续展示 parent client ticket 的公开对话，但 header 不能因此回退成 client ticket title。
6. `/engineer` 是 legacy Engineer UI；Controlled Launch 的正式工程师入口是 `/workspace`，管理入口是 `/workspace/admin`。
7. `/workspace` 只展示系统派发给当前账号的 Engineer Case，不提供 claim、take ticket、ready to claim 或任何人工抢单控件。
8. `/workspace/admin` 必须分别展示：
   - Client Ticket status：`open / communicating / escalated / investigating / resolved`
   - Engineer Case assignment status：`pending / assigned / resolved`
9. assignment status 只能读取后端 `assignment_status`，不得根据 Client Ticket status 或 `assigned_engineer_id` 是否为空在前端推导。
10. engineer 的 dispatch availability 完全由数据库 schedule 计算：账号必须为 active 且当前命中班次；不得维护独立 available/unavailable 状态、reason 或人工开关。
11. Engineer Case 的 SLA 使用后端 `assigned_at / sla_due_at`，不得使用浏览器本地时间或 localStorage 创建 SLA 起点。
12. `/workspace` 未登录页面采用轻量事务型登录壳：顶部只展示 `Workspace` 品牌，主区居中展示欢迎文案与单一登录卡，底部展示安全工作区信息；不得复用登录后的 rail 或工单工作区结构。
13. `/workspace/admin` 未登录页面复用同一事务型登录壳，顶部品牌固定为 `Admin`，主区文案说明账号、班表、派单与 SLA 管理范围；登录后的管理控制台结构不受此规则影响。
14. `/workspace/admin` 登录后的 rail 在桌面默认使用 `96px` icon-only 状态，hover 或 focus-within 时展开到 `264px`；当前账号与 Logout 固定放在 rail footer，不再放入全局顶栏。
15. `/workspace/admin` 将工程师状态管理与完整班表拆成相邻工作面：`Engineer Management` 固定展示 `On Schedule Now` 和全量 `Engineer Schedules` 名单；独立 `Schedule` tab 展示 `Weekly Schedule` 时间网格与右侧 shift inspector。
   - Engineer Management 的每位工程师只展示 on/off-schedule，并提供 `Modify Schedule` 入口；入口切换到 `Schedule` tab 后直接打开对应 shift inspector。不得出现独立 Availability、Reason 或人工 availability 开关。
   - `Weekly Schedule` 固定使用横轴 Monday-Sunday、纵轴 `00:00-24:00` 的时间网格，不得退回“工程师为行”的文本表格；每小时横向拆成两个固定 30 分钟 slot。
   - 每个整点标签与对应 `:00-:30` slot 垂直居中，时间标签与姓名块必须来自同一 CSS grid row，不得再用 transform 修补坐标。
   - 班次按 30 分钟 slot 逐格显示工程师姓名，不使用跨多个格子的连续填充块；跨夜班次拆分到相邻两天，同一 slot 的重叠工程师必须横向并排且可分别操作。
   - Schedule inspector 使用有限的小时与分钟选择器：Start hour 为 `00-23`，End hour 为 `00-24`，分钟仅 `00/30`，`24` 仅能搭配 `00`；不得使用循环滚动的原生 time picker。
   - Schedule 保存必须在首次提交时立即显示 `Saving schedule...`、锁定重复提交并通过单一 schedule 请求完成；成功和失败都必须提供可见反馈，保存期间不得清空 Admin rail。
   - 网格完整展开 24 小时高度，由页面主滚动条负责纵向浏览；网格内部仅在窄屏支持横向滚动，时间列在横向滚动时保持可见，且不得造成页面级横向溢出。
   - 每个在班半小时格只显示工程师姓名，并使用按内容收缩的蓝色胶囊，不得横向铺满整个 slot 或 overlap lane；胶囊必须限制在所属 lane 内，长姓名使用省略号。完整日期与半小时范围通过可访问名称提供，点击姓名胶囊可打开对应 shift inspector。
   - Admin 工作面的品牌与交互主色继续使用 `primary / primary-fixed / secondary` 蓝色体系；不得以青绿或绿色替代导航、班次、头像和成功反馈的主色。
16. `On Schedule Now` 表达当前时间命中数据库 schedule，也是唯一的实时 dispatch availability；不能命名为 `Online Engineers`，也不能推导浏览器连接或 presence。
17. 新账号创建使用独立邀请任务页：Engineer Management 只保留 `New Account` 命令，邀请页收集 email 与冻结角色，并覆盖 sending / success / error 状态。
   - setup 页面将邀请 email 作为唯一账号身份，Email 输入框由已校验的邀请记录自动填充并保持只读；页面和登录表单不得再向用户展示 `Account ID`。
   - setup 提交不得接受客户端另行指定账号身份；后端必须使用邀请记录中的规范化 email 创建账号。数据库内部可继续保留 `account_id` 作为兼容主键。
   - Workspace 与 Admin 登录表单统一使用 `Email` 标签；现有无 email 的 legacy 账号可继续通过原内部 ID 登录，但新邀请账号使用 email 登录。
18. Admin 响应式 rail 规则固定为：`>= 901px` 支持 hover/focus 展开，`721px - 900px` 保持 icon-only，`<= 720px` 退化为顶部静态导航并显示必要标签与账户操作。
19. Admin rail 不显示浏览器滚动条；内容超出时仍须保留滚轮、触控板、触摸与键盘滚动能力，移动端顶部导航同样隐藏滚动轨迹。
   - Rail 的品牌、导航和 Logout 不得依赖远程图标字体才能识别。只有所需 Material Symbols glyph 完整加载并校验后才进入 icon-ready 状态；刷新、字体 pending、加载失败或状态异常时必须持续显示稳定的短标签 fallback，不能出现空白导航框。
   - Rail 刷新或重渲染时，桌面与平板的 sidebar body 横向位置必须归零；active item 居中只允许调整移动端顶部导航自身的 `scrollLeft`，不得使用会把桌面 rail 内容卷出可视区域的 `scrollIntoView()`。
20. `/workspace` 登录成功后必须先进入 Engineer Workspace 首页，不得自动打开已派发 case；只有工程师点击 `I'm ready to roll` 后，系统才检查并打开下一条 assigned Engineer Case。首页欢迎区保持紧凑，只展示 `Engineer workspace`、动态 `Welcome back, {name}` 与该准备按钮，不得重复展示 assignment 标题或说明文案。
21. Engineer Workspace 首页必须只读展示当前账号的 weekly schedule、当前是否命中 schedule 和 schedule timezone；dispatch availability 直接跟随当前 schedule，排班编辑仍只允许在 `/workspace/admin` 完成。
22. 个人 weekly schedule 使用 Monday-Sunday 的横向扫描布局：每天显示真实起止时间，无班次显示 `Off`，跨夜班次明确标记次日结束；窄屏允许区域内横向滚动，不得造成页面级横向溢出。
23. `/workspace` 登录后的 Ready 首页、准备态和 Engineer Case 详情统一使用无侧栏的单工作面与 sticky 顶部栏：左侧固定展示 Workspace icon/品牌，右侧展示当前用户与 Logout；移动端保留 icon、用户名和 Logout，不得退回 rail、抽屉或悬浮在内容上的账户控件。
24. `/workspace` 与 `/workspace/admin` 必须使用独立的浏览器会话存储命名空间；Engineer 入口只接受 `role === "engineer"`，Admin 入口只接受 `role === "admin"`。角色不匹配时必须显示对应入口的登录页，Logout 与 401 清理不得影响另一入口的登录状态。
25. `/workspace/admin` 的 Engineer Case 工作面固定使用 `Pending Assignment`、`Assigned`、`Resolved` 三个 assignment tab，并直接按后端 `assignment_status` 分组：
   - `Pending Assignment` 列为 `ID / Subject / Status / Requester / Priority`。
   - `Assigned` 与 `Resolved` 列为 `ID / Subject / Status / Requester / Priority / Assignee`。
   - `Status` 表达 Client Ticket status；当前 assignment status 由选中的 tab 表达，不再重复为表格列。
   - 未提供 Priority 时显示明确空值，不得在前端推导或伪造优先级。

### 6.3 Ticket Dashboard (`/dashboard`)
1. 固定 KPI 名称：
   - `Today Ticket Volume`
   - `Resolution Rate`
   - `Sentiment Alerts`
2. 左侧导航顺序固定为：
   - `Ticket Ops`
   - `RAG Benchmark`
   - `Ticket Details`
3. `Ticket Details` 必须是默认展开的分组，而不是独立 overview tab；其二级状态顺序固定为：
   - `investigating`
   - `escalated`
   - `communicating`
   - `resolved`
4. `Ticket Ops` 保留 overview / KPI / queue health / throughput / summary surfaces；工单列表不再使用单独的 live stream section 展示。
5. 选中 `Ticket Details` 的任一状态后，主区应切成该状态的 full-page ticket board，并复用现有 ticket 数据，不得改写 ticket ops 数据契约。
6. `Ticket Details` 状态页不得继续显示顶部 `Admin Operations` hero；该 hero 仅属于 `Ticket Ops` overview。
7. `Ticket Details` board 必须提供与 engineer 端同语义的 `List / Grid` view toggle，但仅影响 dashboard-local 呈现；不复制 engineer 端的 `localStorage` 持久化。
8. `Ticket Details` 的默认 board view 固定为 `Grid`；同一页面内切换状态时可以保持当前 view，但刷新页面后必须回到 `Grid`。
9. dashboard 内允许打开只读 ticket detail overlay，用于查看上下文、调查摘要和近期工单进展；工单处理动作仍留在 `/engineer`。
10. 必须存在前往 `/dashboard/rag/` 的明确入口，并以 rail 中的 `RAG Benchmark` 作为主导航入口。
11. dashboard rail footer 只允许保留两个入口：
   - `Realtime`
   - `Logout`
12. `Realtime` 和 `Logout` 必须复用与主导航一致的 rail item icon slot、padding、collapsed/expanded 对齐和 reveal 规则；dashboard 中不得展示用户头像、用户名、角色。
13. dashboard 收起态下，顶部 `Concierge AI` brand icon 也必须与 rail 导航 icon 共用同一条中心线；隐藏 brand 文案时不得留下额外 gap。
14. dashboard rail 在桌面和平板必须锚定到 viewport 高度，不得因为主区内容变长而被拉高；滚动长页面时 rail 本身保持在视窗内，必要时仅 rail 内部滚动。
15. `Realtime` 和 `Logout` 继续作为 rail footer，底部锚定但必须比当前更靠上；不得掉出首屏可视区。
16. 响应式规则固定为：
   - `>= 901px`：桌面竖向 rail，允许 hover 展开
   - `721px - 900px`：固定竖向 rail，保持 collapsed icon-only 状态，不依赖 hover reveal
   - `<= 720px`：回到顶部静态 rail，labels 全显
17. 页面重点是：
   - queue health
   - categorized ticket visibility
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
13. benchmark session 级别的 `Session Runs / Run History / Run Comparison / changelog` 面板只允许出现在 `Scorecard`。除 `Scorecard` 外，其余 benchmark 页面只保留顶部 `Current Benchmark Run` selector 作为当前 run 语境，不再重复渲染 session 级摘要或跨-run 对比。
14. 只有各页面顶部 `sections.summary.cards` 渲染出的 summary metric card 允许在指标名右侧放 inline help `?`；tooltip 必须使用自定义气泡而不是浏览器原生 `title`，支持 hover / focus / tap，并保持明显 focus ring 和不少于 `44x44px` 的可点击目标。

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

### 6.6 Client Preview Post-Send Correspondence Exception
1. 适用于 `/clienttest` 在 `New Ticket` 首条消息发送后的 correspondence detail shell，以及 `/client2` 的所有非 draft ticket detail。
2. 该页面允许恢复截图式 breadcrumb + issue title header，并改用自然高度的 thread / composer / sidebar 布局，不强制沿用 draft 态的 fixed-height section 规则。
3. 该页面允许使用低对比度边框消息卡与轻色 header band，作为 correspondence card 的视觉例外；边框必须保持柔和，不得退化成高对比后台表格风格。
4. 左侧 rail 必须继续保留，禁止引入截图中的顶栏导航。
5. `/client2` correspondence detail 中的 `Knowledge Base Articles` 卡允许继续使用 draft sidebar 的固定卡高，以保持 knowledge rail 一致；内容超出时在卡内滚动，不得把整页 sidebar 撑高。
6. `/client2` correspondence detail 的 header meta 允许在 `Updated` 右侧追加 reply ETA chip，但只允许在最新一条可见消息来自客户时显示；agent 最新发言时必须隐藏。该 chip 对所有 eligible statuses 的 baseline next-update SLA 固定为 20 minutes，不按 status 拆分。展示文案规则固定为：`< 60 min` 显示 `Next update in MM:00`，`>= 60 min` 显示 `Next update in Hh MMm`。
7. 该例外不适用于 `/client`、`/clienttest` draft 页面、`/client2` draft 页面，或 `/clienttest` 的其他 ticket detail 页面。

### 6.7 Account Automation Admin Surfaces
1. `Automated Cases` 使用单行 metric strip 加紧凑 case table；占比文案必须明确表示 routed-to-Automated，不得暗示已解决。
2. `Route & Prompt` 使用纵向 route timeline 和独立等宽 Prompt inspector。历史记录缺少 snapshot 时显示明确 unavailable state，deterministic route 显示未使用 LLM。
3. `Persona Prompt Template` 使用左右 workspace：Persona/version 列表作为上下文，编辑器作为主工作区；Draft、Publish、Rollback 必须通过文字状态与确认动作区分。
4. `Environment Config` 只能显示可搜索的配置名 inventory，并为每个配置名显示一条仅由 key 名决定的静态用途说明；说明不得读取或推断 value、set/unset、长度、哈希或来源。每行使用独立的 copy-name icon command，搜索同时匹配 key 和用途说明。
5. 上述页面沿用 Admin 的低对比 surface 和紧凑信息密度；移动端改为单列，不允许 Prompt、key 或 route label 横向溢出。

### 6.8 Account Ticket Conversation (`/account`)
1. 所有 account ticket 都必须先展示 route 结果，再由 AI 尝试生成仅在 `/account` 内可见的回复；不得把 AI draft 或 assistant message 回传到来源 Zendesk / 客户邮件渠道。
2. 新 ticket 和每次客户补充后的 AI 回复统一随机延迟 6–10 分钟。等待期间使用紧凑的 scheduled status row，不创建空 assistant bubble，也不展示 draft 正文。
3. Conversation 中每条客户与 AI 消息都必须展示服务端 timestamp；UI 使用本地化日期、时间和时区，原始 ISO 时间保留在 `time[datetime]`。
4. scheduled status row 必须保持固定最小高度，通过轻量 pulse 表达处理中；`prefers-reduced-motion` 下取消动画，状态变化不得引发布局跳动。
5. 同一规范化缺失字段在同一 ticket 中最多询问一次。客户未回答时不得换措辞重复追问；后续状态显示继续处理或 manual attention。
6. `manual_attention / failed` 必须作为文字状态显示并使用 `aria-live="polite"`，不能只靠颜色。客户在等待期间追加消息时，旧 scheduled reply 必须取消并由最新上下文替代。

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
