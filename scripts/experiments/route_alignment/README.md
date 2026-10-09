# Route alignment experiment

This offline runner compares the stored Production Account route label with a
fixed TypeSafe Jev candidate and a stateless Hermes route-prompt candidate. It
does not submit intake, create a Hermes case, or write to the ticket database.
Both candidates are mandatory.

## Workflow

Freeze the input first. Production access is limited to this separate read-only
step; the DSN is named on the command line but its value stays in the
environment. Freezing customer text requires explicit local approval:

```bash
export ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED=1
python3 -m scripts.experiments.route_alignment \
  --mode freeze \
  --production-dsn-env ROUTE_EXPERIMENT_READ_ONLY_DSN \
  --schema supportportal_production \
  --limit 100 \
  --output-dir artifacts/route-alignment/dataset-001
```

Use `--fixture fixtures/cases.jsonl` instead of `--production-dsn-env` for a
local fixture freeze. The frozen directory is created with mode `0700`; its
JSONL files use `0600` and share a content-derived `dataset_id`.

The local stateless Hermes service remains useful for offline tests. It is not
the live experiment endpoint: ordinary Hermes `/v1/responses` is rejected for
the live path because it creates agent/session state and may use tools or
fallbacks.

```bash
python3 -m scripts.experiments.route_alignment.hermes_service \
  --host 127.0.0.1 \
  --port 8765
```

The service requires `HERMES_EXPERIMENT_TOKEN`,
`HERMES_ROUTE_EXPERIMENT_API_KEY`, `HERMES_ROUTE_EXPERIMENT_BASE_URL`,
`HERMES_ROUTE_EXPERIMENT_MODEL`, and
`HERMES_ROUTE_EXPERIMENT_REASONING_EFFORT`. `HERMES_ROUTE_EXPERIMENT_MAX_OUTPUT_TOKENS`
is optional and defaults to `1600`; valid values are `256` through `8192`.
The value is recorded with each result. The service sends one Responses request
per case with no retry, fallback, tools, session, store, or ambient trace.

Run both live candidates only from a frozen dataset, using the dedicated
gateway and its capability preflight:

```bash
export ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED=1
python3 -m scripts.experiments.route_alignment \
  --frozen-snapshots artifacts/route-alignment/dataset-001/frozen_snapshots.jsonl \
  --jev-direct \
  --hermes-endpoint http://127.0.0.1:8765/v1/route-alignment/responses \
  --live-candidates \
  --output-dir artifacts/route-alignment/run-001
```

The direct Jev adapter requires `TYPESAFE_API_KEY` and fixes the provider model
to `jev-1.13.0`. The runner first reads `/v1/route-alignment/capabilities` and
requires one attempt, no fallback, no tools, no session/response store, and
structured output. It also requires the Hermes bearer token in
`HERMES_EXPERIMENT_TOKEN`. Credentials are never accepted as CLI arguments or
written to artifacts. Fixture-only comparison remains available with
`--fixture-candidates fixtures/candidates.json` and makes no provider calls.

## Input and result contracts

Jev and Hermes receive the same redacted state: subject, public messages through
the latest customer message, and allowlisted `product`/`status` metadata. They
never receive ticket identity, case alias/revision, or the Production baseline
as model input. Alias and revision exist only on the loopback transport envelope
so the runner can reject a mismatched response. Input size limits fail closed;
text is not silently truncated. Before each live candidate runs, the runner
checks that candidate's exact request size. A Jev size failure does not hide a
valid Hermes result, and vice versa; either result still requires human review
and cannot make the case automation-eligible.

Jev treats an uncertain or low-confidence cross-route additional intent as a
review signal. In particular, it cannot leave account-suspension automation
eligible when another requested route may be present. The Hermes transport
waits 75 seconds around the 60-second model deadline. Provider authentication
failure stops all later candidate calls, including model-unavailable responses
returned with HTTP 401/403. HTTP status takes precedence over body error text;
nested or malformed error bodies remain controlled candidate errors.

Production extraction selects `processing_profile='production'`, retains cases
without a v11 baseline for manual review, and samples deterministic round-robin
groups over primary label, secondary label, and route target. `comments_revision`
is preferred over the case timestamp. Historical-label agreement is reported
separately from same-input agreement because old Production labels may have been
produced from a different snapshot.

Outputs are `manifest.jsonl`, controlled `raw_results.jsonl`,
`normalized_comparison.jsonl`, `disagreement_report.<run_id>.csv`,
`candidate_error_report.<run_id>.csv`, `three_way_comparison.csv`, and
`summary.json`. `three_way_comparison.csv` has one row for every frozen case,
including candidate failures, and records Production/Hermes/Jev
`primary_label`, `secondary_label`, `conversation_subcategory`, and
`route_target`, plus the differing fields for Production–Hermes,
Production–Jev, and Hermes–Jev. `summary.json` reports each pair's
comparable sample count and per-field/overall agreement rate; each denominator
uses only cases where both sides succeeded and the compared fields are present.
The disagreement
report contains only valid classifications with field differences. Candidate
errors, input-size failures, and missing baselines are recorded separately and
are excluded from agreement denominators. The controlled evidence file does
not store arbitrary provider responses or customer text. Failure diagnostics
use the same sanitized `metadata.diagnostics` object in JSONL and CSV:
`wrapper_http_status` identifies the loopback response and
`provider_http_status` identifies the upstream response when available. Failed
Hermes calls preserve every upstream HTTP 4xx/5xx status; authentication and
rate-limit responses keep their dedicated error codes, while other statuses use
`provider_http_error`. Invalid enum types in an otherwise valid model JSON are
reported as `invalid_model_classification`. Both paths retain model,
reasoning/output limits, token usage, incomplete
status, implementation/schema/config provenance, Route Manual content hash,
and normalizer version. The summary aggregates those identities across success
and failure results. Every result carries
one `run_id` and `dataset_id`; the summary records attempted/success/valid-
comparison counts, completion rate, error categories, latency, model versions,
output configuration, usage, agreement, model-identity readiness, and Jev's
documented cost estimate. A live candidate without a provider-returned model
identity or with any candidate error cannot set
`formal_experiment_ready=true`. An authentication error stops all later paid
calls. The runner refuses to overwrite a non-empty output directory.

For a controlled Hermes parameter experiment, keep the frozen dataset fixed and
change only `HERMES_ROUTE_EXPERIMENT_REASONING_EFFORT` and
`HERMES_ROUTE_EXPERIMENT_MAX_OUTPUT_TOKENS` between fresh output directories.
Record the configuration in each `summary.json`; do not combine runs with
different prompt, schema, normalizer, model, or configuration versions.

Default result artifacts remove `backend_operation.evidence` customer text.
`--include-review-text` adds redacted `review_context.jsonl` only when
`ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED=1`. Before sending real case text to a
provider, separately confirm the provider's data-processing boundary; this tool
does not grant that approval.
