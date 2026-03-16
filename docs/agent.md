# UI 统一规范（Agent 规范源）

## 适用范围与优先级
1. 本文件适用于本地 POC 三端界面：`/client`、`/engineer`、`/dashboard`。
2. 本文件是 UI 唯一规范源，若与其他文档冲突，以本文件为准。
3. 本文件用于实现约束、测试核对与阶段闸门（特别是 P4-T3）。

## 设计令牌（Design Tokens）

### Color
| Token | Value | Usage |
|---|---|---|
| `--bg-page` | `#F4F6F8` | 页面背景 |
| `--bg-card` | `#FFFFFF` | 卡片背景 |
| `--text-primary` | `#1F2937` | 主要文本 |
| `--text-secondary` | `#6B7280` | 次要文本 |
| `--border-default` | `#D1D5DB` | 边框 |
| `--brand-primary` | `#0B5FFF` | 主按钮、关键操作 |
| `--status-success` | `#0F9D58` | 成功态 |
| `--status-warning` | `#F59E0B` | 处理中/预警 |
| `--status-danger` | `#D93025` | 高优先级/错误 |
| `--focus-ring` | `#2563EB` | 焦点描边 |

### Typography
| Token | Value | Usage |
|---|---|---|
| `--font-family` | `"Noto Sans SC","PingFang SC","Helvetica Neue",Arial,sans-serif` | 全局字体栈 |
| `--font-size-h1` | `28px` | 页面标题 |
| `--font-size-h2` | `22px` | 区域标题 |
| `--font-size-body` | `16px` | 正文 |
| `--font-size-caption` | `13px` | 辅助信息 |
| `--line-height-base` | `1.5` | 默认行高 |

### Spacing
| Token | Value | Usage |
|---|---|---|
| `--space-1` | `4px` | 最小间距 |
| `--space-2` | `8px` | 紧凑间距 |
| `--space-3` | `12px` | 常规控件内距 |
| `--space-4` | `16px` | 卡片内距 |
| `--space-5` | `24px` | 区块间距 |
| `--radius-sm` | `6px` | 输入框/标签 |
| `--radius-md` | `10px` | 卡片 |

## 三端组件规范

### 通用组件
1. `Button`：
   - 变体：`primary`、`secondary`、`danger`。
   - 高度：`40px`，水平内边距：`16px`。
   - 禁用态透明度：`0.5`，不可点击。
2. `StatusBadge`：
   - 状态：`processing`、`resolved`、`handoff`、`high-priority`。
   - 使用语义色：warning/success/secondary/danger。
3. `Card`：
   - 背景 `--bg-card`，圆角 `--radius-md`，边框 `1px solid --border-default`。
4. `Table`：
   - 表头与内容左对齐。
   - 行高最小 `44px`。
   - 支持空状态提示。

### 客户端（/client）
1. 顶部固定标题：`AI Ticket System`。
2. 必有输入区、回答区、进度区。
3. 进度区状态词：`Processing`、`Escalated`、`Resolved`。

### 工程师端（/engineer）
1. 工单卡片必须显示：`Priority`、`Ticket ID`、`Customer`、`Issue`、`Context`。
2. 操作按钮顺序固定：`Processing`、`Resolved`、`Handoff`、`View Full Conversation`。
3. 高优先级工单卡片左侧必须有 danger 色强调条。

### 看板端（/dashboard）
1. KPI 卡片固定三项：`Today Ticket Volume`、`Resolution Rate`、`Sentiment Alerts`。
2. 实时流区域展示最近事件，按时间倒序。
3. 趋势变化符号使用 `+/-` 与颜色联合表达，不能仅依赖颜色。

## 交互状态规范
1. `loading`：显示骨架屏或 `Loading...`，禁止空白等待。
2. `error`：显示错误信息与重试入口。
3. `empty`：显示空状态文案和下一步建议。
4. `success`：显示成功提示并可追踪到相关工单。
5. WebSocket 中断：显示 `Disconnected` 状态并自动重连。

## 可访问性（A11y）
1. 键盘可达：
   - 所有交互控件可通过 `Tab` 访问。
   - 焦点样式使用 `--focus-ring`，不可移除。
2. 对比度：
   - 正文字体与背景对比度至少 `4.5:1`。
3. 语义化：
   - 输入框关联 `label`。
   - 主要区域使用 `main/section` 语义标签。
   - 状态更新区域使用 `aria-live="polite"`。
4. 图表与趋势：
   - 看板图形须提供文本替代信息。

## 性能预算
1. 首屏加载时间：
   - `/client`、`/engineer`、`/dashboard` 各自 `<= 2s`（本地环境）。
2. 静态资源预算：
   - 单端 JS 总体积建议 `<= 500KB`（未压缩上限）。
3. 实时刷新：
   - 看板实时流更新不应阻塞主线程超过 `100ms`。

## UI 测试清单与闸门规则

### Checklist（用于 P4-T3）
1. 设计令牌是否统一引用（颜色、字体、间距）。
2. 三端主按钮样式是否一致。
3. 高优先级状态是否具备文字 + 颜色双表达。
4. loading/error/empty/success 四种状态是否完整。
5. 键盘导航是否可覆盖所有关键操作。
6. 三端首屏加载是否均满足 `<= 2s`。
7. 工程师端工单动作顺序是否符合规范。
8. 看板 KPI 三项是否齐全且命名一致。

### 闸门判定
1. Checklist 任一 P0 项失败：P4-T3 判定 `Failed`。
2. Checklist 任一 P1 项失败：P4-T3 判定 `Failed`，需修复后重测。
3. P4-T3 通过前，不允许进入 Phase 5。

## 变更流程
1. 任何 UI 规范变更必须先更新本文件，再更新实现代码。
2. 变更记录必须同步到 `docs/poc_progress.md` 的每日进展日志。
3. 若变更影响既有测试用例，必须同步更新测试登记表中的 Expected 字段。
4. 对关键令牌（颜色、字体、间距）变更必须触发 P4-T3 回归测试。
