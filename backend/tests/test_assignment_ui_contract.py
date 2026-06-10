from __future__ import annotations

import unittest
from pathlib import Path


class AssignmentUiContractTests(unittest.TestCase):
    def test_assignment_ui_is_served_as_standalone_static_page(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('ASSIGNMENT_DIR = UI_DIR / "assignment-ui"', main_source)
        self.assertIn(
            'app.mount("/assignment", StaticFiles(directory=ASSIGNMENT_DIR, html=True), name="assignment-ui")',
            main_source,
        )

        for path in (
            Path("ui/assignment-ui/index.html"),
            Path("ui/assignment-ui/styles.css"),
            Path("ui/assignment-ui/app.js"),
        ):
            self.assertTrue(path.exists(), f"{path} should exist")

    def test_assignment_ui_contains_engineer_selector_shift_and_mock_sla_flow(self) -> None:
        html = Path("ui/assignment-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/assignment-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/assignment-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("Engineer Assignment", html)
        self.assertIn("Choose a demo engineer", html)
        self.assertIn('id="engineer-selector"', html)
        self.assertIn("DEMO_ENGINEERS", app_source)
        self.assertIn("Jack", app_source)
        self.assertIn("Maya", app_source)
        self.assertIn("Leo", app_source)
        self.assertIn("ASSIGNMENT_AUTH_KEY", app_source)
        self.assertIn("ASSIGNMENT_SHIFT_KEY", app_source)
        self.assertIn("localStorage.setItem(key, JSON.stringify(value));", app_source)
        self.assertIn("writeStorage(ASSIGNMENT_AUTH_KEY, selectedEngineerId);", app_source)
        self.assertIn("writeStorage(ASSIGNMENT_SHIFT_KEY, shift);", app_source)
        self.assertIn("UTC+8 daily shift", app_source)
        self.assertIn("09:00", app_source)
        self.assertIn("18:00", app_source)
        self.assertIn("In shift", app_source)
        self.assertIn("Out of shift", app_source)
        self.assertIn("Eligible for assignment", app_source)
        self.assertIn("Not assignable", app_source)
        self.assertIn("Current Engineer Ticket", app_source)
        self.assertIn("3h SLA from assign", app_source)
        self.assertIn("mark engineer timeout", app_source)
        self.assertIn("transfer to next eligible engineer", app_source)
        self.assertIn("Assign next mock ticket", app_source)
        self.assertIn("Approve & send customer reply", app_source)
        self.assertIn("simulate-timeout", app_source)
        self.assertIn(".engineer-selector-grid", css)
        self.assertIn(".shift-panel", css)
        self.assertIn(".assignment-status-strip", css)
        self.assertIn(".current-ticket-sla", css)
        self.assertIn(".ticket-workbench", css)

    def test_assignment_ui_does_not_modify_existing_engineer_ui_contract(self) -> None:
        engineer_html = Path("ui/engineer-ui/index.html").read_text(encoding="utf-8")
        engineer_app = Path("ui/engineer-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("Engineer Sign In", engineer_html)
        self.assertIn('const ENGINEER_ID = "Jack";', engineer_app)
        self.assertNotIn("ASSIGNMENT_SHIFT_KEY", engineer_app)


if __name__ == "__main__":
    unittest.main()
