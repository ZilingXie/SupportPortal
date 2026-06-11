from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StageResult:
    output_files: dict[str, Path]
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SchemaValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    entity_type_count: int
    edge_type_count: int


@dataclass(frozen=True)
class PromptRulesResult:
    output_files: dict[str, Path]
    prompt_rules: dict[str, str]


@dataclass(frozen=True)
class SampleQualityReport:
    entity_count: int
    edge_count: int
    entity_type_distribution: dict[str, int]
    edge_type_distribution: dict[str, int]
    entity_fallback_ratio: float
    zero_degree_ratio: float
    entity_not_found_ratio: float
    edge_entity_ratio: float
    avg_entities_per_chunk: float
    avg_edges_per_chunk: float
    edge_type_coverage: float
    cross_type_duplicates: dict[str, list[str]]
    entity_reject_reasons: dict[str, int]
    edge_reject_reasons: dict[str, int]
    conclusion: str


@dataclass(frozen=True)
class CheckItem:
    id: str
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PreflightResult:
    passed: bool
    checks: list[CheckItem]
    can_proceed: bool
    blocking_issues: list[CheckItem]


@dataclass(frozen=True)
class FinalReport:
    summary: dict[str, Any]
    entity_type_distribution: dict[str, int]
    edge_type_distribution: dict[str, int]
    zero_degree_breakdown: dict[str, list[dict[str, Any]]]
    cleanup_breakdown: dict[str, int]
    entity_reject_reasons_top10: list[tuple[str, int]]
    edge_reject_reasons_top10: list[tuple[str, int]]
    missing_types: dict[str, list[str]]
    overrepresented_edges: list[tuple[str, int]]
    new_entity_alignment_candidates: list[dict[str, Any]]
    schema_improvement_suggestions: list[str]


# ── Stage 5.5: Schema Decision Brief ──────────────────────────────────────


@dataclass(frozen=True)
class SchemaScore:
    """Static schema quality score — no extraction needed."""
    total: float  # 0.0-1.0 weighted total
    entity_count_score: float
    edge_count_score: float
    endpoint_completeness: float
    example_completeness: float
    filter_coverage: float
    reasoning_path_coverage: float
    orphan_penalty: float
    overgeneral_penalty: float
    dimension_scores: dict[str, float]
    warnings: list[str]


# ── Stage 6C: Schema Critic ───────────────────────────────────────────────


@dataclass(frozen=True)
class CriticIssue:
    severity: str  # 'critical' | 'major' | 'minor'
    category: str  # 'orphan_type' | 'missing_type' | 'overgeneral_relation' | ...
    description: str
    suggestion: str


@dataclass(frozen=True)
class CriticResult:
    issues: list[CriticIssue]
    overall_assessment: str  # 'ready' | 'minor_fixes' | 'major_rework'
    needs_repair: bool


# ── Stage 9: Local Dry-run ────────────────────────────────────────────────


@dataclass(frozen=True)
class LocalDryRunResult:
    entities: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    rejected_entities: list[dict[str, Any]]
    rejected_edges: list[dict[str, Any]]
    quality_report: SampleQualityReport
    sample_chunks_used: int


# ── Stage 10: Auto-fix ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SchemaFix:
    action: str  # 'add_entity_type' | 'remove_entity_type' | 'modify_entity_type' |
    # 'add_edge_type' | 'remove_edge_type' | 'modify_edge_type'
    target: str  # entity or edge type name
    changes: dict[str, Any]  # what to change
    reason: str


@dataclass(frozen=True)
class PromptFix:
    action: str  # 'add_entity_rule' | 'add_edge_rule' | 'modify_rule' | 'add_trigger_word'
    target: str  # rule name or edge type
    changes: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class FilterFix:
    action: str  # 'add_filter' | 'remove_filter'
    pattern: str
    description: str
    reason: str


@dataclass(frozen=True)
class SynonymFix:
    source_name: str
    target_official_name: str
    reason: str


@dataclass(frozen=True)
class AutoFixPlan:
    schema_fixes: list[SchemaFix]
    prompt_fixes: list[PromptFix]
    filter_fixes: list[FilterFix]
    synonym_fixes: list[SynonymFix]
    confidence: float  # how confident the auto-fix is


# ── Confidence ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfidenceReport:
    overall: float  # 0.0-1.0
    schema_confidence: float
    reasoning_coverage: float
    sample_quality_passed: bool
    needs_human_review: bool
    human_review_reasons: list[str]
