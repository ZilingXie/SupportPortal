import os
import unittest
from unittest.mock import patch

from backend.services.automation_contracts import (
    AutomationEnvironment,
    CommentVisibility,
    resolve_comment_visibility,
    validate_ticket_policy,
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


if __name__ == "__main__":
    unittest.main()
