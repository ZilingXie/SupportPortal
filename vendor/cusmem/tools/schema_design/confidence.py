from __future__ import annotations

from tools.schema_design.models import ConfidenceReport, CriticResult, SampleQualityReport, SchemaScore


def compute_confidence(
    schema_score: SchemaScore,
    critic_result: CriticResult | None,
    dryrun_quality: SampleQualityReport | None,
    fix_round: int,
    *,
    text_garbled_ratio: float = 0.0,
    candidate_score_gap: float = 0.5,  # Score gap between best and 2nd best candidate
    max_fix_rounds: int = 3,
) -> ConfidenceReport:
    """Compute overall confidence and determine if human review is needed.

    Only flags human review when automated approaches are genuinely stuck.
    """
    reasons: list[str] = []

    # ── Schema confidence (from static score + critic) ──────────────────
    schema_confidence = schema_score.total
    if critic_result is not None:
        critical_count = sum(1 for i in critic_result.issues if i.severity == 'critical')
        major_count = sum(1 for i in critic_result.issues if i.severity == 'major')
        # Each critical issue reduces confidence
        schema_confidence = max(0.0, schema_confidence - critical_count * 0.15 - major_count * 0.05)

    # ── Reasoning coverage ─────────────────────────────────────────────
    reasoning_coverage = schema_score.reasoning_path_coverage

    # ── Sample quality ─────────────────────────────────────────────────
    sample_passed = False
    if dryrun_quality is not None:
        sample_passed = dryrun_quality.conclusion == 'PASS'

    # ── Determine if human review is needed ─────────────────────────────

    # 1. Consecutive auto-fix failures
    if fix_round >= max_fix_rounds and not sample_passed:
        reasons.append(
            f'连续 {fix_round} 轮自动修正未能达标 (conclusion={dryrun_quality.conclusion if dryrun_quality else "N/A"})'
        )

    # 2. Severely degraded text quality
    if text_garbled_ratio > 0.10:
        reasons.append(f'文本质量严重不足 (乱码率={text_garbled_ratio:.1%})')

    # 3. Candidate scores too close
    if candidate_score_gap < 0.05:
        reasons.append(f'多个候选 schema 分数接近 (差距={candidate_score_gap:.3f})，无法自动选择最佳')

    # 4. Low reasoning coverage
    if reasoning_coverage < 0.70:
        reasons.append(f'核心目标问题覆盖率过低 ({reasoning_coverage:.0%})')

    # 5. Critical dry-run failures
    if dryrun_quality is not None:
        if dryrun_quality.entity_not_found_ratio > 0.20:
            reasons.append(
                f'小样本 entity-not-found 过高 ({dryrun_quality.entity_not_found_ratio:.1%})'
            )
        if dryrun_quality.zero_degree_ratio > 0.40:
            reasons.append(
                f'小样本 zero-degree 过高 ({dryrun_quality.zero_degree_ratio:.1%})'
            )

    # ── Overall confidence ──────────────────────────────────────────────
    overall = schema_confidence
    if dryrun_quality is not None and sample_passed:
        overall = (overall + 1.0) / 2  # Boost when sample quality passes
    elif dryrun_quality is not None:
        # Penalize based on distance from PASS
        quality_penalty = (
            abs(dryrun_quality.entity_fallback_ratio - 0.15) +
            abs(dryrun_quality.zero_degree_ratio - 0.25) +
            abs(dryrun_quality.entity_not_found_ratio - 0.10)
        ) / 3
        overall = max(0.0, overall - quality_penalty)

    needs_human = len(reasons) > 0

    return ConfidenceReport(
        overall=round(min(1.0, max(0.0, overall)), 4),
        schema_confidence=round(schema_confidence, 4),
        reasoning_coverage=round(reasoning_coverage, 4),
        sample_quality_passed=sample_passed,
        needs_human_review=needs_human,
        human_review_reasons=reasons,
    )
