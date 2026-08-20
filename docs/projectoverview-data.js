window.SUPPORTPORTAL_PROJECT_DATA = {
  "schema_version": 2,
  "generated_at": "2026-08-20T04:06:31Z",
  "source_base_commit": "d58b79f6e8536368f31d138e0c39ca62bc8e5945",
  "registry_digest": "6b8d1e3636c1932432825b2ffe9517d1344f3d970537f68f57a77103b638862a",
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
          "number": 575,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/575",
          "label": "PR #575"
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
          "type": "test",
          "command": "python3 -m py_compile backend/main.py backend/services/internal_email_template.py backend/services/account_verification_automation.py backend/services/billing_automation.py backend/services/enablement_automation.py backend/services/quota_automation.py backend/services/internal_email_payload.py"
        },
        {
          "type": "test",
          "command": "python3 -m unittest backend.tests.test_internal_email_template backend.tests.test_account_verification_automation.AccountVerificationAutomationTests.test_missing_information_is_followed_up_only_once backend.tests.test_enablement_automation.EnablementAutomationTests.test_sample_routes_and_extracts_media_relay backend.tests.test_quota_automation",
          "result": "12 passed"
        },
        {
          "type": "test",
          "command": "Focused email contract smoke: matching Zendesk Ticket IDs render clickable HTML links; mismatched source IDs fail closed.",
          "result": "passed"
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
        },
        {
          "type": "test",
          "label": "Task worktree and direct-to-main repository policy preflight",
          "command": "git status --short --branch; git branch -vv; git worktree list --porcelain; scripts/workflow/bootstrap_main_repo_policy.sh --verify-only",
          "result": "Task worktree and branch are correct; root main is clean at 902f3b5; repository policy verification passed."
        },
        {
          "type": "document",
          "label": "karpathy-guidelines read before runtime edits",
          "command": "Read /Users/xieziling/.codex/skills/karpathy-guidelines/SKILL.md",
          "result": "Complete skill read; runtime implementation must remain surgical, explicit, fail-loud, and test-driven."
        },
        {
          "type": "test",
          "label": "Account Automation integrated targeted suite",
          "command": "python -m unittest backend.tests.test_automation_routing backend.tests.test_automation_persona backend.tests.test_account_reply_version_fence backend.tests.test_account_verification_automation backend.tests.test_enablement_automation backend.tests.test_account_full_reroute backend.tests.test_account_reroute_dispatch backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_account_rerun_recovery",
          "result": "368 tests passed; normal Intake, full rerun, reply-only recovery, Persona v9 contracts, Worker publication gates, the three active Automation flows, and legacy Suspension full-rerun migration are green."
        },
        {
          "type": "test",
          "label": "Account Automation static and registry checks",
          "command": "python -m py_compile backend/main.py backend/worker.py backend/services/automation_persona.py backend/services/account_reply_jobs.py backend/services/account_suspension_automation.py backend/services/account_full_reroute.py; python3 scripts/verify_feature_list.py; python3 scripts/generate_project_overview.py --write; python3 scripts/generate_project_overview.py --check; git diff --check",
          "result": "Python compilation, feature-list validation, Project Overview generation/check, and diff whitespace validation passed."
        },
        {
          "type": "test",
          "label": "Signature source-removal repair preflight",
          "command": "git status --short --branch; git branch -vv; git worktree list --porcelain; scripts/workflow/bootstrap_main_repo_policy.sh --verify-only",
          "result": "Root main and origin/main are synchronized at 3f1f65c; the repair worktree is clean on codex/account-automation-signature-source-removal; repository policy verification passed."
        },
        {
          "type": "test",
          "label": "Persona Signature source-removal focused suite",
          "command": "python -m unittest backend.tests.test_account_admin_features backend.tests.test_workspace_api backend.tests.test_account_persona_postgres backend.tests.test_workspace_admin_ui_contract backend.tests.test_agent_config backend.tests.test_worker.WorkerResilienceTests.test_reply_facts_prepare_pins_persisted_persona_assignment",
          "result": "107 tests passed with 19 environment-dependent PostgreSQL tests skipped; Persona presets, API validation, repository writes/rollback, Admin UI, Agent Config, and runtime prompt projection are green."
        },
        {
          "type": "test",
          "label": "Non-destructive signed reply publication fence",
          "command": "python -m unittest backend.tests.test_automation_persona backend.tests.test_account_reply_version_fence backend.tests.test_worker; python -m py_compile backend/services/automation_persona.py backend/worker.py backend/tests/test_worker.py; git diff --check",
          "result": "120 tests passed; signed generated replies move to Human Review before publish_account_reply, unsigned replies remain unchanged, and Python compilation and diff checks passed."
        },
        {
          "type": "test",
          "label": "Account reply polarity and Enablement current-state validation",
          "command": "python -m unittest backend.tests.test_automation_persona backend.tests.test_enablement_automation backend.tests.test_account_reply_version_fence backend.tests.test_worker; python -m py_compile backend/services/automation_persona.py backend/worker.py backend/tests/test_automation_persona.py backend/tests/test_worker.py; git diff --check",
          "result": "139 tests passed; questions, requests, future activation, negated commitments, and revoked Enablement states no longer satisfy completion, handoff, SLA, or closure contracts."
        },
        {
          "type": "test",
          "label": "Signature source-removal integrated verification",
          "command": "python -m unittest backend.tests.test_account_admin_features backend.tests.test_workspace_admin_ui_contract backend.tests.test_workspace_api backend.tests.test_account_persona_postgres backend.tests.test_agent_config backend.tests.test_automation_routing backend.tests.test_automation_persona backend.tests.test_account_reply_version_fence backend.tests.test_account_verification_automation backend.tests.test_enablement_automation backend.tests.test_account_full_reroute backend.tests.test_account_reroute_dispatch backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_account_rerun_recovery; python -m py_compile backend/main.py backend/worker.py backend/services/account_admin.py backend/services/automation_persona.py backend/services/account_reply_jobs.py backend/services/account_suspension_automation.py backend/services/account_full_reroute.py backend/repositories/ticket_repository.py; node --check ui/workspace-ui/admin/app.js; python3 scripts/verify_feature_list.py; python3 scripts/generate_project_overview.py --write; python3 scripts/generate_project_overview.py --check; git diff --check",
          "result": "478 tests passed with 19 environment-dependent PostgreSQL tests skipped; Python and Node syntax, Feature List, Project Overview, and diff checks passed."
        },
        {
          "type": "test",
          "label": "Persona v11 bounded validation retry targeted suite",
          "command": "python -m pytest -q backend/tests/test_account_ai_execution.py backend/tests/test_automation_persona.py backend/tests/test_worker.py backend/tests/test_account_rerun_fail_fast_resume.py backend/tests/test_account_rerun_recovery.py backend/tests/test_account_reroute_dispatch.py backend/tests/test_account_ui_contract.py",
          "result": "210 tests passed with 29 subtests passed; transport and validation share four calls, exact Fraud handoff output succeeds on the fourth call, exhausted failures retain their code and attempt count, Worker remains fail closed, and failed rerun summaries are observed without historical writes."
        },
        {
          "type": "test",
          "label": "Persona v11 integrated Account Automation verification",
          "command": "python -m pytest -q backend/tests/test_account_ai_execution.py backend/tests/test_account_admin_features.py backend/tests/test_workspace_admin_ui_contract.py backend/tests/test_workspace_api.py backend/tests/test_account_persona_postgres.py backend/tests/test_agent_config.py backend/tests/test_automation_routing.py backend/tests/test_automation_persona.py backend/tests/test_account_reply_version_fence.py backend/tests/test_account_verification_automation.py backend/tests/test_enablement_automation.py backend/tests/test_account_full_reroute.py backend/tests/test_account_reroute_dispatch.py backend/tests/test_account_intake.py backend/tests/test_worker.py backend/tests/test_account_rerun_fail_fast_resume.py backend/tests/test_account_rerun_recovery.py backend/tests/test_account_ui_contract.py",
          "result": "519 tests passed with 19 environment-dependent PostgreSQL tests skipped and 67 subtests passed; greeting-prefixed publication validation, Intake, full rerun, reply-only recovery, Persona assignment, and publication fences are green."
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
      "task_count": 9,
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
        "routing-taxonomy",
        "routing-fallback-billing-risk-sniff",
        "routing-quality-validation",
        "controlled-rollout",
        "routing-billing-risky-negatives",
        "p2-06",
        "routing-real-zendesk-replay",
        "p2-07",
        "routing-semantic-golden-expand",
        "p2-08"
      ],
      "status": "active",
      "task_count": 11,
      "done_count": 10,
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
      "legacy_ids": [
        "billing-human-review",
        "p2-16"
      ],
      "status": "active",
      "task_count": 5,
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
          "type": "pr",
          "number": 752,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/752",
          "label": "PR #752"
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
          "label": "Production Zendesk delivery gate, audit readback, and Admin production-view regressions",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_zendesk_comments.py backend/tests/test_worker.py backend/tests/test_workspace_api.py backend/tests/test_account_admin_features.py -q"
        },
        {
          "type": "test",
          "label": "Post-merge schema bootstrap and production delivery regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_repository_configuration.py backend/tests/test_zendesk_comments.py backend/tests/test_worker.py backend/tests/test_workspace_api.py backend/tests/test_account_admin_features.py -q"
        },
        {
          "type": "test",
          "label": "Queued publication intent, atomic delivery claim, recovery drain, and production timeout regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_worker.py backend/tests/test_repository_configuration.py backend/tests/test_account_ui_contract.py -q",
          "result": "230 passed, 9 subtests passed"
        },
        {
          "type": "test",
          "label": "Full Zendesk, Worker, Repository, Workspace API, Account Admin, and Account UI regression suite",
          "command": "source /Users/xieziling/Desktop/personal_proj/SupportPortal/.env && /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_repository_configuration.py backend/tests/test_zendesk_comments.py backend/tests/test_account_zendesk_comment.py backend/tests/test_worker.py backend/tests/test_workspace_api.py backend/tests/test_account_admin_features.py backend/tests/test_account_ui_contract.py -q",
          "result": "297 passed, 4 warnings, 22 subtests passed"
        },
        {
          "type": "test",
          "label": "Shared API-backed Account/Zendesk service, production publication, PostgreSQL contract, and UI timeout regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/pytest -q backend/tests/test_repository_configuration.py backend/tests/test_zendesk_comments.py backend/tests/test_account_zendesk_comment.py backend/tests/test_account_zendesk_internal_comment_service.py backend/tests/test_worker.py backend/tests/test_workspace_api.py backend/tests/test_account_admin_features.py backend/tests/test_account_ui_contract.py",
          "result": "308 passed, 4 warnings, 22 subtests passed"
        },
        {
          "type": "test",
          "label": "PostgreSQL publication and shared Zendesk result persistence integration coverage",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/pytest -q backend/tests/test_account_reply_publication_postgres.py",
          "result": "8 skipped without RUN_POSTGRES_INTEGRATION=1; no external Zendesk writes"
        },
        {
          "type": "pr",
          "number": 792,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/792",
          "label": "PR #792"
        },
        {
          "type": "deployment",
          "label": "Post-merge official single-host stack verification",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh",
          "result": "official_project=deployment; image, health, and runtime build ref 4623389cde87 matched; auxiliary_stack_present=false"
        },
        {
          "type": "deployment",
          "label": "Single-case Production Zendesk private-comment recovery",
          "result": "AC-PRD-12838 / Zendesk ticket 12838 / message 1372 had no local ledger or prior private-comment readback; the /account Admin API returned added with comment 52663858132628, local idempotency and message meta persisted, and Zendesk audits read back the same private comment"
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
        },
        {
          "type": "test",
          "label": "Zendesk AI assignment service and Account API tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_zendesk_assignment -q"
        },
        {
          "type": "test",
          "label": "Account UI assignment contract tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_ui_contract -q"
        },
        {
          "type": "test",
          "label": "Account Zendesk assignment regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_zendesk_comments backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_zendesk_assignment backend.tests.test_account_ui_contract -q"
        },
        {
          "type": "test",
          "label": "Zendesk assignee identity and error classification regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_zendesk_assignment -q"
        },
        {
          "type": "test",
          "label": "Feature list and Project Overview validation",
          "command": "python3 scripts/verify_feature_list.py && python3 scripts/generate_project_overview.py --check"
        },
        {
          "type": "test",
          "label": "Task worktree preflight",
          "command": "git status --short --branch; git worktree list",
          "result": "Worktree production-automated-public-replies created from clean main 8a746a7 on branch codex/production-automated-public-replies."
        },
        {
          "type": "test",
          "label": "Targeted automation and Zendesk delivery suite",
          "command": ".venv/bin/python -m pytest -q backend/tests/test_zendesk_ticket_assignment.py backend/tests/test_account_zendesk_assignment.py backend/tests/test_account_zendesk_internal_comment_service.py backend/tests/test_account_zendesk_comment.py backend/tests/test_account_zendesk_comment_sync.py backend/tests/test_account_reply_publication_postgres.py backend/tests/test_automation_persona.py backend/tests/test_account_verification_automation.py backend/tests/test_enablement_automation.py backend/tests/test_account_intake.py backend/tests/test_account_full_reroute.py backend/tests/test_account_reroute_dispatch.py backend/tests/test_worker.py backend/tests/test_account_ui_contract.py backend/tests/test_production_ui_contract.py backend/tests/test_repository_configuration.py backend/tests/test_workspace_api.py backend/tests/test_account_reply_version_fence.py backend/tests/test_zendesk_comments.py backend/tests/test_zendesk_public_comment.py backend/tests/test_account_automation_ownership.py",
          "result": "623 passed, 8 skipped (PostgreSQL opt-in), 56 subtests passed."
        },
        {
          "type": "test",
          "label": "PostgreSQL integration (isolated schema) including the confirmed_at timestamptz regression",
          "command": "RUN_POSTGRES_INTEGRATION=1 .venv/bin/python -m pytest -q backend/tests/test_account_reply_publication_postgres.py",
          "result": "8 passed. The record_account_zendesk_internal_comment_result test fails on main with psycopg DatatypeMismatch and passes in this worktree, confirming the confirmed_at::timestamptz fix against real PostgreSQL."
        },
        {
          "type": "test",
          "label": "Static and registry checks",
          "command": ".venv/bin/python -m py_compile backend/main.py backend/worker.py backend/repositories/ticket_repository.py backend/services/account_automation_ownership.py backend/services/account_zendesk_internal_comment.py backend/services/zendesk_comments.py backend/services/zendesk_ticket_assignment.py backend/services/automation_persona.py; node --check ui/account-ui/app.js; node --check ui/production-ui/app.js; python3 scripts/verify_feature_list.py; python3 scripts/generate_project_overview.py --write; python3 scripts/generate_project_overview.py --check; git diff --check",
          "result": "All checks passed."
        },
        {
          "type": "deployment",
          "label": "Merged PRs and deployed builds",
          "command": "gh pr view 803/804/805/808/809/811/812",
          "result": "PR #803 (core), #804 (solve checkbox + ownership context carry), #805 (completion trigger currency), #808 (custom_fields solve + closed_at column), #809/#811 (audit readback signature tolerance), #812 (jsonb brace escape in close SQL) all merged; EC2 running cc36eb5 (build ref cc36eb552055) with local stack restarted to the same main; migrations 2026_08_19_production_public_zendesk_delivery.sql and 2026_08_20_support_tickets_closed_at.sql applied to both databases."
        },
        {
          "type": "deployment",
          "label": "Live acceptance matrix on tickets 12838/12839/12864/12865",
          "command": "psql production ledger + tickets; Zendesk API readback",
          "result": "12838 enablement: public submission comment 52671546896660 with 24h + Mon-Fri, ticket intentionally open; 12864 fraud: public handoff comment 52671576049812 with exact 'The relevant team will contact you within 24 hours.', no email resend, ticket open; 12839 enablement completion: internal reply consumed end-to-end, public completion comment 52704906920980, Zendesk solved, local resolved with closed_at 2026-08-20T04:00:20Z; 12865 suspension: confirmation -> single internal email -> public closing comment 52705195825556, Zendesk solved, local resolved with closed_at 2026-08-20T04:00:19Z. All four tickets owned by AI agent 48557297720084. Legacy stuck AC-12839 private delivery reconciled to delivered without resend; historical private rows stayed private."
        },
        {
          "type": "test",
          "label": "Live-discovered repair loop (post-deploy fixes)",
          "command": "pytest targeted suites per fix PR",
          "result": "Six follow-up defects found live and fixed with regression tests: solve required checkbox must use custom_fields array (flat field_\u003cid> silently ignored), support_tickets lacked closed_at, audit readback must tolerate platform-appended signatures (prefix match), jsonb path braces must be escaped in close SQL, enablement completion trigger must equal the latest customer message timestamp, fraud attempt merge dropped ownership context. All suites green after each fix."
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
      "task_count": 7,
      "done_count": 6,
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
        },
        {
          "type": "test",
          "label": "Endpoint payload + admin UI contract",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_workspace_api backend.tests.test_workspace_admin_ui_contract",
          "details": "54 全绿。endpoint 测试扩展为 4 个 seed（fraud/enablement automated、account_suspension not_automated、human_review 不进桶），断言 automation_subcategories 固定顺序、零填充、每行 total/automated/not_automated/rate，且 route_status=automated 筛选下分类计数不变（全量口径）；UI 契约新增静态 markers（automation_subcategories、Automation category metrics）与 runtime 断言（三张卡文案、'Automated 1 · 50.0%'、数据缺失时区块不渲染）。"
        },
        {
          "type": "test",
          "label": "Contract regression",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_production_ui_contract backend.tests.test_account_ui_contract backend.tests.test_account_admin_features && node --check ui/workspace-ui/admin/app.js",
          "details": "79 全绿 + app.js 语法检查通过：production/account UI 契约与 account admin 行为回归不受影响。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official-stack restart & build provenance",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && curl /health",
          "details": "PR #806 合并后从根 main（6c7902fb7f41）重启官方栈：deployment 模式、无辅助栈；镜像 tag 与 /health app_build.ref 均为 6c7902fb7f41，status ok、runtime_profile local_lightweight。"
        },
        {
          "type": "deployment",
          "label": "Live automation_subcategories matches production database",
          "command": "psql 等价直查 PRODUCTION_TICKET_DB_DSN + bootstrap admin 登录调用 GET /api/workspace/admin/account-automation",
          "details": "production 库直查与 admin API 返回逐条一致：fraud_account total=1/automated=1（AC-12864）、enablement total=2/automated=2（AC-12839/12838）、account_suspension total=1/automated=1（AC-12865），三行 rate 均 1.0；子类 rag 的 AC-12807 正确不进任何桶。页面卡片渲染由 UI 契约 runtime 用例直接验证（IAB 内嵌浏览器无法提交登录表单，属工具限制）。"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10",
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "legacy_ids": [],
      "status": "active",
      "task_count": 7,
      "done_count": 3,
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
        },
        {
          "type": "test",
          "label": "Single-host restart reliability regression coverage",
          "command": "python3 -m unittest backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_rebuilds_with_current_main_build_metadata backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_health_failure_restores_previous_image backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_same_tag_failure_restores_previous_image_id backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_rejects_remote_health_missing_contract backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_rejects_active_deploy_lock",
          "details": "覆盖 core-first/worker-second 启动、严格 remote health、回滚阶段顺序、活动部署锁和 stale lock 隔离。"
        },
        {
          "type": "test",
          "label": "Workflow and shell validation",
          "command": "python3 -m unittest backend.tests.test_workflow_scripts.WorkflowScriptTests && bash -n scripts/workflow/restart_single_host_stack.sh"
        },
        {
          "type": "test",
          "label": "Bootstrap CLI and Compose profile contract",
          "command": "python3 -m unittest backend.tests.test_single_host_compose && python3 -m unittest backend.tests.test_runtime_bootstrap",
          "details": "验证 runtime_bootstrap profile、串行 repository 初始化、异常清理和 check-only 不调用 DDL。当地环境缺少 psycopg 时 CLI 测试会跳过。"
        },
        {
          "type": "test",
          "label": "Runtime schema preflight wiring",
          "command": "python3 -m py_compile backend/services/runtime_schema.py backend/services/prompt_runtime.py backend/main.py backend/rag_api.py backend/rag_worker.py backend/worker.py && git diff --check",
          "details": "验证 RUNTIME_SCHEMA_MODE=check 的只读门禁、四个运行入口的 DDL 跳过分支、RAG telemetry 运行时准备和默认 bootstrap 模式兼容。"
        },
        {
          "type": "test",
          "label": "Isolated PostgreSQL bootstrap and runtime preflight",
          "command": "podman run --rm --network host -e TICKET_DB_DSN=postgresql://supportportal:supportportal@127.0.0.1:25432/supportportal -e PGVECTOR_DSN=postgresql://supportportal:supportportal@127.0.0.1:25432/supportportal python -m backend.scripts.runtime_bootstrap bootstrap && python -m backend.scripts.runtime_bootstrap check-only",
          "details": "真实 pgvector/pg16 验证了四个 repository 串行 bootstrap、Prompt catalog sync、只读 check-only、缺失 vector 表 fail-closed，以及两个并发 bootstrap 的幂等与 advisory-lock 串行化。"
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
      "status": "active",
      "task_count": 4,
      "done_count": 3,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "account-controlled-rollout",
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "title": "Account Automation 受控扩围",
      "goal": "根据真实运行质量和业务决策，安全扩大 Account Automation 的处理范围。",
      "acceptance_criteria": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes",
        "docs/roadmap/phase2.html",
        "docs/feature_list.md"
      ],
      "legacy_ids": [
        "routing-automation-rollout",
        "routing-rollout-taxonomy",
        "p2-25",
        "p2-27"
      ],
      "status": "planned",
      "task_count": 1,
      "done_count": 0,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "account-production-environment",
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "title": "Account Production 独立环境",
      "goal": "提供与 /account 功能对等但数据与副作用隔离的 /production 独立环境，n8n 可直接将工单转发到 production 数据库并自动投递真实 Zendesk internal comment。",
      "acceptance_criteria": [],
      "evidence": [
        {
          "type": "test",
          "label": "Production UI/deploy contract",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_production_ui_contract backend.tests.test_account_ui_contract backend.tests.test_single_host_compose",
          "details": "10+全绿：/production mount 与三件套存在、标题/版本串、API 前缀 withProductionApiBase、promote 代码不存在（app.js/styles.css）、node --check、compose profile 门控与 PRODUCTION_TICKET_DB_DSN、nginx /production 路由与变量 upstream、deploy 脚本 profile 门禁与 DSN 相异校验、.env.example 文档。test_single_host_compose 的 runtime image 计数契约已扩展纳入三个 production 服务。"
        },
        {
          "type": "test",
          "label": "Default processing profile behavior",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_intake",
          "details": "164 全绿，含新增 3 例：ACCOUNT_DEFAULT_PROCESSING_PROFILE=production 时 POST /account 落库 production 档案且 zendesk_ticket_id 取 external_id；未设置 env 时保持 staging 且 zendesk_ticket_id 为空；GET /api/account/cases 默认档案随 env 切换（production 时可见、staging 时不可见）。失败持久化路径同样保留 profile 与 zendesk_ticket_id。"
        },
        {
          "type": "test",
          "label": "Regression suites around changed paths",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_repository_configuration backend.tests.test_account_zendesk_comment backend.tests.test_workspace_api backend.tests.test_bootstrap_auto_deploy_ec2",
          "details": "全绿（86+142+部署契约），证明 Zendesk 投递、publication 事务台账、workspace admin 与部署脚本回归安全。test_workflow_scripts 存在 5 个与本次无关的环境性失败（干净 main 上同样失败，已对照验证）。"
        },
        {
          "type": "test",
          "label": "Forward contract (UI + backend removal + nginx)",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_ui_contract backend.tests.test_production_ui_contract",
          "details": "42 全绿：account-ui 含 forwardAccountCaseToProduction/PRODUCTION_FORWARD_TIMEOUT_MS=300_000/POST /production/account/超时提示 Open /production//幂等 toast/无 Zendesk 号就地产错；account-ui 与 backend/main.py 均断言无 promote-production 残留；nginx intake location 断言 proxy_read_timeout 300s。"
        },
        {
          "type": "test",
          "label": "Regression suites around removed endpoint",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_reply_publication_postgres backend.tests.test_workspace_api backend.tests.test_repository_configuration backend.tests.test_single_host_compose",
          "details": "435 通过、8 跳过（无活库的 Postgres 集成用例，与改动前一致）：intake/rerun、Zendesk 投递与发布台账、workspace admin、compose 契约均不受 promote 端点删除影响。"
        },
        {
          "type": "test",
          "label": "Syntax gates",
          "command": "python3 -m py_compile backend/main.py && node --check ui/account-ui/app.js && git diff --check",
          "details": "后端编译、前端 JS 语法、空白检查全部通过。"
        },
        {
          "type": "test",
          "label": "Sync unit tests (InMemory + CLI)",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy python3 -m unittest backend.tests.test_prompt_versioning（应用镜像内执行）",
          "details": "21 全绿，含新增 4 例：active release 同步进全新目标库并成为唯一 active；重复同步幂等（created=false）；candidate 同步后源端 activate 再同步可在目标端对齐 active 并切换 active version；内容哈希不匹配被拒绝。"
        },
        {
          "type": "test",
          "label": "Postgres integration (independent schema)",
          "command": "RUN_PROMPT_POSTGRES_TEST=true TICKET_DB_DSN=\u003cstaging> TICKET_DB_MIGRATION_DSN=\u003cmigration> python3 -m unittest backend.tests.test_prompt_versioning_postgres（应用镜像内执行）",
          "details": "3 全绿，含新增 1 例：在独立 schema 中验证 candidate 同步、唯一 active 不变、源端 activate 后目标端状态与 active version 内容对齐；临时 schema 用后即删。"
        },
        {
          "type": "test",
          "label": "Deploy script contract",
          "command": "python3 -m unittest backend.tests.test_deploy_ec2 backend.tests.test_production_ui_contract",
          "details": "27 全绿，含新增 2 例：启用 production profile 的成功部署在 validate 后（down 前）与 activate 后各记录一次 sync 调用且参数正确；sync 失败时部署非零退出且未发生 down/up（运行栈不受影响）；契约测试断言 deploy 脚本含 sync 接线。"
        },
        {
          "type": "test",
          "label": "Syntax gates",
          "command": "bash -n deployment/deploy_ec2.sh",
          "details": "部署脚本语法检查通过。"
        },
        {
          "type": "test",
          "label": "Post-merge live stack verification",
          "command": "./deployment/deploy_ec2.sh --skip-pull（根 main 9f2eeea）",
          "details": "官方栈重启成功：部署日志在 validate 后与 activate 后各出现一次 Synced Prompt Release pr-52b4eed80337 to the /production database；/health 内外均 ok 且 app_build.ref=9f2eeeae0720 与合并 main 一致；/production 页面门禁通过并返回 Account Production 标题；api_production/worker_query_production/worker_aux_production 稳定运行，api_production 日志 prompt_runtime_loaded release_id=pr-52b4eed80337，production worker 无 ERROR。"
        },
        {
          "type": "test",
          "label": "Workspace admin API + production repository resolution",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_workspace_api",
          "details": "26 全绿，含新增 5 例：endpoint 级 fail-closed（staging 无 PRODUCTION_TICKET_DB_DSN 时 account-automation 与 metrics 均 503 且 detail 指明原因）、production 栈沿用默认 repository、staging 栈懒加载 PRODUCTION_TICKET_DB_DSN 单例（构造参数断言）、DSN 缺失 fail-closed、DSN 与 staging 相同 fail-closed。"
        },
        {
          "type": "test",
          "label": "UI/compose contract regression",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_production_ui_contract backend.tests.test_account_ui_contract backend.tests.test_workspace_admin_ui_contract backend.tests.test_single_host_compose",
          "details": "98 全绿：admin UI 契约（含 automated-cases 拉取 /api/workspace/admin/account-automation?route_status=automated 不变）、production/account UI 契约、compose 契约均不受影响。"
        },
        {
          "type": "test",
          "label": "Account intake profile regression",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_intake",
          "details": "164 全绿：ACCOUNT_DEFAULT_PROCESSING_PROFILE 相关 intake 行为未受访问器复用影响。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official-stack restart & build provenance",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && curl /health",
          "details": "PR #800 合并后从根 main（13a13565953b）重启官方栈：deployment 模式、无辅助栈（inspect_single_host_stack_mode 仅报 build 落后，重启后消除）；镜像 tag 与 /health app_build.ref 均为 13a13565953b，status ok、runtime_profile local_lightweight。"
        },
        {
          "type": "deployment",
          "label": "Admin automated-cases reads live production database",
          "command": "psql 等价直查 PRODUCTION_TICKET_DB_DSN + bootstrap admin 登录调用 GET /api/workspace/admin/account-automation（及 ?route_status=automated、/api/workspace/admin/metrics）",
          "details": "production 库直查与 admin API 返回逐条一致：total_account_cases=5、automated_cases=4、not_automated_cases=1、automation_rate=0.8（AC-12839/12865/12864/12838 automated，AC-12807 not_automated）；filtered 调用 total=4 且 metrics 汇总不变；metrics.billing={total:5, automation:4, not_automated:1}。注：用户最初观察到 2 条/1 automated 为更早快照，验证时 production 库已增至 5 条，一致性以实时库为准。"
        },
        {
          "type": "test",
          "label": "Environment-specific Account reply timing and UI contracts",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_reply_version_fence backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_repair_account_customer_name backend.tests.test_account_ui_contract backend.tests.test_production_ui_contract",
          "details": "314 tests 全绿：staging intake、Enablement worker 补偿与 customer-name repair 的 reply job 立即到期；production 三条路径保持 360-600 秒采样；非法 profile 明确失败；staging UI 改为 queued/immediate，production UI 保留 scheduled/6-10 分钟。"
        },
        {
          "type": "test",
          "label": "Changed Python and JavaScript syntax",
          "command": "python -m py_compile backend/main.py backend/worker.py backend/services/account_reply_jobs.py backend/scripts/repair_account_customer_name.py && node --check ui/account-ui/app.js && node --check ui/production-ui/app.js",
          "details": "四个 Python 文件编译通过，两套 Account UI JavaScript 语法检查通过。"
        }
      ],
      "source_refs": [
        "docs/feature_list.md",
        "deployment/docker-compose.single-host.yml",
        "deployment/nginx/supportportal.conf"
      ],
      "legacy_ids": [],
      "status": "active",
      "task_count": 5,
      "done_count": 2,
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
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 case-automation 合并到 case-route。"
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
      "function_id": "case-route"
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
      "task_id": "p1-09",
      "title": "完成 Billing 与 Detailed Invoice 自动化执行闭环",
      "status": "done",
      "owner": "jojo",
      "summary": "Billing 与 Detailed Invoice 已完成路由、内部处理、结果回传和客户更新闭环。",
      "next_action": "",
      "acceptance_criteria": [
        "Billing 路由、内部通知、客户回复和结果处理已完成验证。"
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
          "number": 575,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/575",
          "label": "PR #575"
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
      "function_id": "automation-execution-loop"
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
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 case-automation 合并到 case-route。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "billing-persona-registry",
        "p2-12"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
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
      "title": "Manager 接管 Compliance、Security、法务及其他敏感工单",
      "status": "planned",
      "owner": "emma / derek",
      "summary": "由 Manager 接管 Compliance、Security、法务及其他敏感工单，保持统一人工口径。",
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
      "title": "完成 AI 回复写回 Zendesk：internal comment",
      "status": "done",
      "owner": "zac",
      "summary": "Production 注册 Automation 的 Account AI 回复可幂等写回 Zendesk private internal comment，并以只读 audit 回查处理未知投递结果。",
      "next_action": "已完成实现、合并后官方栈验证，以及 AC-PRD-12838 / message 1372 的单条 audit-first private comment 写回和 Zendesk readback。",
      "acceptance_criteria": [
        "Admin 可将 Account AI 消息作为 public=false internal comment 写入关联 Zendesk Ticket，并记录幂等结果。",
        "Production 注册 Automation 的已发布 Account AI reply 与 queued Zendesk delivery intent 在同一 publication transaction 内持久化。",
        "queued delivery 只能被原子 claim 一次；pending 或 outcome_unknown 只允许 audit readback，不能重复 PUT。",
        "缺 App ID 导致 internal email 为 not_ready 时，enablement production reply 仍创建 Zendesk private-comment delivery intent。",
        "Run in Production 使用专用 300 秒 timeout，并在 timeout 后提示服务端可能仍在执行及检查 PRD-* Case。",
        "staging、未注册 route、退役 route 或无 Zendesk ticket 的 Case 不创建 delivery ledger 或 Zendesk side effect。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "pr",
          "number": 752,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/752",
          "label": "PR #752"
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
          "label": "Production Zendesk delivery gate, audit readback, and Admin production-view regressions",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_zendesk_comments.py backend/tests/test_worker.py backend/tests/test_workspace_api.py backend/tests/test_account_admin_features.py -q"
        },
        {
          "type": "test",
          "label": "Post-merge schema bootstrap and production delivery regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_repository_configuration.py backend/tests/test_zendesk_comments.py backend/tests/test_worker.py backend/tests/test_workspace_api.py backend/tests/test_account_admin_features.py -q"
        },
        {
          "type": "test",
          "label": "Queued publication intent, atomic delivery claim, recovery drain, and production timeout regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_worker.py backend/tests/test_repository_configuration.py backend/tests/test_account_ui_contract.py -q",
          "result": "230 passed, 9 subtests passed"
        },
        {
          "type": "test",
          "label": "Full Zendesk, Worker, Repository, Workspace API, Account Admin, and Account UI regression suite",
          "command": "source /Users/xieziling/Desktop/personal_proj/SupportPortal/.env && /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_repository_configuration.py backend/tests/test_zendesk_comments.py backend/tests/test_account_zendesk_comment.py backend/tests/test_worker.py backend/tests/test_workspace_api.py backend/tests/test_account_admin_features.py backend/tests/test_account_ui_contract.py -q",
          "result": "297 passed, 4 warnings, 22 subtests passed"
        },
        {
          "type": "test",
          "label": "Shared API-backed Account/Zendesk service, production publication, PostgreSQL contract, and UI timeout regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/pytest -q backend/tests/test_repository_configuration.py backend/tests/test_zendesk_comments.py backend/tests/test_account_zendesk_comment.py backend/tests/test_account_zendesk_internal_comment_service.py backend/tests/test_worker.py backend/tests/test_workspace_api.py backend/tests/test_account_admin_features.py backend/tests/test_account_ui_contract.py",
          "result": "308 passed, 4 warnings, 22 subtests passed"
        },
        {
          "type": "test",
          "label": "PostgreSQL publication and shared Zendesk result persistence integration coverage",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/pytest -q backend/tests/test_account_reply_publication_postgres.py",
          "result": "8 skipped without RUN_POSTGRES_INTEGRATION=1; no external Zendesk writes"
        },
        {
          "type": "pr",
          "number": 792,
          "url": "https://github.com/ZilingXie/SupportPortal/pull/792",
          "label": "PR #792"
        },
        {
          "type": "deployment",
          "label": "Post-merge official single-host stack verification",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh",
          "result": "official_project=deployment; image, health, and runtime build ref 4623389cde87 matched; auxiliary_stack_present=false"
        },
        {
          "type": "deployment",
          "label": "Single-case Production Zendesk private-comment recovery",
          "result": "AC-PRD-12838 / Zendesk ticket 12838 / message 1372 had no local ledger or prior private-comment readback; the /account Admin API returned added with comment 52663858132628, local idempotency and message meta persisted, and Zendesk audits read back the same private comment"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-19",
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
        },
        {
          "at": "2026-08-18",
          "event": "production_delivery_safety_hardened",
          "summary": "Production 写回仅允许当前注册 Automation；Zendesk outcome_unknown 或遗留 pending delivery 只执行 private audit 回查，绝不重复 PUT；Workspace Admin Account 指标和自动化列表显式读取 production profile。真实合成 Zendesk 验收在合并后执行。"
        },
        {
          "at": "2026-08-18",
          "event": "schema_bootstrap_sql_format_fixed",
          "summary": "首次合并后的官方 local_lightweight 栈启动暴露 support_account_cases rule_release JSONB 默认值在 psycopg SQL.format 模板中未转义的问题；修复 CREATE 和 ALTER TABLE 模板的双大括号转义，并将 Account Case 持久化契约测试更新为 44 字段。254 项 repository、Zendesk、Worker 和 Admin 回归通过；合并后重新执行官方栈健康验证。"
        },
        {
          "at": "2026-08-18",
          "event": "production_publication_delivery_gap_reopened",
          "summary": "真实 production Account reply 已发布到本地但未创建 Zendesk delivery ledger；重新打开 Task，补齐 publication transaction 内 queued intent、worker 恢复 drain 与 Run in Production 专用 timeout。"
        },
        {
          "at": "2026-08-18",
          "event": "production_publication_delivery_intent_implemented",
          "summary": "Production registered Automation 的 reply publication 已与 queued Zendesk delivery intent 绑定；worker 支持 queued claim 与 poller recovery，pending/outcome_unknown 维持 audit-only，Run in Production 使用 300 秒专用 timeout。目标与完整回归通过，待合并后官方栈和合成 Zendesk 验收。"
        },
        {
          "at": "2026-08-19",
          "event": "production_account_zendesk_contract_unification_started",
          "summary": "只读核对发现 Production worker 直接调用 Zendesk transport，未复用 /account 的产品级幂等记录和 message meta；AC-PRD-12838 的 AI message 1372 没有 delivery ledger、幂等记录或 private comment。开始抽取共享 API-backed internal-comment service，并统一三套结果状态。"
        },
        {
          "at": "2026-08-19",
          "event": "production_account_zendesk_contract_unification_implemented",
          "summary": "完成共享 API-backed internal-comment service：/account 管理动作、Production worker 和 audit reconciliation 读取同一持久化 AI message，复用同一幂等记录并写回 message meta；Production delivery ledger 保持 queued/pending/delivered/outcome_unknown 的 fail-closed 状态。"
        },
        {
          "at": "2026-08-19",
          "event": "production_account_zendesk_contract_verified",
          "summary": "PR #792 合并后的 official deployment 栈 provenance、/account Production marker 和 AC-PRD-12838 单条 audit-first private comment 写回均已验证；Zendesk comment 52663858132628 通过 audits readback 确认。"
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
      "status": "active",
      "owner": "zac",
      "summary": "当前使用 ai-support-agent@agora.io 作为 Account Automation 发送身份，后续可评估是否创建专用账号。",
      "next_action": "确认 ai-support-agent@agora.io 的显示名称、权限和端到端发送结果，并评估新专用账号需求。",
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
      "status": "done",
      "owner": "zac",
      "summary": "通过 Zendesk users side-load 补齐评论作者姓名和身份，并在 Account 中派生 is_agent。",
      "next_action": "",
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
      "title": "定义 Admin Dashboard 与 Automation Monitor 指标",
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
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
    },
    {
      "schema_version": 2,
      "task_id": "p1-24",
      "title": "监控 Account Automation 执行结果与失败原因",
      "status": "done",
      "owner": "unassigned",
      "summary": "Monitor automated case 执行结果：跟踪 automation_status、missing_fields、internal_email_send_status、Outlook reply / PDF 附件转发、customer follow-up 和异常失败原因。",
      "next_action": "",
      "acceptance_criteria": [
        "完成 Monitor 维度的交付和验证。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "command": "python3 -m py_compile backend/main.py backend/services/internal_email_template.py backend/services/account_verification_automation.py backend/services/billing_automation.py backend/services/enablement_automation.py backend/services/quota_automation.py backend/services/internal_email_payload.py"
        },
        {
          "type": "test",
          "command": "python3 -m unittest backend.tests.test_internal_email_template backend.tests.test_account_verification_automation.AccountVerificationAutomationTests.test_missing_information_is_followed_up_only_once backend.tests.test_enablement_automation.EnablementAutomationTests.test_sample_routes_and_extracts_media_relay backend.tests.test_quota_automation",
          "result": "12 passed"
        },
        {
          "type": "test",
          "command": "Focused email contract smoke: matching Zendesk Ticket IDs render clickable HTML links; mismatched source IDs fail closed.",
          "result": "passed"
        }
      ],
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
      "task_id": "p1-26",
      "title": "在 Admin Dashboard 展示 Automation Controlled Launch 指标",
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
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
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
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 case-automation 合并到 case-route。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "account-case-intake"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
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
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 case-automation 合并到 case-route。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "enablement-automation"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
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
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 case-automation 合并到 case-route。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "quota-automation"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
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
        },
        {
          "at": "2026-08-17",
          "event": "reclassified",
          "summary": "Function 从 case-automation 合并到 case-route。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [
        "billing-automation-foundation"
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "case-route"
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
      "title": "建立 Admin Route Strategy 管理面",
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
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
    },
    {
      "schema_version": 2,
      "task_id": "p1-49",
      "title": "支持 Account Case 一键派给 Zendesk AI Agent",
      "status": "done",
      "owner": "zac",
      "summary": "Account Admin 可让关联 Zendesk ticket 由配置的 AI Agent 接手，并展示 Zendesk 返回的最终 ownership 和 group。",
      "next_action": "",
      "acceptance_criteria": [
        "Account Case 详情提供 Take ownership as AI 操作，只有 Admin 可以执行。",
        "操作只发送 Zendesk assignee_id 更新，保留 status 和 comments，并展示 Zendesk 返回的最终 group。",
        "目标用户由服务端配置并校验为 active Agent；Zendesk 拒绝或结果未知时明确提示。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Zendesk AI assignment service and Account API tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_zendesk_assignment -q"
        },
        {
          "type": "test",
          "label": "Account UI assignment contract tests",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_ui_contract -q"
        },
        {
          "type": "test",
          "label": "Account Zendesk assignment regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_zendesk_comments backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_zendesk_assignment backend.tests.test_account_ui_contract -q"
        },
        {
          "type": "test",
          "label": "Zendesk assignee identity and error classification regression suite",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_zendesk_assignment -q"
        },
        {
          "type": "test",
          "label": "Feature list and Project Overview validation",
          "command": "python3 scripts/verify_feature_list.py && python3 scripts/generate_project_overview.py --check"
        }
      ],
      "source_refs": [
        "docs/feature_list.md"
      ],
      "created_at": "2026-08-17",
      "updated_at": "2026-08-17",
      "history": [
        {
          "at": "2026-08-17",
          "event": "implemented",
          "summary": "修正 Account Admin ownership 语义，展示 Zendesk 接手后实际返回的最终 group。"
        },
        {
          "at": "2026-08-17",
          "event": "verified",
          "summary": "47 项后端与 Account UI 合同测试、Python/Node 语法检查及 Feature List/Project Overview 校验通过。"
        },
        {
          "at": "2026-08-17",
          "event": "assignee_identity_resolution_fixed",
          "summary": "改用 users/me 校验配置邮箱对应的凭据身份，避免 users/search 权限不足误报为 group membership，并区分 Zendesk 权限错误与 422 group 拒绝。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "zendesk-connection"
    },
    {
      "schema_version": 2,
      "task_id": "p1-50",
      "title": "统一 Account Automation 客户回复与 Rerun 契约",
      "status": "active",
      "owner": "zac",
      "summary": "统一 fraud_account、enablement 和 account_suspension 的客户回复内容、内部交接顺序、关闭条件以及 Intake、full rerun 和 recovery 的一致行为。",
      "next_action": "Finalize Persona v11 repair，重启官方 local_lightweight 栈，Resume account-rerun-f53393771ddd47118d4eb821d83c89e9，并读回 child job 与 AC-12715 reply-only 结果。",
      "acceptance_criteria": [
        "fraud_account 在内部邮件确认发送成功后，客户回复明确说明 relevant team 将在 24 小时内联系，且不会自动关闭工单。",
        "account_suspension 首次回复询问首选联系邮箱及是否使用工单邮箱，说明 24 小时联系、关闭和 24 小时后可 reopen；仅在明确确认、内部邮件成功和 closing reply 持久发布后关闭。",
        "enablement 客户提交确认包含最长 24 小时激活时间和 Monday-Friday 变更窗口；只有真实内部回复明确表示已启用后才通知客户并关闭，否定回复不关闭。",
        "Account Persona 不再配置、持久化或生成签名；发布前只做非破坏性的尾部签名检查，不能误删正文中的普通 best 或 regards。",
        "Intake、full rerun 和 reply-only recovery 使用同一 canonical reply intent 和关闭判定；intent 冲突、旧 Fraud 关闭契约或无效回复 fail closed 或进入 Human Review。",
        "未通过最终 content/intent 校验的回复不会调用 publish_account_reply，也不会创建 production Zendesk delivery intent。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Task worktree and direct-to-main repository policy preflight",
          "command": "git status --short --branch; git branch -vv; git worktree list --porcelain; scripts/workflow/bootstrap_main_repo_policy.sh --verify-only",
          "result": "Task worktree and branch are correct; root main is clean at 902f3b5; repository policy verification passed."
        },
        {
          "type": "document",
          "label": "karpathy-guidelines read before runtime edits",
          "command": "Read /Users/xieziling/.codex/skills/karpathy-guidelines/SKILL.md",
          "result": "Complete skill read; runtime implementation must remain surgical, explicit, fail-loud, and test-driven."
        },
        {
          "type": "test",
          "label": "Account Automation integrated targeted suite",
          "command": "python -m unittest backend.tests.test_automation_routing backend.tests.test_automation_persona backend.tests.test_account_reply_version_fence backend.tests.test_account_verification_automation backend.tests.test_enablement_automation backend.tests.test_account_full_reroute backend.tests.test_account_reroute_dispatch backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_account_rerun_recovery",
          "result": "368 tests passed; normal Intake, full rerun, reply-only recovery, Persona v9 contracts, Worker publication gates, the three active Automation flows, and legacy Suspension full-rerun migration are green."
        },
        {
          "type": "test",
          "label": "Account Automation static and registry checks",
          "command": "python -m py_compile backend/main.py backend/worker.py backend/services/automation_persona.py backend/services/account_reply_jobs.py backend/services/account_suspension_automation.py backend/services/account_full_reroute.py; python3 scripts/verify_feature_list.py; python3 scripts/generate_project_overview.py --write; python3 scripts/generate_project_overview.py --check; git diff --check",
          "result": "Python compilation, feature-list validation, Project Overview generation/check, and diff whitespace validation passed."
        },
        {
          "type": "test",
          "label": "Signature source-removal repair preflight",
          "command": "git status --short --branch; git branch -vv; git worktree list --porcelain; scripts/workflow/bootstrap_main_repo_policy.sh --verify-only",
          "result": "Root main and origin/main are synchronized at 3f1f65c; the repair worktree is clean on codex/account-automation-signature-source-removal; repository policy verification passed."
        },
        {
          "type": "test",
          "label": "Persona Signature source-removal focused suite",
          "command": "python -m unittest backend.tests.test_account_admin_features backend.tests.test_workspace_api backend.tests.test_account_persona_postgres backend.tests.test_workspace_admin_ui_contract backend.tests.test_agent_config backend.tests.test_worker.WorkerResilienceTests.test_reply_facts_prepare_pins_persisted_persona_assignment",
          "result": "107 tests passed with 19 environment-dependent PostgreSQL tests skipped; Persona presets, API validation, repository writes/rollback, Admin UI, Agent Config, and runtime prompt projection are green."
        },
        {
          "type": "test",
          "label": "Non-destructive signed reply publication fence",
          "command": "python -m unittest backend.tests.test_automation_persona backend.tests.test_account_reply_version_fence backend.tests.test_worker; python -m py_compile backend/services/automation_persona.py backend/worker.py backend/tests/test_worker.py; git diff --check",
          "result": "120 tests passed; signed generated replies move to Human Review before publish_account_reply, unsigned replies remain unchanged, and Python compilation and diff checks passed."
        },
        {
          "type": "test",
          "label": "Account reply polarity and Enablement current-state validation",
          "command": "python -m unittest backend.tests.test_automation_persona backend.tests.test_enablement_automation backend.tests.test_account_reply_version_fence backend.tests.test_worker; python -m py_compile backend/services/automation_persona.py backend/worker.py backend/tests/test_automation_persona.py backend/tests/test_worker.py; git diff --check",
          "result": "139 tests passed; questions, requests, future activation, negated commitments, and revoked Enablement states no longer satisfy completion, handoff, SLA, or closure contracts."
        },
        {
          "type": "test",
          "label": "Signature source-removal integrated verification",
          "command": "python -m unittest backend.tests.test_account_admin_features backend.tests.test_workspace_admin_ui_contract backend.tests.test_workspace_api backend.tests.test_account_persona_postgres backend.tests.test_agent_config backend.tests.test_automation_routing backend.tests.test_automation_persona backend.tests.test_account_reply_version_fence backend.tests.test_account_verification_automation backend.tests.test_enablement_automation backend.tests.test_account_full_reroute backend.tests.test_account_reroute_dispatch backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_account_rerun_recovery; python -m py_compile backend/main.py backend/worker.py backend/services/account_admin.py backend/services/automation_persona.py backend/services/account_reply_jobs.py backend/services/account_suspension_automation.py backend/services/account_full_reroute.py backend/repositories/ticket_repository.py; node --check ui/workspace-ui/admin/app.js; python3 scripts/verify_feature_list.py; python3 scripts/generate_project_overview.py --write; python3 scripts/generate_project_overview.py --check; git diff --check",
          "result": "478 tests passed with 19 environment-dependent PostgreSQL tests skipped; Python and Node syntax, Feature List, Project Overview, and diff checks passed."
        },
        {
          "type": "test",
          "label": "Persona v11 bounded validation retry targeted suite",
          "command": "python -m pytest -q backend/tests/test_account_ai_execution.py backend/tests/test_automation_persona.py backend/tests/test_worker.py backend/tests/test_account_rerun_fail_fast_resume.py backend/tests/test_account_rerun_recovery.py backend/tests/test_account_reroute_dispatch.py backend/tests/test_account_ui_contract.py",
          "result": "210 tests passed with 29 subtests passed; transport and validation share four calls, exact Fraud handoff output succeeds on the fourth call, exhausted failures retain their code and attempt count, Worker remains fail closed, and failed rerun summaries are observed without historical writes."
        },
        {
          "type": "test",
          "label": "Persona v11 integrated Account Automation verification",
          "command": "python -m pytest -q backend/tests/test_account_ai_execution.py backend/tests/test_account_admin_features.py backend/tests/test_workspace_admin_ui_contract.py backend/tests/test_workspace_api.py backend/tests/test_account_persona_postgres.py backend/tests/test_agent_config.py backend/tests/test_automation_routing.py backend/tests/test_automation_persona.py backend/tests/test_account_reply_version_fence.py backend/tests/test_account_verification_automation.py backend/tests/test_enablement_automation.py backend/tests/test_account_full_reroute.py backend/tests/test_account_reroute_dispatch.py backend/tests/test_account_intake.py backend/tests/test_worker.py backend/tests/test_account_rerun_fail_fast_resume.py backend/tests/test_account_rerun_recovery.py backend/tests/test_account_ui_contract.py",
          "result": "519 tests passed with 19 environment-dependent PostgreSQL tests skipped and 67 subtests passed; greeting-prefixed publication validation, Intake, full rerun, reply-only recovery, Persona assignment, and publication fences are green."
        }
      ],
      "source_refs": [
        "docs/superpowers/plans/2026-08-18-account-automation-reply-contracts.md",
        "docs/feature_list.md",
        "docs/prompt_change_log.md"
      ],
      "created_at": "2026-08-18",
      "updated_at": "2026-08-19",
      "history": [
        {
          "at": "2026-08-18",
          "event": "started",
          "summary": "创建任务并完成 Stage 0 工作区、仓库策略和实现前置规则检查；尚未声明运行时完成。"
        },
        {
          "at": "2026-08-19",
          "event": "repair_started",
          "summary": "PR #790 已合并，但验收确认签名仍由运行时生成后清理，且完成状态和回复极性校验仍有缺口；创建独立 repair worktree 从源头移除签名能力。"
        },
        {
          "at": "2026-08-19",
          "event": "persona_v11_repair_started",
          "summary": "Rerun 在 AC-12715 因 Fraud handoff 自然改写未通过确定性合同而停止；将 exact sentence 校验纳入现有四次 Account AI 调用预算，并保留具体失败码与真实尝试次数。"
        },
        {
          "at": "2026-08-19",
          "event": "followup_intent_repair",
          "summary": "修复 fraud follow-up intent 冲突：_reply_to_billing_ticket_impl 的 top-level reply_intent 与 close_after_publish 仅在字段收齐且内部邮件成功（reply_ready）时传入，追问场景沿用 nested request_missing_information；同批新增 missing-information 禁止 24h/SLA 承诺句的确定性校验 fence。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p1-51",
      "title": "Production Automated Case 自动 Ownership 与 Zendesk public reply 闭环",
      "status": "done",
      "owner": "zac",
      "summary": "让 production 环境 Automated case（fraud_account / account_suspension / enablement）完整闭环：自动 Take Ownership 替代手动按钮、AI 回复以公开评论发给客户、客户在 Zendesk 的公开评论通过 n8n 同步触发后续自动化、closing 类回复确认 solved 后本地才关闭，并修复 delivery ledger confirmed_at timestamptz 写入失败与 fraud follow-up intent 冲突两个缺陷。",
      "next_action": "",
      "acceptance_criteria": [
        "production Automated case 在任何外部副作用（内部邮件、reply job、Zendesk comment）之前完成自动 Take Ownership；ownership 失败 fail closed 进入 human_review，不发送邮件和评论；staging 不自动改 assignee；人工改派后停止 automation 不抢回。",
        "production AI 回复以 Zendesk public comment 发送并可被 readback 确认；历史 private delivery 行不升级、不重发；手动运维投递路径保持 private。",
        "suspension closing 与 enablement completion 使用同一 PUT 附带 status=solved，Zendesk 确认 solved 后本地才在一个事务内关闭；其他 intent 不关单。",
        "n8n 评论同步快照带 trigger_comment_id 时，新的客户公开评论触发与 admin reply 相同的状态机；agent/private/unknown/初始描述/重放不触发、不产生重复副作用。",
        "fraud follow-up 缺信息时正常追问且不含 24h 承诺句；信息齐全后 handoff 回复保留精确独立句；enablement completion 走标准 reply job 与公开投递。",
        "全部 AI 回复无签名；confirmed_at timestamptz 修复后 postgres 真库能把 delivery 确认为 delivered。",
        "四个测试 ticket（12864/12865/12839/12838）live 验收矩阵通过并记录 public comment ID。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Task worktree preflight",
          "command": "git status --short --branch; git worktree list",
          "result": "Worktree production-automated-public-replies created from clean main 8a746a7 on branch codex/production-automated-public-replies."
        },
        {
          "type": "test",
          "label": "Targeted automation and Zendesk delivery suite",
          "command": ".venv/bin/python -m pytest -q backend/tests/test_zendesk_ticket_assignment.py backend/tests/test_account_zendesk_assignment.py backend/tests/test_account_zendesk_internal_comment_service.py backend/tests/test_account_zendesk_comment.py backend/tests/test_account_zendesk_comment_sync.py backend/tests/test_account_reply_publication_postgres.py backend/tests/test_automation_persona.py backend/tests/test_account_verification_automation.py backend/tests/test_enablement_automation.py backend/tests/test_account_intake.py backend/tests/test_account_full_reroute.py backend/tests/test_account_reroute_dispatch.py backend/tests/test_worker.py backend/tests/test_account_ui_contract.py backend/tests/test_production_ui_contract.py backend/tests/test_repository_configuration.py backend/tests/test_workspace_api.py backend/tests/test_account_reply_version_fence.py backend/tests/test_zendesk_comments.py backend/tests/test_zendesk_public_comment.py backend/tests/test_account_automation_ownership.py",
          "result": "623 passed, 8 skipped (PostgreSQL opt-in), 56 subtests passed."
        },
        {
          "type": "test",
          "label": "PostgreSQL integration (isolated schema) including the confirmed_at timestamptz regression",
          "command": "RUN_POSTGRES_INTEGRATION=1 .venv/bin/python -m pytest -q backend/tests/test_account_reply_publication_postgres.py",
          "result": "8 passed. The record_account_zendesk_internal_comment_result test fails on main with psycopg DatatypeMismatch and passes in this worktree, confirming the confirmed_at::timestamptz fix against real PostgreSQL."
        },
        {
          "type": "test",
          "label": "Static and registry checks",
          "command": ".venv/bin/python -m py_compile backend/main.py backend/worker.py backend/repositories/ticket_repository.py backend/services/account_automation_ownership.py backend/services/account_zendesk_internal_comment.py backend/services/zendesk_comments.py backend/services/zendesk_ticket_assignment.py backend/services/automation_persona.py; node --check ui/account-ui/app.js; node --check ui/production-ui/app.js; python3 scripts/verify_feature_list.py; python3 scripts/generate_project_overview.py --write; python3 scripts/generate_project_overview.py --check; git diff --check",
          "result": "All checks passed."
        },
        {
          "type": "deployment",
          "label": "Merged PRs and deployed builds",
          "command": "gh pr view 803/804/805/808/809/811/812",
          "result": "PR #803 (core), #804 (solve checkbox + ownership context carry), #805 (completion trigger currency), #808 (custom_fields solve + closed_at column), #809/#811 (audit readback signature tolerance), #812 (jsonb brace escape in close SQL) all merged; EC2 running cc36eb5 (build ref cc36eb552055) with local stack restarted to the same main; migrations 2026_08_19_production_public_zendesk_delivery.sql and 2026_08_20_support_tickets_closed_at.sql applied to both databases."
        },
        {
          "type": "deployment",
          "label": "Live acceptance matrix on tickets 12838/12839/12864/12865",
          "command": "psql production ledger + tickets; Zendesk API readback",
          "result": "12838 enablement: public submission comment 52671546896660 with 24h + Mon-Fri, ticket intentionally open; 12864 fraud: public handoff comment 52671576049812 with exact 'The relevant team will contact you within 24 hours.', no email resend, ticket open; 12839 enablement completion: internal reply consumed end-to-end, public completion comment 52704906920980, Zendesk solved, local resolved with closed_at 2026-08-20T04:00:20Z; 12865 suspension: confirmation -> single internal email -> public closing comment 52705195825556, Zendesk solved, local resolved with closed_at 2026-08-20T04:00:19Z. All four tickets owned by AI agent 48557297720084. Legacy stuck AC-12839 private delivery reconciled to delivered without resend; historical private rows stayed private."
        },
        {
          "type": "test",
          "label": "Live-discovered repair loop (post-deploy fixes)",
          "command": "pytest targeted suites per fix PR",
          "result": "Six follow-up defects found live and fixed with regression tests: solve required checkbox must use custom_fields array (flat field_\u003cid> silently ignored), support_tickets lacked closed_at, audit readback must tolerate platform-appended signatures (prefix match), jsonb path braces must be escaped in close SQL, enablement completion trigger must equal the latest customer message timestamp, fraud attempt merge dropped ownership context. All suites green after each fix."
        }
      ],
      "source_refs": [
        "backend/services/automation_routing.py",
        "backend/services/zendesk_comments.py",
        "backend/services/account_zendesk_internal_comment.py",
        "backend/services/zendesk_ticket_assignment.py",
        "backend/repositories/ticket_repository.py",
        "backend/worker.py",
        "docs/integrations/n8n/zendesk_account_comment_sync.md"
      ],
      "created_at": "2026-08-19",
      "updated_at": "2026-08-20",
      "history": [
        {
          "at": "2026-08-19",
          "event": "started",
          "summary": "诊断确认：production 投递契约为 private comment、无自动 Take Ownership、n8n 同步仅投影不触发自动化；ledger confirmed_at 存在 timestamptz 类型 bug 导致对账死循环；fraud follow-up 存在 intent 冲突导致追问无法排队。计划经用户批准后开工。"
        },
        {
          "at": "2026-08-19",
          "event": "implementation_complete",
          "summary": "完成代码与测试：自动 Ownership gate（intake/回复核心/投递前三点接入、fail-closed、人工改派停止）、公开评论投递与 target_status=solved 同 PUT、本地关闭延后到 solved 确认的原子事务、Enablement completion 改标准 reply job、fraud follow-up intent 修复、missing-info 24h fence、confirmed_at timestamptz 修复、n8n trigger_comment_id 触发器与幂等、UI 按钮移除。"
        },
        {
          "at": "2026-08-20",
          "event": "completed",
          "summary": "四个测试 ticket 的 live 矩阵全部闭环（12838/12864 公开回复且按规则不关单，12839/12865 公开回复 + Zendesk solved + 本地 resolved）。部署期共发现并修复六个缺陷（#804/#805/#808/#809/#811/#812），全部带回归测试。任务完成。"
        }
      ],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "zendesk-connection"
    },
    {
      "schema_version": 2,
      "task_id": "p1-52",
      "title": "Admin Automated Cases 页新增 Automation 子类指标卡",
      "status": "done",
      "owner": "zac",
      "summary": "在 workspace admin 的 Automated Cases 页指标条下方，为 automation 的三个活跃子类（fraud_account、enablement、account_suspension）各显示一组指标卡（Total、Automated 数、自动化率）。后端 account_automation_payload 在全量 all_cases 上新增 automation_subcategories 数组（与现有 metrics 同口径、不受筛选影响，子类归一化与表格 Subcategory 列共用同一助手）；前端复用 renderMetricCard/admin-metric-grid 卡片样式渲染三张卡，数据缺失时整段不渲染。纯展示，不改筛选器与表格。",
      "next_action": "",
      "acceptance_criteria": [
        "GET /api/workspace/admin/account-automation 返回 automation_subcategories：三个子类固定顺序且零填充，每行含 subcategory/label/total/automated/not_automated/automation_rate。",
        "子类计数基于全量数据（忽略 route_status/category/date 筛选），与表格 Subcategory 列同一套归一化口径；非 automation 子类（如 human_review）不进任何桶。",
        "admin 页 Automated Cases 段在指标条下方渲染三张指标卡（Fraud Account / Enablement / Account Suspension），展示 total 与 Automated 数/率；automation_subcategories 缺失时不渲染该区块。",
        "筛选器、case 表格与既有 endpoint 契约零改动；workspace admin 相关测试回归通过。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Endpoint payload + admin UI contract",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_workspace_api backend.tests.test_workspace_admin_ui_contract",
          "details": "54 全绿。endpoint 测试扩展为 4 个 seed（fraud/enablement automated、account_suspension not_automated、human_review 不进桶），断言 automation_subcategories 固定顺序、零填充、每行 total/automated/not_automated/rate，且 route_status=automated 筛选下分类计数不变（全量口径）；UI 契约新增静态 markers（automation_subcategories、Automation category metrics）与 runtime 断言（三张卡文案、'Automated 1 · 50.0%'、数据缺失时区块不渲染）。"
        },
        {
          "type": "test",
          "label": "Contract regression",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_production_ui_contract backend.tests.test_account_ui_contract backend.tests.test_account_admin_features && node --check ui/workspace-ui/admin/app.js",
          "details": "79 全绿 + app.js 语法检查通过：production/account UI 契约与 account admin 行为回归不受影响。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official-stack restart & build provenance",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && curl /health",
          "details": "PR #806 合并后从根 main（6c7902fb7f41）重启官方栈：deployment 模式、无辅助栈；镜像 tag 与 /health app_build.ref 均为 6c7902fb7f41，status ok、runtime_profile local_lightweight。"
        },
        {
          "type": "deployment",
          "label": "Live automation_subcategories matches production database",
          "command": "psql 等价直查 PRODUCTION_TICKET_DB_DSN + bootstrap admin 登录调用 GET /api/workspace/admin/account-automation",
          "details": "production 库直查与 admin API 返回逐条一致：fraud_account total=1/automated=1（AC-12864）、enablement total=2/automated=2（AC-12839/12838）、account_suspension total=1/automated=1（AC-12865），三行 rate 均 1.0；子类 rag 的 AC-12807 正确不进任何桶。页面卡片渲染由 UI 契约 runtime 用例直接验证（IAB 内嵌浏览器无法提交登录表单，属工具限制）。"
        }
      ],
      "source_refs": [
        "backend/services/account_admin.py",
        "ui/workspace-ui/admin/app.js",
        "backend/tests/test_workspace_api.py",
        "backend/tests/test_workspace_admin_ui_contract.py"
      ],
      "created_at": "2026-08-20",
      "updated_at": "2026-08-20",
      "history": [
        {
          "at": "2026-08-20",
          "event": "created",
          "summary": "为 admin Automated Cases 页 automation 子类指标卡功能创建任务（Function admin-case-operations）。"
        },
        {
          "at": "2026-08-20",
          "event": "progress",
          "summary": "完成实现与目标测试：后端 payload 新增 automation_subcategories（全量口径、固定顺序零填充、子类归一化提取为 _admin_case_subcategory 与表格列共用），前端复用 renderMetricCard/admin-metric-grid 渲染三张卡；workspace API 26+契约与回归 79 全绿。"
        },
        {
          "at": "2026-08-20",
          "event": "done",
          "summary": "PR #806 合并入 main（6c7902fb7f41）；根 main 官方栈重启后 live 验证通过：/health 与镜像 build ref 均为 6c7902fb7f41，automation_subcategories 与 production 独立库逐条一致（fraud 1/1、enablement 2/2、suspension 1/1，均 automated）。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
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
    },
    {
      "schema_version": 2,
      "task_id": "p2-71",
      "title": "依据试运行质量决定是否扩展 Billing 自动化范围",
      "status": "planned",
      "owner": "zac",
      "summary": "根据受控试运行的 route accuracy、automation coverage、失败原因和人工复盘结果，决定是否扩大 Billing 自动化范围。",
      "next_action": "完成受控试运行并形成 Billing 自动化扩围决策记录。",
      "acceptance_criteria": [
        "完成受控试运行质量复盘并记录扩围决策。",
        "扩围决策明确覆盖范围、风险边界和回退条件。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "docs/roadmap.html#lanes",
        "docs/roadmap/phase2.html"
      ],
      "created_at": "2026-08-18",
      "updated_at": "2026-08-18",
      "history": [
        {
          "at": "2026-08-18",
          "event": "moved-to-phase-2",
          "summary": "从 p1-23 迁移到 Phase 2 的 Account Automation 受控扩围 Function。"
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
        "p1-23",
        "p2-23"
      ],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-controlled-rollout"
    },
    {
      "schema_version": 2,
      "task_id": "p2-72",
      "title": "加固 single-host 启动健康门禁和部署互斥",
      "status": "active",
      "owner": "zac",
      "summary": "降低 single-host 重启期间的并发启动、误回滚和不完整健康检查风险。",
      "next_action": "将 Ticket、Asset/Event 和 Knowledge schema bootstrap 收敛到一次性初始化流程，并让 API、RAG 和 workers 仅执行 runtime schema 校验。",
      "acceptance_criteria": [
        "部署重启使用全局互斥锁，并能回收已退出进程留下的 stale lock。",
        "旧 deployment 容器未完全退出时拒绝启动第二套 stack。",
        "Core 服务先通过严格 health 后才启动 workers，回滚复用相同阶段顺序。",
        "remote DB 模式的 health 必须确认 PostgreSQL storage、Knowledge storage、RAG 和 Prompt runtime 均可用。",
        "提供可显式执行的一次性 schema bootstrap 工具，并在 PostgreSQL 集成验证完成后再切换 runtime-only 启动。"
      ],
      "blockers": [
        "生产默认切换仍需在受控窗口执行 RUNTIME_SCHEMA_MODE=check，并完成 canonical image provenance 与 live health 验收。"
      ],
      "evidence": [
        {
          "type": "test",
          "label": "Single-host restart reliability regression coverage",
          "command": "python3 -m unittest backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_rebuilds_with_current_main_build_metadata backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_health_failure_restores_previous_image backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_same_tag_failure_restores_previous_image_id backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_rejects_remote_health_missing_contract backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_rejects_active_deploy_lock",
          "details": "覆盖 core-first/worker-second 启动、严格 remote health、回滚阶段顺序、活动部署锁和 stale lock 隔离。"
        },
        {
          "type": "test",
          "label": "Workflow and shell validation",
          "command": "python3 -m unittest backend.tests.test_workflow_scripts.WorkflowScriptTests && bash -n scripts/workflow/restart_single_host_stack.sh"
        },
        {
          "type": "test",
          "label": "Bootstrap CLI and Compose profile contract",
          "command": "python3 -m unittest backend.tests.test_single_host_compose && python3 -m unittest backend.tests.test_runtime_bootstrap",
          "details": "验证 runtime_bootstrap profile、串行 repository 初始化、异常清理和 check-only 不调用 DDL。当地环境缺少 psycopg 时 CLI 测试会跳过。"
        },
        {
          "type": "test",
          "label": "Runtime schema preflight wiring",
          "command": "python3 -m py_compile backend/services/runtime_schema.py backend/services/prompt_runtime.py backend/main.py backend/rag_api.py backend/rag_worker.py backend/worker.py && git diff --check",
          "details": "验证 RUNTIME_SCHEMA_MODE=check 的只读门禁、四个运行入口的 DDL 跳过分支、RAG telemetry 运行时准备和默认 bootstrap 模式兼容。"
        },
        {
          "type": "test",
          "label": "Isolated PostgreSQL bootstrap and runtime preflight",
          "command": "podman run --rm --network host -e TICKET_DB_DSN=postgresql://supportportal:supportportal@127.0.0.1:25432/supportportal -e PGVECTOR_DSN=postgresql://supportportal:supportportal@127.0.0.1:25432/supportportal python -m backend.scripts.runtime_bootstrap bootstrap && python -m backend.scripts.runtime_bootstrap check-only",
          "details": "真实 pgvector/pg16 验证了四个 repository 串行 bootstrap、Prompt catalog sync、只读 check-only、缺失 vector 表 fail-closed，以及两个并发 bootstrap 的幂等与 advisory-lock 串行化。"
        }
      ],
      "source_refs": [
        "docs/project/modules/platform-delivery.json",
        "scripts/workflow/restart_single_host_stack.sh",
        "backend/tests/test_workflow_scripts.py",
        "backend/scripts/runtime_bootstrap.py",
        "backend/services/runtime_schema.py",
        "backend/main.py",
        "backend/rag_api.py",
        "backend/rag_worker.py",
        "backend/worker.py",
        "deployment/docker-compose.single-host.yml",
        "deployment/docker-compose.single-host.local-db.yml"
      ],
      "created_at": "2026-08-18",
      "updated_at": "2026-08-18",
      "history": [
        {
          "at": "2026-08-18",
          "event": "created",
          "summary": "建立 single-host 启动可靠性任务，先实施部署止血和严格健康门禁。"
        },
        {
          "at": "2026-08-18",
          "event": "implementation_started",
          "summary": "完成部署互斥、stale lock 回收、旧容器退出门禁、core-first 启动、严格 health 和脱敏诊断。"
        },
        {
          "at": "2026-08-18",
          "event": "bootstrap_tool_added",
          "summary": "新增显式 runtime_bootstrap CLI 和 bootstrap Compose profile；默认 API、RAG、worker 启动路径尚未切换为 runtime-only，等待 PostgreSQL 集成验证。"
        },
        {
          "at": "2026-08-18",
          "event": "runtime_check_integrated",
          "summary": "完成共享 runtime schema preflight 和 RUNTIME_SCHEMA_MODE=check 接入，并在隔离 pgvector/pg16 上验证 bootstrap、check-only、负向门禁和并发幂等；默认 bootstrap 仍保持兼容。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "platform-delivery",
      "function_id": "project-governance"
    },
    {
      "schema_version": 2,
      "task_id": "p2-73",
      "title": "新增 /production 独立环境（独立数据库 + 路径路由 + 无 Run in Production）",
      "status": "active",
      "owner": "zac",
      "summary": "在同一 single-host 部署内新增第二组 api/worker 容器（compose profile production 门控），指向独立数据库 supportportal_production；新增 /production UI（功能与 /account 相同、移除 Run in Production）；nginx 以路径路由 /production、/production/api 与 intake POST /production/account；production 栈 intake 直接以 processing_profile=production 创建工单并沿用现有 delivery 台账自动投递 Zendesk internal comment；/account staging 行为零改动。",
      "next_action": "合并后从根 main 重启官方栈并做 live 验证（/production 页面 marker、production 容器与队列隔离、n8n 新 URL）；完成后补充 live 证据并收尾任务。",
      "acceptance_criteria": [
        "/account（staging）现有功能与 API 行为零改动：未设置 ACCOUNT_DEFAULT_PROCESSING_PROFILE 时所有默认值仍为 staging。",
        "新增 /production 页面由 api StaticFiles 挂载，功能与 /account 相同，源码中不含 Run in Production（promote-production）相关代码。",
        "production 栈（ACCOUNT_DEFAULT_PROCESSING_PROFILE=production）下 POST /account intake 以 processing_profile=production 落库，AI 回复经现有 delivery 台账自动写入真实 Zendesk internal comment。",
        "api_production/worker_query_production/worker_aux_production 仅在 compose profile production 启用，使用独立 TICKET_DB_DSN（PRODUCTION_TICKET_DB_DSN）、独立队列名与事件 channel，与 staging 栈互不串扰。",
        "nginx 在未启动 production profile 时仍可正常启动（变量 upstream），路径 /production/、/production/api/*、= /production/account 正确转发到 api_production。",
        "本地官方栈（不启用 production profile）不受影响，现有测试全部通过。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Production UI/deploy contract",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_production_ui_contract backend.tests.test_account_ui_contract backend.tests.test_single_host_compose",
          "details": "10+全绿：/production mount 与三件套存在、标题/版本串、API 前缀 withProductionApiBase、promote 代码不存在（app.js/styles.css）、node --check、compose profile 门控与 PRODUCTION_TICKET_DB_DSN、nginx /production 路由与变量 upstream、deploy 脚本 profile 门禁与 DSN 相异校验、.env.example 文档。test_single_host_compose 的 runtime image 计数契约已扩展纳入三个 production 服务。"
        },
        {
          "type": "test",
          "label": "Default processing profile behavior",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_intake",
          "details": "164 全绿，含新增 3 例：ACCOUNT_DEFAULT_PROCESSING_PROFILE=production 时 POST /account 落库 production 档案且 zendesk_ticket_id 取 external_id；未设置 env 时保持 staging 且 zendesk_ticket_id 为空；GET /api/account/cases 默认档案随 env 切换（production 时可见、staging 时不可见）。失败持久化路径同样保留 profile 与 zendesk_ticket_id。"
        },
        {
          "type": "test",
          "label": "Regression suites around changed paths",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_repository_configuration backend.tests.test_account_zendesk_comment backend.tests.test_workspace_api backend.tests.test_bootstrap_auto_deploy_ec2",
          "details": "全绿（86+142+部署契约），证明 Zendesk 投递、publication 事务台账、workspace admin 与部署脚本回归安全。test_workflow_scripts 存在 5 个与本次无关的环境性失败（干净 main 上同样失败，已对照验证）。"
        }
      ],
      "source_refs": [
        "backend/main.py",
        "ui/production-ui",
        "deployment/docker-compose.single-host.yml",
        "deployment/nginx/supportportal.conf",
        "docs/integrations/n8n/zendesk_account_comment_sync.md"
      ],
      "created_at": "2026-08-19",
      "updated_at": "2026-08-19",
      "history": [
        {
          "at": "2026-08-19",
          "event": "created",
          "summary": "为 /production 独立环境（独立数据库、路径路由、无 Run in Production）创建任务。"
        },
        {
          "at": "2026-08-19",
          "event": "progress",
          "summary": "完成实现与目标测试：/production UI、ACCOUNT_DEFAULT_PROCESSING_PROFILE 默认值（含失败持久化路径）、InMemory production 列表过滤修复、compose/nginx/deploy/.env.example 变更与契约/行为测试。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-74",
      "title": "/account Run in Production 重构为转发 production intake",
      "status": "active",
      "owner": "zac",
      "summary": "移除 staging 库内 promote-production 端点与 PRD-* 晋级逻辑；/account 的 Run in Production 按钮改为以 n8n 同款五字段 intake 直连 POST /production/account，由 production 栈完成完整路由并在命中已注册 Automation 时自动写入 Zendesk internal comment。nginx intake 路由超时提升到 300s 匹配前端等待。",
      "next_action": "合并后从根 main 重启官方栈并做 live 验证（/account 转发按钮新契约、/production 页面 marker），完成后收尾。",
      "acceptance_criteria": [
        "account-ui 的 Run in Production 按钮直接 POST /production/account（同源 n8n 同款 intake），载荷含 external_id/title/question/customer_email/customer_name/source/created_by；未关联数字 Zendesk 号的 Case 拒绝转发并就地报错。",
        "转发成功与幂等重放（idempotent_replay）分别有明确 toast；超时提示指向 /production/ 而非 PRD-* Case。",
        "后端不再存在 POST /api/account/cases/{id}/promote-production 端点及其专属辅助（_production_rule_release/_production_zendesk_ticket_id/snapshot 导入）；rerun、metrics、intake 等共享路径零改动。",
        "nginx location = /production/account 的 proxy_read_timeout 为 300s。",
        "契约测试覆盖新前端契约并断言 account-ui 与 backend/main.py 无 promote-production 残留；test_worker/test_account_intake 等回归套件全绿。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Forward contract (UI + backend removal + nginx)",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_ui_contract backend.tests.test_production_ui_contract",
          "details": "42 全绿：account-ui 含 forwardAccountCaseToProduction/PRODUCTION_FORWARD_TIMEOUT_MS=300_000/POST /production/account/超时提示 Open /production//幂等 toast/无 Zendesk 号就地产错；account-ui 与 backend/main.py 均断言无 promote-production 残留；nginx intake location 断言 proxy_read_timeout 300s。"
        },
        {
          "type": "test",
          "label": "Regression suites around removed endpoint",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_reply_publication_postgres backend.tests.test_workspace_api backend.tests.test_repository_configuration backend.tests.test_single_host_compose",
          "details": "435 通过、8 跳过（无活库的 Postgres 集成用例，与改动前一致）：intake/rerun、Zendesk 投递与发布台账、workspace admin、compose 契约均不受 promote 端点删除影响。"
        },
        {
          "type": "test",
          "label": "Syntax gates",
          "command": "python3 -m py_compile backend/main.py && node --check ui/account-ui/app.js && git diff --check",
          "details": "后端编译、前端 JS 语法、空白检查全部通过。"
        }
      ],
      "source_refs": [
        "ui/account-ui/app.js",
        "backend/main.py",
        "deployment/nginx/supportportal.conf",
        "backend/tests/test_account_ui_contract.py"
      ],
      "created_at": "2026-08-19",
      "updated_at": "2026-08-19",
      "history": [
        {
          "at": "2026-08-19",
          "event": "created",
          "summary": "将 /account 的 Run in Production 从 staging 库内晋级重构为转发到 /production 独立环境。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-75",
      "title": "部署时把候选 Prompt Release 同步到 /production 库",
      "status": "done",
      "owner": "zac",
      "summary": "修复 #795 遗留的部署缺口：deploy_ec2.sh 只对 staging 库执行 prompt_release prepare/activate，但 PROMPT_RELEASE_ID 同时下发给 production 栈；release id 为随机 UUID，/production 独立库没有同 id 的 release 行时 api_production 启动即崩并触发整栈回滚。新增 prompt_release CLI sync 子命令（--release-id/--target-dsn，幂等，按内容哈希校验）与仓库层 sync_prompt_release（Protocol/InMemory/Postgres），部署脚本在 validate 之后、activate 之后各同步一次：前者是服务启动前的硬门禁（失败即中止且不停栈），后者把 production 库的 release 状态对齐为 active（失败仅告警，candidate 状态仍可部署）。",
      "next_action": "",
      "acceptance_criteria": [
        "prompt_release CLI 新增 sync 子命令：以 staging 库为源、--target-dsn 指定目标库，幂等复制 release 行、items 与被引用版本行（内容哈希不一致时报错拒绝），目标库无该 id 时插入、有则保持；源 release 为 active 时把目标对齐为 active；完成后在目标库执行与启动时相同的 validate。",
        "仓库层 sync_prompt_release 在 Protocol、InMemoryTicketRepository、PostgresTicketRepository 三处实现；Postgres 实现使用独立 advisory lock 与单事务，遵守 one-active release/one-active version 唯一索引。",
        "deploy_ec2.sh 在 prompt release validate 成功后（停止服务前）执行 production 库同步，失败则标记 candidate 失败、清理回滚镜像并以非零退出（运行栈不受影响）；activate 成功后再次同步对齐 active 状态，失败输出 WARNING（candidate 仍可部署）；production profile 未启用时不执行同步。",
        "旧行为零回归：未设置 PRODUCTION_TICKET_DB_DSN 时部署流程与之前完全一致。",
        "单元测试（InMemory + CLI 注入双仓库）、真实 Postgres 集成测试（独立 schema 模拟独立库）与部署脚本契约测试覆盖上述行为。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Sync unit tests (InMemory + CLI)",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy python3 -m unittest backend.tests.test_prompt_versioning（应用镜像内执行）",
          "details": "21 全绿，含新增 4 例：active release 同步进全新目标库并成为唯一 active；重复同步幂等（created=false）；candidate 同步后源端 activate 再同步可在目标端对齐 active 并切换 active version；内容哈希不匹配被拒绝。"
        },
        {
          "type": "test",
          "label": "Postgres integration (independent schema)",
          "command": "RUN_PROMPT_POSTGRES_TEST=true TICKET_DB_DSN=\u003cstaging> TICKET_DB_MIGRATION_DSN=\u003cmigration> python3 -m unittest backend.tests.test_prompt_versioning_postgres（应用镜像内执行）",
          "details": "3 全绿，含新增 1 例：在独立 schema 中验证 candidate 同步、唯一 active 不变、源端 activate 后目标端状态与 active version 内容对齐；临时 schema 用后即删。"
        },
        {
          "type": "test",
          "label": "Deploy script contract",
          "command": "python3 -m unittest backend.tests.test_deploy_ec2 backend.tests.test_production_ui_contract",
          "details": "27 全绿，含新增 2 例：启用 production profile 的成功部署在 validate 后（down 前）与 activate 后各记录一次 sync 调用且参数正确；sync 失败时部署非零退出且未发生 down/up（运行栈不受影响）；契约测试断言 deploy 脚本含 sync 接线。"
        },
        {
          "type": "test",
          "label": "Syntax gates",
          "command": "bash -n deployment/deploy_ec2.sh",
          "details": "部署脚本语法检查通过。"
        },
        {
          "type": "test",
          "label": "Post-merge live stack verification",
          "command": "./deployment/deploy_ec2.sh --skip-pull（根 main 9f2eeea）",
          "details": "官方栈重启成功：部署日志在 validate 后与 activate 后各出现一次 Synced Prompt Release pr-52b4eed80337 to the /production database；/health 内外均 ok 且 app_build.ref=9f2eeeae0720 与合并 main 一致；/production 页面门禁通过并返回 Account Production 标题；api_production/worker_query_production/worker_aux_production 稳定运行，api_production 日志 prompt_runtime_loaded release_id=pr-52b4eed80337，production worker 无 ERROR。"
        }
      ],
      "source_refs": [
        "backend/scripts/prompt_release.py",
        "backend/repositories/ticket_repository.py",
        "deployment/deploy_ec2.sh",
        "backend/tests/test_prompt_versioning.py",
        "backend/tests/test_prompt_versioning_postgres.py",
        "backend/tests/test_deploy_ec2.py",
        "backend/tests/test_production_ui_contract.py"
      ],
      "created_at": "2026-08-19",
      "updated_at": "2026-08-19",
      "history": [
        {
          "at": "2026-08-19",
          "event": "created",
          "summary": "为 /production 库的 prompt release 部署同步缺口创建任务：新增 sync 子命令与部署接线。"
        },
        {
          "at": "2026-08-19",
          "event": "progress",
          "summary": "完成实现与目标测试：仓库层 sync_prompt_release（Protocol/InMemory/Postgres）、CLI sync 子命令、deploy_ec2.sh 双点接线与失败契约，单元/PG 集成/部署契约测试全绿。"
        },
        {
          "at": "2026-08-19",
          "event": "done",
          "summary": "PR #797 合并入 main；根 main 官方栈重启后 live 验证通过（两次 release 同步日志、build ref 一致、/production 门禁与页面 marker、production 容器健康）。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-76",
      "title": "Workspace Admin 自动化看板切换为读取 production 独立库数据",
      "status": "done",
      "owner": "zac",
      "summary": "PR #796 将 Run in Production 改为经 /production/account 直写独立 production 库后，staging 侧 /api/workspace/admin/account-automation 与 /api/workspace/admin/metrics 的 billing 部分仍查 staging 库的遗留 production 档案行，导致 admin Automated Cases 与 /production 实际数据不一致。修复：staging api 进程按需懒加载 PRODUCTION_TICKET_DB_DSN 的独立 repository 供这两个查询使用；production 栈自身沿用默认 repository；未配置或与 staging DSN 相同时 fail-closed 返回 503 并说明原因。前端与 API 契约零改动。",
      "next_action": "",
      "acceptance_criteria": [
        "/api/workspace/admin/account-automation 在 staging 栈返回 production 独立库（PRODUCTION_TICKET_DB_DSN）的 account case 数据，metrics 与 /production 实际工单一致。",
        "/api/workspace/admin/metrics 的 billing 部分同样读取 production 独立库；其余指标（engineer/client/accounts）仍读 staging，行为不变。",
        "production 栈（ACCOUNT_DEFAULT_PROCESSING_PROFILE=production）访问同一 endpoint 时沿用默认 repository，行为不变。",
        "staging 栈未配置 PRODUCTION_TICKET_DB_DSN 或其与 TICKET_DB_DSN 相同时，两个 endpoint fail-closed 返回 503 且 detail 指明原因，不静默回退 staging 遗留数据。",
        "workspace admin 前端与 endpoint 契约零改动；现有 workspace/admin 测试回归通过。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Workspace admin API + production repository resolution",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_workspace_api",
          "details": "26 全绿，含新增 5 例：endpoint 级 fail-closed（staging 无 PRODUCTION_TICKET_DB_DSN 时 account-automation 与 metrics 均 503 且 detail 指明原因）、production 栈沿用默认 repository、staging 栈懒加载 PRODUCTION_TICKET_DB_DSN 单例（构造参数断言）、DSN 缺失 fail-closed、DSN 与 staging 相同 fail-closed。"
        },
        {
          "type": "test",
          "label": "UI/compose contract regression",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_production_ui_contract backend.tests.test_account_ui_contract backend.tests.test_workspace_admin_ui_contract backend.tests.test_single_host_compose",
          "details": "98 全绿：admin UI 契约（含 automated-cases 拉取 /api/workspace/admin/account-automation?route_status=automated 不变）、production/account UI 契约、compose 契约均不受影响。"
        },
        {
          "type": "test",
          "label": "Account intake profile regression",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_intake",
          "details": "164 全绿：ACCOUNT_DEFAULT_PROCESSING_PROFILE 相关 intake 行为未受访问器复用影响。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official-stack restart & build provenance",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && curl /health",
          "details": "PR #800 合并后从根 main（13a13565953b）重启官方栈：deployment 模式、无辅助栈（inspect_single_host_stack_mode 仅报 build 落后，重启后消除）；镜像 tag 与 /health app_build.ref 均为 13a13565953b，status ok、runtime_profile local_lightweight。"
        },
        {
          "type": "deployment",
          "label": "Admin automated-cases reads live production database",
          "command": "psql 等价直查 PRODUCTION_TICKET_DB_DSN + bootstrap admin 登录调用 GET /api/workspace/admin/account-automation（及 ?route_status=automated、/api/workspace/admin/metrics）",
          "details": "production 库直查与 admin API 返回逐条一致：total_account_cases=5、automated_cases=4、not_automated_cases=1、automation_rate=0.8（AC-12839/12865/12864/12838 automated，AC-12807 not_automated）；filtered 调用 total=4 且 metrics 汇总不变；metrics.billing={total:5, automation:4, not_automated:1}。注：用户最初观察到 2 条/1 automated 为更早快照，验证时 production 库已增至 5 条，一致性以实时库为准。"
        }
      ],
      "source_refs": [
        "backend/main.py",
        "backend/tests/test_workspace_api.py"
      ],
      "created_at": "2026-08-19",
      "updated_at": "2026-08-19",
      "history": [
        {
          "at": "2026-08-19",
          "event": "created",
          "summary": "为 admin 自动化看板数据源切换到 production 独立库创建任务（p2-73 生产环境落地的后续修复）。"
        },
        {
          "at": "2026-08-19",
          "event": "progress",
          "summary": "完成实现与目标测试（workspace API 26 例含新增 5 例、UI/compose 契约 98 例、account intake 164 例全绿），经 finalize 以 PR #800 squash 合入 main。"
        },
        {
          "at": "2026-08-19",
          "event": "done",
          "summary": "PR #800 合并入 main（13a13565953b）；根 main 官方栈重启后 live 验证通过：/health 与镜像 build ref 均为 13a13565953b，admin account-automation 与 metrics billing 返回值和 production 独立库逐条一致（5 条、4 automated、1 not_automated）。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-77",
      "title": "/account 取消人为回复延迟并仅在 production 保留 6-10 分钟节奏",
      "status": "active",
      "owner": "zac",
      "summary": "将 Account reply job 的人为延迟按 processing_profile 收敛：staging /account 新回复立即到期，production 继续为每个 job 随机采样 360-600 秒；持久化 job、Persona preparation、幂等 claim、新消息取消和 Zendesk delivery 流程保持不变。",
      "next_action": "目标测试通过；经 finalize 合入 main 后重启官方栈，验证 staging=0 秒、production=360-600 秒和两套 UI marker。",
      "acceptance_criteria": [
        "staging /account 创建的所有新 Account reply job 不再增加 6-10 分钟人为等待，scheduled_for 立即到期；回复仍经持久化 job 和异步 worker 发布。",
        "production 环境的正常 intake/rerun、Enablement worker 补偿确认和 customer-name repair replacement job 均继续按每个 job 随机 360-600 秒调度。",
        "processing_profile 缺失按 staging 处理；共享策略拒绝非法 profile，现有环境变量解析仍保持记录错误并回落 staging 的兼容行为；不新增配置、feature flag、数据库字段或迁移。",
        "staging UI 明确显示回复 queued 并在 preparation 完成后发布；production UI 保留 scheduled 和 6-10 分钟说明。",
        "现有 reply job 的状态、scheduled_for API 字段、Persona version fence、幂等 claim、新消息取消、失败处理和 Zendesk delivery 契约无回归。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Environment-specific Account reply timing and UI contracts",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_reply_version_fence backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_repair_account_customer_name backend.tests.test_account_ui_contract backend.tests.test_production_ui_contract",
          "details": "314 tests 全绿：staging intake、Enablement worker 补偿与 customer-name repair 的 reply job 立即到期；production 三条路径保持 360-600 秒采样；非法 profile 明确失败；staging UI 改为 queued/immediate，production UI 保留 scheduled/6-10 分钟。"
        },
        {
          "type": "test",
          "label": "Changed Python and JavaScript syntax",
          "command": "python -m py_compile backend/main.py backend/worker.py backend/services/account_reply_jobs.py backend/scripts/repair_account_customer_name.py && node --check ui/account-ui/app.js && node --check ui/production-ui/app.js",
          "details": "四个 Python 文件编译通过，两套 Account UI JavaScript 语法检查通过。"
        }
      ],
      "source_refs": [
        "backend/services/account_reply_jobs.py",
        "backend/main.py",
        "backend/worker.py",
        "backend/scripts/repair_account_customer_name.py",
        "ui/account-ui"
      ],
      "created_at": "2026-08-19",
      "updated_at": "2026-08-19",
      "history": [
        {
          "at": "2026-08-19",
          "event": "created",
          "summary": "按环境拆分 Account reply timing：staging 取消人为延迟，production 保留 6-10 分钟随机节奏。"
        },
        {
          "at": "2026-08-19",
          "event": "progress",
          "summary": "共享延迟策略、API/worker/repair 接线与 UI 文案完成；314 个目标测试及 Python/JavaScript 语法检查通过。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
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
        "`/account` 的 Automated execution view 展示三类 active Automation：Account & Billing / Fraud Account、Account & Billing / Account Suspension 和 Backend Operation / Enablement；每个 Case 同时保留其 Primary Category。Backend Operation / Unregistered 仅作为发现 taxonomy 缺口的诊断 fallback，不属于 Automated 或 Human Review membership。",
        "Quota 自动化会处理配额审核、并发提升和 Big Event 容量报备，最多追问一次后将现有信息交给内部团队。",
        "Enablement 使用 LLM 从客户原文提取并校验字段证据，不限制 App ID 格式；缺失时生成上下文追问，不确定或多候选时转 Human Review。",
        "Fraud Account 使用 LLM 收集公司、联系人、使用场景和安全支付概况，Website 为可选，最多追问一次并阻止敏感支付凭据进入派生数据。",
        "Billing 自动化统一通过公司 Outlook reply 接收内部处理结果，并可将 PDF 附件转发到客户工单。",
        "Account 入口可通过 HTTP 或手动 UI 创建 Account Case，并记录 Automated 或非自动化路由。",
        "Account 入口可查看 Account Case 历史和详情。",
        "staging Account 入口的 AI 消息可由 Admin 选择写入关联 Zendesk ticket 的 internal comment；production Automated case 的 AI 回复自动以公开评论发给客户，人工改派工单后自动停止发言。",
        "production Automated case 在任何外部副作用前自动由配置的 AI Agent 接手 Zendesk 工单并持久化 ownership 状态，手动按钮已移除；ownership 失败 fail closed 转 Human Review。",
        "Account Automation 提供 Sid Precise、Sid Bright、Sid Warm 三套独立 Persona presets，首次客户回复随机分配并固定精确版本，完整 Rerun 后重新选择。",
        "Automation Behavior 只提取结构化字段和处理事实，所有实际客户文案在发送前统一由 Automation Persona 生成；Persona 失败时转 Human Review。",
        "Account 入口支持人工纠正完整路由元组，并通过 Route errors 视图分析误路由案例。",
        "Account 入口支持对每条工单的路由结果进行 pass/review 标记，默认只显示未 review 工单，可切换 reviewed 视图。",
        "Account 入口支持默认 All 的重叠 route filter，按 Automated、Backend Operation、Account & Billing、Tech、Security & Compliance、Conversation 和 Human Review 等细分类别分页查看，并显示同一快照的 case counts。",
        "Account 入口支持按 ticket # 精准打开 Case，并可对单 Case 执行仅保留客户消息、保留独立审计的完整 Rerun。",
        "Account Case 读取受 Workspace Admin 保护；n8n 可通过独立 Zendesk comment snapshot integration 将 Account Case 的 public/internal comments 幂等同步到独立 projection，并可用 trigger_comment_id 将新的客户公开评论触发进自动化处理（agent 评论与重放不触发），详情按不同标签和气泡展示，Rerun 不删除这些 Zendesk comments。",
        "Account Rerun 先冻结目标 Case，再以无网络副作用的 Account-only preflight 校验数据库、Prompt runtime 和 Luna profile；首个 Case 的只读 Prepare 执行首次模型请求，任何错误立即停止并展示准确的失败阶段与未处理数量，支持从冻结 checkpoint Resume。",
        "Account 入口强制使用当前 layered route 并记录 pipeline 版本；Agora Router 将安全、隐私、信任、审计和合规请求归入 Security & Compliance classification-only 路由，Account & Billing 子 Router 将请求细分为 Account Suspension、Fraud Account、Detailed Invoice 或 Other，Backend Operation/Automation Router 将明确后台操作细分为 Enablement、Quota 或 Unregistered。每次新建异步全量 Rerun 都会重新执行路由、字段提取和 handler reconciliation，并允许 Automation 重新发送内部邮件，同时保留单个 job 内的幂等和审计历史。",
        "Account 入口通过 external ID 或来源 ticket ID 幂等处理重复请求，避免重复建单和重复发送内部邮件。",
        "Account Case 仅在命中已注册 Automation 时执行 handler 和延迟客户回复；其他路由只记录标签并进入对应人工或后续处理目标。",
        "Account 自动化遇到 AI/API、结构化输出、字段处理、Persona 或内部处理链路故障时最多重试 3 次且不使用 fallback；失败会停止客户回复、取消待处理 reply job、转为 human review，并向指定负责人发送脱敏的幂等故障告警。",
        "Enablement 使用 LLM 从客户原文提取并校验字段证据，不限制 App ID 格式；缺失时生成上下文追问，不确定或多候选时转 Human Review。",
        "Account Verification 使用 LLM 收集公司、联系人、使用场景和安全支付概况，最多追问一次并阻止敏感支付凭据进入派生数据。",
        "/production 独立环境提供与 /account 相同的 Account 处理能力（无 Run in Production），经独立数据库、独立 worker 和同域名路径路由运行；n8n 可将工单直接转发到 production，AI 回复自动以真实 Zendesk 公开评论发送，closing 类回复同次写入并置工单为 solved，确认后才关闭本地工单。",
        "/account 的 Run in Production 按钮将 Case 以 n8n 同款 intake 转发到 production 环境，由 production 侧完成完整路由与 Zendesk 公开评论投递；staging 库内晋级（PRD Case）逻辑已移除。",
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
        "staging Account 入口的 AI 消息可由 Admin 选择写入关联 Zendesk ticket 的 internal comment；production Automated case 的 AI 回复自动以公开评论发给客户，人工改派工单后自动停止发言。",
        "production Automated case 在任何外部副作用前自动由配置的 AI Agent 接手 Zendesk 工单并持久化 ownership 状态，手动按钮已移除；ownership 失败 fail closed 转 Human Review。",
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
    "source_count": 151,
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
        "target_id": "p2-71",
        "disposition": "moved-to-phase-2"
      },
      {
        "source_ref": "docs/project/tasks/billing-human-review.json",
        "legacy_id": "billing-human-review",
        "target_type": "function",
        "target_id": "human-review",
        "disposition": "merged"
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
        "target_type": "function",
        "target_id": "account-controlled-rollout",
        "disposition": "merged"
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
        "target_type": "function",
        "target_id": "case-route",
        "disposition": "merged"
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
        "target_type": "function",
        "target_id": "case-route",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/tasks/routing-rollout-taxonomy.json",
        "legacy_id": "routing-rollout-taxonomy",
        "target_type": "function",
        "target_id": "account-controlled-rollout",
        "disposition": "merged"
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
        "target_type": "function",
        "target_id": "case-route",
        "disposition": "merged"
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
        "target_type": "function",
        "target_id": "case-route",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/tasks/p2-07.json",
        "legacy_id": "p2-07",
        "target_type": "function",
        "target_id": "case-route",
        "disposition": "merged"
      },
      {
        "source_ref": "docs/project/tasks/p2-08.json",
        "legacy_id": "p2-08",
        "target_type": "function",
        "target_id": "case-route",
        "disposition": "merged"
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
        "target_type": "function",
        "target_id": "human-review",
        "disposition": "merged"
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
        "target_id": "p2-71",
        "disposition": "moved-to-phase-2"
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
        "target_type": "function",
        "target_id": "account-controlled-rollout",
        "disposition": "merged"
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
        "target_type": "function",
        "target_id": "account-controlled-rollout",
        "disposition": "merged"
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
        "target_id": "case-route",
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
      },
      {
        "source_ref": "docs/project/tasks/p1-23.json",
        "legacy_id": "p1-23",
        "target_type": "function",
        "target_id": "account-controlled-rollout",
        "disposition": "moved-to-phase-2"
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
        "target_id": "p2-71"
      },
      "billing-human-review": {
        "target_type": "function",
        "target_id": "human-review"
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
        "target_type": "function",
        "target_id": "account-controlled-rollout"
      },
      "routing-billing-review-customer-experience": {
        "target_type": "task",
        "target_id": "p1-17"
      },
      "routing-billing-risky-negatives": {
        "target_type": "function",
        "target_id": "case-route"
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
        "target_type": "function",
        "target_id": "case-route"
      },
      "routing-rollout-taxonomy": {
        "target_type": "function",
        "target_id": "account-controlled-rollout"
      },
      "routing-security-compliance": {
        "target_type": "task",
        "target_id": "p1-02"
      },
      "routing-semantic-golden-expand": {
        "target_type": "function",
        "target_id": "case-route"
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
        "target_type": "function",
        "target_id": "case-route"
      },
      "p2-07": {
        "target_type": "function",
        "target_id": "case-route"
      },
      "p2-08": {
        "target_type": "function",
        "target_id": "case-route"
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
        "target_type": "function",
        "target_id": "human-review"
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
        "target_id": "p2-71"
      },
      "p2-24": {
        "target_type": "task",
        "target_id": "p1-24"
      },
      "p2-25": {
        "target_type": "function",
        "target_id": "account-controlled-rollout"
      },
      "p2-26": {
        "target_type": "task",
        "target_id": "p1-26"
      },
      "p2-27": {
        "target_type": "function",
        "target_id": "account-controlled-rollout"
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
        "target_id": "case-route"
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
      },
      "p1-23": {
        "target_type": "function",
        "target_id": "account-controlled-rollout"
      }
    }
  }
}
