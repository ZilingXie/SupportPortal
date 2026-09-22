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

Start the stateless Hermes candidate on loopback after setting its explicit
model profile and an experiment-only bearer token:

```bash
python3 -m scripts.experiments.route_alignment.hermes_service \
  --host 127.0.0.1 \
  --port 8765
```

The service requires `HERMES_EXPERIMENT_TOKEN`,
`HERMES_ROUTE_EXPERIMENT_API_KEY`, `HERMES_ROUTE_EXPERIMENT_BASE_URL`,
`HERMES_ROUTE_EXPERIMENT_MODEL`, and
`HERMES_ROUTE_EXPERIMENT_REASONING_EFFORT`. It sends one Responses request per
case with no retry, fallback, tools, session, store, or ambient trace.

Run both live candidates only from a frozen dataset:

```bash
export ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED=1
python3 -m scripts.experiments.route_alignment \
  --frozen-snapshots artifacts/route-alignment/dataset-001/frozen_snapshots.jsonl \
  --jev-direct \
  --hermes-endpoint http://127.0.0.1:8765/route-alignment/v1/classify \
  --live-candidates \
  --output-dir artifacts/route-alignment/run-001
```

The direct Jev adapter requires `TYPESAFE_API_KEY` and fixes the provider model
to `jev-1.13.0`. The runner requires the Hermes bearer token in
`HERMES_EXPERIMENT_TOKEN`. Credentials are never accepted as CLI arguments or
written to artifacts. Fixture-only comparison remains available with
`--fixture-candidates fixtures/candidates.json` and makes no provider calls.

## Input and result contracts

Jev and Hermes receive the same redacted state: subject, public messages through
the latest customer message, and allowlisted `product`/`status` metadata. They
never receive ticket identity, case alias/revision, or the Production baseline
as model input. Alias and revision exist only on the loopback transport envelope
so the runner can reject a mismatched response. Input size limits fail closed;
text is not silently truncated. Before either candidate runs, the runner checks
the exact Jev and Hermes request sizes and rejects both candidates together if
either request is too large.

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
`normalized_comparison.jsonl`, `disagreement_report.<run_id>.csv`, and
`summary.json`. The controlled evidence file does not store arbitrary provider
responses or customer text. Every result carries one `run_id` and `dataset_id`;
the summary records per-candidate calls, errors, latency, model versions, usage,
agreement, model-identity readiness, and Jev's documented cost estimate. A live
candidate without a provider-returned model identity is an error and cannot set
`formal_experiment_ready=true`. An authentication error stops all later paid
calls. The runner refuses to overwrite a non-empty output directory.

Default result artifacts remove `backend_operation.evidence` customer text.
`--include-review-text` adds redacted `review_context.jsonl` only when
`ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED=1`. Before sending real case text to a
provider, separately confirm the provider's data-processing boundary; this tool
does not grant that approval.
