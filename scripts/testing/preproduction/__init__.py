"""Preproduction-only regression scenarios (PP-*).

These scenarios deliberately live outside the shared
``backend.services.automation_test_scenarios.SCENARIOS`` registry: the legacy
``/automation/test`` console auto-lists that registry and its API base points
at the legacy production stack, so registering Preproduction scenarios there
would let the web console start them against the wrong environment. Run them
through the CLI in this package (``python -m scripts.testing.preproduction``).
"""
