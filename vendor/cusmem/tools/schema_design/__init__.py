"""Schema design automation tool package."""

__all__ = [
    'SchemaDesignPipeline',
    'SchemaScore',
    'ConfidenceReport',
    'build_decision_brief',
    'score_schema_static',
    'critic_review',
    'repair_schema',
    'run_local_sample_extraction',
    'generate_auto_fix_plan',
    'apply_auto_fix',
    'compute_confidence',
    'draft_schema_multi',
]


def __getattr__(name: str):
    if name == 'SchemaDesignPipeline':
        from tools.schema_design.pipeline import SchemaDesignPipeline
        return SchemaDesignPipeline
    if name == 'SchemaScore':
        from tools.schema_design.models import SchemaScore
        return SchemaScore
    if name == 'ConfidenceReport':
        from tools.schema_design.models import ConfidenceReport
        return ConfidenceReport
    if name == 'build_decision_brief':
        from tools.schema_design.decision_brief import build_decision_brief
        return build_decision_brief
    if name == 'score_schema_static':
        from tools.schema_design.static_scorer import score_schema_static
        return score_schema_static
    if name == 'critic_review':
        from tools.schema_design.schema_critic import critic_review
        return critic_review
    if name == 'repair_schema':
        from tools.schema_design.schema_critic import repair_schema
        return repair_schema
    if name == 'run_local_sample_extraction':
        from tools.schema_design.local_dryrun import run_local_sample_extraction
        return run_local_sample_extraction
    if name == 'generate_auto_fix_plan':
        from tools.schema_design.auto_fix import generate_auto_fix_plan
        return generate_auto_fix_plan
    if name == 'apply_auto_fix':
        from tools.schema_design.auto_fix import apply_auto_fix
        return apply_auto_fix
    if name == 'compute_confidence':
        from tools.schema_design.confidence import compute_confidence
        return compute_confidence
    if name == 'draft_schema_multi':
        from tools.schema_design.schema_generation import draft_schema_multi
        return draft_schema_multi
    raise AttributeError(name)
