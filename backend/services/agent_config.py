from __future__ import annotations

from typing import Any

from backend.services.account_admin import routing_config_payload
from backend.services.account_route_pipeline import account_router_prompt_catalog
from backend.services.llm_profiles import ACCOUNT_ROUTE_SCENARIO, resolve_model_profile
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


CLIENT_ROUTE_PROMPT_VERSION = "account-router-v2"


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


def _route_node(
    key: str,
    name: str,
    description: str,
    *,
    kind: str,
    is_agent: bool,
    prompt_keys: list[str] | None = None,
    capabilities: list[dict[str, str]] | None = None,
    children: list[dict[str, Any]] | None = None,
    persona_scope: str | None = None,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "key": key,
        "name": name,
        "description": description,
        "kind": kind,
        "is_agent": is_agent,
        "status": "active",
        "prompt_keys": list(prompt_keys or []),
        "capabilities": list(capabilities or []),
        "children": list(children or []),
    }
    if persona_scope:
        node["persona_scope"] = persona_scope
    if workflow:
        node["workflow"] = dict(workflow)
    return node


def _route_agent_navigation() -> dict[str, Any]:
    human_review = lambda key, description: _route_node(
        key,
        "Human Review",
        description,
        kind="handoff",
        is_agent=False,
    )
    automation = _route_node(
        "automation-router",
        "Automation Router",
        "Coordinates the three registered Automation outcomes across Account & Billing and Backend Operation; Unregistered is a diagnostic fallback for requests without a registered workflow.",
        kind="router",
        is_agent=True,
        prompt_keys=[
            "account-backend-operation-router-system",
            "account-automation-router-system",
        ],
        persona_scope="account-automation",
        children=[
            _route_node(
                "enablement",
                "Enablement",
                "Extracts grounded enablement fields and runs the registered feature-enablement workflow.",
                kind="automation",
                is_agent=False,
                prompt_keys=["account-enablement-field-extractor-system"],
                capabilities=[
                    _component("enablement-handler", "Enablement Handler", "Submits a complete enablement request and prepares confirmation behavior."),
                ],
                workflow={
                    "category": "backend_operation",
                    "subcategory": "enablement",
                    "route_family": "automated",
                    "automation_handler": "enablement",
                    "status": "registered",
                    "steps": ["grounded field extraction", "internal handoff", "Persona ownership update"],
                },
            ),
            _route_node(
                "quota",
                "Quota",
                "Extracts quota and capacity requirements before dispatching the registered quota workflow.",
                kind="automation",
                is_agent=False,
                prompt_keys=["account-quota-field-extractor-system"],
                capabilities=[
                    _component("quota-handler", "Quota Handler", "Handles quota, concurrency, and Big Event capacity requests."),
                ],
                workflow={
                    "category": "backend_operation",
                    "subcategory": "quota",
                    "route_family": "automated",
                    "automation_handler": "quota",
                    "status": "registered",
                    "steps": ["grounded capacity extraction", "internal handoff", "Persona ownership update"],
                },
            ),
            _route_node(
                "unregistered",
                "Unregistered",
                "Records an unregistered backend operation for diagnosis. It remains Backend Operation / Unregistered and is not a registered Automation or Human Review filter member.",
                kind="fallback",
                is_agent=False,
                capabilities=[
                    _component("unregistered-diagnostic", "Unregistered diagnostic", "Surfaces Automation taxonomy gaps for operator review without assigning a registered handler."),
                ],
                workflow={
                    "category": "backend_operation",
                    "subcategory": "unregistered",
                    "route_family": "human_review",
                    "automation_handler": None,
                    "status": "fallback",
                    "steps": ["record candidate", "human review"],
                },
            ),
        ],
    )
    account_billing = _route_node(
        "account-billing-router",
        "Account & Billing Router",
        "Classifies Account & Billing requests as Account Suspension, Fraud Account, Detailed Invoice, or Other.",
        kind="router",
        is_agent=True,
        prompt_keys=["account-account-billing-router-system"],
        children=[
            _route_node(
                "account-suspension",
                "Account Suspension",
                "Extracts optional suspension details without sending email or customer replies.",
                kind="classification",
                is_agent=False,
                prompt_keys=["account-suspension-field-extractor-system"],
                capabilities=[
                    _component("classification-only", "Classification only", "Runs best-effort field extraction only."),
                ],
                workflow={
                    "category": "account_billing",
                    "subcategory": "account_suspension",
                    "route_family": "human_review",
                    "automation_handler": None,
                    "status": "classification_only",
                    "steps": ["optional context extraction", "human review"],
                },
            ),
            _route_node(
                "fraud-account",
                "Fraud Account",
                "Collects grounded fraud-review information, composes one follow-up, and applies payment safety checks.",
                kind="automation",
                is_agent=False,
                prompt_keys=["account-verification-field-extractor-system"],
                capabilities=[
                    _component("fraud-account-handler", "Fraud Account Handler", "Controls follow-up and internal handoff."),
                    _component("account-verification-payment-safety", "Payment Safety Validator", "Blocks sensitive payment credentials deterministically."),
                ],
                workflow={
                    "category": "account_billing",
                    "subcategory": "fraud_account",
                    "route_family": "automated",
                    "automation_handler": "billing",
                    "status": "registered",
                    "steps": ["grounded field extraction", "one follow-up maximum", "internal handoff", "Persona ownership update"],
                },
            ),
            _route_node(
                "detailed-invoice",
                "Detailed Invoice",
                "Classifies detailed invoice requests for downstream human handling without running Automation.",
                kind="classification",
                is_agent=False,
                prompt_keys=["account-detailed-invoice-field-extractor-system"],
                capabilities=[
                    _component("classification-only", "Classification only", "Records the Detailed Invoice classification without invoking its dormant handler."),
                ],
                workflow={
                    "category": "account_billing",
                    "subcategory": "detailed_invoice",
                    "route_family": "human_review",
                    "automation_handler": None,
                    "status": "classification_only",
                    "steps": ["record classification", "human review"],
                },
            ),
            _route_node(
                "account-billing-other",
                "Other",
                "Classifies other account and billing requests for downstream human handling.",
                kind="outcome",
                is_agent=False,
                workflow={
                    "category": "account_billing",
                    "subcategory": "other",
                    "route_family": "human_review",
                    "automation_handler": None,
                    "status": "human_review",
                    "steps": ["record classification", "human review"],
                },
            ),
        ],
    )
    agora = _route_node(
        "agora-router",
        "Agora Router",
        "Classifies Agora requests as Technical, Security & Compliance, Account & Billing, Backend Operation, or Uncategorized.",
        kind="router",
        is_agent=True,
        prompt_keys=["account-agora-router-system"],
        children=[
            _route_node("agora-technical", "Agora Technical", "Routes technical Agora product and SDK questions to technical support.", kind="outcome", is_agent=False),
            _route_node("security-compliance", "Security & Compliance", "Records security, privacy, trust, audit, and compliance requests for Human Review.", kind="outcome", is_agent=False, workflow={
                "category": "security_compliance",
                "subcategory": None,
                "route_family": "human_review",
                "automation_handler": None,
                "status": "classification_only",
                "steps": ["record classification", "human review"],
            }),
            account_billing,
            automation,
            human_review("agora-uncategorized", "Handles Agora requests that the router cannot categorize reliably."),
        ],
    )
    return _route_node(
        "route-agent",
        "Route Agent",
        "Routes Account Cases through layered classifiers and explicit human-review fallbacks.",
        kind="agent",
        is_agent=True,
        prompt_keys=["account-intent-classifier-system"],
        capabilities=[
            _component("account-intent-classifier", "Intent Classifier", "Classifies Account messages as Conversation, Agora, or Uncertain."),
        ],
        children=[
            _route_node("conversation-action", "Conversation Action", "Handles conversation-only messages before Agora support routing.", kind="outcome", is_agent=False),
            agora,
            human_review("intent-uncertain", "Handles Account messages whose intent cannot be classified reliably."),
        ],
    )


def _automation_workflow_catalog(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten workflow metadata for the Automation Router overview.

    The navigation tree remains responsible for ownership and deep links; this
    catalog gives the Admin overview one compact, complete view of registered
    Automation behavior, including billing-owned automated outcomes.
    """
    workflows: list[dict[str, Any]] = []

    def collect(current: dict[str, Any], owner: str) -> None:
        workflow = current.get("workflow")
        if isinstance(workflow, dict) and str(workflow.get("status") or "").strip().lower() in {
            "registered",
            "fallback",
        }:
            workflows.append(
                {
                    "key": current.get("key"),
                    "name": current.get("name"),
                    "description": current.get("description"),
                    "kind": current.get("kind"),
                    "owner_router": owner,
                    "prompt_keys": list(current.get("prompt_keys") or []),
                    "capabilities": list(current.get("capabilities") or []),
                    **dict(workflow),
                }
            )
        next_owner = current.get("name") if current.get("is_agent") else owner
        for child in current.get("children") or []:
            if isinstance(child, dict):
                collect(child, str(next_owner or owner))

    collect(node, str(node.get("name") or "Route Agent"))
    return workflows


def _build_agent_config_payload(personas: list[dict[str, Any]]) -> dict[str, Any]:
    account_route_prompts = [
        _prompt(
            item["key"],
            item["name"],
            item["component_key"],
            item["content"],
            version=item["version"],
            metadata={"managed": bool(item.get("managed", False)), "scope": "/account"},
        )
        for item in account_router_prompt_catalog()
    ]
    agents = [
        {
            "key": "route-agent",
            "kind": "agent",
            "name": "Route Agent",
            "description": "Routes Account Cases through layered classifiers while preserving the existing Client route.",
            "status": "active",
            "components": [
                _component(
                    "account-intent-classifier",
                    "Intent Classifier",
                    "Classifies /account messages as Conversation, Agora, or Uncertain.",
                ),
                _component(
                    "account-agora-router",
                    "Agora Router",
                    "Classifies Agora requests as Technical, Security & Compliance, Account & Billing, Backend Operation, or Uncategorized.",
                ),
                _component(
                    "account-account-billing-router",
                    "Account & Billing Router",
                    "Classifies /account billing requests as Account Suspension, Fraud Account, Detailed Invoice, or Other.",
                ),
                _component(
                    "account-automation-router",
                    "Automation Router",
                    "Coordinates three registered Automation outcomes and retains Unregistered as a diagnostic fallback.",
                ),
                _component(
                    "account-backend-operation-router",
                    "Backend Operation Router",
                    "Active routing stage behind the Automation Router; selects a registered operation or Unregistered.",
                ),
                _component(
                    "account-enablement-field-extractor",
                    "Enablement Field Extractor",
                    "Extracts grounded Enablement fields from customer-authored /account messages.",
                ),
                _component(
                    "account-quota-field-extractor",
                    "Quota Field Extractor",
                    "Extracts grounded quota, concurrency, and Big Event capacity details from /account history.",
                ),
                _component(
                    "fraud-account-handler",
                    "Fraud Account Handler",
                    "Controls one follow-up, Human Review, and internal handoff for fraud/risk account review.",
                ),
                _component(
                    "account-verification-field-extractor",
                    "Fraud Account Field Extractor",
                    "Extracts seven grounded, non-sensitive fraud-review information fields from /account history.",
                ),
                _component(
                    "account-suspension-field-extractor",
                    "Account Suspension Field Extractor",
                    "Best-effort extraction for classification-only non-fraud suspension cases.",
                ),
                _component(
                    "account-verification-payment-safety",
                    "Payment Safety Validator",
                    "Deterministically blocks sensitive payment credentials from derived Automation data.",
                ),
                _component(
                    "route-classifier",
                    "Client Route Classifier",
                    "Existing shared classifier used by /client and non-Account workflows.",
                )
            ],
            "prompts": [
                *account_route_prompts,
                _prompt(
                    "route-system",
                    "Client Route Classifier",
                    "route-classifier",
                    build_route_system_prompt(),
                    version=CLIENT_ROUTE_PROMPT_VERSION,
                    metadata={"scope": "/client"},
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
    route_navigation = _route_agent_navigation()
    route_runtime = routing_config_payload()
    account_profile = resolve_model_profile(ACCOUNT_ROUTE_SCENARIO)
    return {
        "agents": agents,
        "route_navigation": route_navigation,
        "automation_workflows": _automation_workflow_catalog(route_navigation),
        "route_runtime": {
            "router_prompt_version": route_runtime["router_prompt_version"],
            "stage_details": route_runtime["stage_details"],
            "account_profile": {
                "scope": "/account only",
                "model": account_profile.model,
                "reasoning_effort": account_profile.reasoning_effort,
                "timeout_seconds": account_profile.timeout_seconds,
                "temperature": account_profile.temperature,
                "boundary": "/client and shared legacy router use their existing profiles",
            },
        },
        "automation_personas": list(personas),
    }


def build_managed_prompt_catalog() -> list[dict[str, Any]]:
    payload = _build_agent_config_payload([])
    catalog: list[dict[str, Any]] = []
    for agent in payload["agents"]:
        for prompt in agent["prompts"]:
            if prompt.get("metadata", {}).get("managed") is False:
                continue
            catalog.append(
                {
                    "prompt_key": prompt["key"],
                    "name": prompt["name"],
                    "agent_key": agent["key"],
                    "component_key": prompt["component_key"],
                    "content": prompt["content"],
                    "editable": True,
                }
            )
    return catalog


def build_agent_config_payload(
    personas: list[dict[str, Any]],
    managed_prompts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = _build_agent_config_payload(personas)
    if managed_prompts is None:
        return payload

    by_key = {
        str(item.get("prompt_key") or ""): item
        for item in managed_prompts
        if isinstance(item, dict)
    }
    for agent in payload["agents"]:
        updated_prompts: list[dict[str, Any]] = []
        for prompt in agent["prompts"]:
            managed = by_key.get(prompt["key"])
            if managed is None:
                updated_prompts.append(prompt)
                continue
            active = managed.get("active_version") or {}
            scheduled = managed.get("scheduled_version") or None
            updated = dict(prompt)
            updated["content"] = str(active.get("content") or prompt["content"])
            updated["version"] = str(active.get("version") or prompt.get("version") or "") or None
            updated["metadata"] = {
                **dict(prompt.get("metadata") or {}),
                "managed": True,
                "status": "active",
                "active_version": active.get("version"),
                "scheduled_version": scheduled.get("version") if isinstance(scheduled, dict) else None,
                "versions": list(managed.get("versions") or []),
            }
            updated_prompts.append(updated)
        agent["prompts"] = updated_prompts
    payload["prompt_release"] = {
        "release_id": next(
            (
                item.get("active_release_id")
                for item in managed_prompts
                if isinstance(item, dict) and item.get("active_release_id")
            ),
            None,
        )
    }
    return payload
