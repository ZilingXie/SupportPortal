from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.services.support_router import SupportRouteDecision
from backend.services.support_router_prompt import build_route_system_prompt
from backend.services.customer_reply_composer import ensure_customer_reply_email_style
from backend.services.route_correction import VALID_ROUTE_TUPLES
from backend.services.automation_routing import automation_metadata


ROUTER_PROMPT_VERSION = "account-router-v2"
ROUTING_STAGE_DESCRIPTIONS = {
    "semantic_intent": "Classifies the request intent and captures the evidence used by the router.",
    "confidence_threshold": "Checks whether the model confidence is high enough to use the semantic result.",
    "policy_gate": "Applies automation and safety policy before an action can run.",
    "heuristic_fallback": "Uses deterministic signals when semantic routing is unavailable or uncertain.",
    "final_route": "Selects the execution action and tooling profile used to handle the request.",
}
ROUTE_CATEGORY_METADATA = {
    "automation": ("Automation", "Account cases handled by a registered automation subcategory."),
    "ticket_resolution": ("Ticket resolution", "Requests to resolve or close an existing support ticket."),
    "billing": ("Billing", "Account, invoice, verification, and billing review requests."),
    "agora_technical": ("Agora technical", "Technical Agora product and SDK questions handled with Agora documentation."),
    "agora_non_technical": ("Agora non-technical", "Agora company or product questions that may use official web sources."),
    "small_talk": ("Small talk", "Brief conversational messages that receive a controlled response."),
    "non_agora": ("Non-Agora", "Requests outside Agora support scope that are refused."),
}
DEFAULT_PERSONA_KEY = "default-support"
DEFAULT_PERSONA_CONTENT = {
    "instruction": "Use a calm, warm, polished concierge-style support voice. Match the customer's language.",
    "opener": "",
    "signoff_name": "Sid",
}
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_ENV_EXACT_DESCRIPTIONS = {
    "OPENAI_API_KEY": "Credential used to authenticate requests to the OpenAI API.",
    "TICKET_DB_DSN": "PostgreSQL connection string used by the ticket and workspace repository.",
    "TICKET_DB_MIGRATION_DSN": "Privileged PostgreSQL connection string used only for ticket schema migrations.",
    "PGVECTOR_DSN": "PostgreSQL connection string used by the pgvector knowledge store.",
    "WORKSPACE_AUTH_SECRET": "Secret used to sign and verify Workspace authentication tokens.",
    "WORKSPACE_PUBLIC_BASE_URL": "Public base URL used when SupportPortal creates Workspace links.",
    "WORKSPACE_BOOTSTRAP_ADMIN_ID": "Stable identifier for the bootstrap Workspace administrator account.",
    "WORKSPACE_BOOTSTRAP_ADMIN_NAME": "Display name for the bootstrap Workspace administrator account.",
    "WORKSPACE_BOOTSTRAP_ADMIN_PASSWORD": "Initial password for the bootstrap Workspace administrator account.",
    "STACK_RUNTIME_MODE": "Selects which SupportPortal services the single-host stack runs.",
    "STACK_DB_MODE": "Selects whether the single-host stack uses local or remote data services.",
    "NGINX_HOST_PORT": "Host port exposed by the SupportPortal Nginx gateway.",
    "ASYNC_QUERY_ENABLED": "Enables asynchronous processing for supported query workflows.",
    "OPTIMISTIC_PARALLEL_ROUTE_ENABLED": "Enables optimistic parallel execution in the support routing path.",
    "REDIS_URL": "Redis connection URL used by task queues and runtime coordination.",
    "TASK_QUEUE_NAME": "Queue name used for general asynchronous SupportPortal tasks.",
    "RAG_QUEUE_NAME": "Queue name used for asynchronous RAG tasks.",
    "EVENT_BUS_CHANNEL": "Redis channel used for SupportPortal runtime events.",
    "RAG_SERVICE_URL": "Internal base URL used to call the RAG service.",
    "RAG_SERVICE_SHARED_TOKEN": "Shared credential used to authenticate internal RAG service requests.",
    "APP_BUILD_REF": "Source revision identifier reported by the running application build.",
    "APP_BUILD_TIME": "Build timestamp reported by the running application.",
    "APP_RUNTIME_IMAGE": "Container image reference used to run SupportPortal application services.",
    "API_WORKERS": "Number of API worker processes started by the application server.",
    "RUNTIME_PROFILE": "Selects the SupportPortal runtime behavior profile.",
    "CLIENT_TICKET_AGENT_RUNTIME_MODE": "Selects the execution mode for the client ticket agent.",
    "LOCAL_KNOWLEDGE_ROOT": "Filesystem root containing knowledge sources for local ingestion.",
    "PRIMARY_CHUNK_STRATEGY": "Chunking strategy used by the primary knowledge index.",
    "SHADOW_CHUNK_STRATEGY": "Chunking strategy used by the shadow knowledge index.",
    "ASSET_STORAGE_PROVIDER": "Selects the storage backend used for uploaded support assets.",
    "ASSET_ALLOWED_EXTENSIONS": "File extensions accepted by the support asset upload API.",
    "ASSET_S3_KMS_KEY_ID": "AWS KMS key identifier used to encrypt uploaded S3 assets.",
    "AWS_ACCESS_KEY_ID": "AWS access key identifier used by configured AWS integrations.",
    "AWS_SECRET_ACCESS_KEY": "AWS secret access key used by configured AWS integrations.",
    "AWS_SESSION_TOKEN": "Temporary AWS session credential used by configured AWS integrations.",
    "DEPLOY_DOMAIN": "Public domain checked by the EC2 deployment workflow.",
    "DEPLOY_ALERT_FROM": "Sender address used for deployment alert email.",
    "DEPLOY_ALERT_TO": "Recipient address used for deployment alert email.",
    "ALERT_FROM_EMAIL": "Legacy alias for the deployment alert sender address.",
    "ALERT_TO_EMAIL": "Legacy alias for the deployment alert recipient address.",
    "BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL": "Legacy fallback destination for account-verification automation requests.",
    "BILLING_AUTOMATION_DETAILED_INVOICE_EMAIL": "Internal destination for detailed-invoice automation requests.",
    "BILLING_AUTOMATION_INTERNAL_EMAIL": "Legacy fallback destination for billing automation requests.",
    "BILLING_AUTOMATION_EMAIL_FROM": "Sender address used by billing automation email.",
    "BILLING_AUTOMATION_MAIL_TRANSPORT": "Selects the transport used to send billing automation email.",
    "BILLING_AUTOMATION_REPLY_RECORD_PATH": "Filesystem path used to record polled billing reply metadata.",
    "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": "Internal destination for backend feature-enablement requests.",
    "AUTOMATION_REPLY_POLL_ENABLED": "Enables polling for registered automation-handler email replies.",
    "AUTOMATION_REPLY_POLL_INTERVAL_SECONDS": "Interval between automation email reply polls.",
    "AUTOMATION_REPLY_POLL_MAX_MESSAGES": "Maximum unread messages inspected by each automation reply poll.",
    "DEPLOY_MIN_FREE_DISK_GB": "Minimum free disk space required before an EC2 deployment proceeds.",
    "DEPLOY_REPORT_ENABLE_AI": "Enables AI-generated analysis in deployment reports.",
    "DEPLOY_REPORT_LOG_SINCE": "Lookback window used when collecting logs for a deployment report.",
    "DEPLOY_REPORT_LOG_LINES_PER_SERVICE": "Maximum log lines collected from each service for a deployment report.",
    "DEPLOY_REPORT_TIMEZONE": "Timezone used for deployment report timestamps.",
    "TICKET_DB_APPLICATION_NAME": "PostgreSQL application name attached to ticket database connections.",
    "TICKET_DB_CONNECT_TIMEOUT": "Connection timeout used by the ticket database client.",
    "PGVECTOR_CONNECT_TIMEOUT": "Connection timeout used by the pgvector client.",
    "PGVECTOR_CONNECT_RETRIES": "Maximum number of pgvector connection attempts.",
    "PGVECTOR_CONNECT_RETRY_DELAY_SECONDS": "Delay between pgvector connection attempts.",
    "TICKET_WORKER_RAG_MAX_WAIT_SECONDS": "Maximum time a ticket worker waits for a RAG result.",
}

_ENV_PREFIX_DESCRIPTIONS = (
    ("ENABLEMENT_AUTOMATION_", "enablement automation"),
    ("AUTOMATION_REPLY_", "automation reply polling"),
    ("BILLING_AUTOMATION_", "billing automation"),
    ("ENGINEER_INVESTIGATION_REPLY_", "engineer investigation reply"),
    ("ENGINEER_ASSIGNMENT_", "engineer assignment"),
    ("ENGINEER_MULTI_AGENT_", "engineer multi-agent"),
    ("REQUEST_BODY_ANALYZER_", "request body analysis"),
    ("RAG_CONTEXT_COMPRESSION_", "RAG context compression"),
    ("RAG_QUERY_EXPANSION_", "RAG query expansion"),
    ("RAG_SUFFICIENCY_JUDGE_", "RAG sufficiency judging"),
    ("RAG_BENCHMARK_", "RAG benchmark"),
    ("RAG_RERANK_", "RAG reranking"),
    ("RAG_VECTOR_", "RAG vector retrieval"),
    ("RAG_SHADOW_", "RAG shadow retrieval"),
    ("RAG_KG_", "RAG knowledge-graph integration"),
    ("CLIENT_RAG_", "client RAG request"),
    ("CLIENT_ACK_", "automated client acknowledgement"),
    ("INTENT_ROUTER_", "account intent routing"),
    ("OPENAI_WEB_SEARCH_", "OpenAI web search"),
    ("OPENAI_", "OpenAI integration"),
    ("DEEPSEEK_", "DeepSeek fallback integration"),
    ("SILICONFLOW_", "SiliconFlow integration"),
    ("SILLICONFLOW_", "legacy SiliconFlow integration"),
    ("EMBEDDING_", "embedding generation"),
    ("KNOWLEDGE_", "knowledge ingestion"),
    ("KG_EMBEDDING_", "knowledge-graph embedding"),
    ("KG_LLM_", "knowledge-graph language model"),
    ("KG_NEO4J_", "knowledge-graph Neo4j"),
    ("KG_", "knowledge graph"),
    ("RAG_", "retrieval-augmented generation"),
    ("LOCAL_POSTGRES_", "local PostgreSQL"),
    ("LOCAL_TICKET_DB_", "local ticket database"),
    ("LOCAL_PGVECTOR_", "local pgvector"),
    ("LOCAL_NEO4J_", "local Neo4j"),
    ("LOCAL_KNOWLEDGE_", "local knowledge index"),
    ("TICKET_DB_", "ticket database"),
    ("PGVECTOR_", "pgvector knowledge store"),
    ("WORKSPACE_", "Workspace"),
    ("ASSET_S3_", "S3 asset storage"),
    ("ASSET_", "support asset storage"),
    ("AWS_", "AWS integration"),
    ("MSGRAPH_", "Microsoft Graph integration"),
    ("DEPLOY_REPORT_", "deployment report"),
    ("DEPLOY_", "EC2 deployment"),
    ("SENTIMENT_", "sentiment analysis"),
)

_ENV_SUFFIX_PURPOSES = (
    ("_API_KEY", "API credential"), ("_CLIENT_SECRET", "client credential"),
    ("_PASSWORD", "password credential"), ("_SHARED_TOKEN", "shared authentication token"),
    ("_TOKEN_CACHE", "authentication token cache path"), ("_DSN", "database connection string"),
    ("_BASE_URL", "service base URL"), ("_URI", "service connection URI"),
    ("_MODEL_ID", "model identifier"), ("_MODELS", "model selection list"),
    ("_MODEL", "model selection"), ("_PROVIDER", "provider selection"),
    ("_REASONING_EFFORT", "model reasoning-effort level"),
    ("_CONFIDENCE_THRESHOLD", "minimum confidence threshold"),
    ("_TIMEOUT_SECONDS", "timeout in seconds"), ("_TIMEOUT_MS", "timeout in milliseconds"),
    ("_POLL_INTERVAL_SECONDS", "polling interval in seconds"),
    ("_RECOVERY_WINDOW_SECONDS", "recovery window in seconds"),
    ("_RETRY_DELAY_SECONDS", "delay between retry attempts in seconds"),
    ("_MAX_WAIT_SECONDS", "maximum wait time in seconds"),
    ("_TTL_SECONDS", "retention duration in seconds"), ("_TTL_HOURS", "retention duration in hours"),
    ("_SLA_HOURS", "service-level target in hours"),
    ("_MAX_RETRIES", "maximum retry count"), ("_MAX_OUTPUT_TOKENS", "maximum generated token count"),
    ("_MAX_ATTACHMENTS", "maximum attachment count"), ("_MAX_MESSAGES", "maximum message count per poll"),
    ("_MAX_LOG_CHARS", "maximum captured log character count"), ("_MAX_BYTES", "maximum allowed size in bytes"),
    ("_MAX_SIZE", "maximum pool size"), ("_MIN_SIZE", "minimum pool size"),
    ("_BATCH_SIZE", "batch size"), ("_NUM_RESULTS", "result count"),
    ("_CANDIDATE_K", "retrieval candidate count"), ("_TOP_K", "top retrieval result count"),
    ("_TOP_N", "top result count"), ("_WINDOW_TOKENS", "context window token budget"),
    ("_BUFFER_TOKENS", "context safety buffer in tokens"),
    ("_RESERVE_TOKENS", "reserved output token budget"),
    ("_ENABLED", "feature toggle"), ("_DIMENSIONS", "embedding vector dimensions"),
    ("_DIM", "vector dimensions"), ("_SCHEMA", "database schema name"),
    ("_TABLE", "database table name"), ("_USERNAME", "service account username"),
    ("_USER", "service account username"), ("_CLIENT_ID", "OAuth client identifier"),
    ("_TENANT_ID", "OAuth tenant identifier"), ("_HOST_PORT", "host port mapping"),
    ("_PORT", "network port"), ("_REGION", "cloud region"),
    ("_BUCKET", "object storage bucket name"), ("_PREFIX", "object key prefix"),
    ("_QUEUE_NAME", "task queue name"), ("_PERCENT", "rollout percentage"),
    ("_PATH", "filesystem path"), ("_DIR", "filesystem directory"),
)


def environment_config_names(env_path: Path, *, required: bool = False) -> list[str]:
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        if required:
            raise
        return []
    names: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, _ = line.partition("=")
        name = name.strip()
        if separator and _ENV_KEY_RE.fullmatch(name):
            names.add(name)
    return sorted(names)


def environment_config_description(name: str) -> str:
    """Describe an environment key without reading or inferring its value."""
    normalized_name = name.upper()
    if normalized_name in _ENV_EXACT_DESCRIPTIONS:
        return _ENV_EXACT_DESCRIPTIONS[normalized_name]

    scope = "SupportPortal"
    remainder = normalized_name
    for prefix, description in _ENV_PREFIX_DESCRIPTIONS:
        if normalized_name.startswith(prefix):
            scope = description
            remainder = normalized_name[len(prefix):]
            break

    for suffix, purpose in _ENV_SUFFIX_PURPOSES:
        if normalized_name.endswith(suffix):
            return f"{purpose.capitalize()} used by the {scope} configuration."

    label = remainder.replace("_", " ").lower()
    return f"Runtime setting for {label} in the {scope} configuration."


def environment_config_entries(env_path: Path, *, required: bool = False) -> list[dict[str, str]]:
    return [
        {"name": name, "description": environment_config_description(name)}
        for name in environment_config_names(env_path, required=required)
    ]


def _is_automated(ticket: dict[str, Any]) -> bool:
    route_status = str(ticket.get("route_status") or "").strip().lower()
    if route_status:
        return route_status == "automated"
    metadata = automation_metadata(
        route_family=ticket.get("route_family"),
        execution_action=ticket.get("execution_action") or ticket.get("route"),
    )
    if metadata["route_status"] == "automated":
        return True
    return (
        not ticket.get("route_family")
        and str(ticket.get("automation_status") or ticket.get("status") or "").strip().lower()
        in {"automation", "automated"}
    )


def account_automation_payload(
    repository: Any,
    *,
    page: int = 1,
    page_size: int = 50,
    route_status: str | None = None,
    category: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> dict[str, Any]:
    safe_page = max(1, int(page))
    safe_size = min(200, max(1, int(page_size)))
    all_cases = repository.list_account_cases(limit=100000, offset=0)
    automated = sum(1 for item in all_cases if _is_automated(item))
    total = len(all_cases)
    filtered = list(all_cases)
    normalized_status = str(route_status or "").strip().lower()
    if normalized_status in {"automation", "automated"}:
        filtered = [item for item in filtered if _is_automated(item)]
    elif normalized_status == "not_automated":
        filtered = [item for item in filtered if not _is_automated(item)]
    normalized_category = str(category or "").strip().lower()
    if normalized_category:
        filtered = [
            item
            for item in filtered
            if normalized_category == str(item.get("category") or "").lower()
        ]
    if created_from:
        filtered = [item for item in filtered if str(item.get("created_at") or "") >= str(created_from)]
    if created_to:
        filtered = [item for item in filtered if str(item.get("created_at") or "") <= str(created_to)]
    start = (safe_page - 1) * safe_size
    return {
        "metrics": {
            "total_account_cases": total,
            "automated_cases": automated,
            "not_automated_cases": total - automated,
            "automation_rate": automated / total if total else 0,
        },
        "cases": filtered[start : start + safe_size],
        "page": safe_page,
        "page_size": safe_size,
        "total": len(filtered),
    }


def route_execution_from_decision(
    *,
    ticket_id: str,
    decision: SupportRouteDecision,
    system_prompt: str | None,
    user_prompt: str | None,
    created_at: str | None = None,
) -> dict[str, Any]:
    attempted = bool(decision.intent_router_attempted)
    stages = [
        {"name": "semantic_intent", "status": "attempted" if attempted else "skipped"},
        {"name": "confidence_threshold", "status": "passed" if attempted and not decision.intent_router_fallback_reason else "not_passed"},
        {"name": "policy_gate", "status": str(decision.policy_decision or "not_applicable")},
        {"name": "final_route", "status": str(decision.execution_action or decision.route)},
    ]
    return {
        "execution_id": f"route-{uuid4().hex}",
        "ticket_id": str(ticket_id),
        "final_route": str(decision.execution_action or decision.route),
        "route_source": decision.router_source,
        "semantic_intent": decision.semantic_intent,
        "automation_eligibility": decision.automation_eligibility,
        "policy_decision": decision.policy_decision,
        "confidence": decision.confidence,
        "confidence_threshold": decision.intent_router_confidence_threshold,
        "fallback_reason": decision.intent_router_fallback_reason,
        "failure_type": decision.intent_router_failure_type,
        "failure_source": decision.intent_router_failure_source,
        "matched_policy_rules": list(decision.matched_signals),
        "router_prompt_version": ROUTER_PROMPT_VERSION if attempted else None,
        "system_prompt": system_prompt if attempted else None,
        "user_prompt": user_prompt if attempted else None,
        "prompt_snapshot_available": bool(attempted and system_prompt and user_prompt),
        "stages": stages,
        "created_at": created_at,
    }


def routing_config_payload() -> dict[str, Any]:
    actions_by_category: dict[str, list[str]] = {}
    for route in VALID_ROUTE_TUPLES:
        category = (
            "automation"
            if route["route_family"] == "automated"
            else route["scope_label"]
        )
        action = route["execution_action"]
        actions = actions_by_category.setdefault(category, [])
        if action not in actions:
            actions.append(action)

    route_categories = []
    for name, actions in actions_by_category.items():
        display_name, description = ROUTE_CATEGORY_METADATA[name]
        route_categories.append(
            {
                "name": name,
                "display_name": display_name,
                "description": description,
                "execution_actions": actions,
                "subcategories": actions if name == "automation" else [],
            }
        )

    return {
        "router_prompt_version": ROUTER_PROMPT_VERSION,
        "system_prompt": build_route_system_prompt(),
        "stages": list(ROUTING_STAGE_DESCRIPTIONS),
        "stage_details": [
            {"name": name, "description": description}
            for name, description in ROUTING_STAGE_DESCRIPTIONS.items()
        ],
        "route_categories": route_categories,
    }


def apply_persona_to_customer_reply(reply: str, persona: dict[str, Any]) -> str:
    content = persona.get("content") if isinstance(persona.get("content"), dict) else {}
    signoff_name = str(content.get("signoff_name") or "Sid").strip() or "Sid"
    opener = str(content.get("opener") or "").strip()
    normalized = str(reply or "").strip()
    if opener and opener not in normalized:
        salutation = re.match(r"^([^\n]+(?:,|：))\n\n", normalized)
        normalized = (
            f"{salutation.group(1)}\n\n{opener}\n\n{normalized[salutation.end():]}"
            if salutation
            else f"{opener}\n\n{normalized}"
        )
    if signoff_name != "Sid" and re.search(r"\nSid\s*$", normalized):
        normalized = re.sub(r"\nSid\s*$", f"\n{signoff_name}", normalized)
    signoff_pattern = re.compile(r"(\n\n(?:Best [Rr]egards,|此致)\n)[^\n]+\s*$")
    if signoff_name != "Sid" and signoff_pattern.search(normalized):
        normalized = signoff_pattern.sub(lambda match: f"{match.group(1)}{signoff_name}", normalized)
    if re.search(r"\n(?:Sid|" + re.escape(signoff_name) + r")\s*$", normalized):
        return normalized
    return ensure_customer_reply_email_style(body=normalized, opener=opener or None, signoff_name=signoff_name)
