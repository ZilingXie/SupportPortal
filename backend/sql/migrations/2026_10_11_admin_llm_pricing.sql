-- Admin model pricing registry and immutable per-call pricing snapshots.
CREATE TABLE IF NOT EXISTS supportportal.support_llm_pricing (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_usd_per_1m NUMERIC(20,8),
    cached_input_usd_per_1m NUMERIC(20,8),
    output_usd_per_1m NUMERIC(20,8),
    embedding_usd_per_1m NUMERIC(20,8),
    pricing_source TEXT NOT NULL DEFAULT 'unpriced',
    source_url TEXT,
    observed_at TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, model),
    CONSTRAINT support_llm_pricing_source_check CHECK (pricing_source IN ('assistant_found','user_confirmed','unpriced'))
);

ALTER TABLE supportportal.support_account_case_llm_usage
    ADD COLUMN IF NOT EXISTS input_usd_per_1m NUMERIC(20,8),
    ADD COLUMN IF NOT EXISTS cached_input_usd_per_1m NUMERIC(20,8),
    ADD COLUMN IF NOT EXISTS output_usd_per_1m NUMERIC(20,8),
    ADD COLUMN IF NOT EXISTS embedding_usd_per_1m NUMERIC(20,8),
    ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(20,8),
    ADD COLUMN IF NOT EXISTS pricing_source TEXT NOT NULL DEFAULT 'legacy_unpriced',
    ADD COLUMN IF NOT EXISTS pricing_source_url TEXT,
    ADD COLUMN IF NOT EXISTS pricing_observed_at TIMESTAMPTZ;

GRANT SELECT, INSERT, UPDATE, DELETE ON supportportal.support_llm_pricing TO supportportal_runtime;

INSERT INTO supportportal.support_llm_pricing
    (provider, model, input_usd_per_1m, cached_input_usd_per_1m, output_usd_per_1m, pricing_source)
VALUES
    ('openai', 'gpt-5.6-luna', 0.2, 0.02, 1.2, 'user_confirmed'),
    ('openai', 'gpt-6-astra', 10.0, 1.0, 50.0, 'user_confirmed')
ON CONFLICT (provider, model) DO NOTHING;
