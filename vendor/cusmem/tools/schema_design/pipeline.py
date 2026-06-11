from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.schema_design.auto_fix import apply_auto_fix, generate_auto_fix_plan
from tools.schema_design.candidate_evidence_profiler import profile_candidate_evidence
from tools.schema_design.chunking import build_chunks
from tools.schema_design.confidence import compute_confidence
from tools.schema_design.decision_brief import build_decision_brief
from tools.schema_design.io_utils import ensure_dir, read_json, read_jsonl, write_json, write_yaml, yaml_load
from tools.schema_design.llm_client import LLMClient
from tools.schema_design.local_dryrun import run_local_sample_extraction
from tools.schema_design.patterns import profile_patterns
from tools.schema_design.prompt_rules import generate_prompt_rules
from tools.schema_design.quality import generate_final_report, generate_sample_quality_report, preflight_check
from tools.schema_design.schema_critic import critic_review, repair_schema
from tools.schema_design.schema_generation import draft_schema, draft_schema_multi
from tools.schema_design.schema_selection_from_pool import select_schema_from_pool
from tools.schema_design.state import PipelineState
from tools.schema_design.static_scorer import score_schema_from_pool, score_schema_static
from tools.schema_design.terms import profile_terms
from tools.schema_design.text_extraction import extract_text
from tools.schema_design.user_candidate_pool import load_candidate_pool, normalize_candidate_pool


class SchemaDesignPipeline:
    """Coordinate schema design stages and persist auditable artifacts."""

    def __init__(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        mode: str = 'auto',
        skip_stages: set[int] | None = None,
        llm_config: dict[str, str] | None = None,
        max_fix_rounds: int = 3,
        candidate_pool: Path | None = None,
        selection_mode: str = 'legacy',  # 'pool' | 'legacy'
    ) -> None:
        self.input_path = input_path
        self.output_dir = ensure_dir(output_dir)
        self.mode = mode
        self.skip_stages = skip_stages or set()
        self.llm_config = llm_config  # None = no LLM; {} = use env defaults
        self.max_fix_rounds = max_fix_rounds
        self.candidate_pool_path = candidate_pool
        self.selection_mode = selection_mode  # 'pool' if candidate_pool else 'legacy'
        self._normalized_pool: dict[str, Any] | None = None
        self.state = PipelineState.load(self.output_dir / 'pipeline_state.json', input_path=input_path)
        self._llm: LLMClient | None = None

    @property
    def llm(self) -> LLMClient | None:
        if self._llm is None and self.llm_config is not None:
            try:
                client = LLMClient(**self.llm_config)
                if client.api_key or 'localhost' in client.base_url or '127.0.0.1' in client.base_url or 'ollama' in client.base_url.lower():
                    self._llm = client
                else:
                    import logging
                    logging.getLogger(__name__).warning('No LLM API key configured; using rule-based schema generation. Set DEEPSEEK_API_KEY or use --llm-api-key.')
            except Exception:
                import logging
                logging.getLogger(__name__).warning('Failed to create LLM client; using rule-based schema generation.', exc_info=True)
        return self._llm

    def run(self, only_stages: set[int] | None = None, *, no_gates: bool = False) -> dict[str, Any]:
        stages_enabled = only_stages or {
            1, 2, 3, 4, 5, 55, 6, 7, 8, 9, 10, 11, 12, 13
        }
        gates_enabled = only_stages is None and not no_gates  # quality gates only for full runs

        # ── Stages 1-5: Text → Chunks → Patterns → Terms → Topics ──────
        if 1 in stages_enabled and 1 not in self.skip_stages:
            self._stage1_text_extraction()
            if gates_enabled:
                self._gate_needs_ocr()

        if 2 in stages_enabled and 2 not in self.skip_stages:
            self._stage2_chunking()
            if gates_enabled:
                self._gate_chunk_count(min_chunks=10)

        if 3 in stages_enabled and 3 not in self.skip_stages:
            self._stage3_patterns()

        if 4 in stages_enabled and 4 not in self.skip_stages:
            self._stage4_terms()
            if gates_enabled:
                self._gate_term_count(min_terms=10)

        if 5 in stages_enabled and 5 not in self.skip_stages:
            self._stage5_topics()

        # ── Stage 5.5: Pool evidence or Decision Brief ──────────────────
        if 55 in stages_enabled and 55 not in self.skip_stages:
            if self._is_pool_mode():
                self._stage55_pool_evidence()
            else:
                self._stage55_decision_brief()

        # ── Stage 6: Selection from pool OR Multi-candidate generation ──
        if 6 in stages_enabled and 6 not in self.skip_stages:
            if self._is_pool_mode():
                self._stage6_pool_selection()
            else:
                self._stage6_schema()

        # ── Stage 7: Auto Review (REPLACED) ─────────────────────────────
        if 7 in stages_enabled and 7 not in self.skip_stages:
            self._stage7_auto_review()

        # ── Stage 8: Prompt Rules ───────────────────────────────────────
        if 8 in stages_enabled and 8 not in self.skip_stages:
            self._stage8_prompts()

        # ── Auto-fix loop: Stage 9 → 10 → 9 → ... ──────────────────────
        if 9 in stages_enabled and 10 in stages_enabled:
            self._auto_fix_loop(gates_enabled=gates_enabled)

        # ── Stage 10: emit schema config ────────────────────────────────
        if 10 in stages_enabled:
            self.emit_schema_config()

        # ── Stage 11-13: Preflight → Full Extraction → Final Report ─────
        if 11 in stages_enabled and 11 not in self.skip_stages:
            self._stage11_preflight()
            if gates_enabled:
                self._gate_preflight()

        if 12 in stages_enabled and 12 not in self.skip_stages:
            self._stage12_full_extraction()

        if 13 in stages_enabled and 13 not in self.skip_stages:
            self._stage13_final_report()

        if 10 in stages_enabled or 13 in stages_enabled:
            return self.emit_final_config()
        return {}

    def emit_schema_config(self) -> dict[str, Any]:
        schema_path = self.output_dir / 'candidate_schema.yaml'
        output_path = self.output_dir / 'schema_config.yaml'
        if not schema_path.exists():
            if output_path.exists():
                return yaml_load(output_path)
            raise RuntimeError(
                f'Missing {schema_path.name}; run stage 6 or provide an existing schema_config.yaml before emitting the final schema.'
            )
        schema = yaml_load(schema_path)
        write_yaml(output_path, schema)
        return schema

    def emit_final_config(self) -> dict[str, Any]:
        prompt_rules = {}
        prompt_path = self.output_dir / 'prompt_rules.yaml'
        if prompt_path.exists():
            prompt_rules = yaml_load(prompt_path)
        config = {
            'meta': {
                'generated_at': datetime.now().isoformat(timespec='seconds'),
                'pipeline_run_id': self.state.data.get('pipeline_run_id'),
                'source_documents': [str(self.input_path)],
            },
            'schema': {'path': str(self.output_dir / 'schema_config.yaml'), 'mode': 'strict'},
            'prompts': {
                'entity_prompt': prompt_rules.get('entity_prompt', ''),
                'edge_prompt': prompt_rules.get('edge_prompt', ''),
            },
            'entity_alignment': {
                'synonym_guidance': [],
                'dedup': {'cosine_min_score': 0.70, 'candidate_limit': 10},
            },
            'filters': {
                'entity_exclusions': [
                    {'pattern': r'^(第.*[章节条]|[A-Z]?\d+(?:\.\d+)+)$', 'reason': 'document_structure_metadata_only'},
                    {'pattern': r'^\d+$', 'reason': 'isolated_parameter_value'},
                    {'pattern': r'^(Hz|kHz|mm|cm|m|km|N|kN|V|kV|A|mA)$', 'reason': 'unit_only_parameter'},
                ],
                'zero_degree_cleanup': {
                    'catalog_patterns': ['ICS', 'CCS', '发布日期'],
                    'ocr_fragment_patterns': [r'[,，].*[\u4e00-\u9fff]', r'[&＃@]\\d*$'],
                },
            },
            'quality_targets': {
                'entity_fallback_max': 0.15,
                'zero_degree_max': 0.25,
                'entity_not_found_max': 0.10,
                'edge_entity_ratio_min': 0.50,
                'edge_type_coverage_min': 0.60,
            },
        }
        write_yaml(self.output_dir / 'final_config.yaml', config)
        return config

    def _stage1_text_extraction(self) -> None:
        result = extract_text(self.input_path, self.output_dir)
        self.state.mark_completed('stage1_text_extraction', asdict(result), input_paths=[self.input_path])

    def _stage2_chunking(self) -> None:
        pages = self.output_dir / 'pages.jsonl'
        result = build_chunks(pages, self.output_dir)
        self.state.mark_completed('stage2_cleaning_and_chunking', asdict(result), input_paths=[pages])

    def _stage3_patterns(self) -> None:
        chunks = self.output_dir / 'chunks.jsonl'
        result = profile_patterns(chunks, self.output_dir)
        self.state.mark_completed('stage3_pattern_recognition', asdict(result), input_paths=[chunks])

    def _stage4_terms(self) -> None:
        chunks = self.output_dir / 'chunks.jsonl'
        patterns = self.output_dir / 'pattern_inventory.json'
        result = profile_terms(chunks, patterns, self.output_dir)
        self.state.mark_completed('stage4_term_frequency', asdict(result), input_paths=[chunks, patterns])

    def _stage5_topics(self) -> None:
        chunks = read_jsonl(self.output_dir / 'chunks.jsonl')
        topic_path = self.output_dir / 'topic_clusters.md'
        lines = ['# Topic Clusters', '', 'Offline implementation: topic clustering is summarized by section titles.']
        sections: dict[str, int] = {}
        for chunk in chunks:
            key = chunk.get('section_title') or 'unknown'
            sections[key] = sections.get(key, 0) + 1
        for section, count in sorted(sections.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f'- {section}: {count} chunks')
        topic_path.write_text('\n'.join(lines), encoding='utf-8')
        self.state.mark_completed(
            'stage5_topic_clustering',
            {'output_files': {'topic_clusters_md': topic_path}, 'metrics': {'topic_count': len(sections)}},
            input_paths=[self.output_dir / 'chunks.jsonl'],
        )

    def _score_schema(self, schema: dict[str, Any], role_clusters: dict[str, Any] | None = None) -> Any:
        """Pick the right scorer based on mode."""
        if self._is_pool_mode():
            pool = self._load_normalized_pool()
            evidence_path = self.output_dir / 'candidate_pool_evidence.json'
            evidence = read_json(evidence_path) if evidence_path.exists() else None
            return score_schema_from_pool(schema, pool, evidence) if pool else score_schema_static(schema, role_clusters, pool)
        return score_schema_static(schema, role_clusters)

    def _is_pool_mode(self) -> bool:
        """Check if we are in candidate pool selection mode."""
        return (
            self.candidate_pool_path is not None
            and self.candidate_pool_path.exists()
            and self.selection_mode == 'pool'
        )

    def _load_normalized_pool(self) -> dict[str, Any] | None:
        """Load normalized pool from memory or disk (for resume/partial runs)."""
        if self._normalized_pool is not None:
            return self._normalized_pool
        path = self.output_dir / 'normalized_candidate_pool.json'
        if path.exists():
            self._normalized_pool = read_json(path)
            return self._normalized_pool
        return None

    def _stage55_pool_evidence(self) -> None:
        """Stage 5.5 (pool mode): Load candidate pool + profile evidence."""
        pool = load_candidate_pool(self.candidate_pool_path)  # type: ignore[arg-type]
        self._normalized_pool = normalize_candidate_pool(pool, self.output_dir)

        # Also generate corpus profile for downstream use
        from tools.schema_design.decision_brief import _build_corpus_profile
        from tools.schema_design.io_utils import read_json
        patterns = read_json(self.output_dir / 'pattern_inventory.json')
        terms = read_json(self.output_dir / 'term_frequency.json')
        corpus = _build_corpus_profile(patterns, terms, self.output_dir / 'topic_clusters.md', None)
        write_json(self.output_dir / 'corpus_profile.json', corpus)

        result = profile_candidate_evidence(
            self._normalized_pool,
            self.output_dir / 'pages.jsonl',
            self.output_dir / 'chunks.jsonl',
            self.output_dir / 'pattern_inventory.json',
            self.output_dir / 'term_frequency.json',
            self.output_dir / 'topic_clusters.md',
            self.output_dir,
        )
        self.state.mark_completed(
            'stage55_pool_evidence',
            asdict(result),
            input_paths=[
                self.output_dir / 'chunks.jsonl',
                self.output_dir / 'pattern_inventory.json',
                self.output_dir / 'term_frequency.json',
            ],
        )

    def _stage6_pool_selection(self) -> None:
        """Stage 6 (pool mode): LLM selects schema from candidate pool."""
        llm = self.llm
        if llm is None:
            raise RuntimeError('Pool selection mode requires LLM')

        result = select_schema_from_pool(
            self.output_dir / 'normalized_candidate_pool.json',
            self.output_dir / 'candidate_pool_evidence.json',
            self.output_dir / 'corpus_profile.json',
            self.output_dir,
            llm,
        )
        self.state.mark_completed(
            'stage6_pool_selection',
            {
                'output_files': {k: str(v) for k, v in result.output_files.items()},
                'metrics': result.metrics,
            },
            input_paths=[self.output_dir / 'normalized_candidate_pool.json',
                        self.output_dir / 'candidate_pool_evidence.json'],
        )

    def _stage55_decision_brief(self) -> None:
        """Stage 5.5: Auto-generate Schema Decision Brief."""
        result = build_decision_brief(
            self.output_dir / 'pattern_inventory.json',
            self.output_dir / 'term_frequency.json',
            self.output_dir / 'topic_clusters.md',
            self.output_dir,
            llm=self.llm,
        )
        self.state.mark_completed(
            'stage55_decision_brief',
            asdict(result),
            input_paths=[
                self.output_dir / 'pattern_inventory.json',
                self.output_dir / 'term_frequency.json',
                self.output_dir / 'topic_clusters.md',
            ],
        )

    def _stage6_schema(self) -> None:
        """Stage 6: Multi-candidate generation → score → critic+repair → pick best.

        If LLM is available, generates 3 candidate schemas with different strategies,
        scores them statically, applies critic+repair to the best one.
        Falls back to single schema (rule-based or LLM) when LLM is unavailable.
        """
        llm = self.llm
        # Check for role clusters (new Stage 5.5 output) or fall back to old decision_brief.json
        clusters_path = self.output_dir / 'candidate_role_clusters.json'
        brief_path = self.output_dir / 'decision_brief.json'

        if llm is not None and (clusters_path.exists() or brief_path.exists()):
            # ── 6A: Generate 3 candidates ───────────────────────────────
            candidates = draft_schema_multi(
                self.output_dir / 'pattern_inventory.json',
                self.output_dir / 'term_frequency.json',
                brief_path,
                self.output_dir,
                llm,
                num_candidates=3,
            )
            self.state.mark_completed(
                'stage6a_candidate_generation',
                {
                    'output_files': {f'candidate_{i}': c.output_files['candidate_schema_yaml']
                                    for i, c in enumerate(candidates)},
                    'metrics': {'num_candidates': len(candidates),
                               'strategies': [c.metrics.get('strategy', '') for c in candidates]},
                },
            )

            # ── 6B: Score all candidates using role clusters ─────────────
            role_clusters = read_json(self.output_dir / 'candidate_role_clusters.json') if (self.output_dir / 'candidate_role_clusters.json').exists() else None
            scored = []
            for i, candidate in enumerate(candidates):
                schema = yaml_load(candidate.output_files['candidate_schema_yaml'])
                score = self._score_schema(schema, role_clusters)
                scored.append((i, candidate, schema, score))

            scored.sort(key=lambda x: x[3].total, reverse=True)
            best_idx, best_candidate, best_schema, best_score = scored[0]

            scores_data = {
                'rankings': [
                    {
                        'candidate_idx': idx,
                        'strategy': cand.metrics.get('strategy', ''),
                        'total_score': score.total,
                        'dimension_scores': score.dimension_scores,
                        'warnings': score.warnings,
                    }
                    for idx, cand, _, score in scored
                ],
                'best_candidate_idx': best_idx,
                'score_gap': (
                    scored[0][3].total - scored[1][3].total
                    if len(scored) >= 2 else 0.5
                ),
            }
            write_json(self.output_dir / 'schema_scores.json', scores_data)
            self.state.mark_completed(
                'stage6b_score_candidates',
                {'output_files': {'schema_scores_json': self.output_dir / 'schema_scores.json'},
                 'metrics': {'best_candidate_idx': best_idx, 'best_score': best_score.total}},
            )

            # ── 6C: Critic + Repair on best schema ──────────────────────
            # Build brief-like dict for critic (uses corpus_profile + role_clusters)
            corpus_profile = read_json(self.output_dir / 'corpus_profile.json') if (self.output_dir / 'corpus_profile.json').exists() else {}
            critic_brief = {
                'document_type': corpus_profile.get('document_archetype', {}),
                'target_questions': [],
                'recommended_entity_types': [],
                'recommended_relation_paths': [],
                'high_risk_confusions': [],
                'must_filter_noise': [],
            }
            if role_clusters:
                critic_brief['target_questions'] = [
                    {'question': f'{data.get("role_label", "")} 簇有 {data.get("count", 0)} 个候选项，schema 是否覆盖？',
                     'reasoning_type': role_key}
                    for role_key, data in role_clusters.get('role_clusters', {}).items()
                    if data.get('count', 0) > 3
                ]

            critic_result = critic_review(best_schema, critic_brief, best_score, llm)
            write_json(self.output_dir / 'critic_result.json', {
                'issues': [{'severity': i.severity, 'category': i.category,
                           'description': i.description, 'suggestion': i.suggestion}
                          for i in critic_result.issues],
                'overall_assessment': critic_result.overall_assessment,
                'needs_repair': critic_result.needs_repair,
            })

            # Write best raw candidate as candidate_schema.yaml first
            # (critic repair will overwrite only if it produces a better result)
            write_yaml(self.output_dir / 'candidate_schema.yaml', best_schema)
            best_schema_path = self.output_dir / 'candidate_schema.yaml'

            if critic_result.needs_repair:
                repaired = repair_schema(best_schema, critic_result, critic_brief, llm)
                # Only use repaired schema if it actually preserved edge_types
                if repaired.get('edge_types'):
                    best_schema = repaired
                    write_yaml(best_schema_path, best_schema)
                # Otherwise keep the original (already written)

        else:
            # Fallback: single schema (rule-based or LLM without brief)
            result = draft_schema(
                self.output_dir / 'pattern_inventory.json',
                self.output_dir / 'term_frequency.json',
                self.output_dir,
                llm=llm,
                topic_md=self.output_dir / 'topic_clusters.md',
            )
            best_score = None

        # Validate and write review checklist
        schema_path = self.output_dir / 'candidate_schema.yaml'
        from tools.schema_design.schema_generation import generate_review_checklist, validate_candidate_schema
        review_path = self.output_dir / 'candidate_schema_review.md'
        review_path.write_text(generate_review_checklist(schema_path), encoding='utf-8')
        validation = validate_candidate_schema(schema_path)

        self.state.mark_completed(
            'stage6_schema_generation',
            {
                'output_files': {
                    'candidate_schema_yaml': schema_path,
                    'candidate_schema_review_md': review_path,
                },
                'metrics': {
                    'schema_valid': validation.valid,
                    'entity_type_count': validation.entity_type_count,
                    'edge_type_count': validation.edge_type_count,
                    'schema_error_count': len(validation.errors),
                    'schema_warning_count': len(validation.warnings),
                    'best_static_score': best_score.total if best_score else None,
                },
            },
            input_paths=[self.output_dir / 'pattern_inventory.json',
                        self.output_dir / 'term_frequency.json'],
        )

    def _stage7_auto_review(self) -> None:
        """Stage 7: Automated review replacing human review.

        Runs: orphan check, overgeneral check, reasoning path coverage,
        schema validation. Computes confidence score.
        Only flags for human when confidence is critically low.
        """
        schema_path = self.output_dir / 'candidate_schema.yaml'
        brief_path = self.output_dir / 'decision_brief.json'

        # Collect automated checks
        from tools.schema_design.quality import (
            check_orphan_entity_types,
            check_overgeneral_relations,
            check_reasoning_path_coverage,
        )
        from tools.schema_design.schema_generation import validate_candidate_schema

        validation = validate_candidate_schema(schema_path)
        orphan_check = check_orphan_entity_types(schema_path)
        overgeneral_check = check_overgeneral_relations(schema_path)

        checks = [orphan_check, overgeneral_check]
        if brief_path.exists():
            checks.append(check_reasoning_path_coverage(schema_path, brief_path))

        all_passed = validation.valid and all(c.passed for c in checks)

        # Compute confidence using role clusters
        role_clusters = read_json(self.output_dir / 'candidate_role_clusters.json') if (self.output_dir / 'candidate_role_clusters.json').exists() else None
        schema = yaml_load(schema_path)
        static_score = self._score_schema(schema, role_clusters)

        stage1 = self.state.data.get('stages', {}).get('stage1_text_extraction', {})
        garbled = stage1.get('metrics', {}).get('garbled_ratio', 0.0)

        scores_data = read_json(self.output_dir / 'schema_scores.json') if (self.output_dir / 'schema_scores.json').exists() else {}
        score_gap = scores_data.get('score_gap', 0.5)

        confidence = compute_confidence(
            schema_score=static_score,
            critic_result=None,  # critic was already applied in stage 6C
            dryrun_quality=None,  # dryrun hasn't run yet
            fix_round=0,
            text_garbled_ratio=garbled,
            candidate_score_gap=score_gap,
            max_fix_rounds=self.max_fix_rounds,
        )
        write_json(self.output_dir / 'confidence_report.json', {
            'overall': confidence.overall,
            'schema_confidence': confidence.schema_confidence,
            'reasoning_coverage': confidence.reasoning_coverage,
            'sample_quality_passed': confidence.sample_quality_passed,
            'needs_human_review': confidence.needs_human_review,
            'human_review_reasons': confidence.human_review_reasons,
        })

        self.state.mark_completed(
            'stage7_auto_review',
            {
                'output_files': {
                    'candidate_schema_yaml': schema_path,
                    'candidate_schema_review_md': self.output_dir / 'candidate_schema_review.md',
                    'confidence_report_json': self.output_dir / 'confidence_report.json',
                },
                'metrics': {
                    'review_approved': not confidence.needs_human_review,
                    'confidence': confidence.overall,
                    'auto_checks_passed': all_passed,
                },
            },
            input_paths=[schema_path],
            extra={
                'review_approved': not confidence.needs_human_review,
                'synonym_guidance_reviewed': True,
                'status': 'APPROVED' if not confidence.needs_human_review else 'NEEDS_REVIEW',
                'confidence': confidence.overall,
                'human_review_reasons': confidence.human_review_reasons,
            },
        )

    def _stage8_prompts(self) -> None:
        result = generate_prompt_rules(self.output_dir / 'candidate_schema.yaml', self.output_dir)
        self.state.mark_completed(
            'stage8_prompt_generation',
            {'output_files': result.output_files, 'metrics': {'prompt_rule_count': len(result.prompt_rules)}},
            input_paths=[self.output_dir / 'candidate_schema.yaml'],
        )

    def _auto_fix_loop(self, gates_enabled: bool) -> None:
        """Run Stage 9 (local dry-run) → Stage 10 (auto-fix) loop.

        Up to max_fix_rounds iterations. Exits early if quality passes.
        Falls back to SKIPPED_EXTERNAL_SERVICE if no LLM is available.
        """
        llm = self.llm
        if llm is None:
            # No LLM → fall back to placeholder
            self._stage9_skip()
            self._stage10_skip()
            return

        schema = yaml_load(self.output_dir / 'candidate_schema.yaml')
        prompt_rules = yaml_load(self.output_dir / 'prompt_rules.yaml')

        # Recovery: if schema lost its edge_types, restore from best candidate
        if not schema.get('edge_types'):
            manifest_path = self.output_dir / 'candidate_schemas_manifest.json'
            if manifest_path.exists():
                manifest = read_json(manifest_path)
                for cf in manifest.get('candidate_files', []):
                    candidate = yaml_load(self.output_dir / cf)
                    if candidate.get('edge_types'):
                        schema['edge_types'] = candidate['edge_types']
                        break

        best_quality = None
        best_schema = schema
        best_prompts = prompt_rules

        for round_idx in range(self.max_fix_rounds):
            # Stage 9: Local dry-run
            dryrun = run_local_sample_extraction(
                self.output_dir / 'chunks.jsonl',
                best_schema,
                best_prompts,
                self.output_dir,
                llm,
                sample_size=20,
            )
            quality = dryrun.quality_report
            self.state.mark_completed(
                f'stage9_local_dryrun_round{round_idx}',
                {
                    'output_files': {
                        'local_dryrun_entities_jsonl': self.output_dir / 'local_dryrun_entities.jsonl',
                        'local_dryrun_edges_jsonl': self.output_dir / 'local_dryrun_edges.jsonl',
                        'local_dryrun_rejected_entities_jsonl': self.output_dir / 'local_dryrun_rejected_entities.jsonl',
                        'local_dryrun_rejected_edges_jsonl': self.output_dir / 'local_dryrun_rejected_edges.jsonl',
                    },
                    'metrics': {
                        'conclusion': quality.conclusion,
                        'entity_fallback_ratio': quality.entity_fallback_ratio,
                        'zero_degree_ratio': quality.zero_degree_ratio,
                        'entity_not_found_ratio': quality.entity_not_found_ratio,
                        'edge_type_coverage': quality.edge_type_coverage,
                        'round': round_idx,
                    },
                },
                input_paths=[self.output_dir / 'candidate_schema.yaml',
                            self.output_dir / 'chunks.jsonl'],
            )

            # Update best so far
            if best_quality is None or quality.conclusion == 'PASS':
                best_quality = quality
                best_schema = schema
                best_prompts = prompt_rules

            # Gate check
            if quality.conclusion == 'PASS':
                break

            # Stage 10: Auto-fix
            if round_idx < self.max_fix_rounds - 1:
                plan = generate_auto_fix_plan(quality, best_schema, dryrun, llm, normalized_pool=self._load_normalized_pool())
                write_json(self.output_dir / f'auto_fix_plan_round{round_idx}.json', {
                    'round': round_idx,
                    'conclusion_before': quality.conclusion,
                    'schema_fixes': [{'action': f.action, 'target': f.target, 'reason': f.reason}
                                    for f in plan.schema_fixes],
                    'prompt_fixes': [{'action': f.action, 'target': f.target, 'reason': f.reason}
                                    for f in plan.prompt_fixes],
                    'filter_fixes': [{'action': f.action, 'pattern': f.pattern, 'reason': f.reason}
                                    for f in plan.filter_fixes],
                    'synonym_fixes': [{'source': f.source_name, 'target': f.target_official_name}
                                     for f in plan.synonym_fixes],
                    'confidence': plan.confidence,
                })
                best_schema, best_prompts = apply_auto_fix(best_schema, best_prompts, plan)

                # Write updated schema and prompts
                write_yaml(self.output_dir / 'candidate_schema.yaml', best_schema)
                write_yaml(self.output_dir / 'prompt_rules.yaml', best_prompts)

        # Write final round as the consolidated result
        final_conclusion = best_quality.conclusion if best_quality else 'UNKNOWN'
        write_json(self.output_dir / 'sample_extraction_result.json', {
            'final_conclusion': final_conclusion,
            'rounds': round_idx + 1,
            'entity_fallback_ratio': best_quality.entity_fallback_ratio if best_quality else 0,
            'zero_degree_ratio': best_quality.zero_degree_ratio if best_quality else 0,
            'entity_not_found_ratio': best_quality.entity_not_found_ratio if best_quality else 0,
        })

        # Update confidence with dry-run results
        self._stage7_auto_review_with_dryrun(best_quality, round_idx + 1)

        if gates_enabled and final_conclusion != 'PASS':
            raise PipelineBlockedError(
                f'Stage 9-10 auto-fix loop: 经过 {round_idx + 1} 轮修正仍不达标 '
                f'(conclusion={final_conclusion})。请人工介入。'
            )

    def _stage7_auto_review_with_dryrun(
        self, quality: Any, fix_round: int
    ) -> None:
        """Update confidence report with dry-run quality results."""
        role_clusters = read_json(self.output_dir / 'candidate_role_clusters.json') if (self.output_dir / 'candidate_role_clusters.json').exists() else None
        schema = yaml_load(self.output_dir / 'candidate_schema.yaml')

        static_score = self._score_schema(schema, role_clusters)

        stage1 = self.state.data.get('stages', {}).get('stage1_text_extraction', {})
        garbled = stage1.get('metrics', {}).get('garbled_ratio', 0.0)

        scores_data = read_json(self.output_dir / 'schema_scores.json') if (self.output_dir / 'schema_scores.json').exists() else {}
        score_gap = scores_data.get('score_gap', 0.5)

        confidence = compute_confidence(
            schema_score=static_score,
            critic_result=None,
            dryrun_quality=quality,
            fix_round=fix_round,
            text_garbled_ratio=garbled,
            candidate_score_gap=score_gap,
            max_fix_rounds=self.max_fix_rounds,
        )
        write_json(self.output_dir / 'confidence_report.json', {
            'overall': confidence.overall,
            'schema_confidence': confidence.schema_confidence,
            'reasoning_coverage': confidence.reasoning_coverage,
            'sample_quality_passed': confidence.sample_quality_passed,
            'needs_human_review': confidence.needs_human_review,
            'human_review_reasons': confidence.human_review_reasons,
            'fix_rounds': fix_round,
        })

    def _stage9_skip(self) -> None:
        """Placeholder when LLM is not available."""
        skipped_path = write_json(
            self.output_dir / 'sample_extraction_skipped.json',
            {
                'status': 'SKIPPED_NO_LLM',
                'reason': 'Local dry-run requires LLM for entity/edge extraction.',
            },
        )
        schema = yaml_load(self.output_dir / 'candidate_schema.yaml')
        report = generate_sample_quality_report([], [], [], [], schema)
        self.state.mark_completed(
            'stage9_sample_extraction',
            {
                'output_files': {'sample_extraction_skipped_json': skipped_path},
                'metrics': {
                    'conclusion': 'SKIPPED_NO_LLM',
                    'entity_fallback_ratio': 0.0,
                    'zero_degree_ratio': 0.0,
                    'entity_not_found_ratio': 0.0,
                },
            },
            input_paths=[self.output_dir / 'candidate_schema.yaml',
                        self.output_dir / 'chunks.jsonl'],
        )

    def _stage10_skip(self) -> None:
        """Placeholder when LLM is not available."""
        path = write_json(
            self.output_dir / 'quality_fix_suggestions.json',
            {
                'conclusion': 'SKIPPED_NO_LLM',
                'suggestions': [
                    'Configure LLM to enable auto-fix.',
                    'Keep synonym guidance in official_name/synonyms; do not rewrite node names.',
                ],
            },
        )
        self.state.mark_completed(
            'stage10_quality_fix',
            {'output_files': {'quality_fix_suggestions_json': path},
             'metrics': {'suggestion_count': 2}},
            input_paths=[self.output_dir / 'prompt_rules.yaml'],
        )

    def _stage9_sample_extraction(self) -> None:
        """Deprecated: use _auto_fix_loop() instead."""
        self._auto_fix_loop(gates_enabled=False)

    def _stage10_quality_fix(self) -> None:
        """Deprecated: handled inside _auto_fix_loop()."""
        pass  # auto-fix is applied inside the loop

    def _stage11_preflight(self) -> None:
        result = preflight_check(self.state, self.output_dir)
        self.state.mark_completed(
            'stage11_preflight_check',
            {'output_files': {'preflight_report_json': self.output_dir / 'preflight_report.json'}, 'metrics': {'passed': result.passed}},
            input_paths=[self.output_dir / 'candidate_schema.yaml', self.output_dir / 'prompt_rules.yaml'],
        )

    def _stage12_full_extraction(self) -> None:
        llm = self.llm
        if llm is None:
            path = write_json(
                self.output_dir / 'full_extraction_skipped.json',
                {
                    'status': 'SKIPPED_NO_LLM',
                    'reason': 'Full extraction requires an LLM plus configured graph database services.',
                },
            )
            for name in ('entities.jsonl', 'edges.jsonl', 'rejected_entities.jsonl', 'rejected_edges.jsonl', 'zero_degree_entities.jsonl'):
                (self.output_dir / name).write_text('', encoding='utf-8')
            write_json(self.output_dir / 'cleanup_result.json', {})
            self.state.mark_completed(
                'stage12_full_extraction',
                {'output_files': {'full_extraction_skipped_json': path}, 'metrics': {'status': 'SKIPPED_NO_LLM'}},
                input_paths=[self.output_dir / 'schema_config.yaml'],
            )
            return

        schema_path = self.output_dir / 'schema_config.yaml'
        if not schema_path.exists():
            self.emit_schema_config()

        schema = yaml_load(schema_path)
        prompt_rules = yaml_load(self.output_dir / 'prompt_rules.yaml') if (self.output_dir / 'prompt_rules.yaml').exists() else {}
        chunks = read_jsonl(self.output_dir / 'chunks.jsonl')

        dryrun = run_local_sample_extraction(
            self.output_dir / 'chunks.jsonl',
            schema,
            prompt_rules,
            self.output_dir,
            llm,
            sample_size=len(chunks),
        )
        artifact_counts = self._promote_full_extraction_artifacts()
        graph_result = self._run_graph_ingest(schema_path)

        result_path = write_json(
            self.output_dir / 'full_extraction_result.json',
            {
                'status': 'EXECUTED',
                'schema_path': str(schema_path),
                'graph_ingest': graph_result,
                'local_extraction': {
                    'entities': len(dryrun.entities),
                    'edges': len(dryrun.edges),
                    'rejected_entities': len(dryrun.rejected_entities),
                    'rejected_edges': len(dryrun.rejected_edges),
                    'sample_chunks_used': dryrun.sample_chunks_used,
                    'quality_conclusion': dryrun.quality_report.conclusion,
                },
                'artifact_counts': artifact_counts,
            },
        )
        skipped_path = self.output_dir / 'full_extraction_skipped.json'
        if skipped_path.exists():
            skipped_path.unlink()
        self.state.mark_completed(
            'stage12_full_extraction',
            {
                'output_files': {
                    'full_extraction_result_json': result_path,
                    'entities_jsonl': self.output_dir / 'entities.jsonl',
                    'edges_jsonl': self.output_dir / 'edges.jsonl',
                    'rejected_entities_jsonl': self.output_dir / 'rejected_entities.jsonl',
                    'rejected_edges_jsonl': self.output_dir / 'rejected_edges.jsonl',
                    'zero_degree_entities_jsonl': self.output_dir / 'zero_degree_entities.jsonl',
                },
                'metrics': {
                    'status': 'EXECUTED',
                    'graph_files': graph_result.get('files', 0),
                    'graph_chunks': graph_result.get('chunks', 0),
                    'graph_extracted': graph_result.get('extracted', 0),
                    **artifact_counts,
                },
            },
            input_paths=[schema_path],
        )

    def _run_graph_ingest(self, schema_path: Path) -> dict[str, Any]:
        import asyncio

        from graphiti_rag import GraphRAG
        from graphiti_rag.config_loader import load_config
        from graphiti_rag.schema_loader import load_graph_schema

        config = load_config()
        loaded_schema = load_graph_schema(schema_path)
        config.schema_path = str(schema_path)
        config.entity_types = loaded_schema.entity_types
        config.edge_types = loaded_schema.edge_types
        config.edge_type_map = loaded_schema.edge_type_map
        llm = self.llm
        if llm is not None:
            config.llm_api_key = llm.api_key
            config.llm_base_url = llm.base_url
            config.llm_model = llm.model
        config.progress = False

        async def run_ingest() -> dict[str, Any]:
            rag = GraphRAG(config)
            try:
                return await rag.ingest([str(self.input_path)])
            finally:
                await rag.close()

        return asyncio.run(run_ingest())

    def _promote_full_extraction_artifacts(self) -> dict[str, int]:
        mapping = {
            'local_dryrun_entities.jsonl': 'entities.jsonl',
            'local_dryrun_edges.jsonl': 'edges.jsonl',
            'local_dryrun_rejected_entities.jsonl': 'rejected_entities.jsonl',
            'local_dryrun_rejected_edges.jsonl': 'rejected_edges.jsonl',
        }
        for source_name, target_name in mapping.items():
            source = self.output_dir / source_name
            target = self.output_dir / target_name
            target.write_text(source.read_text(encoding='utf-8') if source.exists() else '', encoding='utf-8')

        entities = read_jsonl(self.output_dir / 'entities.jsonl')
        edges = read_jsonl(self.output_dir / 'edges.jsonl')
        connected = {edge.get('source_entity_name', '') for edge in edges} | {edge.get('target_entity_name', '') for edge in edges}
        zero_degree = [entity for entity in entities if entity.get('name', '') not in connected]
        from tools.schema_design.io_utils import write_jsonl

        write_jsonl(self.output_dir / 'zero_degree_entities.jsonl', zero_degree)
        cleanup = {'zero_degree_count': len(zero_degree), 'catalog': 0, 'ocr_fragment': 0}
        write_json(self.output_dir / 'cleanup_result.json', cleanup)
        return {
            'entity_count': len(entities),
            'edge_count': len(edges),
            'rejected_entity_count': len(read_jsonl(self.output_dir / 'rejected_entities.jsonl')),
            'rejected_edge_count': len(read_jsonl(self.output_dir / 'rejected_edges.jsonl')),
            'zero_degree_count': len(zero_degree),
        }

    def _stage13_final_report(self) -> None:
        # Check if Stage 12 actually ran
        stage12 = self.state.data.get('stages', {}).get('stage12_full_extraction', {})
        stage12_status = stage12.get('metrics', {}).get('status', '')
        if stage12_status == 'SKIPPED_EXTERNAL_SERVICE':
            self._write_not_executed_report()
            return

        schema = yaml_load(self.output_dir / 'schema_config.yaml')
        report = generate_final_report(
            entities=read_jsonl(self.output_dir / 'entities.jsonl'),
            edges=read_jsonl(self.output_dir / 'edges.jsonl'),
            rejected_entities=read_jsonl(self.output_dir / 'rejected_entities.jsonl'),
            rejected_edges=read_jsonl(self.output_dir / 'rejected_edges.jsonl'),
            zero_degree_entities=read_jsonl(self.output_dir / 'zero_degree_entities.jsonl'),
            cleanup_result=read_json(self.output_dir / 'cleanup_result.json') if (self.output_dir / 'cleanup_result.json').exists() else {},
            schema=schema,
            output_dir=self.output_dir,
        )
        self.state.mark_completed(
            'stage13_final_quality_report',
            {
                'output_files': {
                    'final_quality_report_md': self.output_dir / 'final_quality_report.md',
                    'final_quality_report_json': self.output_dir / 'final_quality_report.json',
                },
                'metrics': report.summary,
            },
            input_paths=[self.output_dir / 'schema_config.yaml'],
        )

    # ── Quality Gates ────────────────────────────────────────────────────────

    def _gate_needs_ocr(self) -> None:
        stage1 = self.state.data.get('stages', {}).get('stage1_text_extraction', {})
        metrics = stage1.get('metrics', {})
        needs_ocr_ratio = metrics.get('needs_ocr_ratio', 0)
        ocr_pages = metrics.get('ocr_pages', 0)
        # If OCR was actually applied, the gate passes (OCR handled the quality issue)
        if ocr_pages > 0:
            return
        if needs_ocr_ratio > 0.30:
            raise PipelineBlockedError(
                f'Stage 1 文本质量不达标: needs_ocr_ratio={needs_ocr_ratio:.1%} > 30%'
                f' 且未执行 OCR（ocr_pages={ocr_pages}）。'
                f' 请安装 tesseract 或 paddleocr，或升级 PDF 文本质量。'
            )

    def _gate_chunk_count(self, min_chunks: int = 10) -> None:
        stage2 = self.state.data.get('stages', {}).get('stage2_cleaning_and_chunking', {})
        metrics = stage2.get('metrics', {})
        chunk_count = metrics.get('chunk_count', 0)
        if chunk_count < min_chunks:
            raise PipelineBlockedError(
                f'Stage 2 chunk 数量不足: {chunk_count} < {min_chunks}。'
                f' 请检查文本质量和章节识别逻辑。'
            )

    def _gate_term_count(self, min_terms: int = 10) -> None:
        stage4 = self.state.data.get('stages', {}).get('stage4_term_frequency', {})
        metrics = stage4.get('metrics', {})
        term_count = metrics.get('candidate_object_term_count', 0)
        if term_count < min_terms:
            raise PipelineBlockedError(
                f'Stage 4 候选实体词不足: {term_count} < {min_terms}。'
                f' 请检查 chunk 质量和词频提取逻辑。'
            )

    def _gate_sample_extraction(self) -> None:
        stage9 = self.state.data.get('stages', {}).get('stage9_sample_extraction', {})
        metrics = stage9.get('metrics', {})
        conclusion = metrics.get('conclusion', 'UNKNOWN')
        if conclusion != 'PASS':
            raise PipelineBlockedError(
                f'Stage 9 小样本试跑未通过: conclusion={conclusion}。'
                f' 请根据拒绝账本修正 schema/prompt 后重试。'
            )

    def _gate_preflight(self) -> None:
        stage11 = self.state.data.get('stages', {}).get('stage11_preflight_check', {})
        if not stage11.get('completed'):
            raise PipelineBlockedError('Stage 11 preflight 检查未完成。')
        metrics = stage11.get('metrics', {})
        if not metrics.get('passed', False):
            raise PipelineBlockedError('Stage 11 preflight 检查未通过。请查看 preflight_report.json。')

    def _write_not_executed_report(self) -> None:
        """Write a NOT_EXECUTED final report when Stage 12 was skipped."""
        import logging
        logger = logging.getLogger(__name__)
        logger.warning('Stage 12 was skipped; Stage 13 report marked as NOT_EXECUTED.')

        (self.output_dir / 'final_quality_report.md').write_text(
            '# Schema Design Final Quality Report\n\n'
            '## Status: NOT_EXECUTED\n\n'
            'Stage 12 (全量抽取) 未执行，因此没有实体/边数据用于最终质量复盘。\n\n'
            '要生成完整报告，请先运行 Stage 9 (小样本试跑) 和 Stage 12 (全量抽取)，'
            '这需要配置 LLM 服务和 Neo4j 图数据库。\n',
            encoding='utf-8',
        )
        write_json(self.output_dir / 'final_quality_report.json', {
            'status': 'NOT_EXECUTED',
            'reason': 'Stage 12 full extraction was skipped.',
        })
        self.state.mark_completed(
            'stage13_final_quality_report',
            {
                'output_files': {
                    'final_quality_report_md': self.output_dir / 'final_quality_report.md',
                    'final_quality_report_json': self.output_dir / 'final_quality_report.json',
                },
                'metrics': {'status': 'NOT_EXECUTED', 'reason': 'Stage 12 skipped'},
            },
            input_paths=[self.output_dir / 'schema_config.yaml'],
        )


class PipelineBlockedError(RuntimeError):
    """Raised when a quality gate blocks pipeline progression."""
    pass
