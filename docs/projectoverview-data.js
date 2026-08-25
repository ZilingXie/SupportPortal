window.SUPPORTPORTAL_PROJECT_DATA = {
  "schema_version": 2,
  "generated_at": "2026-08-25T10:48:40Z",
  "source_base_commit": "5d858c51e9362bdb0639aabaa7c6259ad040aee3",
  "registry_digest": "11d4797199a03744a1cb7c236a35ae1c85db81f4393d26ef7d9e97e3a9984c4d",
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
        },
        {
          "type": "test",
          "label": "Email prefix mapping and fenced code HTML conversion",
          "command": ".venv/bin/python -m unittest backend.tests.test_email_prefix_and_codeblocks backend.tests.test_billing_automation_email backend.tests.test_internal_email_template backend.tests.test_zendesk_comments backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_reply_rag_fallback",
          "details": "新增 7 项单测（request_type 按 action 映射、suspension 邮件正文不再含 Billing:、围栏代码块转 pre/code HTML、HTML 转义、多代码块、纯代码体、无代码块返回 None）；70 项相关回归全绿。"
        },
        {
          "type": "test",
          "label": "7-field extraction + Slack fields + intake regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_account_verification_automation backend.tests.test_account_slack_n8n backend.tests.test_account_intake",
          "details": "202 项测试全绿；覆盖 7 字段提取/grounding/追问覆盖验证、内部邮件 Provided+Missing 新标签、Slack 消息含 Provided/Missing 行、intake 全链路回归（fixture 已迁移到 7 键）。"
        },
        {
          "type": "test",
          "label": "Persona v12 missing-information layout and tone contract",
          "command": ".venv/bin/python -m pytest -q backend/tests/test_automation_persona.py backend/tests/test_account_ai_execution.py",
          "details": "38 项测试通过、13 个子测试通过；覆盖 Fraud Account 1-2 项 inline、3+ 项 bullet、编号列表拒绝、Persona v12 prompt wording，以及现有四次 Account AI 校验预算。"
        },
        {
          "type": "test",
          "label": "Fraud Account/Intake/Worker regression",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy OPENAI_API_KEY= .venv/bin/python -m pytest -q backend/tests/test_account_verification_automation.py backend/tests/test_account_slack_n8n.py backend/tests/test_account_intake.py backend/tests/test_worker.py backend/tests/test_account_reply_version_fence.py",
          "details": "318 项测试通过、33 个子测试通过；覆盖 7 字段提取、Slack/邮件摘要、Intake 追问与第二次回复、Persona Worker 发布和版本 fence。"
        },
        {
          "type": "deployment",
          "label": "Official single-host stack after merge",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh; curl -fsS http://127.0.0.1:8080/health; podman exec deployment_api_1 python -c \"from backend.services.automation_persona import AUTOMATION_PERSONA_PROMPT_VERSION, _assert_missing_information_format_contract; print({'persona_prompt_version': AUTOMATION_PERSONA_PROMPT_VERSION, 'format_contract': _assert_missing_information_format_contract.__name__})\"",
          "details": "官方项目 deployment；root_main_ref、official_image_tag、official_health_build_ref、official_runtime_build_ref 均为 d8a40785739f；health 返回 200/status=ok；runtime_profile=local_lightweight；auxiliary_stack_present=false；容器内 Persona marker 为 automation-persona-v12。"
        },
        {
          "type": "test",
          "label": "Fraud Account Prompt schema contract",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy OPENAI_API_KEY= .venv/bin/python -m pytest -q backend/tests/test_account_verification_automation.py backend/tests/test_agent_config.py backend/tests/test_account_intake.py backend/tests/test_automation_persona.py backend/tests/test_account_ai_execution.py",
          "details": "227 项测试通过、24 个子测试通过、4 个既有 FastAPI deprecation warnings；结构测试解析 managed Prompt 的 Output JSON，确保七个 canonical keys 存在且旧四字段及 contact_information 不存在。"
        },
        {
          "type": "deployment",
          "label": "Fraud Account Prompt v4 official runtime readback",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh; curl -fsS http://127.0.0.1:8080/health; podman exec deployment_api_1 python -c 'from backend.services.account_verification_field_extractor import ACCOUNT_VERIFICATION_REQUIRED_GROUPS; from backend.services.prompts.account_routing import build_account_verification_field_system_prompt, ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION; p=build_account_verification_field_system_prompt(); print({\"version\":ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION,\"canonical\":all((chr(34)+k+chr(34)) in p for k in ACCOUNT_VERIFICATION_REQUIRED_GROUPS),\"legacy_exact\":{k:(chr(34)+k+chr(34)) in p for k in (\"company_information\",\"contact_information\",\"use_case\",\"payment_information\")}})'",
          "details": "官方单机 local_lightweight 栈于 root_main_ref=23c19e3bd7d4 构建并通过 health=200；official_image_tag、health build ref、runtime build ref 均匹配；prompt_runtime release_id=code-8a779db0373b、status=loaded、prompt_count=28；api、rag_api、rag_worker、worker_query、worker_aux 日志均加载同一 code snapshot。容器内 Prompt 版本为 fraud-account-fields-v4，七个 canonical keys 全部存在，旧四字段均不存在。"
        },
        {
          "type": "test",
          "label": "Fraud Account v4 deployment gate",
          "command": ".venv/bin/python -m unittest backend.tests.test_prompt_versioning backend.tests.test_deploy_ec2",
          "details": "Prompt Release validate 在停旧栈前检查 v4 版本常量、Output JSON 精确七字段、legacy fields 缺失及候选内容与代码 SHA-256 一致；production sync 复用同一 validator。部署契约同时覆盖八个 runtime 的同镜像/build/release 门禁和 production active-release 回读。"
        },
        {
          "type": "test",
          "label": "Changed-area unit suites",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_routing backend.tests.test_account_route_pipeline backend.tests.test_worker backend.tests.test_account_reply_version_fence backend.tests.test_zendesk_comments backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_intake backend.tests.test_automation_persona",
          "details": "全绿（新增 detailed_invoice 完成 job / Zendesk upload / 投递附件集成 / intent 契约用例；翻转 routing 断言）。test_agent_config、quota reroute、route_correction suspension、roadmap、filter-select 的失败在干净 main 上同样失败，为遗留问题非本任务引入。"
        },
        {
          "type": "test",
          "label": "Scenario driver smoke",
          "command": ".venv/bin/python scripts/testing/production_ticket_scenarios.py --list",
          "details": "--list 列出含 D1 的五剧本。生产实跑待用户上线后执行。"
        },
        {
          "type": "test",
          "label": "Scenario engine suites (post p2-101 merge)",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_scenarios backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "D1 加入共享引擎后 41 用例全过（scenario overview 断言更新为含 D1）。"
        },
        {
          "type": "test",
          "label": "Classification-only broad verification",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_routing backend.tests.test_account_route_pipeline backend.tests.test_account_intake backend.tests.test_account_case_filter_postgres backend.tests.test_repository_configuration backend.tests.test_account_admin_features backend.tests.test_workspace_admin_ui_contract",
          "details": "405 tests passed；PostgreSQL parity integration 因当前环境未配置 TEST_POSTGRES_DSN 跳过 1 项。"
        },
        {
          "type": "test",
          "label": "Detailed Invoice focused execution gates",
          "command": ".venv/bin/python -m unittest backend.tests.test_route_correction.RouteCorrectionValidationTests.test_valid_billing_detailed_invoice_is_classification_only backend.tests.test_agent_config.AgentConfigTests.test_detailed_invoice_is_classification_only_and_not_an_automation_workflow backend.tests.test_account_case_reroute.AccountCaseRerouteTests.test_account_billing_automation_keeps_domain_category_and_handler backend.tests.test_account_case_reroute.AccountCaseRerouteTests.test_detailed_invoice_reroute_keeps_classification_without_handler backend.tests.test_account_admin_features.AccountAdminFeatureTests.test_inactive_detailed_invoice_is_not_counted_as_automation backend.tests.test_account_admin_features.AccountAdminFeatureTests.test_legacy_automation_status_uses_subcategory_for_active_eligibility backend.tests.test_worker.WorkerResilienceTests.test_inactive_detailed_invoice_reply_is_dismissed_before_side_effects backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_generates_customer_followup backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_uses_pdf_ocr_text_when_body_is_empty backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_attaches_pdf_to_customer_message_without_ocr backend.tests.test_worker.WorkerResilienceTests.test_detailed_invoice_reply_queues_closing_reply_job_with_pdf_attachments",
          "details": "11 tests passed；覆盖 classification-only route、Rerun/correction、历史 Automated view 排除、legacy Fraud 兼容、inactive_automation reply dismissal，以及 dormant implementation 重新注册后的可执行性。"
        },
        {
          "type": "test",
          "label": "Worker regression suite",
          "command": ".venv/bin/python -m unittest backend.tests.test_worker",
          "details": "105 tests passed；Detailed Invoice mailbox reply 在 inactive gate 后无 linked-ticket、附件或 reply-job 副作用。"
        },
        {
          "type": "test",
          "label": "Owner review",
          "command": "git diff --check + residual detailed_invoice execution-entry review",
          "details": "无剩余 finding；保留分类 taxonomy 与 dormant handler/extractor/email/PDF/Zendesk code，所有生产执行入口均受 ACTIVE_AUTOMATION_SUBCATEGORIES gate 控制。"
        },
        {
          "type": "test",
          "label": "New + affected suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_worker.py backend/tests/test_account_intake.py backend/tests/test_account_zendesk_comment_sync.py backend/tests/test_automation_persona.py backend/tests/test_ragflow_docs_search_skill.py backend/tests/test_account_reply_rag_fallback.py backend/tests/test_account_reply_version_fence.py backend/tests/test_llm_profiles.py backend/tests/test_rag_qa.py backend/tests/test_rag_api.py backend/tests/test_account_ai_execution.py backend/tests/test_llm_usage_capture.py -q",
          "details": "515 passed。新增用例：skill 侧 core_content_only 提示词+capture 内 entries 落 stage=ragflow_docs_answer+场景默认值/env 覆盖；persona 侧 rag_fallback 转述策略进 system prompt+user_prompt 携带 provided_answer+缺字段报错；worker 侧 prepare 走 persona 渲染（persona_v8_scheduled）+publish 时 References 确定性追加；intake 侧 job 以 facts 入队。既有断言按新契约更新（references 拆分、facts 入队、verbatim 测试重写为 persona+References）。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote",
          "details": "PR#928 合并后官方栈运行 root main 23c19e3（镜像 23c19e3bd7d4，built 2026-08-24T12:35:25Z）：/health ok（rag_service ok、runtime_profile=local_lightweight）、build_provenance_status=matched、auxiliary_stack_present=false；容器内实测 resolve_model_profile(ragflow_answer) → model=gpt-5.6-luna / effort=xhigh / fallbacks=()。纯后端改动无资产版本 marker 要求。"
        },
        {
          "type": "test",
          "label": "Affected suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_quota_field_extractor.py backend/tests/test_enablement_completion_classifier.py backend/tests/test_automation_persona.py backend/tests/test_worker.py backend/tests/test_account_intake.py backend/tests/test_account_zendesk_comment_sync.py backend/tests/test_enablement_automation.py backend/tests/test_account_suspension_field_extractor.py backend/tests/test_enablement_field_extractor.py backend/tests/test_account_verification_automation.py backend/tests/test_billing_automation_email.py backend/tests/test_account_route_pipeline.py backend/tests/test_account_ai_execution.py backend/tests/test_llm_usage_capture.py -q",
          "details": "482 passed。新增用例：ACCOUNT_EXTRACTOR 默认值（luna/low/30s/pinned）+三 env 旋钮覆盖；billing/enablement_reply 默认断言更新为 luna/30s；classifier/persona 测试中的 mini 字符串仅为 mock 标签无需改。"
        },
        {
          "type": "decision",
          "label": "范围与档位定案",
          "command": "",
          "details": "问答未获答复按推荐执行：仅账号链路（客户端 ack/web 搜索/engineer/本地 RAG 管线保持原模型，避免客户端首响时延劣化）、小任务 low 档；extractor 拆独立场景解决共享 INTENT_ROUTER 的 3 秒紧超时与客户端流耦合。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote",
          "details": "PR#934 合并后官方栈运行 root main f468c6e（镜像 f468c6e309f2，built 2026-08-25T02:36:37Z）：/health ok、build_provenance_status=matched、auxiliary_stack_present=false；容器内实测五个账号链路场景 account_extractor/automation_persona/enablement_completion_classifier/billing_reply/enablement_reply 全部 gpt-5.6-luna/low（超时 30/30/20/30/30s），intent_router（客户端流）保持 gpt-5.4-mini/low 不变。"
        },
        {
          "type": "test",
          "label": "Compose contract suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_single_host_compose.py backend/tests/test_llm_profiles.py -q",
          "details": "45 passed（合并前在任务 worktree 跑）。断言更新：worker_aux 块 ENABLEMENT_COMPLETION_CLASSIFIER_MODEL 默认 gpt-5.6-luna；compose 6 处 MODEL 行换 luna、6 处 TIMEOUT 行换 30/20；INTENT_ROUTER 与范围外模型钉值未动。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "podman inspect deployment_worker_aux_1 --format '{{.Config.Env}}'",
          "details": "PR#943 合并后官方栈运行 root main 09d9820（09d9820160fa）：/health ok、build_provenance_status=matched；容器 env 实测 AUTOMATION_PERSONA_MODEL=gpt-5.6-luna、AUTOMATION_PERSONA_TIMEOUT_SECONDS=30、ENABLEMENT_COMPLETION_CLASSIFIER_MODEL=gpt-5.6-luna、ENABLEMENT_COMPLETION_CLASSIFIER_TIMEOUT_SECONDS=20、INTENT_ROUTER_MODEL=gpt-5.4-mini（故意保留）。诊断证据：supportportal.support_account_case_llm_usage 逐条记录显示 08-24 10:07-11:11 的 case extractor=luna 但 automation_persona=gpt-5.4-mini（同次运行，env 钉值所致），08:59 前为旧代码全 mini 历史。"
        },
        {
          "type": "test",
          "label": "Classifier unit + worker integration + contract",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy OPENAI_API_KEY= .venv/bin/python -m unittest backend.tests.test_enablement_completion_classifier backend.tests.test_worker backend.tests.test_single_host_compose",
          "details": "8 单测（confirmed/llm false/disabled 不调用/missing key/invocation error/非 JSON/非布尔 payload/空 note）+ 93 worker 集成（含新增中文回复升级完成路径、regex 命中不调用分类器、分类器失败保持 resolution_update；存量 regex-negative 测试补 mock）+ compose 契约。空 OPENAI_API_KEY 运行证明测试密闭无真实 LLM 依赖。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy OPENAI_API_KEY= .venv/bin/python -m unittest backend.tests.test_automation_persona backend.tests.test_enablement_automation backend.tests.test_account_intake backend.tests.test_repository_configuration",
          "details": "连同前组合计 462 通过：persona、enablement 自动化、intake、repo 配置不受影响。"
        },
        {
          "type": "test",
          "label": "Syntax gates",
          "command": "python3 -m py_compile backend/worker.py backend/services/enablement_completion_classifier.py backend/services/llm_profiles.py && git diff --check",
          "details": "编译与空白检查通过。"
        },
        {
          "type": "deployment",
          "label": "Official stack restart + live markers",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && podman exec deployment_worker_aux_1 python -c \"from backend.services.enablement_completion_classifier import classify_enablement_completion; print(classify_enablement_completion('已开通', feature_label='Media Relay'))\"",
          "details": "2026-08-20 官方栈重启，/health app_build.ref=ebba123280b5 与合并后 main HEAD 一致；worker_aux 运行镜像内 prompt 版本 enablement-completion-classifier-v1、scenario profile（gpt-5.4-mini/low/温度0）解析正确；用真实凭据对中文 note '已开通' 实测分类返回 completed=True source=llm（真实 LLM 端到端判定成功）。"
        },
        {
          "type": "test",
          "label": "RAGFlow failure matrix and 12992-shaped queue handoff regression",
          "command": "../../.venv/bin/python -m pytest backend/tests/test_ragflow_docs_search_skill.py backend/tests/test_account_reply_rag_fallback.py backend/tests/test_account_human_review_escalation.py backend/tests/test_account_intake.py backend/tests/test_automation_comment_sync.py backend/tests/test_worker.py -q",
          "details": "325 tests + 66 subtests passed。覆盖无结果/证据不足、无效或非官方引用、缺 key、401/403、timeout、执行/搜索/生成/JSON 异常；12992 同形态（execution_action=rag、automation_handler=None、无 superseded handler）不再 skipped_inactive_handler，所有失败原因均调用 private-note + route_ticket_back_to_queue、置 human_review_required、释放 ownership、取消 pending jobs；main.py 与 split automation_account_reply_sync caller 均有回归。"
        },
        {
          "type": "test",
          "label": "Shared Account escalation handoff regression",
          "command": "../../.venv/bin/python -m pytest backend/tests/test_account_human_review_escalation.py backend/tests/test_account_reply_rag_fallback.py -q",
          "details": "RAG escalation 委托共享 Account Human Review service；覆盖 Production private note/queue route、staging 无 Zendesk side effect、独立失败和 outcome_unknown 门禁。"
        },
        {
          "type": "test",
          "label": "Reply RAG fallback service and intake regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_account_reply_rag_fallback backend.tests.test_account_intake",
          "details": "新增 account_reply_rag_fallback service 单测 7 项（answer/escalate 映射、RAG 故障升级、staging 仅本地标记、production note+route back+ownership、route back 失败不抛异常）与 test_account_intake 3 个新用例（RAG answer 创建 draft reply job、escalate 置 human_review_required、开关关闭保持静默旧行为）；test_account_intake 170 项全过（旧用例经 env 隔离维持原语义）。"
        },
        {
          "type": "test",
          "label": "Local live verification of both fallback paths",
          "command": "本地官方栈（lightweight + remote DB）走 /account 真实链路：enablement 追问 App ID 后发送反问/跑题回复",
          "details": "escalate 路径：'Thank you for checking.' 被重路由为 Conversation/Follow-up，真实 RAG 判 insufficient_evidence，case 置 human_review_required（not_automated_reason=reply_rag_fallback_escalation:insufficient_evidence），workspace audit 事件 account_reply_rag_fallback_escalation 落库（staging 模式跳过 Zendesk 出站）。answer 路径：'where can I find the App ID in the Agora console?' 触发真实 RAG answer，签名（Best Regards, Sid）剥离后经 publish_account_reply 原文直发为 assistant 消息（20 秒内可见），automation 状态 not_automated 保持不变。三次实测暴露并修复了直发链路的 persona v8 状态机问题（PR#872/#874/#876）与签名门禁（PR#877）。"
        },
        {
          "type": "deployment",
          "label": "Production live escalation on ticket 12931 (full chain)",
          "command": "EC2 主栈 /production（478b45d）+ Zendesk 工单 12931 + n8n 评论 snapshot 通道",
          "details": "用户指定测试工单 12931（Zac Enablememt Test）：n8n intake 自动建 AC-12931（enablement）→ AI 接管（assignee→AI agent）→ 追问 App ID 公开评论 ✅；客户真实回复 \"what is appid?\" 经评论快照触发 RAG 兜底，RAG 60s 超时按 fail-safe 升级人工：internal note（正文=指定文案+rag_error_timeout+客户原文，comment 52807992328212 public=false）、route_ticket_back_to_queue 成功（assignee 清空、group 恢复原组 27216253642772、status=open）、本地 case human_review_required、workspace audit account_reply_rag_fallback_escalation 完整落库。answer 路径（RAG 答案→production 公开评论）未在本工单触发（超时走向 escalate）；production RAG 链路耗时>60s，建议运维调大 ACCOUNT_REPLY_RAG_FALLBACK_TIMEOUT_SECONDS（如 120）后用新工单补测。另发现重复追问去重在部分路径未生效（模拟评论 -002 重复问 app_id），记为后续排查项。"
        },
        {
          "type": "deployment",
          "label": "Production RAG answer delivered as public comment on 12935",
          "command": "EC2 主栈 31745e3 + Zendesk 工单 12935 + n8n 评论快照触发",
          "details": "工单 12935（Enablement answer delivery test）完整闭环 answer 路径：n8n 自动 intake（AC-12935，enablement）→ AI 接管并公开追问 App ID → 客户反问 \"what is the App ID exactly?\" 经评论快照触发（processed）→ 重路由 rag → RAG 兜底 answer → rag_fallback_answer job 直发 → production 延迟后作为公开评论 52809771838100 发布（\"The App ID is the unique random string Agora generates in Agora Console...\"，public=true）→ delivery ledger 状态 delivered/is_public=true/comment id 一致。至此 p2-93 两条路径均在 production live 闭环（escalate=12931/12933，answer=12935）；过程共修复四层 automation 注册门（worker 投递门 PR#886、评论触发门 PR#888、InMemory+Postgres delivery ledger eligibility PR#889/#890）。"
        },
        {
          "type": "test",
          "label": "RAGFlow skill adapter and caller-path regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_ragflow_docs_search_skill backend.tests.test_account_reply_rag_fallback backend.tests.test_account_intake backend.tests.test_worker",
          "details": "295 tests passed；覆盖 skill 命令与 env 合同、grounded answer/citation 校验、检索耗尽总预算时禁止启动模型、超时与错误转人工、Account intake 及 reply worker 既有发布合同。"
        },
        {
          "type": "test",
          "label": "Missing-key fail-closed verification",
          "command": "在未配置 RAGFLOW_API_KEY 的进程中直接调用 try_rag_fallback_answer，并注入禁止执行的模型 invoker/job publisher",
          "details": "结果为 escalate / ragflow_skill_configuration；未调用模型、未创建 reply job、未产生客户发布。"
        },
        {
          "type": "test",
          "label": "Upstream ragflow-docs-search source parity",
          "command": "比较 AgoraIO-Support/AgentsGateway-Skills-Scripts@main 与本地 vendored 文件的 Git blob SHA",
          "details": "SKILL.md 均为 73682d9676d7b092bc80b09cf383943db0283f27；scripts/search.py 均为 96f34efcf200acc578651d043c3b837f19c8d4f1。"
        },
        {
          "type": "deployment",
          "label": "Credentialed RAGFlow retrieval and grounded answer on official local stack",
          "command": "官方 deployment local_lightweight 栈 9f55be557628：容器内 ticket-agent read-only search、RagflowDocsSearchSkillClient.query 与 try_rag_fallback_answer",
          "details": "仅确认 RAGFLOW_API_KEY 非空且容器已加载，不读取或输出值。ticket-agent 检索返回 6 条非空 docs.agora.io 结果；adapter 通过内建 endpoint 默认值返回 answer（494 字符、2 条官方引用）；deployed fallback 默认客户端返回 answer（682 字符、References 与官方文档 URL 均存在）。全过程未创建 case、reply job、delivery ledger 或 Zendesk 评论。官方 image/health/runtime ref 均匹配 9f55be557628，auxiliary stack 不存在。"
        },
        {
          "type": "deployment",
          "label": "Production RAGFlow deployment and container-level grounded answer verification",
          "command": "EC2 scripts/ops/deploy_surfaces_ec2.sh --skip-split + https://support.stellarix.space/health + deployment-api_production-1 container checks",
          "details": "将 RAGFLOW_BASE_URL 和非空 RAGFLOW_API_KEY 原子写入 EC2 .env（未读取或输出 key），仅部署 main stack 到 52e9d3595a0e。外部 /health 返回同 ref、/production/ HTTP 200；api_production 使用 localhost/supportportal-app:52e9d3595a0e，默认 client 为 RagflowDocsSearchSkillClient，容器已加载 ticket-agent endpoint 与非空 key。通用 Agora RTC token 问题的容器内 adapter 调用返回 answer、答案非空、2 条 docs.agora.io 引用；api_production 与 production workers 启动后 ERROR/Traceback 计数均为 0。未创建 case、reply job、delivery ledger 或 Zendesk 评论，因此客户公开投递/readback blocker 保留。"
        },
        {
          "type": "test",
          "label": "Citations append and marketing footer strip",
          "command": ".venv/bin/python -m unittest backend.tests.test_account_reply_rag_fallback backend.tests.test_account_intake backend.tests.test_worker",
          "details": "新增 3 项单测：12940 真实营销尾模板整块剥离（May Collins/Discord/support-plans 全部移除且正文保留）、citations 按 URL 去重并以 heading — url 附加 References、短签名规则回归；11 项 fallback 单测 + intake/worker 套件全绿。"
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
      "task_count": 18,
      "done_count": 12,
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
          "type": "test",
          "label": "Registered Enablement deterministic routing regression",
          "command": ".venv/bin/pytest -q backend/tests/test_enablement_automation.py backend/tests/test_account_route_pipeline.py backend/tests/test_account_intake.py",
          "result": "Included in the focused Account routing and ownership suite: 340 passed with 51 subtests passed. A separate 20-run direct check classified the case #12875 message shape as media_relay 20/20 times without calling the Agora Router model."
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
          "type": "test",
          "label": "Account Human Review queue handoff regression",
          "command": "../../.venv/bin/python -m pytest backend/tests/test_account_human_review_escalation.py backend/tests/test_account_intake.py backend/tests/test_worker.py -q",
          "details": "283 passed；覆盖 Production private note + route back、staging 无出站、note/route 独立失败、审计幂等、非 numeric identity、outcome_unknown 不重试，以及四类 Account intake/reply worker fallback 的 human_review_required、not_automated 和 pending job cancellation。"
        },
        {
          "type": "test",
          "label": "Human Review queue mismatch reconciliation",
          "command": "../../.venv/bin/python -m pytest backend/tests/test_account_human_review_escalation.py backend/tests/test_account_reply_rag_fallback.py backend/tests/test_account_intake.py backend/tests/test_worker.py -q",
          "details": "297 passed；覆盖旧 worker manual_attention 漏接、Production bounded reconciliation、staging/no-side-effect、AI ownership guard 和 handoff 终态幂等。"
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
        },
        {
          "type": "test",
          "label": "Default-assignment and public-human-reply ownership regression",
          "command": ".venv/bin/pytest -q backend/tests/test_account_automation_ownership.py backend/tests/test_zendesk_ticket_assignment.py backend/tests/test_enablement_automation.py backend/tests/test_account_route_pipeline.py backend/tests/test_worker.py backend/tests/test_account_intake.py",
          "result": "340 passed with 51 subtests passed. Coverage includes default human assignment takeover before any public human reply, complete paginated comment history, customer and AI comment exclusions, unknown-author fail-closed behavior, AI group plus assignee transfer, safe-update conflict reconciliation including a concurrent human reply, post-takeover human reassignment, post-takeover public human reply, terminal worker delivery cancellation, and ownership event diagnostics."
        },
        {
          "type": "test",
          "label": "Ownership retry + detail persistence",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_automation_ownership backend.tests.test_zendesk_ticket_assignment",
          "details": "27 全绿：422 重试成功（重取快照+复PUT 后 assigned）；持续 422 三次尝试后 failed 且 automation_context 持久化 failure_detail；重试窗口出现人工回复按 policy 停机且不再 PUT；HTTPError 错误体解析为 detail；原有 409 冲突恢复与禁用重试（env 置空）语义保持。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_account_intake backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_assignment backend.tests.test_account_reply_publication_postgres backend.tests.test_zendesk_comments",
          "details": "290 通过、8 跳过（无活库 Postgres 集成用例，与改动前一致）：delivery/verify 流、intake gate 包装、手动 assignment 端点、评论服务全部不受影响。"
        },
        {
          "type": "test",
          "label": "Syntax gates",
          "command": "python3 -m py_compile backend/services/account_automation_ownership.py backend/services/zendesk_ticket_assignment.py backend/services/zendesk_comments.py backend/main.py && git diff --check",
          "details": "四个改动文件编译与空白检查通过。"
        },
        {
          "type": "deployment",
          "label": "Official stack restart + live markers",
          "command": "podman exec deployment_api_1 python -c \"from backend.services.account_automation_ownership import DEFAULT_ASSIGNMENT_RETRY_DELAYS, _assignment_retry_delays; from backend.services.zendesk_ticket_assignment import _http_error_detail; print(DEFAULT_ASSIGNMENT_RETRY_DELAYS, callable(_http_error_detail))\"",
          "details": "2026-08-20 官方栈（app_build.ref=5318360e267f）：运行镜像内默认退避 (20.0, 40.0)、env 解析与错误体捕获函数均在。真实 422 路由窗口重试事件待 EC2 下一次部署后的新工单自然验证（本地栈为 staging，不触发 production gate）。"
        },
        {
          "type": "test",
          "label": "Initial delay + detail capture",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_automation_ownership backend.tests.test_zendesk_ticket_assignment",
          "details": "32 全绿：默认 90s 等待后重取快照再用新 updated_stamp 发 PUT；等待期间人工回复停机且不发 PUT；已分配匹配路径零等待；env=0 立即分配；422 响应体 details 以紧凑 JSON 追加进 detail（RecordInvalid | {\"assignee_id\":[...]}）。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_account_intake backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_assignment backend.tests.test_account_zendesk_comment_sync backend.tests.test_zendesk_comments",
          "details": "301 通过（worktree 需先 link_worktree_env.sh，否则 intake 分类缺凭证误报）。intake gate 包装、verify 流、手动 assignment、评论服务全部不受影响。"
        },
        {
          "type": "deployment",
          "label": "Local official stack restart + live markers",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && podman exec deployment_api_1 python -c \"from backend.services.account_automation_ownership import DEFAULT_ASSIGNMENT_INITIAL_DELAY, _assignment_initial_delay; from backend.services.zendesk_ticket_assignment import _http_error_detail; print(DEFAULT_ASSIGNMENT_INITIAL_DELAY, _assignment_initial_delay(), callable(_http_error_detail))\"",
          "details": "2026-08-20 官方栈重启（app_build.ref=1020e2e26c9b，/health status=ok）：运行镜像内默认初始延迟 90.0、env 解析 90.0、错误体捕获函数均在。"
        },
        {
          "type": "deployment",
          "label": "EC2 production stack deploy + live markers",
          "command": "ssh zacbot ./deployment/deploy_ec2.sh --branch main（auto-deploy 自动执行）+ docker exec deployment-api_production-1 python -c \"...import DEFAULT_ASSIGNMENT_INITIAL_DELAY, _assignment_initial_delay...\" + curl https://support.stellarix.space/health",
          "details": "2026-08-20 EC2 生产栈（main=1020e2e）：api/worker_query/worker_aux production 容器全部运行 supportportal-app:1020e2e26c9b；域名 /health status=ok build=1020e2e26c9b；生产容器内 DEFAULT_ASSIGNMENT_INITIAL_DELAY=90.0、_assignment_initial_delay()=90.0。真实 90s 等待路径待下一个 production 自动化工单自然验证。"
        },
        {
          "type": "test",
          "label": "Autofill payload tests",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_automation_ownership",
          "details": "34 全绿：字段为空时 PUT 附带 custom_fields:[{id:31503099534100,value:video_calling}]；字段已有值（voice_calling）时完全不附带；快照 required_field_missing 两种取值解析正确。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_account_intake backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_assignment backend.tests.test_account_zendesk_comment_sync backend.tests.test_zendesk_comments",
          "details": "301 通过（worktree 需从仓库根执行 link_worktree_env.sh）。"
        },
        {
          "type": "deployment",
          "label": "Local official stack restart + marker",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && podman exec deployment_api_1 python -c \"from backend.services.zendesk_ticket_assignment import ZENDESK_ASSIGNMENT_REQUIRED_FIELD_ID, ZENDESK_ASSIGNMENT_REQUIRED_FIELD_VALUE; print(...)\"",
          "details": "2026-08-21 官方栈（app_build.ref=0cffc5950cc0，/health ok）容器内常量 31503099534100/video_calling 生效。"
        },
        {
          "type": "deployment",
          "label": "EC2 production deploy + live takeover on 12893",
          "command": "ssh zacbot ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space + docker exec deployment-api_production-1 python -c \"assign_ticket_to_configured_ai(ticket_id='12893')\"",
          "details": "2026-08-21 EC2 生产栈部署 0cffc5950cc0（域名 /health ok）。真实验证链：① 顶层键形式实测被忽略（12893 PUT 仍 422 needed，detail 完整捕获）；② custom_fields 数组形式对 12893 实测 200 并写入 video_calling；③ 部署修正版后经生产容器调用 assign_ticket_to_configured_ai 成功：assignee=48557297720084（AI agent）、group=29388501432596、sdk_product=video_calling。全新工单的 PUT 内自动填充路径待下一个真实 production 自动化工单自然验证。"
        },
        {
          "type": "test",
          "label": "Handoff service + worker hook tests",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_zendesk_ticket_assignment backend.tests.test_worker",
          "details": "115 全绿：按 id 解析 reviewer（非数字 id、inactive agent 均 fail-closed）、assign 到 default group 的 PUT payload、already-assigned no-op；worker 侧 public+fraud 触发 / internal、非 fraud、缺配置不触发；handoff 失败记录 failed 事件且不影响已发布回复。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_automation_ownership backend.tests.test_account_intake backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_assignment backend.tests.test_account_zendesk_comment_sync backend.tests.test_zendesk_comments",
          "details": "229 通过。"
        },
        {
          "type": "deployment",
          "label": "Local official stack + EC2 production deploy",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote；ssh zacbot ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space",
          "details": "2026-08-21 双栈部署 ba2a44d3d67c（PR#835），/health ok；本地与 EC2 生产容器内 assign_ticket_to_reviewer 可导入、ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID=31116634341396 已注入 worker。"
        },
        {
          "type": "deployment",
          "label": "Live permission + function verification",
          "command": "docker exec deployment-api_production-1 python -c \"assign_ticket_to_reviewer(ticket_id='12895', reviewer_user_id='31116634341396')\"",
          "details": "前置权限试探（12895 手动 PUT assignee=xieziling+group=Tier 2 CSE → 200，AI agent token 有 assign 权限，无需 Admin）；部署后函数级验证：GET /users/{id}.json 解析 + ticket 比对 → already_assigned（200）。注：AI agent token 无 users/search（403）与 show_many?emails（空）权限，故按 id 配置。完整自动链路（fraud public 回复发布 → 自动 handoff 事件）待下一个真实 fraud_account 工单自然验证。已知边界：已 solved 工单的任何 API 更新被声明式 checkbox 36379228408724 拦截（12893 实测 422，detail 明确）；fraud 回复后工单为 pending，不受影响。"
        },
        {
          "type": "test",
          "label": "Status sync endpoint + repository transition tests",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_status_sync backend.tests.test_account_zendesk_status_sync_postgres",
          "details": "9 全绿：token 401/422/404、solved 联动关闭（resolved+closed_at+automation_status=closed+prior 快照+审计事件）、unchanged/stale_ignored、重开恢复 automation、status_endpoint 字段、summary/detail payload 带出新字段；Postgres 契约（同事务 SQL 参数数、重开不写工单、unchanged/stale 零写入、缺案 KeyError）。"
        },
        {
          "type": "test",
          "label": "n8n offset timestamp boundary",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_status_sync",
          "details": "覆盖 n8n 的 2026-08-21T03:09:00.862-04:00，API 接受并将 zendesk_status_updated_at 规范化为 2026-08-21T07:09:00.862000+00:00；非法日期与 zendesk_status=ticket id 仍返回 422。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_zendesk_comment_sync_postgres backend.tests.test_account_intake backend.tests.test_account_reply_publication_postgres backend.tests.test_worker backend.tests.test_repository_configuration backend.tests.test_account_automation_ownership backend.tests.test_workspace_api backend.tests.test_account_full_reroute backend.tests.test_account_reroute_dispatch backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_zendesk_assignment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_comment backend.tests.test_zendesk_comments backend.tests.test_account_reply_version_fence backend.tests.test_account_slack_n8n",
          "details": "572 通过（8 skip 为无 DSN 的 Postgres 用例）；UI 契约（account/production 含新徽章断言与版本串 20260821-zendesk-status-1）另 55 通过。"
        },
        {
          "type": "deployment",
          "label": "Migrations applied to both databases",
          "command": "psql $TICKET_DB_MIGRATION_DSN / $PRODUCTION_TICKET_DB_DSN -f backend/sql/migrations/2026_08_21_account_zendesk_status_sync.sql（staging 需 migration DSN，runtime 角色非 owner）",
          "details": "2026-08-21 对 supportportal（TICKET_DB_MIGRATION_DSN，zac）与 supportportal_production（runtime DSN）各执行迁移；information_schema 确认两库 zendesk_ticket_status/zendesk_status_updated_at/zendesk_status_synced_at 三列齐全。"
        },
        {
          "type": "deployment",
          "label": "Local official stack + EC2 production deploy",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote；ssh zacbot ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space",
          "details": "PR#837 合并（main=9587d44）后双栈部署：本地 /health ok app_build.ref=9587d44a47ea（local_lightweight）；EC2 外部 https://support.stellarix.space/health ok 同 ref（full）。/account 与 /production 页面均 serving app.js?v=20260821-zendesk-status-1。"
        },
        {
          "type": "deployment",
          "label": "Live status sync on both origins",
          "command": "curl -X PUT -H 'X-Zendesk-Account-Sync-Token: …' -d '{\"zendesk_status\":…}' http://127.0.0.1:8080/api/integrations/zendesk/account-cases/12862/status 与 https://support.stellarix.space/production/api/integrations/zendesk/account-cases/12896/status",
          "details": "staging 源（12862）：target 返回 status_endpoint、push open→updated、重放→unchanged、审计事件落库。production 源（12896，Zendesk 实测 solved 而本地仍 open 的真实缺口）：push solved→updated+local_ticket_closed=true；DB 终态 support_tickets=resolved+closed_at、case zendesk=solved automation=closed prior=automation、审计 closed=true。n8n 工作流待用户按 docs/integrations/n8n/zendesk_account_status_sync.md 配置。"
        },
        {
          "type": "test",
          "label": "Route-back service, API, ownership fence, Production UI, and worker regression",
          "command": "TICKET_DB_DSN=postgresql://example.invalid/test SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_assignment backend.tests.test_account_automation_ownership backend.tests.test_zendesk_ticket_assignment backend.tests.test_production_ui_contract backend.tests.test_worker",
          "details": "171 tests pass：覆盖保存/审计原真人 group、真人 assignment 不清空、已 queued 零 PUT、audit fallback、无可靠 group fail closed、safe_update payload、outcome_unknown 只读对账且不可盲重试、human_review/released worker fence、Production admin API 与 UI contract。"
        },
        {
          "type": "test",
          "label": "Production JavaScript and Project Overview contracts",
          "command": "node --check ui/production-ui/app.js && git diff --check && python3 scripts/generate_project_overview.py --check",
          "details": "JavaScript syntax、diff whitespace 与 Project Overview generated view 校验通过；Production asset marker 为 20260821-route-back-queue-1。"
        },
        {
          "type": "test",
          "label": "Route-back email, Zendesk fence, UI and Graph Mail regression",
          "command": "TICKET_DB_DSN=postgresql://example.invalid/test SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_assignment backend.tests.test_account_automation_ownership backend.tests.test_zendesk_ticket_assignment backend.tests.test_production_ui_contract backend.tests.test_worker backend.tests.test_workspace_invitations backend.tests.test_account_failure_alerts",
          "details": "177 tests pass：覆盖 queued/assigned/already_human_owned/failed/outcome_unknown 邮件、固定收件人和无客户正文、邮件失败不重放 Zendesk、非法请求零邮件、审计状态、UI notification toast 与 120 秒 timeout，以及既有 worker/ownership/Graph Mail 回归。"
        },
        {
          "type": "test",
          "label": "JavaScript and generated Project Overview contracts",
          "command": "node --check ui/production-ui/app.js && git diff --check && python3 scripts/generate_project_overview.py --check",
          "details": "JavaScript syntax、diff whitespace、Project Overview generated view 校验通过；Production asset marker 为 20260821-route-back-email-1。"
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
      "task_count": 14,
      "done_count": 13,
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
        },
        {
          "type": "test",
          "label": "Affected unit suites",
          "command": "rtk python3.12 -m pytest backend/tests/test_llm_usage_capture.py backend/tests/test_workspace_api.py backend/tests/test_rag_api.py backend/tests/test_account_ai_execution.py backend/tests/test_workspace_admin_ui_contract.py -q",
          "details": "90 passed（新增 capture 15 例：作用域/no-op/flush/bind/两封装记录/JSON 失败仍记录（4 次重试各一条）；admin 合并与 RagServiceError 不可见路径 2 例；RAG batch 端点 1 例）。"
        },
        {
          "type": "test",
          "label": "Admin UI contract suite",
          "command": "rtk python3.12 -m pytest backend/tests/test_workspace_admin_ui_contract.py -q",
          "details": "28 passed；sandbox 内新增断言：Tokens 列头/本页 tokens 指标/不可用占位 —/1,234 in / 567 out 单元格/toggle-token-detail/展开明细含 rag_answer、quota_field_extractor、openai:gpt-test、calls；版本串断言更新为 20260824-token-usage-1。"
        },
        {
          "type": "test",
          "label": "Worker/intake wrapper regression suites",
          "command": "rtk python3.12 -m pytest backend/tests/test_worker.py backend/tests/test_account_zendesk_comment_sync.py -q",
          "details": "121 passed。注意：worktree 需先 scripts/workflow/link_worktree_env.sh 链接 root .env，否则 route 凭据缺失导致 ZendeskCommentTriggerTests 假失败；test_production_ui_contract 的 deploy 脚本断言失败在干净 main 同样失败（遗留问题，非本任务引入，与 p2-104 evidence 记录一致）。"
        },
        {
          "type": "decision",
          "label": "本地栈预验证跳过原因",
          "command": "",
          "details": "本地 podman 栈与 EC2 共用 RDS supportportal 库且 worker 会争抢 reply/rerun job（既有教训）；本次为纯展示改动，用 TestClient 端点测试 + Node sandbox 契约测试确定性覆盖渲染逻辑，避免未合并代码抢占真实生产 job。runtime live 验证按仓库规则留待合并后官方栈重启执行。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack restart + live verification",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote",
          "details": "PR#903 合并后（root main 897e70c）重启官方轻量栈：镜像 localhost/supportportal-app:897e70c88f88；/health ok 且 app_build.ref=897e70c88f88（rag_service ok、runtime_profile=local_lightweight）；inspect_single_host_stack_mode.sh 复查 build_provenance_status=matched、auxiliary_stack_present=false。live marker：/workspace/admin/ 页面资产 app.js?v=20260824-token-usage-1；GET /api/workspace/admin/account-automation 未授权返回 401（守卫与路由存活）；repository ensure 已在共享库建好 support_account_case_llm_usage 表与索引（to_regclass 双确认，rows=0 属预期）。"
        },
        {
          "type": "test",
          "label": "RAG batch endpoint live data check",
          "command": "podman exec deployment_api_1 python (POST http://rag_api:8020/internal/rag/ticket-families/token-usage/batch)",
          "details": "真实数据验证（经运行栈、内部 auth）：ticket 12940 返回 11,561 in / 4,358 out / 28 emb，stage_totals=rag_answer×4+embedding×4+query_self_query×2+query_rewrite×2，token_by_model=openai:gpt-5.4 / openai:gpt-5.4-mini / siliconflow:BAAI/bge-m3，与库内 support_rag_query_runs 逐项一致；12951 同样非零且明细正确；errors=[]。"
        },
        {
          "type": "decision",
          "label": "已知边界（代码核实）",
          "command": "",
          "details": "①独立 route_service 容器（/v1/cases 控制台流的路由+准备，设计上无 ticket DB）的 tokens 不采集：production /v1/cases 走 call_route HTTP 到该容器；若需要可后续经 RouteResult 契约透传另开任务。②provider 报错的重试 attempt 无 usage 可记（错误响应不含 usage），只记成功 invocation。③自动化侧历史无法回补，只有上线后新数据；RAG 侧含全部历史。④本地栈 admin 的 RAG 数字取决于该栈 RAG 服务指向的知识库，权威视图为 EC2 production 栈。"
        },
        {
          "type": "test",
          "label": "New unit suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_factory.py backend/tests/test_llm_pricing.py backend/tests/test_llm_usage_capture.py -q",
          "details": "factory details 解析 3 例（Responses/Chat/无 details 容错）+ pricing 7 例（未定价 unavailable、跨模型求和、cached 回落 input 价、cached 不超 input、空 usage=0、默认表全 None）+ capture 透传/InMemory roundtrip 含 token_by_model 分桶两列。"
        },
        {
          "type": "test",
          "label": "Affected full suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest \u003c15 个受影响套件> -q",
          "details": "524 passed。两个失败（test_rag_agentic comparison first_pass_tools、test_rag_service_client probe_health_disabled）在干净 root main 以完全相同组合同样失败、单跑均通过——既有跨文件顺序污染，非本任务引入。"
        },
        {
          "type": "test",
          "label": "Mock tuple migration",
          "command": "",
          "details": "test_rag_qa.py（25 处跨行+4 处内联+2 处直调解包+7 处类型注解）与 test_rag_agentic.py（3 处）的 _invoke_llm_payload_with_trace mock 4 元组→6 元组（尾部补 0,0）。"
        },
        {
          "type": "decision",
          "label": "价格表默认留空的决策依据",
          "command": "",
          "details": "docs/prompt_change_log.md（gpt54-token-only-observability-v1 条目）：旧成本展示曾因过时/不全价格造成噪音被有意移除，约定保留 unknown-cost markers、未定价显式标记不静默 0；gpt-5.4/gpt-5.6-luna 等为本环境具体模型，价格数字须用户提供，不可编造。knowledge_repository.py _model_cost_for_tokens 为引用不存在字典的死代码，未模仿。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh",
          "details": "PR#917 合并后官方栈运行 root main 4dc624f（含本任务，built 2026-08-24T11:06:13Z）：build_provenance_status=matched、official_health_build_ref=4dc624fbb1a7、auxiliary_stack_present=false；/workspace/admin/ 实际服务 app.js?v=20260824-token-cost-1；GET /api/workspace/admin/account-automation 未授权 401（守卫存活）；共享库 support_account_case_llm_usage 列序含 cached_input_tokens/reasoning_tokens（repository 幂等 ALTER 生效）。价格表未填时成本显示 $— 属预期。"
        },
        {
          "type": "test",
          "label": "Affected suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_pricing.py backend/tests/test_workspace_admin_ui_contract.py backend/tests/test_workspace_api.py -q",
          "details": "67 passed + 4 subtests（合并前在任务 worktree 跑）。新增/更新：默认表 luna 三价精确断言+其余模型全 None、纯 luna 端到端计价（100 万 input 含 20 万 cached + 5 万 output=$0.224）、model_pricing_payload 形状、端点 model_pricing 契约、前端横条渲染/未定价标记/无数据不渲染、版本串断言 20260825-model-pricing-1。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh",
          "details": "PR#940 合并后官方栈运行 root main 48ca775（built 2026-08-25T03:43:54Z）：official_health_status=ok、build_provenance_status=matched、official_health_build_ref=48ca775d09ad；/workspace/admin/ 实际服务 app.js?v=20260825-model-pricing-1；admin/admin 登录后 GET account-automation 实测 model_pricing：gpt-5.6-luna priced=true（0.2/0.02/1.2），其余五模型 priced=false；当前页 case 为 luna+mini 混合（EC2 旧代码仍在产 mini 条目），成本 $— 与 page cost_usd_available=false 符合全有或全无契约。"
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10",
        "docs/roadmap.html",
        "docs/feature_list.md"
      ],
      "legacy_ids": [],
      "status": "active",
      "task_count": 11,
      "done_count": 6,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "ecs-environment-migration",
      "phase_id": "phase-1",
      "module_id": "platform-delivery",
      "title": "ECS 三环境迁移",
      "goal": "以 ECS Staging、Preproduction 与 Production 建立可验证、可晋升、可回滚且不影响旧 EC2 /production 的发布体系。",
      "acceptance_criteria": [
        "Staging、Preproduction 与 Production 均运行于 ECS，并使用独立资源身份、队列、凭据和入口；rerun/reset 测试代码不进入 Preproduction 或 Production 镜像。",
        "三环境从 ECR 使用可追溯的不可变 release digest，Preproduction 与 Production 只晋升已验收的同一组 digest。",
        "ECS Production 支持预热、健康门禁、蓝绿发布、请求排空和不重放请求的回滚，迁移期间旧 EC2 /production 保持独立运行。"
      ],
      "evidence": [
        {
          "type": "decision",
          "label": "EC2 split environments retired",
          "details": "2026-08-25 用户确认 Staging、Preproduction、Production 全部转为 ECS 承载；EC2 保留主栈与现有 /production，三条 /automation/* 路径下线。"
        },
        {
          "type": "decision",
          "label": "ECS migration architecture agreed",
          "command": "Architecture discussion 2026-08-25",
          "details": "最初确定本地 Staging、ECS Preproduction/Production；2026-08-25 用户进一步确认 Staging也迁入 ECS。三环境均从 ECR晋升可追溯 release，首次 Production启用通过独立 n8n endpoint完成，不触碰当前 EC2 /production。"
        },
        {
          "type": "document",
          "label": "EC2 split decommission prepared",
          "command": "deployment and Nginx contract update 2026-08-25",
          "details": "EC2主 Nginx对三条 /automation/* 路径返回410，main与timer部署不再构建、部署或验证 split环境，也不再创建或连接 split网络；运行数据与 volumes明确保留。"
        },
        {
          "type": "test",
          "label": "EC2 retirement deployment contracts",
          "command": ".venv/bin/python -m unittest backend.tests.test_auto_deploy_ec2 backend.tests.test_split_environment_deployment backend.tests.test_deploy_ec2 backend.tests.test_single_host_compose",
          "details": "79项全绿：覆盖定时 wrapper main-only参数、surface脚本无 split build/deploy/verify、三条路径410、/automation/test继续代理 production API、main部署不创建或连接 split网络，以及既有 Compose/legacy rollback契约。"
        }
      ],
      "source_refs": [
        "backend/Dockerfile.automation",
        "deployment/docker-compose.single-host.yml",
        "deployment/build_automation_release.sh",
        "deployment/deploy_automation_production_blue_green.sh",
        "docs/deploy_automation_release.md",
        "docs/integrations/n8n/automation_environments_cutover.md"
      ],
      "legacy_ids": [],
      "status": "active",
      "task_count": 1,
      "done_count": 0,
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
          "label": "Split deployment and compose contracts",
          "command": ".venv/bin/python -m unittest backend.tests.test_deploy_ec2 backend.tests.test_single_host_compose backend.tests.test_split_environment_deployment backend.tests.test_build_automation_release",
          "details": "66 项通过：compose 七个 automation profile 服务与 worker 契约（镜像/DB 绑定/队列隔离/邮件命名空间/poller 门控/.msgraph 挂载/网络）、蓝绿契约（worker recreate+APP_RUNTIME_IMAGE 校验）、bootstrap 脚本契约（migration DSN 必填/同库校验/防误指 staging 主库/deploy 集成）、deploy_ec2 假命令回归（production 路径 bootstrap 前置+worker 服务清单+*_worker 健康判定）。"
        },
        {
          "type": "test",
          "label": "Production migration DSN isolation",
          "command": ".venv/bin/python -m unittest backend.tests.test_production_blue_green_behavior backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_deploy_ec2",
          "details": "77 项通过：production bootstrap 只读取 AUTOMATION_PRODUCTION_DB_MIGRATION_DSN；即使全局 TICKET_DB_MIGRATION_DSN 指向 supportportal，专用值仍映射到一次性 runtime_bootstrap 的 TICKET_DB_MIGRATION_DSN 并目标 supportportal_production；专用值缺失或指向其他数据库时在任何 Compose up 前 fail closed，蓝绿 candidate/worker/cutover 顺序与 Compose 契约保持通过。"
        },
        {
          "type": "test",
          "label": "Production runtime deployment gates",
          "command": ".venv/bin/python -m unittest backend.tests.test_deploy_ec2 backend.tests.test_single_host_compose backend.tests.test_prompt_versioning backend.tests.test_account_verification_automation",
          "details": "长期运行的主栈五个服务与 /production 三个服务显式清空 AUTOMATION_PRODUCTION_DB_MIGRATION_DSN，仅一次性 runtime_bootstrap 保留 DDL 凭据；deploy_ec2 在 activate 前校验八个 Prompt runtime 的容器状态、镜像 ID、build ref、release、当前容器日志与 health，并以稳定窗口拒绝 worker 重启。activate 后 production sync/readback 失败返回非零但不回滚已健康主栈。"
        },
        {
          "type": "test",
          "label": "Prompt Release target-local version remap",
          "command": ".venv/bin/python -m unittest backend.tests.test_prompt_versioning backend.tests.test_deploy_ec2",
          "details": "63 项通过：同号异内容分配目标本地新版本、已有同 hash 的不同本地版本直接复用、candidate 不改变目标 active release、激活后两库 release snapshot 内容一致、篡改 hash 继续 fail closed；EC2 近库随机双 schema collision test 1 项通过并自动清理。"
        },
        {
          "type": "test",
          "label": "Production API Prompt runtime service identity",
          "command": ".venv/bin/python -m unittest backend.tests.test_startup_repository_fallbacks backend.tests.test_prompt_versioning backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_prompt_runtime_verification_retries_transient_startup_failure backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_prompt_runtime_verification_rejects_stale_image backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_prompt_runtime_verification_rejects_stale_build_or_release backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_prompt_runtime_verification_rejects_restarting_worker backend.tests.test_single_host_compose.SingleHostComposeTests.test_prompt_runtime_release_is_shared_by_all_llm_services_only",
          "details": "API schema-check startup honors PROMPT_RUNTIME_SERVICE=api-production while preserving api default；部署门禁继续拒绝 stale image/build/release 与 worker restart，Compose 八 runtime service labels 保持一致。"
        },
        {
          "type": "deployment",
          "label": "/production fast deployment",
          "command": "scripts/ops/deploy_surfaces_ec2.sh --skip-split",
          "details": "EC2 无外层 timeout 部署成功：公网 /health build=76d22d5ae1a3、Prompt Release=pr-c9b3a291ecf1；/production/ 200；主栈五个与 production 三个 runtime 使用同一镜像/build/release，RestartCount=0，workers 稳定观察 10 秒；主库与 production DB active release 回读一致且 Fraud v4/code-hash validation=loaded。/automation/production/health 200 并按 --skip-split 保持原 build 48ca775d09ad；未执行客户 Ticket。日志 /tmp/deploy-surfaces-20260825-094728/main-stack.log。"
        },
        {
          "type": "test",
          "label": "Parity intake and runtime contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_account_intake backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_contracts backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_deploy_ec2 backend.tests.test_build_automation_release",
          "details": "111 项全部通过：intake 六分支单测（fraud 缺/齐字段、suspension contact、抽取失败→#916 升级、not_automated→Engineer Case+派单、ownership fail-closed）；production runtime 契约（无 visibility 也进管线、pipeline 异常→failed+409 重放、legacy 五字段免 visibility、intake_outcome 落库）；契约矩阵（production visibility 可选，preprod forced internal 不变）；route_payload decision 字段；bundle/镜像清单（依赖模块留在 production 镜像）；compose/deploy/蓝绿假命令回归。"
        },
        {
          "type": "test",
          "label": "Comment ingestion and reply chain regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_comment_sync backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_account_intake backend.tests.test_automation_contracts backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_account_zendesk_comment_sync_postgres",
          "details": "86 项通过：comment-sync-target 鉴权与 membership、PUT comments 快照校验/404/触发调用、agent/initial 忽略不占幂等、Engineer Case 分支事件落库、既有 intake/runtime/contracts/compose 全回归绿。"
        },
        {
          "type": "test",
          "label": "Status sync endpoint and full parity regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_comment_sync backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_account_intake backend.tests.test_automation_contracts backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_account_zendesk_status_sync",
          "details": "94 项通过：status 端点鉴权/非法状态 422、solved 关 Engineer Case（线程事件+ticket resolved+派单 resolve 断言）、open 不触发收尾；既有评论/intake/runtime/contracts/compose 全回归绿。"
        },
        {
          "type": "test",
          "label": "Engineer Slack endpoints and parity regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_comment_sync backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_account_intake backend.tests.test_automation_contracts backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose",
          "details": "91 项通过：Slack 端点鉴权/非法载荷 422、messages 跑完整 AI 回合（conversation/draft 版本与 engineer_ai_response 事件断言）、thread-bindings 未配置 503、评论触发 Engineer 分支升级后版本断言；既有全回归绿。"
        },
        {
          "type": "test",
          "label": "Static verification and parity regression",
          "command": "bash -n deployment/verify_split_environments.sh && .venv/bin/python -m unittest backend.tests.test_split_environment_deployment backend.tests.test_automation_comment_sync backend.tests.test_automation_production_runtime_contract",
          "details": "verify 脚本语法通过；split 部署/评论/intake/runtime 契约回归绿。"
        },
        {
          "type": "test",
          "label": "Usage capture and prepare-flag regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_comment_sync backend.tests.test_route_service_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_contracts backend.tests.test_automation_account_intake backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose",
          "details": "94 项通过：新增 runtime 断言 route_request.prepare=False、回复链 begin/end/flush 调用与条目数断言；route 契约（含新字段默认行为）与既有全回归绿。"
        },
        {
          "type": "test",
          "label": "Production Automation classification email regression",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/pytest -q backend/tests/test_production_automation_classification_email.py backend/tests/test_account_intake.py backend/tests/test_automation_production_runtime_contract.py backend/tests/test_worker.py backend/tests/test_repository_configuration.py backend/tests/test_automation_routing.py backend/tests/test_account_route_pipeline.py",
          "details": "fresh suite 合计 458 项通过；覆盖 active Automation 匹配、分类路径与客户问题、可信 Case 链接、幂等 outbox、Graph 成功/失败/未知状态和既有 Account/worker/repository/routing 回归。"
        },
        {
          "type": "document",
          "label": "Project records and generated overview",
          "command": "python3 scripts/verify_feature_list.py; python3 scripts/generate_project_overview.py --write; python3 scripts/generate_project_overview.py --check; git diff --check; python3 -m compileall -q backend",
          "details": "Feature list、Project Overview 生成与检查、差异空白检查和 Python compile check 均通过。"
        },
        {
          "type": "document",
          "label": "Implemented plan review",
          "command": "review-implemented-plan skill",
          "details": "review 未发现需修复的功能性问题。"
        },
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
          "type": "deployment",
          "label": "Official stack restart + live markers",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && podman exec deployment_api_1 python -c \"import urllib.request; html=urllib.request.urlopen('http://127.0.0.1:8000/production/', timeout=10).read().decode(); print('\u003ctitle>Account Production\u003c/title>' in html)\"",
          "details": "2026-08-20 官方栈重启成功，/health app_build.ref=5318360e267f 与合并后 main HEAD 一致；/production 页面由 api 挂载返回且标题为 Account Production（资源版本串已被后续 automated-public 工作更新为 20260819-automated-public-1，与当前 main 一致）。EC2 侧已部署并可访问 /production/（用户确认），production 库/容器组随 profile 生效。"
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
          "type": "deployment",
          "label": "Official stack restart + live markers",
          "command": "podman exec deployment_api_1 python -c \"import urllib.request, urllib.error; js=urllib.request.urlopen('http://127.0.0.1:8000/account/app.js', timeout=10).read().decode(); print('forward' in js)\" ; POST /api/account/cases/AC-X/promote-production -> 404",
          "details": "2026-08-20 官方栈（app_build.ref=5318360e267f）：/account app.js 含 forwardAccountCaseToProduction 且无 promote-production 残留；后端 promote-production 端点已删除（404）。端到端转发在 EC2 production 环境运行（本地栈不启用 production profile，属设计行为）。"
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
        },
        {
          "type": "test",
          "label": "Namespaced internal email suite",
          "command": ".venv/bin/python -m pytest -q backend/tests/test_internal_email_namespace.py backend/tests/test_worker.py backend/tests/test_billing_automation_email.py backend/tests/test_enablement_automation.py backend/tests/test_account_verification_automation.py backend/tests/test_account_intake.py backend/tests/test_repository_configuration.py",
          "result": "425 passed with 39 subtests."
        },
        {
          "type": "deployment",
          "label": "Live isolation verification",
          "command": "deploy_ec2.sh + restart_single_host_stack.sh; container env probe; worker logs",
          "result": "PR #815 + #816 deployed (main 6a30eb1; local stack health ref 6a30eb11d5b9). Staging container namespace '[staging]' -> subject '[staging][Enablement Request]'; production container empty -> unchanged subject. Staging worker noise for ticket 12872 stopped immediately (namespace filter); production 12804 claim-time loop terminated via terminal dismissal (0 warnings in the last minute)."
        },
        {
          "type": "test",
          "label": "Split Route/Automation contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_automation_contracts backend.tests.test_account_automation_ownership",
          "details": "本轮 27 项 split/image/ledger/runtime 契约测试通过；覆盖 Route side-effect-free action plan、account_suspension preparation、三环境 capability、production visibility、production OpenAPI 无 rerun/reset、服务端 Zendesk delivery readback、preproduction ownership、六服务/profile 和 Route/production image exclusion contract。"
        },
        {
          "type": "test",
          "label": "Static syntax and configuration checks",
          "command": ".venv/bin/python -m py_compile ... && node --check ui/automation-production/app.js && bash -n deployment/deploy_ec2.sh && git diff --check",
          "details": "Python/JavaScript 编译、UI node --check、deploy shell syntax、Compose YAML 静态资源身份解析和 diff whitespace 校验通过；deploy_ec2 fake-command 回归 18 项通过。"
        },
        {
          "type": "decision",
          "label": "Docker/EC2 runtime verification pending",
          "command": "docker compose config/build/up；Nginx runtime health；Zendesk remote readback；rollback drill",
          "details": "当前工作机没有 Docker CLI，仅有 docker-compose 兼容命令；六镜像 build/up、Production filesystem inspect、Nginx runtime、Zendesk remote readback 与 rollback drill 必须在 Docker-capable host/EC2 执行，不能由本地静态检查替代。"
        },
        {
          "type": "test",
          "label": "Per-operation delivery ledger and staging Zendesk deny boundary",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_delivery_ledger backend.tests.test_automation_delivery_reconciliation backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract",
          "details": "delivery ledger/reconciliation 相关测试通过；execution 记录 ownership/comment/status 的稳定 delivery key、attempt、ticket/status 绑定和 completed/outcome_unknown 状态，并验证 staging Zendesk client boundary 显式拒绝出站；reconcile 必须由服务端 Zendesk readback 产生证据。"
        },
        {
          "type": "test",
          "label": "Release builder and manifest promotion contract",
          "command": ".venv/bin/python -m unittest backend.tests.test_build_automation_release backend.tests.test_deploy_ec2",
          "details": "fake Docker 回归通过：release builder 在本地构建 route/automation/production 三种 role，生成本地 tag、六个 image pointer 和 image ID；split deploy 校验本地 image ID、跳过 compose pull，image 缺失/不匹配或 manifest 缺失时在 network/compose 变更前 fail closed；旧 digest 迁移和 rollback 契约仍通过。"
        },
        {
          "type": "test",
          "label": "Existing database DSN fallback and split provenance",
          "command": ".venv/bin/python -m unittest backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_split_deploy_loads_release_manifest_without_manual_image_variables backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_preproduction_reuses_account_database_with_environment_defaults backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_production_reuses_production_database_with_environment_defaults",
          "details": "验证 staging/preproduction 缺少 AUTOMATION_*_DB_DSN 时复用 TICKET_DB_DSN，production 复用 PRODUCTION_TICKET_DB_DSN；三环境默认 schema/queue/event identity 由部署进程导出，release manifest commit/build_time 映射到 split route/automation build markers。"
        },
        {
          "type": "test",
          "label": "Environment-specific automation execution tables",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_execution_store backend.tests.test_automation_contracts",
          "details": "验证 execution store 使用 schema-qualified 的 automation_executions_staging、automation_executions_preproduction、automation_executions_production 三张表，表名非法或未按环境绑定时 fail closed。"
        },
        {
          "type": "test",
          "label": "Split nginx automation edge attachment",
          "command": ".venv/bin/python -m unittest backend.tests.test_deploy_ec2 backend.tests.test_split_environment_deployment",
          "details": "验证 split deploy 在启动环境服务前将正在运行的官方 nginx 幂等接入 supportportal_automation_edge，不重建 nginx；nginx 不存在时 fail closed，避免容器 health 正常但外部路径因 Docker DNS 不可达而持续 502。"
        },
        {
          "type": "test",
          "label": "Acceptance remediation: outbound networks, execution tokens, unknown write paths",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_deploy_ec2 backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_route_service_contract backend.tests.test_automation_contracts backend.tests.test_account_automation_ownership backend.tests.test_automation_execution_store backend.tests.test_build_automation_release backend.tests.test_automation_delivery_ledger backend.tests.test_automation_delivery_reconciliation",
          "details": "2026-08-22 线上验收发现 staging 执行链 502 route_http_error：route 容器只挂在 --internal 网络上无法出站解析 LLM API。修复为 automation 网络不再 --internal 创建、既存 internal 网络 fail closed 并要求人工迁移；同时 /v1/cases、rerun、reset、reconcile 增加 AUTOMATION_EXECUTION_TOKEN Bearer 鉴权（三 UI 提供 token 输入）、production 未知写路径返回 404、deploy 校验三个 AUTOMATION_*_EXECUTION_TOKEN 必填。116 项相关测试通过。"
        },
        {
          "type": "deployment",
          "label": "Release-005 three-environment deployment and acceptance probes",
          "command": "EC2 deploy_ec2.sh --release release-20260822-005（staging -> preproduction -> production）；curl /automation/*/health|capabilities|v1/cases",
          "details": "2026-08-22 三环境迁移非 internal 网络并部署 release-20260822-005：三环境 health 200、capabilities 策略矩阵与设计一致；staging 带 token 探针 9 秒返回 prepared 并落库 automation_executions_staging（此前 502 route_http_error 已消除）；空 body 无/错 token 均 401（鉴权先于请求体校验）；production POST /v1/reruns 404；staging 容器无 Zendesk 凭据；旧 /account、/production 仍 200；PREPRODUCTION/PRODUCTION_ZENDESK_SIDE_EFFECTS_ENABLED=1、TARGET_TICKET_STATUS=pending 已生效。preproduction allowlist 工单 12872/12895 验证 quota/unregistered/enablement-字段不足/suspension-prepared 各路由与 human_review 无副作用落库，suspension prepared 链路执行到 side-effect 调用并以 failed+pending ledger 正确落库。"
        },
        {
          "type": "deployment",
          "label": "Three-environment rollback drill",
          "command": "EC2 deploy_ec2.sh --environment {staging,preproduction,production} --rollback 后重新 --release release-20260822-005",
          "details": "2026-08-22 三环境各执行 rollback（staging/production 回退 release-20260822-004，preproduction 回退同版 previous）并恢复 release-20260822-005；全程 health 200，manifest current/previous 指针正确交替，回滚只影响目标 compose project。"
        },
        {
          "type": "decision",
          "label": "Zendesk credentials 401 blocks real side-effect acceptance",
          "command": "容器内 GET agoraio.zendesk.com/api/v2/tickets/12895.json",
          "details": "preproduction 与主栈 api_production 容器使用 .env 的 zendesk_basic_auth 均 401；该值不含冒号（非 email:token 格式），疑似 Zendesk token 轮换后未更新 EC2 .env。真实 Zendesk 写入验收（preproduction internal 全链路、production internal/external 与 readback）与主栈 /production 自动投递均被阻塞，等待运维更新凭据。"
        },
        {
          "type": "deployment",
          "label": "Zendesk credential resolved, verification probes all green",
          "command": "EC2 deploy_ec2.sh --release release-20260822-005（preproduction/production recreate）+ ./deployment/verify_split_environments.sh",
          "details": "2026-08-23 运维更新 EC2 .env 的 zendesk_basic_auth 并 recreate preproduction/production 容器后，verify_split_environments.sh 36/36 全部通过（三环境 health/capabilities/鉴权/404/容器不变量/网络/route 出站/Zendesk 凭据只读 GET/旧端点）。真实工单写入验收按用户指示暂缓，不动真实工单。"
        },
        {
          "type": "deployment",
          "label": "Local podman split environment startup",
          "command": "scripts/workflow/start_local_split_environments.sh [--skip-build]",
          "details": "新增本地（podman）三环境启动脚本：从当前工作树构建三个 role 镜像（worktree 可验证未提交改动，脏树 tag 带 -wip）、幂等建网络、自动生成三个执行 token 写入 root .env、按 EC2 同名 project 启动三环境并验证 health 与 401 负例；本地 Zendesk 副作用默认关闭 fail-closed，PRODUCTION_TICKET_DB_DSN 缺失时跳过本地 production。配套文档见 docs/deploy_automation_release.md 第 6 节。"
        },
        {
          "type": "document",
          "label": "T4 n8n cutover design (company-ID canary + unified token)",
          "command": "docs/integrations/n8n/automation_environments_cutover.md",
          "details": "2026-08-23 产出 T4 方案先行设计：确认 /automation/{env}/v1/cases 新工单投递端点已存在且已验证，评论/状态同步在新环境无等价端点、保持旧端点；production 采用克隆工作流 + TARGET_COMPANY_IDS 互斥名单灰度分流（零双写、可回滚）；token 统一为同一密钥值贯穿 AUTOMATION_{三环境}_EXECUTION_TOKEN、ZENDESK_ACCOUNT_SYNC_TOKEN、n8n_request_token（旧同步端点已支持 Bearer 回退，backend/main.py require_zendesk_account_sync_token），n8n 单个 Bearer 凭据覆盖全部入向调用。含 EC2/n8n 操作 runbook、双写防护红线与验证清单；实施待 T3 完成与用户批准。"
        },
        {
          "type": "document",
          "label": "Report v2 refresh with cutover direction",
          "command": "docs/split_environments_report.md",
          "details": "2026-08-24 按用户决策将报告刷新为 v2：新增第 0 节总目标（三环境上线并完全替代旧 /account 与 /production；preproduction 与 production 配置统一、进入 case 由 n8n 控制、production 最后切流）；修正第 1 节过时内容（鉴权已统一为单一 X-N8n-Request-Token/n8n_request_token，旧 Bearer 与三个 AUTOMATION_*_EXECUTION_TOKEN 已废弃；allowlist 三态含 * 放行）；T1/T6 标完成（p2-89），T2/T5 标未承接并降级（T5 探针半边放弃、被 /automation/test 回归体系超越），T3/T4 剩余并入新包；新增 T7（preproduction 配置统一 + n8n 筛选流量影子验收）与 T8（production 最终切流与旧端点下线，以 automation_environments_cutover.md 为权威操作手册）。纯文档刷新，无运行时变更。"
        },
        {
          "type": "deployment",
          "label": "Automation production blue-green deployment implementation",
          "command": ".venv/bin/python -m unittest backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose && bash -n deployment/deploy_automation_production_blue_green.sh",
          "details": "新增专用蓝绿入口：candidate 使用 release 唯一服务名和生产 DB/Redis identity，readiness 通过后以 Nginx runtime include 原子切换并 graceful reload；/automation/production/ 禁止 upstream 自动重试，旧 compose project 默认排空 360 秒后停止，--rollback 只切换 upstream、不重放请求。当前本机缺少可用 Docker CLI/.env 完整必填变量，EC2 栈验证待执行。"
        },
        {
          "type": "deployment",
          "label": "EC2 review remediation",
          "command": ".venv/bin/python -m unittest backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose && bash -n deployment/deploy_automation_production_blue_green.sh",
          "details": "修复 EC2 review 发现的 release manifest 未注入、候选 Redis 重复创建、drain 后 rollback 指针失效、切流健康检查失败不恢复、缺部署锁、Nginx optional upstream 破坏和旧 Nginx runtime mount 缺失：manifest 校验本地 image ID；candidate 直接复用 external production Redis；旧服务只 stop 且持久化 override；失败自动恢复 upstream；共享 .deploy_ec2.lock；Nginx 使用 server scope variable；首次切换前自动补齐 runtime mount。Docker/EC2 演练仍待执行。"
        },
        {
          "type": "test",
          "label": "Blue-green schema and worker readiness remediation",
          "command": ".venv/bin/python -m unittest backend.tests.test_production_blue_green_behavior backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_deploy_ec2 && bash -n deployment/{deploy_automation_production_blue_green,bootstrap_automation_production_schema,deploy_ec2,verify_split_environments}.sh",
          "details": "75 项部署回归通过。蓝绿顺序收紧为 schema bootstrap -> candidate readiness -> parity worker recreate -> worker stability -> Nginx cutover -> state commit -> drain；worker 注入必填 PGVECTOR_DSN，重启/退出时在切流前失败并停止 candidate。verify_split_environments.sh 按 active upstream 的 Compose service label 识别 candidate，双采样同一 worker 的 running/status/RestartCount，移除硬编码容器名和 grep|head pipefail。EC2 数据库 bootstrap、容器重建和异步回复 readback 尚未执行。"
        },
        {
          "type": "test",
          "label": "Split runtime query/rerun/reset contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_execution_store backend.tests.test_automation_contracts backend.tests.test_automation_delivery_ledger backend.tests.test_automation_delivery_reconciliation backend.tests.test_route_service_contract backend.tests.test_split_environment_deployment backend.tests.test_build_automation_release backend.tests.test_deploy_ec2 backend.tests.test_single_host_compose backend.tests.test_account_automation_ownership",
          "details": "122 项相关测试通过。新增覆盖：GET /v1/executions 无 token 401、分页/status 过滤/case 查找/status_counts 同快照、execution payload 持久化原始 request 字段；GET /v1/executions/{id} 401/404/200；rerun 创建链式新 execution（新 request_id、rerun_of_execution_id 可追溯、原记录不可变、旧记录 422 execution_request_not_persisted、case 不匹配 404）；staging reset 清空并返回 deleted_count、preproduction reset 404；production runtime 具备列表/详情端点且 OpenAPI 仍无 rerun/reset；production UI bundle 物理不含 rerun 字符串。"
        },
        {
          "type": "test",
          "label": "Static syntax checks for the three console UIs",
          "command": "node --check ui/automation-staging/app.js && node --check ui/automation-preproduction/app.js && node --check ui/automation-production/app.js",
          "details": "三份 app.js 通过 node --check；staging/preproduction 主体逐字节一致（仅 ENV 常量块不同），production 变体由同一源生成并剥离 rerun 代码块（含 ENV 键与文案），满足镜像物理排除契约的 UI 侧约束。"
        },
        {
          "type": "deployment",
          "label": "Release-20260823-001..004 three-environment rollout",
          "command": "EC2 build_automation_release.sh + deploy_ec2.sh（staging -> preproduction -> production，DEPLOY_PRODUCTION_APPROVED=1）+ verify_split_environments.sh",
          "details": "2026-08-23 依次部署 release-001/002/003/004：-001 首次上线新控制台与查询/rerun/reset 端点；-002 因 EC2 构建时 origin/main 引用未刷新（stale ref，manifest commit=47a0c9d）实际仍为旧代码，改为先 git fetch 再构建；-003 修复 token 门委托 submit 取 currentTarget 导致 FormData(div) 抛错的缺陷（manifest commit=d668302）；-004 修复模板字符串内哨兵标记渲染为可见文本的缺陷并升静态版本 v3（manifest commit=d3e1941）。每轮部署前后 verify_split_environments.sh 全部通过。"
        },
        {
          "type": "test",
          "label": "Live browser acceptance of the three consoles",
          "command": "浏览器实测 http://ec2-52-71-106-188.compute-1.amazonaws.com:8080/automation/{staging,preproduction,production}/",
          "details": "staging：token 门 -> 工作台（6 条历史、状态过滤计数 All6/Prepared5/Failed1、Case 搜索、分页）-> 详情（meta 网格、Rerun of 链路回溯、请求区、问答时间线、折叠 raw JSON）-> rerun 确认弹窗（冻结 Case ID）-> 状态过滤 -> reset 确认弹窗与执行（toast 6 executions deleted、列表清空）；API 实测 POST /v1/cases 落库含 request 字段、POST /v1/reruns 产生链式新 execution、GET 列表/详情 401/200。preproduction：ticket 必填、锁定 internal、无 reset、能力行 rerun enabled/reset disabled/visibility internal、4 条历史。production：visibility 下拉 internal/external、无 rerun/reset、空态、production rerun 404、bundle 无 rerun 字符串。未做任何真实 Zendesk 写入（T3 按用户指示暂缓，12895 未动）。"
        },
        {
          "type": "test",
          "label": "Admin login contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_contracts backend.tests.test_split_environment_deployment",
          "details": "32 项测试通过。新增覆盖：POST /v1/auth/login 无需 Bearer、admin/admin 成功返回本环境 execution token、错误用户名/密码 401、缺字段 422、执行查询端点仍 401；AUTOMATION_ADMIN_USERNAME/PASSWORD 覆盖默认凭据后 admin/admin 拒绝、覆盖凭据通过；production runtime 同契约；production UI bundle 仍无 rerun 字符串。三份 app.js node --check 通过、staging/preproduction 主体一致。"
        },
        {
          "type": "deployment",
          "label": "Release-20260823-005 rollout and live login verification",
          "command": "EC2 build（manifest commit=8307746）+ deploy staging -> preproduction -> production + verify_split_environments.sh + curl /v1/auth/login + 浏览器实测",
          "details": "2026-08-23 部署 release-20260823-005（构建前 git fetch 核对 commit）：verify 探针全绿；三环境 POST /v1/auth/login 实测 admin/admin=200、错误密码=401；浏览器实测登录页与 /workspace/admin 同构（Welcome Back/Email/Password/Sign In/ac_unit 品牌），hostname 会话自动进入工作台且侧栏显示 admin 会话卡与 Sign out。本会话 IAB 浏览器事件通道后期故障（已知可用页面的 chip 点击亦失效，fill/快照正常），Sign In 的真实鼠标点击未能在本会话完成——该代码路径与 release-003 实测可用的委托 submit 修复路径一致，端点与渲染均已线上验证。"
        },
        {
          "type": "test",
          "label": "Unified auth targeted regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_zendesk_status_sync backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_deploy_ec2 backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_build_automation_release backend.tests.test_route_service_contract",
          "details": "2026-08-23 前六套件 76 项：失败 7 项与干净 main 同命令基线完全一致（test_deploy_ec2 的 DSN/顺序耦合 6 项 + test_account_zendesk_status_sync 硬编码日期断言 1 项，均为存量、非本任务引入，已记入 p2-88 history）；新增失败 0。后三套件 35 项全绿。py_compile 三个后端文件、node --check 三份 app.js、bash -n 三个部署/工作流脚本、git diff --check 均通过。"
        },
        {
          "type": "decision",
          "label": "Unified token mechanism choice",
          "command": "用户决策（对话确认）",
          "details": "用户选定：统一使用 X-N8n-Request-Token、别的机制都不接受；automation 环境值来源直接读 n8n_request_token（单变量贯穿，含 compose/deploy/本地脚本契约变更），而非保留三个 AUTOMATION_*_EXECUTION_TOKEN 同值。"
        },
        {
          "type": "test",
          "label": "Route filter and sidebar rerun contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_execution_store backend.tests.test_automation_contracts backend.tests.test_split_environment_deployment && node --check ui/automation-{staging,preproduction,production}/app.js",
          "details": "33 项测试通过。新增覆盖：GET /v1/executions 的 route_category/route_subcategory 过滤、route_counts 与选中 category 的 route_subcategory_counts 同快照返回；production runtime 同参数透传。三份 app.js node --check 通过、staging/preproduction 主体一致、production bundle 无 rerun 字符串（全量 rerun 代码全部位于剥离块内，中性变量名 bulkActionButtonHtml/bulkStatusHtml）。"
        },
        {
          "type": "deployment",
          "label": "Release-006 rollout and route-field fix handling",
          "command": "EC2 build/deploy release-20260823-006（commit=05591ab）+ verify + 探针；修复 PR#868 后构建 release-20260823-007（commit=0a079b2，未部署）",
          "details": "2026-08-23 部署 release-006：verify 全绿；admin/admin 登录 200、旧 Bearer 头 401、新 X-N8n-Request-Token 200（含并行 #866 鉴权变更上线）、production rerun 404、UI v5。线上探针发现 route_counts 全落 uncategorized（真实 router payload 用 scope_label/execution_action 而非 category/subcategory），PR#868 修复（SQL 与 Python helper 对齐 UI 徽标的 fallback 语义，测试补 scope_label 形态）并构建 release-007 镜像；随后用户指示改动不再部署 EC2、改为本地验证，007 保持未部署。"
        },
        {
          "type": "test",
          "label": "Legacy intake compat regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_contracts backend.tests.test_split_environment_deployment",
          "details": "2026-08-23 新增 6 用例：staging 表单五字段投递推导（request_id=n8n-zd-12999/case_id=AC-12999/zendesk_ticket_id 来自 source/subject 来自 title）、同表单重放 idempotent_replay=true、JSON 旧字段名映射 + 未知字段 422、无 request_id/source 生成标识、production 表单缺 comment_visibility 422、production 表单带 internal 通过并完成映射。含存量共 36 项全部通过。"
        },
        {
          "type": "decision",
          "label": "Body compatibility decision",
          "command": "用户决策（对话确认）",
          "details": "用户两次追问 body 差异后明确要求\"就按照旧的/account的来\"。实现取舍：复用旧 intake 的 source→ticket 正则与 AC-{id}/幂等推导语义；唯一不妥协项=production comment_visibility 显式强制（p2-88 验收标准，防服务端静默默认客户可见性）。"
        },
        {
          "type": "test",
          "label": "Dual-format credential regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_zendesk_basic_auth_header backend.tests.test_zendesk_comments backend.tests.test_zendesk_public_comment backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_automation_ownership backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_zendesk_status_sync",
          "details": "2026-08-23 新增 test_zendesk_basic_auth_header 7 用例（裸值/base64/Basic 前缀/缺值/三类 invalid）；与既有 zendesk_comments、public_comment、ticket_assignment、ownership、comment_sync、status_sync 套件共 96 项，除已登记的存量失败 test_status_flows_to_summary_and_detail_payloads（硬编码日期断言，p2-88 history 在案）外全部通过。bash -n verify 脚本通过。"
        },
        {
          "type": "decision",
          "label": "Tolerant parsing instead of env-only fix",
          "command": "线上证据三角定位：production 502 zendesk_basic_auth_invalid（代码侧 base64 解码失败）+ verify 探针 33/33 绿（探针按裸值编码）→ .env 实为裸值、两消费者格式期望相反。",
          "details": "选择代码兼容而非只改 .env：线上裸值已是既成部署状态，且探针与代码期望相反会在任一单向修复后留下误报/隐患；':' 判据在两种格式间无歧义（base64 字母表不含 ':'），兼容分支是封闭的两态判定而非开放回退。"
        },
        {
          "type": "test",
          "label": "Allowlist opt-out regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_contracts backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract",
          "details": "新增 2 用例：* 放行任意工单且 visibility 强制 internal 不变；空 allowlist 保持拒绝全部。与既有 contracts/runtime/production 套件全部通过。"
        }
      ],
      "source_refs": [
        "docs/feature_list.md",
        "deployment/docker-compose.single-host.yml",
        "deployment/nginx/supportportal.conf"
      ],
      "legacy_ids": [],
      "status": "active",
      "task_count": 22,
      "done_count": 8,
      "blocked_count": 0
    },
    {
      "schema_version": 2,
      "function_id": "production-regression-testing",
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "title": "Production 工单回归测试",
      "goal": "提供 /automation/test 控制台：复用 workspace 登录，按分类（fraud_account / enablement / account_suspension）一键通过专用测试邮箱向 support@agoraio.zendesk.com 发送可编辑的测试工单邮件，并单独建表追踪测试工单与其在 production 管线的实时状态，用于大改动后的生产回归验证。",
      "acceptance_criteria": [],
      "evidence": [
        {
          "type": "test",
          "label": "Lazy-schema regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_scenarios backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "新增 2 用例：SpyStore 断言 ticket/run 两 store 的 get/list 读路径都触发 ensure_schema；41 用例全过。"
        },
        {
          "type": "test",
          "label": "Reproduced then fixed on live container",
          "command": "podman exec deployment_api_1 python - … GET /api/automation-test/scenarios",
          "details": "修复前本地官方栈（staging 库无表）登录后 GET scenarios 500（psycopg UndefinedTable: automation_test_scenario_runs）。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_scenarios backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "41 用例全过（含 p2-102 的读路径 ensure 回归）。"
        },
        {
          "type": "test",
          "label": "Dual-DB migration executed",
          "command": "psycopg execute backend/sql/migrations/2026_08_23_automation_test_console.sql via TICKET_DB_MIGRATION_DSN (staging) and same master creds on /supportportal_production",
          "details": "staging 与 production 两库均输出 migration applied；随后容器内 GET /api/automation-test/scenarios 复验（部署 p2-103 镜像后）。"
        },
        {
          "type": "test",
          "label": "Console API + UI contract + prefix-safety",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "18 用例全过：未登录 401；templates 返回带 [zac test] 前缀的三类模板与邮箱配置状态；未知类目 422；发送成功落 sent、失败/未配置落 failed+原因且 502 不重试；refresh 按 production case 关联并快照（zendesk 链接/internal email/reply job intent），无匹配 not_found、失败发送不关联、未知 id 404；[zac test] 前缀不破坏 enablement 确定性检测；UI 契约（挂载/nginx 指向 api_production/版本戳/workspace 登录经 /production/api）。"
        },
        {
          "type": "test",
          "label": "Static page smoke via TestClient",
          "command": ".venv/bin/python -c \"from fastapi.testclient import TestClient; import backend.main as main; r=TestClient(main.app).get('/automation/test/')\"",
          "details": "GET /automation/test/ 200，含 \u003ctitle>Automation Test\u003c/title>，Cache-Control private no-store；app.js 静态资源 200。既有套件对照：test_account_zendesk_status_sync 与 test_production_ui_contract 各 1 个失败在干净 main 上同样失败（日期敏感/部署脚本断言，与本任务无关）。"
        },
        {
          "type": "test",
          "label": "SMTP transport unit tests",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "新增 5 用例：缺 host/username/password fail-closed（configured=false+缺失键清单+AutomationTestMailError）；SMTP_SSL+login+send_message 成功（From/To/Subject 头与超时/端口断言）；context 默认 sender=SMTP_USERNAME；发送失败原因包裹进异常；非法 transport 拒绝。与 p2-97 既有 18 用例（graph 默认路径）合计 23 个全过。"
        }
      ],
      "source_refs": [
        "ui/automation-test",
        "backend/services/automation_test_store.py",
        "backend/services/automation_test_mail.py",
        "backend/services/automation_test_templates.py",
        "deployment/nginx/supportportal.conf",
        "docs/testing/production_ticket_regression_runbook.md"
      ],
      "legacy_ids": [],
      "status": "done",
      "task_count": 4,
      "done_count": 4,
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
      "evidence": [
        {
          "type": "test",
          "summary": "Latest-main regression passed with direct Slack sender, event-backed binding, fail-closed worker, resolver API, inbound-only n8n workflows, intake, Workspace, Zendesk delivery, repository and deployment coverage: 583 tests and 37 subtests.",
          "ref": "backend/tests/test_engineer_slack.py, backend/tests/test_engineer_slack_workflows.py, backend/tests/test_investigation_flow.py, backend/tests/test_account_intake.py, backend/tests/test_account_zendesk_comment_sync.py, backend/tests/test_worker.py"
        },
        {
          "type": "document",
          "summary": "Retained only redacted app-mention/interaction n8n exports and inbound ledger SQL; SupportPortal owns direct outbound and durable thread bindings.",
          "ref": "docs/integrations/n8n/"
        },
        {
          "type": "decision",
          "summary": "Approved Slack Team/Channel and temporary User OAuth outbound identity are known; live acceptance remains pending deployment, n8n inbound activation and a new Production Zendesk test ticket. No secret is tracked.",
          "ref": "docs/integrations/n8n/engineer_case_slack_runbook.md"
        },
        {
          "type": "test",
          "summary": "Production not_automated intake creates one active Engineer shell, one round-robin dispatch and one root outbox event; staging and excluded routes remain unchanged.",
          "ref": "backend/tests/test_account_intake.py"
        },
        {
          "type": "test",
          "summary": "Latest-main intake, Engineer flow, repository, in-memory/PostgreSQL comment-sync, worker and runtime contract regression passed: 583 tests and 37 subtests with ENGINEER_MULTI_AGENT_ENABLED=true.",
          "ref": "backend/tests/test_investigation_flow.py, backend/tests/test_account_zendesk_comment_sync.py, backend/tests/test_worker.py"
        },
        {
          "type": "decision",
          "summary": "Ticket 12967 已是既有 Account Case 且没有 Engineer Case，不适合验证新 intake；需在 direct Slack 配置 ready 后创建新的 Production 测试工单。",
          "ref": "docs/integrations/n8n/engineer_case_slack_runbook.md"
        },
        {
          "type": "test",
          "label": "Account handoff Slack outbox, n8n and Zendesk independence suite",
          "command": "TICKET_DB_DSN=postgresql://example.invalid/test SENTIMENT_PROVIDER=legacy .venv/bin/python -m pytest -q backend/tests/test_account_slack_n8n.py backend/tests/test_runtime_bootstrap.py backend/tests/test_repository_configuration.py backend/tests/test_account_zendesk_internal_comment_service.py backend/tests/test_worker.py",
          "details": "239 tests passed with 22 subtests after owner review; verified exact message and POST/GET contracts, case-action plus reply-intent trigger matrix, public-delivered release gate, private/failed/unknown non-release, concurrent claim deduplication, unknown-outcome status-only reconciliation, missing-only requeue, Slack failure independence, and X-N8n-Request-Token header propagation from n8n_request_token."
        },
        {
          "type": "deployment",
          "label": "Production EC2 deployment and runtime configuration",
          "command": "ssh zacbot 'cd /home/ubuntu/SupportPortal && ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space'",
          "details": "Deployment completed successfully. Public /health returned HTTP 200 with app_build.ref=2166840d5e90 and runtime_profile=full. api_production and worker_aux_production run localhost/supportportal-app:2166840d5e90; both have n8n_request_token, ACCOUNT_SLACK_N8N_WEBHOOK_URL, and ACCOUNT_SLACK_N8N_STATUS_URL set without exposing values."
        },
        {
          "type": "test",
          "label": "Production n8n client synthetic delivery and replay",
          "command": "docker exec deployment-worker_aux_production-1 python - (synthetic event; executed on zacbot)",
          "details": "The formal Production worker client sent a synthetic fraud_handoff_confirmation event and received delivered; replaying the identical event_id also returned delivered, confirming the authenticated POST path and n8n event-idempotent replay response. GET status remains unavailable because the production status webhook is not registered."
        }
      ],
      "source_refs": [
        "docs/roadmap.html#lanes",
        "backend/services/account_slack_n8n.py",
        "docs/integrations/n8n/account_automation_slack_notification.md"
      ],
      "legacy_ids": [],
      "status": "active",
      "task_count": 8,
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
        },
        {
          "type": "test",
          "label": "Registered Enablement deterministic routing regression",
          "command": ".venv/bin/pytest -q backend/tests/test_enablement_automation.py backend/tests/test_account_route_pipeline.py backend/tests/test_account_intake.py",
          "result": "Included in the focused Account routing and ownership suite: 340 passed with 51 subtests passed. A separate 20-run direct check classified the case #12875 message shape as media_relay 20/20 times without calling the Agora Router model."
        }
      ],
      "source_refs": [
        "docs/roadmap/meetings.html#ticketing-system-2026-08-10",
        "backend/services/account_route_pipeline.py",
        "backend/services/enablement_automation.py",
        "backend/services/prompts/account_routing.py"
      ],
      "created_at": "2026-08-10",
      "updated_at": "2026-08-20",
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
        },
        {
          "at": "2026-08-20",
          "event": "reopened",
          "summary": "Production case #12875 与 #12874 客户正文相同，但 Agora Router 在 backend_operation 与 uncategorized 之间漂移；重新打开任务以增加已注册 Enablement 请求的确定性边界和回归覆盖。"
        },
        {
          "at": "2026-08-20",
          "event": "completed",
          "summary": "将明确的已注册 Media Relay 激活请求在 Agora Router 边界确定性归类为 backend_operation，同时保留技术咨询、故障诊断、价格问题、模糊请求和未注册功能的原有 LLM/人工边界。"
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
        "Account AI 或自动化处理在 OpenAI/API 不可用、重试 3 次仍失败、结构化输出耗尽、Persona/字段处理异常或内部处理链路失败时停止自动化，最多执行首次调用加 3 次重试；不使用备用 provider/model，不生成客户回复，Case 持久化为 human_review_required，取消 pending reply job，并向预设的项目负责人邮箱发送一次脱敏、incident 幂等的故障邮件；邮件投递失败可重试。",
        "fraud_account、enablement、detailed_invoice 和 account_suspension 的 Production fallback 通过共享 escalation service 写 Zendesk private internal note、释放 AI ownership 并 route back 到原始 queue；note/route 独立记录失败和 outcome_unknown，staging/preproduction 无 Zendesk side effect。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Account Human Review queue handoff regression",
          "command": "../../.venv/bin/python -m pytest backend/tests/test_account_human_review_escalation.py backend/tests/test_account_intake.py backend/tests/test_worker.py -q",
          "details": "283 passed；覆盖 Production private note + route back、staging 无出站、note/route 独立失败、审计幂等、非 numeric identity、outcome_unknown 不重试，以及四类 Account intake/reply worker fallback 的 human_review_required、not_automated 和 pending job cancellation。"
        },
        {
          "type": "test",
          "label": "Human Review queue mismatch reconciliation",
          "command": "../../.venv/bin/python -m pytest backend/tests/test_account_human_review_escalation.py backend/tests/test_account_reply_rag_fallback.py backend/tests/test_account_intake.py backend/tests/test_worker.py -q",
          "details": "297 passed；覆盖旧 worker manual_attention 漏接、Production bounded reconciliation、staging/no-side-effect、AI ownership guard 和 handoff 终态幂等。"
        },
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
      "updated_at": "2026-08-24",
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
        },
        {
          "at": "2026-08-24",
          "event": "account_human_review_queue_handoff",
          "summary": "四类 active Account automation fallback 统一进入 Human Review：Production 写 private internal note、route back 原始 Zendesk queue、释放 AI ownership、取消 pending reply jobs 并保留 alert/audit；staging/preproduction 仅本地状态。"
        },
        {
          "at": "2026-08-24",
          "event": "account_human_review_queue_mismatch_reconciliation",
          "summary": "修复 reply worker 直接写 manual_attention 的 prepare/publish 分支，并在 Account poller 增加 bounded Production reconciliation，处理 automation_status=human_review_required 且 route_status=automated、仍由 AI 持有的历史 case；queued/already_human_owned/outcome_unknown 不重复写 Zendesk。"
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
        },
        {
          "type": "test",
          "label": "Default-assignment and public-human-reply ownership regression",
          "command": ".venv/bin/pytest -q backend/tests/test_account_automation_ownership.py backend/tests/test_zendesk_ticket_assignment.py backend/tests/test_enablement_automation.py backend/tests/test_account_route_pipeline.py backend/tests/test_worker.py backend/tests/test_account_intake.py",
          "result": "340 passed with 51 subtests passed. Coverage includes default human assignment takeover before any public human reply, complete paginated comment history, customer and AI comment exclusions, unknown-author fail-closed behavior, AI group plus assignee transfer, safe-update conflict reconciliation including a concurrent human reply, post-takeover human reassignment, post-takeover public human reply, terminal worker delivery cancellation, and ownership event diagnostics."
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
        },
        {
          "at": "2026-08-20",
          "event": "reopened",
          "summary": "Production #12874/#12875 调查确认 Zendesk 默认真人 assignee 和自动 group/status 变化不代表真人已回复；重新打开任务以用完整公开评论历史判定人工接管，并让 AI assignment 同时写入目标 group 与 assignee。"
        },
        {
          "at": "2026-08-20",
          "event": "completed",
          "summary": "Production ownership 改为基于完整 Zendesk 公开评论历史：默认真人 assignee 在尚未公开回复时允许 AI 接管；真人公开回复、未知公开作者或 AI 接管后的真人改派均 fail closed。AI 接管使用 safe_update 原子写入 assignee 与目标 group，并记录失败类别、Zendesk 状态码和阻塞评论 ID。"
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
      "task_id": "p1-53",
      "title": "Automation 三环境迁移至 ECS",
      "status": "active",
      "owner": "zac",
      "summary": "将 Staging、Preproduction、Production 三环境全部迁移到 ECS Fargate：从干净 commit 构建可追溯的 route/API/worker 镜像并推送 ECR，按 Staging → Preproduction → Production 晋升受控 release；Production 使用相同 image digest 与 Prompt Release进行 ECS 蓝绿部署。EC2 直接下线三套 split runtime及公网路径，只保留主栈和独立的现有 /production；数据库与 Docker volumes 保留。正式 Case是否进入 ECS Production由 n8n workflow publish状态控制。",
      "next_action": "完成 EC2 split runtime下线与 main-only部署门禁后，确认 AWS Region、与现有 RDS 同 VPC 的 private/public subnet、Route53 hosted zone、三环境域名和 GitHub Actions OIDC权限；随后实现 ECR/ECS/Terraform发布链并先部署 ECS Staging。",
      "acceptance_criteria": [
        "ECS Staging使用独立 Service、Redis、数据库身份、凭据和入口接收 n8n测试 Case；Staging保留 rerun/reset且 Zendesk副作用关闭。",
        "release builder 从干净 commit 构建 linux/amd64 的 route、production-safe API、production-safe worker与所需 RAG镜像；安全镜像的文件系统、OpenAPI和 UI均不包含 rerun/reset，worker不再使用包含测试代码的完整 APP_RUNTIME_IMAGE。",
        "ECR repository启用 immutable tag与扫描；每个 release manifest持久化 commit、route/API/worker/RAG image digest和 prompt_release_id，Preproduction验收后 Production禁止重新 build或替换 digest。",
        "Terraform建立同一 Region/VPC内的三套 ECS Fargate环境、公开 ALB、ACM、ECR、分环境 ElastiCache、Secrets Manager、CloudWatch、EFS token cache与最小权限 IAM；运行任务位于 private subnet且无公网 IP，schema bootstrap使用不进入长期 task的独立 DDL凭据。",
        "Preproduction与 Production使用独立 ECS Service、RDS schema、Redis endpoint、queue/channel、Secrets、日志和入口；API task与同 release Route sidecar共同部署，worker具有可验证 heartbeat，Production API与 worker均具备无单点的最小健康副本数。",
        "Preproduction接收 n8n筛选的测试 Case，完成 intake、异步 reply、delivery ledger、Zendesk、邮件、Slack和外部 readback；只有上述证据与运行 provenance匹配时 release才可标记 approved_for_production。",
        "Production读取已批准的同一 manifest创建 green task set，依次通过 schema/Prompt同步、task健康、worker heartbeat、ALB target和 build provenance门禁后才开放 endpoint；后续发布由 ECS/CodeDeploy完成预热、流量切换、旧 task排空与失败回滚。",
        "EC2 的三套 split runtime、split网络与公网路径完成下线，常规和每日 EC2部署只管理主栈；历史数据库与 Docker volumes保留，现有 EC2 /production不切换或重启。",
        "首次 ECS Production上线不切换或重启旧 EC2 /production；新 endpoint健康后才 publish对应 n8n workflow，旧 workflow unpublish只停止新 Case进入，旧环境既有异步任务仍按独立排空流程处理。",
        "GitHub Actions使用 AWS OIDC而非长期 access key，发布与晋升命令可审计；CloudWatch对 ALB 5xx、task退出、worker heartbeat和部署失败提供告警。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "decision",
          "label": "ECS migration architecture agreed",
          "command": "Architecture discussion 2026-08-25",
          "details": "最初确定本地 Staging、ECS Preproduction/Production；2026-08-25 用户进一步确认 Staging也迁入 ECS。三环境均从 ECR晋升可追溯 release，首次 Production启用通过独立 n8n endpoint完成，不触碰当前 EC2 /production。"
        },
        {
          "type": "document",
          "label": "EC2 split decommission prepared",
          "command": "deployment and Nginx contract update 2026-08-25",
          "details": "EC2主 Nginx对三条 /automation/* 路径返回410，main与timer部署不再构建、部署或验证 split环境，也不再创建或连接 split网络；运行数据与 volumes明确保留。"
        },
        {
          "type": "test",
          "label": "EC2 retirement deployment contracts",
          "command": ".venv/bin/python -m unittest backend.tests.test_auto_deploy_ec2 backend.tests.test_split_environment_deployment backend.tests.test_deploy_ec2 backend.tests.test_single_host_compose",
          "details": "79项全绿：覆盖定时 wrapper main-only参数、surface脚本无 split build/deploy/verify、三条路径410、/automation/test继续代理 production API、main部署不创建或连接 split网络，以及既有 Compose/legacy rollback契约。"
        }
      ],
      "source_refs": [
        "backend/Dockerfile.automation",
        "deployment/docker-compose.single-host.yml",
        "deployment/build_automation_release.sh",
        "deployment/deploy_automation_production_blue_green.sh",
        "scripts/workflow/start_local_split_environments.sh",
        "docs/deploy_automation_release.md",
        "docs/integrations/n8n/automation_environments_cutover.md"
      ],
      "created_at": "2026-08-25",
      "updated_at": "2026-08-25",
      "history": [
        {
          "at": "2026-08-25",
          "event": "planned",
          "summary": "用户确认迁移目标：Staging仅本地测试并通过 ngrok接收 n8n请求；测试后构建 production-safe镜像上传 ECR；Preproduction以 n8n筛选的测试 Case完成最终验收；Production部署同一 release到 ECS并使用独立 endpoint，旧 EC2 /production保持不变。"
        },
        {
          "at": "2026-08-25",
          "event": "progress",
          "summary": "用户更新决策为 Staging、Preproduction、Production三环境全部部署到 ECS，并授权直接下线 EC2 split runtime；EC2保留主栈、现有 /production、数据库与历史 volumes。"
        }
      ],
      "legacy_refs": [
        "p2-88",
        "p2-108",
        "p2-115"
      ],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "platform-delivery",
      "function_id": "ecs-environment-migration"
    },
    {
      "schema_version": 2,
      "task_id": "p2-100",
      "title": "内部邮件 Billing 前缀修正与 Zendesk 评论代码块渲染",
      "status": "active",
      "owner": "zac",
      "summary": "两处修复：①suspension/verification/invoice 的内部邮件正文第二行硬编码 'Billing: ' 前缀，按 action 映射正确类型（Account Suspension/Account Verification/Detailed Invoice）；②Zendesk 评论中 Markdown 围栏代码块以纯文本字面反引号显示，add_ticket_comment 自动检测围栏代码块并生成 html_body（pre/code 标签），无代码块的评论不受影响。",
      "next_action": "部署 EC2 后在真实工单上验证两处修复效果。",
      "acceptance_criteria": [
        "suspension 内部邮件正文第二行显示 'Account Suspension: ...' 而非 'Billing: ...'。",
        "包含围栏代码块的 Zendesk 评论以 pre/code HTML 渲染（html_body 字段），无代码块的评论不变。",
        "审计对账仍按纯文本 body 匹配，不受 html_body 影响。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Email prefix mapping and fenced code HTML conversion",
          "command": ".venv/bin/python -m unittest backend.tests.test_email_prefix_and_codeblocks backend.tests.test_billing_automation_email backend.tests.test_internal_email_template backend.tests.test_zendesk_comments backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_reply_rag_fallback",
          "details": "新增 7 项单测（request_type 按 action 映射、suspension 邮件正文不再含 Billing:、围栏代码块转 pre/code HTML、HTML 转义、多代码块、纯代码体、无代码块返回 None）；70 项相关回归全绿。"
        }
      ],
      "source_refs": [
        "backend/services/billing_automation.py",
        "backend/services/zendesk_comments.py",
        "backend/tests/test_email_prefix_and_codeblocks.py"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "由 12953 suspension 邮件缩略图 Billing 问题和 12951 RAG 答案代码块渲染问题创建。"
        },
        {
          "at": "2026-08-24",
          "event": "email_cc_and_recipient_routing",
          "summary": "所有自动化内部邮件统一 cc xieziling@agora.io（AUTOMATION_INTERNAL_EMAIL_CC env 可覆盖）；EC2 .env 变更：enablement→zhonghuang、suspension+verification→suhrid.das、fraud assignee→suhrid.das (31116644140308)。"
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
      "task_id": "p2-101",
      "title": "Fraud Account 字段改为 7 项 + Slack/邮件显示字段",
      "status": "done",
      "owner": "zac",
      "summary": "fraud_account（account_verification）的字段提取从 4 组（company/contact/use_case/payment）改为 7 项独立字段（account_type/name/office_address/contact_number/contact_email/use_case_description/console_configuration）。内部邮件和 Slack 消息增加已收集与缺失字段的展示。",
      "next_action": "无（fraud-account-fields-v4 已合并并完成定向回归、官方单机栈和 Prompt runtime 验证）。",
      "acceptance_criteria": [
        "客户收到的追问涵盖 7 项信息（account type、name、office address、contact number、contact email、use-case description、console configuration）。",
        "内部邮件 Provided information 按新字段标签列出已收集值，Missing after one follow-up 列出缺失项。",
        "Slack 消息增加 Provided: 和 Missing: 行显示字段。",
        "客户追问缺失字段时，1-2 项以内嵌入自然句子，3 项及以上每项独立使用 bullet；不合规 Persona 输出不得发布。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "7-field extraction + Slack fields + intake regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_account_verification_automation backend.tests.test_account_slack_n8n backend.tests.test_account_intake",
          "details": "202 项测试全绿；覆盖 7 字段提取/grounding/追问覆盖验证、内部邮件 Provided+Missing 新标签、Slack 消息含 Provided/Missing 行、intake 全链路回归（fixture 已迁移到 7 键）。"
        },
        {
          "type": "test",
          "label": "Persona v12 missing-information layout and tone contract",
          "command": ".venv/bin/python -m pytest -q backend/tests/test_automation_persona.py backend/tests/test_account_ai_execution.py",
          "details": "38 项测试通过、13 个子测试通过；覆盖 Fraud Account 1-2 项 inline、3+ 项 bullet、编号列表拒绝、Persona v12 prompt wording，以及现有四次 Account AI 校验预算。"
        },
        {
          "type": "test",
          "label": "Fraud Account/Intake/Worker regression",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy OPENAI_API_KEY= .venv/bin/python -m pytest -q backend/tests/test_account_verification_automation.py backend/tests/test_account_slack_n8n.py backend/tests/test_account_intake.py backend/tests/test_worker.py backend/tests/test_account_reply_version_fence.py",
          "details": "318 项测试通过、33 个子测试通过；覆盖 7 字段提取、Slack/邮件摘要、Intake 追问与第二次回复、Persona Worker 发布和版本 fence。"
        },
        {
          "type": "deployment",
          "label": "Official single-host stack after merge",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh; curl -fsS http://127.0.0.1:8080/health; podman exec deployment_api_1 python -c \"from backend.services.automation_persona import AUTOMATION_PERSONA_PROMPT_VERSION, _assert_missing_information_format_contract; print({'persona_prompt_version': AUTOMATION_PERSONA_PROMPT_VERSION, 'format_contract': _assert_missing_information_format_contract.__name__})\"",
          "details": "官方项目 deployment；root_main_ref、official_image_tag、official_health_build_ref、official_runtime_build_ref 均为 d8a40785739f；health 返回 200/status=ok；runtime_profile=local_lightweight；auxiliary_stack_present=false；容器内 Persona marker 为 automation-persona-v12。"
        },
        {
          "type": "test",
          "label": "Fraud Account Prompt schema contract",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy OPENAI_API_KEY= .venv/bin/python -m pytest -q backend/tests/test_account_verification_automation.py backend/tests/test_agent_config.py backend/tests/test_account_intake.py backend/tests/test_automation_persona.py backend/tests/test_account_ai_execution.py",
          "details": "227 项测试通过、24 个子测试通过、4 个既有 FastAPI deprecation warnings；结构测试解析 managed Prompt 的 Output JSON，确保七个 canonical keys 存在且旧四字段及 contact_information 不存在。"
        },
        {
          "type": "deployment",
          "label": "Fraud Account Prompt v4 official runtime readback",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh; curl -fsS http://127.0.0.1:8080/health; podman exec deployment_api_1 python -c 'from backend.services.account_verification_field_extractor import ACCOUNT_VERIFICATION_REQUIRED_GROUPS; from backend.services.prompts.account_routing import build_account_verification_field_system_prompt, ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION; p=build_account_verification_field_system_prompt(); print({\"version\":ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION,\"canonical\":all((chr(34)+k+chr(34)) in p for k in ACCOUNT_VERIFICATION_REQUIRED_GROUPS),\"legacy_exact\":{k:(chr(34)+k+chr(34)) in p for k in (\"company_information\",\"contact_information\",\"use_case\",\"payment_information\")}})'",
          "details": "官方单机 local_lightweight 栈于 root_main_ref=23c19e3bd7d4 构建并通过 health=200；official_image_tag、health build ref、runtime build ref 均匹配；prompt_runtime release_id=code-8a779db0373b、status=loaded、prompt_count=28；api、rag_api、rag_worker、worker_query、worker_aux 日志均加载同一 code snapshot。容器内 Prompt 版本为 fraud-account-fields-v4，七个 canonical keys 全部存在，旧四字段均不存在。"
        },
        {
          "type": "test",
          "label": "Fraud Account v4 deployment gate",
          "command": ".venv/bin/python -m unittest backend.tests.test_prompt_versioning backend.tests.test_deploy_ec2",
          "details": "Prompt Release validate 在停旧栈前检查 v4 版本常量、Output JSON 精确七字段、legacy fields 缺失及候选内容与代码 SHA-256 一致；production sync 复用同一 validator。部署契约同时覆盖八个 runtime 的同镜像/build/release 门禁和 production active-release 回读。"
        }
      ],
      "source_refs": [
        "backend/services/account_verification_field_extractor.py",
        "backend/services/agent_config.py",
        "backend/services/account_verification_automation.py",
        "backend/services/prompts/account_routing.py",
        "backend/services/account_slack_n8n.py",
        "backend/services/automation_persona.py",
        "backend/tests/test_account_verification_automation.py",
        "backend/tests/test_agent_config.py",
        "backend/tests/test_account_slack_n8n.py",
        "backend/tests/test_account_intake.py",
        "backend/tests/test_automation_persona.py"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-25",
      "history": [
        {
          "at": "2026-08-25",
          "event": "fraud_v4_deployment_gate",
          "summary": "Prompt Release validate 在旧栈停止前强制校验 fraud-account-fields-v4、Output JSON 精确七字段、旧四字段缺失，以及候选 Prompt 内容 hash 与当前代码一致；同一门禁复用于 production Prompt sync。"
        },
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "按用户需求将 fraud 字段从 4 组改为 7 项独立字段，同步更新提取 prompt、邮件标签、Slack 模板。"
        },
        {
          "at": "2026-08-24",
          "event": "persona_missing_format",
          "summary": "persona 指令增加缺失信息格式规则（1-2 项入句、3+ 项编号列表），missing_information 在送 LLM 前转换为人类可读标签（Account type 而非 account_type）。"
        },
        {
          "at": "2026-08-24",
          "event": "persona_v12_missing_format_contract",
          "summary": "将 3+ 缺失信息从编号列表改为每项独立 bullet，收紧 1-2 项同句与 warm first-person 语气，并在现有四次 Account AI 预算内拒绝不合规格式。"
        },
        {
          "at": "2026-08-24",
          "event": "live_stack_verified",
          "summary": "合并后官方 deployment 单机 lightweight 栈健康检查和 build provenance 均匹配，Persona v12 marker 已在容器内确认，无辅助栈。"
        },
        {
          "at": "2026-08-24",
          "event": "fraud_account_prompt_schema_contract",
          "summary": "发现并修复 Fraud Account extractor required groups 与 managed Prompt Output 示例漂移；Prompt 升级为 fraud-account-fields-v4，增加结构一致性回归。"
        },
        {
          "at": "2026-08-24",
          "event": "fraud_account_prompt_schema_contract_verified",
          "summary": "合并后完成 227 项定向回归、Project Overview 校验、官方单机栈 build provenance 和 Prompt runtime readback；七字段 Prompt v4 已加载，未执行 AC-12974 重跑或 Zendesk 副作用。"
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
      "task_id": "p2-102",
      "title": "修复：automation test 两 store 读路径懒建表（全新库 GET 不再 500）",
      "status": "done",
      "owner": "zac",
      "summary": "p2-101 合并后活栈验证发现：automation_test_tickets 与 automation_test_scenario_runs 两张表只在写入路径懒建，全新数据库上 GET /api/automation-test/tickets 与 /scenarios 直接 500（UndefinedTable）。修复：ensure_schema 加进程级 _schema_ensured 幂等 flag，get/list 读方法入口先 ensure（每进程仅首连执行 DDL），内存模式语义不变。",
      "next_action": "无（随 p2-101 一并部署 EC2 后生效）。",
      "acceptance_criteria": [
        "全新数据库上 GET /api/automation-test/tickets 与 GET /api/automation-test/scenarios 返回 200 空列表而非 500。",
        "ensure_schema 每进程只执行一次 DDL（幂等 flag）；内存模式行为不变。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Lazy-schema regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_scenarios backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "新增 2 用例：SpyStore 断言 ticket/run 两 store 的 get/list 读路径都触发 ensure_schema；41 用例全过。"
        },
        {
          "type": "test",
          "label": "Reproduced then fixed on live container",
          "command": "podman exec deployment_api_1 python - … GET /api/automation-test/scenarios",
          "details": "修复前本地官方栈（staging 库无表）登录后 GET scenarios 500（psycopg UndefinedTable: automation_test_scenario_runs）。"
        }
      ],
      "source_refs": [
        "backend/services/automation_test_store.py",
        "backend/tests/test_automation_test_console.py"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "p2-101 合并后活栈 marker 验证暴露：本地 staging 库无 automation_test_scenario_runs 表，GET /scenarios 500；同病存在于 ticket store 的读路径。统一改读路径懒 ensure + 幂等 flag。"
        }
      ],
      "legacy_refs": [
        "p2-97",
        "p2-101"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "production-regression-testing"
    },
    {
      "schema_version": 2,
      "task_id": "p2-103",
      "title": "修复：automation test 建表走迁移 DSN + 双库迁移 SQL（runtime 角色无 CREATE 权限）",
      "status": "done",
      "owner": "zac",
      "summary": "p2-102 的读路径懒 ensure 在本地官方栈暴露第二层问题：runtime 角色（supportportal_runtime）对 supportportal schema 无 CREATE 权限，且 CREATE TABLE IF NOT EXISTS 对已存在表也先查权限——GET /scenarios 仍 500（InsufficientPrivilege）。修复三件套：① ensure_schema 先用 runtime DSN to_regclass 探测表存在即跳过 DDL；② 需要建表时 DDL 走迁移 DSN（AUTOMATION_TEST_MIGRATION_DSN/TICKET_DB_MIGRATION_DSN，且仅当其库名与 runtime DSN 一致才用，防 api_production 容器里误建到 staging 库）；③ 新增 backend/sql/migrations/2026_08_23_automation_test_console.sql（两张表+授权）并按仓库惯例手工双库执行（staging+production 均已应用）。",
      "next_action": "无（表已在双库存在，运行时 DDL 成为 no-op 兜底）。",
      "acceptance_criteria": [
        "表已存在时（迁移建好）读路径 ensure 直接跳过 DDL，GET /tickets 与 /scenarios 在无 CREATE 权限的 runtime 角色下返回 200。",
        "表不存在时 DDL 尝试走迁移 DSN（同库才用）；迁移 SQL 文件可重复执行（IF NOT EXISTS + 授权）。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Regression suites",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_scenarios backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "41 用例全过（含 p2-102 的读路径 ensure 回归）。"
        },
        {
          "type": "test",
          "label": "Dual-DB migration executed",
          "command": "psycopg execute backend/sql/migrations/2026_08_23_automation_test_console.sql via TICKET_DB_MIGRATION_DSN (staging) and same master creds on /supportportal_production",
          "details": "staging 与 production 两库均输出 migration applied；随后容器内 GET /api/automation-test/scenarios 复验（部署 p2-103 镜像后）。"
        }
      ],
      "source_refs": [
        "backend/services/automation_test_store.py",
        "backend/sql/migrations/2026_08_23_automation_test_console.sql"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "p2-102 部署后复验仍 500：runtime 角色无 schema CREATE 权限且 IF NOT EXISTS 也查权限；按仓库迁移惯例（migration DSN+手工双库）修复并执行迁移。"
        }
      ],
      "legacy_refs": [
        "p2-101",
        "p2-102"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "production-regression-testing"
    },
    {
      "schema_version": 2,
      "task_id": "p2-104",
      "title": "Detailed Invoice 保留分类并停用 Automation 执行",
      "status": "done",
      "owner": "zac",
      "summary": "按用户最新决策将 detailed_invoice 从 active Automation 移除，仅保留 Account & Billing / Detailed Invoice 分类。新 intake、Rerun 与人工 Route correction 均记录 classification-only / not_automated，不绑定 handler、不发内部邮件、不创建 Persona assignment 或 Automation reply job；历史 Detailed Invoice Case 也不再计入 Automated 视图。既有字段提取、内部邮件、PDF 资产、Zendesk 附件与完成回复实现代码和 handler registry 保留，供未来重新启用。fraud_account、enablement、account_suspension 的执行资格不变。",
      "next_action": "按仓库流程 finalize，并在官方 lightweight stack 验证 merged build；未来重新启用 Detailed Invoice Automation 时另开任务。",
      "acceptance_criteria": [
        "Detailed Invoice 仍分类为 Account & Billing / Detailed Invoice，并持久化 category=account_billing、subcategory=detailed_invoice。",
        "Detailed Invoice 路由为 not_automated / human_review / not_eligible，不绑定 automation_handler，不发送内部邮件，不创建 Persona assignment 或 Automation reply job。",
        "人工 Route correction 与历史 automated Detailed Invoice 行均不能重新激活 handler，且不再进入 Automation view/count；Account & Billing membership 保留。",
        "历史 Detailed Invoice 内部邮件回信在读取 linked ticket、保存附件或创建 completion reply job 前，以 inactive_automation 终止 claim。",
        "Agent Config 将 Detailed Invoice 展示为 classification-only outcome，并从 Automation Workflow catalog 移除。",
        "字段提取、内部邮件、PDF、Zendesk 附件及完成回复实现代码与 handler registry 保留，未删除以支持未来重新启用。",
        "fraud_account、enablement、account_suspension 的现有行为不变。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Changed-area unit suites",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_routing backend.tests.test_account_route_pipeline backend.tests.test_worker backend.tests.test_account_reply_version_fence backend.tests.test_zendesk_comments backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_intake backend.tests.test_automation_persona",
          "details": "全绿（新增 detailed_invoice 完成 job / Zendesk upload / 投递附件集成 / intent 契约用例；翻转 routing 断言）。test_agent_config、quota reroute、route_correction suspension、roadmap、filter-select 的失败在干净 main 上同样失败，为遗留问题非本任务引入。"
        },
        {
          "type": "test",
          "label": "Scenario driver smoke",
          "command": ".venv/bin/python scripts/testing/production_ticket_scenarios.py --list",
          "details": "--list 列出含 D1 的五剧本。生产实跑待用户上线后执行。"
        },
        {
          "type": "test",
          "label": "Scenario engine suites (post p2-101 merge)",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_scenarios backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "D1 加入共享引擎后 41 用例全过（scenario overview 断言更新为含 D1）。"
        },
        {
          "type": "test",
          "label": "Classification-only broad verification",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_routing backend.tests.test_account_route_pipeline backend.tests.test_account_intake backend.tests.test_account_case_filter_postgres backend.tests.test_repository_configuration backend.tests.test_account_admin_features backend.tests.test_workspace_admin_ui_contract",
          "details": "405 tests passed；PostgreSQL parity integration 因当前环境未配置 TEST_POSTGRES_DSN 跳过 1 项。"
        },
        {
          "type": "test",
          "label": "Detailed Invoice focused execution gates",
          "command": ".venv/bin/python -m unittest backend.tests.test_route_correction.RouteCorrectionValidationTests.test_valid_billing_detailed_invoice_is_classification_only backend.tests.test_agent_config.AgentConfigTests.test_detailed_invoice_is_classification_only_and_not_an_automation_workflow backend.tests.test_account_case_reroute.AccountCaseRerouteTests.test_account_billing_automation_keeps_domain_category_and_handler backend.tests.test_account_case_reroute.AccountCaseRerouteTests.test_detailed_invoice_reroute_keeps_classification_without_handler backend.tests.test_account_admin_features.AccountAdminFeatureTests.test_inactive_detailed_invoice_is_not_counted_as_automation backend.tests.test_account_admin_features.AccountAdminFeatureTests.test_legacy_automation_status_uses_subcategory_for_active_eligibility backend.tests.test_worker.WorkerResilienceTests.test_inactive_detailed_invoice_reply_is_dismissed_before_side_effects backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_generates_customer_followup backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_uses_pdf_ocr_text_when_body_is_empty backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_attaches_pdf_to_customer_message_without_ocr backend.tests.test_worker.WorkerResilienceTests.test_detailed_invoice_reply_queues_closing_reply_job_with_pdf_attachments",
          "details": "11 tests passed；覆盖 classification-only route、Rerun/correction、历史 Automated view 排除、legacy Fraud 兼容、inactive_automation reply dismissal，以及 dormant implementation 重新注册后的可执行性。"
        },
        {
          "type": "test",
          "label": "Worker regression suite",
          "command": ".venv/bin/python -m unittest backend.tests.test_worker",
          "details": "105 tests passed；Detailed Invoice mailbox reply 在 inactive gate 后无 linked-ticket、附件或 reply-job 副作用。"
        },
        {
          "type": "test",
          "label": "Owner review",
          "command": "git diff --check + residual detailed_invoice execution-entry review",
          "details": "无剩余 finding；保留分类 taxonomy 与 dormant handler/extractor/email/PDF/Zendesk code，所有生产执行入口均受 ACTIVE_AUTOMATION_SUBCATEGORIES gate 控制。"
        }
      ],
      "source_refs": [
        "backend/services/automation_routing.py",
        "backend/services/account_route_pipeline.py",
        "backend/services/account_billing_handlers.py",
        "backend/services/account_reply_jobs.py",
        "backend/services/automation_persona.py",
        "backend/services/zendesk_comments.py",
        "backend/services/asset_storage.py",
        "backend/services/account_zendesk_internal_comment.py",
        "backend/repositories/ticket_repository.py",
        "backend/worker.py",
        "backend/main.py",
        "scripts/testing/production_ticket_scenarios.py",
        "docs/testing/production_ticket_regression_runbook.md",
        "backend/services/automation_test_scenarios.py"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "用户拍板（对应 p2-71 受控扩围决策）：detailed_invoice 加入 automation；内部邮件回复带 PDF 时将 PDF 转发到 Zendesk 工单；/account 与 /production 实现。完成回采纳用户选择=自动 solve 并关本地单；控制台暂不加附件卡片展示。"
        },
        {
          "at": "2026-08-23",
          "event": "updated",
          "summary": "finalize 刷新 origin/main 时与并行任务 p2-101/p2-102 冲突（剧本逻辑已重构进 backend/services/automation_test_scenarios.py，且 p2-102 任务号已被占用）：任务改号 p2-103，D1 移植进共享引擎，runbook 合并两边改动。"
        },
        {
          "at": "2026-08-23",
          "event": "updated",
          "summary": "再次改号 p2-103→p2-104：并行会话合并 #898（automation test 建表迁移）又占用了 p2-103；该会话 worktree 已清理，无后续竞争。"
        },
        {
          "at": "2026-08-24",
          "event": "updated",
          "summary": "用户决定将 Detailed Invoice 从 Automation 移除并改为只分类；停用当前执行资格与 Automation membership，保留全部实现代码和 handler registry 供未来启用。"
        },
        {
          "at": "2026-08-24",
          "event": "completed",
          "summary": "classification-only 运行契约、历史 view/membership、Agent Config、route correction、mailbox reply gate 与 dormant 实现保留均完成验证。"
        }
      ],
      "legacy_refs": [
        "p1-43",
        "p2-71",
        "p2-100"
      ],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p2-105",
      "title": "/workspace/admin 统计 production 每 case 的 LLM token 用量",
      "status": "done",
      "owner": "zac",
      "summary": "在 /workspace/admin 的 Automated Cases（本就是 production cases）表格新增 Tokens 列与可展开明细，统计口径=RAG 链路 + 自动化链路合并。RAG 侧复用既有 support_rag_query_runs 落库数据（含历史），新增 POST /internal/rag/ticket-families/token-usage/batch 批量端点（≤200 families/次，紧凑 summary 含 stage_totals）避免 N+1。自动化侧此前 usage 全部被丢弃：新增 backend/services/llm_usage_capture.py（ContextVar 作用域采集），在 account_ai_execution 两个封装的 LlmTextResult 返回处埋点（覆盖路由 decide_account_route、quota/billing/enablement/发票/验证/封禁字段抽取、persona、enablement 分类器——它们全部经这两个封装），采集作用域为 worker _prepare_account_reply_job、main create_account_intake、main _process_account_customer_reply 三处 per-case 边界，finally best-effort flush 进新表 support_account_case_llm_usage（migration 2026_08_24 + repository 幂等 ensure，软引用 billing_ticket_id）。admin endpoint 在 payload 后合并两来源为每 case token_usage{available,totals,token_by_model,sources.rag/automation(含 stage_totals)}，RagServiceError 时 available=false+reason（不伪造 0），另附 token_usage_page_total 本页合计。前端加 Tokens 列（in/out，不可用显示 —）、行展开明细（RAG/Automation 两来源 stage 表 + 按模型表）、metric strip 本页 tokens；版本号 bump 20260824-token-usage-1。",
      "next_action": "已 done（本地官方栈 live 验证通过）。用户侧剩余：仅需部署 EC2 main stack（/production 与 /workspace/admin 所在面，含 api/api_production/workers/nginx）——scripts/ops/deploy_surfaces_ec2.sh --skip-split；/automation/* 三环境与 route 容器不含本功能，无需部署。新表在 production 库由 repository ensure 自动建（或用 migration DSN 在 production 库执行 backend/sql/migrations/2026_08_24_account_case_llm_usage.sql）。部署后跑一条 production 自动化即可看到 automation 来源 tokens 入表。",
      "acceptance_criteria": [
        "自动化链路 LLM 调用（经 account_ai_execution 两封装）在采集作用域内的 tokens 落 support_account_case_llm_usage，失败重试的每次成功 attempt 各记一条。",
        "采集与 flush 均为旁路 best-effort：失败仅 warning log，不影响自动化主链路；无 billing id 的 entries 丢弃并告警。",
        "GET /api/workspace/admin/account-automation 每页仅一次 RAG batch 调用 + 一次 usage 汇总查询；token_usage 合并 RAG+automation，RAG 失败时 available=false 且带 reason、totals 归零不误导，automation 侧数字保留供诊断。",
        "admin 前端 Tokens 列渲染 in/out（含 embedding 附注），行展开显示 RAG/Automation stage 明细与按模型明细；RAG 不可用显示 —（title 为原因）。",
        "现有 API 响应只增不改；RAG 与 automation 两来源 stage 不重叠，无双重计数。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Affected unit suites",
          "command": "rtk python3.12 -m pytest backend/tests/test_llm_usage_capture.py backend/tests/test_workspace_api.py backend/tests/test_rag_api.py backend/tests/test_account_ai_execution.py backend/tests/test_workspace_admin_ui_contract.py -q",
          "details": "90 passed（新增 capture 15 例：作用域/no-op/flush/bind/两封装记录/JSON 失败仍记录（4 次重试各一条）；admin 合并与 RagServiceError 不可见路径 2 例；RAG batch 端点 1 例）。"
        },
        {
          "type": "test",
          "label": "Admin UI contract suite",
          "command": "rtk python3.12 -m pytest backend/tests/test_workspace_admin_ui_contract.py -q",
          "details": "28 passed；sandbox 内新增断言：Tokens 列头/本页 tokens 指标/不可用占位 —/1,234 in / 567 out 单元格/toggle-token-detail/展开明细含 rag_answer、quota_field_extractor、openai:gpt-test、calls；版本串断言更新为 20260824-token-usage-1。"
        },
        {
          "type": "test",
          "label": "Worker/intake wrapper regression suites",
          "command": "rtk python3.12 -m pytest backend/tests/test_worker.py backend/tests/test_account_zendesk_comment_sync.py -q",
          "details": "121 passed。注意：worktree 需先 scripts/workflow/link_worktree_env.sh 链接 root .env，否则 route 凭据缺失导致 ZendeskCommentTriggerTests 假失败；test_production_ui_contract 的 deploy 脚本断言失败在干净 main 同样失败（遗留问题，非本任务引入，与 p2-104 evidence 记录一致）。"
        },
        {
          "type": "decision",
          "label": "本地栈预验证跳过原因",
          "command": "",
          "details": "本地 podman 栈与 EC2 共用 RDS supportportal 库且 worker 会争抢 reply/rerun job（既有教训）；本次为纯展示改动，用 TestClient 端点测试 + Node sandbox 契约测试确定性覆盖渲染逻辑，避免未合并代码抢占真实生产 job。runtime live 验证按仓库规则留待合并后官方栈重启执行。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack restart + live verification",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote",
          "details": "PR#903 合并后（root main 897e70c）重启官方轻量栈：镜像 localhost/supportportal-app:897e70c88f88；/health ok 且 app_build.ref=897e70c88f88（rag_service ok、runtime_profile=local_lightweight）；inspect_single_host_stack_mode.sh 复查 build_provenance_status=matched、auxiliary_stack_present=false。live marker：/workspace/admin/ 页面资产 app.js?v=20260824-token-usage-1；GET /api/workspace/admin/account-automation 未授权返回 401（守卫与路由存活）；repository ensure 已在共享库建好 support_account_case_llm_usage 表与索引（to_regclass 双确认，rows=0 属预期）。"
        },
        {
          "type": "test",
          "label": "RAG batch endpoint live data check",
          "command": "podman exec deployment_api_1 python (POST http://rag_api:8020/internal/rag/ticket-families/token-usage/batch)",
          "details": "真实数据验证（经运行栈、内部 auth）：ticket 12940 返回 11,561 in / 4,358 out / 28 emb，stage_totals=rag_answer×4+embedding×4+query_self_query×2+query_rewrite×2，token_by_model=openai:gpt-5.4 / openai:gpt-5.4-mini / siliconflow:BAAI/bge-m3，与库内 support_rag_query_runs 逐项一致；12951 同样非零且明细正确；errors=[]。"
        },
        {
          "type": "decision",
          "label": "已知边界（代码核实）",
          "command": "",
          "details": "①独立 route_service 容器（/v1/cases 控制台流的路由+准备，设计上无 ticket DB）的 tokens 不采集：production /v1/cases 走 call_route HTTP 到该容器；若需要可后续经 RouteResult 契约透传另开任务。②provider 报错的重试 attempt 无 usage 可记（错误响应不含 usage），只记成功 invocation。③自动化侧历史无法回补，只有上线后新数据；RAG 侧含全部历史。④本地栈 admin 的 RAG 数字取决于该栈 RAG 服务指向的知识库，权威视图为 EC2 production 栈。"
        }
      ],
      "source_refs": [
        "backend/services/llm_usage_capture.py",
        "backend/services/account_ai_execution.py",
        "backend/repositories/ticket_repository.py",
        "backend/sql/migrations/2026_08_24_account_case_llm_usage.sql",
        "backend/worker.py",
        "backend/main.py",
        "backend/rag_api.py",
        "backend/services/rag_service_client.py",
        "ui/workspace-ui/admin/app.js",
        "ui/workspace-ui/admin/styles.css",
        "ui/workspace-ui/admin/index.html",
        "docs/rag_change_log.md"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "用户需求：统计 /production 每个 case 的 token 用量并展示在 /workspace/admin。方案问答未获回复，按推荐口径（全链路合并）实施：RAG 复用既有落库，自动化链路新增采集表与埋点。"
        },
        {
          "at": "2026-08-24",
          "event": "updated",
          "summary": "PR#903 合并（root main 897e70c，finalize 成功含 workspace/分支清理）；首会话因 worktree 清理触发 shell ENOENT 故障，重启后完成官方栈重启（897e70c88f88，/health ok，provenance matched，无辅助栈）与全部 live 验证（资产版本 marker、admin 端点 401 守卫、新表 ensure、RAG batch 端点 12940/12951 真实数据逐项核对一致），翻 done。"
        },
        {
          "at": "2026-08-24",
          "event": "updated",
          "summary": "用户纠正部署面说法：本功能只落 /production（EC2 main stack：api/api_production/workers/nginx），与 /automation/* 三环境和 route 容器无关；next_action 从'三环境部署'改为 deploy_surfaces_ec2.sh --skip-split 单发 main stack。"
        }
      ],
      "legacy_refs": [
        "p1-52",
        "p2-93"
      ],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
    },
    {
      "schema_version": 2,
      "task_id": "p2-107",
      "title": "补齐 cached/reasoning tokens 采集 + token 用量 USD 换算展示（/workspace/admin）",
      "status": "done",
      "owner": "zac",
      "summary": "p2-105 的后续增强，两段：(A) cached/reasoning tokens 采集补齐——此前全仓库无任何代码解析 provider usage 明细，两条链路恒记 0；现在 llm_factory 的 _responses_usage/_chat_usage 解析 input_tokens_details.cached_tokens / prompt_tokens_details.cached_tokens / output_tokens_details.reasoning_tokens / completion_tokens_details.reasoning_tokens，LlmTextResult 新增 cached_input_tokens/reasoning_tokens 字段（默认 0，全调用方零破坏）；自动化链 capture 透传 + support_account_case_llm_usage 加两列（migration 2026_08_24_account_case_llm_usage_details.sql + repository 幂等 ALTER，历史数据两列=0 属预期）；RAG 链 query_understanding/rag_context_budget 补传参、rag_qa _invoke_llm_payload_with_trace 4 元组→6 元组（6 调用点+重试累加+RagQueryTrace 尾部两默认字段+fanout 聚合同步累加）、rag_api rag_answer ledger 条目带 trace 两值（usage_ledger JSONB 只增键，表零改动）；token_usage.aggregate_usage_ledger 的 token_by_model 分桶新增 cached_input_tokens/reasoning_tokens 供按模型计价。(B) USD 换算——新模块 backend/services/llm_pricing.py：LLM_PRICING_USD_PER_1M 单价表（key=provider:model，维度 input/output/cached_input/embedding，默认全 None=未定价，遵循仓库 unknown-cost marker 约定：未定价显式 —，绝不静默按 0）+ estimate_token_usage_cost_usd 纯函数（计价语义：input 含 cached，成本=(input−cached)×input价+cached×(cached价缺省回落 input 价)+output×output价+embedding×embedding价；任一模型未定价→该模型 usd=None 且整体 available=False）；main.py _attach_account_case_token_usage 对每 case 挂 token_usage.cost_usd，page_total 加 cost_usd_total/cost_usd_available；admin 前端 Tokens 单元格追加成本小字（$0.0123，未定价显示 $—带 title）、明细 by-model 表加 Cost 列、metric strip 本页合计带成本；版本 bump 20260824-token-cost-1。价格数字待用户提供后一行一改填入 LLM_PRICING_USD_PER_1M 即生效。",
      "next_action": "已 done（官方栈 4dc624fbb1a7 运行含本任务，live 验证通过）。用户侧剩余：EC2 仅部署 main stack（deploy_surfaces_ec2.sh --skip-split，与 p2-105 同，migration 由 repository 幂等 ALTER 自动生效）；各模型单价数字待用户提供后填入 backend/services/llm_pricing.py 的 LLM_PRICING_USD_PER_1M（默认全 None 时页面成本显示 $— 属预期上线态）。",
      "acceptance_criteria": [
        "llm_factory 解析 Responses/Chat 两形态 usage 明细，缺 details 容错为 0；LlmTextResult 新字段默认 0 不破坏任何既有调用方。",
        "自动化链 cached/reasoning 落列（capture→INSERT→summaries 聚合→token_by_model 分桶全透传）；RAG 链 ledger 只增键、检索/生成行为零变化。",
        "llm_pricing：未定价模型 usd=None 且 available=False（无 0 美元假象）；cached⊂input 不双计；cached 价缺省回落 input 价；embedding 计价。",
        "admin 前端：priced 显示 $x.xxxx，unpriced 显示 $—（title=model pricing not configured），usage 不可用时不显示成本；明细 Cost 列按模型分摊。",
        "既有 API 响应只增不改；test_rag_qa/test_rag_agentic 的 mock 元组同步更新为 6 元组。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "New unit suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_factory.py backend/tests/test_llm_pricing.py backend/tests/test_llm_usage_capture.py -q",
          "details": "factory details 解析 3 例（Responses/Chat/无 details 容错）+ pricing 7 例（未定价 unavailable、跨模型求和、cached 回落 input 价、cached 不超 input、空 usage=0、默认表全 None）+ capture 透传/InMemory roundtrip 含 token_by_model 分桶两列。"
        },
        {
          "type": "test",
          "label": "Affected full suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest \u003c15 个受影响套件> -q",
          "details": "524 passed。两个失败（test_rag_agentic comparison first_pass_tools、test_rag_service_client probe_health_disabled）在干净 root main 以完全相同组合同样失败、单跑均通过——既有跨文件顺序污染，非本任务引入。"
        },
        {
          "type": "test",
          "label": "Mock tuple migration",
          "command": "",
          "details": "test_rag_qa.py（25 处跨行+4 处内联+2 处直调解包+7 处类型注解）与 test_rag_agentic.py（3 处）的 _invoke_llm_payload_with_trace mock 4 元组→6 元组（尾部补 0,0）。"
        },
        {
          "type": "decision",
          "label": "价格表默认留空的决策依据",
          "command": "",
          "details": "docs/prompt_change_log.md（gpt54-token-only-observability-v1 条目）：旧成本展示曾因过时/不全价格造成噪音被有意移除，约定保留 unknown-cost markers、未定价显式标记不静默 0；gpt-5.4/gpt-5.6-luna 等为本环境具体模型，价格数字须用户提供，不可编造。knowledge_repository.py _model_cost_for_tokens 为引用不存在字典的死代码，未模仿。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh",
          "details": "PR#917 合并后官方栈运行 root main 4dc624f（含本任务，built 2026-08-24T11:06:13Z）：build_provenance_status=matched、official_health_build_ref=4dc624fbb1a7、auxiliary_stack_present=false；/workspace/admin/ 实际服务 app.js?v=20260824-token-cost-1；GET /api/workspace/admin/account-automation 未授权 401（守卫存活）；共享库 support_account_case_llm_usage 列序含 cached_input_tokens/reasoning_tokens（repository 幂等 ALTER 生效）。价格表未填时成本显示 $— 属预期。"
        }
      ],
      "source_refs": [
        "backend/services/llm_factory.py",
        "backend/services/token_usage.py",
        "backend/services/llm_pricing.py",
        "backend/services/llm_usage_capture.py",
        "backend/repositories/ticket_repository.py",
        "backend/sql/migrations/2026_08_24_account_case_llm_usage_details.sql",
        "backend/services/query_understanding.py",
        "backend/services/rag_context_budget.py",
        "backend/services/rag_qa.py",
        "backend/rag_api.py",
        "backend/main.py",
        "ui/workspace-ui/admin/app.js",
        "ui/workspace-ui/admin/styles.css",
        "ui/workspace-ui/admin/index.html",
        "docs/rag_change_log.md"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "用户追问缓存命中与美元换算。方案问答未获回复，按推荐定案：价格架子先行未配置显示 —、cached/reasoning 采集一起补齐、展示仅 /workspace/admin。"
        },
        {
          "at": "2026-08-24",
          "event": "updated",
          "summary": "PR#917 合并（root 前进至 633b524 后并行链又进 #918 至 4dc624f）；finalize 后会话 shell 第三次触发 ENOENT，重启 ZCode 后在官方栈 4dc624fbb1a7 完成全部 live 验证（provenance matched、资产 20260824-token-cost-1、端点 401、表两列 ALTER 生效），翻 done。"
        }
      ],
      "legacy_refs": [
        "p2-105"
      ],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
    },
    {
      "schema_version": 2,
      "task_id": "p2-108",
      "title": "/automation/production 替代 /production：Phase A 地基（schema bootstrap + parity worker + 部署覆盖）",
      "status": "active",
      "owner": "zac",
      "summary": "按用户 2026-08-24 决策（终态=三环境上线并完全替代旧 /account 与 /production，本轮先行 /automation/production 替代 /production）的分阶段搬迁计划 Phase A：为 split production 环境补齐承载旧栈管线的数据与运行地基。新增 automation_production_worker（完整 app 镜像跑 backend.worker，绑定 supportportal_production schema，队列/事件通道/内部邮件主题命名空间与旧 production 栈隔离，RUNTIME_SCHEMA_MODE=check fail-fast）、一次性幂等的 supportportal_production 全套 account-case 建表脚本（独立 AUTOMATION_PRODUCTION_DB_MIGRATION_DSN 角色，runtime 授权自动跟随）、deploy_ec2 production split 部署集成（worker 纳入服务清单、up 前自动 bootstrap、worker 用容器运行判定代替 HTTP 健康探测）与蓝绿脚本 worker 覆盖（APP_RUNTIME_IMAGE 本地存在性校验 + 切换后 recreate worker）。旧栈 /production 与 /account 零行为变化；worker 的 reply/job/Slack 消费为空转待后续 Phase（B-F）接线。",
      "next_action": "等待用户提供获批的新测试 Ticket 后，对已上线 /production 执行一次客户链 readback；不重跑 AC-12993。随后继续 p2-108 的 /automation/production 蓝绿上线验证，split production 当前保持 build 48ca775d09ad。",
      "acceptance_criteria": [
        "automation_production_worker 服务存在：automation profile、APP_RUNTIME_IMAGE 完整镜像、python -m backend.worker、TICKET_DB_DSN/SCHEMA 绑定 supportportal_production、队列/事件通道为 automation_production 专属（support.ticket_queries.automation_production 等，不与旧栈 support.ticket_queries.production 冲突）、INTERNAL_EMAIL_SUBJECT_NAMESPACE=[automation]、reply poller 开启、邮箱回复消费默认关闭（AUTOMATION_PRODUCTION_REPLY_POLL_ENABLED）。",
        "deployment/bootstrap_automation_production_schema.sh：以独立 AUTOMATION_PRODUCTION_DB_MIGRATION_DSN 角色对 supportportal_production 跑 runtime_bootstrap 全量 DDL（幂等），不得 fallback 到全局 TICKET_DB_MIGRATION_DSN；带同库校验与 staging 主库误指防护，deploy_ec2 production split 部署在 up 之前执行它。",
        "deploy_ec2 production：SPLIT_SERVICES 含 automation_production_worker、APP_RUNTIME_IMAGE 缺失 fail-closed、wait_for_split_service 对 *_worker 服务用容器运行状态判定；蓝绿脚本校验 APP_RUNTIME_IMAGE 本地存在并在切换后将 split worker 按当前 APP_RUNTIME_IMAGE recreate。",
        "Prompt Release 跨独立数据库同步以 content_sha256 作为内容身份并映射到 target-local version；同号异内容不得覆盖目标历史，已有同 hash 内容不得重复插入，candidate 同步不得改变 production active release。",
        "旧栈 /production、/account 与 staging/preproduction split 环境不受影响；worker 在空 schema 上只空转（无 Zendesk/邮件/Slack 出站副作用）。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Split deployment and compose contracts",
          "command": ".venv/bin/python -m unittest backend.tests.test_deploy_ec2 backend.tests.test_single_host_compose backend.tests.test_split_environment_deployment backend.tests.test_build_automation_release",
          "details": "66 项通过：compose 七个 automation profile 服务与 worker 契约（镜像/DB 绑定/队列隔离/邮件命名空间/poller 门控/.msgraph 挂载/网络）、蓝绿契约（worker recreate+APP_RUNTIME_IMAGE 校验）、bootstrap 脚本契约（migration DSN 必填/同库校验/防误指 staging 主库/deploy 集成）、deploy_ec2 假命令回归（production 路径 bootstrap 前置+worker 服务清单+*_worker 健康判定）。"
        },
        {
          "type": "test",
          "label": "Production migration DSN isolation",
          "command": ".venv/bin/python -m unittest backend.tests.test_production_blue_green_behavior backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_deploy_ec2",
          "details": "77 项通过：production bootstrap 只读取 AUTOMATION_PRODUCTION_DB_MIGRATION_DSN；即使全局 TICKET_DB_MIGRATION_DSN 指向 supportportal，专用值仍映射到一次性 runtime_bootstrap 的 TICKET_DB_MIGRATION_DSN 并目标 supportportal_production；专用值缺失或指向其他数据库时在任何 Compose up 前 fail closed，蓝绿 candidate/worker/cutover 顺序与 Compose 契约保持通过。"
        },
        {
          "type": "test",
          "label": "Production runtime deployment gates",
          "command": ".venv/bin/python -m unittest backend.tests.test_deploy_ec2 backend.tests.test_single_host_compose backend.tests.test_prompt_versioning backend.tests.test_account_verification_automation",
          "details": "长期运行的主栈五个服务与 /production 三个服务显式清空 AUTOMATION_PRODUCTION_DB_MIGRATION_DSN，仅一次性 runtime_bootstrap 保留 DDL 凭据；deploy_ec2 在 activate 前校验八个 Prompt runtime 的容器状态、镜像 ID、build ref、release、当前容器日志与 health，并以稳定窗口拒绝 worker 重启。activate 后 production sync/readback 失败返回非零但不回滚已健康主栈。"
        },
        {
          "type": "test",
          "label": "Prompt Release target-local version remap",
          "command": ".venv/bin/python -m unittest backend.tests.test_prompt_versioning backend.tests.test_deploy_ec2",
          "details": "63 项通过：同号异内容分配目标本地新版本、已有同 hash 的不同本地版本直接复用、candidate 不改变目标 active release、激活后两库 release snapshot 内容一致、篡改 hash 继续 fail closed；EC2 近库随机双 schema collision test 1 项通过并自动清理。"
        },
        {
          "type": "test",
          "label": "Production API Prompt runtime service identity",
          "command": ".venv/bin/python -m unittest backend.tests.test_startup_repository_fallbacks backend.tests.test_prompt_versioning backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_prompt_runtime_verification_retries_transient_startup_failure backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_prompt_runtime_verification_rejects_stale_image backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_prompt_runtime_verification_rejects_stale_build_or_release backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_prompt_runtime_verification_rejects_restarting_worker backend.tests.test_single_host_compose.SingleHostComposeTests.test_prompt_runtime_release_is_shared_by_all_llm_services_only",
          "details": "API schema-check startup honors PROMPT_RUNTIME_SERVICE=api-production while preserving api default；部署门禁继续拒绝 stale image/build/release 与 worker restart，Compose 八 runtime service labels 保持一致。"
        },
        {
          "type": "deployment",
          "label": "/production fast deployment",
          "command": "scripts/ops/deploy_surfaces_ec2.sh --skip-split",
          "details": "EC2 无外层 timeout 部署成功：公网 /health build=76d22d5ae1a3、Prompt Release=pr-c9b3a291ecf1；/production/ 200；主栈五个与 production 三个 runtime 使用同一镜像/build/release，RestartCount=0，workers 稳定观察 10 秒；主库与 production DB active release 回读一致且 Fraud v4/code-hash validation=loaded。/automation/production/health 200 并按 --skip-split 保持原 build 48ca775d09ad；未执行客户 Ticket。日志 /tmp/deploy-surfaces-20260825-094728/main-stack.log。"
        }
      ],
      "source_refs": [
        "deployment/docker-compose.single-host.yml",
        "deployment/bootstrap_automation_production_schema.sh",
        "deployment/deploy_ec2.sh",
        "backend/main.py",
        "backend/services/prompt_versioning.py",
        "deployment/deploy_automation_production_blue_green.sh",
        "backend/scripts/runtime_bootstrap.py",
        "backend/worker.py",
        "docs/split_environments_report.md",
        "docs/deploy_automation_release.md"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-25",
      "history": [
        {
          "at": "2026-08-25",
          "event": "production_fast_deploy_completed",
          "summary": "合并跨库 version remap 与 api-production runtime label 修复后，/production 成功上线 build 76d22d5ae1a3 与 Prompt Release pr-c9b3a291ecf1；八 runtime provenance、worker 稳定性、双库 active release、Fraud v4 hash 和公网健康门禁通过，split /automation/production 未改动。"
        },
        {
          "at": "2026-08-25",
          "event": "production_api_prompt_runtime_service_label",
          "summary": "EC2 新栈已健康并成功加载跨库 candidate，但 api_production 的 Prompt runtime 日志被 backend.main 硬编码为 api，导致八 runtime 门禁查找 api-production 超时并回滚；API 改为读取 Compose 已配置的 PROMPT_RUNTIME_SERVICE，默认值仍为 api。"
        },
        {
          "at": "2026-08-25",
          "event": "prompt_release_target_local_version_remap",
          "summary": "EC2 production sync 暴露独立数据库同一 Prompt version 整数可对应不同历史内容；改为按 content_sha256 识别内容并将 release items 映射到目标本地版本，保留目标历史、candidate active 边界与 payload hash 防篡改门禁。"
        },
        {
          "at": "2026-08-25",
          "event": "production_runtime_deploy_gates",
          "summary": "修复 env_file 向八个长期 runtime 泄漏 AUTOMATION_PRODUCTION_DB_MIGRATION_DSN；扩展 deploy_ec2 对主栈与 /production runtime 的统一镜像/build/Prompt Release、日志、health、RestartCount 和 worker 稳定性门禁，并增加激活后 production active-release readback。"
        },
        {
          "at": "2026-08-25",
          "event": "production_migration_dsn_isolation",
          "summary": "EC2 release-20260825-002 蓝绿部署在 schema bootstrap 处暴露全局 TICKET_DB_MIGRATION_DSN 与 production runtime 分属 supportportal/supportportal_production；新增必填 AUTOMATION_PRODUCTION_DB_MIGRATION_DSN，无全局 fallback，并保持同库校验、candidate 前失败边界和长期 runtime 无 DDL 凭据。"
        },
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "用户决策改为 /automation/production 直接替代 /production 并逐项确认功能差距处置（quota→human_review 预期、human review 跳过、detailed_invoice 回复闭环与运维工具跳过、执行内容/回复链/状态同步/Slack 按旧栈、n8n 摄入由用户转发）。按七阶段计划创建 Phase A 任务；采纳当日新合入的 #916 human queue escalation（失败分支语义）与 #918 直发 Engineer Slack（worker env 按直发三件套）。"
        },
        {
          "at": "2026-08-24",
          "event": "preexisting_test_debt_found",
          "summary": "合并 origin/main（含 #920 蓝绿回滚加固）时发现其新增 backend/tests/test_production_blue_green_behavior.py 5 项在本机 macOS 全部失败于 'Missing command: flock'（脚本 flock 依赖在参数校验阶段即触发，macOS 无原生 flock），与本次改动无关（同版本干净 main 上同样失败），按协调规则不顺手修，留待 flock shim 或 Linux 环境执行；本任务验证命令暂不含该套件。"
        }
      ],
      "legacy_refs": [
        "p2-88"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-109",
      "title": "/automation/production 替代 /production：Phase B intake 旧栈语义（进行中）",
      "status": "active",
      "owner": "zac",
      "summary": "Phase B：把旧栈 /production 的 intake 执行语义搬进 /automation/production——新增 backend/services/automation_account_intake.py（完整移植 main.py _create_account_intake_impl 自动执行段：ticket/account case/route execution 持久化、四类 ACTIVE handler 的字段抽取与内部邮件、缺字段追问与确认 reply job、ownership gate（含事件与 fail-closed）、#916 human queue 升级、失败 reconcile+alert、not_automated→Engineer Case+派单）；automation_production_runtime /v1/cases 改为调用该管线（废除即时 comment/status 三副作用与 delivery ledger 流，execution 记录保留为审计视图）；production 契约废除 comment_visibility 必填（preproduction forced internal 不动）。",
      "next_action": "代码与测试已完成待用户侧部署验证：EC2 部署新 release（deploy_ec2 --environment production 或蓝绿，含 bootstrap 建表与 automation_production_worker）后，用受控 ticket 验证 intake 全链（内部邮件送达、缺字段追问 reply job 的延迟 public 回复、not_automated→Engineer Case+Slack、失败转人工队列）。随后进入 Phase C：评论摄入端点（comment-sync-target/PUT comments）+ 客户回复触发链（含 RAGFlow fallback 与 escalation），n8n commen_sync production origin 换 URL 由用户执行。",
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Parity intake and runtime contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_account_intake backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_contracts backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_deploy_ec2 backend.tests.test_build_automation_release",
          "details": "111 项全部通过：intake 六分支单测（fraud 缺/齐字段、suspension contact、抽取失败→#916 升级、not_automated→Engineer Case+派单、ownership fail-closed）；production runtime 契约（无 visibility 也进管线、pipeline 异常→failed+409 重放、legacy 五字段免 visibility、intake_outcome 落库）；契约矩阵（production visibility 可选，preprod forced internal 不变）；route_payload decision 字段；bundle/镜像清单（依赖模块留在 production 镜像）；compose/deploy/蓝绿假命令回归。"
        }
      ],
      "source_refs": [
        "backend/services/automation_account_intake.py",
        "backend/automation_production_runtime.py",
        "backend/services/automation_contracts.py",
        "backend/main.py",
        "backend/services/account_human_review_escalation.py",
        "deployment/docker-compose.single-host.yml",
        "backend/Dockerfile.automation"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "Phase A（p2-108/PR#921）合并后开工 Phase B。已读齐移植面（main.py 薄壳与 intake 自动执行段、route_service/route_preparation 返回结构、#916 升级服务、engineer case 构造与派单）。关键澄清：is_registered_automation 只认 ACTIVE 集合，quota 在旧栈同样是 not_automated→Engineer Case，与用户'quota→human_review 预期'的旧描述相比按旧栈语义统一走 Engineer Case（Slack 协作按旧栈）。"
        },
        {
          "at": "2026-08-24",
          "event": "phase_b_completed",
          "summary": "收尾清单①-⑤完成：route_payload 增补全部 decision 语义字段（stage_attempts/matched_signals/semantic_intent 等，RouteResult route 为自由 dict 无需模型变更）；Dockerfile production 角色放开六个依赖模块（保留 main.py/rerun 系/worker.py/rag_reset 排除）并同步 bundle 断言；compose 与蓝绿 candidate 补 TICKET_DB/[automation] 前缀/OPENAI/DEEPSEEK/Graph 全套 env；契约与 runtime 测试按新行为重写并新增 intake 六分支单测（共 111 项绿）；cutover/runbook 文档同步（production comment_visibility 不再必填）。已知取舍：route prepare 与 runtime attempt 各做一次字段抽取（旧栈语义忠实移植的代价，后续可加 prepare:false 优化）；reconcile 端点保留但新流程不再产生 outcome_unknown 记录。"
        }
      ],
      "legacy_refs": [
        "p2-88",
        "p2-108"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-110",
      "title": "/automation/production 替代 /production：Phase C 评论摄入 + 客户回复链（纯移植）",
      "status": "active",
      "owner": "zac",
      "summary": "按用户选定方案 B（纯移植、镜像物理排除契约不变）把旧栈评论摄入与客户回复链搬进 /automation/production：新增 backend/services/automation_account_reply_sync.py（main.py _process_zendesk_comment_trigger 与 _process_account_customer_reply_impl 的忠实移植：幂等 claim、过滤规则、Engineer Case 客户评论入线程事件、ownership gate、suspension 两阶段确认（contact 确认→handoff 邮件→closing reply job）、handler 字段进展、无进展重路由（decide_account_route）、RAGFlow fallback（answer→verbatim reply job / 不能答→escalate_unexpected_reply_to_human）、追问/确认 reply job）；runtime 新增 GET comment-sync-target 与 PUT comments 端点（X-N8n-Request-Token，快照校验/404/409 语义复刻）；compose 与蓝绿 candidate 补 RAG/RAGFlow env。Phase B 模块的 attempt 构建器扩展 existing_fields/already_requested/follow_up 参数供回复链复用。工程师 AI 调查回合（_process_engineer_investigation_message）按阶段边界留给 Slack 协作阶段接线，本阶段先落客户评论的线程事件。",
      "next_action": "待用户 EC2 部署 + n8n commen_sync 的 production origin 换 URL 后做真实工单评论往返验收（追问进展、RAG fallback、suspension 确认）。随后 Phase D：状态同步端点（PUT status + solved/closed 关 case/Engineer Case 收尾）。",
      "acceptance_criteria": [
        "GET /api/integrations/zendesk/account-cases/{id}/comment-sync-target 与 PUT .../comments 在 /automation/production 下可用，鉴权与 422/404/409 语义与旧栈一致。",
        "触发链：幂等 per comment id、agent/initial/private/empty/前置评论忽略、非 production case 忽略、Engineer Case 分支记录客户评论事件。",
        "回复链：ownership gate fail-closed 停自动化、suspension 两阶段状态机（确认→handoff→closing）、handler 进展判定与重路由、RAG fallback answer/escalation、追问与确认 reply job。",
        "旧栈 /production 与 /account 零行为变化；preproduction/staging 契约不变。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Comment ingestion and reply chain regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_comment_sync backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_account_intake backend.tests.test_automation_contracts backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_account_zendesk_comment_sync_postgres",
          "details": "86 项通过：comment-sync-target 鉴权与 membership、PUT comments 快照校验/404/触发调用、agent/initial 忽略不占幂等、Engineer Case 分支事件落库、既有 intake/runtime/contracts/compose 全回归绿。"
        }
      ],
      "source_refs": [
        "backend/services/automation_account_reply_sync.py",
        "backend/services/automation_account_intake.py",
        "backend/automation_production_runtime.py",
        "backend/main.py",
        "deployment/docker-compose.single-host.yml",
        "deployment/deploy_automation_production_blue_green.sh",
        "docs/integrations/n8n/automation_environments_cutover.md"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "用户在 A（复用 backend.main，需放开镜像物理排除）与 B（纯移植，契约不变）之间选定 B。完成移植与接线；工程师 AI 调查回合按阶段边界留给 Slack 协作阶段。"
        }
      ],
      "legacy_refs": [
        "p2-108",
        "p2-109"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-111",
      "title": "RAGFlow 兜底答案改由 gpt-5.6-luna 生成并经 Persona 渲染后回复",
      "status": "done",
      "owner": "zac",
      "summary": "production case 意外回复兜底链路的两项行为变更（p2-93 后续）：(A) 生成侧——新场景 RAGFLOW_ANSWER（llm_profiles.py，默认 openai/gpt-5.6-luna/xhigh/120s/pinned 无 fallback，独立 env 旋钮 RAGFLOW_ANSWER_MODEL/RAGFLOW_ANSWER_REASONING_EFFORT，不动全仓共享的 RAG_ANSWER 场景）；ragflow_docs_search_skill 生成提示词新增 core_content_only（answer=核心技术内容，无问候/签名，Persona 负责口吻；JSON 契约 answer/key_steps/citations/insufficient_evidence 不变）；生成后 record_llm_invocation(stage=ragflow_docs_answer) 补上 p2-107 发现的 token 采集缺口。(B) 回复侧——RagFallbackOutcome 拆分 references；main.py 入队改为 reply_facts（behavior/reply_intent=rag_fallback_answer、provided_answer、references、customer_first_name）走 persona 管线；worker prepare 短路分支与 publish 的 rag 特判改为仅 legacy draft-only job 保真发布，新 job 经 render_automation_reply 渲染；persona 提示词 v12→v13 新增 rag_fallback_answer 转述策略（第一人称转述 provided_answer，不增删技术事实，不编造链接，References 由系统追加）；发布前把 facts.references 确定性追加到 persona 正文后（链接零丢失）。fail-closed 语义全保留（检索/证据/引用/生成/persona 任一失败→人工）。单次兜底成本 = luna(xhigh) 生成 + mini persona 渲染两次调用，token 两侧均采集。",
      "next_action": "已 done（官方栈 23c19e3bd7d4 运行含本任务，live 验证通过）。用户侧剩余：EC2 仅部署 main stack（deploy_surfaces_ec2.sh --skip-split）；可选经 /automation/test 触发一条带意外回复的剧本做 E2E 复验（persona 化回复+末尾 References）。",
      "acceptance_criteria": [
        "RAGFLOW_ANSWER 场景默认 gpt-5.6-luna/xhigh/pinned，env 可覆盖；RAG_ANSWER 场景（本地 RAG 管线/dashboard 客户端流）行为不变。",
        "ragflow 生成 token 落 support_account_case_llm_usage（stage=ragflow_docs_answer），在 worker/main 采集作用域内生效。",
        "rag_fallback job 以 provided_answer facts 进 persona 管线：render_automation_reply 转述生成最终回复，缺 provided_answer 报 automation_persona_missing_provided_answer；legacy draft-only rag job 保持 verbatim 发布不进渲染。",
        "References 在 persona 正文校验（签名门/契约）通过后确定性追加，链接零丢失、不被 persona 改写。",
        "fail-closed 链不变：RAGFlow 检索失败/证据不足/引用无效/luna 生成失败/persona 不可用或渲染失败→全部升级人工。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "New + affected suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_worker.py backend/tests/test_account_intake.py backend/tests/test_account_zendesk_comment_sync.py backend/tests/test_automation_persona.py backend/tests/test_ragflow_docs_search_skill.py backend/tests/test_account_reply_rag_fallback.py backend/tests/test_account_reply_version_fence.py backend/tests/test_llm_profiles.py backend/tests/test_rag_qa.py backend/tests/test_rag_api.py backend/tests/test_account_ai_execution.py backend/tests/test_llm_usage_capture.py -q",
          "details": "515 passed。新增用例：skill 侧 core_content_only 提示词+capture 内 entries 落 stage=ragflow_docs_answer+场景默认值/env 覆盖；persona 侧 rag_fallback 转述策略进 system prompt+user_prompt 携带 provided_answer+缺字段报错；worker 侧 prepare 走 persona 渲染（persona_v8_scheduled）+publish 时 References 确定性追加；intake 侧 job 以 facts 入队。既有断言按新契约更新（references 拆分、facts 入队、verbatim 测试重写为 persona+References）。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote",
          "details": "PR#928 合并后官方栈运行 root main 23c19e3（镜像 23c19e3bd7d4，built 2026-08-24T12:35:25Z）：/health ok（rag_service ok、runtime_profile=local_lightweight）、build_provenance_status=matched、auxiliary_stack_present=false；容器内实测 resolve_model_profile(ragflow_answer) → model=gpt-5.6-luna / effort=xhigh / fallbacks=()。纯后端改动无资产版本 marker 要求。"
        }
      ],
      "source_refs": [
        "backend/services/llm_profiles.py",
        "backend/services/ragflow_docs_search_skill.py",
        "backend/services/prompts/rag_answer.py",
        "backend/services/account_reply_rag_fallback.py",
        "backend/services/account_reply_jobs.py",
        "backend/services/automation_persona.py",
        "backend/main.py",
        "backend/worker.py",
        ".env.example",
        "docs/prompt_change_log.md",
        "docs/rag_change_log.md"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "用户需求：RAGFlow 检索后生成模型换 gpt-5.6-luna（复用路由 luna 配置，问答定独立场景旋钮）；生成只需核心技术内容，最终回复经 persona 组装；References 渲染后确定性追加。任务号 p2-110（p2-108/109 已被 split-env 并行链占用）。"
        },
        {
          "at": "2026-08-24",
          "event": "updated",
          "summary": "任务号 p2-110→p2-111：finalize 刷新时并行链（split-env Phase C，PR#927）已占用 p2-110；内容不变。"
        },
        {
          "at": "2026-08-24",
          "event": "updated",
          "summary": "PR#928 合并（root 23c19e3）；finalize 后会话 shell 第五次触发 ENOENT，重启 ZCode 后官方栈重启至 23c19e3bd7d4 并完成 live 验证（provenance matched、容器内场景解析 gpt-5.6-luna/xhigh/无 fallback），翻 done。"
        }
      ],
      "legacy_refs": [
        "p2-93",
        "p2-107"
      ],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p2-112",
      "title": "/automation/production 替代 /production：Phase D 状态同步端点（纯移植）",
      "status": "active",
      "owner": "zac",
      "summary": "Phase D：把旧栈 Zendesk 工单状态同步搬进 /automation/production——automation_account_reply_sync.py 增 sync_account_case_ticket_status（main.py PUT .../status 语义移植：状态投影 update_account_case_zendesk_status + solved/closed 时关闭活跃 Engineer Case（build/close_case_context + engineer_case_closed 线程事件 + ticket resolved + EngineerAssignmentService.resolve_case））；runtime 新增 PUT /api/integrations/zendesk/account-cases/{id}/status 端点（token 鉴权、zendesk_status 白名单校验、updated_at ISO 规范化、404/409 语义复刻）。任务号 p2-111 已被并行链（#928 RAGFlow persona 渲染）占用，顺延为 p2-112。",
      "next_action": "待用户 EC2 部署 + n8n case_status_sync 的 production origin 换 URL 后做真实工单状态同步验收（solved 关 case/Engineer Case）。随后 Phase E：Slack 协作收口（工程师 AI 调查回合 _process_engineer_investigation_message 移植 + Slack 入向双目标路由 + fraud 公开回复后 assignee 转人工）。",
      "acceptance_criteria": [
        "PUT .../status 在 /automation/production 下可用，鉴权与 422/404 语义与旧栈一致。",
        "solved/closed 触发：本地 ticket 置 resolved+closed_at、活跃 Engineer Case 关闭（线程事件+investigation 收尾）、派单 resolve。",
        "非终态状态只做投影不触发收尾；旧栈与 preprod/staging 零行为变化。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Status sync endpoint and full parity regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_comment_sync backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_account_intake backend.tests.test_automation_contracts backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_account_zendesk_status_sync",
          "details": "94 项通过：status 端点鉴权/非法状态 422、solved 关 Engineer Case（线程事件+ticket resolved+派单 resolve 断言）、open 不触发收尾；既有评论/intake/runtime/contracts/compose 全回归绿。"
        }
      ],
      "source_refs": [
        "backend/services/automation_account_reply_sync.py",
        "backend/automation_production_runtime.py",
        "backend/main.py",
        "backend/services/engineer_cases.py",
        "docs/integrations/n8n/automation_environments_cutover.md"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "Phase C（p2-110/PR#927）合并后开工 Phase D。payload_to_record/close_for_customer_resolution 两个 main.py 薄壳按 B 纯移植方案搬入 reply_sync 模块（依赖 engineer_cases 三个 service 函数均已在 production 镜像内）。"
        }
      ],
      "legacy_refs": [
        "p2-108",
        "p2-109",
        "p2-110"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-113",
      "title": "/automation/production 替代 /production：Phase E Slack 协作收口（纯移植）",
      "status": "active",
      "owner": "zac",
      "summary": "Phase E：把旧栈 Engineer Case Slack 协作闭环搬进 /automation/production——新增 backend/services/automation_engineer_collab.py：process_engineer_investigation_message（guardrail-only 路径的调查 AI 回合：状态版本管理、append_engineer_investigation_message + default_investigation_prompt、engineer_ai_response 线程事件）、handle_slack_engineer_message（event_id 幂等 claim/fail/complete）、handle_slack_engineer_action（guardrail 终审 + final_approve：guardrail 校验链、engineer Zendesk delivery 入队（幂等键/immutable_content/comments_revision 门控））、resolve_slack_engineer_thread_binding（team/channel hmac 校验 + 线程绑定解析）；runtime 新增三个入向端点（GET thread-bindings/resolve、POST messages、POST actions，X-N8n-Request-Token）；评论触发链的 Engineer Case 分支从'仅记录事件'升级为完整 AI 调查回合。刻意省略（登记）：multi-agent Plan/Execute/Review 刷新分支（两条 split 入向均 multi_agent_enabled=False）与 _normalize_engineer_case_payload_for_read 读取整形。",
      "next_action": "待用户 EC2 部署 + n8n Slack 入向工作流（App Mention/Interaction→Engineer）切到 /automation/production 端点后做全链验收（工程师消息→AI 回合→guardrail→final_approve→Zendesk 公开评论投递，投递由 automation_production_worker 的 engineer zendesk delivery drain 执行）。随后 Phase F：邮箱闭环（开 AUTOMATION_PRODUCTION_REPLY_POLL_ENABLED，[automation] 前缀，fraud/enablement 完成识别，detailed_invoice 分支跳过）。",
      "acceptance_criteria": [
        "三个入向端点在 /automation/production 下可用，鉴权与 422/404/409 语义与旧栈一致；messages/actions 按 event_id/interaction_id 幂等。",
        "评论触发链 Engineer 分支：客户评论→zendesk_customer_comment 事件→调查 AI 回合→engineer_ai_response 事件（conversation/draft 版本推进）。",
        "guardrail→final_approve→engineer Zendesk delivery 入队（worker 侧投递），guardrail 校验链与 stale 防护语义一致。",
        "旧栈与 preprod/staging 零行为变化。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Engineer Slack endpoints and parity regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_comment_sync backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_account_intake backend.tests.test_automation_contracts backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose",
          "details": "91 项通过：Slack 端点鉴权/非法载荷 422、messages 跑完整 AI 回合（conversation/draft 版本与 engineer_ai_response 事件断言）、thread-bindings 未配置 503、评论触发 Engineer 分支升级后版本断言；既有全回归绿。"
        }
      ],
      "source_refs": [
        "backend/services/automation_engineer_collab.py",
        "backend/services/automation_account_reply_sync.py",
        "backend/automation_production_runtime.py",
        "backend/main.py",
        "backend/services/engineer_guardrail_agent.py",
        "docs/integrations/n8n/automation_environments_cutover.md"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "Phase D（p2-112/PR#929）合并后开工 Phase E。investigation AI 回合/三个入向端点/guardrail-final_approve 链按 B 纯移植方案完成；依赖的 engineer_guardrail_agent/investigation_flow/engineer_cases 均已在 production 镜像内。"
        }
      ],
      "legacy_refs": [
        "p2-108",
        "p2-109",
        "p2-110",
        "p2-112"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-114",
      "title": "账号链路小任务模型全换 gpt-5.6-luna（extractor/persona/classifier/reply）",
      "status": "done",
      "owner": "zac",
      "summary": "用户指令\"为什么还有5.4？全换成5.6\"，定案仅账号链路（客户端流/工程师流/本地 RAG 不动）、小任务 low 档（路由与 ragflow 兜底保持 xhigh）。改动：llm_profiles 新场景 ACCOUNT_EXTRACTOR（默认 openai/gpt-5.6-luna/low/timeout 30s/pinned 无 fallback，env ACCOUNT_EXTRACTOR_MODEL/REASONING_EFFORT/TIMEOUT_SECONDS）——七个 production case 字段 extractor（quota/detailed_invoice/verification/suspension/enablement_field + billing_automation 内部）从共享的 INTENT_ROUTER_SCENARIO（gpt-5.4-mini、3 秒紧超时、客户端流共享不可直改）整体迁到新场景；AUTOMATION_PERSONA / ENABLEMENT_COMPLETION_CLASSIFIER / BILLING_REPLY / ENABLEMENT_REPLY 四场景默认 model gpt-5.4-mini→gpt-5.6-luna（effort 保持 low；persona/billing/enablement_reply 超时 8s/6s→30s、classifier 8s→20s）；enablement_automation 无需改文件（走 ENABLEMENT_REPLY 场景默认）。INTENT_ROUTER（客户端流路由）模型与 3s 时延预算保持不变；root/EC2 .env 的 INTENT_ROUTER_MODEL 不动。换完后 production case 链路只剩 openai:gpt-5.6-luna 一个模型（token_by_model 单条目；价格表只需 luna 一套价，LLM_PRICING_USD_PER_1M 已有 key）。改动由预制幂等脚本 ~/Desktop/p2-113-apply.py 套用（任务号原定 p2-110→p2-113 均被并行占用，最终 p2-114）。",
      "next_action": "已 done（本地官方栈 f468c6e309f2 运行含本任务，live 验证通过）。用户侧剩余：EC2 蓝绿部署链用户自修中（bootstrap schema/worker PGVECTOR_DSN/稳定等待/verify 识别 candidate 四根因，2026-08-25）；修好后 fetch 即得 luna 默认（PR#934 已在 origin/main）；EC2 .env 经用户检查无 AUTOMATION_PERSONA_MODEL/ENABLEMENT_COMPLETION_CLASSIFIER_MODEL/BILLING_REPLY_MODEL/INTENT_ROUTER_MODEL 覆盖，走代码默认即生效，无需改 .env。",
      "acceptance_criteria": [
        "ACCOUNT_EXTRACTOR 场景默认 gpt-5.6-luna/low/30s/pinned，env 三旋钮可覆盖；INTENT_ROUTER 场景（客户端流）模型、effort、3s 超时与 deepseek 兜底行为全部不变。",
        "七个 extractor 的场景引用全部从 INTENT_ROUTER_SCENARIO 迁到 ACCOUNT_EXTRACTOR_SCENARIO；enablement_automation 经 ENABLEMENT_REPLY 默认换 luna。",
        "persona/classifier/billing_reply/enablement_reply 默认 luna/low，超时放宽（30/20/30/30s），温度与重试次数不变。",
        "production case 链路 token_by_model 只剩 openai:gpt-5.6-luna。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Affected suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_quota_field_extractor.py backend/tests/test_enablement_completion_classifier.py backend/tests/test_automation_persona.py backend/tests/test_worker.py backend/tests/test_account_intake.py backend/tests/test_account_zendesk_comment_sync.py backend/tests/test_enablement_automation.py backend/tests/test_account_suspension_field_extractor.py backend/tests/test_enablement_field_extractor.py backend/tests/test_account_verification_automation.py backend/tests/test_billing_automation_email.py backend/tests/test_account_route_pipeline.py backend/tests/test_account_ai_execution.py backend/tests/test_llm_usage_capture.py -q",
          "details": "482 passed。新增用例：ACCOUNT_EXTRACTOR 默认值（luna/low/30s/pinned）+三 env 旋钮覆盖；billing/enablement_reply 默认断言更新为 luna/30s；classifier/persona 测试中的 mini 字符串仅为 mock 标签无需改。"
        },
        {
          "type": "decision",
          "label": "范围与档位定案",
          "command": "",
          "details": "问答未获答复按推荐执行：仅账号链路（客户端 ack/web 搜索/engineer/本地 RAG 管线保持原模型，避免客户端首响时延劣化）、小任务 low 档；extractor 拆独立场景解决共享 INTENT_ROUTER 的 3 秒紧超时与客户端流耦合。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote",
          "details": "PR#934 合并后官方栈运行 root main f468c6e（镜像 f468c6e309f2，built 2026-08-25T02:36:37Z）：/health ok、build_provenance_status=matched、auxiliary_stack_present=false；容器内实测五个账号链路场景 account_extractor/automation_persona/enablement_completion_classifier/billing_reply/enablement_reply 全部 gpt-5.6-luna/low（超时 30/30/20/30/30s），intent_router（客户端流）保持 gpt-5.4-mini/low 不变。"
        }
      ],
      "source_refs": [
        "backend/services/llm_profiles.py",
        "backend/services/quota_field_extractor.py",
        "backend/services/detailed_invoice_field_extractor.py",
        "backend/services/account_verification_field_extractor.py",
        "backend/services/account_suspension_field_extractor.py",
        "backend/services/enablement_field_extractor.py",
        "backend/services/billing_automation.py",
        ".env.example",
        "docs/prompt_change_log.md"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "用户\"为什么还有5.4？全换成5.6\"。任务号 p2-110→p2-113→p2-114（两次被并行链占用）。改动由预制幂等脚本套用；billing_reply 超时默认实为 6s（脚本锚 8s 未命中）已手工补正为 30s。"
        },
        {
          "at": "2026-08-25",
          "event": "updated",
          "summary": "PR#934 合并（root f468c6e）；官方栈重启至 f468c6e309f2 完成全部 live 验证（provenance matched、五场景容器内实测 luna/low、客户端流 mini 不变），翻 done。用户确认 EC2 .env 无相关模型覆盖；EC2 蓝绿部署链四根因（schema bootstrap/worker PGVECTOR_DSN/稳定等待/verify 识别 candidate）由用户自修，修好后 fetch 即得 luna 默认。"
        }
      ],
      "legacy_refs": [
        "p2-111",
        "p2-107"
      ],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p2-115",
      "title": "/automation/production 替代 /production：Phase F+G 收尾（邮箱闭环开关 + 切流准备，任务号因 p2-114 被并行链 #934 占用顺延）",
      "status": "active",
      "owner": "zac",
      "summary": "七阶段搬迁收尾。Phase F（邮箱闭环）经核实为零代码缺口：worker 邮箱 poller 由 AUTOMATION_REPLY_POLL_ENABLED 门控（split worker 映射 AUTOMATION_PRODUCTION_REPLY_POLL_ENABLED，默认 false），[automation] 主题前缀过滤（internal_email_subject_matches 锚定前缀）与跨栈 dismissal 机制均为既有代码，启用=EC2 .env 开关+重启。Phase G（切流准备）落地：verify_split_environments.sh 新增六个 parity 端点鉴权负例探针（comment-sync-target/comments/status/slack messages/actions/thread-bindings）；split_environments_report.md 刷新 v3（A-E 合并记录、Phase F 开关步骤、n8n 四组 URL 切流清单、路线更新为直接替代）。",
      "next_action": "用户侧执行：① EC2 部署最新 release（A-E 代码）；② Phase F 开关（.env AUTOMATION_PRODUCTION_REPLY_POLL_ENABLED=1 + recreate worker）；③ 跑 verify_split_environments.sh（含新探针）全绿；④ 受控工单全链验收（intake→邮件/追问→延迟 public 回复→评论回复→guardrail/final_approve→状态同步关闭→邮箱完成闭环）；⑤ Phase G：n8n 四组换 URL + Company ID 互斥灰度切流（cutover 文档 §2/§4），观察期后旧端点下线。",
      "acceptance_criteria": [
        "verify 脚本六个 parity 端点探针（401 负例）在 EC2 全绿。",
        "报告 v3 准确反映 A-E 合并状态与切流清单；邮箱闭环开关路径文档化。",
        "无运行时代码变更（纯探针+文档+登记）。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Static verification and parity regression",
          "command": "bash -n deployment/verify_split_environments.sh && .venv/bin/python -m unittest backend.tests.test_split_environment_deployment backend.tests.test_automation_comment_sync backend.tests.test_automation_production_runtime_contract",
          "details": "verify 脚本语法通过；split 部署/评论/intake/runtime 契约回归绿。"
        }
      ],
      "source_refs": [
        "deployment/verify_split_environments.sh",
        "docs/split_environments_report.md",
        "deployment/docker-compose.single-host.yml",
        "backend/worker.py",
        "docs/integrations/n8n/automation_environments_cutover.md"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "Phase E（p2-113/PR#933）合并后收尾 F+G。核实邮箱 poller 门控链（AUTOMATION_REPLY_POLL_ENABLED→AUTOMATION_PRODUCTION_REPLY_POLL_ENABLED 映射已在 Phase A compose）确认 F 无代码缺口；G 落地探针与报告 v3。另发现根仓库存在他人未提交改动（quota_field_extractor.py/prompt_change_log.md），未触碰。"
        }
      ],
      "legacy_refs": [
        "p2-108",
        "p2-109",
        "p2-110",
        "p2-112",
        "p2-113"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-116",
      "title": "/automation/production parity：回复链接入 token 用量采集 + 消除双重字段抽取",
      "status": "active",
      "owner": "zac",
      "summary": "两项 parity 缺口收口（用户指示）：① 回复链接入 per-case LLM token 用量采集——process_account_customer_reply 按旧栈同款 begin/end/flush_case_usage_capture 包裹（billing_ticket_id 维度，采集条目经 split 生产 repository 落库，进入 /workspace/admin 统计口径）；② 消除每单双重字段抽取——RouteRequest 增加 prepare 开关（默认 True，staging/preproduction 即时评论流不受影响），production runtime 投递时传 prepare=False，route 服务跳过 prepare_action_plan（返回 preparation_status=skipped 的空 plan），因为 parity runtime 按旧栈语义自行重建 attempt（B 阶段设计），route 侧那次 LLM 抽取纯属重复成本。",
      "next_action": "随 A-F 一并由用户 EC2 部署验证；无独立线上步骤（token 采集条目会在受控工单验收时出现在 /workspace/admin）。",
      "acceptance_criteria": [
        "回复链处理期间 LLM 用量被采集并 flush 到 supportportal_production 的用量表（billing_ticket_id 维度）。",
        "production intake 的 route 调用带 prepare=False，route 服务不再执行 prepare_action_plan 的 LLM 抽取；staging/preproduction 行为不变（默认 prepare=True）。",
        "旧栈零行为变化。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Usage capture and prepare-flag regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_comment_sync backend.tests.test_route_service_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_contracts backend.tests.test_automation_account_intake backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose",
          "details": "94 项通过：新增 runtime 断言 route_request.prepare=False、回复链 begin/end/flush 调用与条目数断言；route 契约（含新字段默认行为）与既有全回归绿。"
        }
      ],
      "source_refs": [
        "backend/services/automation_account_reply_sync.py",
        "backend/services/automation_contracts.py",
        "backend/route_service.py",
        "backend/automation_production_runtime.py",
        "backend/services/llm_usage_capture.py"
      ],
      "created_at": "2026-08-24",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-24",
          "event": "created",
          "summary": "用户确认两项缺口需处理：回复链 token 采集（缺失）与双重字段抽取（B 阶段已知取舍，登记为可优化）。同 PR 修复：采集直搬旧栈包裹；prepare 开关消除 route 侧重复抽取。"
        }
      ],
      "legacy_refs": [
        "p2-109",
        "p2-110",
        "p2-115"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-117",
      "title": "填入 gpt-5.6-luna 官方单价 + /workspace/admin 展示模型价格（p2-107 后续）",
      "status": "done",
      "owner": "zac",
      "summary": "p2-107 的收口：用户提供官方价格页（developers.openai.com/api/docs/models/gpt-5.6-luna），两段：(A) 填价——LLM_PRICING_USD_PER_1M 的 openai:gpt-5.6-luna 填入 input $0.2 / cached_input $0.02 / output $1.2（USD 每 1M tokens；官方页另注 >272K 长上下文 2x/1.5x 与缓存写 1.25x 属请求级特殊计费，不进按 token 用量估算的表）；其余模型保持 None=未定价（unknown-cost 约定不变，历史混合 case 仍显示 $— 属预期）。纯 luna case（p2-114 后生产链路唯一模型）的成本从 $— 变为真实金额。(B) 展示——llm_pricing 新增 model_pricing_payload()（每模型 input/cached_input/output/embedding 单价 + priced 标记）；/api/workspace/admin/account-automation payload 增加 model_pricing 字段；admin 前端 Automated Cases 页 metric strip 下方新增 Model pricing 横条（priced 模型显示 in $X.XX / cached $X.XX / out $X.XX per 1M，未定价模型显示 pricing not configured）；版本串 bump 20260825-model-pricing-1。",
      "next_action": "已 done（官方栈 48ca775d09ad 运行含本任务，live 验证通过）。用户侧剩余：EC2 仅部署 main stack（--skip-split）后，production 新 case 的 token_by_model 将只剩 luna、成本显示真实金额；如需 gpt-5.4-mini 等模型也计价，同样按官方数字填入 LLM_PRICING_USD_PER_1M 即生效（混合模型 case 遵循全有或全无：任一未定价→整 case $—）。",
      "acceptance_criteria": [
        "价格表 luna 三价精确为 0.2/0.02/1.2，其余模型仍全 None；纯 luna usage 计价端到端正确（100 万 input 含 20 万 cached + 5 万 output = $0.224）。",
        "account-automation 端点 payload 含 model_pricing（provider/model/四维单价/priced），不依赖 case 数据恒返回。",
        "admin 前端 Automated Cases 页展示 Model pricing 横条：priced 模型显示三档单价 per 1M，未定价模型显示 pricing not configured；无 model_pricing 数据时不渲染横条。",
        "既有 API 响应字段只增不改；既有测试除价格表默认值断言（按新事实更新）外全绿。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Affected suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_pricing.py backend/tests/test_workspace_admin_ui_contract.py backend/tests/test_workspace_api.py -q",
          "details": "67 passed + 4 subtests（合并前在任务 worktree 跑）。新增/更新：默认表 luna 三价精确断言+其余模型全 None、纯 luna 端到端计价（100 万 input 含 20 万 cached + 5 万 output=$0.224）、model_pricing_payload 形状、端点 model_pricing 契约、前端横条渲染/未定价标记/无数据不渲染、版本串断言 20260825-model-pricing-1。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "bash scripts/workflow/inspect_single_host_stack_mode.sh",
          "details": "PR#940 合并后官方栈运行 root main 48ca775（built 2026-08-25T03:43:54Z）：official_health_status=ok、build_provenance_status=matched、official_health_build_ref=48ca775d09ad；/workspace/admin/ 实际服务 app.js?v=20260825-model-pricing-1；admin/admin 登录后 GET account-automation 实测 model_pricing：gpt-5.6-luna priced=true（0.2/0.02/1.2），其余五模型 priced=false；当前页 case 为 luna+mini 混合（EC2 旧代码仍在产 mini 条目），成本 $— 与 page cost_usd_available=false 符合全有或全无契约。"
        }
      ],
      "source_refs": [
        "backend/services/llm_pricing.py",
        "backend/main.py",
        "ui/workspace-ui/admin/app.js",
        "ui/workspace-ui/admin/styles.css",
        "ui/workspace-ui/admin/index.html"
      ],
      "created_at": "2026-08-25",
      "updated_at": "2026-08-25",
      "history": [
        {
          "at": "2026-08-25",
          "event": "created",
          "summary": "用户提供 luna 官方价格页 URL 并要求把使用的模型与 input/cached/output 价格放到 /workspace/admin 的 Automated Cases 页。任务号 p2-117（p2-116 已被并行链 PR#939 占用）。"
        },
        {
          "at": "2026-08-25",
          "event": "updated",
          "summary": "实现经 PR#940 合并（root 前进至 48ca775）；finalize 后会话 shell 第 10 次 ENOENT（未尾接 cd 回根），重启 ZCode 后完成官方栈重启与全部 live 验证（provenance matched、资产 20260825-model-pricing-1、端点 model_pricing 三价实测），翻 done。"
        }
      ],
      "legacy_refs": [
        "p2-107",
        "p2-105"
      ],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "admin-operations",
      "function_id": "admin-case-operations"
    },
    {
      "schema_version": 2,
      "task_id": "p2-118",
      "title": "修复 compose 钉值：persona/enablement classifier 容器默认模型对齐代码默认 gpt-5.6-luna（p2-114 残留）",
      "status": "done",
      "owner": "zac",
      "summary": "p2-114 的残留缺口（用户问\"为什么还有 gpt 5.4\"查出）：deployment/docker-compose.single-host.yml 用 ${VAR:-gpt-5.4-mini} 语法给三个服务块（api/worker 系）钉死 AUTOMATION_PERSONA_MODEL 与 ENABLEMENT_COMPLETION_CLASSIFIER_MODEL 的容器 env 默认值，并以 ${...:-8} 钉死两者超时——env 优先级高于 p2-114 改的代码默认（luna/low/30s/20s），且 root .env 未设这些变量，导致账号 case 的 automation_persona/enablement classifier 阶段实际仍调 gpt-5.4-mini（DB 逐条记录实锤：08-24 10:07-11:11 的 case extractor=luna 但 persona=mini，同一次运行）。后果：case 恒混合 luna+mini→成本列 $—（全有或全无）。修复：compose 12 行默认值对齐代码默认（两个 MODEL gpt-5.4-mini→gpt-5.6-luna、AUTOMATION_PERSONA_TIMEOUT_SECONDS 8→30、CLASSIFIER_TIMEOUT 8→20，三个服务块各 4 行），test_single_host_compose.py 断言同步；INTENT_ROUTER:-mini 故意保留（客户端流 p2-114 定案），KNOWLEDGE_INGESTION/RAG_QUERY_EXPANSION/RAG_CONTEXT_COMPRESSION/REQUEST_BODY_ANALYZER 等范围外不动。本地与 EC2 共用该文件，EC2 下次部署自动生效。",
      "next_action": "已 done（官方栈 09d9820160fa 运行含本任务，容器 env 实测通过）。用户侧：EC2 下次部署（--skip-split）自动携带同一 compose 默认值；此后新 /account case 的 token_by_model 应只剩 openai:gpt-5.6-luna、成本列显示真实金额（历史混合 case 仍 $— 属预期）。",
      "acceptance_criteria": [
        "compose 三个服务块的 AUTOMATION_PERSONA_MODEL/ENABLEMENT_COMPLETION_CLASSIFIER_MODEL 默认值为 gpt-5.6-luna，超时默认 30/20，与 llm_profiles 代码默认一致。",
        "INTENT_ROUTER_MODEL 及范围外模型钉值不变。",
        "重启后容器 env 实测两变量为 gpt-5.6-luna；此后新 /account case 的 token_by_model 只剩 openai:gpt-5.6-luna，成本列显示真实金额。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Compose contract suites",
          "command": "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_single_host_compose.py backend/tests/test_llm_profiles.py -q",
          "details": "45 passed（合并前在任务 worktree 跑）。断言更新：worker_aux 块 ENABLEMENT_COMPLETION_CLASSIFIER_MODEL 默认 gpt-5.6-luna；compose 6 处 MODEL 行换 luna、6 处 TIMEOUT 行换 30/20；INTENT_ROUTER 与范围外模型钉值未动。"
        },
        {
          "type": "deployment",
          "label": "Post-merge official stack live verification",
          "command": "podman inspect deployment_worker_aux_1 --format '{{.Config.Env}}'",
          "details": "PR#943 合并后官方栈运行 root main 09d9820（09d9820160fa）：/health ok、build_provenance_status=matched；容器 env 实测 AUTOMATION_PERSONA_MODEL=gpt-5.6-luna、AUTOMATION_PERSONA_TIMEOUT_SECONDS=30、ENABLEMENT_COMPLETION_CLASSIFIER_MODEL=gpt-5.6-luna、ENABLEMENT_COMPLETION_CLASSIFIER_TIMEOUT_SECONDS=20、INTENT_ROUTER_MODEL=gpt-5.4-mini（故意保留）。诊断证据：supportportal.support_account_case_llm_usage 逐条记录显示 08-24 10:07-11:11 的 case extractor=luna 但 automation_persona=gpt-5.4-mini（同次运行，env 钉值所致），08:59 前为旧代码全 mini 历史。"
        }
      ],
      "source_refs": [
        "deployment/docker-compose.single-host.yml",
        "backend/tests/test_single_host_compose.py"
      ],
      "created_at": "2026-08-25",
      "updated_at": "2026-08-25",
      "history": [
        {
          "at": "2026-08-25",
          "event": "created",
          "summary": "诊断发现 compose ${VAR:-旧默认} 钉值覆盖 p2-114 代码默认（.env 未设→compose 默认生效；p2-114 只查了 .env 没查 compose），DB 记录实锤 persona/classifier 仍跑 mini。用户确认修复。"
        },
        {
          "at": "2026-08-25",
          "event": "updated",
          "summary": "实现经 PR#943 合并（root 前进至 09d9820）；官方栈重启后容器 env 实测两模型默认已是 gpt-5.6-luna、超时 30/20，provenance matched；finalize 尾接 cd 成功保住 shell。翻 done。"
        }
      ],
      "legacy_refs": [
        "p2-114",
        "p2-117"
      ],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
    },
    {
      "schema_version": 2,
      "task_id": "p2-119",
      "title": "Production Automation 分类邮件通知",
      "status": "active",
      "owner": "codex",
      "summary": "Production 中每个满足 active Automation 执行条件的 Account Case，在分类结果持久化事务内幂等创建独立邮件 outbox，向 xieziling@agora.io 发送可信 Zendesk Case 链接、客户问题和 canonical classification path；staging、非 active Automation 和 classification-only 路由不触发。",
      "next_action": "finalize 到 main，重启官方栈并用受控 Production Case 完成 Graph 与邮箱 readback。",
      "acceptance_criteria": [
        "Production active Automation Case 只创建一条分类邮件通知，收件人为 xieziling@agora.io，内容包含可信 Zendesk Case 链接、原始客户问题和 canonical classification path。",
        "重复保存、重复分类、worker 重启和并发 claim 不产生重复邮件；通知创建与 Case upsert 在同一事务内完成。",
        "staging、非 active Automation、detailed_invoice、quota、unregistered 和缺少可信 Zendesk source 的 Case 不发送错误邮件，并保留可审计失败状态。",
        "Graph 200/202 标记 delivered；明确错误标记 failed；网络或 5xx 结果未知标记 outcome_unknown，禁止自动盲目重发。",
        "邮件失败不回滚或重放已有 Zendesk side effect。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Production Automation classification email regression",
          "command": "/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/pytest -q backend/tests/test_production_automation_classification_email.py backend/tests/test_account_intake.py backend/tests/test_automation_production_runtime_contract.py backend/tests/test_worker.py backend/tests/test_repository_configuration.py backend/tests/test_automation_routing.py backend/tests/test_account_route_pipeline.py",
          "details": "fresh suite 合计 458 项通过；覆盖 active Automation 匹配、分类路径与客户问题、可信 Case 链接、幂等 outbox、Graph 成功/失败/未知状态和既有 Account/worker/repository/routing 回归。"
        },
        {
          "type": "document",
          "label": "Project records and generated overview",
          "command": "python3 scripts/verify_feature_list.py; python3 scripts/generate_project_overview.py --write; python3 scripts/generate_project_overview.py --check; git diff --check; python3 -m compileall -q backend",
          "details": "Feature list、Project Overview 生成与检查、差异空白检查和 Python compile check 均通过。"
        },
        {
          "type": "document",
          "label": "Implemented plan review",
          "command": "review-implemented-plan skill",
          "details": "review 未发现需修复的功能性问题。"
        }
      ],
      "source_refs": [
        "backend/services/automation_routing.py",
        "backend/services/account_route_pipeline.py",
        "backend/repositories/ticket_repository.py",
        "backend/worker.py",
        "backend/sql/migrations/2026_08_25_production_automation_classification_emails.sql",
        "backend/tests/test_production_automation_classification_email.py"
      ],
      "created_at": "2026-08-25",
      "updated_at": "2026-08-25",
      "history": [
        {
          "at": "2026-08-25",
          "event": "created",
          "summary": "实现 Production Automation 分类邮件通知的独立 outbox、Graph Mail worker 投递和幂等验证。"
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
      "task_id": "p2-120",
      "title": "Automated Cases 页显示 cached token 数量（p2-107/p2-117 后续）",
      "status": "active",
      "owner": "zac",
      "summary": "用户要求显示 cached token 数量（预期多轮 case 交流会出现前缀缓存命中）。p2-107 已采集 cached（DB 列 cached_input_tokens、aggregate_usage_ledger 分桶与 total_cached_input_tokens、RAG 批量端点透传），但 admin 展示层无任何 cached 输出。本任务补齐展示链路（数据两侧已就绪，纯透传+渲染）：main.py _merge_account_case_token_by_model 分桶加 cached_input_tokens 累加；_attach_account_case_token_usage 的 rag/automation sources、每 case token_usage、page_total 均加 total_cached_input_tokens；admin 前端 Tokens 单元格 cached>0 时显示 \"· N cached\" 小字（admin-token-cached 样式）、by-model 表加固定 Cached 列（In 之后）、Page tokens 页合计 cached>0 时追加；版本串 bump 20260825-cached-display-1。cached 参与成本计算自 p2-107 已有，本任务不改计价。当前实测 cached 全 0（OpenAI 自动前缀缓存未命中），显示为 0/隐藏属预期，多轮命中后自然出现。",
      "next_action": "实现合并后按流程重启官方栈并 live 验证（/health+build ref+provenance matched+资产 20260825-cached-display-1+端点 token_usage 含 total_cached_input_tokens 与 by-model cached）；p2-120 翻 done。EC2 随用户下次部署自动生效。",
      "acceptance_criteria": [
        "端点 token_usage 含 total_cached_input_tokens（case/两 source/page_total 四处），by_model 分桶含 cached_input_tokens（RAG+automation 合并累加）。",
        "前端：单元格与页合计 cached>0 时显示 · N cached；by-model 表固定 Cached 列（0 也显示）。",
        "既有字段只增不改；计价行为不变；相关测试全绿。"
      ],
      "blockers": [],
      "evidence": [],
      "source_refs": [
        "backend/main.py",
        "ui/workspace-ui/admin/app.js",
        "ui/workspace-ui/admin/styles.css",
        "ui/workspace-ui/admin/index.html"
      ],
      "created_at": "2026-08-25",
      "updated_at": "2026-08-25",
      "history": [
        {
          "at": "2026-08-25",
          "event": "created",
          "summary": "用户问\"cached 的 token 数量呢\"并确认需要显示（多轮交流会有 cached）。任务号 p2-120（p2-119 已被并行链占用）。"
        }
      ],
      "legacy_refs": [
        "p2-107",
        "p2-117"
      ],
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
      "status": "active",
      "owner": "unassigned",
      "summary": "SupportPortal 将 Production Non automated Engineer Case 直接发送到固定 Slack Channel 并持久化 thread binding；n8n 只负责固定 Team/Channel/thread 的入站校验、幂等和 AI 转发。",
      "next_action": "部署 direct Slack 配置并激活两个 n8n inbound workflow，随后用新的 Production Non automated Case 完成真实 thread、两阶段批准和 Zendesk public readback 验收。",
      "acceptance_criteria": [
        "Production Non automated Case 只在 SupportPortal Production 环境配置的固定 Slack Channel 创建一个 thread。",
        "其他频道、无绑定 thread、无 app mention、bot/edit/delete 事件只 ACK，不调用 SupportPortal 或 AI。",
        "Slack 指导、AI 草稿、guardrail、批准、Zendesk 客户评论和发布结果在同一 Case thread 幂等闭环。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "summary": "Latest-main regression passed with direct Slack sender, event-backed binding, fail-closed worker, resolver API, inbound-only n8n workflows, intake, Workspace, Zendesk delivery, repository and deployment coverage: 583 tests and 37 subtests.",
          "ref": "backend/tests/test_engineer_slack.py, backend/tests/test_engineer_slack_workflows.py, backend/tests/test_investigation_flow.py, backend/tests/test_account_intake.py, backend/tests/test_account_zendesk_comment_sync.py, backend/tests/test_worker.py"
        },
        {
          "type": "document",
          "summary": "Retained only redacted app-mention/interaction n8n exports and inbound ledger SQL; SupportPortal owns direct outbound and durable thread bindings.",
          "ref": "docs/integrations/n8n/"
        },
        {
          "type": "decision",
          "summary": "Approved Slack Team/Channel and temporary User OAuth outbound identity are known; live acceptance remains pending deployment, n8n inbound activation and a new Production Zendesk test ticket. No secret is tracked.",
          "ref": "docs/integrations/n8n/engineer_case_slack_runbook.md"
        }
      ],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-24",
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
        },
        {
          "at": "2026-08-24",
          "event": "started",
          "summary": "按批准方案开始实施 Production Non automated Engineer Case 的固定 Slack Channel/thread 双向协作与审批发布闭环。"
        },
        {
          "at": "2026-08-24",
          "event": "architecture_updated",
          "summary": "按用户确认调整为 SupportPortal 直接发送 Slack、n8n 仅控制入站消息与交互。"
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
      "status": "active",
      "owner": "unassigned",
      "summary": "将新的 Production not_automated Account Case 全量创建为 Engineer Case 并进行 Round Robin 派单；human_review_required 与手动 route-back 保持原流程。",
      "next_action": "部署 SupportPortal direct Slack 配置后完成新的 Production 测试工单 intake readback，确认 Engineer Case、assignment、root Slack event 和外部 root message 在重放下各只产生一次；随后与 p2-68 一起进行 live thread 验收。",
      "acceptance_criteria": [
        "每个新的 Production not_automated Account Case 创建且仅创建一个 active Engineer Case。",
        "重复 intake 不重复创建、派单或 Slack root event。",
        "staging、human_review_required 和手动 route-back 不创建本流程的 Slack event。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "summary": "Production not_automated intake creates one active Engineer shell, one round-robin dispatch and one root outbox event; staging and excluded routes remain unchanged.",
          "ref": "backend/tests/test_account_intake.py"
        },
        {
          "type": "test",
          "summary": "Latest-main intake, Engineer flow, repository, in-memory/PostgreSQL comment-sync, worker and runtime contract regression passed: 583 tests and 37 subtests with ENGINEER_MULTI_AGENT_ENABLED=true.",
          "ref": "backend/tests/test_investigation_flow.py, backend/tests/test_account_zendesk_comment_sync.py, backend/tests/test_worker.py"
        },
        {
          "type": "decision",
          "summary": "Ticket 12967 已是既有 Account Case 且没有 Engineer Case，不适合验证新 intake；需在 direct Slack 配置 ready 后创建新的 Production 测试工单。",
          "ref": "docs/integrations/n8n/engineer_case_slack_runbook.md"
        }
      ],
      "source_refs": [
        "docs/roadmap.html#lanes"
      ],
      "created_at": "2026-08-16",
      "updated_at": "2026-08-24",
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
        },
        {
          "at": "2026-08-24",
          "event": "started",
          "summary": "按批准范围从已移除的 10% 占位切换到 Production not_automated 全量 Engineer Case 创建，明确排除 human_review_required。"
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
      "status": "done",
      "owner": "zac",
      "summary": "在同一 single-host 部署内新增第二组 api/worker 容器（compose profile production 门控），指向独立数据库 supportportal_production；新增 /production UI（功能与 /account 相同、移除 Run in Production）；nginx 以路径路由 /production、/production/api 与 intake POST /production/account；production 栈 intake 直接以 processing_profile=production 创建工单并沿用现有 delivery 台账自动投递 Zendesk internal comment；/account staging 行为零改动。",
      "next_action": "",
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
        },
        {
          "type": "deployment",
          "label": "Official stack restart + live markers",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && podman exec deployment_api_1 python -c \"import urllib.request; html=urllib.request.urlopen('http://127.0.0.1:8000/production/', timeout=10).read().decode(); print('\u003ctitle>Account Production\u003c/title>' in html)\"",
          "details": "2026-08-20 官方栈重启成功，/health app_build.ref=5318360e267f 与合并后 main HEAD 一致；/production 页面由 api 挂载返回且标题为 Account Production（资源版本串已被后续 automated-public 工作更新为 20260819-automated-public-1，与当前 main 一致）。EC2 侧已部署并可访问 /production/（用户确认），production 库/容器组随 profile 生效。"
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
      "updated_at": "2026-08-20",
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
        },
        {
          "at": "2026-08-20",
          "event": "completed",
          "summary": "官方栈重启（app_build.ref=5318360e267f）与 live marker 验证通过，标记完成。"
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
      "status": "done",
      "owner": "zac",
      "summary": "移除 staging 库内 promote-production 端点与 PRD-* 晋级逻辑；/account 的 Run in Production 按钮改为以 n8n 同款五字段 intake 直连 POST /production/account，由 production 栈完成完整路由并在命中已注册 Automation 时自动写入 Zendesk internal comment。nginx intake 路由超时提升到 300s 匹配前端等待。",
      "next_action": "",
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
        },
        {
          "type": "deployment",
          "label": "Official stack restart + live markers",
          "command": "podman exec deployment_api_1 python -c \"import urllib.request, urllib.error; js=urllib.request.urlopen('http://127.0.0.1:8000/account/app.js', timeout=10).read().decode(); print('forward' in js)\" ; POST /api/account/cases/AC-X/promote-production -> 404",
          "details": "2026-08-20 官方栈（app_build.ref=5318360e267f）：/account app.js 含 forwardAccountCaseToProduction 且无 promote-production 残留；后端 promote-production 端点已删除（404）。端到端转发在 EC2 production 环境运行（本地栈不启用 production profile，属设计行为）。"
        }
      ],
      "source_refs": [
        "ui/account-ui/app.js",
        "backend/main.py",
        "deployment/nginx/supportportal.conf",
        "backend/tests/test_account_ui_contract.py"
      ],
      "created_at": "2026-08-19",
      "updated_at": "2026-08-20",
      "history": [
        {
          "at": "2026-08-19",
          "event": "created",
          "summary": "将 /account 的 Run in Production 从 staging 库内晋级重构为转发到 /production 独立环境。"
        },
        {
          "at": "2026-08-20",
          "event": "completed",
          "summary": "官方栈重启（app_build.ref=5318360e267f）与 live marker 验证通过，标记完成。"
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
    },
    {
      "schema_version": 2,
      "task_id": "p2-78",
      "title": "内部邮件按环境命名空间隔离与跨环境回复终态忽略",
      "status": "done",
      "owner": "zac",
      "summary": "staging 与 production 共用同一 Graph 内部邮箱导致双栈消费同一封回复（曾引发跨环境误完成与每分钟重试噪音）。为内部邮件主题增加环境命名空间标签（production 为空、staging 为 [staging]，形如 [staging][Enablement Request]），轮询匹配改为剥离 Re:/FW: 后的锚定前缀匹配；跨环境 not-found 回复改为终态 dismiss，单条坏消息不再中断整个收件箱轮询周期。",
      "next_action": "",
      "acceptance_criteria": [
        "production 内部邮件主题保持不变；staging 主题携带 [staging] 前缀标签。",
        "staging worker 只消费 [staging] 前缀回复，production worker 只消费无标签前缀回复；锚定匹配使子串误匹配不可能发生。",
        "跨环境回复（对端 case 不存在/ handler 不匹配/缺 ticket）被终态 dismiss，不再每分钟重试。",
        "单条 handler 异常不中断当轮收件箱处理，失败消息保持未读。",
        "compose 层 staging 服务默认 [staging]、production 服务强制为空，单一 .env 可同时驱动两套环境。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Namespaced internal email suite",
          "command": ".venv/bin/python -m pytest -q backend/tests/test_internal_email_namespace.py backend/tests/test_worker.py backend/tests/test_billing_automation_email.py backend/tests/test_enablement_automation.py backend/tests/test_account_verification_automation.py backend/tests/test_account_intake.py backend/tests/test_repository_configuration.py",
          "result": "425 passed with 39 subtests."
        },
        {
          "type": "deployment",
          "label": "Live isolation verification",
          "command": "deploy_ec2.sh + restart_single_host_stack.sh; container env probe; worker logs",
          "result": "PR #815 + #816 deployed (main 6a30eb1; local stack health ref 6a30eb11d5b9). Staging container namespace '[staging]' -> subject '[staging][Enablement Request]'; production container empty -> unchanged subject. Staging worker noise for ticket 12872 stopped immediately (namespace filter); production 12804 claim-time loop terminated via terminal dismissal (0 warnings in the last minute)."
        }
      ],
      "source_refs": [
        "backend/services/internal_email_template.py",
        "backend/services/billing_automation.py",
        "backend/services/enablement_automation.py",
        "backend/worker.py",
        "deployment/docker-compose.single-host.yml"
      ],
      "created_at": "2026-08-20",
      "updated_at": "2026-08-20",
      "history": [
        {
          "at": "2026-08-20",
          "event": "started",
          "summary": "用户确认单邮箱方案：主题命名空间 [staging][xxx]；n8n 侧已由用户另行修复。"
        },
        {
          "at": "2026-08-20",
          "event": "completed",
          "summary": "命名空间隔离上线并 live 验证：staging 主题带 [staging] 标签且两套 worker 互不消费；claim 期 not-found 也终态 dismiss（#816），12804/12872 双侧循环噪音全部消失。"
        }
      ],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-79",
      "title": "Production Fraud 与 Account Suspension handoff Slack 通知",
      "status": "active",
      "owner": "zac",
      "summary": "Production Fraud Account 和 Account Suspension 的最终 handoff 客户回复经 Zendesk public delivery 确认后，通过持久化 outbox 和 n8n 幂等投递到 Slack；Slack 失败与客户回复、Zendesk solved 和本地 Case close 独立。",
      "next_action": "激活 n8n 的 SupportPortal Account Handoff Status GET workflow；随后用 Prod worker 做 status readback，并确认 delivered/failed/outcome_unknown/missing 对账路径。",
      "acceptance_criteria": [
        "Fraud 仅 fraud_handoff_confirmation、Suspension 仅 account_suspension_handoff_and_close 在 public Zendesk delivered 后创建一次 Slack 投递；contact confirmation 和其他 Automation 不触发。",
        "Slack 消息包含 Case 类型和标题、Zendesk ticket link、持久化 question summary，不包含客户身份、collected fields、AI reply 或凭据。",
        "SupportPortal 以 event_id 持久化 outbox；n8n 以 PostgreSQL primary key 原子去重，POST 超时后只查 status，只有 missing 才重新入队。",
        "n8n 或 Slack 失败不回滚客户回复、Zendesk solved、本地 Case close，也不转 Human Review。",
        "配置只注入 Production API 和 auxiliary worker；缺失配置由 Production health warning 和结构化日志暴露。"
      ],
      "blockers": [
        "n8n production GET status webhook /webhook/supportportal/account-handoff/slack/status is not registered; activate the status workflow before final reconciliation acceptance."
      ],
      "evidence": [
        {
          "type": "test",
          "label": "Account handoff Slack outbox, n8n and Zendesk independence suite",
          "command": "TICKET_DB_DSN=postgresql://example.invalid/test SENTIMENT_PROVIDER=legacy .venv/bin/python -m pytest -q backend/tests/test_account_slack_n8n.py backend/tests/test_runtime_bootstrap.py backend/tests/test_repository_configuration.py backend/tests/test_account_zendesk_internal_comment_service.py backend/tests/test_worker.py",
          "details": "239 tests passed with 22 subtests after owner review; verified exact message and POST/GET contracts, case-action plus reply-intent trigger matrix, public-delivered release gate, private/failed/unknown non-release, concurrent claim deduplication, unknown-outcome status-only reconciliation, missing-only requeue, Slack failure independence, and X-N8n-Request-Token header propagation from n8n_request_token."
        },
        {
          "type": "deployment",
          "label": "Production EC2 deployment and runtime configuration",
          "command": "ssh zacbot 'cd /home/ubuntu/SupportPortal && ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space'",
          "details": "Deployment completed successfully. Public /health returned HTTP 200 with app_build.ref=2166840d5e90 and runtime_profile=full. api_production and worker_aux_production run localhost/supportportal-app:2166840d5e90; both have n8n_request_token, ACCOUNT_SLACK_N8N_WEBHOOK_URL, and ACCOUNT_SLACK_N8N_STATUS_URL set without exposing values."
        },
        {
          "type": "test",
          "label": "Production n8n client synthetic delivery and replay",
          "command": "docker exec deployment-worker_aux_production-1 python - (synthetic event; executed on zacbot)",
          "details": "The formal Production worker client sent a synthetic fraud_handoff_confirmation event and received delivered; replaying the identical event_id also returned delivered, confirming the authenticated POST path and n8n event-idempotent replay response. GET status remains unavailable because the production status webhook is not registered."
        }
      ],
      "source_refs": [
        "backend/services/account_slack_n8n.py",
        "backend/repositories/ticket_repository.py",
        "backend/worker.py",
        "backend/sql/migrations/2026_08_20_account_handoff_slack_delivery.sql",
        "deployment/docker-compose.single-host.yml",
        "docs/integrations/n8n/account_automation_slack_notification.md"
      ],
      "created_at": "2026-08-20",
      "updated_at": "2026-08-20",
      "history": [
        {
          "at": "2026-08-20",
          "event": "created",
          "summary": "建立 Production Account handoff Slack 独立投递链路，采用 SupportPortal outbox 与 n8n PostgreSQL 幂等状态查询。"
        },
        {
          "at": "2026-08-20",
          "event": "progress",
          "summary": "SupportPortal outbox、n8n client、Production 配置、集成文档和目标测试完成；等待 finalize、部署与外部读回。"
        },
        {
          "at": "2026-08-20",
          "event": "progress",
          "summary": "Production Slack client 与 API/auxiliary worker 已统一使用 n8n_request_token 和 X-N8n-Request-Token；POST webhook synthetic delivery 已读回 delivered，status workflow 仍需发布后完成未知结果对账验收。"
        },
        {
          "at": "2026-08-20",
          "event": "progress",
          "summary": "EC2 已部署 main 2166840d5e90，Production API/auxiliary worker 的 n8n URL 与 token 已注入；Prod worker synthetic POST 首次与相同 event_id 重放均 delivered。n8n GET status workflow 仍未激活，保留 active 等待 status readback。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "engineer-workspace",
      "function_id": "engineer-case-delivery"
    },
    {
      "schema_version": 2,
      "task_id": "p2-80",
      "title": "修复 Zendesk AI 接管撞上 omnichannel 路由窗口时被固化成永久失败",
      "status": "done",
      "owner": "zac",
      "summary": "Production 自动接管 gate 在工单创建后数秒内执行时，可能撞上 Zendesk omnichannel 路由引擎的分配占有窗口而被 422 拒绝；现行代码把一切 422 归为 permanent 且丢弃 Zendesk 错误体，导致瞬时冲突被固化为 human_review 永久失败（AC-12878 实例）。修复：捕获并持久化 Zendesk 错误体（failure_detail），gate 对 422 做有界退避重试（默认 20s/40s，env 可调），每次重试重新快照、复查 human_replied 等策略阻断。",
      "next_action": "",
      "acceptance_criteria": [
        "zendesk_ticket_assignment 的 HTTPError 处理捕获 Zendesk 错误体（best-effort），以 detail 附加在 ZendeskCommentError 上并不再丢弃。",
        "ownership gate 对 422 按默认 20s/40s（ZENDESK_OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_SECONDS 可调）退避重试；每次重试前重新读取快照（盖新 updated_stamp）、复查策略阻断与已分配状态；重试期间人工回复则按 policy 阻断停机。",
        "ownership 结果/事件（zendesk_ai_ownership event 与 automation_context.zendesk_ownership）持久化 failure_detail；手动 Take Ownership 端点的错误响应透出该详情。",
        "重试成功路径返回 assigned；持续 422 最终落 failed 并带 failure_detail；非 422 错误行为不变。",
        "现有 ownership/assignment/worker/intake 测试全部保持通过。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Ownership retry + detail persistence",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_automation_ownership backend.tests.test_zendesk_ticket_assignment",
          "details": "27 全绿：422 重试成功（重取快照+复PUT 后 assigned）；持续 422 三次尝试后 failed 且 automation_context 持久化 failure_detail；重试窗口出现人工回复按 policy 停机且不再 PUT；HTTPError 错误体解析为 detail；原有 409 冲突恢复与禁用重试（env 置空）语义保持。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_account_intake backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_assignment backend.tests.test_account_reply_publication_postgres backend.tests.test_zendesk_comments",
          "details": "290 通过、8 跳过（无活库 Postgres 集成用例，与改动前一致）：delivery/verify 流、intake gate 包装、手动 assignment 端点、评论服务全部不受影响。"
        },
        {
          "type": "test",
          "label": "Syntax gates",
          "command": "python3 -m py_compile backend/services/account_automation_ownership.py backend/services/zendesk_ticket_assignment.py backend/services/zendesk_comments.py backend/main.py && git diff --check",
          "details": "四个改动文件编译与空白检查通过。"
        },
        {
          "type": "deployment",
          "label": "Official stack restart + live markers",
          "command": "podman exec deployment_api_1 python -c \"from backend.services.account_automation_ownership import DEFAULT_ASSIGNMENT_RETRY_DELAYS, _assignment_retry_delays; from backend.services.zendesk_ticket_assignment import _http_error_detail; print(DEFAULT_ASSIGNMENT_RETRY_DELAYS, callable(_http_error_detail))\"",
          "details": "2026-08-20 官方栈（app_build.ref=5318360e267f）：运行镜像内默认退避 (20.0, 40.0)、env 解析与错误体捕获函数均在。真实 422 路由窗口重试事件待 EC2 下一次部署后的新工单自然验证（本地栈为 staging，不触发 production gate）。"
        }
      ],
      "source_refs": [
        "backend/services/zendesk_ticket_assignment.py",
        "backend/services/account_automation_ownership.py",
        "backend/services/zendesk_comments.py",
        "backend/main.py",
        "backend/tests/test_account_automation_ownership.py",
        "backend/tests/test_zendesk_ticket_assignment.py"
      ],
      "created_at": "2026-08-20",
      "updated_at": "2026-08-20",
      "history": [
        {
          "at": "2026-08-20",
          "event": "created",
          "summary": "基于 AC-12878 实测调查（接管发生在建单后 15 秒内撞上路由窗口 422；同载荷稍后成功；AI agent 组成员关系与写权限均正常）立项修复。"
        },
        {
          "at": "2026-08-20",
          "event": "completed",
          "summary": "官方栈重启（app_build.ref=5318360e267f）与 live marker 验证通过，标记完成。"
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
      "task_id": "p2-81",
      "title": "Enablement 内部回复完成识别增加 LLM 分类器（正则保底）",
      "status": "done",
      "owner": "zac",
      "summary": "内部邮件回复是否确认开通的判定目前是纯英文正则，中文/typo/自由表达会漏判为完成，导致客户收到倒序的重复回复且工单不自动关闭。新增两级判定：正则命中即完成（零变化快路径）；正则判否时由小模型单次分类仲裁（强制 JSON、温度 0），LLM 确认才走完成+关单路径；任何 LLM 失败/开关关闭一律回退正则结果，永不 fail-open。新增 ENABLEMENT_COMPLETION_CLASSIFIER_* scenario 与总开关，判定来源写入审计事件。",
      "next_action": "",
      "acceptance_criteria": [
        "正则命中的英文完成回复不调用 LLM 直接走完成路径（快路径零变化）。",
        "正则判否时调用 LLM 分类（ENABLEMENT_COMPLETION_CLASSIFIER_* scenario，gpt-5.4-mini/low/温度0/8s/重试1，JSON 强制模式）；LLM confirmed=true 才走 enablement_completed_and_close。",
        "凭证缺失、调用失败、JSON 无效、开关 disabled 均回退 regex_fallback（=现行为 resolution_update），classify 永不抛异常；disabled 时不发起任何 LLM 调用。",
        "判定来源（regex/llm/regex_fallback+原因）记录日志并写入完成路径的 resolution 审计事件。",
        "worker_aux 与 worker_aux_production 的 compose env、.env.example、compose 契约测试同步；存量 regex-negative enablement 测试补 classifier mock，全部回归通过。",
        "docs/prompt_change_log.md 记录 enablement-completion-classifier-v1。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Classifier unit + worker integration + contract",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy OPENAI_API_KEY= .venv/bin/python -m unittest backend.tests.test_enablement_completion_classifier backend.tests.test_worker backend.tests.test_single_host_compose",
          "details": "8 单测（confirmed/llm false/disabled 不调用/missing key/invocation error/非 JSON/非布尔 payload/空 note）+ 93 worker 集成（含新增中文回复升级完成路径、regex 命中不调用分类器、分类器失败保持 resolution_update；存量 regex-negative 测试补 mock）+ compose 契约。空 OPENAI_API_KEY 运行证明测试密闭无真实 LLM 依赖。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy OPENAI_API_KEY= .venv/bin/python -m unittest backend.tests.test_automation_persona backend.tests.test_enablement_automation backend.tests.test_account_intake backend.tests.test_repository_configuration",
          "details": "连同前组合计 462 通过：persona、enablement 自动化、intake、repo 配置不受影响。"
        },
        {
          "type": "test",
          "label": "Syntax gates",
          "command": "python3 -m py_compile backend/worker.py backend/services/enablement_completion_classifier.py backend/services/llm_profiles.py && git diff --check",
          "details": "编译与空白检查通过。"
        },
        {
          "type": "deployment",
          "label": "Official stack restart + live markers",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && podman exec deployment_worker_aux_1 python -c \"from backend.services.enablement_completion_classifier import classify_enablement_completion; print(classify_enablement_completion('已开通', feature_label='Media Relay'))\"",
          "details": "2026-08-20 官方栈重启，/health app_build.ref=ebba123280b5 与合并后 main HEAD 一致；worker_aux 运行镜像内 prompt 版本 enablement-completion-classifier-v1、scenario profile（gpt-5.4-mini/low/温度0）解析正确；用真实凭据对中文 note '已开通' 实测分类返回 completed=True source=llm（真实 LLM 端到端判定成功）。"
        }
      ],
      "source_refs": [
        "backend/services/enablement_completion_classifier.py",
        "backend/services/llm_profiles.py",
        "backend/worker.py",
        "deployment/docker-compose.single-host.yml",
        "backend/tests/test_enablement_completion_classifier.py",
        "backend/tests/test_worker.py"
      ],
      "created_at": "2026-08-20",
      "updated_at": "2026-08-20",
      "history": [
        {
          "at": "2026-08-20",
          "event": "created",
          "summary": "用户确认采用 LLM 分类方案（选项3）：正则保底、LLM 仅升级否→是、失败回退现行为、带免部署开关。"
        },
        {
          "at": "2026-08-20",
          "event": "progress",
          "summary": "完成实现与目标测试（462 全绿），prompt_change_log 已记录 enablement-completion-classifier-v1。"
        },
        {
          "at": "2026-08-20",
          "event": "completed",
          "summary": "PR #827 合并后官方栈重启（build ref ebba123280b5）与 live marker（含真实 LLM 中文判定）验证通过，标记完成。"
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
      "task_id": "p2-82",
      "title": "AI 接管等待 Zendesk 路由窗口 90 秒后再首试，并保留 422 字段级拒绝原因",
      "status": "done",
      "owner": "zac",
      "summary": "AC-12879/AC-12880 实测：建单后 1 秒内 omnichannel 把工单分给真人 agent，其持有期间（超过 1 分钟）所有 assignment PUT 一律 422 RecordInvalid；PR#825 的 0s/20s/40s 重试全部落在窗口内，fail-closed 转 human_review，人工 8 分钟后同载荷 Take Ownership 成功。修复：gate 在首次 assignment PUT 前先等待路由窗口（默认 90s，ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS 可调，0=立即），等待后重取快照（新 updated_stamp）并复查策略阻断；同时 _http_error_detail 把 Zendesk 422 响应体的 details 字段级原因拼进 failure_detail（此前只留 top-level error，需翻 ticket audits 才能定位）。",
      "next_action": "",
      "acceptance_criteria": [
        "gate 模式首次 assignment PUT 前等待 ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS（默认 90，0=禁用）；等待后重新读取快照并用新 updated_stamp 发 PUT。",
        "等待期间出现人工回复/策略阻断则停机且不发 PUT；已分配匹配（verify/复访）路径不受等待影响。",
        "重试语义保持：422 仍按 20s/40s 退避重试至多 3 次尝试；非 422 行为不变。",
        "_http_error_detail 将 422 响应体 details（dict/list 紧凑 JSON 或字符串）追加进 detail（上限 1000 字符），无 details 时行为不变。",
        "现有 ownership/assignment/intake/worker/comment 回归套件全部保持通过。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Initial delay + detail capture",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_automation_ownership backend.tests.test_zendesk_ticket_assignment",
          "details": "32 全绿：默认 90s 等待后重取快照再用新 updated_stamp 发 PUT；等待期间人工回复停机且不发 PUT；已分配匹配路径零等待；env=0 立即分配；422 响应体 details 以紧凑 JSON 追加进 detail（RecordInvalid | {\"assignee_id\":[...]}）。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_account_intake backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_assignment backend.tests.test_account_zendesk_comment_sync backend.tests.test_zendesk_comments",
          "details": "301 通过（worktree 需先 link_worktree_env.sh，否则 intake 分类缺凭证误报）。intake gate 包装、verify 流、手动 assignment、评论服务全部不受影响。"
        },
        {
          "type": "deployment",
          "label": "Local official stack restart + live markers",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && podman exec deployment_api_1 python -c \"from backend.services.account_automation_ownership import DEFAULT_ASSIGNMENT_INITIAL_DELAY, _assignment_initial_delay; from backend.services.zendesk_ticket_assignment import _http_error_detail; print(DEFAULT_ASSIGNMENT_INITIAL_DELAY, _assignment_initial_delay(), callable(_http_error_detail))\"",
          "details": "2026-08-20 官方栈重启（app_build.ref=1020e2e26c9b，/health status=ok）：运行镜像内默认初始延迟 90.0、env 解析 90.0、错误体捕获函数均在。"
        },
        {
          "type": "deployment",
          "label": "EC2 production stack deploy + live markers",
          "command": "ssh zacbot ./deployment/deploy_ec2.sh --branch main（auto-deploy 自动执行）+ docker exec deployment-api_production-1 python -c \"...import DEFAULT_ASSIGNMENT_INITIAL_DELAY, _assignment_initial_delay...\" + curl https://support.stellarix.space/health",
          "details": "2026-08-20 EC2 生产栈（main=1020e2e）：api/worker_query/worker_aux production 容器全部运行 supportportal-app:1020e2e26c9b；域名 /health status=ok build=1020e2e26c9b；生产容器内 DEFAULT_ASSIGNMENT_INITIAL_DELAY=90.0、_assignment_initial_delay()=90.0。真实 90s 等待路径待下一个 production 自动化工单自然验证。"
        }
      ],
      "source_refs": [
        "backend/services/account_automation_ownership.py",
        "backend/services/zendesk_ticket_assignment.py",
        "backend/tests/test_account_automation_ownership.py",
        "backend/tests/test_zendesk_ticket_assignment.py",
        ".env.example"
      ],
      "created_at": "2026-08-20",
      "updated_at": "2026-08-20",
      "history": [
        {
          "at": "2026-08-20",
          "event": "created",
          "summary": "基于 AC-12879/AC-12880 生产调查（omnichannel 路由窗口实测超过 60s，3 次重试全部 422；人工 8 分钟后成功）立项。"
        },
        {
          "at": "2026-08-20",
          "event": "completed",
          "summary": "PR#829 合并（main=1020e2e）；本地官方栈与 EC2 生产栈均部署重启，/health 与 build ref 匹配，镜像内初始延迟 90s marker 验证通过，标记完成。"
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
      "task_id": "p2-83",
      "title": "AI 接管 PUT 自动补 Zendesk 必填字段 SDK Product（为空时默认 video_calling）",
      "status": "done",
      "owner": "zac",
      "summary": "12893 实测（PR#829 的 details 捕获）定位 422 真因：Zendesk 字段级 required 字段 31503099534100 \"SDK Product (Selectable)\" 为空时拒绝一切 API 工单更新（RecordInvalid \"needed\"），与路由窗口/时机无关；此前 12878/12879/12880 的手动成功都是因为人工在 UI 接手时被表单强制填了该字段。Zendesk 侧无法修改（用户确认），改为代码侧：assignment PUT 在该字段为空时自动带上默认值 video_calling，已有值（人工已选）一律不覆盖；快照与手动 GET 路径同样判定。",
      "next_action": "",
      "acceptance_criteria": [
        "assign_ticket_to_configured_ai 的 PUT 在字段 31503099534100 为空/缺失时附带默认值 video_calling；字段已有任何值（如人工选的 voice_calling）时完全不附带，不覆盖人工选择。",
        "ownership_snapshot 路径与手动 GET 路径使用同一判定（custom_fields 中该字段非空才算已填）。",
        "already_assigned 早退路径行为不变；其余 PUT 字段（assignee_id/group_id/safe_update/updated_stamp）语义不变。",
        "现有 ownership/assignment/intake/worker/comment 回归套件全部保持通过。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Autofill payload tests",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_automation_ownership",
          "details": "34 全绿：字段为空时 PUT 附带 custom_fields:[{id:31503099534100,value:video_calling}]；字段已有值（voice_calling）时完全不附带；快照 required_field_missing 两种取值解析正确。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_account_intake backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_assignment backend.tests.test_account_zendesk_comment_sync backend.tests.test_zendesk_comments",
          "details": "301 通过（worktree 需从仓库根执行 link_worktree_env.sh）。"
        },
        {
          "type": "deployment",
          "label": "Local official stack restart + marker",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote && podman exec deployment_api_1 python -c \"from backend.services.zendesk_ticket_assignment import ZENDESK_ASSIGNMENT_REQUIRED_FIELD_ID, ZENDESK_ASSIGNMENT_REQUIRED_FIELD_VALUE; print(...)\"",
          "details": "2026-08-21 官方栈（app_build.ref=0cffc5950cc0，/health ok）容器内常量 31503099534100/video_calling 生效。"
        },
        {
          "type": "deployment",
          "label": "EC2 production deploy + live takeover on 12893",
          "command": "ssh zacbot ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space + docker exec deployment-api_production-1 python -c \"assign_ticket_to_configured_ai(ticket_id='12893')\"",
          "details": "2026-08-21 EC2 生产栈部署 0cffc5950cc0（域名 /health ok）。真实验证链：① 顶层键形式实测被忽略（12893 PUT 仍 422 needed，detail 完整捕获）；② custom_fields 数组形式对 12893 实测 200 并写入 video_calling；③ 部署修正版后经生产容器调用 assign_ticket_to_configured_ai 成功：assignee=48557297720084（AI agent）、group=29388501432596、sdk_product=video_calling。全新工单的 PUT 内自动填充路径待下一个真实 production 自动化工单自然验证。"
        }
      ],
      "source_refs": [
        "backend/services/zendesk_ticket_assignment.py",
        "backend/tests/test_zendesk_ticket_assignment.py"
      ],
      "created_at": "2026-08-21",
      "updated_at": "2026-08-21",
      "history": [
        {
          "at": "2026-08-21",
          "event": "created",
          "summary": "12893 追踪中由 failure_detail 的 details 捕获定位真因（required 字段而非路由窗口），用户决策采用代码侧自动填 video_calling。"
        },
        {
          "at": "2026-08-21",
          "event": "updated",
          "summary": "首版（PR#831）用顶层 \"\u003cfield_id>\": \"\u003cvalue>\" 形式被该 Zendesk 账户静默忽略（12893 实测 PUT 仍报 needed）；改为 custom_fields 数组形式（12893 实测 200 并成功写入 video_calling）。"
        },
        {
          "at": "2026-08-21",
          "event": "completed",
          "summary": "PR#831（顶层键形式）+ PR#832（custom_fields 数组修正）合并；EC2 生产栈部署 0cffc5950cc0，12893 实测接管成功且字段写入 video_calling，标记完成。"
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
      "task_id": "p2-84",
      "title": "fraud_account 公开回复发布后将 Zendesk 工单 handoff 给 xieziling 复审",
      "status": "done",
      "owner": "zac",
      "summary": "fraud_account 自动化流程的首次公开回复（\"已转相关团队，24 小时内联系\"）发布后，工单需要人工复审。新增：worker 在 production fraud_account 案的 public 评论投递成功后，用现有 AI agent 凭证把 Zendesk 工单 assign 给 ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID（=31116634341396 即 xieziling@agora.io；assign 权限 PUT 200 已实测，但该 token 无按 email 搜索用户的权限，users/search 403、show_many 空，GET /users/{id}.json 可用，故按数字 id 配置）。权限试探在 12895 上先行验证通过；handoff 失败不回滚已发布回复，记录 zendesk_fraud_review_handoff 事件（assigned/already_assigned/failed/skipped）+ 日志。",
      "next_action": "",
      "acceptance_criteria": [
        "worker：仅 production + fraud_account + is_public 的投递成功后触发 handoff；internal 投递与非 fraud 案不触发。",
        "assign_ticket_to_reviewer：按数字 user id 解析 reviewer（GET /users/{id}.json，必须 active agent），assign 到其 default group；已 assign 则 no-op；复用 safe_update 与 required-field autofill 语义。",
        "handoff 结果记录 zendesk_fraud_review_handoff 事件与结构化日志；失败/缺配置为 owner 可见信号，不影响已发布回复。",
        "现有 assignment/ownership/worker/intake/comment 回归套件全部保持通过。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Handoff service + worker hook tests",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_zendesk_ticket_assignment backend.tests.test_worker",
          "details": "115 全绿：按 id 解析 reviewer（非数字 id、inactive agent 均 fail-closed）、assign 到 default group 的 PUT payload、already-assigned no-op；worker 侧 public+fraud 触发 / internal、非 fraud、缺配置不触发；handoff 失败记录 failed 事件且不影响已发布回复。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_automation_ownership backend.tests.test_account_intake backend.tests.test_account_zendesk_comment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_assignment backend.tests.test_account_zendesk_comment_sync backend.tests.test_zendesk_comments",
          "details": "229 通过。"
        },
        {
          "type": "deployment",
          "label": "Local official stack + EC2 production deploy",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote；ssh zacbot ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space",
          "details": "2026-08-21 双栈部署 ba2a44d3d67c（PR#835），/health ok；本地与 EC2 生产容器内 assign_ticket_to_reviewer 可导入、ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID=31116634341396 已注入 worker。"
        },
        {
          "type": "deployment",
          "label": "Live permission + function verification",
          "command": "docker exec deployment-api_production-1 python -c \"assign_ticket_to_reviewer(ticket_id='12895', reviewer_user_id='31116634341396')\"",
          "details": "前置权限试探（12895 手动 PUT assignee=xieziling+group=Tier 2 CSE → 200，AI agent token 有 assign 权限，无需 Admin）；部署后函数级验证：GET /users/{id}.json 解析 + ticket 比对 → already_assigned（200）。注：AI agent token 无 users/search（403）与 show_many?emails（空）权限，故按 id 配置。完整自动链路（fraud public 回复发布 → 自动 handoff 事件）待下一个真实 fraud_account 工单自然验证。已知边界：已 solved 工单的任何 API 更新被声明式 checkbox 36379228408724 拦截（12893 实测 422，detail 明确）；fraud 回复后工单为 pending，不受影响。"
        }
      ],
      "source_refs": [
        "backend/services/zendesk_ticket_assignment.py",
        "backend/worker.py",
        "backend/tests/test_zendesk_ticket_assignment.py",
        "backend/tests/test_worker.py",
        ".env.example"
      ],
      "created_at": "2026-08-21",
      "updated_at": "2026-08-21",
      "history": [
        {
          "at": "2026-08-21",
          "event": "created",
          "summary": "用户提出 fraud 回复后转人工复审；先用 12895 实测 AI agent 凭证可 assign 给 xieziling（PUT 200）后立项实现。"
        },
        {
          "at": "2026-08-21",
          "event": "updated",
          "summary": "PR#834 首版按 email 搜索解析 reviewer，实测该 token users/search 403/show_many 空；改为 ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID 数字 id 解析（/users/{id}.json 实测 200 含完整记录）。"
        },
        {
          "at": "2026-08-21",
          "event": "completed",
          "summary": "PR#834+PR#835 合并（main=ba2a44d），双栈部署，权限与函数级 live 验证通过，标记完成。"
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
      "task_id": "p2-85",
      "title": "n8n 同步 Zendesk 工单状态到 /account 与 /production 并联动关闭本地 case",
      "status": "done",
      "owner": "zac",
      "summary": "n8n 监听 Zendesk 工单状态变更事件后，通过新的集成端点把状态推回 SupportPortal。新增 PUT /api/integrations/zendesk/account-cases/{zendesk_ticket_id}/status（X-Zendesk-Account-Sync-Token 认证，与 comment sync 同模式）：n8n 对 staging 源与 production 源各调一次已有 comment-sync-target 归属检测，命中者接收推送；端点幂等可重放，带旧 updated_at 的事件 stale_ignored。solved/closed 在同一事务联动关闭：support_tickets.status=resolved + closed_at（与 p1-51 solved 读回同语义）+ automation_status=closed（AI 自动回复停机，UI 手动回复不受影响），prior automation_status 存 automation_context 快照；Zendesk 重开时恢复。support_account_cases 新增 zendesk_ticket_status / zendesk_status_updated_at / zendesk_status_synced_at 三列（迁移双库执行）。/account 与 /production UI 列表徽章 + 详情 meta 行显示 Zendesk 状态；n8n ISO-8601 offset 时间在共享 status-sync API 边界规范化为 UTC。",
      "next_action": "",
      "acceptance_criteria": [
        "PUT status 端点：token 认证（401/503 语义与 comment sync 一致）、404 非本栈工单、422 非法状态；重复同状态返回 unchanged，旧 updated_at 返回 stale_ignored，可安全重放。",
        "updated_at 接受带时区 offset 的 ISO-8601 timestamp 并在比较/存储前规范化为 UTC；非法日期与将 ticket id 映射到 zendesk_status 均返回 422。",
        "solved/closed 联动：同事务置 support_tickets.status=resolved + closed_at + automation_status=closed，并记录 prior 快照；solved→非 solved 重开时恢复 prior automation_status。",
        "审计事件 account_zendesk_status_synced（actor zendesk_n8n）仅在状态实际变化时记录。",
        "comment-sync-target 响应新增 status_endpoint 字段。",
        "account-ui 与 production-ui：列表项 Zendesk 状态徽章（空值不显示）+ 详情 meta-grid「Zendesk 状态」行（徽章+同步时间），文字始终存在。",
        "迁移对 supportportal 与 supportportal_production 两库执行；相关回归套件保持通过。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Status sync endpoint + repository transition tests",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_status_sync backend.tests.test_account_zendesk_status_sync_postgres",
          "details": "9 全绿：token 401/422/404、solved 联动关闭（resolved+closed_at+automation_status=closed+prior 快照+审计事件）、unchanged/stale_ignored、重开恢复 automation、status_endpoint 字段、summary/detail payload 带出新字段；Postgres 契约（同事务 SQL 参数数、重开不写工单、unchanged/stale 零写入、缺案 KeyError）。"
        },
        {
          "type": "test",
          "label": "n8n offset timestamp boundary",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_status_sync",
          "details": "覆盖 n8n 的 2026-08-21T03:09:00.862-04:00，API 接受并将 zendesk_status_updated_at 规范化为 2026-08-21T07:09:00.862000+00:00；非法日期与 zendesk_status=ticket id 仍返回 422。"
        },
        {
          "type": "test",
          "label": "Regression suites",
          "command": "TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_zendesk_comment_sync_postgres backend.tests.test_account_intake backend.tests.test_account_reply_publication_postgres backend.tests.test_worker backend.tests.test_repository_configuration backend.tests.test_account_automation_ownership backend.tests.test_workspace_api backend.tests.test_account_full_reroute backend.tests.test_account_reroute_dispatch backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_zendesk_assignment backend.tests.test_account_zendesk_internal_comment_service backend.tests.test_account_zendesk_comment backend.tests.test_zendesk_comments backend.tests.test_account_reply_version_fence backend.tests.test_account_slack_n8n",
          "details": "572 通过（8 skip 为无 DSN 的 Postgres 用例）；UI 契约（account/production 含新徽章断言与版本串 20260821-zendesk-status-1）另 55 通过。"
        },
        {
          "type": "deployment",
          "label": "Migrations applied to both databases",
          "command": "psql $TICKET_DB_MIGRATION_DSN / $PRODUCTION_TICKET_DB_DSN -f backend/sql/migrations/2026_08_21_account_zendesk_status_sync.sql（staging 需 migration DSN，runtime 角色非 owner）",
          "details": "2026-08-21 对 supportportal（TICKET_DB_MIGRATION_DSN，zac）与 supportportal_production（runtime DSN）各执行迁移；information_schema 确认两库 zendesk_ticket_status/zendesk_status_updated_at/zendesk_status_synced_at 三列齐全。"
        },
        {
          "type": "deployment",
          "label": "Local official stack + EC2 production deploy",
          "command": "bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote；ssh zacbot ./deployment/deploy_ec2.sh --branch main --domain support.stellarix.space",
          "details": "PR#837 合并（main=9587d44）后双栈部署：本地 /health ok app_build.ref=9587d44a47ea（local_lightweight）；EC2 外部 https://support.stellarix.space/health ok 同 ref（full）。/account 与 /production 页面均 serving app.js?v=20260821-zendesk-status-1。"
        },
        {
          "type": "deployment",
          "label": "Live status sync on both origins",
          "command": "curl -X PUT -H 'X-Zendesk-Account-Sync-Token: …' -d '{\"zendesk_status\":…}' http://127.0.0.1:8080/api/integrations/zendesk/account-cases/12862/status 与 https://support.stellarix.space/production/api/integrations/zendesk/account-cases/12896/status",
          "details": "staging 源（12862）：target 返回 status_endpoint、push open→updated、重放→unchanged、审计事件落库。production 源（12896，Zendesk 实测 solved 而本地仍 open 的真实缺口）：push solved→updated+local_ticket_closed=true；DB 终态 support_tickets=resolved+closed_at、case zendesk=solved automation=closed prior=automation、审计 closed=true。n8n 工作流待用户按 docs/integrations/n8n/zendesk_account_status_sync.md 配置。"
        }
      ],
      "source_refs": [
        "backend/main.py",
        "backend/repositories/ticket_repository.py",
        "backend/sql/ticket_storage.sql",
        "backend/sql/migrations/2026_08_21_account_zendesk_status_sync.sql",
        "ui/account-ui/app.js",
        "ui/production-ui/app.js",
        "docs/integrations/n8n/zendesk_account_status_sync.md"
      ],
      "created_at": "2026-08-21",
      "updated_at": "2026-08-21",
      "history": [
        {
          "at": "2026-08-21",
          "event": "created",
          "summary": "用户提出 n8n 监听 Zendesk 状态变更并同步到 /account 与 /production；确认采用联动关闭（solved/closed 停 AI 自动回复）+ 列表与详情双展示。"
        },
        {
          "at": "2026-08-21",
          "event": "completed",
          "summary": "PR#837 合并（main=9587d44），双库迁移、双栈部署与双源 live 验证通过（production 12896 真实 solved 缺口被补齐），标记完成；n8n 侧工作流由用户按契约文档配置。"
        },
        {
          "at": "2026-08-21",
          "event": "reopened",
          "summary": "n8n 首次状态同步暴露 updated_at 带 -04:00 offset 的兼容性边界，同时确认 422 样例实际将 Zendesk ticket id 映射到了 zendesk_status；重新打开任务补齐日期解析与错误映射回归。"
        },
        {
          "at": "2026-08-21",
          "event": "completed",
          "summary": "共享 /account 与 /production status-sync API 接受 n8n 带时区 ISO-8601 updated_at，规范化为 UTC 并对非法日期返回 422；保留严格 zendesk_status 枚举，补齐错误字段映射回归。"
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
      "task_id": "p2-86",
      "title": "Production 手动将 AI 工单退回 Zendesk routing queue",
      "status": "done",
      "owner": "zac",
      "summary": "在 /production case 详情提供 Route back to queue 操作。操作先停止本地 AI 自动化并取消待发送回复，再将仍由配置 AI 持有的 Zendesk 工单恢复到可靠识别的原真人 group、清空 assignee、设置 Waiting for Support 并添加 routing tag；更新后 readback 验证，重复操作不得释放已经由真人接管的工单。",
      "next_action": "由用户在部署后的 /production 使用新 case 点击 Route back to queue，确认 Zendesk 标准 routing 分配给当前在线工程师。",
      "acceptance_criteria": [
        "仅 Production workspace admin 可调用手动 route-back endpoint；非 production case、无数字 Zendesk ticket、solved/closed 工单均拒绝。",
        "操作开始即将本地 automation_status 置为 human_review_required、取消 pending reply jobs，并在 automation_context 与审计事件中记录结果；失败时保持 AI 停机。",
        "Zendesk 工单仍由配置 AI 持有时，恢复已保存或 audit 可证明的原真人 group，清空 assignee，设为 open/Waiting for Support，并添加 auto_route 与 supportportal_human_fallback tags。",
        "无法可靠识别原 group 时 fail closed；已由真人持有时返回 already_human_owned，已在非 AI group 且未分配时返回 queued，不覆盖或清空真人 assignment。",
        "PUT 使用 safe_update 与 updated_stamp；网络 outcome_unknown 仅做 GET 对账，不盲目重复 PUT；成功后 readback 返回 queued 或 assigned。",
        "/production 详情页显示 Route back to queue 按钮与确认弹窗，pending 时防重复，成功后 toast 并刷新详情；已退回或真人接管状态不可再次释放。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Route-back service, API, ownership fence, Production UI, and worker regression",
          "command": "TICKET_DB_DSN=postgresql://example.invalid/test SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_assignment backend.tests.test_account_automation_ownership backend.tests.test_zendesk_ticket_assignment backend.tests.test_production_ui_contract backend.tests.test_worker",
          "details": "171 tests pass：覆盖保存/审计原真人 group、真人 assignment 不清空、已 queued 零 PUT、audit fallback、无可靠 group fail closed、safe_update payload、outcome_unknown 只读对账且不可盲重试、human_review/released worker fence、Production admin API 与 UI contract。"
        },
        {
          "type": "test",
          "label": "Production JavaScript and Project Overview contracts",
          "command": "node --check ui/production-ui/app.js && git diff --check && python3 scripts/generate_project_overview.py --check",
          "details": "JavaScript syntax、diff whitespace 与 Project Overview generated view 校验通过；Production asset marker 为 20260821-route-back-queue-1。"
        }
      ],
      "source_refs": [
        "backend/services/zendesk_ticket_assignment.py",
        "backend/services/account_automation_ownership.py",
        "backend/main.py",
        "ui/production-ui/app.js",
        "ui/production-ui/styles.css"
      ],
      "created_at": "2026-08-21",
      "updated_at": "2026-08-21",
      "history": [
        {
          "at": "2026-08-21",
          "event": "created",
          "summary": "用户批准先实现 Production 手动 Route back to queue，用按钮验证 Zendesk 标准 routing；自动 human fallback 与 RAG fallback 不在本任务范围。"
        },
        {
          "at": "2026-08-21",
          "event": "completed",
          "summary": "完成 Production 手动回队列按钮、AI 停机与 reply-job 取消、原 group 恢复、Zendesk safe update/readback、outcome_unknown fence 和专用审计；自动 fallback 与 RAG fallback 保持后续任务。"
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
      "task_id": "p2-87",
      "title": "Production route-back 操作邮件通知",
      "status": "done",
      "owner": "zac",
      "summary": "Production 管理员执行 Route back to queue 后，通过现有 Microsoft Graph Mail 给 xieziling@agora.io 发送状态通知。通知覆盖 Zendesk queued、assigned、already_human_owned、failed 与 outcome_unknown 终态，不包含客户正文或凭据。邮件失败不得回滚或诱发重复 Zendesk 写入，结果必须进入 API 响应与审计。",
      "next_action": "由用户使用新的 Production case 执行 Route back to queue，确认 Zendesk routing 与 xieziling@agora.io 收件同时成功。",
      "acceptance_criteria": [
        "通过 Production/admin/numeric-ticket 前置校验并开始 route-back 后，每个 Zendesk 终态都尝试向 xieziling@agora.io 发送一封 Graph Mail。",
        "邮件包含 Account Case ID、Zendesk ticket 链接、route-back 状态、group/assignee、取消 reply-job 数和触发时间，不包含客户问题正文或 secret。",
        "Zendesk 成功但邮件失败时保持 route-back 成功，不重试 Zendesk；API 与审计明确记录 notification_email_status=failed。",
        "Zendesk route-back 失败或 outcome_unknown 时仍尝试发送失败状态邮件，并保留原有 HTTP/fail-closed 语义。",
        "未通过前置校验的请求不发送邮件。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Route-back email, Zendesk fence, UI and Graph Mail regression",
          "command": "TICKET_DB_DSN=postgresql://example.invalid/test SENTIMENT_PROVIDER=legacy .venv/bin/python -m unittest backend.tests.test_account_zendesk_assignment backend.tests.test_account_automation_ownership backend.tests.test_zendesk_ticket_assignment backend.tests.test_production_ui_contract backend.tests.test_worker backend.tests.test_workspace_invitations backend.tests.test_account_failure_alerts",
          "details": "177 tests pass：覆盖 queued/assigned/already_human_owned/failed/outcome_unknown 邮件、固定收件人和无客户正文、邮件失败不重放 Zendesk、非法请求零邮件、审计状态、UI notification toast 与 120 秒 timeout，以及既有 worker/ownership/Graph Mail 回归。"
        },
        {
          "type": "test",
          "label": "JavaScript and generated Project Overview contracts",
          "command": "node --check ui/production-ui/app.js && git diff --check && python3 scripts/generate_project_overview.py --check",
          "details": "JavaScript syntax、diff whitespace、Project Overview generated view 校验通过；Production asset marker 为 20260821-route-back-email-1。"
        }
      ],
      "source_refs": [
        "backend/main.py",
        "backend/services/graph_mail.py",
        "backend/tests/test_account_zendesk_assignment.py"
      ],
      "created_at": "2026-08-21",
      "updated_at": "2026-08-21",
      "history": [
        {
          "at": "2026-08-21",
          "event": "created",
          "summary": "用户要求执行 Production Route back to queue 时同时给 xieziling@agora.io 发送邮件。"
        },
        {
          "at": "2026-08-21",
          "event": "completed",
          "summary": "Route-back 的所有执行终态都会尝试发送 Graph Mail；通知失败进入 API/UI/审计但不回滚或重放 Zendesk，扩展回归通过。"
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
      "task_id": "p2-88",
      "title": "三套 Route 与 Automation 环境迁移",
      "status": "active",
      "owner": "zac",
      "summary": "将现有 Account route/automation 流程拆分为 staging、preproduction、production 三套 Automation UI/runtime 与 route container。Route 负责无副作用的 route 和 automated case AI/action-plan preparation；Automation 按环境策略执行内部记录、Zendesk internal/external comment、take ownership 和 ticket status。三套环境使用独立镜像、现有项目 DSN 下的独立 schema、独立 execution table、队列和凭据，Production 镜像物理排除 rerun，旧 /account 与 /production 在新环境验收和切流批准前保持不变。",
      "next_action": "2026-08-25 蓝绿部署失败修复代码已完成，待 EC2 更新 main 后重新执行同一 release 的蓝绿部署：必须先由 bootstrap 补齐 supportportal_production ticket/knowledge schema，再确认 automation_production_worker 在稳定窗口内 running 且 RestartCount 不变，最后运行 candidate-aware verify_split_environments.sh 并验证异步回复链。当前公网 health 200 但 worker 持续重启，线上恢复尚未完成。",
      "acceptance_criteria": [
        "新增 /automation/staging/、/automation/preproduction/、/automation/production/，每个环境有独立 UI/API/Route/schema/execution table/queue/credentials 与 build marker；数据库 DSN 复用现有项目配置。",
        "Automation 始终先调用绑定 environment 的 Route；Route 返回 route 和 automated case 的完整 AI/action-plan preparation，不执行 Zendesk、ownership、status 或 delivery side effect。",
        "staging 只做内部记录、无真实 Zendesk 出站、允许 rerun/reset；preproduction 只允许 allowlisted ticket，执行 ownership/status 和 Zendesk internal comment，允许 rerun；production 执行真实动作、每次显式选择 internal/external comment、禁止 rerun。",
        "release builder 在同一台 EC2 本地一次构建 route、automation、production 三种 role，生成六个本地 image pointers 和对应 image IDs；deploy_ec2.sh 按 release manifest 校验本机镜像、晋升和回滚，不执行 registry push/pull，Production Automation image manifest、runtime application bundle、OpenAPI 和 UI 不含 rerun。",
        "三套环境复用现有项目 DSN，但使用 supportportal_staging、supportportal_preproduction、supportportal_production 独立 schema 和 automation_executions_staging、automation_executions_preproduction、automation_executions_production 三张独立 execution table；Redis/queue、secret、Route token 和 compose project 不交叉污染；deploy_ec2.sh 不默认整栈 down。",
        "按 staging -> preproduction -> production 完成契约、镜像内容、fake Zendesk、远程 Zendesk readback、回滚和官方栈验证；旧端点退出需要单独切流批准。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Split Route/Automation contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_route_service_contract backend.tests.test_automation_runtime_contract backend.tests.test_split_environment_deployment backend.tests.test_automation_contracts backend.tests.test_account_automation_ownership",
          "details": "本轮 27 项 split/image/ledger/runtime 契约测试通过；覆盖 Route side-effect-free action plan、account_suspension preparation、三环境 capability、production visibility、production OpenAPI 无 rerun/reset、服务端 Zendesk delivery readback、preproduction ownership、六服务/profile 和 Route/production image exclusion contract。"
        },
        {
          "type": "test",
          "label": "Static syntax and configuration checks",
          "command": ".venv/bin/python -m py_compile ... && node --check ui/automation-production/app.js && bash -n deployment/deploy_ec2.sh && git diff --check",
          "details": "Python/JavaScript 编译、UI node --check、deploy shell syntax、Compose YAML 静态资源身份解析和 diff whitespace 校验通过；deploy_ec2 fake-command 回归 18 项通过。"
        },
        {
          "type": "decision",
          "label": "Docker/EC2 runtime verification pending",
          "command": "docker compose config/build/up；Nginx runtime health；Zendesk remote readback；rollback drill",
          "details": "当前工作机没有 Docker CLI，仅有 docker-compose 兼容命令；六镜像 build/up、Production filesystem inspect、Nginx runtime、Zendesk remote readback 与 rollback drill 必须在 Docker-capable host/EC2 执行，不能由本地静态检查替代。"
        },
        {
          "type": "test",
          "label": "Per-operation delivery ledger and staging Zendesk deny boundary",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_delivery_ledger backend.tests.test_automation_delivery_reconciliation backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract",
          "details": "delivery ledger/reconciliation 相关测试通过；execution 记录 ownership/comment/status 的稳定 delivery key、attempt、ticket/status 绑定和 completed/outcome_unknown 状态，并验证 staging Zendesk client boundary 显式拒绝出站；reconcile 必须由服务端 Zendesk readback 产生证据。"
        },
        {
          "type": "test",
          "label": "Release builder and manifest promotion contract",
          "command": ".venv/bin/python -m unittest backend.tests.test_build_automation_release backend.tests.test_deploy_ec2",
          "details": "fake Docker 回归通过：release builder 在本地构建 route/automation/production 三种 role，生成本地 tag、六个 image pointer 和 image ID；split deploy 校验本地 image ID、跳过 compose pull，image 缺失/不匹配或 manifest 缺失时在 network/compose 变更前 fail closed；旧 digest 迁移和 rollback 契约仍通过。"
        },
        {
          "type": "test",
          "label": "Existing database DSN fallback and split provenance",
          "command": ".venv/bin/python -m unittest backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_split_deploy_loads_release_manifest_without_manual_image_variables backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_preproduction_reuses_account_database_with_environment_defaults backend.tests.test_deploy_ec2.DeployEc2ScriptTests.test_production_reuses_production_database_with_environment_defaults",
          "details": "验证 staging/preproduction 缺少 AUTOMATION_*_DB_DSN 时复用 TICKET_DB_DSN，production 复用 PRODUCTION_TICKET_DB_DSN；三环境默认 schema/queue/event identity 由部署进程导出，release manifest commit/build_time 映射到 split route/automation build markers。"
        },
        {
          "type": "test",
          "label": "Environment-specific automation execution tables",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_execution_store backend.tests.test_automation_contracts",
          "details": "验证 execution store 使用 schema-qualified 的 automation_executions_staging、automation_executions_preproduction、automation_executions_production 三张表，表名非法或未按环境绑定时 fail closed。"
        },
        {
          "type": "test",
          "label": "Split nginx automation edge attachment",
          "command": ".venv/bin/python -m unittest backend.tests.test_deploy_ec2 backend.tests.test_split_environment_deployment",
          "details": "验证 split deploy 在启动环境服务前将正在运行的官方 nginx 幂等接入 supportportal_automation_edge，不重建 nginx；nginx 不存在时 fail closed，避免容器 health 正常但外部路径因 Docker DNS 不可达而持续 502。"
        },
        {
          "type": "test",
          "label": "Acceptance remediation: outbound networks, execution tokens, unknown write paths",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_deploy_ec2 backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_route_service_contract backend.tests.test_automation_contracts backend.tests.test_account_automation_ownership backend.tests.test_automation_execution_store backend.tests.test_build_automation_release backend.tests.test_automation_delivery_ledger backend.tests.test_automation_delivery_reconciliation",
          "details": "2026-08-22 线上验收发现 staging 执行链 502 route_http_error：route 容器只挂在 --internal 网络上无法出站解析 LLM API。修复为 automation 网络不再 --internal 创建、既存 internal 网络 fail closed 并要求人工迁移；同时 /v1/cases、rerun、reset、reconcile 增加 AUTOMATION_EXECUTION_TOKEN Bearer 鉴权（三 UI 提供 token 输入）、production 未知写路径返回 404、deploy 校验三个 AUTOMATION_*_EXECUTION_TOKEN 必填。116 项相关测试通过。"
        },
        {
          "type": "deployment",
          "label": "Release-005 three-environment deployment and acceptance probes",
          "command": "EC2 deploy_ec2.sh --release release-20260822-005（staging -> preproduction -> production）；curl /automation/*/health|capabilities|v1/cases",
          "details": "2026-08-22 三环境迁移非 internal 网络并部署 release-20260822-005：三环境 health 200、capabilities 策略矩阵与设计一致；staging 带 token 探针 9 秒返回 prepared 并落库 automation_executions_staging（此前 502 route_http_error 已消除）；空 body 无/错 token 均 401（鉴权先于请求体校验）；production POST /v1/reruns 404；staging 容器无 Zendesk 凭据；旧 /account、/production 仍 200；PREPRODUCTION/PRODUCTION_ZENDESK_SIDE_EFFECTS_ENABLED=1、TARGET_TICKET_STATUS=pending 已生效。preproduction allowlist 工单 12872/12895 验证 quota/unregistered/enablement-字段不足/suspension-prepared 各路由与 human_review 无副作用落库，suspension prepared 链路执行到 side-effect 调用并以 failed+pending ledger 正确落库。"
        },
        {
          "type": "deployment",
          "label": "Three-environment rollback drill",
          "command": "EC2 deploy_ec2.sh --environment {staging,preproduction,production} --rollback 后重新 --release release-20260822-005",
          "details": "2026-08-22 三环境各执行 rollback（staging/production 回退 release-20260822-004，preproduction 回退同版 previous）并恢复 release-20260822-005；全程 health 200，manifest current/previous 指针正确交替，回滚只影响目标 compose project。"
        },
        {
          "type": "decision",
          "label": "Zendesk credentials 401 blocks real side-effect acceptance",
          "command": "容器内 GET agoraio.zendesk.com/api/v2/tickets/12895.json",
          "details": "preproduction 与主栈 api_production 容器使用 .env 的 zendesk_basic_auth 均 401；该值不含冒号（非 email:token 格式），疑似 Zendesk token 轮换后未更新 EC2 .env。真实 Zendesk 写入验收（preproduction internal 全链路、production internal/external 与 readback）与主栈 /production 自动投递均被阻塞，等待运维更新凭据。"
        },
        {
          "type": "deployment",
          "label": "Zendesk credential resolved, verification probes all green",
          "command": "EC2 deploy_ec2.sh --release release-20260822-005（preproduction/production recreate）+ ./deployment/verify_split_environments.sh",
          "details": "2026-08-23 运维更新 EC2 .env 的 zendesk_basic_auth 并 recreate preproduction/production 容器后，verify_split_environments.sh 36/36 全部通过（三环境 health/capabilities/鉴权/404/容器不变量/网络/route 出站/Zendesk 凭据只读 GET/旧端点）。真实工单写入验收按用户指示暂缓，不动真实工单。"
        },
        {
          "type": "deployment",
          "label": "Local podman split environment startup",
          "command": "scripts/workflow/start_local_split_environments.sh [--skip-build]",
          "details": "新增本地（podman）三环境启动脚本：从当前工作树构建三个 role 镜像（worktree 可验证未提交改动，脏树 tag 带 -wip）、幂等建网络、自动生成三个执行 token 写入 root .env、按 EC2 同名 project 启动三环境并验证 health 与 401 负例；本地 Zendesk 副作用默认关闭 fail-closed，PRODUCTION_TICKET_DB_DSN 缺失时跳过本地 production。配套文档见 docs/deploy_automation_release.md 第 6 节。"
        },
        {
          "type": "document",
          "label": "T4 n8n cutover design (company-ID canary + unified token)",
          "command": "docs/integrations/n8n/automation_environments_cutover.md",
          "details": "2026-08-23 产出 T4 方案先行设计：确认 /automation/{env}/v1/cases 新工单投递端点已存在且已验证，评论/状态同步在新环境无等价端点、保持旧端点；production 采用克隆工作流 + TARGET_COMPANY_IDS 互斥名单灰度分流（零双写、可回滚）；token 统一为同一密钥值贯穿 AUTOMATION_{三环境}_EXECUTION_TOKEN、ZENDESK_ACCOUNT_SYNC_TOKEN、n8n_request_token（旧同步端点已支持 Bearer 回退，backend/main.py require_zendesk_account_sync_token），n8n 单个 Bearer 凭据覆盖全部入向调用。含 EC2/n8n 操作 runbook、双写防护红线与验证清单；实施待 T3 完成与用户批准。"
        },
        {
          "type": "document",
          "label": "Report v2 refresh with cutover direction",
          "command": "docs/split_environments_report.md",
          "details": "2026-08-24 按用户决策将报告刷新为 v2：新增第 0 节总目标（三环境上线并完全替代旧 /account 与 /production；preproduction 与 production 配置统一、进入 case 由 n8n 控制、production 最后切流）；修正第 1 节过时内容（鉴权已统一为单一 X-N8n-Request-Token/n8n_request_token，旧 Bearer 与三个 AUTOMATION_*_EXECUTION_TOKEN 已废弃；allowlist 三态含 * 放行）；T1/T6 标完成（p2-89），T2/T5 标未承接并降级（T5 探针半边放弃、被 /automation/test 回归体系超越），T3/T4 剩余并入新包；新增 T7（preproduction 配置统一 + n8n 筛选流量影子验收）与 T8（production 最终切流与旧端点下线，以 automation_environments_cutover.md 为权威操作手册）。纯文档刷新，无运行时变更。"
        },
        {
          "type": "deployment",
          "label": "Automation production blue-green deployment implementation",
          "command": ".venv/bin/python -m unittest backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose && bash -n deployment/deploy_automation_production_blue_green.sh",
          "details": "新增专用蓝绿入口：candidate 使用 release 唯一服务名和生产 DB/Redis identity，readiness 通过后以 Nginx runtime include 原子切换并 graceful reload；/automation/production/ 禁止 upstream 自动重试，旧 compose project 默认排空 360 秒后停止，--rollback 只切换 upstream、不重放请求。当前本机缺少可用 Docker CLI/.env 完整必填变量，EC2 栈验证待执行。"
        },
        {
          "type": "deployment",
          "label": "EC2 review remediation",
          "command": ".venv/bin/python -m unittest backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose && bash -n deployment/deploy_automation_production_blue_green.sh",
          "details": "修复 EC2 review 发现的 release manifest 未注入、候选 Redis 重复创建、drain 后 rollback 指针失效、切流健康检查失败不恢复、缺部署锁、Nginx optional upstream 破坏和旧 Nginx runtime mount 缺失：manifest 校验本地 image ID；candidate 直接复用 external production Redis；旧服务只 stop 且持久化 override；失败自动恢复 upstream；共享 .deploy_ec2.lock；Nginx 使用 server scope variable；首次切换前自动补齐 runtime mount。Docker/EC2 演练仍待执行。"
        },
        {
          "type": "test",
          "label": "Blue-green schema and worker readiness remediation",
          "command": ".venv/bin/python -m unittest backend.tests.test_production_blue_green_behavior backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_deploy_ec2 && bash -n deployment/{deploy_automation_production_blue_green,bootstrap_automation_production_schema,deploy_ec2,verify_split_environments}.sh",
          "details": "75 项部署回归通过。蓝绿顺序收紧为 schema bootstrap -> candidate readiness -> parity worker recreate -> worker stability -> Nginx cutover -> state commit -> drain；worker 注入必填 PGVECTOR_DSN，重启/退出时在切流前失败并停止 candidate。verify_split_environments.sh 按 active upstream 的 Compose service label 识别 candidate，双采样同一 worker 的 running/status/RestartCount，移除硬编码容器名和 grep|head pipefail。EC2 数据库 bootstrap、容器重建和异步回复 readback 尚未执行。"
        }
      ],
      "source_refs": [
        "backend/main.py",
        "backend/services/account_route_pipeline.py",
        "backend/services/account_admin.py",
        "backend/services/account_automation_ownership.py",
        "backend/services/account_zendesk_internal_comment.py",
        "backend/services/zendesk_comments.py",
        "backend/services/automation_delivery_ledger.py",
        "backend/services/automation_delivery_reconciliation.py",
        "backend/services/automation_rerun_contracts.py",
        "deployment/docker-compose.single-host.yml",
        "deployment/nginx/supportportal.conf",
        "deployment/deploy_ec2.sh",
        "deployment/deploy_automation_production_blue_green.sh",
        "deployment/bootstrap_automation_production_schema.sh",
        "deployment/verify_split_environments.sh",
        "deployment/build_automation_release.sh",
        "docs/deploy_automation_release.md",
        "backend/Dockerfile",
        "ui/account-ui",
        "ui/production-ui",
        "design.md"
      ],
      "created_at": "2026-08-22",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-22",
          "event": "preproduction_human_review_return",
          "summary": "preproduction 线上业务验收暴露非 production runtime 的 human_review 分支缺少 return，落入末尾 store.save 时引用未赋值 delivery_ledger 导致 500；与 production runtime 对齐补显式 return 并增加回归用例。"
        },
        {
          "at": "2026-08-22",
          "event": "local_stack_compose_default_network_compat",
          "summary": "修复 split compose 顶层未显式声明 default 网络导致本地官方 podman-compose 栈无法重启的问题；networks 顶层增加 default: deployment_default 声明，podman-compose config 与 compose 相关测试通过。"
        },
        {
          "at": "2026-08-22",
          "event": "execution_token_dependency_order",
          "summary": "release-003 线上验证发现空 body 请求先触发 422 校验而非 401 鉴权；执行 token 检查从 handler 内移到路由级 Depends，保证鉴权先于请求体校验，测试补充空 body 无 token 必须 401 的断言。"
        },
        {
          "at": "2026-08-22",
          "event": "created",
          "summary": "根据三环境 Route/Automation 迁移计划创建实施任务。"
        },
        {
          "at": "2026-08-22",
          "event": "acceptance_remediation",
          "summary": "修复 split rollback manifest 双指针、Production rerun 物理隔离和服务端 Zendesk readback；本地 targeted/deploy 回归通过，Docker/EC2 gate 保留待外部执行。"
        },
        {
          "at": "2026-08-22",
          "event": "release_promotion_automation",
          "summary": "新增三 role release builder 和 immutable manifest；split deploy 支持按 release 晋升，不再要求手工填写六个 image digest。"
        },
        {
          "at": "2026-08-22",
          "event": "local_release_alignment",
          "summary": "按单台 EC2 原始部署模型移除 release builder 的 registry push 和 split deploy 的 release pull；manifest 改为本地 image tag + image ID，并在 Compose/network 变更前校验本机镜像身份。"
        },
        {
          "at": "2026-08-22",
          "event": "existing_database_schema_fallback",
          "summary": "split deploy 默认复用现有 account/production DSN，以独立 schema、queue 和 event channel 隔离环境，并将 release manifest provenance 传入六个 split 服务。"
        },
        {
          "at": "2026-08-22",
          "event": "environment_specific_execution_tables",
          "summary": "按用户修正将 execution ledger 从共享表改为三张环境专属表，并在 Compose、资源 identity 和部署校验中显式绑定表名。"
        },
        {
          "at": "2026-08-22",
          "event": "nginx_automation_edge_attachment",
          "summary": "修复 split 环境后启动时官方 nginx 未加入 automation edge 导致的 502；deploy 脚本改为幂等连接共享网络且不重建 nginx。"
        },
        {
          "at": "2026-08-22",
          "event": "acceptance_remediation_outbound_and_auth",
          "summary": "线上验收发现 --internal 网络阻断 route 出站 LLM 调用后，automation 网络改为非 internal 创建并对既存 internal 网络 fail closed；执行端点增加每环境 Bearer token 鉴权（compose 映射 AUTOMATION_*_EXECUTION_TOKEN，deploy 必填校验，三 UI 提供输入），production 未知写路径统一 404。"
        },
        {
          "at": "2026-08-23",
          "event": "ui_parity_split_to_p2_89",
          "summary": "报告任务包 T1（rerun 真实现）与 T6（design.md 覆盖 + UI 迭代）及三环境 UI/功能对齐旧端点的工作分流到新任务 p2-89 承接；本任务保留拆分环境基础设施与真实写入验收（T3）、切流（T4）范围。"
        },
        {
          "at": "2026-08-23",
          "event": "n8n_cutover_design",
          "summary": "T4 方案先行：产出 n8n 切流设计文档（/automation/*/v1/cases 端点契约与字段映射、Company ID 灰度分流、token 统一 runbook），并同步更新三个 n8n 集成契约文档的 token 段落；未改任何运行时代码与线上配置，实施待 T3 与用户批准。"
        },
        {
          "at": "2026-08-23",
          "event": "unified_auth_split_to_p2_91",
          "summary": "用户决策将 token 统一升级为单一 X-N8n-Request-Token/n8n_request_token 机制（其余头与变量不再接受），服务端及配套实施分流到 p2-91；本任务保留 T3/T4 范围。"
        },
        {
          "at": "2026-08-23",
          "event": "preexisting_test_debt_found",
          "summary": "p2-91 验证期间发现干净 main（8307746）存量测试失败 7 项且与顺序耦合：test_deploy_ec2 的 6 项（DSN 回退期望 example.invalid/test 与 TICKET_DB_DSN 不符、requires_execution_token/successful_deploy 批量跑时才失败）+ test_account_zendesk_status_sync 1 项（zendesk_status_synced_at 硬编码 startswith '2026-08-21'）。均非 p2-91 引入（同命令基线对比一致），按协调规则不顺手修，留待专项清理。"
        },
        {
          "at": "2026-08-24",
          "event": "direction_refresh",
          "summary": "用户决策终态方向：三环境上线并完全替代旧 /account 与 /production；preproduction 与 production 配置做成一模一样（仅保留架构固有差异），进入的 case 由 n8n 控制（production 收 production case、preproduction 收 n8n 筛选后的 case）；production 最后切流以避免与现有 /production 冲突。报告刷新为 v2：T1/T6 已完成（p2-89），T2/T5 未承接降级为可选 backlog（T5 探针半边放弃），T3/T4 剩余分别并入新增 T7（preproduction 配置统一 + n8n 筛选流量影子验收）与 T8（production 最终切流与旧端点下线）。"
        },
        {
          "at": "2026-08-24",
          "event": "production_parity_split_to_p2_108",
          "summary": "用户进一步改变路线：直接用 /automation/production 替代 /production（不再走 T7 preprod 先行的顺序），并逐项确认功能差距处置（quota→human_review 预期、human review 流跳过、detailed_invoice 回复闭环与运维工具跳过、执行内容/客户回复链/状态同步/Slack 按旧栈、评论与状态摄入由 n8n 转发）。七阶段搬迁计划 Phase A（schema bootstrap + automation_production_worker + deploy/蓝绿覆盖）分流到新任务 p2-108 承接；本任务保留三环境基础设施与蓝绿演进范围，T7/T8 顺序由用户后续迁移节奏决定。"
        }
      ],
      "legacy_refs": [
        "p2-73",
        "p2-74"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-89",
      "title": "三环境控制台 UI 与功能对齐旧端点",
      "status": "done",
      "owner": "zac",
      "summary": "将 /automation/staging、/automation/preproduction、/automation/production 三个 UI 从最小表单页升级为与旧端点同款的工作台：staging 对齐 /account 模板、preproduction 与 production 对齐 /production 模板，route correction/route review 不做。后端新增 execution 列表/详情查询端点（Bearer 鉴权）、rerun 真实现（持久化原始请求、新 execution 链）、reset 真实现（仅 staging，清空执行记录表）。旧 /account 与 /production 端点和页面零改动。",
      "next_action": "已完成。三环境控制台与旧端点模板级对齐已上线 release-20260823-004 并完成线上验收；真实 Zendesk 写入验收（T3）与旧端点切流（T4）仍由 p2-88 跟踪。",
      "acceptance_criteria": [
        "三个 /automation/* UI 提供与旧端点同款的工作台布局：execution token 门、侧边栏执行历史（状态过滤+计数、Case 搜索、分页）、执行表单、详情视图（meta 网格、customer question + AI reply 问答时间线、delivery ledger 表、折叠 raw JSON）。",
        "GET /v1/executions 与 GET /v1/executions/{id} 在 staging/preproduction/production 三个 runtime 均可用且强制 Bearer execution token；列表返回分页、过滤与 status_counts，同一快照。",
        "POST /v1/reruns 真实创建新 execution：execution 记录持久化原始请求字段（向后兼容读取），rerun 以新 request_id 复用同一执行路径并记录 rerun_of_execution_id 链，原 execution 不可变；production OpenAPI 仍无 rerun/reset。",
        "POST /v1/reset（仅 staging）真实清空本环境执行记录表并返回 deleted_count；preproduction/production 维持 404。",
        "UI 危险操作（rerun/reset）使用冻结 case_id/ticket 的确认弹窗；按钮可见性由 /v1/capabilities 驱动；outcome_unknown 露出 reconcile 入口；failed 状态 aria-live。",
        "不做 route correction/route review；不新增跨环境转发与 route back to queue；不写真实 Zendesk 工单。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Split runtime query/rerun/reset contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_execution_store backend.tests.test_automation_contracts backend.tests.test_automation_delivery_ledger backend.tests.test_automation_delivery_reconciliation backend.tests.test_route_service_contract backend.tests.test_split_environment_deployment backend.tests.test_build_automation_release backend.tests.test_deploy_ec2 backend.tests.test_single_host_compose backend.tests.test_account_automation_ownership",
          "details": "122 项相关测试通过。新增覆盖：GET /v1/executions 无 token 401、分页/status 过滤/case 查找/status_counts 同快照、execution payload 持久化原始 request 字段；GET /v1/executions/{id} 401/404/200；rerun 创建链式新 execution（新 request_id、rerun_of_execution_id 可追溯、原记录不可变、旧记录 422 execution_request_not_persisted、case 不匹配 404）；staging reset 清空并返回 deleted_count、preproduction reset 404；production runtime 具备列表/详情端点且 OpenAPI 仍无 rerun/reset；production UI bundle 物理不含 rerun 字符串。"
        },
        {
          "type": "test",
          "label": "Static syntax checks for the three console UIs",
          "command": "node --check ui/automation-staging/app.js && node --check ui/automation-preproduction/app.js && node --check ui/automation-production/app.js",
          "details": "三份 app.js 通过 node --check；staging/preproduction 主体逐字节一致（仅 ENV 常量块不同），production 变体由同一源生成并剥离 rerun 代码块（含 ENV 键与文案），满足镜像物理排除契约的 UI 侧约束。"
        },
        {
          "type": "deployment",
          "label": "Release-20260823-001..004 three-environment rollout",
          "command": "EC2 build_automation_release.sh + deploy_ec2.sh（staging -> preproduction -> production，DEPLOY_PRODUCTION_APPROVED=1）+ verify_split_environments.sh",
          "details": "2026-08-23 依次部署 release-001/002/003/004：-001 首次上线新控制台与查询/rerun/reset 端点；-002 因 EC2 构建时 origin/main 引用未刷新（stale ref，manifest commit=47a0c9d）实际仍为旧代码，改为先 git fetch 再构建；-003 修复 token 门委托 submit 取 currentTarget 导致 FormData(div) 抛错的缺陷（manifest commit=d668302）；-004 修复模板字符串内哨兵标记渲染为可见文本的缺陷并升静态版本 v3（manifest commit=d3e1941）。每轮部署前后 verify_split_environments.sh 全部通过。"
        },
        {
          "type": "test",
          "label": "Live browser acceptance of the three consoles",
          "command": "浏览器实测 http://ec2-52-71-106-188.compute-1.amazonaws.com:8080/automation/{staging,preproduction,production}/",
          "details": "staging：token 门 -> 工作台（6 条历史、状态过滤计数 All6/Prepared5/Failed1、Case 搜索、分页）-> 详情（meta 网格、Rerun of 链路回溯、请求区、问答时间线、折叠 raw JSON）-> rerun 确认弹窗（冻结 Case ID）-> 状态过滤 -> reset 确认弹窗与执行（toast 6 executions deleted、列表清空）；API 实测 POST /v1/cases 落库含 request 字段、POST /v1/reruns 产生链式新 execution、GET 列表/详情 401/200。preproduction：ticket 必填、锁定 internal、无 reset、能力行 rerun enabled/reset disabled/visibility internal、4 条历史。production：visibility 下拉 internal/external、无 rerun/reset、空态、production rerun 404、bundle 无 rerun 字符串。未做任何真实 Zendesk 写入（T3 按用户指示暂缓，12895 未动）。"
        }
      ],
      "source_refs": [
        "backend/automation_runtime.py",
        "backend/automation_production_runtime.py",
        "backend/services/automation_execution_store.py",
        "backend/services/automation_rerun_contracts.py",
        "ui/automation-staging",
        "ui/automation-preproduction",
        "ui/automation-production",
        "ui/account-ui",
        "ui/production-ui",
        "design.md",
        "docs/split_environments_report.md"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "sentinel_template_leak_fix",
          "summary": "release-20260823-003 浏览器验收发现 staging/preproduction 页面泄漏 /*__RERUN_START__*/ 文本：哨兵标记位于模板字符串内部。重构为 JS 变量（中性命名 extraActionButtonHtml/chainRowHtml/extraModalHtml，production 剥离后保留空默认值），静态资源版本升至 v3，随 release-20260823-004 部署；同时记录 EC2 构建 lesson：构建前必须先 git fetch，-002 曾因 stale origin/main 引用从旧 commit 构建。"
        },
        {
          "at": "2026-08-23",
          "event": "token_gate_delegated_submit_fix",
          "summary": "release-20260823-001 线上浏览器验收发现 token 门提交无响应：connectToken 经 appRoot 委托监听取 event.currentTarget 得到的是容器 div 而非表单，new FormData(div) 抛 TypeError 被 void 调用静默吞掉。改为从 event.target 取 HTMLFormElement，三份 UI 重新生成并随 release-20260823-002 重新部署。"
        },
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "承接 p2-88 报告中 T1（rerun 真实现）与 T6（UI 迭代）并扩展为三环境控制台与旧端点的模板级对齐：staging 取 /account 模板、preproduction/production 取 /production 模板，route correction/review 明确排除。"
        }
      ],
      "legacy_refs": [
        "p2-88"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-90",
      "title": "三环境控制台登录对齐 /workspace/admin（admin/admin）",
      "status": "done",
      "owner": "zac",
      "summary": "用户要求三个 /automation/* 控制台的登录从 Execution access 令牌门改为与 /workspace/admin 一致的账号密码登录（admin/admin）。每个 runtime 新增 POST /v1/auth/login：校验 AUTOMATION_ADMIN_USERNAME/AUTOMATION_ADMIN_PASSWORD（默认 admin/admin），成功返回本环境 execution token，UI 存入原 localStorage 键继续以 Bearer 调用全部 API；n8n 与执行 API 的 Bearer 契约不变。",
      "next_action": "已完成。三环境 release-20260823-005 上线 admin/admin 登录；凭据可用 AUTOMATION_ADMIN_USERNAME/PASSWORD 覆盖。",
      "acceptance_criteria": [
        "POST /v1/auth/login 在 staging/preproduction/production 三个 runtime 均可用：admin/admin 成功返回 execution_token，错误凭据 401；凭据可用 AUTOMATION_ADMIN_USERNAME/AUTOMATION_ADMIN_PASSWORD 覆盖。",
        "三个 UI 登录页与 /workspace/admin 同构（Email+Password、Welcome Back、Sign In），不再出现 Execution access 令牌输入；登录成功自动进入工作台，401 显示错误并停留。",
        "执行/查询 API 的 Bearer execution token 鉴权契约不变（n8n 不受影响）；登录端点本身不要求 Bearer。",
        "production bundle 仍不含 rerun 字符串（镜像物理排除契约保持）。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Admin login contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_contracts backend.tests.test_split_environment_deployment",
          "details": "32 项测试通过。新增覆盖：POST /v1/auth/login 无需 Bearer、admin/admin 成功返回本环境 execution token、错误用户名/密码 401、缺字段 422、执行查询端点仍 401；AUTOMATION_ADMIN_USERNAME/PASSWORD 覆盖默认凭据后 admin/admin 拒绝、覆盖凭据通过；production runtime 同契约；production UI bundle 仍无 rerun 字符串。三份 app.js node --check 通过、staging/preproduction 主体一致。"
        },
        {
          "type": "deployment",
          "label": "Release-20260823-005 rollout and live login verification",
          "command": "EC2 build（manifest commit=8307746）+ deploy staging -> preproduction -> production + verify_split_environments.sh + curl /v1/auth/login + 浏览器实测",
          "details": "2026-08-23 部署 release-20260823-005（构建前 git fetch 核对 commit）：verify 探针全绿；三环境 POST /v1/auth/login 实测 admin/admin=200、错误密码=401；浏览器实测登录页与 /workspace/admin 同构（Welcome Back/Email/Password/Sign In/ac_unit 品牌），hostname 会话自动进入工作台且侧栏显示 admin 会话卡与 Sign out。本会话 IAB 浏览器事件通道后期故障（已知可用页面的 chip 点击亦失效，fill/快照正常），Sign In 的真实鼠标点击未能在本会话完成——该代码路径与 release-003 实测可用的委托 submit 修复路径一致，端点与渲染均已线上验证。"
        }
      ],
      "source_refs": [
        "backend/services/automation_contracts.py",
        "backend/automation_runtime.py",
        "backend/automation_production_runtime.py",
        "ui/automation-staging",
        "ui/automation-preproduction",
        "ui/automation-production",
        "ui/workspace-ui/admin",
        "design.md"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "用户反馈三环境登录页不应是 Execution access 令牌门，要求改为与 /workspace/admin 一致的 admin/admin 账号密码登录。"
        }
      ],
      "legacy_refs": [
        "p2-89"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-91",
      "title": "n8n 入向鉴权统一为 X-N8n-Request-Token / n8n_request_token",
      "status": "active",
      "owner": "zac",
      "summary": "用户决定：SupportPortal 全部 n8n 入向端点统一只接受 X-N8n-Request-Token 头，值只来自单一环境变量 n8n_request_token，其余机制不再接受。实施：backend/main.py 的 Zendesk 评论/状态同步三端点鉴权重写为 require_n8n_request_token（删除 X-Zendesk-Account-Sync-Token 头与 Authorization: Bearer 回退，废弃 ZENDESK_ACCOUNT_SYNC_TOKEN）；两个 automation runtime 的 _require_execution_token 与 /v1/auth/login 改读 n8n_request_token 并校验 X-N8n-Request-Token（废弃三个 AUTOMATION_*_EXECUTION_TOKEN）；三个 automation UI 的请求头改为 X-N8n-Request-Token；compose 映射、deploy_ec2.sh 必填校验、本地 split 启动脚本与 verify 探针同步更新；n8n 契约文档与 T4 切流设计 §6 改写为单一机制方案。出向 Slack handoff 本就使用同头同值，无需改动。",
      "next_action": "代码与测试完成，待 finalize 合并后与 p2-90 同一 release 构建（release-20260823-005 之后顺延）部署三环境；EC2 .env 删除四个废弃 token 变量；n8n 切换窗口内把 commen_sync/case_status_sync 各 4 处与 /automation/*/v1/cases 投递节点统一挂 X-N8n-Request-Token 凭据（值=现有 n8n_request_token）；验证旧头 401、新头 200/404、verify_split_environments.sh 全绿。",
      "acceptance_criteria": [
        "旧 Zendesk 同步三端点（comment-sync-target/PUT comments/PUT status）只接受 X-N8n-Request-Token == env n8n_request_token；X-Zendesk-Account-Sync-Token、Authorization: Bearer 一律 401；未配置 n8n_request_token 时 503。",
        "三个 automation runtime 的全部执行/查询端点与 /v1/auth/login 换取的 token 均基于 n8n_request_token + X-N8n-Request-Token 头；AUTOMATION_EXECUTION_TOKEN/AUTOMATION_*_EXECUTION_TOKEN 不再被读取。",
        "三个 automation UI 的登录后 API 调用发送 X-N8n-Request-Token；登录换 token 流程（p2-90）不受影响。",
        "deploy_ec2.sh 对 n8n_request_token 必填校验；compose 三环境服务注入 n8n_request_token；本地启动脚本只生成/引用 n8n_request_token；verify_split_environments.sh 401 负例探针使用 X-N8n-Request-Token。",
        "文档同步：deploy_automation_release.md、三份 n8n 契约文档、automation_environments_cutover.md §6、.env.example（删除 ZENDESK_ACCOUNT_SYNC_TOKEN）均为单一机制表述。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Unified auth targeted regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_zendesk_status_sync backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_deploy_ec2 backend.tests.test_split_environment_deployment backend.tests.test_single_host_compose backend.tests.test_build_automation_release backend.tests.test_route_service_contract",
          "details": "2026-08-23 前六套件 76 项：失败 7 项与干净 main 同命令基线完全一致（test_deploy_ec2 的 DSN/顺序耦合 6 项 + test_account_zendesk_status_sync 硬编码日期断言 1 项，均为存量、非本任务引入，已记入 p2-88 history）；新增失败 0。后三套件 35 项全绿。py_compile 三个后端文件、node --check 三份 app.js、bash -n 三个部署/工作流脚本、git diff --check 均通过。"
        },
        {
          "type": "decision",
          "label": "Unified token mechanism choice",
          "command": "用户决策（对话确认）",
          "details": "用户选定：统一使用 X-N8n-Request-Token、别的机制都不接受；automation 环境值来源直接读 n8n_request_token（单变量贯穿，含 compose/deploy/本地脚本契约变更），而非保留三个 AUTOMATION_*_EXECUTION_TOKEN 同值。"
        }
      ],
      "source_refs": [
        "backend/main.py",
        "backend/automation_runtime.py",
        "backend/automation_production_runtime.py",
        "backend/services/account_slack_n8n.py",
        "ui/automation-staging/app.js",
        "ui/automation-preproduction/app.js",
        "ui/automation-production/app.js",
        "deployment/docker-compose.single-host.yml",
        "deployment/deploy_ec2.sh",
        "deployment/verify_split_environments.sh",
        "scripts/workflow/start_local_split_environments.sh",
        "docs/integrations/n8n/automation_environments_cutover.md",
        "docs/deploy_automation_release.md",
        ".env.example"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "T4 切流设计（PR#859）原定同值贯穿 5 变量 + Bearer 凭据；用户改选单一机制（X-N8n-Request-Token + n8n_request_token，其余不接受），并确认 automation 端直接读 n8n_request_token，遂立本任务实施服务端与配套变更。"
        }
      ],
      "legacy_refs": [
        "p2-88",
        "p2-85",
        "p2-90"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-92",
      "title": "三环境控制台补齐旧端点侧栏 Rerun 与路由两级过滤",
      "status": "done",
      "owner": "zac",
      "summary": "用户观测三环境控制台与 /account、/production 仍有差异：侧栏缺 Rerun 按钮（只有 Reset environment），过滤器是执行状态而非旧 UI 的路由两级过滤。本任务：侧栏新增 Rerun（对全部 execution 逐个 POST /v1/reruns，确认弹窗+进度面板，capabilities 驱动，production 物理排除）；过滤器改为 /account 同款路由两级过滤（route category 主组按钮+计数、subcategory 下拉+计数），后端 list_executions 增加 route_category/route_subcategory 过滤与 route_counts。",
      "next_action": "已完成。代码/测试/镜像构建齐备；按用户 2026-08-23 指示，后续改动不再部署 EC2、由用户在本地验证（scripts/workflow/start_local_split_environments.sh）。EC2 停留在 release-20260823-006；含路由字段修复的 release-20260823-007 镜像已构建未部署（如需上线执行 deploy_ec2.sh --release release-20260823-007 即可）。",
      "acceptance_criteria": [
        "GET /v1/executions 支持 route_category/route_subcategory 过滤并返回 route_counts（各 category 计数）与选中 category 的 subcategory 计数，均与当前页同快照；status/case 过滤保持兼容。",
        "侧栏按钮组为 New execution + Rerun（capabilities.rerun 时显示）+ Reset environment（仅 staging）+ Sign out；Rerun 打开确认弹窗（冻结目标数量，preproduction 提示会写 internal Zendesk 评论），确认后逐个 rerun 全部 execution 并在侧栏显示进度面板（processed/total、succeeded/failed、失败明细、aria-live）。",
        "过滤器与 /account 同构：紧凑主组按钮（All+各 route category，含 facet count）+ 单个二级 subcategory 下拉（含计数，无子类目时禁用）；切换过滤重置到第一页。",
        "production UI bundle 仍不含 rerun 字符串；production 无 Rerun 按钮（镜像物理排除契约保持）。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Route filter and sidebar rerun contract regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_execution_store backend.tests.test_automation_contracts backend.tests.test_split_environment_deployment && node --check ui/automation-{staging,preproduction,production}/app.js",
          "details": "33 项测试通过。新增覆盖：GET /v1/executions 的 route_category/route_subcategory 过滤、route_counts 与选中 category 的 route_subcategory_counts 同快照返回；production runtime 同参数透传。三份 app.js node --check 通过、staging/preproduction 主体一致、production bundle 无 rerun 字符串（全量 rerun 代码全部位于剥离块内，中性变量名 bulkActionButtonHtml/bulkStatusHtml）。"
        },
        {
          "type": "deployment",
          "label": "Release-006 rollout and route-field fix handling",
          "command": "EC2 build/deploy release-20260823-006（commit=05591ab）+ verify + 探针；修复 PR#868 后构建 release-20260823-007（commit=0a079b2，未部署）",
          "details": "2026-08-23 部署 release-006：verify 全绿；admin/admin 登录 200、旧 Bearer 头 401、新 X-N8n-Request-Token 200（含并行 #866 鉴权变更上线）、production rerun 404、UI v5。线上探针发现 route_counts 全落 uncategorized（真实 router payload 用 scope_label/execution_action 而非 category/subcategory），PR#868 修复（SQL 与 Python helper 对齐 UI 徽标的 fallback 语义，测试补 scope_label 形态）并构建 release-007 镜像；随后用户指示改动不再部署 EC2、改为本地验证，007 保持未部署。"
        }
      ],
      "source_refs": [
        "backend/services/automation_execution_store.py",
        "backend/automation_runtime.py",
        "backend/automation_production_runtime.py",
        "ui/automation-staging",
        "ui/automation-preproduction",
        "ui/automation-production",
        "ui/account-ui",
        "design.md"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "ec2_deployment_waived_by_user",
          "summary": "用户指示：以后的改动不需要部署到 EC2，由用户在本地验证。release-007（含路由字段修复）镜像已构建但不部署。"
        },
        {
          "at": "2026-08-23",
          "event": "route_field_scope_label_fix",
          "summary": "release-20260823-006 线上探针发现 route_counts 全落 uncategorized：真实 router payload 无 category/subcategory 键，实际字段为 scope_label/execution_action（UI 徽标靠 fallback 才显示）。store 的 SQL 表达式与 Python helper 对齐 UI 取值语义（category→scope_label、subcategory→execution_action 逐级 fallback），测试补 scope_label 形态记录，随 release-20260823-007 部署。"
        },
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "用户反馈：三环境侧栏缺 Rerun（疑被 Reset environment 取代）、过滤器应为旧端点的路由两级过滤而非执行状态过滤。原拟 p2-91 编号已被并行 n8n 鉴权统一任务（PR#866）占用，改用 p2-92。"
        }
      ],
      "legacy_refs": [
        "p2-89",
        "p2-90"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-93",
      "title": "意外客户回复的 RAG 兜底与人工升级",
      "status": "active",
      "owner": "zac",
      "summary": "自动化多轮对话中客户回复意料之外的内容（如反问 what is appid?）时不再静默：先用 RAG 尝试回答（能答则走标准 reply job 管线回复客户），RAG 无法回答（含服务故障）时把 case 交回人工——production 写 internal note 并复用 route_ticket_back_to_queue 放回 Zendesk queue、本地置 human review 并取消 pending reply jobs；staging 仅本地人工标记。行为通过共享 service 落地，/account 与 /production 立即生效，split 三环境在承接客户对话后同源继承。",
      "next_action": "完成 fail-closed queue handoff 修复的 review/finalize、本地官方栈与 EC2 main stack 部署；随后用专用 Production 工单分别验证 grounded answer public delivery 和 insufficient-evidence private-note/route-back/readback。",
      "acceptance_criteria": [
        "意外回复（重路由为非 automation 路由、或命中同一 handler 但无字段进展且追问已问尽）时先调用 RAG；RAG answer 经 reply job 直发客户（production 走 Zendesk 公开评论与既有延迟），RAG escalate 时执行人工升级链。",
        "人工升级链：production 写 internal note（AI agent unable to handle this request, require human review. + 原因与客户原文摘要）、route_ticket_back_to_queue 放回 queue、本地 human_review_required + ownership released + 取消 pending reply jobs + workspace audit event；staging 仅本地标记，不出站 Zendesk。",
        "防抖：case 已 human_review_required 或 ownership 已 released_to_queue 时不重复触发；RAG 任何故障一律升级人工（fail-safe），ACCOUNT_REPLY_RAG_FALLBACK_ENABLED 可关闭回到旧行为。",
        "quota follow_up_count 二次兜底、suspension 两段确认、字段提取失败转人工等既有路径行为不变。",
        "意外回复检索必须通过 backend/skills/ragflow-docs-search/scripts/search.py 的 ticket-agent read-only endpoint；仅带有效 docs.agora.io 或 api-ref.agora.io 引用的 grounded answer 可发布，skill/模型/JSON/citation 任一失败继续 fail-closed 转人工，不回退到旧本地 RAG。"
      ],
      "blockers": [
        "修复尚未 merge/deploy；新的 Production Zendesk 测试工单尚未确定，因此 RAGFlow grounded answer 的公开投递以及 insufficient-evidence 的 private note、route-back 与 Zendesk readback 仍待验收。"
      ],
      "evidence": [
        {
          "type": "test",
          "label": "RAGFlow failure matrix and 12992-shaped queue handoff regression",
          "command": "../../.venv/bin/python -m pytest backend/tests/test_ragflow_docs_search_skill.py backend/tests/test_account_reply_rag_fallback.py backend/tests/test_account_human_review_escalation.py backend/tests/test_account_intake.py backend/tests/test_automation_comment_sync.py backend/tests/test_worker.py -q",
          "details": "325 tests + 66 subtests passed。覆盖无结果/证据不足、无效或非官方引用、缺 key、401/403、timeout、执行/搜索/生成/JSON 异常；12992 同形态（execution_action=rag、automation_handler=None、无 superseded handler）不再 skipped_inactive_handler，所有失败原因均调用 private-note + route_ticket_back_to_queue、置 human_review_required、释放 ownership、取消 pending jobs；main.py 与 split automation_account_reply_sync caller 均有回归。"
        },
        {
          "type": "test",
          "label": "Shared Account escalation handoff regression",
          "command": "../../.venv/bin/python -m pytest backend/tests/test_account_human_review_escalation.py backend/tests/test_account_reply_rag_fallback.py -q",
          "details": "RAG escalation 委托共享 Account Human Review service；覆盖 Production private note/queue route、staging 无 Zendesk side effect、独立失败和 outcome_unknown 门禁。"
        },
        {
          "type": "test",
          "label": "Reply RAG fallback service and intake regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_account_reply_rag_fallback backend.tests.test_account_intake",
          "details": "新增 account_reply_rag_fallback service 单测 7 项（answer/escalate 映射、RAG 故障升级、staging 仅本地标记、production note+route back+ownership、route back 失败不抛异常）与 test_account_intake 3 个新用例（RAG answer 创建 draft reply job、escalate 置 human_review_required、开关关闭保持静默旧行为）；test_account_intake 170 项全过（旧用例经 env 隔离维持原语义）。"
        },
        {
          "type": "test",
          "label": "Local live verification of both fallback paths",
          "command": "本地官方栈（lightweight + remote DB）走 /account 真实链路：enablement 追问 App ID 后发送反问/跑题回复",
          "details": "escalate 路径：'Thank you for checking.' 被重路由为 Conversation/Follow-up，真实 RAG 判 insufficient_evidence，case 置 human_review_required（not_automated_reason=reply_rag_fallback_escalation:insufficient_evidence），workspace audit 事件 account_reply_rag_fallback_escalation 落库（staging 模式跳过 Zendesk 出站）。answer 路径：'where can I find the App ID in the Agora console?' 触发真实 RAG answer，签名（Best Regards, Sid）剥离后经 publish_account_reply 原文直发为 assistant 消息（20 秒内可见），automation 状态 not_automated 保持不变。三次实测暴露并修复了直发链路的 persona v8 状态机问题（PR#872/#874/#876）与签名门禁（PR#877）。"
        },
        {
          "type": "deployment",
          "label": "Production live escalation on ticket 12931 (full chain)",
          "command": "EC2 主栈 /production（478b45d）+ Zendesk 工单 12931 + n8n 评论 snapshot 通道",
          "details": "用户指定测试工单 12931（Zac Enablememt Test）：n8n intake 自动建 AC-12931（enablement）→ AI 接管（assignee→AI agent）→ 追问 App ID 公开评论 ✅；客户真实回复 \"what is appid?\" 经评论快照触发 RAG 兜底，RAG 60s 超时按 fail-safe 升级人工：internal note（正文=指定文案+rag_error_timeout+客户原文，comment 52807992328212 public=false）、route_ticket_back_to_queue 成功（assignee 清空、group 恢复原组 27216253642772、status=open）、本地 case human_review_required、workspace audit account_reply_rag_fallback_escalation 完整落库。answer 路径（RAG 答案→production 公开评论）未在本工单触发（超时走向 escalate）；production RAG 链路耗时>60s，建议运维调大 ACCOUNT_REPLY_RAG_FALLBACK_TIMEOUT_SECONDS（如 120）后用新工单补测。另发现重复追问去重在部分路径未生效（模拟评论 -002 重复问 app_id），记为后续排查项。"
        },
        {
          "type": "deployment",
          "label": "Production RAG answer delivered as public comment on 12935",
          "command": "EC2 主栈 31745e3 + Zendesk 工单 12935 + n8n 评论快照触发",
          "details": "工单 12935（Enablement answer delivery test）完整闭环 answer 路径：n8n 自动 intake（AC-12935，enablement）→ AI 接管并公开追问 App ID → 客户反问 \"what is the App ID exactly?\" 经评论快照触发（processed）→ 重路由 rag → RAG 兜底 answer → rag_fallback_answer job 直发 → production 延迟后作为公开评论 52809771838100 发布（\"The App ID is the unique random string Agora generates in Agora Console...\"，public=true）→ delivery ledger 状态 delivered/is_public=true/comment id 一致。至此 p2-93 两条路径均在 production live 闭环（escalate=12931/12933，answer=12935）；过程共修复四层 automation 注册门（worker 投递门 PR#886、评论触发门 PR#888、InMemory+Postgres delivery ledger eligibility PR#889/#890）。"
        },
        {
          "type": "test",
          "label": "RAGFlow skill adapter and caller-path regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_ragflow_docs_search_skill backend.tests.test_account_reply_rag_fallback backend.tests.test_account_intake backend.tests.test_worker",
          "details": "295 tests passed；覆盖 skill 命令与 env 合同、grounded answer/citation 校验、检索耗尽总预算时禁止启动模型、超时与错误转人工、Account intake 及 reply worker 既有发布合同。"
        },
        {
          "type": "test",
          "label": "Missing-key fail-closed verification",
          "command": "在未配置 RAGFLOW_API_KEY 的进程中直接调用 try_rag_fallback_answer，并注入禁止执行的模型 invoker/job publisher",
          "details": "结果为 escalate / ragflow_skill_configuration；未调用模型、未创建 reply job、未产生客户发布。"
        },
        {
          "type": "test",
          "label": "Upstream ragflow-docs-search source parity",
          "command": "比较 AgoraIO-Support/AgentsGateway-Skills-Scripts@main 与本地 vendored 文件的 Git blob SHA",
          "details": "SKILL.md 均为 73682d9676d7b092bc80b09cf383943db0283f27；scripts/search.py 均为 96f34efcf200acc578651d043c3b837f19c8d4f1。"
        },
        {
          "type": "deployment",
          "label": "Credentialed RAGFlow retrieval and grounded answer on official local stack",
          "command": "官方 deployment local_lightweight 栈 9f55be557628：容器内 ticket-agent read-only search、RagflowDocsSearchSkillClient.query 与 try_rag_fallback_answer",
          "details": "仅确认 RAGFLOW_API_KEY 非空且容器已加载，不读取或输出值。ticket-agent 检索返回 6 条非空 docs.agora.io 结果；adapter 通过内建 endpoint 默认值返回 answer（494 字符、2 条官方引用）；deployed fallback 默认客户端返回 answer（682 字符、References 与官方文档 URL 均存在）。全过程未创建 case、reply job、delivery ledger 或 Zendesk 评论。官方 image/health/runtime ref 均匹配 9f55be557628，auxiliary stack 不存在。"
        },
        {
          "type": "deployment",
          "label": "Production RAGFlow deployment and container-level grounded answer verification",
          "command": "EC2 scripts/ops/deploy_surfaces_ec2.sh --skip-split + https://support.stellarix.space/health + deployment-api_production-1 container checks",
          "details": "将 RAGFLOW_BASE_URL 和非空 RAGFLOW_API_KEY 原子写入 EC2 .env（未读取或输出 key），仅部署 main stack 到 52e9d3595a0e。外部 /health 返回同 ref、/production/ HTTP 200；api_production 使用 localhost/supportportal-app:52e9d3595a0e，默认 client 为 RagflowDocsSearchSkillClient，容器已加载 ticket-agent endpoint 与非空 key。通用 Agora RTC token 问题的容器内 adapter 调用返回 answer、答案非空、2 条 docs.agora.io 引用；api_production 与 production workers 启动后 ERROR/Traceback 计数均为 0。未创建 case、reply job、delivery ledger 或 Zendesk 评论，因此客户公开投递/readback blocker 保留。"
        }
      ],
      "source_refs": [
        "backend/main.py",
        "backend/services/automation_account_reply_sync.py",
        "backend/services/account_reply_rag_fallback.py",
        "backend/services/ragflow_docs_search_skill.py",
        "backend/skills/ragflow-docs-search/SKILL.md",
        "backend/skills/ragflow-docs-search/scripts/search.py",
        "backend/services/zendesk_ticket_assignment.py",
        "backend/services/zendesk_comments.py",
        "backend/services/rag_service_client.py"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-24",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "按用户需求创建：意外回复先 RAG 后人工（放回 queue + internal note），复用 PR#840 route back 与现有 RAG/内部评论能力；任务号跳过 p2-89~92（已由并行线程占用）。"
        },
        {
          "at": "2026-08-23",
          "event": "live_answer_path_direct_publish_fix",
          "summary": "本地 /account 实测发现 draft-only RAG 答案 job 在 worker prepare 阶段落入 legacy 重新生成路径（resolve_support_message/persona 覆盖或转人工）；注册 rag_fallback_answer intent 并在 prepare 加直通分支：该 intent 且带 draft_content 的 job 跳过 legacy 与 persona 渲染，draft 原文直发。escalate 路径实测已闭环（真实 RAG 判 insufficient_evidence → human_review_required + audit 落库）。"
        },
        {
          "at": "2026-08-23",
          "event": "verbatim_publish_hardening",
          "summary": "实测继续暴露直发链路第二层：publish 阶段的 normalize 会为 rag_fallback_answer job 合成 reply_facts 并触发 persona 渲染与回复契约校验导致 manual_attention；修复为 prepare 直通时剥离 facts、publish 跳过 normalize，RAG 答案经 publish_account_reply 原文直发（新增 publish 层单测断言 content 逐字一致）。"
        },
        {
          "at": "2026-08-23",
          "event": "creation_layer_root_cause",
          "summary": "最终定位根因：create_account_reply_job 对任何带 intent 的 job 在创建时就写入 reply_facts 并绑定 persona_v8 pipeline 状态机，prepare/publish 层修复均拦不住 v8 状态机的契约校验（unsupported_account_reply_intent）；修复为 rag_fallback_answer intent 创建时不绑 facts/pipeline（status=scheduled 直发路径），并在 intake 用例断言 job 不进入 persona 管线。"
        },
        {
          "at": "2026-08-23",
          "event": "rag_answer_signature_strip",
          "summary": "创建层修复后实测到达发布门禁，被 assert_no_trailing_automation_signature 拦截（RAG 答案继承支持工程师 'Best Regards, Sid' 尾签，与 Account 自动化无签名风格不符）；fallback service 增加经典 signoff+身份尾块剥离（保留普通收尾句），并有单测锁定。"
        },
        {
          "at": "2026-08-23",
          "event": "local_live_verification_complete",
          "summary": "本地 /account 实测两条路径全部闭环（escalate→human_review+audit；answer→RAG 答案剥签名原文直发）。按用户指示不部署 EC2；production 真实 route back/internal note 验证待用户指定工单。"
        },
        {
          "at": "2026-08-23",
          "event": "production_live_escalation_verified",
          "summary": "工单 12931 上完成 production escalate 全链路验证（internal note+route back+human review+audit）；RAG 超时触发 fail-safe 属设计行为；answer 路径 production 侧待新工单补测。"
        },
        {
          "at": "2026-08-23",
          "event": "rag_timeout_root_cause_and_default_raise",
          "summary": "排查 12931 的 rag_error_timeout：api_production→rag_api 连通 0.01s、同问题预热后稳定 11-22s 返回 grounded answer，根因为请求恰落在 rag_api 主栈重启后的冷启动窗口（首查询懒加载 embedding/预热）叠加 60s 兜底超时偏紧；将 DEFAULT_FALLBACK_TIMEOUT_SECONDS 提至 120s 并补超时解析单测。"
        },
        {
          "at": "2026-08-23",
          "event": "production_answer_delivery_gate_fix",
          "summary": "12933 实测暴露 answer 路径最后一层：客户反问被重路由为 rag 路由后 case 不再是 registered automation，production Zendesk 投递门以 unregistered_automation 拦截 RAG 答案（本地发布成功但客户收不到）；修复为 rag_fallback_answer intent 的投递绕过该门（publish 与 recovery 循环都传 intent），并加投递门单测。"
        },
        {
          "at": "2026-08-23",
          "event": "comment_trigger_gate_for_not_automated",
          "summary": "12933 第二轮触发暴露第三层门：Zendesk 评论触发入口对非 registered automation 路由的 case 直接 ignored_unregistered_automation，RAG answer 后 case 处于 rag 路由/not_automated，客户后续评论无法再进入回复循环；修复为 not_automated 状态的 case 放行评论触发（继续重路由+RAG 兜底，escalate 后由 human_review/closed 状态自然终止）。"
        },
        {
          "at": "2026-08-23",
          "event": "delivery_ledger_eligibility_fix",
          "summary": "12934 实测暴露第四层门：publish_account_reply 的 production_delivery_eligible 同样以 is_registered_automation 为条件，rag 路由的 case 不建 delivery ledger，投递 claim 返回 delivery_ledger_missing；修复为 rag_fallback_answer intent 的发布同样建 ledger，并加 InMemory 集成用例断言 rerouted case 的 RAG 回复有 ledger 条目。"
        },
        {
          "at": "2026-08-23",
          "event": "postgres_ledger_eligibility_fix",
          "summary": "12935 实测发现 #889 只改了 InMemory 版 publish 的 delivery eligibility，生产 Postgres 版（publish_account_reply 内 SQL 事务段）仍以 is_registered_automation 为条件导致 delivery_ledger_missing；同款 rag_fallback_answer 例外补到 Postgres 版。"
        },
        {
          "at": "2026-08-23",
          "event": "production_answer_path_verified",
          "summary": "12935 上 answer 路径 production 全链路闭环（RAG 答案→公开评论 52809771838100→ledger delivered）；p2-93 双路径 production 验证完成，任务收尾。"
        },
        {
          "at": "2026-08-24",
          "event": "ragflow_docs_search_skill_integration_started",
          "summary": "按用户要求重新打开 p2-93：从 AgoraIO-Support/AgentsGateway-Skills-Scripts@main 原样安装 ragflow-docs-search 的 SKILL.md 与 scripts/search.py，计划仅替换意外回复的知识检索/grounded answer 适配层，保留既有 reply job、Production public delivery、签名门禁与 fail-closed 人工升级合同。"
        },
        {
          "at": "2026-08-24",
          "event": "ragflow_docs_search_skill_implementation_verified",
          "summary": "vendored 上游文件与 main blob 一致，RAGFlow adapter、Account intake 与 worker 共 295 项回归通过；缺 key 时确认 fail-closed 且无客户发布。任务因 RAGFLOW_API_KEY 尚未配置及真实 Production answer/readback 未验收而标记 blocked。"
        },
        {
          "at": "2026-08-24",
          "event": "ragflow_credentialed_retrieval_verified",
          "summary": "用户配置 key 后，官方本地栈完成真实 ticket-agent 检索、grounded answer 生成与 deployed fallback 默认客户端验证，官方引用与 References 合同均通过且无业务副作用。key blocker 已解除；任务继续 blocked，仅等待新的 Production 测试工单完成客户公开投递、ledger 与 Zendesk readback。"
        },
        {
          "at": "2026-08-24",
          "event": "ragflow_production_deployed",
          "summary": "EC2 主栈部署到 52e9d3595a0e，api_production 已加载 ticket-agent endpoint 与非空 key，默认 RagflowDocsSearchSkillClient 的无客户数据 grounded-answer 探针返回 answer 和 2 条 docs.agora.io 引用；未触发真实工单，Production 公开评论、delivery ledger 与 Zendesk readback 仍待用户指定新测试工单。"
        },
        {
          "at": "2026-08-25",
          "event": "ragflow_failure_queue_handoff_fix_verified",
          "summary": "定位 12992 同形态 case 的 escalation handler 被 execution_action=rag 污染，导致共享 Human Review active-handler 门禁返回 skipped_inactive_handler；修复 fallback handler origin，并在最终 answer 边界再次强制官方 citation。失败矩阵、12992 route-back 和 main/split 双 caller 共 325 tests + 66 subtests 通过，待 merge/deploy 与 Production readback。"
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
      "task_id": "p2-94",
      "title": "/v1/cases 兼容旧 /account 五字段 intake body",
      "status": "active",
      "owner": "zac",
      "summary": "用户决定：n8n 工作流切到三环境时 body 保持旧 /account 五字段投递（title/question/customer_email/source/customer_name，表单编码）不做任何修改。新增 backend/services/automation_intake_compat.py：/v1/cases 同步接受表单与同字段名 JSON，服务端完成旧 intake 同款推导（title→subject、source Zendesk URL→zendesk_ticket_id、request_id 缺省推导 n8n-zd-{ticket}（确定性幂等）、case_id 缺省推导 AC-{ticket}、无 source 时生成一次性 id 与旧 intake 行为一致）；两个 runtime 的 /v1/cases 改经统一解析器。extra=forbid 防呆保留（除被消费的 title/source 外未知字段仍 422）；production comment_visibility 仍强制显式（安全门不做服务端默认）；原生 JSON 契约不变。",
      "next_action": "代码与测试完成，待 finalize 合并后构建 release 部署 EC2 三环境（主栈不涉及该端点）；用户在 n8n 只改 URL 与鉴权头（body 原样）重发 staging 验证。",
      "acceptance_criteria": [
        "POST /automation/{env}/v1/cases 接受旧五字段表单 body：title→subject 映射、source 解析 zendesk_ticket_id、request_id/case_id 缺省按 n8n-zd-{ticket}/AC-{ticket} 推导，同 body 重发返回 200 idempotent_replay。",
        "同字段名 JSON body 走同一映射；显式提供的新契约字段优先于推导值。",
        "除 title/source 外未知字段仍 422（防呆保留）；JSON body 非法/非对象 422。",
        "production 仍强制显式 comment_visibility（旧 body 缺失即 422）；staging 传 comment_visibility 仍 422；preproduction allowlist/visibility 策略不变。",
        "cutover 设计文档 §2/§3.2/§4 更新为 body 零改动表述。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Legacy intake compat regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract backend.tests.test_automation_contracts backend.tests.test_split_environment_deployment",
          "details": "2026-08-23 新增 6 用例：staging 表单五字段投递推导（request_id=n8n-zd-12999/case_id=AC-12999/zendesk_ticket_id 来自 source/subject 来自 title）、同表单重放 idempotent_replay=true、JSON 旧字段名映射 + 未知字段 422、无 request_id/source 生成标识、production 表单缺 comment_visibility 422、production 表单带 internal 通过并完成映射。含存量共 36 项全部通过。"
        },
        {
          "type": "decision",
          "label": "Body compatibility decision",
          "command": "用户决策（对话确认）",
          "details": "用户两次追问 body 差异后明确要求\"就按照旧的/account的来\"。实现取舍：复用旧 intake 的 source→ticket 正则与 AC-{id}/幂等推导语义；唯一不妥协项=production comment_visibility 显式强制（p2-88 验收标准，防服务端静默默认客户可见性）。"
        }
      ],
      "source_refs": [
        "backend/services/automation_intake_compat.py",
        "backend/automation_runtime.py",
        "backend/automation_production_runtime.py",
        "backend/main.py",
        "docs/integrations/n8n/automation_environments_cutover.md"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "用户在 n8n 切流实测（staging 首次 401/路径问题后）提出 body 沿用旧 /account 五字段不修改；实施服务端兼容层，幂等与身份推导复用旧 intake 语义，production visibility 门保留。"
        }
      ],
      "legacy_refs": [
        "p2-88",
        "p2-91"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-95",
      "title": "zendesk_basic_auth 兼容裸 email:token 与 base64 双格式",
      "status": "active",
      "owner": "zac",
      "summary": "2026-08-23 production 三环境首次真实 Zendesk 写入暴露凭据格式错位：运行时代码 zendesk_comments.zendesk_basic_auth_header 期望 base64(\"email:token\")（.env.example 与 account_admin 描述也如此），但 EC2 .env 换 token 后存的是裸 email:token，且 verify_split_environments.sh 的凭据探针按裸值写（探针绿、写路径 zendesk_basic_auth_invalid 502，fail-closed 未写任何工单；主栈 api_production 写路径同潜伏）。修复：header 解析兼容双格式（值含 ':' 按裸值——base64 字母表不含 ':' 故无歧义；否则按 base64 解码），verify 探针镜像同一解析，.env.example 与 account_admin 描述同步为双格式表述。",
      "next_action": "代码与测试完成，待 finalize 合并后由用户经 scripts/ops/deploy_surfaces_ec2.sh 部署（主栈+三环境都吃到该代码）；EC2 .env 保持现状裸值即可，无需再转 base64。部署后需先 reconcile production 失败 execution exec-bf0c82e115af4c83ab6a49ac47c0fd41（12899，ledger 全 pending 未投递）再重试；preproduction 测试工单还需加入 PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST。",
      "acceptance_criteria": [
        "zendesk_basic_auth_header 接受裸 'user:token' 与 base64 两种形式，输出一致；'Basic ' 前缀仍被剥离；缺值 zendesk_basic_auth_missing；非 base64 且无 ':'、base64 解出无 ':'、用户名或密码为空 → zendesk_basic_auth_invalid。",
        "verify_split_environments.sh 凭据探针对两种格式的 .env 值均能构造正确 Authorization 头（不再强制要求 ':'）。",
        "staging 禁止 Zendesk 出站边界（zendesk_outbound_forbidden_staging）不变。",
        "文档/描述（.env.example、account_admin env 说明）改为双格式表述。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Dual-format credential regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_zendesk_basic_auth_header backend.tests.test_zendesk_comments backend.tests.test_zendesk_public_comment backend.tests.test_zendesk_ticket_assignment backend.tests.test_account_automation_ownership backend.tests.test_account_zendesk_comment_sync backend.tests.test_account_zendesk_status_sync",
          "details": "2026-08-23 新增 test_zendesk_basic_auth_header 7 用例（裸值/base64/Basic 前缀/缺值/三类 invalid）；与既有 zendesk_comments、public_comment、ticket_assignment、ownership、comment_sync、status_sync 套件共 96 项，除已登记的存量失败 test_status_flows_to_summary_and_detail_payloads（硬编码日期断言，p2-88 history 在案）外全部通过。bash -n verify 脚本通过。"
        },
        {
          "type": "decision",
          "label": "Tolerant parsing instead of env-only fix",
          "command": "线上证据三角定位：production 502 zendesk_basic_auth_invalid（代码侧 base64 解码失败）+ verify 探针 33/33 绿（探针按裸值编码）→ .env 实为裸值、两消费者格式期望相反。",
          "details": "选择代码兼容而非只改 .env：线上裸值已是既成部署状态，且探针与代码期望相反会在任一单向修复后留下误报/隐患；':' 判据在两种格式间无歧义（base64 字母表不含 ':'），兼容分支是封闭的两态判定而非开放回退。"
        }
      ],
      "source_refs": [
        "backend/services/zendesk_comments.py",
        "backend/services/account_admin.py",
        "deployment/verify_split_environments.sh",
        ".env.example"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "production 首次真实写入触发（12899 take_ownership 第一步即 502 zendesk_basic_auth_invalid，delivery ledger 全 pending 未投递）；定位为 .env 裸值 vs 代码 base64 期望的格式错位，探针与代码期望相反导致此前 36/36 绿掩盖；实施双格式兼容。"
        }
      ],
      "legacy_refs": [
        "p2-88",
        "p2-91",
        "p2-94"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-96",
      "title": "preproduction allowlist 支持 * 显式放行全部",
      "status": "active",
      "owner": "zac",
      "summary": "用户运营模式改为在 n8n 工作流侧过滤进入 preproduction 的工单，要求服务端不再强制工单白名单。validate_ticket_policy 的 preproduction 分支增加显式 opt-out：PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST=* 时放行任意工单（visibility 强制 internal 等其余策略不变）；逗号分隔名单语义不变；空/未配置保持 fail-closed 拒绝全部（不翻转安全默认）。deploy_automation_release.md 与 cutover 文档同步三态表述。",
      "next_action": "代码与测试完成，待 finalize 合并后部署（deploy_surfaces_ec2.sh）；随后 EC2 .env 设 PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST=* 并 recreate preproduction，用户在 n8n 侧重测（12899 在部署前已可通过现有名单门，容器内实测 PASS）。",
      "acceptance_criteria": [
        "PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST=* 时任意 zendesk_ticket_id 通过 preproduction 门控，comment_visibility 仍强制 internal（external 422）。",
        "逗号分隔工单号名单语义不变；空/未配置仍拒绝全部（fail-closed 默认不翻转）。",
        "staging/production 策略不受影响。",
        "文档三态表述（名单 / * / 空）落在 deploy_automation_release.md 与 cutover 文档。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Allowlist opt-out regression",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_contracts backend.tests.test_automation_runtime_contract backend.tests.test_automation_production_runtime_contract",
          "details": "新增 2 用例：* 放行任意工单且 visibility 强制 internal 不变；空 allowlist 保持拒绝全部。与既有 contracts/runtime/production 套件全部通过。"
        }
      ],
      "source_refs": [
        "backend/services/automation_contracts.py",
        "docs/deploy_automation_release.md",
        "docs/integrations/n8n/automation_environments_cutover.md"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "用户反馈 n8n 侧已过滤工单、要求服务端取消白名单强制；实现 * 显式 opt-out 而非翻转空值默认，保持误配置 fail-closed。此前 12899 已加入名单并 recreate（容器内实测 gate PASS），用户报错为修复前的旧请求。"
        }
      ],
      "legacy_refs": [
        "p2-88",
        "p2-94"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "account-production-environment"
    },
    {
      "schema_version": 2,
      "task_id": "p2-97",
      "title": "/automation/test 生产工单回归测试控制台与 Runbook",
      "status": "done",
      "owner": "zac",
      "summary": "新增 /automation/test 控制台（由 api_production 服务，复用 workspace 登录）：三个自动化分类各一键创建测试工单邮件（模板可编辑，主题默认带 [zac test] 前缀），通过专用测试邮箱（AUTOMATION_TEST_MAIL_* 独立 Graph 凭据，未配置 fail-closed）发送到 support@agoraio.zendesk.com；新表 supportportal.automation_test_tickets 追踪每次发送并按标题+时间窗自动关联 production case，快照路由/自动化/内部邮件/回复 job/Zendesk 状态；配套回归测试 Runbook（含步骤 0 基线探测与三类预期信号）。",
      "next_action": "用户侧跟进：① EC2/本地 .env 配置专用测试邮箱凭据（AUTOMATION_TEST_MAIL_* + token cache）并重启 api_production；② 按 runbook 步骤 0 用 enablement 模板做基线探测（验证新 requester 能进 n8n→production 管线）；③ 之后每轮大改动按 runbook 三类回归。",
      "acceptance_criteria": [
        "GET /api/automation-test/templates 返回三类模板（主题已应用 [zac test] 前缀）与测试邮箱配置状态；未登录 401。",
        "POST /api/automation-test/tickets：校验类目（未知类目 422）；发送成功落表 send_status=sent；发送失败/未配置凭据落表 failed+原因并返回 502，不静默重试。",
        "POST /api/automation-test/tickets/{id}/refresh：按标题+发送时间窗关联 production case，更新 zendesk 工单链接与管线快照；无匹配时 link_status=not_found。",
        "ui/automation-test 三件套挂载于 /automation/test（nginx 指向 api_production），登录复用 /production/api/workspace/*，创建前可编辑主题与正文。",
        "带前缀主题不破坏 enablement/suspension 确定性检测（单测覆盖）。",
        "docs/testing/production_ticket_regression_runbook.md 覆盖前置检查、基线探测、三类操作步骤与预期信号、手动后续回复与清理。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Console API + UI contract + prefix-safety",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "18 用例全过：未登录 401；templates 返回带 [zac test] 前缀的三类模板与邮箱配置状态；未知类目 422；发送成功落 sent、失败/未配置落 failed+原因且 502 不重试；refresh 按 production case 关联并快照（zendesk 链接/internal email/reply job intent），无匹配 not_found、失败发送不关联、未知 id 404；[zac test] 前缀不破坏 enablement 确定性检测；UI 契约（挂载/nginx 指向 api_production/版本戳/workspace 登录经 /production/api）。"
        },
        {
          "type": "test",
          "label": "Static page smoke via TestClient",
          "command": ".venv/bin/python -c \"from fastapi.testclient import TestClient; import backend.main as main; r=TestClient(main.app).get('/automation/test/')\"",
          "details": "GET /automation/test/ 200，含 \u003ctitle>Automation Test\u003c/title>，Cache-Control private no-store；app.js 静态资源 200。既有套件对照：test_account_zendesk_status_sync 与 test_production_ui_contract 各 1 个失败在干净 main 上同样失败（日期敏感/部署脚本断言，与本任务无关）。"
        }
      ],
      "source_refs": [
        "backend/main.py",
        "backend/services/automation_test_store.py",
        "backend/services/automation_test_mail.py",
        "backend/services/automation_test_templates.py",
        "backend/repositories/ticket_repository.py",
        "ui/automation-test/index.html",
        "ui/automation-test/styles.css",
        "ui/automation-test/app.js",
        "deployment/nginx/supportportal.conf",
        "backend/tests/test_automation_test_console.py",
        "backend/tests/test_automation_test_ui_contract.py",
        "docs/testing/production_ticket_regression_runbook.md"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "用户要求以真实邮件工单做生产回归测试（fraud/enablement/account suspension），并提供 /automation/test 页面（复用 /account 登录、独立追踪表、分类一键建单、可编辑内容）。取舍确认：专用测试邮箱、[zac test] 主题前缀、v1 只做创建+追踪。"
        },
        {
          "at": "2026-08-23",
          "event": "implemented",
          "summary": "api_production 挂载 ui/automation-test（nginx /automation/test location），4 个 workspace-admin API；automation_test_tickets 自包含建表；专用邮箱 AUTOMATION_TEST_MAIL_* fail-closed 发信；按标题+时间窗关联 production case 并快照管线；18 个新测试全过；runbook 落 docs/testing/。真实邮件 e2e 属用户侧跟进（凭据+基线探测）。"
        }
      ],
      "legacy_refs": [],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "production-regression-testing"
    },
    {
      "schema_version": 2,
      "task_id": "p2-98",
      "title": "测试控制台发信通道支持 SMTP（163 专用邮箱）",
      "status": "done",
      "owner": "zac",
      "summary": "/automation/test 的专用测试邮箱从 Graph 单通道扩展为 transport 可选（AUTOMATION_TEST_MAIL_TRANSPORT=graph|smtp，默认 graph 不变）：smtp 通道走 SMTP_SSL（163：smtp.163.com:465+授权码；QQ 同理），缺 host/username/password 任一项 fail-closed 报缺失键名；页面横幅与 templates 接口的 configured/missing_config_keys 自动随通道切换；graph 通道行为与 p2-97 完全不变。选型过程：用户原想的 QQ AI 邮箱（unabletodisplay@agent.qq.com）是 Agent Mail 产品，仅有 CLI/OAuth 无 SMTP，不可用作服务端发件通道；确认改用 .env 中已有完整凭据的 163 专用邮箱 xieziling97@163.com。",
      "next_action": "用户侧跟进：EC2 与本地 .env 填 AUTOMATION_TEST_MAIL_TRANSPORT=smtp + SMTP_HOST=smtp.163.com + USERNAME=xieziling97@163.com + PASSWORD=\u003c163 授权码>，部署/重启 api_production 后按 runbook 步骤 0 做 enablement 基线探测。",
      "acceptance_criteria": [
        "AUTOMATION_TEST_MAIL_TRANSPORT=smtp 时：配置齐全正常发送（SMTP_SSL+login+send_message，From=登录邮箱）；缺 host/username/password 任一项时 configured=false、missing_config_keys 列出对应 env 键、发送抛 AutomationTestMailError 且不落 sent。",
        "transport 非法值被拒绝并给出明确错误；graph 通道（默认）行为与 p2-97 完全不变（既有 18 个用例不改动仍通过）。",
        "发送失败原因完整保留在 AutomationTestMailError 与追踪表 send_error（无静默重试）。",
        ".env.example 与 runbook 前置检查更新两种通道的配置说明（163 当前采用，QQ 备选）。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "SMTP transport unit tests",
          "command": ".venv/bin/python -m unittest backend.tests.test_automation_test_console backend.tests.test_automation_test_ui_contract",
          "details": "新增 5 用例：缺 host/username/password fail-closed（configured=false+缺失键清单+AutomationTestMailError）；SMTP_SSL+login+send_message 成功（From/To/Subject 头与超时/端口断言）；context 默认 sender=SMTP_USERNAME；发送失败原因包裹进异常；非法 transport 拒绝。与 p2-97 既有 18 用例（graph 默认路径）合计 23 个全过。"
        }
      ],
      "source_refs": [
        "backend/services/automation_test_mail.py",
        "backend/tests/test_automation_test_console.py",
        ".env.example",
        "docs/testing/production_ticket_regression_runbook.md"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "用户确认 /automation/test 专用测试邮箱用 QQ 邮箱（Graph 不可用），需要 SMTP 通道；当时 ZCode shell 因 finalize 后 ENOENT 故障不可用，补丁以上会话只读方式备好并暂存 ~/Desktop/qq-smtp-patch-p2-98/。"
        },
        {
          "at": "2026-08-23",
          "event": "implemented",
          "summary": "重启后找回 agently 授权邮箱=unabletodisplay@agent.qq.com，确认为 Agent Mail（独立 AI 邮箱产品，仅 CLI/OAuth、无 SMTP、日限 50 封），不适合服务端发件；用户确认改用 .env 现成 163 专用邮箱（xieziling97@163.com）。SMTP transport 补丁按 worktree 流程应用，23 用例全过。"
        }
      ],
      "legacy_refs": [
        "p2-97"
      ],
      "legacy_ids": [],
      "phase_id": "phase-2",
      "module_id": "account-automation",
      "function_id": "production-regression-testing"
    },
    {
      "schema_version": 2,
      "task_id": "p2-99",
      "title": "RAG 兜底答案附带参考文档与营销尾部剥离",
      "status": "active",
      "owner": "zac",
      "summary": "p2-93 上线后实测（工单 12940）暴露两个答案质量问题：RAG 响应中的 citations（含 docs.agora.io 链接与标题）被 fallback 丢弃，客户看不到参考文档；RAG 答案模板的工程师签名后带多行营销尾部（feedback pitch/support plan/Discord 邀请），原短签名剥离规则覆盖不住，营销块被当成答案发到 Zendesk。修复：fallback 提取 citations 去重后以 References 列表附在答案尾部；签名剥离支持多行身份+营销 boilerplate 块（短行或含 agora.io/discord.gg 链接行）。",
      "next_action": "部署 EC2 主栈后在真实工单上验证：答案带 References 且无营销尾部。",
      "acceptance_criteria": [
        "RAG answer 的 citations 按 URL 去重后以 heading — url 形式附在答案尾部 References 列表。",
        "多行签名+营销尾部（May Collins/Agora Support Engineer/feedback/support-plans/Discord）整体剥离，正文与代码块不受影响。",
        "无 citations 的答案不出现空 References 段。"
      ],
      "blockers": [],
      "evidence": [
        {
          "type": "test",
          "label": "Citations append and marketing footer strip",
          "command": ".venv/bin/python -m unittest backend.tests.test_account_reply_rag_fallback backend.tests.test_account_intake backend.tests.test_worker",
          "details": "新增 3 项单测：12940 真实营销尾模板整块剥离（May Collins/Discord/support-plans 全部移除且正文保留）、citations 按 URL 去重并以 heading — url 附加 References、短签名规则回归；11 项 fallback 单测 + intake/worker 套件全绿。"
        }
      ],
      "source_refs": [
        "backend/services/account_reply_rag_fallback.py",
        "backend/tests/test_account_reply_rag_fallback.py"
      ],
      "created_at": "2026-08-23",
      "updated_at": "2026-08-23",
      "history": [
        {
          "at": "2026-08-23",
          "event": "created",
          "summary": "由 12940 实测反馈创建：参考文档丢失（RAG 有给 fallback 丢弃）+ 营销尾部未剥离。"
        }
      ],
      "legacy_refs": [
        "p2-93"
      ],
      "legacy_ids": [],
      "phase_id": "phase-1",
      "module_id": "account-automation",
      "function_id": "automation-execution-loop"
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
        "Fraud Account 自动化通过公司 Outlook reply 接收内部处理结果。",
        "Detailed Invoice 仅保留 Account & Billing 分类，不进入 Automation 执行；既有自动化实现保留供未来启用。",
        "Enablement 内部回复的完成识别支持任意语言与拼写容错：英文关键词正则保底，正则未命中时由 LLM 单次仲裁（失败或关闭时回退正则结果），命中即取消待发提交确认并走完成关单链路，判定来源写入审计事件。",
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
        "n8n 可将 Zendesk 工单状态幂等同步到 Account Case：/account 与 /production 的列表和详情显示 Zendesk 状态，solved/closed 联动关闭本地工单并停止 AI 自动回复，重开后自动恢复。",
        "Account Rerun 先冻结目标 Case，再以无网络副作用的 Account-only preflight 校验数据库、Prompt runtime 和 Luna profile；首个 Case 的只读 Prepare 执行首次模型请求，任何错误立即停止并展示准确的失败阶段与未处理数量，支持从冻结 checkpoint Resume。",
        "Account 入口强制使用当前 layered route 并记录 pipeline 版本；Agora Router 将安全、隐私、信任、审计和合规请求归入 Security & Compliance classification-only 路由，Account & Billing 子 Router 将请求细分为 Account Suspension、Fraud Account、Detailed Invoice 或 Other，Backend Operation/Automation Router 将明确后台操作细分为 Enablement、Quota 或 Unregistered。每次新建异步全量 Rerun 都会重新执行路由、字段提取和 handler reconciliation，并允许 Automation 重新发送内部邮件，同时保留单个 job 内的幂等和审计历史。",
        "Account 入口通过 external ID 或来源 ticket ID 幂等处理重复请求，避免重复建单和重复发送内部邮件。",
        "Account Case 仅在命中已注册 Automation 时执行 handler 和延迟客户回复；其他路由只记录标签并进入对应人工或后续处理目标。",
        "Account 自动化遇到 AI/API、结构化输出、字段处理、Persona 或内部处理链路故障时最多重试 3 次且不使用 fallback；失败会停止客户回复、取消待处理 reply job、转为 human review，并向指定负责人发送脱敏的幂等故障告警。",
        "Enablement 使用 LLM 从客户原文提取并校验字段证据，不限制 App ID 格式；缺失时生成上下文追问，不确定或多候选时转 Human Review。",
        "Account Verification 使用 LLM 收集公司、联系人、使用场景和安全支付概况，最多追问一次并阻止敏感支付凭据进入派生数据。",
        "/production 独立环境提供与 /account 相同的 Account 处理能力（无 Run in Production），经独立数据库、独立 worker 和同域名路径路由运行；n8n 可将工单直接转发到 production，AI 回复自动以真实 Zendesk 公开评论发送，closing 类回复同次写入并置工单为 solved，确认后才关闭本地工单。",
        "/account 的 Run in Production 按钮将 Case 以 n8n 同款 intake 转发到 production 环境，由 production 侧完成完整路由与 Zendesk 公开评论投递；staging 库内晋级（PRD Case）逻辑已移除。",
        "/automation/staging、/automation/preproduction、/automation/production 提供三套独立 Route/Automation 执行环境与控制台 UI（staging 对齐 /account 模板、preproduction/production 对齐 /production 模板）：Execution token 门、执行历史列表（状态过滤+计数、Case 搜索、分页）、详情视图（meta、问答时间线、delivery ledger）、rerun 真实现（staging/preproduction）与 reset（staging 清空执行记录）；执行与查询 API 强制 Bearer token，Production 镜像与 UI 物理排除 rerun，旧 /account 与 /production 入口保留。",
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
        "Engineer AI 会在 final approve 后生成 replay eval dataset candidate，包含 summary packet、review decision、replan/revise 轨迹和 approved reply。",
        "Production Non automated Case 会创建一个 active Engineer Case；SupportPortal 直接发送到固定 Slack Channel 并持久化 thread binding，n8n 只校验并转发固定 Team/Channel/thread 内的 `@bot` 指导与按钮交互。AI 草稿、两阶段批准、Zendesk public comment 和后续客户评论都回到同一 thread；发布一轮后 Engineer Case、派单和 thread 继续保持活跃。",
        "Production Fraud Account 和 Account Suspension 最终 handoff 在 Zendesk 客户回复确认后通过 n8n 通知 Slack。",
        "Production Automation 分类完成后会将 Case 链接、客户问题和分类 path 邮件通知负责人。"
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
        "`/workspace/admin` 将 Route Strategy 统一纳入 Agent Config，以 Agent-only 层级导航 Route Agent、Agora Router、Security & Compliance、Account & Billing Router、Backend Operation Router 与 Automation Router；Account Suspension、Fraud Account 和 Detailed Invoice 位于 Account & Billing Router 下，Security & Compliance 与 Detailed Invoice 作为 classification-only outcome 展示，Automation Workflow catalog 仅展示当前注册的执行/兜底流程。Account Prompt 支持 managed 版本管理，正式 skill 与 MCP 状态继续支持 Draft、Scheduled、Active、Diff、Restore 和历史版本管理，Scheduled Prompt 仅在下一次成功的每日部署后统一生效。",
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
        "Fraud Account 自动化通过公司 Outlook reply 接收内部处理结果。",
        "Detailed Invoice 仅保留 Account & Billing 分类，不进入 Automation 执行；既有自动化实现保留供未来启用。",
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
