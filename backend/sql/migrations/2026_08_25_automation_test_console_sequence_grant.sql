-- Sequence grant for the automation test console (p2-97/p2-101 family).
-- Manual dual-DB migration: execute on BOTH the staging and the production
-- ticket databases with the migration role. The 2026_08_23 console migration
-- granted the tables but omitted the BIGSERIAL sequence, so runtime INSERTs
-- failed with "permission denied for sequence automation_test_tickets_id_seq"
-- until this grant ran. automation_test_scenario_runs uses a TEXT primary key
-- and has no sequence.
-- Applied out-of-band to both live databases on 2026-08-25; this file keeps
-- fresh environments correct.

GRANT USAGE ON SEQUENCE supportportal.automation_test_tickets_id_seq
TO supportportal_runtime;
