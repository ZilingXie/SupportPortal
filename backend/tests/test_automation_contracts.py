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
        self.assertEqual(
            resolve_comment_visibility(AutomationEnvironment.PREPRODUCTION, None),
            CommentVisibility.INTERNAL,
        )
        with self.assertRaises(ValueError):
            resolve_comment_visibility(AutomationEnvironment.PRODUCTION, None)

    def test_preproduction_requires_allowlisted_ticket_and_internal_comment(self):
        with patch.dict(os.environ, {"PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST": "123,456"}, clear=False):
            self.assertEqual(
                validate_ticket_policy(
                    AutomationEnvironment.PREPRODUCTION,
                    "123",
                    CommentVisibility.INTERNAL,
                ),
                CommentVisibility.INTERNAL,
            )
            with self.assertRaises(ValueError):
                validate_ticket_policy(
                    AutomationEnvironment.PREPRODUCTION,
                    "789",
                    CommentVisibility.INTERNAL,
                )
            with self.assertRaises(ValueError):
                validate_ticket_policy(
                    AutomationEnvironment.PREPRODUCTION,
                    "123",
                    CommentVisibility.EXTERNAL,
                )

    def test_production_visibility_is_explicit(self):
        self.assertEqual(
            validate_ticket_policy(
                AutomationEnvironment.PRODUCTION,
                "123",
                CommentVisibility.INTERNAL,
            ),
            CommentVisibility.INTERNAL,
        )
        with self.assertRaises(ValueError):
            validate_ticket_policy(AutomationEnvironment.PRODUCTION, "123", None)

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
