window.SUPPORTPORTAL_PROJECT_DATA = {
  "schema_version": 1,
  "generated_at": "2026-08-16T12:21:46Z",
  "source_base_commit": "924e3dec99a8493fb1f006d561268bffc57fbf21",
  "registry_digest": "cbebc82f432761d9f894d0df66454910cca37642cc03b4052cb7977bc00996a0",
  "project": {
    "schema_version": 1,
    "project_id": "supportportal",
    "title": "SupportPortal",
    "status": "active",
    "goal": "建设一个可追踪、可审计、以 AI 辅助为核心的客户支持工单系统；让客户入口、自动化、工程师处理和验证证据形成同一条闭环。",
    "current_milestone_id": "phase-2-controlled-validation",
    "owner": "Zac",
    "maintainers": [
      "Zac",
      "Codex"
    ],
    "repository_url": "https://github.com/ZilingXie/SupportPortal",
    "source_policy": {
      "progress": "docs/project/tasks/*.json",
      "meetings": "docs/project/meetings/*.json",
      "capabilities": "docs/feature_list.md",
      "pr_facts": "docs/project/generated/pr-index.json",
      "pr_summaries": "docs/project/pr_summaries.json"
    },
    "legacy_pages": [
      "docs/roadmap.html",
      "docs/roadmap/meetings.html",
      "docs/roadmap/phase1.html",
      "docs/roadmap/phase2.html",
      "docs/roadmap/phase3.html"
    ],
    "report_windows_days": [
      7,
      30
    ]
  },
  "milestones": [
    {
      "schema_version": 1,
      "milestone_id": "long-term-agent-collaboration",
      "title": "长期：Engineer multi-agent + governed agent-to-agent",
      "status": "planned",
      "summary": "在真实 evidence tools、权限审计、replay gate 和成本门禁达标后，逐步扩大 AgentRelay 协作和自主调查边界。",
      "target_date": null,
      "exit_criteria": [
        "Evidence provenance、权限和 customer-safe 边界可审计",
        "Replay、成本、并发和 retry 门禁可验证",
        "自主协作不会绕过 Engineer approve、Guardrail 或关闭审计"
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/meetings.html"
      ]
    },
    {
      "schema_version": 1,
      "milestone_id": "phase-1",
      "title": "Phase 1：效率提升与工单系统基线",
      "status": "done",
      "summary": "客户入口、内部工单、Guardrail、Dashboard 和 AgentRelay communication foundation 已形成基线。",
      "target_date": null,
      "exit_criteria": [
        "Zendesk 转发和内部工单链路可用",
        "客户回复发送前存在 Guardrail 或人工确认",
        "Dashboard 能展示 Case、SLA 和审核结果"
      ],
      "source_refs": [
        "docs/roadmap/phase1.html",
        "docs/roadmap.html"
      ]
    },
    {
      "schema_version": 1,
      "milestone_id": "phase-2-controlled-validation",
      "title": "Phase 2：确定性 Automation + Controlled Validation",
      "status": "active",
      "summary": "收口 Account & Billing、Backend Operation、路由策略、回复质量和可观测性，逐步扩大可验证的自动化范围。",
      "target_date": null,
      "exit_criteria": [
        "Registered automation outcome 的路由、字段、内部处理和客户跟进都有可验证证据",
        "Automated coverage、route accuracy、失败原因和人工审核结果可持续观察",
        "敏感、低置信或失败路径明确进入 Human Review，不以 fallback 伪装成功"
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/phase2.html",
        "docs/roadmap/meetings.html"
      ]
    },
    {
      "schema_version": 1,
      "milestone_id": "phase-3-engineer-workflow",
      "title": "Phase 3：AI First Response + Slack Engineer Workflow",
      "status": "planned",
      "summary": "AI 完成首次有效回复和必要信息收集后，把 Case 交给工程师，由 Guardrail、Round Robin、Admin 和 Slack 组成受控处理链路。",
      "target_date": null,
      "exit_criteria": [
        "AI eligibility gate、首次回复和 Engineer Case 交接边界明确",
        "Slack 投递、Admin reassign 和 Round Robin 经过真实权限验证",
        "后续客户回复仍经过 Guardrail 并保留审计"
      ],
      "source_refs": [
        "docs/roadmap/phase3.html",
        "docs/roadmap/meetings.html"
      ]
    }
  ],
  "topics": [
    {
      "schema_version": 1,
      "topic_id": "account-automation",
      "title": "Account Automation",
      "goal": "让稳定的 Account 与 Billing 请求进入可解释、可暂停、可人工接管的自动化闭环。",
      "surfaces": [
        "/account"
      ],
      "components": {
        "ui": [
          "ui/account-ui"
        ],
        "api": [
          "/api/account/*"
        ],
        "services": [
          "backend/services/account_*",
          "backend/services/billing_*"
        ],
        "tests": [
          "backend/tests/test_account_*",
          "backend/tests/test_billing_*"
        ]
      },
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/phase2.html",
        "docs/feature_list.md"
      ]
    },
    {
      "schema_version": 1,
      "topic_id": "admin-operations",
      "title": "Admin Operations",
      "goal": "提供账号、排班、派单、SLA、Agent 配置、Automation 和运营指标的控制面。",
      "surfaces": [
        "/workspace/admin",
        "/dashboard"
      ],
      "components": {
        "ui": [
          "ui/workspace-ui/admin",
          "ui/dashboard-ui"
        ],
        "api": [
          "/api/workspace/admin/*",
          "/api/dashboard/*"
        ],
        "services": [
          "backend/services/admin_*",
          "backend/services/assignment_*"
        ],
        "tests": [
          "backend/tests/test_workspace_admin_*",
          "backend/tests/test_dashboard_*"
        ]
      },
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/phase2.html",
        "docs/feature_list.md"
      ]
    },
    {
      "schema_version": 1,
      "topic_id": "agent-collaboration",
      "title": "Agent Collaboration",
      "goal": "在证据、权限、成本和审计边界成熟后，扩展 Engineer multi-agent 与 AgentRelay 协作。",
      "surfaces": [],
      "components": {
        "ui": [
          "ui/workspace-ui"
        ],
        "api": [
          "backend/services/agent_*",
          "AgentRelay integration"
        ],
        "services": [
          "backend/services/agent_*",
          "backend/services/evidence_*"
        ],
        "tests": [
          "backend/tests/test_agent_*",
          "backend/tests/test_evidence_*"
        ]
      },
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/meetings.html",
        "docs/feature_list.md"
      ]
    },
    {
      "schema_version": 1,
      "topic_id": "client-experience",
      "title": "Client Experience",
      "goal": "保留客户熟悉的 Zendesk/Client 入口，同时把路由、澄清、证据和升级边界做成可观察的体验。",
      "surfaces": [
        "/client"
      ],
      "components": {
        "ui": [
          "ui/client-ui"
        ],
        "api": [
          "/api/client/*"
        ],
        "services": [
          "backend/services/support_router.py",
          "backend/services/troubleshooting_intake.py"
        ],
        "tests": [
          "backend/tests/test_client_*",
          "backend/tests/test_support_router.py"
        ]
      },
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ]
    },
    {
      "schema_version": 1,
      "topic_id": "engineer-workspace",
      "title": "Engineer Workspace",
      "goal": "让 Engineer 处理 Case 时拥有明确的 assignment、SLA、证据、审核、回复和关闭边界。",
      "surfaces": [
        "/workspace",
        "/engineer"
      ],
      "components": {
        "ui": [
          "ui/workspace-ui",
          "ui/engineer-ui"
        ],
        "api": [
          "/api/workspace/*",
          "/api/engineer/*"
        ],
        "services": [
          "backend/services/engineer_*",
          "backend/services/agent_*",
          "backend/services/guardrail*"
        ],
        "tests": [
          "backend/tests/test_workspace_*",
          "backend/tests/test_engineer_*"
        ]
      },
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/phase3.html",
        "docs/feature_list.md"
      ]
    },
    {
      "schema_version": 1,
      "topic_id": "platform-delivery",
      "title": "Platform Delivery",
      "goal": "保持认证、数据库、部署、可靠性和开发工作流可验证，避免产品进度脱离真实运行证据。",
      "surfaces": [
        "/health"
      ],
      "components": {
        "ui": [
          "docs/projectoverview.html"
        ],
        "api": [
          "backend/main.py",
          "/health"
        ],
        "services": [
          "deployment",
          "scripts/workflow"
        ],
        "tests": [
          "backend/tests/test_workflow_scripts.py",
          "backend/tests/test_repository_configuration.py"
        ]
      },
      "source_refs": [
        "AGENTS.md",
        "docs/agent_workflow_details.md",
        "docs/roadmap/phase2.html"
      ]
    },
    {
      "schema_version": 1,
      "topic_id": "rag-knowledge",
      "title": "RAG & Knowledge",
      "goal": "让官网知识、Engineer 知识、检索评测和 KG 辅助信号各司其职，并保留可回溯引用。",
      "surfaces": [
        "/dashboard/rag"
      ],
      "components": {
        "ui": [
          "ui/dashboard-ui/rag"
        ],
        "api": [
          "/api/rag/*",
          "/api/dashboard/rag/*"
        ],
        "services": [
          "backend/services/rag_*",
          "backend/services/kg_*"
        ],
        "tests": [
          "backend/tests/test_rag_*",
          "backend/tests/test_kg_*"
        ]
      },
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md",
        "docs/rag_change_log.md"
      ]
    }
  ],
  "tasks": [
    {
      "schema_version": 1,
      "task_id": "AG-01",
      "title": "收口 billing route 验证、邮件回执和 Dashboard 三项 POC。",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "phase-1",
      "status": "planned",
      "priority": "unclassified",
      "owner": "zac / 团队",
      "summary": "收口 billing route 验证、邮件回执和 Dashboard 三项 POC。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "用真实 Zendesk Case 验证 route、内部请求、回执、关单和指标。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18"
      ],
      "created_at": "2026-06-18",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-01"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "AG-02",
      "title": "确定 fully_automated、ai_draft_human_approve、unable_to_resolve_handoff 的边界。",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "phase-1",
      "status": "planned",
      "priority": "unclassified",
      "owner": "项目团队",
      "summary": "确定 fully_automated、ai_draft_human_approve、unable_to_resolve_handoff 的边界。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "为三类 Case 建立统一处理和升级契约。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18"
      ],
      "created_at": "2026-06-18",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-02"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "AG-03",
      "title": "建立 customer-facing 与 internal-facing 的敏感信息隔离和审计边界。",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "phase-1",
      "status": "planned",
      "priority": "unclassified",
      "owner": "项目团队",
      "summary": "建立 customer-facing 与 internal-facing 的敏感信息隔离和审计边界。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "抽样检查客户回复、内部日志、权限和历史留存。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18"
      ],
      "created_at": "2026-06-18",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-03"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "AG-04",
      "title": "验证邮件回执轮询、SLA 提醒和未回复 fallback。",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "phase-1",
      "status": "planned",
      "priority": "unclassified",
      "owner": "项目团队",
      "summary": "验证邮件回执轮询、SLA 提醒和未回复 fallback。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "覆盖已回复、超时和内部任务无法完成三条路径。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18"
      ],
      "created_at": "2026-06-18",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-04"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "AG-05",
      "title": "用少量真实 Case 对比保守 workflow、成熟 agent 框架和研发 agent 接入方式。",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "phase-1",
      "status": "planned",
      "priority": "unclassified",
      "owner": "项目团队",
      "summary": "用少量真实 Case 对比保守 workflow、成熟 agent 框架和研发 agent 接入方式。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "比较边界覆盖、效果、token 成本、权限和可审计性。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18"
      ],
      "created_at": "2026-06-18",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-05"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "AG-06",
      "title": "在真实 evidence tools、replay gate、权限审计和成本门禁达标前保持 AgentRelay 自主调查为长期计划。",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "phase-1",
      "status": "active",
      "priority": "unclassified",
      "owner": "项目团队",
      "summary": "在真实 evidence tools、replay gate、权限审计和成本门禁达标前保持 AgentRelay 自主调查为长期计划。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "Roadmap 和 Meeting 页面均不把通信基础误写成自主闭环。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18"
      ],
      "created_at": "2026-06-18",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-06"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-01",
      "title": "调整 Fraud、Account Suspension、Billing / Invoice、Enablement 的路由和后续动作。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "done",
      "priority": "unclassified",
      "owner": "zac",
      "summary": "调整 Fraud、Account Suspension、Billing / Invoice、Enablement 的路由和后续动作。",
      "next_action": "",
      "acceptance_criteria": [
        "当前路由、分类、字段处理和 reroute 测试已覆盖四类路径。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 675,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/675",
          "label": "PR #675"
        },
        {
          "type": "pr",
          "number": 676,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/676",
          "label": "PR #676"
        },
        {
          "type": "pr",
          "number": 680,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/680",
          "label": "PR #680"
        },
        {
          "type": "pr",
          "number": 683,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/683",
          "label": "PR #683"
        },
        {
          "type": "pr",
          "number": 685,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/685",
          "label": "PR #685"
        },
        {
          "type": "pr",
          "number": 686,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/686",
          "label": "PR #686"
        },
        {
          "type": "pr",
          "number": 687,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/687",
          "label": "PR #687"
        },
        {
          "type": "pr",
          "number": 702,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/702",
          "label": "PR #702"
        },
        {
          "type": "pr",
          "number": 709,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/709",
          "label": "PR #709"
        },
        {
          "type": "pr",
          "number": 719,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/719",
          "label": "PR #719"
        },
        {
          "type": "pr",
          "number": 731,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/731",
          "label": "PR #731"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-01"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-02",
      "title": "新增并完善 Compliance / Security 分类。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "done",
      "priority": "unclassified",
      "owner": "zac",
      "summary": "新增并完善 Compliance / Security 分类。",
      "next_action": "",
      "acceptance_criteria": [
        "分类为 Security & Compliance，路由到 human_review，不自动生成客户回复。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 729,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/729",
          "label": "PR #729"
        },
        {
          "type": "pr",
          "number": 731,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/731",
          "label": "PR #731"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-02"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-03",
      "title": "完成 AI 回复写回 Zendesk：internal comment 阶段已完成，external/customer reply 仍未完成。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "zac",
      "summary": "完成 AI 回复写回 Zendesk：internal comment 阶段已完成，external/customer reply 仍未完成。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "Admin 可将 Account AI 消息作为 public=false internal comment 写入关联 Zendesk Ticket，并记录幂等结果；external/customer reply 的真实写回与发送身份验收仍待完成。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-03"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-04",
      "title": "确认生产 AI API 账号、数据留存和客户数据安全要求。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "zac",
      "summary": "确认生产 AI API 账号、数据留存和客户数据安全要求。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "已与 Brent Guo 确认使用 OpenAI 官方 API key；官方政策默认不用于训练，但默认 abuse monitoring logs 可能保留最长 30 天，当前项目组织/项目级 retention control 与 ZDR 资格仍需核实。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-04"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-05",
      "title": "增加 AI 故障告警和人工接管机制。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "review",
      "priority": "unclassified",
      "owner": "zac",
      "summary": "增加 AI 故障告警和人工接管机制。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "Account AI 或自动化处理在 OpenAI/API 不可用、重试 3 次仍失败、结构化输出耗尽、Persona/字段处理异常或内部处理链路失败时停止自动化，最多执行首次调用加 3 次重试；不使用备用 provider/model，不生成客户回复，Case 持久化为 human_review_required，取消 pending reply job，并向预设的项目负责人邮箱发送一次脱敏、incident 幂等的故障邮件；邮件投递失败可重试。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-05"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-06",
      "title": "确认通用 Zendesk 账号、显示名称、邮箱地址及 API 权限。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "zac",
      "summary": "确认通用 Zendesk 账号、显示名称、邮箱地址及 API 权限。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "完成发送身份的端到端测试，不使用个人账号。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-06"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-07",
      "title": "在 Admin Dashboard 中增加 Zendesk Ticket 直达链接。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "done",
      "priority": "unclassified",
      "owner": "zac",
      "summary": "在 Admin Dashboard 中增加 Zendesk Ticket 直达链接。",
      "next_action": "",
      "acceptance_criteria": [
        "Automated Cases 的 Source 复用 /account 规则，支持对象、普通 URL 和 JSON 字符串形式的 Zendesk Source，并渲染为可点击的 zen#\u003cticket_id> 链接；Source 列不重复显示内部 Account Case ID。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 735,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/735",
          "label": "PR #735"
        },
        {
          "type": "pr",
          "number": 736,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/736",
          "label": "PR #736"
        },
        {
          "type": "pr",
          "number": 737,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/737",
          "label": "PR #737"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-07"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-08",
      "title": "承接 Billing 和 Detailed Invoice 工单并完成端到端验证。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "jojo",
      "summary": "承接 Billing 和 Detailed Invoice 工单并完成端到端验证。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "Billing 路由、内部通知、客户回复和关单结果与预期一致。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-08"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-09",
      "title": "承接 Account Suspension 和 Fraud 类工单，确认人工判断边界。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "suhird / bdr",
      "summary": "承接 Account Suspension 和 Fraud 类工单，确认人工判断边界。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "覆盖 Fraud、余额、套餐限制等停用原因，并能转 Support 介入。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-09"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-10",
      "title": "人工接管 Compliance、Security、法务及其他敏感工单。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "emma / derek",
      "summary": "人工接管 Compliance、Security、法务及其他敏感工单。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "客户侧不泄露内部信息，Case 保持统一人工口径。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-10"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-11",
      "title": "受控试运行期间每天复盘前一天 AI 处理的全部 Case。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "unclassified",
      "owner": "zac / emma / derek",
      "summary": "受控试运行期间每天复盘前一天 AI 处理的全部 Case。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "连续两天记录分类、回复、内部转交、关单和异常结果。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-11"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "TS-12",
      "title": "补齐 Admin Dashboard 的重点客户过滤。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "zac",
      "summary": "补齐 Admin Dashboard 的重点客户过滤。",
      "next_action": "补齐验收证据并更新状态。",
      "acceptance_criteria": [
        "需要明确并接入 Tag、CID、Requester Email 等重点客户数据来源和过滤路径。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-12"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "account-failure-alerts",
      "title": "Account 失败后的 Human Review 和负责人告警",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "done",
      "priority": "unclassified",
      "owner": "Zac",
      "summary": "重试耗尽后停止客户回复并发送脱敏故障告警。",
      "next_action": "",
      "acceptance_criteria": [
        "重试耗尽后停止客户回复并发送脱敏故障告警。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 744,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/744",
          "label": "PR #744"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "account-rerun-recovery",
      "title": "Account full rerun 的恢复、幂等和 fail-fast",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "done",
      "priority": "unclassified",
      "owner": "Zac",
      "summary": "Rerun 具备冻结、preflight、恢复和结果边界。",
      "next_action": "",
      "acceptance_criteria": [
        "Rerun 具备冻结、preflight、恢复和结果边界。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 738,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/738",
          "label": "PR #738"
        },
        {
          "type": "pr",
          "number": 739,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/739",
          "label": "PR #739"
        },
        {
          "type": "pr",
          "number": 740,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/740",
          "label": "PR #740"
        },
        {
          "type": "pr",
          "number": 741,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/741",
          "label": "PR #741"
        },
        {
          "type": "pr",
          "number": 742,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/742",
          "label": "PR #742"
        },
        {
          "type": "pr",
          "number": 745,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/745",
          "label": "PR #745"
        },
        {
          "type": "pr",
          "number": 746,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/746",
          "label": "PR #746"
        },
        {
          "type": "pr",
          "number": 747,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/747",
          "label": "PR #747"
        },
        {
          "type": "pr",
          "number": 748,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/748",
          "label": "PR #748"
        },
        {
          "type": "pr",
          "number": 751,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/751",
          "label": "PR #751"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "admin-environment-config-inventory",
      "title": "Admin Environment Config names-only inventory",
      "topic_id": "admin-operations",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "只展示合法配置名，不返回 value 或 value-derived metadata。",
      "next_action": "只展示合法配置名，不返回 value 或 value-derived metadata。",
      "acceptance_criteria": [
        "只展示合法配置名，不返回 value 或 value-derived metadata。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "agent-rules",
      "title": "AI 项目维护规则和详细流程分层",
      "topic_id": "platform-delivery",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "done",
      "priority": "unclassified",
      "owner": "Zac",
      "summary": "热路径规则和按需读取的工作流细节已分离。",
      "next_action": "",
      "acceptance_criteria": [
        "热路径规则和按需读取的工作流细节已分离。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 734,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/734",
          "label": "PR #734"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "assign-auth-hardening",
      "title": "P0",
      "topic_id": "engineer-workspace",
      "related_topic_ids": [],
      "milestone_id": "phase-3-engineer-workflow",
      "status": "active",
      "priority": "P0",
      "owner": "unassigned",
      "summary": "P0：完成生产 secret 配置、401 session 失效处理与 RBAC 负向验证。",
      "next_action": "P0：完成生产 secret 配置、401 session 失效处理与 RBAC 负向验证。",
      "acceptance_criteria": [
        "完成 Security 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-auth-hardening"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "assign-legacy-cleanup",
      "title": "9/1 后清理 legacy Engineer Case status、旧 UI 与历史兼容逻辑；清理前保持 `/api/engineer/*` active contract。",
      "topic_id": "engineer-workspace",
      "related_topic_ids": [],
      "milestone_id": "phase-3-engineer-workflow",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "9/1 后清理 legacy Engineer Case status、旧 UI 与历史兼容逻辑；清理前保持 `/api/engineer/*` active contract。",
      "next_action": "9/1 后清理 legacy Engineer Case status、旧 UI 与历史兼容逻辑；清理前保持 `/api/engineer/*` active contract。",
      "acceptance_criteria": [
        "完成 Cleanup 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-legacy-cleanup"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "assign-live-postgres",
      "title": "P0",
      "topic_id": "engineer-workspace",
      "related_topic_ids": [],
      "milestone_id": "phase-3-engineer-workflow",
      "status": "active",
      "priority": "P0",
      "owner": "unassigned",
      "summary": "P0：在真实 PostgreSQL/compose 环境验证 schema migration、账号 upsert、原子派单、SLA reassign 和 audit 写入。",
      "next_action": "P0：在真实 PostgreSQL/compose 环境验证 schema migration、账号 upsert、原子派单、SLA reassign 和 audit 写入。",
      "acceptance_criteria": [
        "完成 Reliability 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-live-postgres"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "assign-metrics",
      "title": "P2",
      "topic_id": "engineer-workspace",
      "related_topic_ids": [],
      "milestone_id": "phase-3-engineer-workflow",
      "status": "active",
      "priority": "P2",
      "owner": "unassigned",
      "summary": "P2：完善 first assignment、resolution、overdue、dispatch failure、SLA/schedule reassign 与 schedule coverage 指标。",
      "next_action": "P2：完善 first assignment、resolution、overdue、dispatch failure、SLA/schedule reassign 与 schedule coverage 指标。",
      "acceptance_criteria": [
        "完成 Metrics 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-metrics"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "assign-phase3-admin-sync",
      "title": "Phase 3",
      "topic_id": "engineer-workspace",
      "related_topic_ids": [],
      "milestone_id": "phase-3-engineer-workflow",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "Phase 3：Admin Dashboard 补充 Slack 送达状态，并把 Admin-only reassign 结果同步到 Slack。",
      "next_action": "Phase 3：Admin Dashboard 补充 Slack 送达状态，并把 Admin-only reassign 结果同步到 Slack。",
      "acceptance_criteria": [
        "完成 Admin 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-phase3-admin-sync"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "assign-phase3-eligibility",
      "title": "Phase 3",
      "topic_id": "engineer-workspace",
      "related_topic_ids": [],
      "milestone_id": "phase-3-engineer-workflow",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "Phase 3：为 Zendesk intake 增加 AI eligibility gate，大客户、明显生气或高风险客户暂不进入 AI 处理。",
      "next_action": "Phase 3：为 Zendesk intake 增加 AI eligibility gate，大客户、明显生气或高风险客户暂不进入 AI 处理。",
      "acceptance_criteria": [
        "完成 Eligibility 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-phase3-eligibility"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "assign-phase3-first-reply",
      "title": "Phase 3",
      "topic_id": "engineer-workspace",
      "related_topic_ids": [],
      "milestone_id": "phase-3-engineer-workflow",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "Phase 3：AI 只完成首次有效回复和必要信息收集，随后将 case assign 给工程师。",
      "next_action": "Phase 3：AI 只完成首次有效回复和必要信息收集，随后将 case assign 给工程师。",
      "acceptance_criteria": [
        "完成 First Reply 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-phase3-first-reply"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "assign-phase3-slack",
      "title": "Phase 3",
      "topic_id": "engineer-workspace",
      "related_topic_ids": [],
      "milestone_id": "phase-3-engineer-workflow",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "Phase 3：验证 Slack bot 权限与消息承载模型，将 Round Robin 派单结果和 Zendesk ticket 关联信息送达工程师。",
      "next_action": "Phase 3：验证 Slack bot 权限与消息承载模型，将 Round Robin 派单结果和 Zendesk ticket 关联信息送达工程师。",
      "acceptance_criteria": [
        "完成 Slack 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-phase3-slack"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "assign-rollout",
      "title": "P1",
      "topic_id": "engineer-workspace",
      "related_topic_ids": [],
      "milestone_id": "phase-3-engineer-workflow",
      "status": "active",
      "priority": "P1",
      "owner": "unassigned",
      "summary": "P1：用 Account Not automated 每第 10 单创建 Engineer Case 进行试运行，问题修复后在 9/1 前切到 100%。",
      "next_action": "P1：用 Account Not automated 每第 10 单创建 Engineer Case 进行试运行，问题修复后在 9/1 前切到 100%。",
      "acceptance_criteria": [
        "完成 Rollout 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-rollout"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "billing-dashboard-metrics",
      "title": "Dashboard / monitor 指标固定为 route_accuracy、automation_coverage、not_automated_reason、response_latency、a",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "Dashboard / monitor 指标固定为 route_accuracy、automation_coverage、not_automated_reason、response_latency、approve/reject rate、SLA risk 和 internal_email_send_status。",
      "next_action": "Dashboard / monitor 指标固定为 route_accuracy、automation_coverage、not_automated_reason、response_latency、approve/reject rate、SLA risk 和 internal_email_send_status。",
      "acceptance_criteria": [
        "完成 Metrics 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-dashboard-metrics"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "billing-expand",
      "title": "是否扩展到更多 billing 小类取决于试运行质量，目前保持收口。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "blocked",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "是否扩展到更多 billing 小类取决于试运行质量，目前保持收口。",
      "next_action": "明确解除 blocker 的验证步骤。",
      "acceptance_criteria": [
        "完成 Decision 维度的交付和验证。"
      ],
      "blockers": [
        "是否扩展到更多 billing 小类取决于试运行质量，目前保持收口。"
      ],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-expand"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "billing-human-review",
      "title": "建立人工审核模式",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "建立人工审核模式：先审核 route 与 automation 状态，再验证 engineer reply 通过/拒绝、revise/override 和 customer-facing reply 延迟发送。",
      "next_action": "建立人工审核模式：先审核 route 与 automation 状态，再验证 engineer reply 通过/拒绝、revise/override 和 customer-facing reply 延迟发送。",
      "acceptance_criteria": [
        "完成 Review 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-human-review"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "billing-human-review-handoff",
      "title": "后续单独设计 Account Human Review 到 Engineer Case 的显式交接；当前只记录 Human Review 标签，不复用旧的第 10 单 rollout。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "后续单独设计 Account Human Review 到 Engineer Case 的显式交接；当前只记录 Human Review 标签，不复用旧的第 10 单 rollout。",
      "next_action": "后续单独设计 Account Human Review 到 Engineer Case 的显式交接；当前只记录 Human Review 标签，不复用旧的第 10 单 rollout。",
      "acceptance_criteria": [
        "完成 Later 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-human-review-handoff"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "billing-idempotency",
      "title": "验证 Zendesk/external ID 幂等与异常恢复，确保重复 webhook 不重复建单、派单或发送内部邮件。",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "验证 Zendesk/external ID 幂等与异常恢复，确保重复 webhook 不重复建单、派单或发送内部邮件。",
      "next_action": "验证 Zendesk/external ID 幂等与异常恢复，确保重复 webhook 不重复建单、派单或发送内部邮件。",
      "acceptance_criteria": [
        "完成 Reliability 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 732,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/732",
          "label": "PR #732"
        },
        {
          "type": "pr",
          "number": 751,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/751",
          "label": "PR #751"
        }
      ],
      "source_refs": [
        "docs/feature_list.md",
        "docs/roadmap.html",
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-idempotency"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "billing-monitor-automation-outcomes",
      "title": "Monitor automated case 执行结果",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "Monitor automated case 执行结果：跟踪 automation_status、missing_fields、internal_email_send_status、Outlook reply / PDF 附件转发、customer follow-up 和异常失败原因。",
      "next_action": "Monitor automated case 执行结果：跟踪 automation_status、missing_fields、internal_email_send_status、Outlook reply / PDF 附件转发、customer follow-up 和异常失败原因。",
      "acceptance_criteria": [
        "完成 Monitor 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-monitor-automation-outcomes"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "billing-monitor-replay-quality",
      "title": "Monitor real Zendesk replay set",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "Monitor real Zendesk replay set：持续看 invoice request、account suspension、company verification、非 billing、billing risky negative set 和 Not automated case 的 route_accuracy、automation_coverage、not_automated_reason 与 response_latency。",
      "next_action": "Monitor real Zendesk replay set：持续看 invoice request、account suspension、company verification、非 billing、billing risky negative set 和 Not automated case 的 route_accuracy、automation_coverage、not_automated_reason 与 response_latency。",
      "acceptance_criteria": [
        "完成 Monitor 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-monitor-replay-quality"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "billing-persona-registry",
      "title": "Account Automation Persona registry 与 ownership 回复",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "done",
      "priority": "unclassified",
      "owner": "Zac",
      "summary": "Persona preset、版本固定和客户 ownership 回复已交付。",
      "next_action": "",
      "acceptance_criteria": [
        "Persona preset、版本固定和客户 ownership 回复已交付。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 749,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/749",
          "label": "PR #749"
        },
        {
          "type": "pr",
          "number": 750,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/750",
          "label": "PR #750"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "billing-recipient-env",
      "title": "P2",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "P2",
      "owner": "unassigned",
      "summary": "P2：正式测试前完成 action-specific 内部邮箱 env 配置与部署校验。",
      "next_action": "P2：正式测试前完成 action-specific 内部邮箱 env 配置与部署校验。",
      "acceptance_criteria": [
        "完成 Configuration 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-recipient-env"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "client-rich-attachments",
      "title": "Client 对话支持图片和更多日志附件",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "补齐图片和 txt/log/md 等附件处理。",
      "next_action": "补齐图片和 txt/log/md 等附件处理。",
      "acceptance_criteria": [
        "补齐图片和 txt/log/md 等附件处理。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "client-streaming-output",
      "title": "Client 对话支持流式输出",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "定义流式回复、断线和最终消息一致性。",
      "next_action": "定义流式回复、断线和最终消息一致性。",
      "acceptance_criteria": [
        "定义流式回复、断线和最终消息一致性。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "kg-async-ingest",
      "title": "P1",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "P1",
      "owner": "unassigned",
      "summary": "P1：离线 KG ingest 已具备 chunk hash/schema hash/upsert state 与失败 chunk 不标记成功的基础；后台任务化、RAG 入库联动和 chunk hash → KG node version 失效路径仍待后续阶段。",
      "next_action": "P1：离线 KG ingest 已具备 chunk hash/schema hash/upsert state 与失败 chunk 不标记成功的基础；后台任务化、RAG 入库联动和 chunk hash → KG node version 失效路径仍待后续阶段。",
      "acceptance_criteria": [
        "完成 Reliability 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-async-ingest"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "kg-benchmark-ab",
      "title": "P0",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "P0",
      "owner": "unassigned",
      "summary": "P0：rag_benchmark 已支持 rag_vs_rag_plus_kg 模式和 gate report；100 chunk bake-off 只负责建图模型选择，生产 shadow/灰度仍建议补 50–100 个真实 query 后再开。",
      "next_action": "P0：rag_benchmark 已支持 rag_vs_rag_plus_kg 模式和 gate report；100 chunk bake-off 只负责建图模型选择，生产 shadow/灰度仍建议补 50–100 个真实 query 后再开。",
      "acceptance_criteria": [
        "完成 Evaluation 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-benchmark-ab"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "kg-citations",
      "title": "KG 不能替代 RAG/Postgres source of truth；客户可见答案引用必须回到官网文档 chunk/citation。",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "blocked",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "KG 不能替代 RAG/Postgres source of truth；客户可见答案引用必须回到官网文档 chunk/citation。",
      "next_action": "明确解除 blocker 的验证步骤。",
      "acceptance_criteria": [
        "完成 Safety 维度的交付和验证。"
      ],
      "blockers": [
        "KG 不能替代 RAG/Postgres source of truth；客户可见答案引用必须回到官网文档 chunk/citation。"
      ],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-citations"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "kg-engineer-vs-client",
      "title": "P1",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "P1",
      "owner": "unassigned",
      "summary": "P1：明确 KG 在 Client AI / Engineer AI 的差异化使用：Client AI 永远 RAG 优先 + KG 仅辅助；Engineer AI 调查路径可允许 KG 主入口做多跳查询，但仍需带原文回链。",
      "next_action": "P1：明确 KG 在 Client AI / Engineer AI 的差异化使用：Client AI 永远 RAG 优先 + KG 仅辅助；Engineer AI 调查路径可允许 KG 主入口做多跳查询，但仍需带原文回链。",
      "acceptance_criteria": [
        "完成 Access Policy 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-engineer-vs-client"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "kg-graph-db",
      "title": "P1",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "P1",
      "owner": "unassigned",
      "summary": "P1：基于 sandbox 结果再比较 AWS Neptune Database/Serverless；Neptune Analytics 仅作为后续离线分析候选，不阻塞本地 benchmark。",
      "next_action": "P1：基于 sandbox 结果再比较 AWS Neptune Database/Serverless；Neptune Analytics 仅作为后续离线分析候选，不阻塞本地 benchmark。",
      "acceptance_criteria": [
        "完成 Infrastructure 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-graph-db"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "kg-grey-gate",
      "title": "P0",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "P0",
      "owner": "unassigned",
      "summary": "P0：生产 flag 暂不开；本地试用效果可先人工观察，进入 shadow/小流量前仍需 telemetry 审计、回滚开关和基准数据。",
      "next_action": "P0：生产 flag 暂不开；本地试用效果可先人工观察，进入 shadow/小流量前仍需 telemetry 审计、回滚开关和基准数据。",
      "acceptance_criteria": [
        "完成 Rollout 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-grey-gate"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "kg-ingest-model-bakeoff",
      "title": "P0",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "P0",
      "owner": "unassigned",
      "summary": "P0：用 100 个分层 official-doc chunks 跑 GraphRAG ingest 模型 bake-off：gpt-5.5 / gpt-5.4-mini / gpt-5.4-nano / deepseek-v4-pro / deepseek-v4-flash，按 schema pass、provenance、实体关系质量、unsupported fact、延迟和实际 token 成本选全量建图模型。",
      "next_action": "P0：用 100 个分层 official-doc chunks 跑 GraphRAG ingest 模型 bake-off：gpt-5.5 / gpt-5.4-mini / gpt-5.4-nano / deepseek-v4-pro / deepseek-v4-flash，按 schema pass、provenance、实体关系质量、unsupported fact、延迟和实际 token 成本选全量建图模型。",
      "acceptance_criteria": [
        "完成 Model Eval 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-ingest-model-bakeoff"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "kg-model-config",
      "title": "P1",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "P1",
      "owner": "unassigned",
      "summary": "P1：离线 CLI 已支持显式 GraphRAG config、schema、state-dir 与 dry-run；后续仍需把生产 KG_* 环境变量边界、secret 管理和 .env 复用策略固化。",
      "next_action": "P1：离线 CLI 已支持显式 GraphRAG config、schema、state-dir 与 dry-run；后续仍需把生产 KG_* 环境变量边界、secret 管理和 .env 复用策略固化。",
      "acceptance_criteria": [
        "完成 Config 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-model-config"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "kg-offline-graph-build",
      "title": "P0",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "P0",
      "owner": "unassigned",
      "summary": "P0：离线 KG ingest readiness report 与 official-doc chunk export CLI 已实现；模型 bake-off 通过后，在本地 Neo4j 用选定模型 ingest 全量真实 official-doc chunks 并观察 RAG+KG 在线效果。",
      "next_action": "P0：离线 KG ingest readiness report 与 official-doc chunk export CLI 已实现；模型 bake-off 通过后，在本地 Neo4j 用选定模型 ingest 全量真实 official-doc chunks 并观察 RAG+KG 在线效果。",
      "acceptance_criteria": [
        "完成 Ingest 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-offline-graph-build"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "ma-agent-to-agent-governed-autonomy",
      "title": "长期方向",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "long-term-agent-collaboration",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "长期方向：在真实证据工具、replay gate、权限审计和成本门禁达标后，扩大 governed agent-to-agent 自主调查；接入前固定 endpoint/forum/hub 协作和审计协议。",
      "next_action": "长期方向：在真实证据工具、replay gate、权限审计和成本门禁达标后，扩大 governed agent-to-agent 自主调查；接入前固定 endpoint/forum/hub 协作和审计协议。",
      "acceptance_criteria": [
        "完成 Long Term 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-agent-to-agent-governed-autonomy"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "ma-agentrelay-support-integration",
      "title": "低优先级保留",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "long-term-agent-collaboration",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "低优先级保留：把 AgentRelay communication foundation 接入 SupportPortal 真实 case：Support Agent 创建 task，Billing / Log / R&D / Data Agent 返回 artifact，结果投递回原始 case thread，并进入 guardrail / final approve。",
      "next_action": "低优先级保留：把 AgentRelay communication foundation 接入 SupportPortal 真实 case：Support Agent 创建 task，Billing / Log / R&D / Data Agent 返回 artifact，结果投递回原始 case thread，并进入 guardrail / final approve。",
      "acceptance_criteria": [
        "完成 Low Priority 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-agentrelay-support-integration"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "ma-controlled-replan",
      "title": "低优先级保留",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "long-term-agent-collaboration",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "低优先级保留：恢复受控 targeted replan：replan_required 只触发缺失证据相关 task，限制 retry / time / token / tool budget，记录每轮 delta，避免整轮盲刷或无限循环。",
      "next_action": "低优先级保留：恢复受控 targeted replan：replan_required 只触发缺失证据相关 task，限制 retry / time / token / tool budget，记录每轮 delta，避免整轮盲刷或无限循环。",
      "acceptance_criteria": [
        "完成 Low Priority 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-controlled-replan"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "ma-guardrail-claim-evidence",
      "title": "低优先级保留",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "long-term-agent-collaboration",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "低优先级保留：强化 Guardrail final 为 claim-to-evidence check：客户回复中的关键结论、步骤、限制和版本条件必须映射到 evidence ref；内部来源只能参与推理，不能直接进入 customer-facing reply。",
      "next_action": "低优先级保留：强化 Guardrail final 为 claim-to-evidence check：客户回复中的关键结论、步骤、限制和版本条件必须映射到 evidence ref；内部来源只能参与推理，不能直接进入 customer-facing reply。",
      "acceptance_criteria": [
        "完成 Low Priority 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-guardrail-claim-evidence"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "ma-real-evidence-tools",
      "title": "低优先级保留",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "long-term-agent-collaboration",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "低优先级保留：把 Execute Agent 的 allowlisted subagents 接到真实 evidence tools：Case Memory search、internal RAG、official RAG fallback、日志/diagnostic 查询；每个 task result 必须带 provenance、access_mode 和 customer_safe 标记。",
      "next_action": "低优先级保留：把 Execute Agent 的 allowlisted subagents 接到真实 evidence tools：Case Memory search、internal RAG、official RAG fallback、日志/diagnostic 查询；每个 task result 必须带 provenance、access_mode 和 customer_safe 标记。",
      "acceptance_criteria": [
        "完成 Low Priority 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-real-evidence-tools"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "ma-replay-runner",
      "title": "低优先级保留",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "long-term-agent-collaboration",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "低优先级保留：replay runner / metrics dashboard / regression gate：基于 replay eval dataset 自动重放并评估 Engineer AI 回复质量，并跟踪 review_decision、guardrail_block_reason、final_approve_latency、reopen/negative feedback。",
      "next_action": "低优先级保留：replay runner / metrics dashboard / regression gate：基于 replay eval dataset 自动重放并评估 Engineer AI 回复质量，并跟踪 review_decision、guardrail_block_reason、final_approve_latency、reopen/negative feedback。",
      "acceptance_criteria": [
        "完成 Low Priority 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-replay-runner"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "ma-rollout-taxonomy-contract",
      "title": "低优先级保留",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "long-term-agent-collaboration",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "低优先级保留：打通 route taxonomy 与 multi-agent 生命周期，让 fully_automated、ai_draft_human_approve、unable_to_resolve_handoff 成为统一 contract 和 dashboard 维度。",
      "next_action": "低优先级保留：打通 route taxonomy 与 multi-agent 生命周期，让 fully_automated、ai_draft_human_approve、unable_to_resolve_handoff 成为统一 contract 和 dashboard 维度。",
      "acceptance_criteria": [
        "完成 Low Priority 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-rollout-taxonomy-contract"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "ma-workspace-action-console",
      "title": "低优先级保留",
      "topic_id": "agent-collaboration",
      "related_topic_ids": [],
      "milestone_id": "long-term-agent-collaboration",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "低优先级保留：把 Multi-Agent Run 面板升级为工程师行动台：展示 blocker、缺失信息、证据强度、建议下一问、是否可安全回复，而不仅是只读状态。",
      "next_action": "低优先级保留：把 Multi-Agent Run 面板升级为工程师行动台：展示 blocker、缺失信息、证据强度、建议下一问、是否可安全回复，而不仅是只读状态。",
      "acceptance_criteria": [
        "完成 Low Priority 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-workspace-action-console"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "phase2-fraud-field-contract",
      "title": "明确 Fraud 与 Account Suspension 的字段边界",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "Suhird",
      "summary": "确定 required/optional 字段，避免缺失字段造成无限追问。",
      "next_action": "确定 required/optional 字段，避免缺失字段造成无限追问。",
      "acceptance_criteria": [
        "确定 required/optional 字段，避免缺失字段造成无限追问。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "project-overview",
      "title": "建立 SupportPortal Project Overview 单一维护入口",
      "topic_id": "platform-delivery",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "unclassified",
      "owner": "Zac",
      "summary": "实现 registry、生成器、汇总页面和旧 URL 兼容。",
      "next_action": "实现 registry、生成器、汇总页面和旧 URL 兼容。",
      "acceptance_criteria": [
        "实现 registry、生成器、汇总页面和旧 URL 兼容。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "rag-dedupe",
      "title": "P1",
      "topic_id": "rag-knowledge",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "P1",
      "owner": "unassigned",
      "summary": "P1：推进 RAG 文档 exact dedupe、near-duplicate clustering、canonical topic 和 conflict review，保证 KG 派生来源稳定。",
      "next_action": "P1：推进 RAG 文档 exact dedupe、near-duplicate clustering、canonical topic 和 conflict review，保证 KG 派生来源稳定。",
      "acceptance_criteria": [
        "完成 RAG Governance 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "rag-dedupe"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "routing-automation-rollout",
      "title": "Phase 2",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "Phase 2：当 real Zendesk replay set 和 dashboard 指标达标时，逐步开放 Fraud Account、Detailed Invoice、Enablement 和 Quota 的 limited automation。",
      "next_action": "Phase 2：当 real Zendesk replay set 和 dashboard 指标达标时，逐步开放 Fraud Account、Detailed Invoice、Enablement 和 Quota 的 limited automation。",
      "acceptance_criteria": [
        "完成 Rollout 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-automation-rollout"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "routing-billing-review-customer-experience",
      "title": "P0",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "P0",
      "owner": "unassigned",
      "summary": "P0：billing_review 客户回复改为人工审核确认，并创建内部待办/queue；不要复用 non_agora refusal copy。",
      "next_action": "P0：billing_review 客户回复改为人工审核确认，并创建内部待办/queue；不要复用 non_agora refusal copy。",
      "acceptance_criteria": [
        "完成 Customer Experience 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-billing-review-customer-experience"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "routing-billing-risky-negatives",
      "title": "补 billing risky negative set",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "补 billing risky negative set：refund/dispute/legal/compensation/source-code-sensitive 等风险信号必须进入 billing_review 或人工。",
      "next_action": "补 billing risky negative set：refund/dispute/legal/compensation/source-code-sensitive 等风险信号必须进入 billing_review 或人工。",
      "acceptance_criteria": [
        "完成 Safety 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-billing-risky-negatives"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "routing-dashboard-metrics",
      "title": "P1",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "P1",
      "owner": "unassigned",
      "summary": "P1：把 route_accuracy、automation_coverage、fallback_rate、correction_rate、not_automated_reason、response_latency 接到 dashboard，支持 10% 到 100% Controlled Launch 判断。",
      "next_action": "P1：把 route_accuracy、automation_coverage、fallback_rate、correction_rate、not_automated_reason、response_latency 接到 dashboard，支持 10% 到 100% Controlled Launch 判断。",
      "acceptance_criteria": [
        "完成 Metrics 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-dashboard-metrics"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "routing-fallback-billing-risk-sniff",
      "title": "P0",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "P0",
      "owner": "unassigned",
      "summary": "P0：conservative fallback 前增加 billing-risk sniff；LLM 缺凭证、超时或低置信时，invoice wrong/refund/dispute/legal/restore access 不应默认进入 agora_technical/RAG。",
      "next_action": "P0：conservative fallback 前增加 billing-risk sniff；LLM 缺凭证、超时或低置信时，invoice wrong/refund/dispute/legal/restore access 不应默认进入 agora_technical/RAG。",
      "acceptance_criteria": [
        "完成 Safety 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-fallback-billing-risk-sniff"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "routing-real-zendesk-replay",
      "title": "维护并扩展 real Zendesk replay monitor",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "维护并扩展 real Zendesk replay monitor：在已接入的 billing replay set 上继续覆盖 invoice request、account suspension、company verification、非 billing、technical handoff 与 billing risky negative set。",
      "next_action": "维护并扩展 real Zendesk replay monitor：在已接入的 billing replay set 上继续覆盖 invoice request、account suspension、company verification、非 billing、technical handoff 与 billing risky negative set。",
      "acceptance_criteria": [
        "完成 Monitor 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-real-zendesk-replay"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "routing-rollout-taxonomy",
      "title": "新增 rollout taxonomy",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "新增 rollout taxonomy：fully_automated、ai_draft_human_approve、unable_to_resolve_handoff，路由输出要能映射到三类 case。",
      "next_action": "新增 rollout taxonomy：fully_automated、ai_draft_human_approve、unable_to_resolve_handoff，路由输出要能映射到三类 case。",
      "acceptance_criteria": [
        "完成 Contract 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-rollout-taxonomy"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "routing-security-compliance",
      "title": "Security & Compliance classification-only route",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "done",
      "priority": "unclassified",
      "owner": "Zac",
      "summary": "敏感请求保持分类和人工边界，不自动生成客户回复。",
      "next_action": "",
      "acceptance_criteria": [
        "敏感请求保持分类和人工边界，不自动生成客户回复。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 729,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/729",
          "label": "PR #729"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    },
    {
      "schema_version": 1,
      "task_id": "routing-semantic-golden-expand",
      "title": "扩展 golden set",
      "topic_id": "client-experience",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "planned",
      "priority": "unclassified",
      "owner": "unassigned",
      "summary": "扩展 golden set：billing terms change、pricing inquiry、plan upgrade、multi-account 等边界 case。",
      "next_action": "扩展 golden set：billing terms change、pricing inquiry、plan upgrade、multi-account 等边界 case。",
      "acceptance_criteria": [
        "完成 Tests 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-semantic-golden-expand"
        }
      ]
    },
    {
      "schema_version": 1,
      "task_id": "routing-taxonomy",
      "title": "Account route taxonomy 和 filter membership",
      "topic_id": "account-automation",
      "related_topic_ids": [],
      "milestone_id": "phase-2-controlled-validation",
      "status": "active",
      "priority": "unclassified",
      "owner": "Zac",
      "summary": "区分 registered Automation、Human Review 和诊断 fallback。",
      "next_action": "区分 registered Automation、Human Review 和诊断 fallback。",
      "acceptance_criteria": [
        "区分 registered Automation、Human Review 和诊断 fallback。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 728,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/728",
          "label": "PR #728"
        },
        {
          "type": "pr",
          "number": 731,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/731",
          "label": "PR #731"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-16",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        }
      ],
      "legacy_refs": []
    }
  ],
  "meetings": [
    {
      "schema_version": 1,
      "meeting_id": "ticketing-system-2026-08-10",
      "date": "2026-08-10",
      "title": "Ticketing System 第一阶段对齐会",
      "participants": [
        "zac",
        "jojo",
        "suhird",
        "bdr",
        "emma",
        "derek"
      ],
      "summary": "第一阶段目标是直接减少进入人工队列的 Zendesk 工单量，而不是让所有 Support 工程师使用 AI 辅助处理。当前系统已部署在 AWS VM，Zendesk 工单通过自部署 n8n 导入，AI 回复仍保存在内部系统中，尚未真实写回 Zendesk 或发送给客户。",
      "decisions": [],
      "open_questions": [],
      "task_ids": [
        "TS-01",
        "TS-02",
        "TS-03",
        "TS-04",
        "TS-05",
        "TS-06",
        "TS-07",
        "TS-12",
        "TS-08",
        "TS-09",
        "TS-10",
        "TS-11"
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "legacy_anchor": "./roadmap/meetings.html#ticketing-system-2026-08-10"
    },
    {
      "schema_version": 1,
      "meeting_id": "agent-system-2026-06-18",
      "date": "2026-06-18",
      "title": "AI Agent 工单系统落地对齐会",
      "participants": [
        "derek",
        "zac",
        "alex",
        "emma"
      ],
      "summary": "会议确定以 agent 工单系统为长期方向，先用可追踪、可审计的保守 workflow 提升效率和回复质量，再逐步扩大 AI 调查与 AgentRelay 协作边界。",
      "decisions": [],
      "open_questions": [],
      "task_ids": [
        "AG-01",
        "AG-02",
        "AG-03",
        "AG-04",
        "AG-05",
        "AG-06"
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18"
      ],
      "legacy_anchor": "./roadmap/meetings.html#agent-system-2026-06-18"
    }
  ],
  "manual": {
    "schema_version": 1,
    "sections": [
      {
        "id": "client",
        "title": "Client 端",
        "audience": "Support 用户和客户入口维护者",
        "entrypoint": "/client",
        "steps": [
          "从 Zendesk/Client 入口打开 Ticket",
          "确认路由、澄清和引用证据",
          "证据不足时等待 Engineer handoff"
        ],
        "source_refs": [
          "docs/feature_list.md",
          "docs/roadmap.html"
        ]
      },
      {
        "id": "account",
        "title": "Account Automation",
        "audience": "Automation owner 和 Admin",
        "entrypoint": "/account",
        "steps": [
          "确认 canonical Ticket ID 和 route tuple",
          "查看 Automation 或 Human Review 状态",
          "发生失败时沿 audit 和故障告警进入人工处理"
        ],
        "source_refs": [
          "docs/feature_list.md",
          "docs/roadmap/phase2.html"
        ]
      },
      {
        "id": "engineer",
        "title": "Engineer Workspace",
        "audience": "Engineer",
        "entrypoint": "/workspace",
        "steps": [
          "查看系统派发的 Engineer Case",
          "按 Plan、Evidence、Review 和 Guardrail 处理",
          "完成 final approve 后关闭 Case 并保留验证证据"
        ],
        "source_refs": [
          "docs/feature_list.md",
          "docs/roadmap/phase3.html"
        ]
      },
      {
        "id": "admin",
        "title": "Admin Operations",
        "audience": "Admin 和项目负责人",
        "entrypoint": "/workspace/admin",
        "steps": [
          "管理账号、排班、Agent Config 和 Prompt 版本",
          "查看派单、SLA、Automation 和 Guardrail 指标",
          "需要改派时记录原因并保留审计"
        ],
        "source_refs": [
          "docs/feature_list.md",
          "docs/roadmap/phase2.html"
        ]
      },
      {
        "id": "rag",
        "title": "RAG Dashboard",
        "audience": "RAG/Knowledge owner",
        "entrypoint": "/dashboard/rag",
        "steps": [
          "同步或选择 benchmark 数据集",
          "运行并比较检索和生成结果",
          "从 citation、候选漏斗和 judge 分歧定位问题"
        ],
        "source_refs": [
          "docs/feature_list.md",
          "docs/rag_change_log.md"
        ]
      },
      {
        "id": "ai-maintenance",
        "title": "AI 维护入口",
        "audience": "Codex、Claude 和项目维护者",
        "entrypoint": "docs/project/README.md",
        "steps": [
          "开工前找到或创建唯一 Task",
          "实现中更新同一个 Task 的 status、next_action 和 evidence",
          "运行 generate_project_overview.py --check 后再提交"
        ],
        "source_refs": [
          "AGENTS.md",
          "docs/agent_workflow_details.md"
        ]
      }
    ]
  },
  "system_map": {
    "schema_version": 1,
    "layers": [
      "surface",
      "api",
      "service",
      "data",
      "external",
      "test"
    ],
    "nodes": [
      {
        "id": "client-surface",
        "label": "/client",
        "layer": "surface",
        "topic_ids": [
          "client-experience"
        ],
        "path": "ui/client-ui"
      },
      {
        "id": "account-surface",
        "label": "/account",
        "layer": "surface",
        "topic_ids": [
          "account-automation"
        ],
        "path": "ui/account-ui"
      },
      {
        "id": "workspace-surface",
        "label": "/workspace",
        "layer": "surface",
        "topic_ids": [
          "engineer-workspace"
        ],
        "path": "ui/workspace-ui"
      },
      {
        "id": "admin-surface",
        "label": "/workspace/admin",
        "layer": "surface",
        "topic_ids": [
          "admin-operations"
        ],
        "path": "ui/workspace-ui/admin"
      },
      {
        "id": "rag-surface",
        "label": "/dashboard/rag",
        "layer": "surface",
        "topic_ids": [
          "rag-knowledge"
        ],
        "path": "ui/dashboard-ui/rag"
      },
      {
        "id": "support-api",
        "label": "FastAPI routes",
        "layer": "api",
        "topic_ids": [
          "client-experience",
          "account-automation",
          "engineer-workspace",
          "admin-operations"
        ],
        "path": "backend/main.py"
      },
      {
        "id": "router-services",
        "label": "Routing / intake",
        "layer": "service",
        "topic_ids": [
          "client-experience",
          "account-automation"
        ],
        "path": "backend/services/support_router.py"
      },
      {
        "id": "engineer-services",
        "label": "Engineer / Guardrail",
        "layer": "service",
        "topic_ids": [
          "engineer-workspace",
          "agent-collaboration"
        ],
        "path": "backend/services"
      },
      {
        "id": "rag-services",
        "label": "RAG / KG runtime",
        "layer": "service",
        "topic_ids": [
          "rag-knowledge"
        ],
        "path": "backend/services/kg_runtime.py"
      },
      {
        "id": "postgres",
        "label": "PostgreSQL repositories",
        "layer": "data",
        "topic_ids": [
          "platform-delivery",
          "account-automation",
          "engineer-workspace"
        ],
        "path": "backend/repositories"
      },
      {
        "id": "zendesk",
        "label": "Zendesk",
        "layer": "external",
        "topic_ids": [
          "client-experience",
          "account-automation"
        ],
        "path": null
      },
      {
        "id": "tests",
        "label": "Contract and unit tests",
        "layer": "test",
        "topic_ids": [
          "platform-delivery"
        ],
        "path": "backend/tests"
      }
    ],
    "edges": [
      {
        "from": "client-surface",
        "to": "support-api",
        "label": "HTTP"
      },
      {
        "from": "account-surface",
        "to": "support-api",
        "label": "HTTP"
      },
      {
        "from": "workspace-surface",
        "to": "support-api",
        "label": "HTTP/WebSocket"
      },
      {
        "from": "admin-surface",
        "to": "support-api",
        "label": "authenticated HTTP"
      },
      {
        "from": "rag-surface",
        "to": "support-api",
        "label": "HTTP"
      },
      {
        "from": "support-api",
        "to": "router-services",
        "label": "dispatch"
      },
      {
        "from": "support-api",
        "to": "engineer-services",
        "label": "case lifecycle"
      },
      {
        "from": "support-api",
        "to": "rag-services",
        "label": "retrieval"
      },
      {
        "from": "router-services",
        "to": "postgres",
        "label": "persist route/audit"
      },
      {
        "from": "engineer-services",
        "to": "postgres",
        "label": "persist case/audit"
      },
      {
        "from": "support-api",
        "to": "zendesk",
        "label": "intake/write-back"
      },
      {
        "from": "tests",
        "to": "support-api",
        "label": "contract coverage"
      }
    ]
  },
  "pr_index": {
    "schema_version": 1,
    "fetched_at": "2026-08-16T12:01:25Z",
    "repository": "ZilingXie/SupportPortal",
    "prs": [
      {
        "createdAt": "2026-08-16T11:57:01Z",
        "headRefName": "codex/zendesk-account-comments",
        "isDraft": false,
        "mergedAt": "2026-08-16T11:57:06Z",
        "number": 754,
        "state": "MERGED",
        "title": "Add Zendesk Account comment sync",
        "updatedAt": "2026-08-16T11:57:08Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/754"
      },
      {
        "createdAt": "2026-08-16T11:43:12Z",
        "headRefName": "codex/account-automation-delivery-failure",
        "isDraft": false,
        "mergedAt": "2026-08-16T11:43:17Z",
        "number": 753,
        "state": "MERGED",
        "title": "Fix Account automation delivery failure recovery",
        "updatedAt": "2026-08-16T11:43:20Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/753"
      },
      {
        "createdAt": "2026-08-16T10:25:39Z",
        "headRefName": "codex/account-zendesk-internal-comment",
        "isDraft": false,
        "mergedAt": "2026-08-16T10:25:45Z",
        "number": 752,
        "state": "MERGED",
        "title": "Add Account AI internal comments to Zendesk",
        "updatedAt": "2026-08-16T10:25:47Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/752"
      },
      {
        "createdAt": "2026-08-14T07:58:24Z",
        "headRefName": "codex/account-reply-version-fence",
        "isDraft": false,
        "mergedAt": "2026-08-14T07:58:29Z",
        "number": 751,
        "state": "MERGED",
        "title": "Fence Account Persona reply jobs by version",
        "updatedAt": "2026-08-14T07:58:31Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/751"
      },
      {
        "createdAt": "2026-08-14T07:41:09Z",
        "headRefName": "codex/account-automation-reply-v8",
        "isDraft": false,
        "mergedAt": "2026-08-14T07:41:15Z",
        "number": 750,
        "state": "MERGED",
        "title": "Improve account automation ownership replies",
        "updatedAt": "2026-08-14T07:41:17Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/750"
      },
      {
        "createdAt": "2026-08-14T06:44:27Z",
        "headRefName": "codex/automation-ownership-replies",
        "isDraft": false,
        "mergedAt": "2026-08-14T06:44:33Z",
        "number": 749,
        "state": "MERGED",
        "title": "Improve Account Automation customer ownership replies",
        "updatedAt": "2026-08-14T06:44:35Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/749"
      },
      {
        "createdAt": "2026-08-14T03:34:27Z",
        "headRefName": "codex/account-rerun-email-claim-fix",
        "isDraft": false,
        "mergedAt": "2026-08-14T03:34:32Z",
        "number": 748,
        "state": "MERGED",
        "title": "Fix Account rerun email claim recovery",
        "updatedAt": "2026-08-14T03:34:34Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/748"
      },
      {
        "createdAt": "2026-08-14T02:43:12Z",
        "headRefName": "codex/account-rerun-reply-fix",
        "isDraft": false,
        "mergedAt": "2026-08-14T02:43:18Z",
        "number": 747,
        "state": "MERGED",
        "title": "Fix Account rerun customer reply scheduling",
        "updatedAt": "2026-08-14T02:43:19Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/747"
      },
      {
        "createdAt": "2026-08-13T09:43:16Z",
        "headRefName": "codex/account-rerun-immutable-result",
        "isDraft": false,
        "mergedAt": "2026-08-13T09:43:21Z",
        "number": 746,
        "state": "MERGED",
        "title": "Fix immutable Account rerun result handling",
        "updatedAt": "2026-08-13T09:43:24Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/746"
      },
      {
        "createdAt": "2026-08-13T08:52:30Z",
        "headRefName": "codex/account-rerun-degradation-guard",
        "isDraft": false,
        "mergedAt": "2026-08-13T08:52:35Z",
        "number": 745,
        "state": "MERGED",
        "title": "Fix Account rerun failures and container OpenAI proxy",
        "updatedAt": "2026-08-13T08:52:36Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/745"
      },
      {
        "createdAt": "2026-08-13T04:36:03Z",
        "headRefName": "codex/account-failure-alerts",
        "isDraft": false,
        "mergedAt": "2026-08-13T04:36:11Z",
        "number": 744,
        "state": "MERGED",
        "title": "TS-05 Account failure alerts and human handoff",
        "updatedAt": "2026-08-13T04:36:13Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/744"
      },
      {
        "createdAt": "2026-08-13T03:03:58Z",
        "headRefName": "codex/ts07-complete",
        "isDraft": false,
        "mergedAt": "2026-08-13T03:04:04Z",
        "number": 743,
        "state": "MERGED",
        "title": "docs(roadmap): mark TS-07 Zendesk links complete",
        "updatedAt": "2026-08-13T03:04:05Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/743"
      },
      {
        "createdAt": "2026-08-13T02:38:27Z",
        "headRefName": "codex/account-rerun-revision-canonicalization",
        "isDraft": false,
        "mergedAt": "2026-08-13T02:38:32Z",
        "number": 742,
        "state": "MERGED",
        "title": "fix(account): canonicalize rerun revision timestamps",
        "updatedAt": "2026-08-13T02:38:39Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/742"
      },
      {
        "createdAt": "2026-08-12T10:17:53Z",
        "headRefName": "codex/account-rerun-preflight-resilience",
        "isDraft": false,
        "mergedAt": "2026-08-12T10:17:59Z",
        "number": 741,
        "state": "MERGED",
        "title": "fix(account): make rerun preflight network resilient",
        "updatedAt": "2026-08-12T10:18:00Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/741"
      },
      {
        "createdAt": "2026-08-12T09:56:15Z",
        "headRefName": "codex/account-full-rerun-always",
        "isDraft": false,
        "mergedAt": "2026-08-12T09:56:20Z",
        "number": 740,
        "state": "MERGED",
        "title": "Allow every Account full rerun",
        "updatedAt": "2026-08-12T09:56:22Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/740"
      },
      {
        "createdAt": "2026-08-12T09:37:44Z",
        "headRefName": "codex/account-rerun-blocked-feedback",
        "isDraft": false,
        "mergedAt": "2026-08-12T09:37:49Z",
        "number": 739,
        "state": "MERGED",
        "title": "Fix blocked Account rerun feedback",
        "updatedAt": "2026-08-12T09:37:51Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/739"
      },
      {
        "createdAt": "2026-08-12T08:50:51Z",
        "headRefName": "codex/account-rerun-fail-fast",
        "isDraft": false,
        "mergedAt": "2026-08-12T08:50:56Z",
        "number": 738,
        "state": "MERGED",
        "title": "Harden Account rerun fail-fast recovery and Luna routing",
        "updatedAt": "2026-08-12T08:50:59Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/738"
      },
      {
        "createdAt": "2026-08-12T07:10:13Z",
        "headRefName": "codex/admin-source-display",
        "isDraft": false,
        "mergedAt": "2026-08-12T07:10:19Z",
        "number": 737,
        "state": "MERGED",
        "title": "fix(admin): hide internal case id from source",
        "updatedAt": "2026-08-12T07:10:21Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/737"
      },
      {
        "createdAt": "2026-08-12T07:02:48Z",
        "headRefName": "codex/admin-source-json",
        "isDraft": false,
        "mergedAt": "2026-08-12T07:02:52Z",
        "number": 736,
        "state": "MERGED",
        "title": "fix(admin): parse serialized account sources",
        "updatedAt": "2026-08-12T07:02:54Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/736"
      },
      {
        "createdAt": "2026-08-12T06:35:33Z",
        "headRefName": "codex/admin-source-link",
        "isDraft": false,
        "mergedAt": "2026-08-12T06:35:39Z",
        "number": 735,
        "state": "MERGED",
        "title": "feat(admin): link account case sources to Zendesk",
        "updatedAt": "2026-08-12T06:35:42Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/735"
      },
      {
        "createdAt": "2026-08-12T05:22:11Z",
        "headRefName": "codex/agents-complexity-boundary",
        "isDraft": false,
        "mergedAt": "2026-08-12T06:21:23Z",
        "number": 734,
        "state": "MERGED",
        "title": "docs: simplify agent hot-path rules",
        "updatedAt": "2026-08-12T06:21:25Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/734"
      },
      {
        "createdAt": "2026-08-12T03:59:55Z",
        "headRefName": "codex/meeting-progress-ui",
        "isDraft": false,
        "mergedAt": "2026-08-12T04:00:01Z",
        "number": 733,
        "state": "MERGED",
        "title": "docs: update meeting progress and navigation",
        "updatedAt": "2026-08-12T04:00:03Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/733"
      },
      {
        "createdAt": "2026-08-11T11:17:50Z",
        "headRefName": "codex/account-route-filter-hardening-validation-fix",
        "isDraft": false,
        "mergedAt": "2026-08-11T11:17:55Z",
        "number": 732,
        "state": "MERGED",
        "title": "Isolate Account route validation side effects",
        "updatedAt": "2026-08-11T11:17:57Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/732"
      },
      {
        "createdAt": "2026-08-11T10:17:26Z",
        "headRefName": "codex/account-route-filter-hardening",
        "isDraft": false,
        "mergedAt": "2026-08-11T10:17:30Z",
        "number": 731,
        "state": "MERGED",
        "title": "Harden Account route taxonomy and filter membership",
        "updatedAt": "2026-08-11T10:17:32Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/731"
      },
      {
        "createdAt": "2026-08-11T09:27:01Z",
        "headRefName": "codex/roadmap-meeting-page",
        "isDraft": false,
        "mergedAt": "2026-08-11T09:27:06Z",
        "number": 730,
        "state": "MERGED",
        "title": "docs: add SupportPortal meeting archive",
        "updatedAt": "2026-08-11T09:27:08Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/730"
      },
      {
        "createdAt": "2026-08-11T04:43:15Z",
        "headRefName": "codex/account-security-compliance",
        "isDraft": false,
        "mergedAt": "2026-08-11T04:43:20Z",
        "number": 729,
        "state": "MERGED",
        "title": "feat(account): add Security & Compliance route",
        "updatedAt": "2026-08-11T04:43:22Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/729"
      },
      {
        "createdAt": "2026-08-11T03:23:05Z",
        "headRefName": "codex/account-backend-operation-filter",
        "isDraft": false,
        "mergedAt": "2026-08-11T03:23:43Z",
        "number": 728,
        "state": "MERGED",
        "title": "feat(account): add Backend Operation filter and Automated labels",
        "updatedAt": "2026-08-11T03:23:45Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/728"
      },
      {
        "createdAt": "2026-08-10T17:06:27Z",
        "headRefName": "codex/automation-router-personas-implementation",
        "isDraft": false,
        "mergedAt": "2026-08-10T17:06:32Z",
        "number": 727,
        "state": "MERGED",
        "title": "feat(account): add randomized Automation Router personas",
        "updatedAt": "2026-08-10T17:06:34Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/727"
      },
      {
        "createdAt": "2026-08-10T12:10:56Z",
        "headRefName": "codex/account-route-filter-membership-hotfix",
        "isDraft": false,
        "mergedAt": "2026-08-10T12:11:01Z",
        "number": 726,
        "state": "MERGED",
        "title": "fix(account): align route filter counts and results",
        "updatedAt": "2026-08-10T12:11:04Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/726"
      },
      {
        "createdAt": "2026-08-10T11:51:47Z",
        "headRefName": "codex/account-route-domain-execution",
        "isDraft": false,
        "mergedAt": "2026-08-10T11:51:52Z",
        "number": 725,
        "state": "MERGED",
        "title": "feat(account): separate route domain from automation execution",
        "updatedAt": "2026-08-10T11:51:55Z",
        "url": "https://github.com/ZilingXie/SupportPortal/pull/725"
      }
    ]
  },
  "pr_summaries": {
    "schema_version": 1,
    "summaries": {
      "752": {
        "summary": "将 Account AI 消息以幂等 internal comment 写回关联 Zendesk ticket。",
        "task_ids": [
          "TS-03",
          "TS-07"
        ]
      },
      "751": {
        "summary": "为 Account Persona reply job 增加版本 fence，避免旧 job 覆盖新状态。",
        "task_ids": [
          "billing-idempotency"
        ]
      },
      "750": {
        "summary": "改善 Account Automation 对客户的 ownership 回复，统一由 Persona 生成安全的状态说明。",
        "task_ids": [
          "billing-persona-registry"
        ]
      },
      "749": {
        "summary": "补齐 Account Automation 客户 ownership 回复和处理中的反馈节奏。",
        "task_ids": [
          "billing-persona-registry"
        ]
      },
      "748": {
        "summary": "修复 Account rerun 邮件 claim 恢复路径，避免重复或丢失内部处理。",
        "task_ids": [
          "account-rerun-recovery"
        ]
      },
      "747": {
        "summary": "修复 Account rerun 客户回复的 scheduled reply 调度。",
        "task_ids": [
          "account-rerun-recovery"
        ]
      },
      "746": {
        "summary": "修复 Account rerun immutable result 的处理和状态边界。",
        "task_ids": [
          "account-rerun-recovery"
        ]
      },
      "745": {
        "summary": "修复 Account rerun 失败降级和容器 OpenAI proxy 配置。",
        "task_ids": [
          "account-rerun-recovery"
        ]
      },
      "744": {
        "summary": "Account 失败在重试耗尽后停止客户回复、进入 Human Review 并告警负责人。",
        "task_ids": [
          "TS-05",
          "account-failure-alerts"
        ]
      },
      "743": {
        "summary": "在 Admin Automated Cases 中增加可直接打开的 Zendesk Source 链接。",
        "task_ids": [
          "TS-07"
        ]
      },
      "742": {
        "summary": "统一 Account rerun revision 时间戳，避免恢复和排序出现不一致。",
        "task_ids": [
          "account-rerun-recovery"
        ]
      },
      "741": {
        "summary": "让 Account rerun preflight 在网络条件变化时保持可验证的失败边界。",
        "task_ids": [
          "account-rerun-recovery"
        ]
      },
      "740": {
        "summary": "允许操作员对每个 Account Case 执行完整 rerun，并保留独立审计。",
        "task_ids": [
          "account-rerun-recovery"
        ]
      },
      "739": {
        "summary": "修复被阻塞 Account rerun 的反馈和可恢复提示。",
        "task_ids": [
          "account-rerun-recovery"
        ]
      },
      "738": {
        "summary": "加固 Account rerun fail-fast recovery 和 Luna routing 选择。",
        "task_ids": [
          "account-rerun-recovery"
        ]
      },
      "737": {
        "summary": "Admin Source 不再重复展示内部 Account Case ID。",
        "task_ids": [
          "TS-07"
        ]
      },
      "736": {
        "summary": "支持把序列化 JSON 形式的 Account Source 解析成 Zendesk 链接。",
        "task_ids": [
          "TS-07"
        ]
      },
      "735": {
        "summary": "为 Account Case Source 增加 Zendesk 直达链接。",
        "task_ids": [
          "TS-07"
        ]
      },
      "734": {
        "summary": "简化 Agent 热路径规则，并把详细流程转移到可按需读取的文档。",
        "task_ids": [
          "agent-rules"
        ]
      },
      "733": {
        "summary": "更新 Meeting 进度页面、导航和 Work Item 展示。",
        "task_ids": [
          "project-overview"
        ]
      },
      "732": {
        "summary": "隔离 Account route validation 的副作用，避免校验行为改变真实状态。",
        "task_ids": [
          "billing-idempotency"
        ]
      },
      "731": {
        "summary": "加固 Account route taxonomy 和 filter membership，区分 Automation、Human Review 和 fallback。",
        "task_ids": [
          "routing-taxonomy"
        ]
      },
      "730": {
        "summary": "新增 SupportPortal Meeting archive，集中呈现 Topic、结论和 Work Item。",
        "task_ids": [
          "project-overview"
        ]
      },
      "729": {
        "summary": "增加 Security & Compliance route，并将敏感请求保持在 classification-only / Human Review 边界。",
        "task_ids": [
          "routing-security-compliance"
        ]
      },
      "728": {
        "summary": "增加 Backend Operation filter 和 Automated labels，统一显示 Enablement、Quota 等路径。",
        "task_ids": [
          "routing-taxonomy"
        ]
      }
    }
  },
  "features": [
    {
      "title": "Client 端",
      "completed": [
        "客户提问会自动生成工单。",
        "系统会识别 Agora 范围并分流。",
        "系统会用 RAG 自动答复技术问题。",
        "证据不足时会转工程师处理。",
        "查询扩展会用词典、LLM 和 PRF 优化技术检索。",
        "系统会自动识别 RTC 或 Cloud Recording，并在不确定时向客户确认后加载对应的 support prompt。",
        "排查型问题会先向客户补齐必要信息，再自动创建工程师工单。",
        "客户工单处理支持 main agent 调度 route、RAG 和 review 子 agent。",
        "Client 对话支持同 ticket 打断重发，并允许不同 ticket 并发等待 AI 回复。",
        "Client 与 Engineer 共用富文本 composer，支持粗体、斜体、列表、代码块和安全 markdown 渲染。",
        "对话支持上传 txt/log/err 日志附件。",
        "Client AI 只能检索官网文档，Engineer AI 优先检索非官网知识并可按需回查官网文档。",
        "`/account` 的 Automated execution view 以四个 registered 子类展示跨业务自动化：Account & Billing / Fraud Account、Account & Billing / Detailed Invoice、Backend Operation / Enablement 和 Backend Operation / Quota；每个 Case 同时保留其 Primary Category。Backend Operation / Unregistered 仅作为发现 taxonomy 缺口的诊断 fallback，不属于 Automated 或 Human Review membership；非风控 Account Suspension 仅提取上下文并保持 not automated。",
        "Quota 自动化会处理配额审核、并发提升和 Big Event 容量报备，最多追问一次后将现有信息交给内部团队。",
        "Enablement 使用 LLM 从客户原文提取并校验字段证据，不限制 App ID 格式；缺失时生成上下文追问，不确定或多候选时转 Human Review。",
        "Fraud Account 使用 LLM 收集公司、联系人、使用场景和安全支付概况，Website 为可选，最多追问一次并阻止敏感支付凭据进入派生数据。",
        "Billing 自动化统一通过公司 Outlook reply 接收内部处理结果，并可将 PDF 附件转发到客户工单。",
        "Account 入口可通过 HTTP 或手动 UI 创建 Account Case，并记录 Automated 或非自动化路由。",
        "Account 入口可查看 Account Case 历史和详情。",
        "Account 入口的 AI 消息可由 Admin 选择写入关联 Zendesk ticket 的 internal comment；external/customer reply 写回仍未完成。",
        "Account Automation 提供 Sid Precise、Sid Bright、Sid Warm 三套独立 Persona presets，首次客户回复随机分配并固定精确版本，完整 Rerun 后重新选择。",
        "Automation Behavior 只提取结构化字段和处理事实，所有实际客户文案在发送前统一由 Automation Persona 生成；Persona 失败时转 Human Review。",
        "Account 入口支持人工纠正完整路由元组，并通过 Route errors 视图分析误路由案例。",
        "Account 入口支持对每条工单的路由结果进行 pass/review 标记，默认只显示未 review 工单，可切换 reviewed 视图。",
        "Account 入口支持默认 All 的重叠 route filter，按 Automated、Backend Operation、Account & Billing、Tech、Security & Compliance、Conversation 和 Human Review 等细分类别分页查看，并显示同一快照的 case counts。",
        "Account 入口支持按 ticket # 精准打开 Case，并可对单 Case 执行仅保留客户消息、保留独立审计的完整 Rerun。",
        "Account Case 读取受 Workspace Admin 保护；n8n 可通过独立 Zendesk comment snapshot integration 将 Account Case 的 public/internal comments 幂等同步到独立 projection，详情按不同标签和气泡展示，Rerun 不删除这些 Zendesk comments。",
        "Account Rerun 先冻结目标 Case，再以无网络副作用的 Account-only preflight 校验数据库、Prompt runtime 和 Luna profile；首个 Case 的只读 Prepare 执行首次模型请求，任何错误立即停止并展示准确的失败阶段与未处理数量，支持从冻结 checkpoint Resume。",
        "Account 入口强制使用当前 layered route 并记录 pipeline 版本；Agora Router 将安全、隐私、信任、审计和合规请求归入 Security & Compliance classification-only 路由，Account & Billing 子 Router 将请求细分为 Account Suspension、Fraud Account、Detailed Invoice 或 Other，Backend Operation/Automation Router 将明确后台操作细分为 Enablement、Quota 或 Unregistered。每次新建异步全量 Rerun 都会重新执行路由、字段提取和 handler reconciliation，并允许 Automation 重新发送内部邮件，同时保留单个 job 内的幂等和审计历史。",
        "Account 入口通过 external ID 或来源 ticket ID 幂等处理重复请求，避免重复建单和重复发送内部邮件。",
        "Account Case 仅在命中已注册 Automation 时执行 handler 和延迟客户回复；其他路由只记录标签并进入对应人工或后续处理目标。",
        "Account 自动化遇到 AI/API、结构化输出、字段处理、Persona 或内部处理链路故障时最多重试 3 次且不使用 fallback；失败会停止客户回复、取消待处理 reply job、转为 human review，并向指定负责人发送脱敏的幂等故障告警。",
        "Enablement 使用 LLM 从客户原文提取并校验字段证据，不限制 App ID 格式；缺失时生成上下文追问，不确定或多候选时转 Human Review。",
        "Account Verification 使用 LLM 收集公司、联系人、使用场景和安全支付概况，最多追问一次并阻止敏感支付凭据进入派生数据。",
        "Summary Agent 会在升级工程师工单前生成结构化上下文摘要包。"
      ],
      "planned": [
        "对话支持上传图片和 txt/log/md 文件。",
        "对话支持流式输出。"
      ]
    },
    {
      "title": "Engineer 端",
      "completed": [
        "升级工单会进入工程师任务池。",
        "工程师可切换托管与接管模式。",
        "证据不足时会转工程师处理。",
        "调查中工单会按工程师 ticket 生命周期流转。",
        "工程师审核草稿后会回传客户。",
        "排查型问题会先向客户补齐必要信息，再自动创建工程师工单。",
        "Client 与 Engineer 共用富文本 composer，支持粗体、斜体、列表、代码块和安全 markdown 渲染。",
        "对话支持上传 txt/log/err 日志附件。",
        "Client AI 只能检索官网文档，Engineer AI 优先检索非官网知识并可按需回查官网文档。",
        "Engineer AI 会在工程师关闭 case 后自动生成结构化学习反馈。",
        "Engineer AI 会把所有学习反馈写入 Case Memory Ledger，并默认关闭自动召回。",
        "`/workspace` 是正式 Engineer Case 处理入口，工程师登录后可查看个人 weekly schedule，并在点击 Ready to roll 后处理系统派发给自己的 case。",
        "`/workspace/admin` 可通过真实邮件邀请创建 Admin/Engineer 账号，一次性 setup link 将邀请邮箱锁定为不可修改的登录身份。",
        "`/workspace/admin` 可在独立 Schedule tab 以 30 分钟格持久化管理 Engineer weekly schedule，支持跨夜与 `24:00` 全天边界；Engineer Management 直接以 on/off-schedule 展示 dispatch availability。",
        "Engineer Case 使用 active 且 on-schedule 的 engineer 进行 round-robin 自动派单，派单后立即开始 3 小时 SLA。",
        "Engineer 离开 schedule、账号 inactive 或 3 小时 SLA 到期时，系统会把未完成 Engineer Case 自动派给下一个合格 engineer。",
        "Engineer Case 派单状态使用 pending、assigned、resolved，并通过版本保护、事务更新和审计避免重复派发。",
        "Client Ticket status 与 Engineer Case assignment status 在 API、Workspace 和 Admin 中独立展示与处理。",
        "Admin 可人工调整 Engineer Case 派单，所有调整会记录操作者、原因、前后 assignee、状态和版本。",
        "旧 `/engineer` UI 已转为 legacy；`/api/engineer/*` 仍是 active backend contract，manual claim endpoint 已禁用。",
        "Summary Agent 会在升级工程师工单前生成结构化上下文摘要包。",
        "Engineer AI 会在调查前生成结构化 Plan Agent 计划。",
        "Engineer AI 会按 Plan Agent 计划执行 allowlisted subagents 并生成 evidence packet。",
        "Engineer AI 会根据执行结果生成 Review Agent 决策。",
        "Engineer multi-agent 默认关闭并与 9/1 Controlled Launch 主链路隔离。",
        "revise 不再自动跑 Plan/Execute/Review replan，也不再强制 max 2 retries，只保留可编辑/重新走 guardrail 的行为。",
        "Engineer AI 通过两段 approve 机制避免直接自动回复客户：第一次 approve 触发 deterministic guardrail 校验，第二次 final approve 才发送客户回复并关闭工单。final approve 后会写入 closure audit event（`engineer_case_closed_after_customer_reply`），并把处理结果记录为 Case Memory candidate；candidate 默认不可检索（`retrieval_enabled=False`）且不会自动晋升 active memory（`active_memory_status=inactive`）。",
        "Engineer AI 会在 final approve 后生成 replay eval dataset candidate，包含 summary packet、review decision、replan/revise 轨迹和 approved reply。"
      ],
      "planned": [
        "对话支持上传图片和 txt/log/md 文件。",
        "对话支持流式输出。"
      ]
    },
    {
      "title": "Ticket Dashboard",
      "completed": [
        "Dashboard 可查看全量工单列表。",
        "Dashboard 可查看工单详情与时间线。",
        "Dashboard 的 ticket detail 可查看按工单 family 聚合的 token 用量摘要。",
        "Dashboard 可跟踪实时事件流。",
        "Dashboard 的 ticket detail 可查看 client agent runtime 摘要与最近 agent events。",
        "Dashboard 的 ticket detail 可在单条 RAG 回复下展开检索计划、执行轮次和最终证据。",
        "Dashboard 的 ticket detail 可查看客户消息、路由、RAG、审核和最终结果组成的执行 Flow。",
        "`/workspace/admin` 可查看 Client Ticket、Engineer Case、SLA、派单/转派、Engineer schedule coverage、Automated Cases 和 guardrail 指标。",
        "`/workspace/admin` 将 Route Strategy 统一纳入 Agent Config，以 Agent-only 层级导航 Route Agent、Agora Router、Security & Compliance、Account & Billing Router、Backend Operation Router 与 Automation Router；Account Suspension、Fraud Account 和 Detailed Invoice 位于 Account & Billing Router 下，Security & Compliance 作为 classification-only outcome 展示，Automation Workflow catalog 统一展示五类执行/兜底流程。Account Prompt 支持 managed 版本管理，正式 skill 与 MCP 状态继续支持 Draft、Scheduled、Active、Diff、Restore 和历史版本管理，Scheduled Prompt 仅在下一次成功的每日部署后统一生效。",
        "对话支持上传 txt/log/err 日志附件。",
        "Account 入口可通过 HTTP 或手动 UI 创建 Account Case，并记录 Automated 或非自动化路由。",
        "Account 入口的 AI 消息可由 Admin 选择写入关联 Zendesk ticket 的 internal comment；external/customer reply 写回仍未完成。",
        "Account 入口支持人工纠正完整路由元组，并通过 Route errors 视图分析误路由案例。",
        "Account 入口支持对每条工单的路由结果进行 pass/review 标记，默认只显示未 review 工单，可切换 reviewed 视图。",
        "Account 入口支持默认 All 的重叠 route filter，按 Automated、Backend Operation、Account & Billing、Tech、Security & Compliance、Conversation 和 Human Review 等细分类别分页查看，并显示同一快照的 case counts。",
        "Account 入口支持按 ticket # 精准打开 Case，并可对单 Case 执行仅保留客户消息、保留独立审计的完整 Rerun。",
        "Account 入口强制使用当前 v8 分层分类并记录 pipeline 版本，支持以全新 Case 执行语义异步 Rerun 全部历史 Case；每个 Case 会保留客户消息和路由审计，删除旧 Account AI 回复、reply job、reply execution 与 Persona assignment 后再重建内部邮件与 Persona 回复。",
        "Account Case 仅在命中已注册 Automation 时执行 handler 和延迟客户回复；其他路由只记录标签并进入对应人工或后续处理目标。",
        "Billing 自动化统一通过公司 Outlook reply 接收内部处理结果，并可将 PDF 附件转发到客户工单。",
        "Automation Behavior 只提取结构化字段和处理事实，所有实际客户文案在发送前统一由 Automation Persona 生成；Persona 失败时转 Human Review。",
        "Account Automation 提供 Sid Precise、Sid Bright、Sid Warm 三套独立 Persona presets，首次客户回复随机分配并固定精确版本，完整 Rerun 后重新选择。",
        "Account Verification 使用 LLM 收集公司、联系人、使用场景和安全支付概况，最多追问一次并阻止敏感支付凭据进入派生数据。"
      ],
      "planned": [
        "待补充。"
      ]
    },
    {
      "title": "RAG Dashboard",
      "completed": [
        "Dashboard 可同步本地 benchmark 数据集。",
        "Dashboard 可发起 benchmark 运行并查看会话。",
        "Dashboard 可按 benchmark run 和 session 查看诊断分布与对比结果。",
        "Dashboard 的 Overview 可查看 benchmark token 汇总与 provider/model 明细。",
        "Dashboard 可复盘 live 与 benchmark case。",
        "Dashboard 可查看 query-understanding、候选漏斗和 judge 分歧诊断。",
        "Dashboard 可评审样本并导出结果。"
      ],
      "planned": [
        "待补充。"
      ]
    },
    {
      "title": "RAG",
      "completed": [
        "工程师可上传知识入库。",
        "系统会做混合检索与重排召回。",
        "查询扩展会用词典、LLM 和 PRF 优化技术检索。",
        "系统会按上下文预算压缩证据再生成技术答案。",
        "系统会按 provider/model 统计 RAG token，并支持 future-ready usage ledger。",
        "系统会输出 benchmark 分层诊断与失败归因。",
        "证据不足时会转工程师处理。",
        "系统已具备本地 benchmark 评测链路。",
        "系统会自动识别 RTC 或 Cloud Recording，并在不确定时向客户确认后加载对应的 support prompt。",
        "排查型问题会先向客户补齐必要信息，再自动创建工程师工单。",
        "客户工单处理支持 main agent 调度 route、RAG 和 review 子 agent。",
        "Dashboard 的 ticket detail 可在单条 RAG 回复下展开检索计划、执行轮次和最终证据。",
        "Client AI 只能检索官网文档，Engineer AI 优先检索非官网知识并可按需回查官网文档。",
        "本地 lightweight 线上路径已支持 RAG+KG 辅助调用，KG 在 query expansion、rerank boost、结构化 fact 三个钩子作为可降级辅助信号，生产灰度仍由 flag 控制。"
      ],
      "planned": [
        "RAG+KG 生产 shadow/灰度需要补齐真实 query 对照数据、telemetry 审计和一键回滚门禁。"
      ]
    }
  ],
  "migration": {
    "source_count": 73,
    "generated_from": [
      "docs/roadmap.html",
      "docs/roadmap/meetings.html",
      "docs/roadmap/phase2.html",
      "docs/roadmap/phase3.html",
      "docs/feature_list.md"
    ]
  }
};
