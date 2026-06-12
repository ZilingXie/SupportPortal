# Archive

Historical/experimental artifacts from the GB/T 25338 era. These files
carry strong domain-specific assumptions and experiment-iteration traces.
They are preserved for reference but are not part of the current product.

## Run Scripts
- `ingest_gbt.py` — original GB/T 25338 ingestion entry point (hardcoded domain)
- `v10_run.py` — v10 pipeline with rejection ledger instrumentation
- `v11_run.py` — v11 pipeline with fixed cleanup + full metrics
- `v10_analyze.py` — v10 post-ingestion Neo4j analysis

## Experiment Data
- `candidate_pool_urban_rail.yaml` — urban rail candidate entity pool
- `candidate_pool_urban_rail_signal.yaml` — railway signal candidate pool

## Report Documents
- `COMMUNITY_COMPARISON.md` — community detection comparison report
- `COMMUNITY_PERF_REPORT.md` — community detection performance
- `FINAL_SUMMARY.md` — final summary of GB/T extraction work
- `REPORT.md` — general project report
- `RESUME_BULLET.md` — resume bullet points
- `SCHEMA_MODE_REPORT.md` — strict vs lenient schema mode report
- `SIMPLIFY_REPORT.md` — simplification report
- `V1_TO_V11_PROGRESS.md` — v1 to v11 progress tracking
- `zero_degree_analysis.md` — zero-degree entity analysis
