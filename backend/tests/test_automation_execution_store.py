import os
import unittest
from unittest.mock import patch

from backend.services.automation_execution_store import AutomationExecutionStore


class AutomationExecutionStoreTest(unittest.TestCase):
    def test_environment_table_is_explicit_and_schema_qualified(self):
        with patch.dict(
            os.environ,
            {
                "AUTOMATION_DB_SCHEMA": "supportportal_preproduction",
                "AUTOMATION_DB_TABLE": "automation_executions_preproduction",
            },
            clear=False,
        ):
            store = AutomationExecutionStore(environment="preproduction")

        self.assertEqual(
            store._table(),
            '"supportportal_preproduction"."automation_executions_preproduction"',
        )

    def test_default_table_is_environment_specific(self):
        with patch.dict(os.environ, {}, clear=True):
            store = AutomationExecutionStore(environment="staging")

        self.assertEqual(store._table(), '"supportportal"."automation_executions_staging"')

    def test_invalid_table_name_is_rejected(self):
        with patch.dict(os.environ, {"AUTOMATION_DB_TABLE": "automation;drop"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "AUTOMATION_DB_TABLE"):
                AutomationExecutionStore(environment="staging")


if __name__ == "__main__":
    unittest.main()
