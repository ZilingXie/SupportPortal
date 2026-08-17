window.SUPPORTPORTAL_PROJECT_DATA = {
  "schema_version": 2,
  "generated_at": "2026-08-17T08:47:50Z",
  "source_base_commit": "716d475e2d4181ccde2c633c3c20e925a1ba1ed3",
  "registry_digest": "59107874fa8ff617957fd62ea58c1dd7c1704e51ff4e76531c1d677927939937",
  "project": {
    "schema_version": 2,
    "project_id": "supportportal",
    "title": "SupportPortal",
    "status": "active",
    "goal": "建设一个可追踪、可审计、以 AI 辅助为核心的客户支持工单系统；让客户入口、自动化、工程师处理和验证证据形成同一条闭环。",
    "owner": "Zac",
    "maintainers": [
      "Zac",
      "Codex"
    ],
    "repository_url": "https://github.com/ZilingXie/SupportPortal",
    "source_policy": {
      "progress": "docs/project/tasks/*.json",
      "hierarchy": "docs/project/phases/*.json + docs/project/modules/*.json + docs/project/functions/*.json",
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
    ],
    "current_phase_id": "phase-1"
  },
  "phases": [
    {
      "schema_version": 2,
      "phase_id": "phase-1",
      "title": "Phase 1：核心交付闭环",
      "status": "active",
      "summary": "当前交付阶段，聚焦 Account Automation、Admin Operations 和 Platform Delivery；这三个 Module 的 Task 属于本阶段完成范围。",
      "target_date": null,
      "exit_criteria": [
        "Account Automation 的路由、执行、人工审核、Zendesk 交付与受控发布任务完成，并有对应 evidence。",
        "Admin Operations 的管理控制面和运营任务完成，并有对应 evidence。",
        "Platform Delivery 的项目治理、生产 AI 约束和交付基础任务完成，并有对应 evidence。"
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/phase1.html",
        "docs/roadmap/phase2.html",
        "docs/roadmap/meetings.html"
      ]
    },
    {
      "schema_version": 2,
      "phase_id": "phase-2",
      "title": "Phase 2：Client、Knowledge 与 Engineer AI 演进",
      "status": "planned",
      "summary": "在 Phase 1 完成后，推进 Client Experience、RAG & Knowledge、Agent Collaboration 和 Engineer Workspace；当前全部 Task 标记为未开始。",
      "target_date": null,
      "exit_criteria": [
        "Client Experience 的对话能力和附件/流式交互可验证。",
        "RAG & Knowledge 的 ingestion、评测、生产平台和范围治理可验证。",
        "Agent Collaboration 与 Engineer Workspace 的证据、AI intake、派单和交接边界可验证。"
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/phase2.html",
        "docs/roadmap/phase3.html",
        "docs/roadmap/meetings.html"
      ]
    },
    {
      "schema_version": 2,
      "phase_id": "phase-3",
      "title": "Phase 3：预留阶段",
      "status": "planned",
      "summary": "当前没有注册 Task；保留为 Phase 2 之后新增能力的预留阶段。",
      "target_date": null,
      "exit_criteria": [
        "为 Phase 2 之后新增的可独立汇报能力建立 Module、Function 和 Task。"
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/phase3.html",
        "docs/roadmap/meetings.html"
      ]
    }
  ],
  "modules": [
    {
      "schema_version": 2,
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
      ],
      "module_id": "account-automation"
    },
    {
      "schema_version": 2,
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
      ],
      "module_id": "admin-operations"
    },
    {
      "schema_version": 2,
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
      ],
      "module_id": "agent-collaboration"
    },
    {
      "schema_version": 2,
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
      ],
      "module_id": "client-experience"
    },
    {
      "schema_version": 2,
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
      ],
      "module_id": "engineer-workspace"
    },
    {
      "schema_version": 2,
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
      ],
      "module_id": "platform-delivery"
    },
    {
      "schema_version": 2,
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
      ],
      "module_id": "rag-knowledge"
    }
  ],
  "functions": [
    {
      "schema_version": 2,
      "function_id": "automation-execution-loop",
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "title": "Automation 执行闭环",
      "goal": "让 Case 自动化执行具备幂等、恢复、监控、失败可见和向人工升级的闭环。",
      "acceptance_criteria": [],
      "evidence": [
        {
          "type": "pr",
          "number": 647,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/647",
          "label": "PR #647"
        },
        {
          "type": "pr",
          "number": 740,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/740",
          "label": "PR #740"
        },
        {
          "type": "pr",
          "number": 751,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/751",
          "label": "PR #751"
        },
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
          "number": 732,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/732",
          "label": "PR #732"
        },
        {
          "type": "pr",
          "number": 568,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/568",
          "label": "PR #568"
        },
        {
          "type": "pr",
          "number": 570,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/570",
          "label": "PR #570"
        },
        {
          "type": "pr",
          "number": 572,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/572",
          "label": "PR #572"
        },
        {
          "type": "pr",
          "number": 574,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/574",
          "label": "PR #574"
        },
        {
          "type": "pr",
          "number": 575,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/575",
          "label": "PR #575"
        },
        {
          "type": "pr",
          "number": 530,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/530",
          "label": "PR #530"
        },
        {
          "type": "pr",
          "number": 661,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/661",
          "label": "PR #661"
        },
        {
          "type": "pr",
          "number": 671,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/671",
          "label": "PR #671"
        },
        {
          "type": "pr",
          "number": 672,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/672",
          "label": "PR #672"
        },
        {
          "type": "pr",
          "number": 705,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/705",
          "label": "PR #705"
        },
        {
          "type": "pr",
          "number": 710,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/710",
          "label": "PR #710"
        },
        {
          "type": "pr",
          "number": 725,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/725",
          "label": "PR #725"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10",
        "docs/roadmap.html",
        "docs/feature_list.md",
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [
        "automation-execution"
      ],
      "status": "active",
      "task_count": 12,
      "done_count": 4,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "case-automation",
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "title": "Case Automation",
      "goal": "定义并交付可安全扩展的 Account/Billing Case 自动化能力和受控范围。",
      "acceptance_criteria": [],
      "evidence": [
        {
          "type": "pr",
          "number": 659,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/659",
          "label": "PR #659"
        },
        {
          "type": "pr",
          "number": 681,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/681",
          "label": "PR #681"
        },
        {
          "type": "pr",
          "number": 657,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/657",
          "label": "PR #657"
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
          "type": "test",
          "label": "Fraud Account grounding v3 regression coverage",
          "details": "二次 LLM verification、唯一 source quote 修正 message id、非法 verifier fail-closed、显式缺字段不重复验证和敏感支付信息 fail-closed 均通过 24 条定向测试。"
        },
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
        },
        {
          "type": "pr",
          "number": 426,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/426",
          "label": "PR #426"
        },
        {
          "type": "pr",
          "number": 432,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/432",
          "label": "PR #432"
        },
        {
          "type": "pr",
          "number": 571,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/571",
          "label": "PR #571"
        },
        {
          "type": "pr",
          "number": 670,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/670",
          "label": "PR #670"
        },
        {
          "type": "pr",
          "number": 674,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/674",
          "label": "PR #674"
        },
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
          "number": 709,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/709",
          "label": "PR #709"
        },
        {
          "type": "pr",
          "number": 568,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/568",
          "label": "PR #568"
        },
        {
          "type": "pr",
          "number": 572,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/572",
          "label": "PR #572"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10",
        "docs/roadmap.html",
        "docs/feature_list.md",
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [
        "controlled-rollout"
      ],
      "status": "active",
      "task_count": 7,
      "done_count": 6,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "case-route",
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "title": "Case Route",
      "goal": "建立可解释、可验证的 Case 分类、风险保护和路由质量边界。",
      "acceptance_criteria": [],
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
        },
        {
          "type": "pr",
          "number": 520,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/520",
          "label": "PR #520"
        },
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
          "number": 729,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/729",
          "label": "PR #729"
        },
        {
          "type": "pr",
          "number": 665,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/665",
          "label": "PR #665"
        },
        {
          "type": "pr",
          "number": 666,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/666",
          "label": "PR #666"
        },
        {
          "type": "pr",
          "number": 679,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/679",
          "label": "PR #679"
        },
        {
          "type": "pr",
          "number": 713,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/713",
          "label": "PR #713"
        },
        {
          "type": "pr",
          "number": 717,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/717",
          "label": "PR #717"
        },
        {
          "type": "pr",
          "number": 720,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/720",
          "label": "PR #720"
        },
        {
          "type": "pr",
          "number": 726,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/726",
          "label": "PR #726"
        },
        {
          "type": "pr",
          "number": 638,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/638",
          "label": "PR #638"
        },
        {
          "type": "pr",
          "number": 645,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/645",
          "label": "PR #645"
        },
        {
          "type": "pr",
          "number": 646,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/646",
          "label": "PR #646"
        },
        {
          "type": "pr",
          "number": 651,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/651",
          "label": "PR #651"
        },
        {
          "type": "pr",
          "number": 689,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/689",
          "label": "PR #689"
        },
        {
          "type": "pr",
          "number": 690,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/690",
          "label": "PR #690"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10",
        "docs/roadmap.html",
        "docs/feature_list.md",
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [
        "routing-taxonomy",
        "routing-fallback-billing-risk-sniff",
        "routing-quality-validation"
      ],
      "status": "active",
      "task_count": 9,
      "done_count": 5,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "human-review",
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "title": "Human Review",
      "goal": "为敏感、低置信、失败或需人工确认的 Case 提供可审计的人工审核与接管路径。",
      "acceptance_criteria": [],
      "evidence": [
        {
          "type": "pr",
          "number": 526,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/526",
          "label": "PR #526"
        },
        {
          "type": "pr",
          "number": 744,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/744",
          "label": "PR #744"
        },
        {
          "type": "pr",
          "number": 515,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/515",
          "label": "PR #515"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10",
        "docs/roadmap.html",
        "docs/feature_list.md",
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [],
      "status": "active",
      "task_count": 6,
      "done_count": 2,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "zendesk-connection",
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "title": "Zendesk Connection",
      "goal": "确保 Zendesk 的建单、派单、评论写回和身份信息可可靠连接与展示。",
      "acceptance_criteria": [],
      "evidence": [
        {
          "type": "pr",
          "number": 649,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/649",
          "label": "PR #649"
        },
        {
          "type": "pr",
          "number": 754,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/754",
          "label": "PR #754"
        },
        {
          "type": "test",
          "label": "Zendesk internal comment response parser and idempotency tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_zendesk_comments backend.tests.test_account_zendesk_comment -q"
        },
        {
          "type": "test",
          "label": "Account UI contract tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_ui_contract -q"
        },
        {
          "type": "test",
          "label": "Account Zendesk internal comment timeout and regression tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_ui_contract backend.tests.test_zendesk_comments backend.tests.test_account_zendesk_comment -q"
        },
        {
          "type": "test",
          "label": "Account Zendesk comment, PostgreSQL and UI contract tests",
          "command": "uv run --with-requirements requirements.base.txt python -m unittest backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_zendesk_comment_sync_postgres backend.tests.test_account_ui_contract"
        },
        {
          "type": "test",
          "label": "Python, frontend and N8n Code syntax checks",
          "command": "python3 -m py_compile backend/main.py backend/services/account_zendesk_comments.py backend/repositories/ticket_repository.py; node --check ui/account-ui/app.js"
        },
        {
          "type": "pr",
          "number": 481,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/481",
          "label": "PR #481"
        },
        {
          "type": "pr",
          "number": 492,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/492",
          "label": "PR #492"
        },
        {
          "type": "pr",
          "number": 663,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/663",
          "label": "PR #663"
        },
        {
          "type": "pr",
          "number": 703,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/703",
          "label": "PR #703"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10",
        "docs/integrations/n8n/zendesk_account_comment_sync.md",
        "docs/feature_list.md"
      ],
      "legacy_ids": [
        "zendesk-delivery"
      ],
      "status": "active",
      "task_count": 5,
      "done_count": 2,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "admin-case-operations",
      "phase_id": "phase-1",
      "module_id": "admin-operations",
      "title": "Admin Case 运营控制面",
      "goal": "提供管理员查看、筛选和追踪 Account Case 的能力。",
      "acceptance_criteria": [],
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
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10",
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "legacy_ids": [],
      "status": "active",
      "task_count": 3,
      "done_count": 1,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "production-ai-governance",
      "phase_id": "phase-1",
      "module_id": "platform-delivery",
      "title": "生产 AI 治理",
      "goal": "明确生产 AI 账号、数据留存和客户数据安全边界。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 1,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "project-governance",
      "phase_id": "phase-1",
      "module_id": "platform-delivery",
      "title": "项目进度治理",
      "goal": "维护 Project Overview、Task 标题和 AI 工作规则的单一事实源。",
      "acceptance_criteria": [],
      "evidence": [
        {
          "type": "pr",
          "number": 734,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/734",
          "label": "PR #734"
        },
        {
          "type": "test",
          "label": "Project Overview registry and route contract tests",
          "command": "python3 scripts/generate_project_overview.py --check && python3 -m unittest backend.tests.test_project_overview_contract backend.tests.test_dashboard_routes"
        },
        {
          "type": "test",
          "label": "Desktop and 390x844 browser verification for board, meetings, functions and handbook"
        },
        {
          "type": "test",
          "label": "Project Overview registry title and route contract tests",
          "command": "python3 scripts/generate_project_overview.py --check && python3 -m unittest backend.tests.test_project_overview_contract backend.tests.test_dashboard_routes"
        },
        {
          "type": "test",
          "label": "Desktop and 390x844 browser verification for renamed Task cards"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md",
        "docs/projectoverview.html",
        "backend/tests/test_project_overview_contract.py",
        "docs/project/tasks"
      ],
      "legacy_ids": [],
      "status": "done",
      "task_count": 3,
      "done_count": 3,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "agent-billing-poc",
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "title": "Agent Billing 验证",
      "goal": "收口 Billing route、回执和提醒的 Agent 验证闭环。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 2,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "agent-evidence-evaluation",
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "title": "Agent Evidence 与 Replay",
      "goal": "建立真实证据工具、Replay 评测和回归门禁。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 2,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "agent-governance",
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "title": "Agent 治理与自主边界",
      "goal": "在证据、权限、成本和审计门禁下控制 Agent 自主行为。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18",
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [
        "agent-controlled-replan"
      ],
      "status": "planned",
      "task_count": 8,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "agent-workspace-console",
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "title": "Agent Workspace 行动台",
      "goal": "把 Multi-Agent Run 面板升级为工程师可执行的行动台。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 1,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "agentrelay-integration",
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "title": "AgentRelay 协作接入",
      "goal": "将 AgentRelay 多角色协作接入 Support Case 工作流。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 1,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "client-conversation-experience",
      "phase_id": "phase-2",
      "module_id": "client-experience",
      "title": "Client 对话体验",
      "goal": "完善客户侧附件、流式回复和入口体验。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 2,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "engineer-ai-intake",
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "title": "Engineer AI Intake",
      "goal": "在 Zendesk Intake 和首次回复之间建立 AI eligibility 与交接边界。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [
        "engineer-case-handoff"
      ],
      "status": "planned",
      "task_count": 3,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "engineer-case-delivery",
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "title": "Engineer Case 派单交付",
      "goal": "完成认证、数据库、排班、指标、Slack 和逐步派单。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 7,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "rag-evaluation",
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "title": "RAG/KG 效果评测",
      "goal": "用真实查询、引用和灰度门禁验证知识增强效果。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 3,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "rag-ingestion-pipeline",
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "title": "RAG/KG 入库流水线",
      "goal": "建立文档去重、图谱构建、模型选择和异步入库链路。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 4,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "rag-production-platform",
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "title": "KG 生产平台",
      "goal": "确定生产图数据库、配置、Secret 和环境边界。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 2,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "rag-scope-governance",
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "title": "RAG/KG 使用边界",
      "goal": "明确 Client AI 和 Engineer AI 的知识使用范围。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "legacy_ids": [],
      "status": "planned",
      "task_count": 1,
      "done_count": 0,
      "blocked_count": 0
    }
  ],
  "tasks": [
    {
      "schema_version": 2,
      "task_id": "p1-01",
      "title": "建立 Account Automation 分层路由基线",
      "status": "done",
      "owner": "zac",
      "summary": "建立 Fraud、Account Suspension、Billing / Invoice 与 Enablement 的分层分类、路由和人工边界。",
      "next_action": "",
      "acceptance_criteria": [
        "当前路由、分类、字段处理和 reroute 测试已覆盖 Account Automation 的主要路径。"
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / routing-taxonomy。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-01 迁移为 p1-01；迁移到 phase-1 / account-automation / routing-taxonomy。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 routing-taxonomy 重新归类到 case-route。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-01"
        }
      ],
      "legacy_ids": [
        "TS-01",
        "p2-01"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
    },
    {
      "schema_version": 2,
      "task_id": "p1-02",
      "title": "建立 Compliance 与 Security 分类及人工边界",
      "status": "done",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / routing-taxonomy。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-02 迁移为 p1-02；迁移到 phase-1 / account-automation / routing-taxonomy。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 routing-taxonomy 重新归类到 case-route。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-02"
        }
      ],
      "legacy_ids": [
        "routing-security-compliance",
        "TS-02",
        "p2-02"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
    },
    {
      "schema_version": 2,
      "task_id": "p1-03",
      "title": "完成 Fraud 与 Account Suspension 字段契约和人工判断边界",
      "status": "done",
      "owner": "suhird / bdr",
      "summary": "承接 Account Suspension 和 Fraud 类工单，确认人工判断边界。",
      "next_action": "",
      "acceptance_criteria": [
        "覆盖 Fraud、余额、套餐限制等停用原因，并能转 Support 介入。"
      ],
      "blockers": [],
      "evidence": [
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
          "type": "test",
          "label": "Fraud Account grounding v3 regression coverage",
          "details": "二次 LLM verification、唯一 source quote 修正 message id、非法 verifier fail-closed、显式缺字段不重复验证和敏感支付信息 fail-closed 均通过 24 条定向测试。"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / routing-taxonomy。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-03 迁移为 p1-03；迁移到 phase-1 / account-automation / routing-taxonomy。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 routing-taxonomy 重新归类到 case-route。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-09"
        }
      ],
      "legacy_ids": [
        "phase2-fraud-field-contract",
        "TS-09",
        "p2-03"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-automation"
    },
    {
      "legacy_ids": [
        "p2-04"
      ],
      "task_id": "p1-04",
      "title": "实现确定性 Billing 风险保护 Gate",
      "summary": "在 Conservative Fallback 前识别 Billing 风险，避免高风险请求被错误自动化处理。",
      "status": "done",
      "owner": "Zac",
      "next_action": "",
      "acceptance_criteria": [
        "Billing 风险 Gate 在语义测试中可验证，并将高风险路径保留给人工处理。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 520,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/520",
          "label": "PR #520"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "created",
          "summary": "从已完成的 Billing 风险 Gate Function 拆出可验收 Task。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / routing-fallback-billing-risk-sniff。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-04 迁移为 p1-04；迁移到 phase-1 / account-automation / routing-fallback-billing-risk-sniff。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 routing-fallback-billing-risk-sniff 重新归类到 case-route。"
        }
      ],
      "legacy_refs": [],
      "schema_version": 2,
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
    },
    {
      "schema_version": 2,
      "task_id": "p1-05",
      "title": "监控真实 Zendesk Replay Set 的路由与自动化质量",
      "status": "active",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / routing-quality-validation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-05 迁移为 p1-05；迁移到 phase-1 / account-automation / routing-quality-validation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 routing-quality-validation 重新归类到 case-route。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-monitor-replay-quality"
        }
      ],
      "legacy_ids": [
        "billing-monitor-replay-quality",
        "p2-05"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
    },
    {
      "schema_version": 2,
      "task_id": "p1-06",
      "title": "扩展 Billing 高风险负样本集",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / routing-quality-validation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-06 迁移为 p1-06；迁移到 phase-1 / account-automation / routing-quality-validation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 routing-quality-validation 重新归类到 case-route。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-billing-risky-negatives"
        }
      ],
      "legacy_ids": [
        "routing-billing-risky-negatives",
        "p2-06"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
    },
    {
      "schema_version": 2,
      "task_id": "p1-07",
      "title": "扩展真实 Zendesk Replay Set 覆盖范围",
      "status": "active",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / routing-quality-validation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-07 迁移为 p1-07；迁移到 phase-1 / account-automation / routing-quality-validation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 routing-quality-validation 重新归类到 case-route。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-real-zendesk-replay"
        }
      ],
      "legacy_ids": [
        "routing-real-zendesk-replay",
        "p2-07"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
    },
    {
      "schema_version": 2,
      "task_id": "p1-08",
      "title": "扩展 Billing 路由边界 Golden Set",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / routing-quality-validation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-08 迁移为 p1-08；迁移到 phase-1 / account-automation / routing-quality-validation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 routing-quality-validation 重新归类到 case-route。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-semantic-golden-expand"
        }
      ],
      "legacy_ids": [
        "routing-semantic-golden-expand",
        "p2-08"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
    },
    {
      "schema_version": 2,
      "task_id": "p1-09",
      "title": "承接 Billing 和 Detailed Invoice 工单并完成端到端验证。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-09 迁移为 p1-09；迁移到 phase-1 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 automation-execution 重新归类到 case-automation。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-08"
        }
      ],
      "legacy_ids": [
        "TS-08",
        "p2-09"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-automation"
    },
    {
      "schema_version": 2,
      "task_id": "p1-10",
      "title": "Account full rerun 的恢复、幂等和 fail-fast",
      "status": "done",
      "owner": "Zac",
      "summary": "Rerun 具备冻结、preflight、恢复和结果边界；失败后禁止继续投递，并将 lease 过期公开为 needs_recovery。",
      "next_action": "",
      "acceptance_criteria": [
        "Rerun 具备冻结、preflight、恢复和结果边界。",
        "Rerun Commit 前固定内部邮件收件人，缺失时零写入并只发送 job-level owner alert。",
        "terminal failed rerun 的 delivery key 被 legacy worker 跳过，不发送内部邮件或客户回复。",
        "Resume 继续使用持久化收件人和已有 delivery/reply checkpoint，不重复投递。",
        "execution lease 过期只进入 needs_recovery，公开稳定原因、一次性 owner alert 和只读 reply 状态汇总，不自动恢复。"
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        },
        {
          "at": "2026-08-16",
          "event": "side_effect_fence_started",
          "summary": "修复 AC-12806 类 rerun 失败后 legacy Enablement poller 继续发送邮件和客户回复的问题。"
        },
        {
          "at": "2026-08-16",
          "event": "side_effect_fence_completed",
          "summary": "Rerun 在 Commit 前固定收件人，legacy poller 跳过 rerun-owned delivery，恢复保留已发送投递证据；定向回归测试通过。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "recovery_contract_completed",
          "summary": "补齐 needs_recovery API/UI 契约、一次性 lease-expiry owner alert、observed reply summary 与 InMemory/PostgreSQL parity；未执行正式 rerun。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-10 迁移为 p1-10；迁移到 phase-1 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 automation-execution 重新归类到 automation-execution-loop。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "account-rerun-recovery",
        "p2-10"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-11",
      "title": "保障 Zendesk Webhook 建单、派单与邮件发送幂等",
      "status": "active",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-11 迁移为 p1-11；迁移到 phase-1 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 automation-execution 重新归类到 automation-execution-loop。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-idempotency"
        }
      ],
      "legacy_ids": [
        "billing-idempotency",
        "p2-11"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-12",
      "title": "Account Automation Persona registry 与 ownership 回复",
      "status": "done",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-12 迁移为 p1-12；迁移到 phase-1 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 automation-execution 重新归类到 case-automation。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "billing-persona-registry",
        "p2-12"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-automation"
    },
    {
      "schema_version": 2,
      "task_id": "p1-13",
      "title": "配置并验证 Automation 内部处理邮箱",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-13 迁移为 p1-13；迁移到 phase-1 / account-automation / automation-execution。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 automation-execution 重新归类到 automation-execution-loop。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-recipient-env"
        }
      ],
      "legacy_ids": [
        "billing-recipient-env",
        "p2-13"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-14",
      "title": "人工接管 Compliance、Security、法务及其他敏感工单。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / human-review。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-14 迁移为 p1-14；迁移到 phase-1 / account-automation / human-review。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-10"
        }
      ],
      "legacy_ids": [
        "TS-10",
        "p2-14"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "human-review"
    },
    {
      "schema_version": 2,
      "task_id": "p1-15",
      "title": "在 Account 处理失败后触发告警并进入 Human Review",
      "status": "done",
      "owner": "zac",
      "summary": "增加 AI 故障告警和人工接管机制。",
      "next_action": "",
      "acceptance_criteria": [
        "Account AI 或自动化处理在 OpenAI/API 不可用、重试 3 次仍失败、结构化输出耗尽、Persona/字段处理异常或内部处理链路失败时停止自动化，最多执行首次调用加 3 次重试；不使用备用 provider/model，不生成客户回复，Case 持久化为 human_review_required，取消 pending reply job，并向预设的项目负责人邮箱发送一次脱敏、incident 幂等的故障邮件；邮件投递失败可重试。"
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
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / human-review。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-15 迁移为 p1-15；迁移到 phase-1 / account-automation / human-review。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-05"
        }
      ],
      "legacy_ids": [
        "account-failure-alerts",
        "TS-05",
        "p2-15"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "human-review"
    },
    {
      "schema_version": 2,
      "task_id": "p1-16",
      "title": "建立 Billing 人工审核与客户回复工作流",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / human-review。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-16 迁移为 p1-16；迁移到 phase-1 / account-automation / human-review。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-human-review"
        }
      ],
      "legacy_ids": [
        "billing-human-review",
        "p2-16"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "human-review"
    },
    {
      "schema_version": 2,
      "task_id": "p1-17",
      "title": "为 Billing Review 建立人工审核与内部待办",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / human-review。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-17 迁移为 p1-17；迁移到 phase-1 / account-automation / human-review。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-billing-review-customer-experience"
        }
      ],
      "legacy_ids": [
        "routing-billing-review-customer-experience",
        "p2-17"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "human-review"
    },
    {
      "schema_version": 2,
      "task_id": "p1-18",
      "title": "完成 AI 回复写回 Zendesk：internal comment 阶段已完成，external/customer reply 仍未完成。",
      "status": "active",
      "owner": "zac",
      "summary": "完成 AI 回复写回 Zendesk：internal comment 阶段已完成，external/customer reply 仍未完成。",
      "next_action": "扩展 external/customer reply 写回并完成发送身份验收。",
      "acceptance_criteria": [
        "Admin 可将 Account AI 消息作为 public=false internal comment 写入关联 Zendesk Ticket，并记录幂等结果；external/customer reply 的真实写回与发送身份验收仍待完成。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Zendesk internal comment response parser and idempotency tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_zendesk_comments backend.tests.test_account_zendesk_comment -q"
        },
        {
          "type": "test",
          "label": "Account UI contract tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_ui_contract -q"
        },
        {
          "type": "test",
          "label": "Account Zendesk internal comment timeout and regression tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_ui_contract backend.tests.test_zendesk_comments backend.tests.test_account_zendesk_comment -q"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / zendesk-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "internal_comment_response_parser_fixed",
          "summary": "修正 Zendesk Update Ticket 顶层 audit.events Comment 解析，并保留 outcome_unknown 幂等保护。"
        },
        {
          "at": "2026-08-17",
          "event": "internal_comment_spinner_timeout_fixed",
          "summary": "为 /account Zendesk internal comment 请求增加前端有界超时，确保 Adding... 在请求挂起时清除并显示错误。"
        },
        {
          "at": "2026-08-17",
          "event": "status_reclassified",
          "summary": "按 review 结论改为进行中；internal comment 已完成，external/customer reply 仍待完成。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-18 迁移为 p1-18；迁移到 phase-1 / account-automation / zendesk-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 zendesk-delivery 重新归类到 zendesk-connection。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-03"
        }
      ],
      "legacy_ids": [
        "TS-03",
        "p2-18"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "zendesk-connection"
    },
    {
      "schema_version": 2,
      "task_id": "p1-19",
      "title": "确认通用 Zendesk 账号、显示名称、邮箱地址及 API 权限。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / zendesk-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-19 迁移为 p1-19；迁移到 phase-1 / account-automation / zendesk-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 zendesk-delivery 重新归类到 zendesk-connection。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-06"
        }
      ],
      "legacy_ids": [
        "TS-06",
        "p2-19"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "zendesk-connection"
    },
    {
      "schema_version": 2,
      "task_id": "p1-20",
      "title": "补齐 Zendesk Account 评论作者身份与 Support Engineer/Customer 展示。",
      "status": "active",
      "owner": "zac",
      "summary": "通过 Zendesk users side-load 补齐评论作者姓名和身份，并在 Account 中派生 is_agent。",
      "next_action": "将 include=users 的 comment-sync workflow 部署到 n8n，并完成公网 Case 验收。",
      "acceptance_criteria": [
        "Account Zendesk comments 保存作者姓名和 author_kind，并派生 is_agent；public customer、public Support Engineer、internal note 和未知作者均有明确展示。",
        "N8n 完整评论快照按 author_id 合并 Zendesk users，重放保持幂等，无法确认身份时不得静默标记为 Agent。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Account Zendesk comment, PostgreSQL and UI contract tests",
          "command": "uv run --with-requirements requirements.base.txt python -m unittest backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_zendesk_comment_sync_postgres backend.tests.test_account_ui_contract"
        },
        {
          "type": "test",
          "label": "Python, frontend and N8n Code syntax checks",
          "command": "python3 -m py_compile backend/main.py backend/services/account_zendesk_comments.py backend/repositories/ticket_repository.py; node --check ui/account-ui/app.js"
        }
      ],
      "source_refs": [
        "docs/integrations/n8n/zendesk_account_comment_sync.md",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "created",
          "summary": "为 Zendesk Account comment sync 作者身份增强建立 Project Task。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / zendesk-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-20 迁移为 p1-20；迁移到 phase-1 / account-automation / zendesk-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 zendesk-delivery 重新归类到 zendesk-connection。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "zendesk-account-comment-identity",
        "p2-20"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "zendesk-connection"
    },
    {
      "schema_version": 2,
      "task_id": "p1-21",
      "title": "受控试运行期间每天复盘前一天 AI 处理的全部 Case。",
      "status": "active",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-21 迁移为 p1-21；迁移到 phase-1 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 controlled-rollout 重新归类到 automation-execution-loop。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-11"
        }
      ],
      "legacy_ids": [
        "TS-11",
        "p2-21"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "human-review"
    },
    {
      "schema_version": 2,
      "task_id": "p1-22",
      "title": "定义 Account Automation Dashboard 与 Monitor 指标",
      "status": "active",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-22 迁移为 p1-22；迁移到 phase-1 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 controlled-rollout 重新归类到 automation-execution-loop。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-dashboard-metrics"
        }
      ],
      "legacy_ids": [
        "billing-dashboard-metrics",
        "p2-22"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-23",
      "title": "依据试运行质量决定是否扩展 Billing 自动化范围",
      "status": "planned",
      "owner": "zac",
      "summary": "是否扩展到更多 billing 小类取决于试运行质量，目前保持收口。",
      "next_action": "开始受控试运行质量验证，形成 Billing 自动化扩围的决策依据。",
      "acceptance_criteria": [
        "完成 Decision 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "status_reclassified",
          "summary": "按 review 结论调整为未开始，负责人设为 zac；等待受控试运行证据后再做扩围决策。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-23 迁移为 p1-23；迁移到 phase-1 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 controlled-rollout 重新归类到 case-automation。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-expand"
        }
      ],
      "legacy_ids": [
        "billing-expand",
        "p2-23"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-24",
      "title": "监控 Account Automation 执行结果与失败原因",
      "status": "active",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-24 迁移为 p1-24；迁移到 phase-1 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 controlled-rollout 重新归类到 automation-execution-loop。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-monitor-automation-outcomes"
        }
      ],
      "legacy_ids": [
        "billing-monitor-automation-outcomes",
        "p2-24"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-25",
      "title": "按 Replay 与指标门禁逐步开放 Account Automation",
      "status": "planned",
      "owner": "unassigned",
      "summary": "Phase 1：当 real Zendesk replay set 和 dashboard 指标达标时，逐步开放 Fraud Account、Detailed Invoice、Enablement 和 Quota 的 limited automation。",
      "next_action": "Phase 1：当 real Zendesk replay set 和 dashboard 指标达标时，逐步开放 Fraud Account、Detailed Invoice、Enablement 和 Quota 的 limited automation。",
      "acceptance_criteria": [
        "完成 Rollout 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-25 迁移为 p1-25；迁移到 phase-1 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 controlled-rollout 重新归类到 case-automation。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-automation-rollout"
        }
      ],
      "legacy_ids": [
        "routing-automation-rollout",
        "p2-25"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-26",
      "title": "在 Dashboard 展示 Automation Controlled Launch 指标",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-26 迁移为 p1-26；迁移到 phase-1 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 controlled-rollout 重新归类到 automation-execution-loop。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-dashboard-metrics"
        }
      ],
      "legacy_ids": [
        "routing-dashboard-metrics",
        "p2-26"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-27",
      "title": "建立 Automation Rollout 三态 Taxonomy",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane routing-rules 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-27 迁移为 p1-27；迁移到 phase-1 / account-automation / controlled-rollout。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 controlled-rollout 重新归类到 case-automation。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "routing-rules",
          "item_id": "routing-rollout-taxonomy"
        }
      ],
      "legacy_ids": [
        "routing-rollout-taxonomy",
        "p2-27"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-28",
      "title": "为 Admin Case 提供 Zendesk Ticket 直达链接",
      "status": "done",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / admin-operations / admin-case-operations。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-28 迁移为 p1-28；迁移到 phase-1 / admin-operations / admin-case-operations。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-07"
        }
      ],
      "legacy_ids": [
        "TS-07",
        "p2-28"
      ],
      "phase_id": "phase-1",
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
    },
    {
      "schema_version": 2,
      "task_id": "p1-29",
      "title": "补齐 Admin Dashboard 的重点客户过滤。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / admin-operations / admin-case-operations。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-29 迁移为 p1-29；迁移到 phase-1 / admin-operations / admin-case-operations。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-12"
        }
      ],
      "legacy_ids": [
        "TS-12",
        "p2-29"
      ],
      "phase_id": "phase-1",
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
    },
    {
      "schema_version": 2,
      "task_id": "p1-30",
      "title": "仅展示 Admin Environment Config 名称清单",
      "status": "active",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / admin-operations / admin-case-operations。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-30 迁移为 p1-30；迁移到 phase-1 / admin-operations / admin-case-operations。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "admin-environment-config-inventory",
        "p2-30"
      ],
      "phase_id": "phase-1",
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
    },
    {
      "schema_version": 2,
      "task_id": "p1-33",
      "title": "确认生产 AI API 账号、数据留存和客户数据安全要求。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting ticketing-system-2026-08-10 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / platform-delivery / production-ai-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-33 迁移为 p1-33；迁移到 phase-1 / platform-delivery / production-ai-governance。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "ticketing-system-2026-08-10",
          "item_id": "TS-04"
        }
      ],
      "legacy_ids": [
        "TS-04",
        "p2-33"
      ],
      "phase_id": "phase-1",
      "module_id": "platform-delivery",
      "function_id": "production-ai-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p1-34",
      "title": "AI 项目维护规则和详细流程分层",
      "status": "done",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / platform-delivery / project-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-34 迁移为 p1-34；迁移到 phase-1 / platform-delivery / project-governance。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "agent-rules",
        "p2-34"
      ],
      "phase_id": "phase-1",
      "module_id": "platform-delivery",
      "function_id": "project-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p1-35",
      "title": "建立 SupportPortal Project Overview 单一维护入口",
      "status": "done",
      "owner": "Zac",
      "summary": "建立 Project Overview 单一维护入口，并优化任务、会议、功能模块和用户手册的展示与跳转。",
      "next_action": "",
      "acceptance_criteria": [
        "项目资料侧栏移除后，Project Overview 在桌面和移动端均使用完整内容宽度。",
        "任务看板卡片只显示 canonical Task ID 和完整标题，长标题不会越过卡片边界。",
        "会议记录以单行卡片展示，点击后弹窗列出带完整标题的全部关联 Task，并可跳转到对应 Task。",
        "功能模块按 Module 分组，Function 使用统一用户可见命名和单列布局，同时保留旧 hash 深链。",
        "用户手册展开后的已完成能力数量与标题统计一致。",
        "file URL、正式静态路由、旧 Roadmap URL 和 Project Overview 数据校验保持可用。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Project Overview registry and route contract tests",
          "command": "python3 scripts/generate_project_overview.py --check && python3 -m unittest backend.tests.test_project_overview_contract backend.tests.test_dashboard_routes"
        },
        {
          "type": "test",
          "label": "Desktop and 390x844 browser verification for board, meetings, functions and handbook"
        }
      ],
      "source_refs": [
        "docs/projectoverview.html",
        "backend/tests/test_project_overview_contract.py",
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        },
        {
          "at": "2026-08-16",
          "event": "implementation_started",
          "summary": "开始优化 Project Overview 的页面结构、任务看板、会议弹窗、功能模块和能力目录。"
        },
        {
          "at": "2026-08-16",
          "event": "completed",
          "summary": "完成 Project Overview 展示优化，并通过桌面、移动端、数据生成器和路由契约验证。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / platform-delivery / project-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-35 迁移为 p1-35；迁移到 phase-1 / platform-delivery / project-governance。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "project-overview",
        "p2-35"
      ],
      "phase_id": "phase-1",
      "module_id": "platform-delivery",
      "function_id": "project-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p1-36",
      "title": "清理 Project Overview 中无法识别的 Task 标题",
      "status": "done",
      "owner": "Zac",
      "summary": "将只显示管理标签、模糊短语或整段说明的 Task 标题改为可独立理解和汇报的功能名称。",
      "next_action": "",
      "acceptance_criteria": [
        "原先仅显示 P0/P1/P2、Phase、长期方向或低优先级保留的 Task 标题均改为具体功能名称。",
        "其他模糊、截断或整段说明式标题也改为简洁的交付名称。",
        "重命名不改变 Task ID、状态、优先级、里程碑、负责人或工作范围。",
        "Project Overview 数据校验和桌面、移动端任务看板验收通过。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Project Overview registry title and route contract tests",
          "command": "python3 scripts/generate_project_overview.py --check && python3 -m unittest backend.tests.test_project_overview_contract backend.tests.test_dashboard_routes"
        },
        {
          "type": "test",
          "label": "Desktop and 390x844 browser verification for renamed Task cards"
        }
      ],
      "source_refs": [
        "docs/project/tasks",
        "docs/projectoverview.html",
        "backend/tests/test_project_overview_contract.py"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "created",
          "summary": "为 Project Overview Task 标题可读性清理建立独立任务记录。"
        },
        {
          "at": "2026-08-17",
          "event": "completed",
          "summary": "完成 46 个模糊 Task 标题清理，并补充 registry 契约和浏览器验证。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / platform-delivery / project-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p2-36 迁移为 p1-36；迁移到 phase-1 / platform-delivery / project-governance。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "project-task-title-cleanup",
        "p2-36"
      ],
      "phase_id": "phase-1",
      "module_id": "platform-delivery",
      "function_id": "project-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p1-37",
      "title": "建立 Account Case 接入、历史列表和详情",
      "status": "done",
      "owner": "zac",
      "summary": "提供 Account Case 的创建、持久化、历史列表和详情读取能力。",
      "next_action": "",
      "acceptance_criteria": [
        "Account Case 可以通过入口创建并保存客户消息、来源和路由结果。",
        "Account 页面可以分页查看历史 Case 并打开详情。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 426,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/426",
          "label": "PR #426"
        },
        {
          "type": "pr",
          "number": 432,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/432",
          "label": "PR #432"
        },
        {
          "type": "pr",
          "number": 571,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/571",
          "label": "PR #571"
        },
        {
          "type": "pr",
          "number": 657,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/657",
          "label": "PR #657"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从历史 Account intake、Case history 和 Automated Case 迁移记录补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "account-case-intake"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-automation"
    },
    {
      "schema_version": 2,
      "task_id": "p1-38",
      "title": "建立 Account 路由纠正与人工复核闭环",
      "status": "done",
      "owner": "zac",
      "summary": "支持人工纠正路由、标记 pass/review，并按 review 状态筛选 Account Case。",
      "next_action": "",
      "acceptance_criteria": [
        "管理员可以纠正完整路由元组并保留审计记录。",
        "管理员可以将 Case 标记为 reviewed 或恢复为 pending，并按状态筛选。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 515,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/515",
          "label": "PR #515"
        },
        {
          "type": "pr",
          "number": 526,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/526",
          "label": "PR #526"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从历史 route correction 和 route review 实现补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "account-route-review"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "human-review"
    },
    {
      "schema_version": 2,
      "task_id": "p1-39",
      "title": "建立 Account 路由筛选、计数和精准定位",
      "status": "done",
      "owner": "zac",
      "summary": "按路由和状态查看 Account Case，并支持稳定计数、分页和 Ticket 精准打开。",
      "next_action": "",
      "acceptance_criteria": [
        "Account 列表按 route group/leaf 返回一致的筛选结果和计数。",
        "管理员可以使用 Ticket ID 精准打开 Case。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 665,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/665",
          "label": "PR #665"
        },
        {
          "type": "pr",
          "number": 666,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/666",
          "label": "PR #666"
        },
        {
          "type": "pr",
          "number": 679,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/679",
          "label": "PR #679"
        },
        {
          "type": "pr",
          "number": 713,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/713",
          "label": "PR #713"
        },
        {
          "type": "pr",
          "number": 717,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/717",
          "label": "PR #717"
        },
        {
          "type": "pr",
          "number": 720,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/720",
          "label": "PR #720"
        },
        {
          "type": "pr",
          "number": 726,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/726",
          "label": "PR #726"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从历史 Account route filter、count 和 exact search 实现补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "account-route-filters"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
    },
    {
      "schema_version": 2,
      "task_id": "p1-40",
      "title": "实现 Enablement 自动化和字段采集",
      "status": "done",
      "owner": "zac",
      "summary": "从客户消息提取 Enablement 字段，执行内部处理并生成受控客户更新。",
      "next_action": "",
      "acceptance_criteria": [
        "App ID 和功能名称可以从客户原文提取并保留证据。",
        "缺失字段最多追问一次，不确定或 grounding 失败时转人工。",
        "内部处理完成后可以生成对应的客户更新。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 659,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/659",
          "label": "PR #659"
        },
        {
          "type": "pr",
          "number": 670,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/670",
          "label": "PR #670"
        },
        {
          "type": "pr",
          "number": 674,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/674",
          "label": "PR #674"
        },
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
          "number": 709,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/709",
          "label": "PR #709"
        }
      ],
      "source_refs": [
        "docs/roadmap/phase2.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从 Phase 2 Enablement handler、字段提取和回复记录补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "enablement-automation"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-automation"
    },
    {
      "schema_version": 2,
      "task_id": "p1-41",
      "title": "实现 Quota 自动化和内部流转",
      "status": "done",
      "owner": "zac",
      "summary": "处理配额审核、并发提升和 Big Event 容量报备，并将结构化信息交给内部团队。",
      "next_action": "",
      "acceptance_criteria": [
        "Quota Case 可以提取产品、App ID、目标容量和活动信息。",
        "缺失字段最多追问一次，随后使用已有信息完成内部流转。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 681,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/681",
          "label": "PR #681"
        }
      ],
      "source_refs": [
        "docs/roadmap/phase2.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从历史 Quota automation handler 和内部流转实现补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "quota-automation"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-automation"
    },
    {
      "schema_version": 2,
      "task_id": "p1-42",
      "title": "建立 Billing 自动化基础处理能力",
      "status": "done",
      "owner": "zac",
      "summary": "将 Billing Case 迁移到 Automated Case，并建立内部请求与结果处理的基础能力。",
      "next_action": "",
      "acceptance_criteria": [
        "Billing Case 可以进入统一 Automated Case 路径。",
        "内部处理请求和结果可以被系统保存并用于后续客户更新。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 657,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/657",
          "label": "PR #657"
        },
        {
          "type": "pr",
          "number": 568,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/568",
          "label": "PR #568"
        },
        {
          "type": "pr",
          "number": 572,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/572",
          "label": "PR #572"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/phase2.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从 Billing Case migration 和 automation foundation 实现补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "billing-automation-foundation"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-automation"
    },
    {
      "schema_version": 2,
      "task_id": "p1-43",
      "title": "建立 Billing Outlook 回复和 PDF 结果闭环",
      "status": "done",
      "owner": "zac",
      "summary": "通过 Outlook 接收内部处理结果，解析 PDF 附件并生成受控客户跟进。",
      "next_action": "",
      "acceptance_criteria": [
        "Billing 内部请求通过公司 Outlook 发送并可轮询回复。",
        "回复正文和 PDF 附件可以被解析并用于客户结果更新。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 568,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/568",
          "label": "PR #568"
        },
        {
          "type": "pr",
          "number": 570,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/570",
          "label": "PR #570"
        },
        {
          "type": "pr",
          "number": 572,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/572",
          "label": "PR #572"
        },
        {
          "type": "pr",
          "number": 574,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/574",
          "label": "PR #574"
        },
        {
          "type": "pr",
          "number": 575,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/575",
          "label": "PR #575"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/roadmap/phase2.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从 Billing Outlook、轮询、OCR 和 PDF 转发实现补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "billing-outlook-loop"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-44",
      "title": "实现一次追问和持久化延迟回复",
      "status": "done",
      "owner": "zac",
      "summary": "对缺失字段进行有界追问，并以持久化 scheduled job 发送延迟客户回复。",
      "next_action": "",
      "acceptance_criteria": [
        "同一规范化字段在一个 Ticket 内最多追问一次。",
        "自动化回复使用持久化调度、幂等 claim 和新消息取消机制。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 530,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/530",
          "label": "PR #530"
        },
        {
          "type": "pr",
          "number": 647,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/647",
          "label": "PR #647"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从 Account 标准流程和 delayed reply 实现补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "account-delayed-reply"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-45",
      "title": "隔离注册 Automation 执行与回复幂等",
      "status": "done",
      "owner": "zac",
      "summary": "只对注册的 Automation handler 执行内部动作和客户回复，并保护回复调度的幂等边界。",
      "next_action": "",
      "acceptance_criteria": [
        "未注册路径不会误触发 Automation handler 或客户回复。",
        "重复 webhook、回复 worker 和 Persona job 不会重复发送结果。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 661,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/661",
          "label": "PR #661"
        },
        {
          "type": "pr",
          "number": 671,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/671",
          "label": "PR #671"
        },
        {
          "type": "pr",
          "number": 672,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/672",
          "label": "PR #672"
        },
        {
          "type": "pr",
          "number": 705,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/705",
          "label": "PR #705"
        },
        {
          "type": "pr",
          "number": 710,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/710",
          "label": "PR #710"
        },
        {
          "type": "pr",
          "number": 725,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/725",
          "label": "PR #725"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从 layered route pipeline、handler binding 和回复生命周期实现补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "registered-automation-execution"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-46",
      "title": "统一 Zendesk Ticket 身份和 Account 来源",
      "status": "done",
      "owner": "zac",
      "summary": "优先使用 Zendesk external ID 或来源 Ticket number，统一 Account 列表与详情中的 canonical Ticket ID。",
      "next_action": "",
      "acceptance_criteria": [
        "重复 webhook 使用同一 Zendesk Ticket 身份，不重复建单。",
        "列表、详情和来源链接展示一致的 Ticket number。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 481,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/481",
          "label": "PR #481"
        },
        {
          "type": "pr",
          "number": 492,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/492",
          "label": "PR #492"
        },
        {
          "type": "pr",
          "number": 649,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/649",
          "label": "PR #649"
        },
        {
          "type": "pr",
          "number": 663,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/663",
          "label": "PR #663"
        },
        {
          "type": "pr",
          "number": 703,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/703",
          "label": "PR #703"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从 Zendesk external ID、Ticket number 和 Account identity 实现补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "account-ticket-identity"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "zendesk-connection"
    },
    {
      "schema_version": 2,
      "task_id": "p1-47",
      "title": "建立 Zendesk Account 评论快照同步",
      "status": "done",
      "owner": "zac",
      "summary": "将 Zendesk public/internal comments 幂等同步到 Account 的独立评论 projection。",
      "next_action": "",
      "acceptance_criteria": [
        "评论快照可独立读取并与 Account Case 详情关联。",
        "重复同步不会重复写入，历史评论不会被 Account rerun 删除。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 754,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/754",
          "label": "PR #754"
        }
      ],
      "source_refs": [
        "docs/integrations/n8n/zendesk_account_comment_sync.md",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从 Zendesk Account comment sync 实现补回；作者身份增强仍由 p1-20 跟踪。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "account-comment-sync"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "zendesk-connection"
    },
    {
      "schema_version": 2,
      "task_id": "p1-48",
      "title": "建立 Account Route Strategy 管理面",
      "status": "done",
      "owner": "zac",
      "summary": "在 Agent Config 中统一管理 Account 路由策略、Prompt 版本和 Automation Workflow。",
      "next_action": "",
      "acceptance_criteria": [
        "管理员可以查看分层 Router、路由策略和 Automation Workflow。",
        "Prompt 支持 managed 版本的发布、差异、恢复和历史查看。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 638,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/638",
          "label": "PR #638"
        },
        {
          "type": "pr",
          "number": 645,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/645",
          "label": "PR #645"
        },
        {
          "type": "pr",
          "number": 646,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/646",
          "label": "PR #646"
        },
        {
          "type": "pr",
          "number": 651,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/651",
          "label": "PR #651"
        },
        {
          "type": "pr",
          "number": 689,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/689",
          "label": "PR #689"
        },
        {
          "type": "pr",
          "number": 690,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/690",
          "label": "PR #690"
        }
      ],
      "source_refs": [
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "backfilled",
          "summary": "从 Agent Config、Route Strategy 和 Prompt 管理实现补回。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "route-strategy-admin"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
    },
    {
      "schema_version": 2,
      "task_id": "p2-31",
      "title": "Client 对话支持图片和更多日志附件",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / client-experience / client-conversation-experience。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "client-rich-attachments"
      ],
      "phase_id": "phase-2",
      "module_id": "client-experience",
      "function_id": "client-conversation-experience"
    },
    {
      "schema_version": 2,
      "task_id": "p2-32",
      "title": "Client 对话支持流式输出",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "seeded",
          "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / client-experience / client-conversation-experience。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "client-streaming-output"
      ],
      "phase_id": "phase-2",
      "module_id": "client-experience",
      "function_id": "client-conversation-experience"
    },
    {
      "schema_version": 2,
      "task_id": "p2-37",
      "title": "将 KG Ingest 后台任务化并联动 RAG 入库",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-ingestion-pipeline。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-async-ingest"
        }
      ],
      "legacy_ids": [
        "kg-async-ingest"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-ingestion-pipeline"
    },
    {
      "schema_version": 2,
      "task_id": "p2-38",
      "title": "完成 GraphRAG Ingest 模型 Bake-off",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-ingestion-pipeline。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-ingest-model-bakeoff"
        }
      ],
      "legacy_ids": [
        "kg-ingest-model-bakeoff"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-ingestion-pipeline"
    },
    {
      "schema_version": 2,
      "task_id": "p2-39",
      "title": "在本地 Neo4j 构建完整官方文档知识图谱",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-ingestion-pipeline。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-offline-graph-build"
        }
      ],
      "legacy_ids": [
        "kg-offline-graph-build"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-ingestion-pipeline"
    },
    {
      "schema_version": 2,
      "task_id": "p2-40",
      "title": "建立 RAG 文档去重、主题归并与冲突审查",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-ingestion-pipeline。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "rag-dedupe"
        }
      ],
      "legacy_ids": [
        "rag-dedupe"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-ingestion-pipeline"
    },
    {
      "schema_version": 2,
      "task_id": "p2-41",
      "title": "用真实查询评估 RAG 与 RAG+KG 的效果门禁",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-evaluation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-benchmark-ab"
        }
      ],
      "legacy_ids": [
        "kg-benchmark-ab"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-evaluation"
    },
    {
      "schema_version": 2,
      "task_id": "p2-42",
      "title": "保证 KG 客户答案引用回到官网文档证据",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-evaluation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "状态从 blocked 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-citations"
        }
      ],
      "legacy_ids": [
        "kg-citations"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-evaluation"
    },
    {
      "schema_version": 2,
      "task_id": "p2-43",
      "title": "建立 KG Shadow 与小流量灰度门禁",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-evaluation。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-grey-gate"
        }
      ],
      "legacy_ids": [
        "kg-grey-gate"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-evaluation"
    },
    {
      "schema_version": 2,
      "task_id": "p2-44",
      "title": "评估 AWS Neptune 作为生产图数据库",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-production-platform。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-graph-db"
        }
      ],
      "legacy_ids": [
        "kg-graph-db"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-production-platform"
    },
    {
      "schema_version": 2,
      "task_id": "p2-45",
      "title": "固化生产 KG 配置、Secret 与环境变量边界",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-production-platform。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-model-config"
        }
      ],
      "legacy_ids": [
        "kg-model-config"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-production-platform"
    },
    {
      "schema_version": 2,
      "task_id": "p2-46",
      "title": "定义 Client AI 与 Engineer AI 的 KG 使用边界",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane rag-vs-kg 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-2 / rag-knowledge / rag-scope-governance。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "rag-vs-kg",
          "item_id": "kg-engineer-vs-client"
        }
      ],
      "legacy_ids": [
        "kg-engineer-vs-client"
      ],
      "phase_id": "phase-2",
      "module_id": "rag-knowledge",
      "function_id": "rag-scope-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-47",
      "title": "收口 billing route 验证、邮件回执和 Dashboard 三项 POC。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-billing-poc。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-01 迁移为 p2-47；迁移到 phase-2 / agent-collaboration / agent-billing-poc。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-01"
        }
      ],
      "legacy_ids": [
        "AG-01",
        "p3-01"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-billing-poc"
    },
    {
      "schema_version": 2,
      "task_id": "p2-48",
      "title": "验证邮件回执轮询、SLA 提醒和未回复 fallback。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-billing-poc。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-02 迁移为 p2-48；迁移到 phase-2 / agent-collaboration / agent-billing-poc。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-04"
        }
      ],
      "legacy_ids": [
        "AG-04",
        "p3-02"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-billing-poc"
    },
    {
      "schema_version": 2,
      "task_id": "p2-49",
      "title": "确定 fully_automated、ai_draft_human_approve、unable_to_resolve_handoff 的边界。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-03 迁移为 p2-49；迁移到 phase-2 / agent-collaboration / agent-governance。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-02"
        }
      ],
      "legacy_ids": [
        "AG-02",
        "p3-03"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-50",
      "title": "建立 customer-facing 与 internal-facing 的敏感信息隔离和审计边界。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-04 迁移为 p2-50；迁移到 phase-2 / agent-collaboration / agent-governance。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-03"
        }
      ],
      "legacy_ids": [
        "AG-03",
        "p3-04"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-51",
      "title": "用少量真实 Case 对比保守 workflow、成熟 agent 框架和研发 agent 接入方式。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-05 迁移为 p2-51；迁移到 phase-2 / agent-collaboration / agent-governance。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-05"
        }
      ],
      "legacy_ids": [
        "AG-05",
        "p3-05"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-52",
      "title": "在真实 evidence tools、replay gate、权限审计和成本门禁达标前保持 AgentRelay 自主调查为长期计划。",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Meeting agent-system-2026-06-18 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-06 迁移为 p2-52；迁移到 phase-2 / agent-collaboration / agent-governance；状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap/meetings.html",
          "meeting_id": "agent-system-2026-06-18",
          "item_id": "AG-06"
        }
      ],
      "legacy_ids": [
        "AG-06",
        "p3-06"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-53",
      "title": "在证据与审计门禁下扩展 Agent-to-Agent 自主调查",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-07 迁移为 p2-53；迁移到 phase-2 / agent-collaboration / agent-governance。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-agent-to-agent-governed-autonomy"
        }
      ],
      "legacy_ids": [
        "ma-agent-to-agent-governed-autonomy",
        "p3-07"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-54",
      "title": "用 Claim-to-Evidence 校验客户回复",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-08 迁移为 p2-54；迁移到 phase-2 / agent-collaboration / agent-governance。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-guardrail-claim-evidence"
        }
      ],
      "legacy_ids": [
        "ma-guardrail-claim-evidence",
        "p3-08"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-55",
      "title": "统一 Route Taxonomy 与 Multi-Agent 生命周期契约",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-governance。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-09 迁移为 p2-55；迁移到 phase-2 / agent-collaboration / agent-governance。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-rollout-taxonomy-contract"
        }
      ],
      "legacy_ids": [
        "ma-rollout-taxonomy-contract",
        "p3-09"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-56",
      "title": "为 Execute Agent 接入真实证据工具",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-evidence-evaluation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-10 迁移为 p2-56；迁移到 phase-2 / agent-collaboration / agent-evidence-evaluation。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-real-evidence-tools"
        }
      ],
      "legacy_ids": [
        "ma-real-evidence-tools",
        "p3-10"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-evidence-evaluation"
    },
    {
      "schema_version": 2,
      "task_id": "p2-57",
      "title": "建立 Engineer AI Replay 评测与回归门禁",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-evidence-evaluation。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-11 迁移为 p2-57；迁移到 phase-2 / agent-collaboration / agent-evidence-evaluation。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-replay-runner"
        }
      ],
      "legacy_ids": [
        "ma-replay-runner",
        "p3-11"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-evidence-evaluation"
    },
    {
      "schema_version": 2,
      "task_id": "p2-58",
      "title": "为 Multi-Agent 调查恢复受控 Targeted Replan",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-controlled-replan。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-12 迁移为 p2-58；迁移到 phase-2 / agent-collaboration / agent-governance；Function agent-controlled-replan 合并到 agent-governance。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-controlled-replan"
        }
      ],
      "legacy_ids": [
        "ma-controlled-replan",
        "p3-12"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-59",
      "title": "将 AgentRelay 多角色协作接入 Support Case",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agentrelay-integration。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-13 迁移为 p2-59；迁移到 phase-2 / agent-collaboration / agentrelay-integration。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-agentrelay-support-integration"
        }
      ],
      "legacy_ids": [
        "ma-agentrelay-support-integration",
        "p3-13"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agentrelay-integration"
    },
    {
      "schema_version": 2,
      "task_id": "p2-60",
      "title": "将 Multi-Agent Run 面板升级为工程师行动台",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane engineer-multi-agent 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / agent-collaboration / agent-workspace-console。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-14 迁移为 p2-60；迁移到 phase-2 / agent-collaboration / agent-workspace-console。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "engineer-multi-agent",
          "item_id": "ma-workspace-action-console"
        }
      ],
      "legacy_ids": [
        "ma-workspace-action-console",
        "p3-14"
      ],
      "phase_id": "phase-2",
      "module_id": "agent-collaboration",
      "function_id": "agent-workspace-console"
    },
    {
      "schema_version": 2,
      "task_id": "p2-61",
      "title": "为 Zendesk Intake 增加 AI Eligibility Gate",
      "status": "planned",
      "owner": "unassigned",
      "summary": "Phase 2：为 Zendesk intake 增加 AI eligibility gate，大客户、明显生气或高风险客户暂不进入 AI 处理。",
      "next_action": "Phase 2：为 Zendesk intake 增加 AI eligibility gate，大客户、明显生气或高风险客户暂不进入 AI 处理。",
      "acceptance_criteria": [
        "完成 Eligibility 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-ai-intake。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-15 迁移为 p2-61；迁移到 phase-2 / engineer-workspace / engineer-ai-intake。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-phase3-eligibility"
        }
      ],
      "legacy_ids": [
        "assign-phase3-eligibility",
        "p3-15"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-ai-intake"
    },
    {
      "schema_version": 2,
      "task_id": "p2-62",
      "title": "AI 完成首次有效回复后自动转交工程师",
      "status": "planned",
      "owner": "unassigned",
      "summary": "Phase 2：AI 只完成首次有效回复和必要信息收集，随后将 case assign 给工程师。",
      "next_action": "Phase 2：AI 只完成首次有效回复和必要信息收集，随后将 case assign 给工程师。",
      "acceptance_criteria": [
        "完成 First Reply 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-ai-intake。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-16 迁移为 p2-62；迁移到 phase-2 / engineer-workspace / engineer-ai-intake。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-phase3-first-reply"
        }
      ],
      "legacy_ids": [
        "assign-phase3-first-reply",
        "p3-16"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-ai-intake"
    },
    {
      "schema_version": 2,
      "task_id": "p2-63",
      "title": "加固生产认证 Secret、Session 失效与 RBAC 验证",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-case-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-17 迁移为 p2-63；迁移到 phase-2 / engineer-workspace / engineer-case-delivery；状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-auth-hardening"
        }
      ],
      "legacy_ids": [
        "assign-auth-hardening",
        "p3-17"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-case-delivery"
    },
    {
      "schema_version": 2,
      "task_id": "p2-64",
      "title": "清理 Legacy Engineer Case 状态、旧 UI 与兼容逻辑",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-case-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-18 迁移为 p2-64；迁移到 phase-2 / engineer-workspace / engineer-case-delivery。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-legacy-cleanup"
        }
      ],
      "legacy_ids": [
        "assign-legacy-cleanup",
        "p3-18"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-case-delivery"
    },
    {
      "schema_version": 2,
      "task_id": "p2-65",
      "title": "在真实 PostgreSQL 环境验证派单与审计写入",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-case-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-19 迁移为 p2-65；迁移到 phase-2 / engineer-workspace / engineer-case-delivery；状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-live-postgres"
        }
      ],
      "legacy_ids": [
        "assign-live-postgres",
        "p3-19"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-case-delivery"
    },
    {
      "schema_version": 2,
      "task_id": "p2-66",
      "title": "完善 Engineer 派单、SLA 与排班覆盖指标",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-case-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-20 迁移为 p2-66；迁移到 phase-2 / engineer-workspace / engineer-case-delivery；状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-metrics"
        }
      ],
      "legacy_ids": [
        "assign-metrics",
        "p3-20"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-case-delivery"
    },
    {
      "schema_version": 2,
      "task_id": "p2-67",
      "title": "在 Admin Dashboard 展示并同步 Slack 派单状态",
      "status": "planned",
      "owner": "unassigned",
      "summary": "Phase 2：Admin Dashboard 补充 Slack 送达状态，并把 Admin-only reassign 结果同步到 Slack。",
      "next_action": "Phase 2：Admin Dashboard 补充 Slack 送达状态，并把 Admin-only reassign 结果同步到 Slack。",
      "acceptance_criteria": [
        "完成 Admin 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-case-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-21 迁移为 p2-67；迁移到 phase-2 / engineer-workspace / engineer-case-delivery。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-phase3-admin-sync"
        }
      ],
      "legacy_ids": [
        "assign-phase3-admin-sync",
        "p3-21"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-case-delivery"
    },
    {
      "schema_version": 2,
      "task_id": "p2-68",
      "title": "通过 Slack 向工程师送达 Round Robin 派单",
      "status": "planned",
      "owner": "unassigned",
      "summary": "Phase 2：验证 Slack bot 权限与消息承载模型，将 Round Robin 派单结果和 Zendesk ticket 关联信息送达工程师。",
      "next_action": "Phase 2：验证 Slack bot 权限与消息承载模型，将 Round Robin 派单结果和 Zendesk ticket 关联信息送达工程师。",
      "acceptance_criteria": [
        "完成 Slack 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-case-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-22 迁移为 p2-68；迁移到 phase-2 / engineer-workspace / engineer-case-delivery。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-phase3-slack"
        }
      ],
      "legacy_ids": [
        "assign-phase3-slack",
        "p3-22"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-case-delivery"
    },
    {
      "schema_version": 2,
      "task_id": "p2-69",
      "title": "从 10% Engineer Case 试运行逐步切换到全量派单",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane assignment-ui 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-case-delivery。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-23 迁移为 p2-69；迁移到 phase-2 / engineer-workspace / engineer-case-delivery；状态从 active 调整为 planned（未开始）。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "assignment-ui",
          "item_id": "assign-rollout"
        }
      ],
      "legacy_ids": [
        "assign-rollout",
        "p3-23"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-case-delivery"
    },
    {
      "schema_version": 2,
      "task_id": "p2-70",
      "title": "设计 Account Human Review 到 Engineer Case 的显式交接",
      "status": "planned",
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
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-16",
          "event": "migrated",
          "summary": "从 Roadmap lane billing-routing 迁移。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "迁移到 phase-3 / engineer-workspace / engineer-case-handoff。"
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Task ID 从 p3-24 迁移为 p2-70；迁移到 phase-2 / engineer-workspace / engineer-ai-intake；Function engineer-case-handoff 合并到 engineer-ai-intake。"
        }
      ],
      "legacy_refs": [
        {
          "source": "docs/roadmap.html",
          "lane_id": "billing-routing",
          "item_id": "billing-human-review-handoff"
        }
      ],
      "legacy_ids": [
        "billing-human-review-handoff",
        "p3-24"
      ],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-ai-intake"
    }
  ],
  "meetings": [
    {
      "schema_version": 2,
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
        "p1-01",
        "p1-02",
        "p1-18",
        "p1-33",
        "p1-15",
        "p1-19",
        "p1-28",
        "p1-29",
        "p1-09",
        "p1-03",
        "p1-14",
        "p1-21"
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "legacy_anchor": "./roadmap/meetings.html#ticketing-system-2026-08-10",
      "function_ids": [],
      "legacy_task_ids": [
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
      ]
    },
    {
      "schema_version": 2,
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
        "p2-47",
        "p2-49",
        "p2-50",
        "p2-48",
        "p2-51",
        "p2-52"
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#agent-system-2026-06-18"
      ],
      "legacy_anchor": "./roadmap/meetings.html#agent-system-2026-06-18",
      "function_ids": [],
      "legacy_task_ids": [
        "AG-01",
        "AG-02",
        "AG-03",
        "AG-04",
        "AG-05",
        "AG-06"
      ]
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
    "schema_version": 2,
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
        "path": "ui/client-ui",
        "module_ids": [
          "client-experience"
        ]
      },
      {
        "id": "account-surface",
        "label": "/account",
        "layer": "surface",
        "path": "ui/account-ui",
        "module_ids": [
          "account-automation"
        ]
      },
      {
        "id": "workspace-surface",
        "label": "/workspace",
        "layer": "surface",
        "path": "ui/workspace-ui",
        "module_ids": [
          "engineer-workspace"
        ]
      },
      {
        "id": "admin-surface",
        "label": "/workspace/admin",
        "layer": "surface",
        "path": "ui/workspace-ui/admin",
        "module_ids": [
          "admin-operations"
        ]
      },
      {
        "id": "rag-surface",
        "label": "/dashboard/rag",
        "layer": "surface",
        "path": "ui/dashboard-ui/rag",
        "module_ids": [
          "rag-knowledge"
        ]
      },
      {
        "id": "support-api",
        "label": "FastAPI routes",
        "layer": "api",
        "path": "backend/main.py",
        "module_ids": [
          "client-experience",
          "account-automation",
          "engineer-workspace",
          "admin-operations"
        ]
      },
      {
        "id": "router-services",
        "label": "Routing / intake",
        "layer": "service",
        "path": "backend/services/support_router.py",
        "module_ids": [
          "client-experience",
          "account-automation"
        ]
      },
      {
        "id": "engineer-services",
        "label": "Engineer / Guardrail",
        "layer": "service",
        "path": "backend/services",
        "module_ids": [
          "engineer-workspace",
          "agent-collaboration"
        ]
      },
      {
        "id": "rag-services",
        "label": "RAG / KG runtime",
        "layer": "service",
        "path": "backend/services/kg_runtime.py",
        "module_ids": [
          "rag-knowledge"
        ]
      },
      {
        "id": "postgres",
        "label": "PostgreSQL repositories",
        "layer": "data",
        "path": "backend/repositories",
        "module_ids": [
          "platform-delivery",
          "account-automation",
          "engineer-workspace"
        ]
      },
      {
        "id": "zendesk",
        "label": "Zendesk",
        "layer": "external",
        "path": null,
        "module_ids": [
          "client-experience",
          "account-automation"
        ]
      },
      {
        "id": "tests",
        "label": "Contract and unit tests",
        "layer": "test",
        "path": "backend/tests",
        "module_ids": [
          "platform-delivery"
        ]
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
    "schema_version": 2,
    "summaries": {
      "426": {
        "summary": "建立 Account Case 接入入口。",
        "task_ids": [
          "p1-37"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-case-intake"
        ]
      },
      "515": {
        "summary": "支持 Account 路由纠正。",
        "task_ids": [
          "p1-38"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-route-review"
        ]
      },
      "526": {
        "summary": "增加 Account 路由审核状态和筛选。",
        "task_ids": [
          "p1-38"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-route-review"
        ]
      },
      "665": {
        "summary": "增加 Account 路由标签筛选。",
        "task_ids": [
          "p1-39"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-route-filters"
        ]
      },
      "659": {
        "summary": "增加 Enablement Automation handler。",
        "task_ids": [
          "p1-40"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "enablement-automation"
        ]
      },
      "681": {
        "summary": "增加 Account Quota 自动化。",
        "task_ids": [
          "p1-41"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "quota-automation"
        ]
      },
      "657": {
        "summary": "将 Billing Case 迁移到 Automated Case。",
        "task_ids": [
          "p1-42"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "billing-automation-foundation"
        ]
      },
      "568": {
        "summary": "通过 Outlook Graph 发送 Billing 内部自动化邮件。",
        "task_ids": [
          "p1-43"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "billing-outlook-loop"
        ]
      },
      "530": {
        "summary": "限制 Account 缺失字段重复追问。",
        "task_ids": [
          "p1-44"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-delayed-reply"
        ]
      },
      "661": {
        "summary": "建立分层 Account route pipeline。",
        "task_ids": [
          "p1-45"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "registered-automation-execution"
        ]
      },
      "649": {
        "summary": "使用 Zendesk external ID 作为 Account Ticket ID。",
        "task_ids": [
          "p1-46"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-ticket-identity"
        ]
      },
      "754": {
        "summary": "同步 Zendesk Account 评论快照。",
        "task_ids": [
          "p1-47"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-comment-sync"
        ]
      },
      "638": {
        "summary": "增加 Account Automation 管理控制能力。",
        "task_ids": [
          "p1-48"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "route-strategy-admin"
        ]
      },
      "752": {
        "summary": "将 Account AI 消息以幂等 internal comment 写回关联 Zendesk ticket。",
        "task_ids": [
          "p1-18",
          "p1-28"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "TS-03",
          "TS-07"
        ]
      },
      "751": {
        "summary": "为 Account Persona reply job 增加版本 fence，避免旧 job 覆盖新状态。",
        "task_ids": [
          "p1-11"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "billing-idempotency"
        ]
      },
      "750": {
        "summary": "改善 Account Automation 对客户的 ownership 回复，统一由 Persona 生成安全的状态说明。",
        "task_ids": [
          "p1-12"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "billing-persona-registry"
        ]
      },
      "749": {
        "summary": "补齐 Account Automation 客户 ownership 回复和处理中的反馈节奏。",
        "task_ids": [
          "p1-12"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "billing-persona-registry"
        ]
      },
      "748": {
        "summary": "修复 Account rerun 邮件 claim 恢复路径，避免重复或丢失内部处理。",
        "task_ids": [
          "p1-10"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-rerun-recovery"
        ]
      },
      "747": {
        "summary": "修复 Account rerun 客户回复的 scheduled reply 调度。",
        "task_ids": [
          "p1-10"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-rerun-recovery"
        ]
      },
      "746": {
        "summary": "修复 Account rerun immutable result 的处理和状态边界。",
        "task_ids": [
          "p1-10"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-rerun-recovery"
        ]
      },
      "745": {
        "summary": "修复 Account rerun 失败降级和容器 OpenAI proxy 配置。",
        "task_ids": [
          "p1-10"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-rerun-recovery"
        ]
      },
      "744": {
        "summary": "Account 失败在重试耗尽后停止客户回复、进入 Human Review 并告警负责人。",
        "task_ids": [
          "p1-15"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "TS-05",
          "account-failure-alerts"
        ]
      },
      "743": {
        "summary": "在 Admin Automated Cases 中增加可直接打开的 Zendesk Source 链接。",
        "task_ids": [
          "p1-28"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "TS-07"
        ]
      },
      "742": {
        "summary": "统一 Account rerun revision 时间戳，避免恢复和排序出现不一致。",
        "task_ids": [
          "p1-10"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-rerun-recovery"
        ]
      },
      "741": {
        "summary": "让 Account rerun preflight 在网络条件变化时保持可验证的失败边界。",
        "task_ids": [
          "p1-10"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-rerun-recovery"
        ]
      },
      "740": {
        "summary": "允许操作员对每个 Account Case 执行完整 rerun，并保留独立审计。",
        "task_ids": [
          "p1-10"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-rerun-recovery"
        ]
      },
      "739": {
        "summary": "修复被阻塞 Account rerun 的反馈和可恢复提示。",
        "task_ids": [
          "p1-10"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-rerun-recovery"
        ]
      },
      "738": {
        "summary": "加固 Account rerun fail-fast recovery 和 Luna routing 选择。",
        "task_ids": [
          "p1-10"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "account-rerun-recovery"
        ]
      },
      "737": {
        "summary": "Admin Source 不再重复展示内部 Account Case ID。",
        "task_ids": [
          "p1-28"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "TS-07"
        ]
      },
      "736": {
        "summary": "支持把序列化 JSON 形式的 Account Source 解析成 Zendesk 链接。",
        "task_ids": [
          "p1-28"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "TS-07"
        ]
      },
      "735": {
        "summary": "为 Account Case Source 增加 Zendesk 直达链接。",
        "task_ids": [
          "p1-28"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "TS-07"
        ]
      },
      "734": {
        "summary": "简化 Agent 热路径规则，并把详细流程转移到可按需读取的文档。",
        "task_ids": [
          "p1-34"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "agent-rules"
        ]
      },
      "733": {
        "summary": "更新 Meeting 进度页面、导航和 Work Item 展示。",
        "task_ids": [
          "p1-35"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "project-overview"
        ]
      },
      "732": {
        "summary": "隔离 Account route validation 的副作用，避免校验行为改变真实状态。",
        "task_ids": [
          "p1-11"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "billing-idempotency"
        ]
      },
      "731": {
        "summary": "加固 Account route taxonomy 和 filter membership，区分 Automation、Human Review 和 fallback。",
        "task_ids": [],
        "schema_version": 2,
        "function_ids": [
          "case-route"
        ],
        "legacy_task_ids": [
          "routing-taxonomy"
        ]
      },
      "730": {
        "summary": "新增 SupportPortal Meeting archive，集中呈现 Function、结论和 Task。",
        "task_ids": [
          "p1-35"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "project-overview"
        ]
      },
      "729": {
        "summary": "增加 Security & Compliance route，并将敏感请求保持在 classification-only / Human Review 边界。",
        "task_ids": [
          "p1-02"
        ],
        "schema_version": 2,
        "function_ids": [],
        "legacy_task_ids": [
          "routing-security-compliance"
        ]
      },
      "728": {
        "summary": "增加 Backend Operation filter 和 Automated labels，统一显示 Enablement、Quota 等路径。",
        "task_ids": [],
        "schema_version": 2,
        "function_ids": [
          "case-route"
        ],
        "legacy_task_ids": [
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
    "source_count": 150,
    "generated_from": [
      "docs/roadmap.html",
      "docs/roadmap/meetings.html",
      "docs/roadmap/phase2.html",
      "docs/roadmap/phase3.html",
      "docs/feature_list.md"
    ],
    "records": [
      {
        "source_ref": "docs/project/tasks/AG-01.json",
        "legacy_id": "AG-01",
        "target_type": "task",
        "target_id": "p2-47",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/AG-02.json",
        "legacy_id": "AG-02",
        "target_type": "task",
        "target_id": "p2-49",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/AG-03.json",
        "legacy_id": "AG-03",
        "target_type": "task",
        "target_id": "p2-50",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/AG-04.json",
        "legacy_id": "AG-04",
        "target_type": "task",
        "target_id": "p2-48",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/AG-05.json",
        "legacy_id": "AG-05",
        "target_type": "task",
        "target_id": "p2-51",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/AG-06.json",
        "legacy_id": "AG-06",
        "target_type": "task",
        "target_id": "p2-52",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/TS-01.json",
        "legacy_id": "TS-01",
        "target_type": "task",
        "target_id": "p1-01",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-02.json",
        "legacy_id": "TS-02",
        "target_type": "task",
        "target_id": "p1-02",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-03.json",
        "legacy_id": "TS-03",
        "target_type": "task",
        "target_id": "p1-18",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-04.json",
        "legacy_id": "TS-04",
        "target_type": "task",
        "target_id": "p1-33",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-05.json",
        "legacy_id": "TS-05",
        "target_type": "task",
        "target_id": "p1-15",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-06.json",
        "legacy_id": "TS-06",
        "target_type": "task",
        "target_id": "p1-19",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-07.json",
        "legacy_id": "TS-07",
        "target_type": "task",
        "target_id": "p1-28",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-08.json",
        "legacy_id": "TS-08",
        "target_type": "task",
        "target_id": "p1-09",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-09.json",
        "legacy_id": "TS-09",
        "target_type": "task",
        "target_id": "p1-03",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-10.json",
        "legacy_id": "TS-10",
        "target_type": "task",
        "target_id": "p1-14",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-11.json",
        "legacy_id": "TS-11",
        "target_type": "task",
        "target_id": "p1-21",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/TS-12.json",
        "legacy_id": "TS-12",
        "target_type": "task",
        "target_id": "p1-29",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/account-failure-alerts.json",
        "legacy_id": "account-failure-alerts",
        "target_type": "task",
        "target_id": "p1-15",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/tasks/account-rerun-recovery.json",
        "legacy_id": "account-rerun-recovery",
        "target_type": "task",
        "target_id": "p1-10",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/admin-environment-config-inventory.json",
        "legacy_id": "admin-environment-config-inventory",
        "target_type": "task",
        "target_id": "p1-30",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/agent-rules.json",
        "legacy_id": "agent-rules",
        "target_type": "task",
        "target_id": "p1-34",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/assign-auth-hardening.json",
        "legacy_id": "assign-auth-hardening",
        "target_type": "task",
        "target_id": "p2-63",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/assign-legacy-cleanup.json",
        "legacy_id": "assign-legacy-cleanup",
        "target_type": "task",
        "target_id": "p2-64",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/assign-live-postgres.json",
        "legacy_id": "assign-live-postgres",
        "target_type": "task",
        "target_id": "p2-65",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/assign-metrics.json",
        "legacy_id": "assign-metrics",
        "target_type": "task",
        "target_id": "p2-66",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/assign-phase3-admin-sync.json",
        "legacy_id": "assign-phase3-admin-sync",
        "target_type": "task",
        "target_id": "p2-67",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/assign-phase3-eligibility.json",
        "legacy_id": "assign-phase3-eligibility",
        "target_type": "task",
        "target_id": "p2-61",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/assign-phase3-first-reply.json",
        "legacy_id": "assign-phase3-first-reply",
        "target_type": "task",
        "target_id": "p2-62",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/assign-phase3-slack.json",
        "legacy_id": "assign-phase3-slack",
        "target_type": "task",
        "target_id": "p2-68",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/assign-rollout.json",
        "legacy_id": "assign-rollout",
        "target_type": "task",
        "target_id": "p2-69",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/billing-dashboard-metrics.json",
        "legacy_id": "billing-dashboard-metrics",
        "target_type": "task",
        "target_id": "p1-22",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/billing-expand.json",
        "legacy_id": "billing-expand",
        "target_type": "task",
        "target_id": "p1-23",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/billing-human-review.json",
        "legacy_id": "billing-human-review",
        "target_type": "task",
        "target_id": "p1-16",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/billing-human-review-handoff.json",
        "legacy_id": "billing-human-review-handoff",
        "target_type": "task",
        "target_id": "p2-70",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/billing-idempotency.json",
        "legacy_id": "billing-idempotency",
        "target_type": "task",
        "target_id": "p1-11",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/billing-monitor-automation-outcomes.json",
        "legacy_id": "billing-monitor-automation-outcomes",
        "target_type": "task",
        "target_id": "p1-24",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/billing-monitor-replay-quality.json",
        "legacy_id": "billing-monitor-replay-quality",
        "target_type": "task",
        "target_id": "p1-05",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/billing-persona-registry.json",
        "legacy_id": "billing-persona-registry",
        "target_type": "task",
        "target_id": "p1-12",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/billing-recipient-env.json",
        "legacy_id": "billing-recipient-env",
        "target_type": "task",
        "target_id": "p1-13",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/client-rich-attachments.json",
        "legacy_id": "client-rich-attachments",
        "target_type": "task",
        "target_id": "p2-31",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/client-streaming-output.json",
        "legacy_id": "client-streaming-output",
        "target_type": "task",
        "target_id": "p2-32",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/kg-async-ingest.json",
        "legacy_id": "kg-async-ingest",
        "target_type": "task",
        "target_id": "p2-37",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/kg-benchmark-ab.json",
        "legacy_id": "kg-benchmark-ab",
        "target_type": "task",
        "target_id": "p2-41",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/kg-citations.json",
        "legacy_id": "kg-citations",
        "target_type": "task",
        "target_id": "p2-42",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/kg-engineer-vs-client.json",
        "legacy_id": "kg-engineer-vs-client",
        "target_type": "task",
        "target_id": "p2-46",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/kg-graph-db.json",
        "legacy_id": "kg-graph-db",
        "target_type": "task",
        "target_id": "p2-44",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/kg-grey-gate.json",
        "legacy_id": "kg-grey-gate",
        "target_type": "task",
        "target_id": "p2-43",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/kg-ingest-model-bakeoff.json",
        "legacy_id": "kg-ingest-model-bakeoff",
        "target_type": "task",
        "target_id": "p2-38",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/kg-model-config.json",
        "legacy_id": "kg-model-config",
        "target_type": "task",
        "target_id": "p2-45",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/kg-offline-graph-build.json",
        "legacy_id": "kg-offline-graph-build",
        "target_type": "task",
        "target_id": "p2-39",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/ma-agent-to-agent-governed-autonomy.json",
        "legacy_id": "ma-agent-to-agent-governed-autonomy",
        "target_type": "task",
        "target_id": "p2-53",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/ma-agentrelay-support-integration.json",
        "legacy_id": "ma-agentrelay-support-integration",
        "target_type": "task",
        "target_id": "p2-59",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/ma-controlled-replan.json",
        "legacy_id": "ma-controlled-replan",
        "target_type": "task",
        "target_id": "p2-58",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/ma-guardrail-claim-evidence.json",
        "legacy_id": "ma-guardrail-claim-evidence",
        "target_type": "task",
        "target_id": "p2-54",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/ma-real-evidence-tools.json",
        "legacy_id": "ma-real-evidence-tools",
        "target_type": "task",
        "target_id": "p2-56",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/ma-replay-runner.json",
        "legacy_id": "ma-replay-runner",
        "target_type": "task",
        "target_id": "p2-57",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/ma-rollout-taxonomy-contract.json",
        "legacy_id": "ma-rollout-taxonomy-contract",
        "target_type": "task",
        "target_id": "p2-55",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/ma-workspace-action-console.json",
        "legacy_id": "ma-workspace-action-console",
        "target_type": "task",
        "target_id": "p2-60",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/phase2-fraud-field-contract.json",
        "legacy_id": "phase2-fraud-field-contract",
        "target_type": "task",
        "target_id": "p1-03",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/tasks/project-overview.json",
        "legacy_id": "project-overview",
        "target_type": "task",
        "target_id": "p1-35",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/project-task-title-cleanup.json",
        "legacy_id": "project-task-title-cleanup",
        "target_type": "task",
        "target_id": "p1-36",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/rag-dedupe.json",
        "legacy_id": "rag-dedupe",
        "target_type": "task",
        "target_id": "p2-40",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/routing-automation-rollout.json",
        "legacy_id": "routing-automation-rollout",
        "target_type": "task",
        "target_id": "p1-25",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/routing-billing-review-customer-experience.json",
        "legacy_id": "routing-billing-review-customer-experience",
        "target_type": "task",
        "target_id": "p1-17",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/routing-billing-risky-negatives.json",
        "legacy_id": "routing-billing-risky-negatives",
        "target_type": "task",
        "target_id": "p1-06",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/routing-dashboard-metrics.json",
        "legacy_id": "routing-dashboard-metrics",
        "target_type": "task",
        "target_id": "p1-26",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/routing-fallback-billing-risk-sniff.json",
        "legacy_id": "routing-fallback-billing-risk-sniff",
        "target_type": "function",
        "target_id": "case-route",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/tasks/routing-real-zendesk-replay.json",
        "legacy_id": "routing-real-zendesk-replay",
        "target_type": "task",
        "target_id": "p1-07",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/routing-rollout-taxonomy.json",
        "legacy_id": "routing-rollout-taxonomy",
        "target_type": "task",
        "target_id": "p1-27",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/routing-security-compliance.json",
        "legacy_id": "routing-security-compliance",
        "target_type": "task",
        "target_id": "p1-02",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/tasks/routing-semantic-golden-expand.json",
        "legacy_id": "routing-semantic-golden-expand",
        "target_type": "task",
        "target_id": "p1-08",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/routing-taxonomy.json",
        "legacy_id": "routing-taxonomy",
        "target_type": "function",
        "target_id": "case-route",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/tasks/zendesk-account-comment-identity.json",
        "legacy_id": "zendesk-account-comment-identity",
        "target_type": "task",
        "target_id": "p1-20",
        "disposition": "renamed"
      },
      {
        "source_ref": "docs/project/tasks/p2-01.json",
        "legacy_id": "p2-01",
        "target_type": "task",
        "target_id": "p1-01",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-02.json",
        "legacy_id": "p2-02",
        "target_type": "task",
        "target_id": "p1-02",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-03.json",
        "legacy_id": "p2-03",
        "target_type": "task",
        "target_id": "p1-03",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-04.json",
        "legacy_id": "p2-04",
        "target_type": "task",
        "target_id": "p1-04",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-05.json",
        "legacy_id": "p2-05",
        "target_type": "task",
        "target_id": "p1-05",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-06.json",
        "legacy_id": "p2-06",
        "target_type": "task",
        "target_id": "p1-06",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-07.json",
        "legacy_id": "p2-07",
        "target_type": "task",
        "target_id": "p1-07",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-08.json",
        "legacy_id": "p2-08",
        "target_type": "task",
        "target_id": "p1-08",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-09.json",
        "legacy_id": "p2-09",
        "target_type": "task",
        "target_id": "p1-09",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-10.json",
        "legacy_id": "p2-10",
        "target_type": "task",
        "target_id": "p1-10",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-11.json",
        "legacy_id": "p2-11",
        "target_type": "task",
        "target_id": "p1-11",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-12.json",
        "legacy_id": "p2-12",
        "target_type": "task",
        "target_id": "p1-12",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-13.json",
        "legacy_id": "p2-13",
        "target_type": "task",
        "target_id": "p1-13",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-14.json",
        "legacy_id": "p2-14",
        "target_type": "task",
        "target_id": "p1-14",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-15.json",
        "legacy_id": "p2-15",
        "target_type": "task",
        "target_id": "p1-15",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-16.json",
        "legacy_id": "p2-16",
        "target_type": "task",
        "target_id": "p1-16",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-17.json",
        "legacy_id": "p2-17",
        "target_type": "task",
        "target_id": "p1-17",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-18.json",
        "legacy_id": "p2-18",
        "target_type": "task",
        "target_id": "p1-18",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-19.json",
        "legacy_id": "p2-19",
        "target_type": "task",
        "target_id": "p1-19",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-20.json",
        "legacy_id": "p2-20",
        "target_type": "task",
        "target_id": "p1-20",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-21.json",
        "legacy_id": "p2-21",
        "target_type": "task",
        "target_id": "p1-21",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-22.json",
        "legacy_id": "p2-22",
        "target_type": "task",
        "target_id": "p1-22",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-23.json",
        "legacy_id": "p2-23",
        "target_type": "task",
        "target_id": "p1-23",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-24.json",
        "legacy_id": "p2-24",
        "target_type": "task",
        "target_id": "p1-24",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-25.json",
        "legacy_id": "p2-25",
        "target_type": "task",
        "target_id": "p1-25",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-26.json",
        "legacy_id": "p2-26",
        "target_type": "task",
        "target_id": "p1-26",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-27.json",
        "legacy_id": "p2-27",
        "target_type": "task",
        "target_id": "p1-27",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-28.json",
        "legacy_id": "p2-28",
        "target_type": "task",
        "target_id": "p1-28",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-29.json",
        "legacy_id": "p2-29",
        "target_type": "task",
        "target_id": "p1-29",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-30.json",
        "legacy_id": "p2-30",
        "target_type": "task",
        "target_id": "p1-30",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-33.json",
        "legacy_id": "p2-33",
        "target_type": "task",
        "target_id": "p1-33",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-34.json",
        "legacy_id": "p2-34",
        "target_type": "task",
        "target_id": "p1-34",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-35.json",
        "legacy_id": "p2-35",
        "target_type": "task",
        "target_id": "p1-35",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p2-36.json",
        "legacy_id": "p2-36",
        "target_type": "task",
        "target_id": "p1-36",
        "disposition": "moved-to-phase-1"
      },
      {
        "source_ref": "docs/project/tasks/p3-01.json",
        "legacy_id": "p3-01",
        "target_type": "task",
        "target_id": "p2-47",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-02.json",
        "legacy_id": "p3-02",
        "target_type": "task",
        "target_id": "p2-48",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-03.json",
        "legacy_id": "p3-03",
        "target_type": "task",
        "target_id": "p2-49",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-04.json",
        "legacy_id": "p3-04",
        "target_type": "task",
        "target_id": "p2-50",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-05.json",
        "legacy_id": "p3-05",
        "target_type": "task",
        "target_id": "p2-51",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-06.json",
        "legacy_id": "p3-06",
        "target_type": "task",
        "target_id": "p2-52",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-07.json",
        "legacy_id": "p3-07",
        "target_type": "task",
        "target_id": "p2-53",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-08.json",
        "legacy_id": "p3-08",
        "target_type": "task",
        "target_id": "p2-54",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-09.json",
        "legacy_id": "p3-09",
        "target_type": "task",
        "target_id": "p2-55",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-10.json",
        "legacy_id": "p3-10",
        "target_type": "task",
        "target_id": "p2-56",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-11.json",
        "legacy_id": "p3-11",
        "target_type": "task",
        "target_id": "p2-57",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-12.json",
        "legacy_id": "p3-12",
        "target_type": "task",
        "target_id": "p2-58",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-13.json",
        "legacy_id": "p3-13",
        "target_type": "task",
        "target_id": "p2-59",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-14.json",
        "legacy_id": "p3-14",
        "target_type": "task",
        "target_id": "p2-60",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-15.json",
        "legacy_id": "p3-15",
        "target_type": "task",
        "target_id": "p2-61",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-16.json",
        "legacy_id": "p3-16",
        "target_type": "task",
        "target_id": "p2-62",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-17.json",
        "legacy_id": "p3-17",
        "target_type": "task",
        "target_id": "p2-63",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-18.json",
        "legacy_id": "p3-18",
        "target_type": "task",
        "target_id": "p2-64",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-19.json",
        "legacy_id": "p3-19",
        "target_type": "task",
        "target_id": "p2-65",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-20.json",
        "legacy_id": "p3-20",
        "target_type": "task",
        "target_id": "p2-66",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-21.json",
        "legacy_id": "p3-21",
        "target_type": "task",
        "target_id": "p2-67",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-22.json",
        "legacy_id": "p3-22",
        "target_type": "task",
        "target_id": "p2-68",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-23.json",
        "legacy_id": "p3-23",
        "target_type": "task",
        "target_id": "p2-69",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/p3-24.json",
        "legacy_id": "p3-24",
        "target_type": "task",
        "target_id": "p2-70",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/functions/agent-controlled-replan.json",
        "legacy_id": "agent-controlled-replan",
        "target_type": "function",
        "target_id": "agent-governance",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/functions/engineer-case-handoff.json",
        "legacy_id": "engineer-case-handoff",
        "target_type": "function",
        "target_id": "engineer-ai-intake",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/functions/automation-execution.json",
        "legacy_id": "automation-execution",
        "target_type": "function",
        "target_id": "automation-execution-loop",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/functions/controlled-rollout.json",
        "legacy_id": "controlled-rollout",
        "target_type": "function",
        "target_id": "case-automation",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/functions/routing-quality-validation.json",
        "legacy_id": "routing-quality-validation",
        "target_type": "function",
        "target_id": "case-route",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/functions/zendesk-delivery.json",
        "legacy_id": "zendesk-delivery",
        "target_type": "function",
        "target_id": "zendesk-connection",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/tasks/p1-37.json",
        "legacy_id": "account-case-intake",
        "target_type": "task",
        "target_id": "p1-37",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-38.json",
        "legacy_id": "account-route-review",
        "target_type": "task",
        "target_id": "p1-38",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-39.json",
        "legacy_id": "account-route-filters",
        "target_type": "task",
        "target_id": "p1-39",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-40.json",
        "legacy_id": "enablement-automation",
        "target_type": "task",
        "target_id": "p1-40",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-41.json",
        "legacy_id": "quota-automation",
        "target_type": "task",
        "target_id": "p1-41",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-42.json",
        "legacy_id": "billing-automation-foundation",
        "target_type": "task",
        "target_id": "p1-42",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-43.json",
        "legacy_id": "billing-outlook-loop",
        "target_type": "task",
        "target_id": "p1-43",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-44.json",
        "legacy_id": "account-delayed-reply",
        "target_type": "task",
        "target_id": "p1-44",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-45.json",
        "legacy_id": "registered-automation-execution",
        "target_type": "task",
        "target_id": "p1-45",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-46.json",
        "legacy_id": "account-ticket-identity",
        "target_type": "task",
        "target_id": "p1-46",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-47.json",
        "legacy_id": "account-comment-sync",
        "target_type": "task",
        "target_id": "p1-47",
        "disposition": "backfilled"
      },
      {
        "source_ref": "docs/project/tasks/p1-48.json",
        "legacy_id": "route-strategy-admin",
        "target_type": "task",
        "target_id": "p1-48",
        "disposition": "backfilled"
      }
    ],
    "aliases": {
      "AG-01": {
        "target_type": "task",
        "target_id": "p2-47"
      },
      "AG-02": {
        "target_type": "task",
        "target_id": "p2-49"
      },
      "AG-03": {
        "target_type": "task",
        "target_id": "p2-50"
      },
      "AG-04": {
        "target_type": "task",
        "target_id": "p2-48"
      },
      "AG-05": {
        "target_type": "task",
        "target_id": "p2-51"
      },
      "AG-06": {
        "target_type": "task",
        "target_id": "p2-52"
      },
      "TS-01": {
        "target_type": "task",
        "target_id": "p1-01"
      },
      "TS-02": {
        "target_type": "task",
        "target_id": "p1-02"
      },
      "TS-03": {
        "target_type": "task",
        "target_id": "p1-18"
      },
      "TS-04": {
        "target_type": "task",
        "target_id": "p1-33"
      },
      "TS-05": {
        "target_type": "task",
        "target_id": "p1-15"
      },
      "TS-06": {
        "target_type": "task",
        "target_id": "p1-19"
      },
      "TS-07": {
        "target_type": "task",
        "target_id": "p1-28"
      },
      "TS-08": {
        "target_type": "task",
        "target_id": "p1-09"
      },
      "TS-09": {
        "target_type": "task",
        "target_id": "p1-03"
      },
      "TS-10": {
        "target_type": "task",
        "target_id": "p1-14"
      },
      "TS-11": {
        "target_type": "task",
        "target_id": "p1-21"
      },
      "TS-12": {
        "target_type": "task",
        "target_id": "p1-29"
      },
      "account-failure-alerts": {
        "target_type": "task",
        "target_id": "p1-15"
      },
      "account-rerun-recovery": {
        "target_type": "task",
        "target_id": "p1-10"
      },
      "admin-environment-config-inventory": {
        "target_type": "task",
        "target_id": "p1-30"
      },
      "agent-rules": {
        "target_type": "task",
        "target_id": "p1-34"
      },
      "assign-auth-hardening": {
        "target_type": "task",
        "target_id": "p2-63"
      },
      "assign-legacy-cleanup": {
        "target_type": "task",
        "target_id": "p2-64"
      },
      "assign-live-postgres": {
        "target_type": "task",
        "target_id": "p2-65"
      },
      "assign-metrics": {
        "target_type": "task",
        "target_id": "p2-66"
      },
      "assign-phase3-admin-sync": {
        "target_type": "task",
        "target_id": "p2-67"
      },
      "assign-phase3-eligibility": {
        "target_type": "task",
        "target_id": "p2-61"
      },
      "assign-phase3-first-reply": {
        "target_type": "task",
        "target_id": "p2-62"
      },
      "assign-phase3-slack": {
        "target_type": "task",
        "target_id": "p2-68"
      },
      "assign-rollout": {
        "target_type": "task",
        "target_id": "p2-69"
      },
      "billing-dashboard-metrics": {
        "target_type": "task",
        "target_id": "p1-22"
      },
      "billing-expand": {
        "target_type": "task",
        "target_id": "p1-23"
      },
      "billing-human-review": {
        "target_type": "task",
        "target_id": "p1-16"
      },
      "billing-human-review-handoff": {
        "target_type": "task",
        "target_id": "p2-70"
      },
      "billing-idempotency": {
        "target_type": "task",
        "target_id": "p1-11"
      },
      "billing-monitor-automation-outcomes": {
        "target_type": "task",
        "target_id": "p1-24"
      },
      "billing-monitor-replay-quality": {
        "target_type": "task",
        "target_id": "p1-05"
      },
      "billing-persona-registry": {
        "target_type": "task",
        "target_id": "p1-12"
      },
      "billing-recipient-env": {
        "target_type": "task",
        "target_id": "p1-13"
      },
      "client-rich-attachments": {
        "target_type": "task",
        "target_id": "p2-31"
      },
      "client-streaming-output": {
        "target_type": "task",
        "target_id": "p2-32"
      },
      "kg-async-ingest": {
        "target_type": "task",
        "target_id": "p2-37"
      },
      "kg-benchmark-ab": {
        "target_type": "task",
        "target_id": "p2-41"
      },
      "kg-citations": {
        "target_type": "task",
        "target_id": "p2-42"
      },
      "kg-engineer-vs-client": {
        "target_type": "task",
        "target_id": "p2-46"
      },
      "kg-graph-db": {
        "target_type": "task",
        "target_id": "p2-44"
      },
      "kg-grey-gate": {
        "target_type": "task",
        "target_id": "p2-43"
      },
      "kg-ingest-model-bakeoff": {
        "target_type": "task",
        "target_id": "p2-38"
      },
      "kg-model-config": {
        "target_type": "task",
        "target_id": "p2-45"
      },
      "kg-offline-graph-build": {
        "target_type": "task",
        "target_id": "p2-39"
      },
      "ma-agent-to-agent-governed-autonomy": {
        "target_type": "task",
        "target_id": "p2-53"
      },
      "ma-agentrelay-support-integration": {
        "target_type": "task",
        "target_id": "p2-59"
      },
      "ma-controlled-replan": {
        "target_type": "task",
        "target_id": "p2-58"
      },
      "ma-guardrail-claim-evidence": {
        "target_type": "task",
        "target_id": "p2-54"
      },
      "ma-real-evidence-tools": {
        "target_type": "task",
        "target_id": "p2-56"
      },
      "ma-replay-runner": {
        "target_type": "task",
        "target_id": "p2-57"
      },
      "ma-rollout-taxonomy-contract": {
        "target_type": "task",
        "target_id": "p2-55"
      },
      "ma-workspace-action-console": {
        "target_type": "task",
        "target_id": "p2-60"
      },
      "phase2-fraud-field-contract": {
        "target_type": "task",
        "target_id": "p1-03"
      },
      "project-overview": {
        "target_type": "task",
        "target_id": "p1-35"
      },
      "project-task-title-cleanup": {
        "target_type": "task",
        "target_id": "p1-36"
      },
      "rag-dedupe": {
        "target_type": "task",
        "target_id": "p2-40"
      },
      "routing-automation-rollout": {
        "target_type": "task",
        "target_id": "p1-25"
      },
      "routing-billing-review-customer-experience": {
        "target_type": "task",
        "target_id": "p1-17"
      },
      "routing-billing-risky-negatives": {
        "target_type": "task",
        "target_id": "p1-06"
      },
      "routing-dashboard-metrics": {
        "target_type": "task",
        "target_id": "p1-26"
      },
      "routing-fallback-billing-risk-sniff": {
        "target_type": "function",
        "target_id": "case-route"
      },
      "routing-real-zendesk-replay": {
        "target_type": "task",
        "target_id": "p1-07"
      },
      "routing-rollout-taxonomy": {
        "target_type": "task",
        "target_id": "p1-27"
      },
      "routing-security-compliance": {
        "target_type": "task",
        "target_id": "p1-02"
      },
      "routing-semantic-golden-expand": {
        "target_type": "task",
        "target_id": "p1-08"
      },
      "routing-taxonomy": {
        "target_type": "function",
        "target_id": "case-route"
      },
      "zendesk-account-comment-identity": {
        "target_type": "task",
        "target_id": "p1-20"
      },
      "p2-01": {
        "target_type": "task",
        "target_id": "p1-01"
      },
      "p2-02": {
        "target_type": "task",
        "target_id": "p1-02"
      },
      "p2-03": {
        "target_type": "task",
        "target_id": "p1-03"
      },
      "p2-04": {
        "target_type": "task",
        "target_id": "p1-04"
      },
      "p2-05": {
        "target_type": "task",
        "target_id": "p1-05"
      },
      "p2-06": {
        "target_type": "task",
        "target_id": "p1-06"
      },
      "p2-07": {
        "target_type": "task",
        "target_id": "p1-07"
      },
      "p2-08": {
        "target_type": "task",
        "target_id": "p1-08"
      },
      "p2-09": {
        "target_type": "task",
        "target_id": "p1-09"
      },
      "p2-10": {
        "target_type": "task",
        "target_id": "p1-10"
      },
      "p2-11": {
        "target_type": "task",
        "target_id": "p1-11"
      },
      "p2-12": {
        "target_type": "task",
        "target_id": "p1-12"
      },
      "p2-13": {
        "target_type": "task",
        "target_id": "p1-13"
      },
      "p2-14": {
        "target_type": "task",
        "target_id": "p1-14"
      },
      "p2-15": {
        "target_type": "task",
        "target_id": "p1-15"
      },
      "p2-16": {
        "target_type": "task",
        "target_id": "p1-16"
      },
      "p2-17": {
        "target_type": "task",
        "target_id": "p1-17"
      },
      "p2-18": {
        "target_type": "task",
        "target_id": "p1-18"
      },
      "p2-19": {
        "target_type": "task",
        "target_id": "p1-19"
      },
      "p2-20": {
        "target_type": "task",
        "target_id": "p1-20"
      },
      "p2-21": {
        "target_type": "task",
        "target_id": "p1-21"
      },
      "p2-22": {
        "target_type": "task",
        "target_id": "p1-22"
      },
      "p2-23": {
        "target_type": "task",
        "target_id": "p1-23"
      },
      "p2-24": {
        "target_type": "task",
        "target_id": "p1-24"
      },
      "p2-25": {
        "target_type": "task",
        "target_id": "p1-25"
      },
      "p2-26": {
        "target_type": "task",
        "target_id": "p1-26"
      },
      "p2-27": {
        "target_type": "task",
        "target_id": "p1-27"
      },
      "p2-28": {
        "target_type": "task",
        "target_id": "p1-28"
      },
      "p2-29": {
        "target_type": "task",
        "target_id": "p1-29"
      },
      "p2-30": {
        "target_type": "task",
        "target_id": "p1-30"
      },
      "p2-33": {
        "target_type": "task",
        "target_id": "p1-33"
      },
      "p2-34": {
        "target_type": "task",
        "target_id": "p1-34"
      },
      "p2-35": {
        "target_type": "task",
        "target_id": "p1-35"
      },
      "p2-36": {
        "target_type": "task",
        "target_id": "p1-36"
      },
      "p3-01": {
        "target_type": "task",
        "target_id": "p2-47"
      },
      "p3-02": {
        "target_type": "task",
        "target_id": "p2-48"
      },
      "p3-03": {
        "target_type": "task",
        "target_id": "p2-49"
      },
      "p3-04": {
        "target_type": "task",
        "target_id": "p2-50"
      },
      "p3-05": {
        "target_type": "task",
        "target_id": "p2-51"
      },
      "p3-06": {
        "target_type": "task",
        "target_id": "p2-52"
      },
      "p3-07": {
        "target_type": "task",
        "target_id": "p2-53"
      },
      "p3-08": {
        "target_type": "task",
        "target_id": "p2-54"
      },
      "p3-09": {
        "target_type": "task",
        "target_id": "p2-55"
      },
      "p3-10": {
        "target_type": "task",
        "target_id": "p2-56"
      },
      "p3-11": {
        "target_type": "task",
        "target_id": "p2-57"
      },
      "p3-12": {
        "target_type": "task",
        "target_id": "p2-58"
      },
      "p3-13": {
        "target_type": "task",
        "target_id": "p2-59"
      },
      "p3-14": {
        "target_type": "task",
        "target_id": "p2-60"
      },
      "p3-15": {
        "target_type": "task",
        "target_id": "p2-61"
      },
      "p3-16": {
        "target_type": "task",
        "target_id": "p2-62"
      },
      "p3-17": {
        "target_type": "task",
        "target_id": "p2-63"
      },
      "p3-18": {
        "target_type": "task",
        "target_id": "p2-64"
      },
      "p3-19": {
        "target_type": "task",
        "target_id": "p2-65"
      },
      "p3-20": {
        "target_type": "task",
        "target_id": "p2-66"
      },
      "p3-21": {
        "target_type": "task",
        "target_id": "p2-67"
      },
      "p3-22": {
        "target_type": "task",
        "target_id": "p2-68"
      },
      "p3-23": {
        "target_type": "task",
        "target_id": "p2-69"
      },
      "p3-24": {
        "target_type": "task",
        "target_id": "p2-70"
      },
      "agent-controlled-replan": {
        "target_type": "function",
        "target_id": "agent-governance"
      },
      "engineer-case-handoff": {
        "target_type": "function",
        "target_id": "engineer-ai-intake"
      },
      "automation-execution": {
        "target_type": "function",
        "target_id": "automation-execution-loop"
      },
      "controlled-rollout": {
        "target_type": "function",
        "target_id": "case-automation"
      },
      "routing-quality-validation": {
        "target_type": "function",
        "target_id": "case-route"
      },
      "zendesk-delivery": {
        "target_type": "function",
        "target_id": "zendesk-connection"
      },
      "account-case-intake": {
        "target_type": "task",
        "target_id": "p1-37"
      },
      "account-route-review": {
        "target_type": "task",
        "target_id": "p1-38"
      },
      "account-route-filters": {
        "target_type": "task",
        "target_id": "p1-39"
      },
      "enablement-automation": {
        "target_type": "task",
        "target_id": "p1-40"
      },
      "quota-automation": {
        "target_type": "task",
        "target_id": "p1-41"
      },
      "billing-automation-foundation": {
        "target_type": "task",
        "target_id": "p1-42"
      },
      "billing-outlook-loop": {
        "target_type": "task",
        "target_id": "p1-43"
      },
      "account-delayed-reply": {
        "target_type": "task",
        "target_id": "p1-44"
      },
      "registered-automation-execution": {
        "target_type": "task",
        "target_id": "p1-45"
      },
      "account-ticket-identity": {
        "target_type": "task",
        "target_id": "p1-46"
      },
      "account-comment-sync": {
        "target_type": "task",
        "target_id": "p1-47"
      },
      "route-strategy-admin": {
        "target_type": "task",
        "target_id": "p1-48"
      }
    }
  }
}
