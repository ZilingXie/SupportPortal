-- Restore account_suspension only when the stored semantic route explicitly identifies it.
UPDATE support_account_cases
SET route = 'account_suspension',
    execution_action = 'account_suspension',
    subcategory = 'account_suspension',
    category = 'automation',
    automation_handler = 'billing',
    route_classification = CASE
        WHEN route_classification <> '{}'::jsonb
            THEN jsonb_set(
                route_classification,
                '{automation_subcategory}',
                '"account_suspension"'::jsonb,
                true
            )
        ELSE route_classification
    END
WHERE route_family IN ('billing_automation', 'automated')
  AND route_status = 'automated'
  AND (
      semantic_intent = 'billing.account_suspension'
      OR route_classification ->> 'automation_subcategory' = 'account_suspension'
  );
