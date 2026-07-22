from __future__ import annotations

import json
from typing import Any

from backend.services.account_admin import ROUTER_PROMPT_VERSION
from backend.services.engineer_plan_agent import ENGINEER_PLAN_SKILLS
from backend.services.openai_input_guardrail import (
    INPUT_GUARDRAIL_PROMPT_VERSION,
    build_input_guardrail_system_prompt,
)
from backend.services.prompts.engineer_investigation_reply import (
    ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION,
    build_engineer_investigation_reply_system_prompt,
)
from backend.services.prompts.product_selection import (
    PRODUCT_SELECTION_PROMPT_VERSION,
    build_product_selection_system_prompt,
)
from backend.services.prompts.query_understanding import (
    build_query_decomposition_system_prompt,
    build_query_rewrite_system_prompt,
    build_self_query_system_prompt,
)
from backend.services.prompts.rag_agent_planner import build_rag_agent_planner_system_prompt
from backend.services.prompts.rag_answer import (
    INSUFFICIENT_EVIDENCE_REPLY,
    build_rag_answer_system_prompt,
)
from backend.services.prompts.rag_context_compression import build_rag_context_compression_system_prompt
from backend.services.prompts.rag_sufficiency import build_rag_sufficiency_system_prompt
from backend.services.prompts.request_body_evidence import (
    REQUEST_BODY_EVIDENCE_PROMPT_VERSION,
    build_request_body_evidence_system_prompt,
)
from backend.services.prompts.troubleshooting_intake import build_troubleshooting_intake_system_prompt
from backend.services.prompts.web_search import (
    PRODUCT_PORTFOLIO_ROUTE_REASON,
    build_web_search_system_prompt,
)
from backend.services.support_products import (
    build_support_product_intake_role,
    build_support_product_prompt_scope,
    build_support_product_rag_role,
    list_support_product_field_labels,
    list_support_products,
)
from backend.services.support_router_prompt import build_route_system_prompt


def _component(key: str, name: str, description: str, status: str = "active") -> dict[str, str]:
    return {"key": key, "name": name, "description": description, "status": status}


def _prompt(
    key: str,
    name: str,
    component_key: str,
    content: str,
    *,
    version: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "version": version,
        "component_key": component_key,
        "content": str(content or "").strip(),
        "metadata": dict(metadata or {}),
    }


def _client_prompts() -> list[dict[str, Any]]:
    prompts = [
        _prompt(
            "product-selection",
            "Product selection",
            "main-agent",
            build_product_selection_system_prompt(),
            version=PRODUCT_SELECTION_PROMPT_VERSION,
        ),
        _prompt("self-query", "Self-query planning", "rag-service", build_self_query_system_prompt()),
        _prompt(
            "query-rewrite",
            "Query rewrite",
            "rag-service",
            build_query_rewrite_system_prompt(query_policy="client_accuracy_first"),
            metadata={"variant": "client_accuracy_first"},
        ),
        _prompt(
            "query-decomposition",
            "Query decomposition",
            "rag-service",
            build_query_decomposition_system_prompt(),
        ),
        _prompt(
            "request-body-evidence",
            "Request body evidence",
            "rag-service",
            build_request_body_evidence_system_prompt(),
            version=REQUEST_BODY_EVIDENCE_PROMPT_VERSION,
        ),
        _prompt(
            "rag-context-compression",
            "RAG context compression",
            "rag-service",
            build_rag_context_compression_system_prompt(),
        ),
        _prompt(
            "rag-sufficiency",
            "RAG answer sufficiency",
            "review-agent",
            build_rag_sufficiency_system_prompt(),
        ),
        _prompt(
            "web-search",
            "Web search",
            "main-agent",
            build_web_search_system_prompt(response_language="en", official_only=False),
            metadata={"variant": "English, authoritative public sources"},
        ),
        _prompt(
            "web-search-product-portfolio",
            "Web search: product portfolio",
            "main-agent",
            build_web_search_system_prompt(
                response_language="en",
                official_only=True,
                route_reason=PRODUCT_PORTFOLIO_ROUTE_REASON,
            ),
            metadata={"variant": "English, official-only product portfolio"},
        ),
    ]
    answer_clarify_fields = list_support_product_field_labels(
        ["desired_outcome", "platform_or_sdk"]
    )
    for product in list_support_products():
        product_metadata = {"product": product.value, "product_label": product.label}
        prompts.extend(
            [
                _prompt(
                    f"rag-planner-{product.value}",
                    f"RAG planner: {product.label}",
                    "rag-service",
                    build_rag_agent_planner_system_prompt(
                        product_role=build_support_product_rag_role(product.value),
                        product_scope=build_support_product_prompt_scope(product.value),
                    ),
                    metadata=product_metadata,
                ),
                _prompt(
                    f"rag-answer-{product.value}",
                    f"RAG answer: {product.label}",
                    "rag-service",
                    build_rag_answer_system_prompt(
                        insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
                        product_role=build_support_product_rag_role(product.value),
                        product_scope=build_support_product_prompt_scope(product.value),
                    ),
                    metadata=product_metadata,
                ),
                _prompt(
                    f"troubleshooting-intake-{product.value}",
                    f"Troubleshooting intake: {product.label}",
                    "review-agent",
                    build_troubleshooting_intake_system_prompt(
                        intake_role=build_support_product_intake_role(product.value) or "",
                        product_scope=build_support_product_prompt_scope(product.value),
                        required_fields=list_support_product_field_labels(
                            list(product.intake_required_fields)
                        ),
                        answer_clarify_fields=answer_clarify_fields,
                    ),
                    metadata=product_metadata,
                ),
            ]
        )
    return prompts


def _persona_prompts(personas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    for persona in personas:
        persona_key = str(persona.get("persona_key") or "").strip()
        display_name = str(persona.get("display_name") or persona_key or "Persona").strip()
        published_version = persona.get("published_version")
        for version in list(persona.get("versions") or []):
            if not isinstance(version, dict):
                continue
            version_number = version.get("version")
            prompts.append(
                _prompt(
                    f"persona-{persona_key}-v{version_number}",
                    f"{display_name} v{version_number}",
                    "persona-registry",
                    json.dumps(version.get("content") or {}, ensure_ascii=False, indent=2, sort_keys=True),
                    version=str(version_number),
                    metadata={
                        "persona_key": persona_key,
                        "persona_name": display_name,
                        "enabled": bool(persona.get("enabled")),
                        "status": str(version.get("status") or "unknown"),
                        "is_published": str(version_number) == str(published_version),
                        "change_note": str(version.get("change_note") or ""),
                        "created_at": version.get("created_at"),
                        "published_at": version.get("published_at"),
                    },
                )
            )
    return prompts


def build_agent_config_payload(personas: list[dict[str, Any]]) -> dict[str, Any]:
    agents = [
        {
            "key": "route-agent",
            "kind": "agent",
            "name": "Route Agent",
            "description": "Classifies incoming support requests and selects the supported scope and execution route.",
            "status": "active",
            "components": [
                _component(
                    "route-classifier",
                    "Route classifier",
                    "LLM semantic classification followed by confidence and policy gates.",
                )
            ],
            "prompts": [
                _prompt(
                    "route-system",
                    "Route classifier",
                    "route-classifier",
                    build_route_system_prompt(),
                    version=ROUTER_PROMPT_VERSION,
                )
            ],
            "skills": [],
            "mcp_servers": [],
        },
        {
            "key": "client-agent",
            "kind": "agent",
            "name": "Client Agent",
            "description": "Coordinates customer ticket turns across routing, retrieval, answer generation, and review.",
            "status": "active",
            "components": [
                _component("main-agent", "Main Agent", "Owns the customer workflow and final action."),
                _component("rag-service", "RAG Service", "Retrieves and packs grounded Agora evidence."),
                _component("review-agent", "Client Review Agent", "Checks answer sufficiency and investigation readiness."),
            ],
            "prompts": _client_prompts(),
            "skills": [],
            "mcp_servers": [],
        },
        {
            "key": "engineer-agent",
            "kind": "agent",
            "name": "Engineer Agent",
            "description": "Supports internal investigations and prepares evidence-bound customer replies for engineer approval. The multi-agent Plan/Execute/Review path is disabled by default.",
            "status": "feature_gated",
            "components": [
                _component("engineer-reply", "Engineer Reply Agent", "Reviews engineer updates and prepares a safe draft."),
                _component("summary-agent", "Summary Agent", "Builds a structured investigation context packet."),
                _component("plan-agent", "Plan Agent", "Builds an allowlisted investigation plan; disabled by default.", "feature_gated"),
                _component("execute-agent", "Execute Agent", "Executes the plan and collects evidence; disabled by default.", "feature_gated"),
                _component("engineer-review-agent", "Review Agent", "Assesses evidence and decides whether to replan; disabled by default.", "feature_gated"),
            ],
            "prompts": [
                _prompt(
                    "engineer-investigation-reply",
                    "Engineer investigation reply",
                    "engineer-reply",
                    build_engineer_investigation_reply_system_prompt(),
                    version=ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION,
                )
            ],
            "skills": [
                {
                    "key": skill,
                    "name": skill.replace("_", " ").title(),
                    "description": "Allowlisted Engineer Plan/Execute skill.",
                }
                for skill in ENGINEER_PLAN_SKILLS
            ],
            "mcp_servers": [],
        },
        {
            "key": "guardrail-agent",
            "kind": "agent",
            "name": "Guardrail Agent",
            "description": "Screens unsafe customer input and validates engineer replies before final human approval.",
            "status": "active",
            "components": [
                _component("input-guardrail", "Input Guardrail", "Classifies unsafe or sensitive customer input."),
                _component("engineer-final-guardrail", "Engineer Final Guardrail", "No prompt; deterministic proof, citation, leakage, claim, and style checks."),
            ],
            "prompts": [
                _prompt(
                    "input-guardrail",
                    "Input guardrail classifier",
                    "input-guardrail",
                    build_input_guardrail_system_prompt(),
                    version=INPUT_GUARDRAIL_PROMPT_VERSION,
                )
            ],
            "skills": [],
            "mcp_servers": [],
        },
    ]
    related_services = [
        {
            "key": "billing-automation",
            "kind": "service",
            "name": "Billing Automation",
            "description": "Deterministic billing intake and response workflow. It is not an autonomous Agent.",
            "status": "active",
            "components": [
                _component("persona-registry", "Persona registry", "Versioned reply voice configuration for /account tickets.")
            ],
            "prompts": _persona_prompts(personas),
            "skills": [],
            "mcp_servers": [],
        }
    ]
    return {"agents": agents, "related_services": related_services}
