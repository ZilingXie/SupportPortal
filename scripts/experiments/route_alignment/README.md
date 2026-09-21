# Route alignment experiment

This runner compares the stored Production Account route classification with Jev
and Hermes classification-only results. It is an offline experiment tool. It
does not submit an intake event, create a Hermes case, or write to the ticket
database.

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
Only disagreement or candidate-error cases enter the CSV. The Production
baseline must carry `pipeline_version=account-layered-router-v11`; fixture rows
without that exact version fail rather than silently inventing a baseline.
