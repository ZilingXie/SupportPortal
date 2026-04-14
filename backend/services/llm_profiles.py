from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

OPENAI_RESPONSES_API = "openai_responses"
OPENAI_CHAT_API = "openai_chat"
SILICONFLOW_CHAT_API = "siliconflow_openai_compatible_chat"

INTENT_ROUTER_SCENARIO = "intent_router"
WEB_SEARCH_SCENARIO = "web_search_non_technical"
CLIENT_ACK_SCENARIO = "client_ack"
TICKET_TITLE_SCENARIO = "ticket_title"
RAG_ANSWER_SCENARIO = "rag_answer"
RAG_SUFFICIENCY_SCENARIO = "rag_sufficiency_judge"
QUERY_EXPANSION_SCENARIO = "query_expansion"
RAG_AGENT_PLANNER_SCENARIO = "rag_agent_planner"
RAG_CONTEXT_COMPRESSION_SCENARIO = "rag_context_compression"
TROUBLESHOOTING_INTAKE_SCENARIO = "troubleshooting_intake"
ENGINEER_HELPER_SCENARIO = "engineer_helper"
ENGINEER_INVESTIGATION_REPLY_SCENARIO = "engineer_investigation_reply"
KNOWLEDGE_INGESTION_SCENARIO = "knowledge_ingestion_metadata"
BENCHMARK_JUDGE_SCENARIO = "benchmark_judge"
AUTO_DEPLOY_REPORT_SCENARIO = "auto_deploy_report"

ProviderName = Literal["openai", "siliconflow"]
ApiModeName = Literal[
    "openai_responses",
    "openai_chat",
    "siliconflow_openai_compatible_chat",
]

LOGGER = logging.getLogger(__name__)
_CONFIG_WARNINGS: set[str] = set()


@dataclass(frozen=True)
class ModelProfile:
    scenario: str
    provider: ProviderName
    model: str
    api_mode: ApiModeName
    api_key: str
    base_url: str | None = None
    reasoning_effort: str | None = None
    temperature: float | None = 0.0
    timeout_seconds: float = 20.0
    max_retries: int = 1
    fallback_models: tuple[str, ...] = ()

    def candidate_models(self) -> list[str]:
        ordered: list[str] = []
        for candidate in [self.model, *self.fallback_models]:
            clean = _clean_text(candidate)
            if clean and clean not in ordered:
                ordered.append(clean)
        return ordered


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _record_config_warning(message: str) -> None:
    normalized = _clean_text(message)
    if not normalized:
        return
    if normalized not in _CONFIG_WARNINGS:
        LOGGER.warning(normalized)
        _CONFIG_WARNINGS.add(normalized)


def _warn_if_deprecated_alias_used(preferred_name: str, *deprecated_names: str) -> None:
    preferred = _clean_text(os.getenv(preferred_name))
    if preferred:
        return
    hits = [name for name in deprecated_names if _clean_text(os.getenv(name))]
    if not hits:
        return
    alias_label = ", ".join(hits)
    noun = "aliases" if len(hits) > 1 else "alias"
    _record_config_warning(f"Using deprecated env {noun} {alias_label}; prefer {preferred_name}.")


def get_config_warnings() -> list[str]:
    return sorted(_CONFIG_WARNINGS)


def clear_config_warnings_for_testing() -> None:
    _CONFIG_WARNINGS.clear()


def _first_env_text(*names: str) -> str:
    for name in names:
        value = _clean_text(os.getenv(name))
        if value:
            return value
    return ""


def _safe_float_env(name: str, default: float) -> float:
    raw = _clean_text(os.getenv(name))
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _safe_positive_float_env(name: str, default: float) -> float:
    raw = _clean_text(os.getenv(name))
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _safe_int_env(name: str, default: int) -> int:
    raw = _clean_text(os.getenv(name))
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _openai_api_key() -> str:
    return _clean_text(os.getenv("OPENAI_API_KEY"))


def _siliconflow_api_key() -> str:
    return (
        _clean_text(os.getenv("SILICONFLOW_API_KEY"))
        or _clean_text(os.getenv("SILICONFLOW_KEY"))
        or _clean_text(os.getenv("SILLICONFLOW_KEY"))
        or _clean_text(os.getenv("siliconflow_key"))
        or _clean_text(os.getenv("silliconflow_key"))
    )


def _siliconflow_base_url() -> str:
    return _clean_text(os.getenv("SILICONFLOW_BASE_URL")) or "https://api.siliconflow.cn/v1"


def _safe_float_env_any(names: tuple[str, ...], default: float) -> float:
    for name in names:
        raw = _clean_text(os.getenv(name))
        if not raw:
            continue
        try:
            parsed = float(raw)
        except ValueError:
            continue
        return parsed if parsed >= 0 else default
    return default


def _safe_positive_float_env_any(names: tuple[str, ...], default: float) -> float:
    for name in names:
        raw = _clean_text(os.getenv(name))
        if not raw:
            continue
        try:
            parsed = float(raw)
        except ValueError:
            continue
        return parsed if parsed > 0 else default
    return default


def _safe_int_env_any(names: tuple[str, ...], default: int) -> int:
    for name in names:
        raw = _clean_text(os.getenv(name))
        if not raw:
            continue
        try:
            parsed = int(raw)
        except ValueError:
            continue
        return parsed if parsed > 0 else default
    return default


def parse_provider_model_reference(value: str, *, default_provider: ProviderName = "openai") -> tuple[ProviderName, str]:
    raw = _clean_text(value)
    if not raw:
        raise ValueError("provider/model reference is required")
    if ":" not in raw:
        return default_provider, raw
    provider, model = raw.split(":", 1)
    normalized_provider = _clean_text(provider).lower()
    normalized_model = _clean_text(model)
    if normalized_provider not in {"openai", "siliconflow"} or not normalized_model:
        raise ValueError(f"invalid provider/model reference: {value}")
    return normalized_provider, normalized_model


def resolve_model_profile(
    scenario: str,
    *,
    provider: ProviderName | None = None,
    model: str | None = None,
) -> ModelProfile:
    if scenario == INTENT_ROUTER_SCENARIO:
        _warn_if_deprecated_alias_used("ROUTE_AGENT_ROUTER_MODEL", "INTENT_ROUTER_MODEL")
        _warn_if_deprecated_alias_used("ROUTE_AGENT_ROUTER_REASONING_EFFORT", "INTENT_ROUTER_REASONING_EFFORT")
        _warn_if_deprecated_alias_used("ROUTE_AGENT_ROUTER_TEMPERATURE", "INTENT_ROUTER_TEMPERATURE")
        _warn_if_deprecated_alias_used("ROUTE_AGENT_ROUTER_TIMEOUT_SECONDS", "INTENT_ROUTER_TIMEOUT_SECONDS")
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_first_env_text("ROUTE_AGENT_ROUTER_MODEL", "INTENT_ROUTER_MODEL") or "gpt-5.4-mini",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_first_env_text("ROUTE_AGENT_ROUTER_REASONING_EFFORT", "INTENT_ROUTER_REASONING_EFFORT") or "low",
            temperature=_safe_float_env_any(("ROUTE_AGENT_ROUTER_TEMPERATURE", "INTENT_ROUTER_TEMPERATURE"), 0.3),
            timeout_seconds=_safe_positive_float_env_any(("ROUTE_AGENT_ROUTER_TIMEOUT_SECONDS", "INTENT_ROUTER_TIMEOUT_SECONDS"), 8.0),
            max_retries=1,
        )
    if scenario == WEB_SEARCH_SCENARIO:
        _warn_if_deprecated_alias_used("ROUTE_AGENT_WEB_SEARCH_MODEL", "OPENAI_WEB_SEARCH_MODEL")
        _warn_if_deprecated_alias_used("ROUTE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS", "OPENAI_WEB_SEARCH_TIMEOUT_SECONDS")
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_first_env_text("ROUTE_AGENT_WEB_SEARCH_MODEL", "OPENAI_WEB_SEARCH_MODEL") or "gpt-5.4",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            temperature=None,
            timeout_seconds=_safe_positive_float_env_any(("ROUTE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS", "OPENAI_WEB_SEARCH_TIMEOUT_SECONDS"), 30.0),
            max_retries=1,
            fallback_models=("gpt-5.4-mini",),
        )
    if scenario == CLIENT_ACK_SCENARIO:
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_clean_text(os.getenv("CLIENT_ACK_MODEL")) or "gpt-5.4-nano",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_clean_text(os.getenv("CLIENT_ACK_REASONING_EFFORT")) or "none",
            temperature=0.0,
            timeout_seconds=_safe_positive_float_env("CLIENT_ACK_TIMEOUT_SECONDS", 5.0),
            max_retries=1,
            fallback_models=(),
        )
    if scenario == TICKET_TITLE_SCENARIO:
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_clean_text(os.getenv("TICKET_TITLE_MODEL")) or "gpt-5.4-nano",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort="none",
            temperature=0.0,
            timeout_seconds=_safe_positive_float_env("TICKET_TITLE_TIMEOUT_SECONDS", 2.0),
            max_retries=1,
            fallback_models=(),
        )
    if scenario == RAG_ANSWER_SCENARIO:
        _warn_if_deprecated_alias_used("RAG_AGENT_ANSWER_MODEL", "RAG_ANSWER_MODEL")
        _warn_if_deprecated_alias_used("RAG_AGENT_ANSWER_REASONING_EFFORT", "RAG_ANSWER_REASONING_EFFORT")
        _warn_if_deprecated_alias_used("RAG_AGENT_ANSWER_TIMEOUT_SECONDS", "RAG_REQUEST_TIMEOUT_SECONDS")
        _warn_if_deprecated_alias_used("RAG_AGENT_ANSWER_MAX_RETRIES", "RAG_OPENAI_MAX_RETRIES")
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_first_env_text("RAG_AGENT_ANSWER_MODEL", "RAG_ANSWER_MODEL") or "gpt-5.4",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_first_env_text("RAG_AGENT_ANSWER_REASONING_EFFORT", "RAG_ANSWER_REASONING_EFFORT") or "medium",
            temperature=0.0,
            timeout_seconds=_safe_positive_float_env_any(("RAG_AGENT_ANSWER_TIMEOUT_SECONDS", "RAG_REQUEST_TIMEOUT_SECONDS"), 20.0),
            max_retries=_safe_int_env_any(("RAG_AGENT_ANSWER_MAX_RETRIES", "RAG_OPENAI_MAX_RETRIES"), 1),
            fallback_models=("gpt-5.4-mini",),
        )
    if scenario == RAG_SUFFICIENCY_SCENARIO:
        _warn_if_deprecated_alias_used("REVIEW_AGENT_POSTCHECK_MODEL", "RAG_SUFFICIENCY_JUDGE_MODEL")
        _warn_if_deprecated_alias_used(
            "REVIEW_AGENT_POSTCHECK_REASONING_EFFORT",
            "RAG_SUFFICIENCY_JUDGE_REASONING_EFFORT",
        )
        _warn_if_deprecated_alias_used("REVIEW_AGENT_POSTCHECK_TEMPERATURE", "RAG_SUFFICIENCY_JUDGE_TEMPERATURE")
        _warn_if_deprecated_alias_used(
            "REVIEW_AGENT_POSTCHECK_TIMEOUT_SECONDS",
            "RAG_SUFFICIENCY_JUDGE_TIMEOUT_SECONDS",
        )
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_first_env_text("REVIEW_AGENT_POSTCHECK_MODEL", "RAG_SUFFICIENCY_JUDGE_MODEL") or "gpt-5.4",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_first_env_text("REVIEW_AGENT_POSTCHECK_REASONING_EFFORT", "RAG_SUFFICIENCY_JUDGE_REASONING_EFFORT") or "low",
            temperature=_safe_float_env_any(("REVIEW_AGENT_POSTCHECK_TEMPERATURE", "RAG_SUFFICIENCY_JUDGE_TEMPERATURE"), 0.0),
            timeout_seconds=_safe_positive_float_env_any(("REVIEW_AGENT_POSTCHECK_TIMEOUT_SECONDS", "RAG_SUFFICIENCY_JUDGE_TIMEOUT_SECONDS"), 8.0),
            max_retries=1,
            fallback_models=("gpt-5.4-mini",),
        )
    if scenario == QUERY_EXPANSION_SCENARIO:
        _warn_if_deprecated_alias_used("RAG_AGENT_QUERY_EXPANSION_MODEL", "RAG_QUERY_EXPANSION_MODEL")
        _warn_if_deprecated_alias_used(
            "RAG_AGENT_QUERY_EXPANSION_REASONING_EFFORT",
            "RAG_QUERY_EXPANSION_REASONING_EFFORT",
        )
        _warn_if_deprecated_alias_used(
            "RAG_AGENT_QUERY_EXPANSION_TEMPERATURE",
            "RAG_QUERY_EXPANSION_TEMPERATURE",
        )
        _warn_if_deprecated_alias_used(
            "RAG_AGENT_QUERY_EXPANSION_TIMEOUT_SECONDS",
            "RAG_QUERY_EXPANSION_TIMEOUT_SECONDS",
        )
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_first_env_text("RAG_AGENT_QUERY_EXPANSION_MODEL", "RAG_QUERY_EXPANSION_MODEL") or "gpt-5.4-mini",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_first_env_text("RAG_AGENT_QUERY_EXPANSION_REASONING_EFFORT", "RAG_QUERY_EXPANSION_REASONING_EFFORT") or "low",
            temperature=_safe_float_env_any(("RAG_AGENT_QUERY_EXPANSION_TEMPERATURE", "RAG_QUERY_EXPANSION_TEMPERATURE"), 0.0),
            timeout_seconds=_safe_positive_float_env_any(("RAG_AGENT_QUERY_EXPANSION_TIMEOUT_SECONDS", "RAG_QUERY_EXPANSION_TIMEOUT_SECONDS"), 8.0),
            max_retries=1,
            fallback_models=(),
        )
    if scenario == RAG_AGENT_PLANNER_SCENARIO:
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_first_env_text("RAG_AGENT_PLANNER_MODEL", "RAG_AGENT_PLANNER_MODEL") or "gpt-5.4-mini",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_first_env_text("RAG_AGENT_PLANNER_REASONING_EFFORT", "RAG_AGENT_PLANNER_REASONING_EFFORT") or "low",
            temperature=_safe_float_env_any(("RAG_AGENT_PLANNER_TEMPERATURE", "RAG_AGENT_PLANNER_TEMPERATURE"), 0.0),
            timeout_seconds=_safe_positive_float_env_any(("RAG_AGENT_PLANNER_TIMEOUT_SECONDS", "RAG_AGENT_PLANNER_TIMEOUT_SECONDS"), 6.0),
            max_retries=1,
            fallback_models=(),
        )
    if scenario == RAG_CONTEXT_COMPRESSION_SCENARIO:
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_first_env_text("RAG_AGENT_CONTEXT_COMPRESSION_MODEL", "RAG_CONTEXT_COMPRESSION_MODEL") or "gpt-5.4-mini",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_first_env_text("RAG_AGENT_CONTEXT_COMPRESSION_REASONING_EFFORT", "RAG_CONTEXT_COMPRESSION_REASONING_EFFORT") or "low",
            temperature=0.0,
            timeout_seconds=_safe_positive_float_env_any(("RAG_AGENT_CONTEXT_COMPRESSION_TIMEOUT_SECONDS", "RAG_CONTEXT_COMPRESSION_TIMEOUT_SECONDS"), 8.0),
            max_retries=1,
            fallback_models=(),
        )
    if scenario == TROUBLESHOOTING_INTAKE_SCENARIO:
        _warn_if_deprecated_alias_used("REVIEW_AGENT_INTAKE_MODEL", "TROUBLESHOOTING_INTAKE_MODEL")
        _warn_if_deprecated_alias_used(
            "REVIEW_AGENT_INTAKE_REASONING_EFFORT",
            "TROUBLESHOOTING_INTAKE_REASONING_EFFORT",
        )
        _warn_if_deprecated_alias_used(
            "REVIEW_AGENT_INTAKE_TEMPERATURE",
            "TROUBLESHOOTING_INTAKE_TEMPERATURE",
        )
        _warn_if_deprecated_alias_used(
            "REVIEW_AGENT_INTAKE_TIMEOUT_SECONDS",
            "TROUBLESHOOTING_INTAKE_TIMEOUT_SECONDS",
        )
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_first_env_text("REVIEW_AGENT_INTAKE_MODEL", "TROUBLESHOOTING_INTAKE_MODEL") or "gpt-5.4-mini",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_first_env_text("REVIEW_AGENT_INTAKE_REASONING_EFFORT", "TROUBLESHOOTING_INTAKE_REASONING_EFFORT") or "low",
            temperature=_safe_float_env_any(("REVIEW_AGENT_INTAKE_TEMPERATURE", "TROUBLESHOOTING_INTAKE_TEMPERATURE"), 0.0),
            timeout_seconds=_safe_positive_float_env_any(("REVIEW_AGENT_INTAKE_TIMEOUT_SECONDS", "TROUBLESHOOTING_INTAKE_TIMEOUT_SECONDS"), 8.0),
            max_retries=1,
            fallback_models=(),
        )
    if scenario == ENGINEER_HELPER_SCENARIO:
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_clean_text(os.getenv("ENGINEER_HELPER_MODEL")) or "gpt-5.4",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_clean_text(os.getenv("ENGINEER_HELPER_REASONING_EFFORT")) or "high",
            temperature=0.0,
            timeout_seconds=_safe_positive_float_env("OPENAI_REQUEST_TIMEOUT_SECONDS", 20.0),
            max_retries=_safe_int_env("OPENAI_MAX_RETRIES", 1),
            fallback_models=("gpt-5.4-mini",),
        )
    if scenario == ENGINEER_INVESTIGATION_REPLY_SCENARIO:
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_clean_text(os.getenv("ENGINEER_INVESTIGATION_REPLY_MODEL")) or "gpt-5.4",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_clean_text(os.getenv("ENGINEER_INVESTIGATION_REPLY_REASONING_EFFORT")) or "medium",
            temperature=0.0,
            timeout_seconds=_safe_positive_float_env("ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS", 20.0),
            max_retries=_safe_int_env("ENGINEER_INVESTIGATION_REPLY_MAX_RETRIES", 1),
            fallback_models=("gpt-5.4-mini",),
        )
    if scenario == KNOWLEDGE_INGESTION_SCENARIO:
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_clean_text(os.getenv("KNOWLEDGE_INGESTION_MODEL")) or "gpt-5.4-mini",
            api_mode=OPENAI_CHAT_API,
            api_key=_openai_api_key(),
            temperature=0.0,
            timeout_seconds=_safe_positive_float_env("OPENAI_REQUEST_TIMEOUT_SECONDS", 20.0),
            max_retries=_safe_int_env("OPENAI_MAX_RETRIES", 1),
            fallback_models=(),
        )
    if scenario == BENCHMARK_JUDGE_SCENARIO:
        resolved_provider = provider or "openai"
        resolved_model = _clean_text(model) or "gpt-5.4"
        if resolved_provider == "siliconflow":
            return ModelProfile(
                scenario=scenario,
                provider="siliconflow",
                model=resolved_model,
                api_mode=SILICONFLOW_CHAT_API,
                api_key=_siliconflow_api_key(),
                base_url=_siliconflow_base_url(),
                temperature=0.0,
                timeout_seconds=_safe_positive_float_env("RAG_BENCHMARK_JUDGE_TIMEOUT_SECONDS", 30.0),
                max_retries=1,
            )
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=resolved_model,
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            temperature=0.0,
            timeout_seconds=_safe_positive_float_env("RAG_BENCHMARK_JUDGE_TIMEOUT_SECONDS", 30.0),
            max_retries=1,
        )
    if scenario == AUTO_DEPLOY_REPORT_SCENARIO:
        return ModelProfile(
            scenario=scenario,
            provider="openai",
            model=_clean_text(os.getenv("DEPLOY_REPORT_MODEL")) or "gpt-5.4-mini",
            api_mode=OPENAI_RESPONSES_API,
            api_key=_openai_api_key(),
            reasoning_effort=_clean_text(os.getenv("DEPLOY_REPORT_REASONING_EFFORT")) or "low",
            temperature=0.0,
            timeout_seconds=_safe_positive_float_env("DEPLOY_REPORT_TIMEOUT_SECONDS", 15.0),
            max_retries=_safe_int_env("DEPLOY_REPORT_MAX_RETRIES", 1),
            fallback_models=(),
        )
    raise ValueError(f"unsupported model scenario: {scenario}")
