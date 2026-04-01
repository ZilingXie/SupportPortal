# Prompt Change Log

This file is the canonical log for every prompt-related or model-related change in this repository.

For each new entry, record:
- Date
- Area or subsystem
- Prompt or model version
- Summary
- Reason
- Affected files or config
- Expected behavior change
- Verification

## 2026-04-01 - Client AI Prompt V2 modularization

- Area or subsystem: Client AI routing, non-technical web search, RAG answer generation, and RAG sufficiency judging
- Prompt or model version: `prompt-v2-modularized`
- Summary: Extracted the client AI prompt text into a dedicated `backend/services/prompts/` package and upgraded the router, web search, RAG answer, and RAG sufficiency prompts to a shared V2 format with explicit role locking, sectioned inputs, fallback instructions, and compact few-shot examples.
- Reason: The previous prompt setup was uneven. RAG prompting was relatively strong, but router and web-search prompting were flatter and more dependent on code-level fallbacks. Standardizing all four prompt surfaces reduces hallucination risk, makes routing behavior easier to reason about, and creates a stable place to track future prompt/model iterations.
- Affected files or config:
  - `AGENTS.md`
  - `backend/services/prompts/__init__.py`
  - `backend/services/prompts/router.py`
  - `backend/services/prompts/web_search.py`
  - `backend/services/prompts/rag_answer.py`
  - `backend/services/prompts/rag_sufficiency.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/support_router.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_sufficiency_prompt.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_prompt_guards.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_rag_sufficiency_judge.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Router prompt now explicitly says it only classifies and uses a stronger troubleshooting-to-`agora_technical` ambiguity policy.
  - Web search prompt is now explicitly source-grounded, official-source-first, and has a stronger `INSUFFICIENT` fallback contract.
  - RAG answer prompt now uses a sectioned template and few-shot examples while preserving the exact insufficient-evidence reply and the existing safe cross-platform guardrails.
  - RAG sufficiency prompt now explicitly says it only judges, never rewrites, and must choose `investigate` when in doubt.
  - No model names or model configuration values were changed in this entry.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_prompt_modules.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_prompt_guards.py backend/tests/test_rag_sufficiency_judge.py backend/tests/test_support_router.py backend/tests/test_ticket_orchestrator.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/prompts/__init__.py backend/services/prompts/router.py backend/services/prompts/web_search.py backend/services/prompts/rag_answer.py backend/services/prompts/rag_sufficiency.py backend/services/support_router_prompt.py backend/services/support_router.py backend/services/rag_qa.py backend/services/rag_sufficiency_prompt.py backend/services/rag_sufficiency_judge.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-ai-prompt-v2`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `podman exec deployment_api_1 python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10).read().decode())"`
  - `podman exec deployment_api_1 python -c "import json, urllib.request; payload=json.dumps({'customer_id':'prompt-smoke-web-4','message':'Who is Agora\\'s CEO?'}).encode(); req=urllib.request.Request('http://127.0.0.1:8000/api/tickets/query', data=payload, headers={'Content-Type':'application/json'}, method='POST'); print(urllib.request.urlopen(req, timeout=30).read().decode())"`
  - `podman exec deployment_api_1 python -c "import json, urllib.request; payload=json.dumps({'customer_id':'prompt-smoke-rag-4','message':'How do I join a channel?'}).encode(); req=urllib.request.Request('http://127.0.0.1:8000/api/tickets/query', data=payload, headers={'Content-Type':'application/json'}, method='POST'); print(urllib.request.urlopen(req, timeout=30).read().decode())"`
