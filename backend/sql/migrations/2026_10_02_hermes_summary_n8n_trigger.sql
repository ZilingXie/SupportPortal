-- Allow the n8n knowledge-source intake to enqueue a Hermes Summary task.
-- The ticket repository schema version bump applies the same change during
-- normal bootstrap; this file documents the standalone migration contract.
ALTER TABLE support_hermes_summary_tasks
    DROP CONSTRAINT IF EXISTS support_hermes_summary_tasks_trigger_kind_check;
ALTER TABLE support_hermes_summary_tasks
    ADD CONSTRAINT support_hermes_summary_tasks_trigger_kind_check
    CHECK (trigger_kind IN ('solved', 'local_resolved', 'closed', 'n8n_source'));
