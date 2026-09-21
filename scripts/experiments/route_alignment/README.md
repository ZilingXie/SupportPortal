# Route alignment experiment

This runner compares the stored Production Account route classification with Jev
and Hermes classification-only results. It is an offline experiment tool. It
does not submit an intake event, create a Hermes case, or write to the ticket
database. Both Jev and Hermes are mandatory; a one-candidate run is rejected.

The default mode uses redacted JSONL fixtures and candidate fixtures:

```bash
python3 -m scripts.experiments.route_alignment \
  --fixture fixtures/cases.jsonl \
  --fixture-candidates fixtures/candidates.json \
  --output-dir artifacts/route-alignment/run-001
```

The live snapshot mode uses a read-only DSN and includes public case comments:

```bash
python3 -m scripts.experiments.route_alignment \
  --production-dsn "$PRODUCTION_READONLY_DSN" \
  --schema supportportal_production \
  --limit 100 \
  --fixture-candidates fixtures/candidates.json \
  --output-dir artifacts/route-alignment/run-001
```

Candidate HTTP endpoints are disabled unless `--live-candidates` is supplied.
They must implement the classification-only contract and must not be the normal
SupportPortal intake endpoint:

```bash
python3 -m scripts.experiments.route_alignment \
  --production-dsn "$PRODUCTION_READONLY_DSN" \
  --jev-endpoint "$JEV_CLASSIFICATION_ENDPOINT" \
  --hermes-endpoint "$HERMES_CLASSIFICATION_ONLY_ENDPOINT" \
  --live-candidates \
  --output-dir artifacts/route-alignment/run-001
```

Outputs are `manifest.jsonl`, `raw_results.jsonl`,
`normalized_comparison.jsonl`, `disagreement_report.csv`, and `summary.json`.
The manifest contains text hashes and lengths by default, not customer text.
`--include-review-text` requires `ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED=1` and
writes a separate redacted `review_context.jsonl`.

Production extraction is restricted to `processing_profile='production'`. It
keeps cases without a v11 baseline, marks them `baseline_status=missing`, and
includes them in the review union. The sample pool is selected by deterministic
round-robin groups over primary label, secondary label, and route target. Case
revision uses `comments_revision` when available and records its source.

The HTTP adapter uses the dedicated `route-alignment-v1` case-snapshot contract:

```json
{
  "contract": "route-alignment-v1",
  "case_snapshot": {
    "case_alias": "prod-001",
    "case_revision": "...",
    "subject": "...",
    "messages": []
  }
}
```

The existing Hermes `classify_route` tool is not compatible: it only accepts a
model-produced `classification` object for normalization. Real candidate calls
also require `ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED=1`; the endpoint must
return a non-empty `classification` or `normalized_classification` plus optional
`model_version` and `prompt_version`.
