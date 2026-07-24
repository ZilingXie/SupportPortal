-- Merge the legacy account_suspension automation subcategory into account_verification.
UPDATE support_account_cases
SET route = CASE
        WHEN route = 'account_suspension' THEN 'account_verification'
        ELSE route
    END,
    execution_action = CASE
        WHEN execution_action = 'account_suspension' THEN 'account_verification'
        ELSE execution_action
    END,
    subcategory = 'account_verification'
WHERE route_family IN ('billing_automation', 'automated')
  AND route_status = 'automated'
  AND (
      route = 'account_suspension'
      OR execution_action = 'account_suspension'
      OR subcategory = 'account_suspension'
  );

UPDATE support_billing_route_corrections
SET corrected_execution_action = CASE
        WHEN corrected_execution_action = 'account_suspension' THEN 'account_verification'
        ELSE corrected_execution_action
    END,
    first_corrected_execution_action = CASE
        WHEN first_corrected_execution_action = 'account_suspension' THEN 'account_verification'
        ELSE first_corrected_execution_action
    END
WHERE corrected_execution_action = 'account_suspension'
   OR first_corrected_execution_action = 'account_suspension';
