-- SupportPortal ticket storage schema (PostgreSQL)
-- This file documents the table design used by backend/repositories/ticket_repository.py.

CREATE TABLE IF NOT EXISTS support_ticket_schema_meta (
    config_key TEXT PRIMARY KEY,
    config_value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    requester TEXT NOT NULL,
    subject TEXT NOT NULL,
    status TEXT NOT NULL,
    last_engineer_action JSONB,
    active_engineer_case_id TEXT,
    engineer_case_count INTEGER NOT NULL DEFAULT 0,
    product TEXT,
    product_selection_state JSONB,
    client_intake_state JSONB,
    client_agent_runtime_state JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS support_ticket_messages (
    id BIGSERIAL PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    sentiment_label TEXT,
    sources JSONB,
    citations JSONB,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS support_ticket_events (
    id BIGSERIAL PRIMARY KEY,
    ticket_id TEXT REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_ticket_agent_events (
    id BIGSERIAL PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    message_id TEXT,
    run_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    phase TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_assets (
    asset_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    extension TEXT NOT NULL,
    status TEXT NOT NULL,
    storage_provider TEXT NOT NULL,
    bucket TEXT NOT NULL,
    s3_key TEXT NOT NULL,
    etag TEXT,
    checksum TEXT,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    uploaded_at TIMESTAMPTZ,
    attached_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS support_asset_events (
    id BIGSERIAL PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES support_assets(asset_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_ticket_investigations (
    investigation_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    trigger_source TEXT NOT NULL,
    draft_customer_reply TEXT,
    final_confirmation_requested_at TIMESTAMPTZ,
    opened_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS support_ticket_investigation_messages (
    id BIGSERIAL PRIMARY KEY,
    message_id TEXT NOT NULL,
    investigation_id TEXT NOT NULL REFERENCES support_ticket_investigations(investigation_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    meta JSONB
);

CREATE TABLE IF NOT EXISTS support_engineer_cases (
    engineer_case_id TEXT PRIMARY KEY,
    client_ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    case_sequence INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_source TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    draft_customer_reply TEXT,
    final_confirmation_requested_at TIMESTAMPTZ,
    engineer_handoff_packet JSONB,
    engineer_agent_state JSONB,
    opened_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS support_engineer_case_messages (
    id BIGSERIAL PRIMARY KEY,
    message_id TEXT NOT NULL,
    engineer_case_id TEXT NOT NULL REFERENCES support_engineer_cases(engineer_case_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    meta JSONB
);

CREATE TABLE IF NOT EXISTS support_engineer_case_events (
    id BIGSERIAL PRIMARY KEY,
    engineer_case_id TEXT REFERENCES support_engineer_cases(engineer_case_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_engineer_hitl_feedback (
    feedback_id TEXT PRIMARY KEY,
    engineer_case_id TEXT NOT NULL REFERENCES support_engineer_cases(engineer_case_id) ON DELETE CASCADE,
    client_ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    run_id TEXT,
    message_id TEXT,
    evidence_packet_id TEXT,
    feedback_type TEXT NOT NULL,
    diagnosis_correctness TEXT NOT NULL,
    root_cause_correctness TEXT NOT NULL,
    evidence_quality TEXT NOT NULL,
    citation_quality TEXT NOT NULL,
    customer_reply_quality TEXT NOT NULL,
    missing_information JSONB NOT NULL DEFAULT '[]'::jsonb,
    incorrect_claims JSONB NOT NULL DEFAULT '[]'::jsonb,
    corrected_root_cause TEXT,
    corrected_solution TEXT,
    corrected_customer_reply TEXT,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    memory_candidate TEXT NOT NULL,
    memory_safety TEXT NOT NULL,
    memory_notes TEXT,
    prompt_version TEXT,
    workflow_version TEXT,
    tool_policy_version TEXT,
    rag_access_policy_version TEXT,
    evidence_packet_version TEXT,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_support_tickets_status_updated
    ON support_tickets (status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_ticket_messages_ticket_created
    ON support_ticket_messages (ticket_id, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_support_ticket_events_ticket_created
    ON support_ticket_events (ticket_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_ticket_agent_events_ticket_created
    ON support_ticket_agent_events (ticket_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_assets_ticket_customer
    ON support_assets (ticket_id, customer_id);

CREATE INDEX IF NOT EXISTS idx_support_asset_events_asset_created
    ON support_asset_events (asset_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_ticket_investigations_ticket_updated
    ON support_ticket_investigations (ticket_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_ticket_investigation_messages_created
    ON support_ticket_investigation_messages (investigation_id, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_support_engineer_cases_ticket_updated
    ON support_engineer_cases (client_ticket_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_engineer_cases_status_updated
    ON support_engineer_cases (status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_engineer_case_messages_created
    ON support_engineer_case_messages (engineer_case_id, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_support_engineer_case_events_created
    ON support_engineer_case_events (engineer_case_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_engineer_hitl_feedback_case_created
    ON support_engineer_hitl_feedback (engineer_case_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_engineer_hitl_feedback_ticket_created
    ON support_engineer_hitl_feedback (client_ticket_id, created_at DESC);
