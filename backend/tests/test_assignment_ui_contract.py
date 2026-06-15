from __future__ import annotations

import unittest
import re
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
            Path("ui/assignment-ui/admin/index.html"),
        ):
            self.assertTrue(path.exists(), f"{path} should exist")

    def test_assignment_ui_contains_welcome_ready_and_admin_flow(self) -> None:
        html = Path("ui/assignment-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/assignment-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/assignment-ui/styles.css").read_text(encoding="utf-8")
        admin_html = Path("ui/assignment-ui/admin/index.html").read_text(encoding="utf-8")

        self.assertIn("Engineer Assignment", html)
        self.assertIn("Choose a demo engineer", html)
        self.assertIn('id="engineer-selector"', html)
        self.assertIn("./styles.css?v=20260615-assignment-rail-centerline-1", html)
        self.assertIn("./app.js?v=20260615-assignment-rail-centerline-1", html)
        self.assertIn("../styles.css?v=20260615-assignment-rail-centerline-1", admin_html)
        self.assertIn("../app.js?v=20260615-assignment-rail-centerline-1", admin_html)
        self.assertIn("DEMO_ENGINEERS", app_source)
        self.assertIn("Jack", app_source)
        self.assertIn("Maya", app_source)
        self.assertIn("Leo", app_source)
        self.assertIn("ASSIGNMENT_AUTH_KEY", app_source)
        self.assertIn("ASSIGNMENT_SHIFT_KEY", app_source)
        self.assertIn("localStorage.setItem(key, JSON.stringify(value));", app_source)
        self.assertIn("writeStorage(ASSIGNMENT_AUTH_KEY, selectedEngineerId);", app_source)
        self.assertIn("writeStorage(ASSIGNMENT_SHIFT_KEY, shift);", app_source)

        # Welcome / readiness page markers
        self.assertIn("renderWelcome", app_source)
        self.assertIn("I&rsquo;m ready to roll", app_source)
        self.assertIn("ready-to-roll", app_source)
        self.assertIn("ready-for-next", app_source)
        self.assertIn("View readiness overview", app_source)
        self.assertIn("enter-welcome", app_source)
        self.assertIn("workspaceActive", app_source)
        self.assertIn("ASSIGNMENT_WORKSPACE_KEY", app_source)

        # Out-of-shift disabled behavior
        self.assertIn("disabled", app_source)
        self.assertIn("!inShift", app_source)

        # Welcome page info cards
        self.assertIn("welcome-view", app_source)
        self.assertIn("welcome-hero", app_source)
        self.assertIn("welcome-grid", app_source)
        self.assertIn("welcome-info-card", app_source)
        self.assertIn("welcome-actions", app_source)
        self.assertIn("UTC+8 current time", app_source)
        self.assertIn("Daily shift", app_source)
        self.assertIn("Queue status", app_source)
        self.assertIn("Active ticket", app_source)
        self.assertIn("SLA policy", app_source)
        self.assertIn("Signed in as", app_source)

        # Welcome page daily shift card now includes inline editable form
        self.assertIn("welcome-shift-form", app_source)
        self.assertIn("welcome-shift-form", css)
        self.assertIn("repeat(auto-fit, minmax(min(100%, 150px), 1fr))", css)
        self.assertIn(".welcome-shift-form input", css)
        self.assertIn("Save shift", app_source)

        # Sidebar / workspace retained
        self.assertIn("assignment-sidebar", app_source)
        self.assertIn("engineer-rail", app_source)
        self.assertIn("rail-brand", app_source)
        self.assertIn("Concierge AI", app_source)
        self.assertIn("panel-card", app_source)
        self.assertIn("btn btn-primary", app_source)
        self.assertIn("detail-investigation-draft", app_source)
        self.assertIn("sidebarCollapsed", app_source)
        self.assertIn("Engineer context", app_source)
        self.assertIn("Problem workspace", app_source)

        # Hover sidebar replaces manual toggle as primary interaction
        self.assertIn(".engineer-rail.assignment-sidebar:hover", css)
        self.assertIn(".engineer-rail.assignment-sidebar:focus-within", css)
        self.assertIn('tabindex="0"', app_source)
        self.assertIn("rail-compact-stack", app_source)
        self.assertIn("rail-compact-avatar", app_source)
        self.assertIn(".rail-compact-stack", css)
        self.assertIn(".rail-compact-status", css)
        self.assertNotIn("data-action=\"toggle-sidebar\"", app_source)

        # Ready loading transition
        self.assertIn("readyTransitionActive", app_source)
        self.assertIn("readyTransitionTimer", app_source)
        self.assertIn("cancelReadyTransition", app_source)
        self.assertIn("window.clearTimeout(readyTransitionTimer)", app_source)
        self.assertIn("renderReadyLoading", app_source)
        self.assertIn("ready-loading-view", app_source)
        self.assertIn("ready-loading-spinner", app_source)
        self.assertIn("setTimeout(() => {", app_source)
        self.assertIn("}, 900);", app_source)
        self.assertIn("if (readyTransitionActive) return;", app_source)

        # Break after this case
        self.assertIn("ASSIGNMENT_BREAK_AFTER_CASE_KEY", app_source)
        self.assertIn("breakAfterCase", app_source)
        self.assertIn("Break after this case", app_source)
        self.assertIn("Break queued after this case", app_source)
        self.assertNotIn("coffee_off", app_source)
        self.assertIn("toggle-break-after-case", app_source)
        self.assertIn("toggleBreakAfterCase", app_source)
        self.assertIn("shouldBreak = breakAfterCase", app_source)
        self.assertIn("writeStorage(ASSIGNMENT_BREAK_AFTER_CASE_KEY, breakAfterCase)", app_source)
        self.assertIn("Customer problem", app_source)
        self.assertIn("Engineer AI investigation", app_source)
        self.assertIn("Draft Customer Reply", app_source)
        self.assertIn("Approve & send customer reply", app_source)

        # Explicit assignment instead of auto-assign
        self.assertIn("assignNextTicket", app_source)
        self.assertIn("readyToRoll", app_source)
        self.assertIn("readyForNextCase", app_source)
        self.assertIn("releaseActiveAssignment", app_source)
        self.assertIn("releaseActiveAssignment();", app_source)
        self.assertIn("queue = [ticket, ...queue];", app_source)
        self.assertIn("activeTicket = null;", app_source)
        self.assertIn("Assignment released", app_source)
        self.assertIn("activeTicket.engineerId !== selectedEngineerId", app_source)
        self.assertNotIn("autoAssignIfEligible", app_source)

        # Still handles pause outside shift
        self.assertIn("pauseAssignmentOutsideShift", app_source)
        self.assertIn("returned to queue", app_source)
        self.assertIn("Waiting for your UTC+8 shift", app_source)
        self.assertIn("No active Engineer Ticket", app_source)

        self.assertIn("UTC+8 daily shift", app_source)
        self.assertIn("09:00", app_source)
        self.assertIn("18:00", app_source)
        self.assertIn("In shift", app_source)
        self.assertIn("Out of shift", app_source)
        self.assertIn("Ready for next", app_source)
        self.assertIn("Not assignable", app_source)
        self.assertIn("Current Engineer Ticket", app_source)
        self.assertIn("3h SLA from assign", app_source)
        self.assertIn("mark engineer timeout", app_source)
        self.assertIn("transfer to next eligible engineer", app_source)
        self.assertIn("simulate-timeout", app_source)

        # Admin page — data model
        self.assertIn("ASSIGNMENT_ADMIN_SCHEDULE_KEY", app_source)
        self.assertIn("DEFAULT_ADMIN_SCHEDULE", app_source)
        self.assertIn("ADMIN_PRESENCE_MOCK", app_source)
        self.assertIn("ENGINEER_COLORS", app_source)
        self.assertIn("WEEKDAYS", app_source)
        self.assertIn("getEngineersOnShiftNow", app_source)
        self.assertIn("isEngineerOnShiftAtHour", app_source)
        self.assertIn("normalizeAdminSchedule", app_source)
        self.assertIn("getShiftForScheduleCell", app_source)
        self.assertIn("getPreviousWeekday", app_source)
        self.assertIn("previousOvernightShiftCoversMinute", app_source)
        self.assertIn("adminSchedule", app_source)
        self.assertIn("adminEditState", app_source)
        self.assertIn("saveAdminSchedule", app_source)

        # Admin page — render
        self.assertIn("System Admin", admin_html)
        self.assertIn("renderAdmin", app_source)
        self.assertIn("admin-shell", app_source)
        self.assertIn("admin-topbar", app_source)
        self.assertIn("admin-metric-grid", app_source)
        self.assertIn("admin-bottom-grid", app_source)
        self.assertIn("Operations Overview", app_source)
        self.assertIn("Shift Schedule", app_source)
        self.assertIn("On Shift Engineers", app_source)
        self.assertIn("Online Coverage", app_source)
        self.assertIn("Online", app_source)
        self.assertIn("Offline", app_source)
        self.assertIn("admin-main-schedule", app_source)
        self.assertIn("admin-schedule-grid", app_source)
        self.assertIn("schedule-chip", app_source)
        self.assertIn("Modify Shifts", app_source)
        self.assertIn("admin-modify-shifts", app_source)
        self.assertIn("admin-edit-target-form", app_source)
        self.assertNotIn("admin-shift-picker", app_source)
        self.assertNotIn("data-admin-shift-picker", app_source)
        self.assertNotIn("Edit selected shift", app_source)
        self.assertIn("admin-edit-panel", app_source)
        self.assertIn("admin-edit-form", app_source)
        self.assertIn("admin-save-shift", app_source)
        self.assertIn("admin-close-panel", app_source)
        self.assertIn("Pending Triage", app_source)
        self.assertIn("activeTicket.engineerId", app_source)
        self.assertIn("isAdminPage", app_source)
        self.assertIn("/admin", app_source)
        self.assertNotIn("force assign", app_source.lower())

        # Admin schedule uses its own localStorage key, separate from /assignment shift
        self.assertIn("writeStorage(ASSIGNMENT_ADMIN_SCHEDULE_KEY, adminSchedule)", app_source)

        # CSS
        self.assertIn(".engineer-selector-grid", css)
        self.assertIn(".assignment-shell", css)
        self.assertIn(".assignment-sidebar", css)
        self.assertIn(".engineer-rail.assignment-sidebar", css)
        self.assertIn(".rail-brand", css)
        rail_brand_match = re.search(r"\.rail-brand \{(?P<body>.*?)\n\}", css, re.S)
        self.assertIsNotNone(rail_brand_match)
        rail_brand_css = rail_brand_match.group("body") if rail_brand_match else ""
        self.assertIn("display: flex;", rail_brand_css)
        self.assertIn("align-items: center;", rail_brand_css)
        self.assertIn("justify-content: center;", rail_brand_css)
        self.assertIn("gap: 0;", rail_brand_css)
        self.assertIn("width: 68px;", rail_brand_css)
        self.assertIn("min-width: 0;", rail_brand_css)
        self.assertRegex(
            css,
            r"\.engineer-rail\.assignment-sidebar:hover \.rail-brand,\s*"
            r"\.engineer-rail\.assignment-sidebar:focus-within \.rail-brand\s*\{[^}]*"
            r"justify-content:\s*flex-start;[^}]*gap:\s*16px;[^}]*width:\s*100%;",
        )
        self.assertIn(".panel-card", css)
        self.assertIn(".btn-primary", css)
        self.assertIn(".detail-investigation-draft", css)
        self.assertIn(".assignment-shell.is-sidebar-collapsed", css)
        self.assertIn(".ready-loading-view", css)
        self.assertIn(".ready-loading-card", css)
        self.assertIn(".ready-loading-spinner", css)
        self.assertIn(".ready-loading-bar", css)
        self.assertIn(".ready-loading-bar-fill", css)
        self.assertIn(".break-after-case-btn", css)
        self.assertIn(".break-after-case-btn.is-active", css)
        self.assertIn(".rail-compact-stack {\n  position: absolute;", css)
        self.assertIn(".engineer-rail.assignment-sidebar:hover .rail-compact-stack", css)
        self.assertIn("visibility: hidden;", css)
        self.assertIn(".problem-workspace", css)
        self.assertIn(".investigation-panel", css)
        self.assertIn(".reply-panel", css)
        self.assertIn(".current-ticket-sla", css)
        self.assertIn("scrollbar-width: none;", css)
        self.assertIn(".sidebar-inner::-webkit-scrollbar", css)
        self.assertIn("display: none;", css)
        self.assertIn(".welcome-view", css)
        self.assertIn(".welcome-hero", css)
        self.assertIn(".welcome-grid", css)
        self.assertIn(".welcome-info-card", css)
        self.assertIn(".welcome-actions", css)
        self.assertIn(".btn-ready", css)
        self.assertIn(".admin-shell", css)
        self.assertIn(".admin-topbar", css)
        self.assertIn(".admin-metric-grid", css)
        self.assertIn(".admin-bottom-grid", css)
        self.assertIn(".admin-engineer-overview", css)
        self.assertIn(".admin-schedule-grid", css)
        self.assertIn(".schedule-chip", css)
        self.assertIn(".admin-edit-target-form", css)
        self.assertNotIn(".admin-shift-picker", css)
        self.assertIn(".admin-edit-panel", css)
        self.assertIn(".admin-edit-form", css)
        self.assertIn(".admin-engineer-pill", css)
        self.assertIn(".schedule-cell", css)
        self.assertIn(".has-edit-panel", css)
        self.assertIn("font-size: clamp(24px, 3vw, 30px);", css)
        self.assertIn("font-size: 18px;", css)
        self.assertIn("font-size: 14px;", css)
        schedule_scroll_match = re.search(r"\.admin-schedule-grid-scroll \{(?P<body>.*?)\n\}", css, re.S)
        self.assertIsNotNone(schedule_scroll_match)
        schedule_scroll_css = schedule_scroll_match.group("body") if schedule_scroll_match else ""
        self.assertIn("width: 100%;", schedule_scroll_css)
        self.assertIn("max-width: 100%;", schedule_scroll_css)
        self.assertIn("min-width: 0;", css)
        self.assertNotIn("font-size: clamp(32px, 4vw, 48px);", css)
        self.assertNotIn("font-size: clamp(30px, 4vw, 46px);", css)
        self.assertNotIn("font-size: 17px;", css)

    def test_assignment_ui_does_not_modify_existing_engineer_ui_contract(self) -> None:
        engineer_html = Path("ui/engineer-ui/index.html").read_text(encoding="utf-8")
        engineer_app = Path("ui/engineer-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("Engineer Sign In", engineer_html)
        self.assertIn('const ENGINEER_ID = "Jack";', engineer_app)
        self.assertNotIn("ASSIGNMENT_SHIFT_KEY", engineer_app)


if __name__ == "__main__":
    unittest.main()
