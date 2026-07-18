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

CREATE TABLE IF NOT EXISTS support_billing_tickets (
    billing_ticket_id TEXT PRIMARY KEY,
    client_ticket_id TEXT NOT NULL UNIQUE REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    external_id TEXT,
    created_by TEXT,
    title TEXT NOT NULL,
    question TEXT NOT NULL,
    route TEXT,
    scope_label TEXT,
    route_family TEXT,
    execution_action TEXT,
    tooling_profile TEXT,
    route_reason TEXT,
    route_confidence REAL,
    matched_signals JSONB,
    automation_status TEXT NOT NULL,
    missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    collected_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    customer_reply TEXT,
    internal_email_payload JSONB,
    internal_email_send_status TEXT,
    internal_email_send_reason TEXT,
    semantic_intent TEXT,
    automation_eligibility TEXT,
    policy_decision TEXT,
    not_automated_reason TEXT,
    risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_spans JSONB NOT NULL DEFAULT '[]'::jsonb,
    router_source TEXT,
    route_review_status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_billing_response_tokens (
    token_hash TEXT PRIMARY KEY,
    billing_ticket_id TEXT NOT NULL REFERENCES support_billing_tickets(billing_ticket_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS support_billing_route_corrections (
    billing_ticket_id TEXT PRIMARY KEY REFERENCES support_billing_tickets(billing_ticket_id) ON DELETE CASCADE,
    client_ticket_id TEXT NOT NULL,
    original_scope_label TEXT,
    original_route_family TEXT,
    original_execution_action TEXT,
    original_tooling_profile TEXT,
    original_route_reason TEXT,
    original_route_confidence REAL,
    corrected_scope_label TEXT NOT NULL,
    corrected_route_family TEXT NOT NULL,
    corrected_execution_action TEXT NOT NULL,
    corrected_tooling_profile TEXT NOT NULL,
    first_corrected_scope_label TEXT NOT NULL,
    first_corrected_route_family TEXT NOT NULL,
    first_corrected_execution_action TEXT NOT NULL,
    first_corrected_tooling_profile TEXT NOT NULL,
    corrector TEXT,
    correction_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    closed_at TIMESTAMPTZ,
    assigned_engineer_id TEXT,
    assignment_status TEXT NOT NULL DEFAULT 'pending',
    assigned_at TIMESTAMPTZ,
    sla_due_at TIMESTAMPTZ,
    assignment_attempt_count INTEGER NOT NULL DEFAULT 0,
    previous_assignees JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_assignment_reason TEXT,
    dispatch_status TEXT NOT NULL DEFAULT 'pending',
    assignment_updated_at TIMESTAMPTZ,
    assignment_version INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS assigned_engineer_id TEXT;
ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS assignment_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ;
ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS sla_due_at TIMESTAMPTZ;
ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS assignment_attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS previous_assignees JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS last_assignment_reason TEXT;
ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS dispatch_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS assignment_updated_at TIMESTAMPTZ;
ALTER TABLE support_engineer_cases
    ADD COLUMN IF NOT EXISTS assignment_version INTEGER NOT NULL DEFAULT 0;

UPDATE support_engineer_cases
SET assignment_status = CASE
        WHEN closed_at IS NOT NULL OR status = 'resolved' THEN 'resolved'
        WHEN assigned_engineer_id IS NOT NULL THEN 'assigned'
        ELSE 'pending'
    END,
    dispatch_status = CASE
        WHEN closed_at IS NOT NULL OR status = 'resolved' THEN 'resolved'
        WHEN assigned_engineer_id IS NOT NULL THEN 'assigned'
        ELSE 'pending'
    END
WHERE assignment_version = 0;

CREATE INDEX IF NOT EXISTS idx_support_engineer_cases_assignment_queue
    ON support_engineer_cases (assignment_status, sla_due_at, updated_at);

CREATE TABLE IF NOT EXISTS support_workspace_accounts (
    account_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    availability TEXT NOT NULL DEFAULT 'unavailable',
    availability_reason TEXT,
    availability_updated_at TIMESTAMPTZ,
    last_assigned_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_support_workspace_accounts_dispatch
    ON support_workspace_accounts (role, active, availability, last_assigned_at, account_id);

CREATE TABLE IF NOT EXISTS support_workspace_audit_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    target_id TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS support_idempotency_records (
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL,
    response_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (scope, idempotency_key)
);

CREATE TABLE IF NOT EXISTS support_rollout_counters (
    counter_key TEXT PRIMARY KEY,
    current_value BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS support_rollout_events (
    counter_key TEXT NOT NULL REFERENCES support_rollout_counters(counter_key) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    position BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (counter_key, event_key),
    UNIQUE (counter_key, position)
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

CREATE TABLE IF NOT EXISTS support_case_memory_ledger (
    memory_record_id TEXT PRIMARY KEY,
    source_feedback_id TEXT NOT NULL REFERENCES support_engineer_hitl_feedback(feedback_id) ON DELETE CASCADE,
    engineer_case_id TEXT NOT NULL REFERENCES support_engineer_cases(engineer_case_id) ON DELETE CASCADE,
    client_ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    feedback_type TEXT NOT NULL,
    ledger_status TEXT NOT NULL,
    retrieval_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    active_memory_status TEXT NOT NULL,
    symptom TEXT,
    root_cause TEXT,
    solution TEXT,
    customer_safe_summary TEXT,
    internal_only_summary TEXT,
    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    safety_label TEXT NOT NULL,
    quality_label TEXT NOT NULL,
    memory_schema_version TEXT NOT NULL,
    prompt_version TEXT,
    workflow_version TEXT,
    tool_policy_version TEXT,
    rag_access_policy_version TEXT,
    evidence_packet_version TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Engineer replay eval dataset items, generated on final_approve.
CREATE TABLE IF NOT EXISTS support_engineer_replay_eval_items (
    eval_item_id TEXT PRIMARY KEY,
    client_ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    engineer_case_id TEXT NOT NULL REFERENCES support_engineer_cases(engineer_case_id) ON DELETE CASCADE,
    source_summary_packet_id TEXT NOT NULL DEFAULT '',
    source_summary_packet_version TEXT NOT NULL DEFAULT '',
    source_plan_id TEXT NOT NULL DEFAULT '',
    source_execution_id TEXT NOT NULL DEFAULT '',
    source_review_id TEXT NOT NULL DEFAULT '',
    review_decision TEXT NOT NULL DEFAULT '',
    review_trace JSONB NOT NULL DEFAULT '{}'::jsonb,
    replan_notes JSONB NOT NULL DEFAULT '[]'::jsonb,
    engineer_revise_feedback JSONB NOT NULL DEFAULT '[]'::jsonb,
    approved_reply TEXT NOT NULL DEFAULT '',
    guardrail_final JSONB NOT NULL DEFAULT '{}'::jsonb,
    expected_outcome TEXT NOT NULL DEFAULT 'resolved_with_customer_reply',
    replay_input JSONB NOT NULL DEFAULT '{}'::jsonb,
    reference_output JSONB NOT NULL DEFAULT '{}'::jsonb,
    dataset_status TEXT NOT NULL DEFAULT 'candidate',
    schema_version TEXT NOT NULL DEFAULT '',
    data_quality_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_support_tickets_status_updated
    ON support_tickets (status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_ticket_messages_ticket_created
    ON support_ticket_messages (ticket_id, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_support_ticket_events_ticket_created
    ON support_ticket_events (ticket_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_ticket_agent_events_ticket_created
    ON support_ticket_agent_events (ticket_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_billing_tickets_created
    ON support_billing_tickets (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_billing_response_tokens_ticket
    ON support_billing_response_tokens (billing_ticket_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_billing_route_corrections_updated
    ON support_billing_route_corrections (updated_at DESC);

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

CREATE INDEX IF NOT EXISTS idx_support_case_memory_ledger_case_created
    ON support_case_memory_ledger (engineer_case_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_case_memory_ledger_ticket_created
    ON support_case_memory_ledger (client_ticket_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_case_memory_ledger_retrieval
    ON support_case_memory_ledger (retrieval_enabled, ledger_status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_engineer_replay_eval_case_created
    ON support_engineer_replay_eval_items (engineer_case_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_engineer_replay_eval_ticket_created
    ON support_engineer_replay_eval_items (client_ticket_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_engineer_replay_eval_status_created
    ON support_engineer_replay_eval_items (dataset_status, created_at DESC);
