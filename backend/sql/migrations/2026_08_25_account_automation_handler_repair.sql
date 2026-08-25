-- Repair account_suspension routing rows rewritten by the retired startup
-- write-backs in ticket_repository.initialize() (p1-51: ticket 13001).
-- Manual dual-DB migration: execute on BOTH the staging and the production
-- ticket databases with the migration role, in one transaction, and record
-- the affected row count. Production expected 8 rows (2 automation + 6
-- closed suspension cases) as inventoried on 2026-08-25; a different count
-- means stop and reconcile before committing.
-- Scope: only rows with explicit suspension routing evidence and
-- route_status='automated'. Does not touch automation_status, workflow
-- state, customer messages, reply jobs, internal emails, or Zendesk status;
-- dormant detailed_invoice rows and non-suspension handlers are untouched.

UPDATE supportportal.support_account_cases
SET category = 'account_billing',
    subcategory = 'account_suspension',
    route_status = 'automated',
    automation_handler = 'account_suspension'
WHERE route_status = 'automated'
  AND (
      subcategory = 'account_suspension'
      OR route = 'account_suspension'
      OR execution_action = 'account_suspension'
      OR semantic_intent = 'billing.account_suspension'
      OR route_classification ->> 'automation_subcategory' = 'account_suspension'
  );
