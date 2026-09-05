import os
import unittest
from unittest.mock import patch

from backend.services.automation_contracts import (
    AutomationEnvironment,
    CommentVisibility,
    resolve_comment_visibility,
    validate_ticket_policy,
    runtime_resource_identity,
)


class AutomationContractsTest(unittest.TestCase):
    def test_environment_policy_matrix(self):
        self.assertIsNone(resolve_comment_visibility(AutomationEnvironment.STAGING, None))
        self.assertIsNone(resolve_comment_visibility(AutomationEnvironment.PREPRODUCTION, None))
        # Production no longer forces an immediate comment (p2-109 Phase B):
        # customer-visible replies are published by the parity worker, so the
        # visibility field is optional and unused at intake.
        self.assertIsNone(resolve_comment_visibility(AutomationEnvironment.PRODUCTION, None))

    def test_preproduction_matches_production_visibility_without_application_allowlist(self):
        self.assertEqual(
            validate_ticket_policy(
                AutomationEnvironment.PREPRODUCTION,
                "123",
                CommentVisibility.EXTERNAL,
            ),
            CommentVisibility.EXTERNAL,
        )
        self.assertIsNone(
            validate_ticket_policy(AutomationEnvironment.PREPRODUCTION, "789", None)
        )
        with self.assertRaisesRegex(ValueError, "requires zendesk_ticket_id"):
            validate_ticket_policy(AutomationEnvironment.PREPRODUCTION, None, None)

    def test_production_visibility_is_optional(self):
        self.assertEqual(
            validate_ticket_policy(
                AutomationEnvironment.PRODUCTION,
                "123",
                CommentVisibility.INTERNAL,
            ),
            CommentVisibility.INTERNAL,
        )
        self.assertIsNone(validate_ticket_policy(AutomationEnvironment.PRODUCTION, "123", None))

    def test_required_resource_identity_rejects_cross_environment_bindings(self):
        with patch.dict(
            os.environ,
            {
                "AUTOMATION_RUNTIME_REQUIRE_RESOURCES": "1",
                "AUTOMATION_RESOURCE_ID": "staging",
                "AUTOMATION_DB_RESOURCE_ID": "production",
                "AUTOMATION_DB_SCHEMA": "supportportal_staging",
                "AUTOMATION_DB_TABLE": "automation_executions_staging",
                "AUTOMATION_REDIS_URL": "redis://automation_redis_staging:6379/0",
                "AUTOMATION_QUEUE_NAME": "automation.staging",
                "AUTOMATION_EVENT_CHANNEL": "automation.events.staging",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "AUTOMATION_DB_RESOURCE_ID"):
                runtime_resource_identity(AutomationEnvironment.STAGING)

    def test_required_resource_identity_accepts_environment_scoped_resources(self):
        with patch.dict(
            os.environ,
            {
                "AUTOMATION_RUNTIME_REQUIRE_RESOURCES": "1",
                "AUTOMATION_RESOURCE_ID": "staging",
                "AUTOMATION_DB_RESOURCE_ID": "staging",
                "AUTOMATION_DB_SCHEMA": "supportportal_staging",
                "AUTOMATION_DB_TABLE": "automation_executions_staging",
                "AUTOMATION_REDIS_URL": "redis://automation_redis_staging:6379/0",
                "AUTOMATION_QUEUE_NAME": "automation.staging",
                "AUTOMATION_EVENT_CHANNEL": "automation.events.staging",
            },
            clear=False,
        ):
            identity = runtime_resource_identity(AutomationEnvironment.STAGING)
        self.assertEqual(identity["db_resource_id"], "staging")
        self.assertEqual(identity["db_table"], "automation_executions_staging")


if __name__ == "__main__":
    unittest.main()
