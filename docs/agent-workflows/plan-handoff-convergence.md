# 计划交接收敛计划：交付与验证

修订：v1，2026-10-08。范围为开发工具与协作规则；不改变应用行为、模型配置、业务审批或部署权限。规划、执行、验收可以由 Codex、ZCode 或其他客户端承担，独立性由是否参与实现决定。

## 当前修订 v2：交接证据闭环

本修订把知识库第一阶段暴露的剩余问题落到共享 `implementation-handoff` Skill 和 PR 交接格式中，保持 Codex、ZCode 和其他客户端的角色中立。新增内容位于：

- `.codex/skills/implementation-handoff/references/implementation-plan-template.md`：可独立转交的计划模板；
- `.codex/skills/implementation-handoff/references/planning.md`：代表性真实路径、状态矩阵、测试载具证明、证据分层和停止点；
- `.codex/skills/implementation-handoff/references/execution.md`：执行前基线恢复、载具自检、故障注入、真实入口和等待证据规则；
- `.codex/skills/implementation-handoff/references/repair.md`：合同缺口、实现问题、载具问题、证据缺口、外部等待和人工决策的分类；
- `docs/agent-workflows/pr-handoff.md`：PR body 的固定交接顺序和未跟踪验证资产门禁。

本修订的验收边界是流程文件和共享 Skill 的一致性检查，不改变应用行为，不部署，不要求所有任务新增独立验收。后续三个适用任务按当前模板记录首次验收发现、实质返修次数和等待证据轮次，再决定是否需要增加自动检查工具。

## 规则放置与迁移

| 原有内容 / 问题 | 当前维护位置 | 保留或调整 |
| --- | --- | --- |
| 冗长的全局规划与执行段落 | [全局模板](global-agents.md) + [交接 Skill](../../.codex/skills/implementation-handoff/SKILL.md) | 删除 low-thinking 默认；规划侧核实入口、持久化与恢复合同；分模式按需读取 |
| 独立判断、只读授权、回复风格、MCP/记忆规则 | 全局模板 | 压缩重复表述；原 Codex MCP Policy / Memory 两节原文保留；两客户端对齐 |
| 需要人决定却只写入交接报告 | 全局、项目入口与 Skill | 每个角色直接提问“需要你确认：任务已暂停在 ...”，暂停依赖动作；没有答复不视为批准 |
| 工作区/合码纪律重复散落 | [AGENTS](../../AGENTS.md) + [工作流细则](../agent_workflow_details.md) | 保留 root clean main、任务归属、PR squash、显式 cleanup、CodeGraph/CodeSight |
| ECS、Production 热修复例外与环境边界 | 项目入口 + 工作流细则 | Preproduction 优先，Production 另授权；热修复细则完整迁移，无放宽 |
| 交接版本不固定、逐轮报告互相覆盖 | [PR 流程](pr-handoff.md) | 同一任务一条分支/PR，追加返修提交；当前计划、合同/问题 ID、证据与完整 HEAD |
| 反复用单个字符串反例修补 | Skill 的 repair 模式 + [验收 Skill](../../.codex/skills/review-implemented-plan/SKILL.md) | 首轮检查受影响边界；复审未关闭项/新 diff；复发时重建合同，不重开无关已关闭项 |
| 验收后 main 刷新或验证改变源代码 | finalize 的 `--reviewed-head` | 刷新后、验证后检查完整 SHA 与 tracked diff；漂移在推送/合并前失败，参数不替代独立批准 |

文档、代码、部署和业务验收仍分层；等待自然样本不算新的代码返修。不增加普遍独立验收门禁；用户明确跨线程实施后交回验收时才选择对应门禁，原门禁持续有效。

## 本机安装与维护

- 版本源：本仓库 `docs/agent-workflows/global-agents.md` 与 `.codex/skills/implementation-handoff/`。
- 已安装全局入口：`~/.codex/AGENTS.md`、`~/.zcode/AGENTS.md`，与全局模板逐字节一致。
- 已安装共享 Skill：`~/.agents/skills/implementation-handoff/`，两个客户端共用；不分别手改两套 Skill。项目验收 Skill 仍由仓库维护，入口可显式按路径读取。
- 后续修改须在任务工作区维护版本源，经授权后同步上述本机位置并比较内容。安装副本不会自动随 Git 更新；发布此类规则的执行侧负责同步和读回。其他机器使用相同内容时需调整全局模板中的本机绝对路径。
- 原全局规则备份：`~/.codex/instruction-backups/plan-handoff-convergence-20261008/{codex,zcode}-AGENTS.md`。回滚只恢复本任务文件；已有会话可能保留旧上下文，后续任务应新开会话或显式读取当前规则。

| 常驻入口 | 原字节数 | 当前字节数 | 减少 |
| --- | ---: | ---: | ---: |
| Codex 全局 AGENTS | 15543 | 9362 | 40% |
| ZCode 全局 AGENTS | 12488 | 9362 | 25% |
| 项目 AGENTS | 17500 | 9315 | 47% |

这是入口字节数对比，不等于模型 token 节省率；详细合同仍可按需读入。

## 验证记录

验证工作区：`codex/plan-handoff-convergence`，起点 `f81fe3f32651d6742daa11d0708ca23348195562`。合并提交与最终检查以此 PR 记录为准。

- 工作流测试使用临时真实 Git 仓库和 bare remote，GitHub/CodeGraph 边界替身，不操作真实业务环境。覆盖 main 推进、验证期间新 commit、验证期间 tracked edit、短 SHA 拒绝、正确 SHA 复用同一 PR，以及未使用新参数的既有路径。
- 实跑命令：根工作区 `.venv/bin/python -m pytest backend/tests/test_workflow_scripts.py -k finalize -q`（cwd 为本任务 worktree），结果 **16 passed，47 deselected**；未用未运行的应用测试总数替代此结果。
- 两条原测试期待 finalize 自动删除工作区，在未修改 main 上同样失败；按现行 finalize/cleanup 分离合同修正，正例实际调用独立 cleanup 并断言工作区/分支删除。清理测试使用仓库内 `.worktrees/`，与真实入口要求一致。
- Skill `quick_validate.py`、脚本 `bash -n`、`git diff --check`、相对文档路径检查及安装副本一致性检查均纳入交付验证。应用部署/业务测试不适用于本次改动。
- ZCode 0.16.9 的 `skills list --json` 在仓库外发现共享 Skill，诊断为空。实际模型探针在现有认证下返回 HTTP 401，**未验证 ZCode 模型对规则的执行效果**；未修改登录或模型配置。CLI 启动显式使用现有 bundled/personal provider 文件路径解决打包入口定位问题，环境变量仅作用于探针子进程。
- Codex 使用既有模型配置，read-only、ephemeral 会话，四个隔离案例：重大留存变更须问人、普通局部修复不重复请示、验收后幂等逻辑变化需定向复审、重试测试不能补回生产丢失的 ID。首轮发现把审查 SHA 当成用户版本 pin 而多问一次的问题，已针对性补清两者区别后复测。
- Codex 复测四项判断均符合合同，实际读取全局 AGENTS、共享 Skill 及 execution/repair 引用；第三项已改为准备定向复审并暂停合并，没有再向人申请批准范围内复审。此项为行为样本观察，不是跨模型稳定性统计。
- 探针输入及脱敏结果保存在上述备份目录的 `evaluation/`；不把运行时 provider 错误原文或敏感配置写入仓库。

## 如何观察是否减少往返

接下来三个适用的真实任务，在原 PR 证据中顺带记录首次验收结果、实质返修次数和原因（计划合同遗漏、实现偏差、证据缺口、范围变化）。不新增日报或自动调度。等待外部证据、非阻断文字调整不计返修；发现的新真实缺陷仍须处理。根据重复原因修正规则，避免把每个个例累积成长清单。

有限探针只能证明所测场景，尚不能声称真实交接次数或首次成功率已经改善。
