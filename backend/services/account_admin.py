from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.services.support_router import SupportRouteDecision
from backend.services.account_route_pipeline import (
    ACCOUNT_ROUTE_PIPELINE_VERSION,
    account_case_labels,
    account_route_metadata,
)
from backend.services.prompts.account_routing import (
    build_account_agora_system_prompt,
    build_account_billing_system_prompt,
    build_account_automation_system_prompt,
    build_account_backend_operation_system_prompt,
    build_account_intent_system_prompt,
)
from backend.services.automation_routing import (
    AUTOMATED_ROUTE_FAMILY,
    automation_metadata,
    is_registered_automation,
)


ROUTER_PROMPT_VERSION = ACCOUNT_ROUTE_PIPELINE_VERSION
ROUTING_STAGE_DESCRIPTIONS = {
    "intent_classifier": "Classifies Account messages as Conversation, Agora, or Uncertain.",
    "agora_router": "Classifies Agora cases as Technical, Security & Compliance, Account & Billing, Backend Operation, or Uncategorized.",
    "account_billing_router": "Classifies Account & Billing cases as Account Suspension, Fraud Account, Detailed Invoice, or Other.",
    "backend_operation_router": "Classifies explicit backend operations as Enablement, Quota, or Unregistered.",
    "automation_router": "Compatibility alias for legacy Automation payloads; new Account routes use the backend-operation taxonomy.",
    "final_route": "Records the route target and the primary and secondary Account labels.",
}
DEFAULT_PERSONA_KEY = "default-support"
ACCOUNT_PERSONA_PRESET_VERSION = "automation-persona-presets-v1"
_ACCOUNT_PERSONA_CONTENT_KEYS = frozenset({"instruction", "opener"})


class AccountPersonaUnavailableError(RuntimeError):
    """Raised when no enabled, genuinely published Account Persona is available."""


@dataclass(frozen=True, slots=True)
class AccountPersonaPreset:
    persona_key: str
    display_name: str
    instruction: str
    seed_marker: str

    @property
    def content(self) -> dict[str, str]:
        return {
            "instruction": self.instruction,
            "opener": "",
        }


ACCOUNT_PERSONA_PRESETS = (
    AccountPersonaPreset(
        persona_key="sid-precise",
        display_name="Sid Precise",
        instruction=(
            "Use a precise, composed, and professional support voice. State the current "
            "status clearly, then explain any information the customer needs to provide "
            "or the next step. Prefer concise, complete sentences and unambiguous wording. "
            "Avoid casual chatter, decorative language, vague reassurance, and promises "
            "not supported by the provided facts. Remain courteous and human; do not sound "
            "legalistic, cold, or robotic."
        ),
        seed_marker="Seeded Sid Precise preset v1",
    ),
    AccountPersonaPreset(
        persona_key="sid-bright",
        display_name="Sid Bright",
        instruction=(
            "Use a professional, upbeat, and energetic support voice. Keep the writing "
            "natural and concise, with positive momentum and varied sentence rhythm. "
            "Friendly contractions are acceptable when they sound natural, but do not use "
            "emoji, slang, exaggerated enthusiasm, excessive exclamation marks, or overly "
            "casual language. For sensitive or serious matters, reduce the energy and use "
            "a calm, respectful tone."
        ),
        seed_marker="Seeded Sid Bright preset v1",
    ),
    AccountPersonaPreset(
        persona_key=DEFAULT_PERSONA_KEY,
        display_name="Sid Warm",
        instruction=(
            "Use a warm, considerate, and reassuring support voice. Acknowledge the "
            "customer's request or patience naturally when supported by the provided "
            "facts, and explain the current status and next step in a personal, caring way. "
            "Avoid canned pleasantries, repetitive thanks or apologies, false empathy, and "
            "promises beyond the provided facts. Remain concise and professional, "
            "especially for sensitive matters."
        ),
        seed_marker="Seeded Sid Warm preset v1",
    ),
)
DEFAULT_PERSONA_CONTENT = ACCOUNT_PERSONA_PRESETS[2].content
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
    "zendesk_basic_auth": "Zendesk Basic Auth credential (literal username:token or its base64 form) used for server-side ticket updates; never returned as a value.",
    "ZENDESK_AI_ASSIGNEE_EMAIL": "Exact email of the active Zendesk Agent used by the Account ownership action.",
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
    "BILLING_AUTOMATION_ACCOUNT_VERIFICATION_EMAIL": "Internal destination for account-verification automation requests.",
    "BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL": "Internal destination for account-suspension automation requests.",
    "BILLING_AUTOMATION_DETAILED_INVOICE_EMAIL": "Internal destination for detailed-invoice automation requests.",
    "BILLING_AUTOMATION_INTERNAL_EMAIL": "Legacy fallback destination for billing automation requests.",
    "BILLING_AUTOMATION_EMAIL_FROM": "Sender address used by billing automation email.",
    "BILLING_AUTOMATION_MAIL_TRANSPORT": "Selects the transport used to send billing automation email.",
    "BILLING_AUTOMATION_REPLY_RECORD_PATH": "Filesystem path used to record polled billing reply metadata.",
    "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": "Internal destination for backend feature-enablement requests.",
    "QUOTA_AUTOMATION_INTERNAL_EMAIL": "Internal destination for quota and big-event capacity requests.",
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
    ("QUOTA_AUTOMATION_", "quota automation"),
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
    # Legacy billing rows may only carry automation_status. Preserve that
    # compatibility signal before normalized route_status defaults to false.
    legacy_automated = not ticket.get("route_family") and str(
        ticket.get("automation_status") or ticket.get("status") or ""
    ).strip().lower() in {"automation", "automated"}
    execution_action = (
        ticket.get("execution_action")
        or ticket.get("route")
        or ticket.get("subcategory")
    )
    route_status = str(ticket.get("route_status") or "").strip().lower()
    if route_status:
        if route_status != "automated":
            return False
        return is_registered_automation(
            route_family=ticket.get("route_family") or AUTOMATED_ROUTE_FAMILY,
            execution_action=execution_action,
        )
    metadata = automation_metadata(
        route_family=ticket.get("route_family"),
        execution_action=execution_action,
    )
    if metadata["route_status"] == "automated":
        return True
    return legacy_automated and is_registered_automation(
        route_family=AUTOMATED_ROUTE_FAMILY,
        execution_action=execution_action,
    )


ADMIN_AUTOMATION_SUBCATEGORY_ORDER = ("fraud_account", "enablement", "account_suspension")


def _admin_case_subcategory(record: dict[str, Any], secondary_label: str = "") -> str:
    raw_subcategory = str(record.get("subcategory") or "").strip().lower()
    if not raw_subcategory:
        label = secondary_label or account_case_labels(record)[1]
        if " / " in label:
            raw_subcategory = label.rsplit(" / ", 1)[-1].strip().lower().replace(" ", "_")
    return raw_subcategory


def account_automation_payload(
    repository: Any,
    *,
    page: int = 1,
    page_size: int = 50,
    route_status: str | None = None,
    category: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    processing_profile: str = "staging",
) -> dict[str, Any]:
    safe_page = max(1, int(page))
    safe_size = min(200, max(1, int(page_size)))
    all_cases = repository.list_account_cases(
        limit=100000,
        offset=0,
        processing_profile=processing_profile,
    )
    automated = sum(1 for item in all_cases if _is_automated(item))
    total = len(all_cases)
    subcategory_buckets = {
        key: {"total": 0, "automated": 0} for key in ADMIN_AUTOMATION_SUBCATEGORY_ORDER
    }
    for item in all_cases:
        bucket = subcategory_buckets.get(_admin_case_subcategory(item))
        if bucket is None:
            continue
        bucket["total"] += 1
        if _is_automated(item):
            bucket["automated"] += 1
    automation_subcategories = []
    for key in ADMIN_AUTOMATION_SUBCATEGORY_ORDER:
        bucket_total = subcategory_buckets[key]["total"]
        bucket_automated = subcategory_buckets[key]["automated"]
        automation_subcategories.append(
            {
                "subcategory": key,
                "label": key.replace("_", " ").title(),
                "total": bucket_total,
                "automated": bucket_automated,
                "not_automated": bucket_total - bucket_automated,
                "automation_rate": bucket_automated / bucket_total if bucket_total else 0,
            }
        )
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
            if normalized_category
            == str(
                account_route_metadata(
                    classification=item.get("route_classification"),
                    route_family=item.get("route_family"),
                    execution_action=item.get("execution_action") or item.get("route"),
                ).get("category")
                or item.get("category")
                or ""
            ).lower()
        ]
    if created_from:
        filtered = [item for item in filtered if str(item.get("created_at") or "") >= str(created_from)]
    if created_to:
        filtered = [item for item in filtered if str(item.get("created_at") or "") <= str(created_to)]
    start = (safe_page - 1) * safe_size
    def admin_case_view(item: dict[str, Any]) -> dict[str, Any]:
        record = dict(item)
        primary_label, secondary_label = account_case_labels(record)
        metadata = account_route_metadata(
            classification=record.get("route_classification"),
            route_family=record.get("route_family"),
            execution_action=record.get("execution_action") or record.get("route"),
        )
        raw_category = str(metadata.get("category") or record.get("category") or "").strip().lower()
        if secondary_label.startswith("Account & Billing /"):
            raw_category = "account_billing"
        elif secondary_label.startswith("Backend Operation /"):
            raw_category = "backend_operation"
        elif secondary_label.startswith("Automation /"):
            raw_category = "automation"
        elif secondary_label == "Security & Compliance" or raw_category == "security_compliance":
            raw_category = "security_compliance"
        elif primary_label == "Human Review":
            raw_category = "human_review"
        elif raw_category not in {
            "automation",
            "backend_operation",
            "account_billing",
            "security_compliance",
            "human_review",
        }:
            raw_category = "human_review"
        raw_subcategory = _admin_case_subcategory(record, secondary_label)
        classification = record.get("route_classification")
        route_reason_code = (
            classification.get("route_reason_code")
            if isinstance(classification, dict)
            else None
        )
        record.update(
            {
                "primary_label": primary_label,
                "secondary_label": secondary_label,
                "category_label": {
                    "automation": "Automation",
                    "backend_operation": "Backend Operation",
                    "account_billing": "Account & Billing",
                    "security_compliance": "Security & Compliance",
                    "human_review": "Human Review",
                }.get(raw_category, raw_category.replace("_", " ").title() or "-"),
                "subcategory_label": raw_subcategory.replace("_", " ").title() or "-",
                "route_reason_code": route_reason_code or record.get("route_reason_code"),
            }
        )
        return record

    return {
        "processing_profile": processing_profile,
        "metrics": {
            "total_account_cases": total,
            "automated_cases": automated,
            "not_automated_cases": total - automated,
            "automation_rate": automated / total if total else 0,
        },
        "automation_subcategories": automation_subcategories,
        "cases": [admin_case_view(item) for item in filtered[start : start + safe_size]],
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
    classification: dict[str, Any] | None = None,
    prompt_snapshots: dict[str, dict[str, str]] | None = None,
    stage_attempts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(classification, dict) and classification:
        stage_confidences = dict(classification.get("stage_confidences") or {})
        stage_reasons = dict(
            classification.get("stage_reason_codes")
            or classification.get("stage_reasons")
            or {}
        )
        attempt_records = dict(stage_attempts or {})
        stages = []
        for name, confidence in stage_confidences.items():
            attempt = attempt_records.get(name)
            failure_type = str(getattr(attempt, "failure_type", "") or "").strip() or None
            recovered = bool(getattr(attempt, "recovered", False))
            stage = {
                "name": name,
                "status": "failed" if failure_type else "completed_after_retry" if recovered else "completed",
                "confidence": confidence,
                "reason": stage_reasons.get(name),
                "failure_type": failure_type,
                "failure_source": str(getattr(attempt, "failure_source", "") or "").strip() or None,
                "attempt_count": max(1, int(getattr(attempt, "attempt_count", 1) or 1)),
                "recovered": recovered,
                "model": str(getattr(attempt, "model_name", "") or "").strip() or None,
                "provider": str(getattr(attempt, "provider_name", "") or "").strip() or None,
                "output_length": int(getattr(attempt, "raw_output_length", 0) or 0),
                "output_sha256": getattr(attempt, "raw_output_sha256", None),
                "output_excerpt": getattr(attempt, "sanitized_output_excerpt", None),
                "attempt_failures": [dict(item) for item in getattr(attempt, "attempt_failures", ())],
            }
            stages.append(stage)
        known_stage_names = {str(stage.get("name") or "") for stage in stages}
        for name, failure_type in dict(classification.get("stage_failure_types") or {}).items():
            if name in known_stage_names:
                continue
            stages.append(
                {
                    "name": name,
                    "status": "failed",
                    "confidence": 0.0,
                    "reason": stage_reasons.get(name),
                    "failure_type": str(failure_type),
                    "failure_source": (classification.get("stage_failure_sources") or {}).get(name),
                    "attempt_count": max(
                        1,
                        int((classification.get("stage_attempt_counts") or {}).get(name) or 1),
                    ),
                    "recovered": bool((classification.get("stage_recovered") or {}).get(name)),
                }
            )
        stages.append(
            {
                "name": "final_route",
                "status": str(classification.get("route_target") or "human_review"),
            }
        )
        snapshots = dict(prompt_snapshots or {})
        return {
            "execution_id": f"route-{uuid4().hex}",
            "ticket_id": str(ticket_id),
            "final_route": str(decision.execution_action or decision.route),
            "route_source": decision.router_source,
            "reason_code": str(classification.get("route_reason_code") or "").strip() or None,
            "degraded": bool(classification.get("degraded")),
            "degradation_stage": classification.get("degradation_stage"),
            "degradation_reason_code": classification.get("degradation_reason_code"),
            "classification": dict(classification),
            "confidence": decision.confidence,
            "confidence_threshold": decision.intent_router_confidence_threshold,
            "router_prompt_version": str(classification.get("pipeline_version") or ROUTER_PROMPT_VERSION),
            "prompt_snapshots": snapshots,
            "prompt_snapshot_available": bool(snapshots),
            "stages": stages,
            "created_at": created_at,
        }
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
        "reason_code": str(decision.reason or "").strip() or None,
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
    account_billing_subcategories = [
        "account_suspension",
        "fraud_account",
        "detailed_invoice",
        "other",
    ]
    automation_subcategories = ["enablement", "quota", "unregistered"]
    route_categories = [
        {
            "name": "conversation",
            "display_name": "Conversation",
            "description": "Conversation-only messages classified before support routing.",
            "execution_actions": ["resolve", "follow_up", "human_review"],
            "subcategories": [],
        },
        {
            "name": "intent",
            "display_name": "Intent Classifier",
            "description": "Account messages are classified as Conversation, Agora, or Uncertain.",
            "execution_actions": ["conversation", "agora", "uncertain"],
            "subcategories": [],
        },
        {
            "name": "agora",
            "display_name": "Agora Router",
            "description": "Agora cases are classified as Technical, Security & Compliance, Account & Billing, Backend Operation, or Uncategorized.",
            "execution_actions": ["technical", "security_compliance", "account_billing", "backend_operation", "uncategorized"],
            "subcategories": [],
        },
        {
            "name": "account_billing",
            "display_name": "Account & Billing Router",
            "description": "Account and billing requests are classified as Account Suspension, Fraud Account, Detailed Invoice, or Other.",
            "execution_actions": account_billing_subcategories,
            "subcategories": account_billing_subcategories,
            "handler_modes": {
                "account_suspension": "classification_only",
                "fraud_account": "billing",
                "detailed_invoice": "classification_only",
                "other": "none",
            },
        },
        {
            "name": "backend_operation",
            "display_name": "Backend Operation Router",
            "description": "Confirmed backend operations are classified into Enablement, Quota, or diagnostic Unregistered.",
            "execution_actions": automation_subcategories,
            "subcategories": list(automation_subcategories),
            "handler_modes": {
                "enablement": "active",
                "quota": "active",
                "unregistered": "human_review",
            },
        },
    ]

    return {
        "router_prompt_version": ROUTER_PROMPT_VERSION,
        "system_prompt": "\n\n---\n\n".join(
            [
                build_account_intent_system_prompt(),
                build_account_agora_system_prompt(),
                build_account_billing_system_prompt(),
                build_account_automation_system_prompt(),
                build_account_backend_operation_system_prompt(),
            ]
        ),
        "stages": list(ROUTING_STAGE_DESCRIPTIONS),
        "stage_details": [
            {"name": name, "description": description}
            for name, description in ROUTING_STAGE_DESCRIPTIONS.items()
        ],
        "route_categories": route_categories,
    }


def normalize_account_persona_content(
    content: dict[str, Any],
    *,
    allow_legacy_fields: bool = False,
) -> dict[str, str]:
    """Return the supported Persona fields and reject new legacy content."""
    if not isinstance(content, dict):
        raise ValueError("persona content must be an object")
    unsupported = sorted(set(content) - _ACCOUNT_PERSONA_CONTENT_KEYS)
    if unsupported and not allow_legacy_fields:
        raise ValueError(f"unsupported persona content fields: {', '.join(unsupported)}")
    instruction = str(content.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("persona instruction is required")
    return {
        "instruction": instruction,
        "opener": str(content.get("opener") or "").strip(),
    }


def apply_persona_to_customer_reply(reply: str, persona: dict[str, Any]) -> str:
    content = persona.get("content") if isinstance(persona.get("content"), dict) else {}
    opener = str(content.get("opener") or "").strip()
    normalized = str(reply or "").strip()
    if opener and opener not in normalized:
        salutation = re.match(r"^([^\n]+(?:,|：))\n\n", normalized)
        normalized = (
            f"{salutation.group(1)}\n\n{opener}\n\n{normalized[salutation.end():]}"
            if salutation
            else f"{opener}\n\n{normalized}"
        )
    return normalized.rstrip()
