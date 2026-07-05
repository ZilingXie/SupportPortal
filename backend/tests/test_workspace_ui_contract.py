from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class WorkspaceUiContractTests(unittest.TestCase):
    def run_workspace_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            (async () => {{
            const fs = require("fs");
            const vm = require("vm");
            const userScript = {script!r};
            const sharedComposerPath = "ui/shared-ui/composer.js";

            const source = fs.readFileSync("ui/workspace-ui/app.js", "utf8");

            function createElementStub(id = "") {{
              return {{
                id,
                innerHTML: "",
                textContent: "",
                value: "",
                dataset: {{}},
                disabled: false,
                hidden: false,
                classList: {{
                  add() {{}},
                  remove() {{}},
                  toggle() {{}},
                  contains() {{ return false; }},
                }},
                addEventListener() {{}},
                removeEventListener() {{}},
                querySelector() {{ return null; }},
                querySelectorAll() {{ return []; }},
                closest() {{ return null; }},
                focus() {{}},
                scrollIntoView() {{}},
                setSelectionRange() {{}},
              }};
            }}

            const elements = new Map();
            const storage = new Map();
            const fetchCalls = [];
            const fetchResponses = [];
            const sandbox = {{
              console,
              URL,
              FormData: function FormData() {{
                return {{ get() {{ return ""; }} }};
              }},
              window: {{
                location: {{
                  hash: "",
                  protocol: "http:",
                  host: "localhost:8080",
                  assign() {{}},
                  reload() {{}},
                }},
                addEventListener() {{}},
                alert(message) {{
                  throw new Error(message);
                }},
                __fetchCalls: fetchCalls,
                __fetchResponses: fetchResponses,
              }},
              document: {{
                getElementById(id) {{
                  if (!elements.has(id)) elements.set(id, createElementStub(id));
                  return elements.get(id);
                }},
                addEventListener() {{}},
                querySelector() {{ return null; }},
                querySelectorAll() {{ return []; }},
              }},
              localStorage: {{
                getItem(key) {{
                  return storage.has(key) ? storage.get(key) : null;
                }},
                setItem(key, value) {{
                  storage.set(key, String(value));
                }},
                removeItem(key) {{
                  storage.delete(key);
                }},
              }},
              fetch: async (url, options = {{}}) => {{
                fetchCalls.push({{ url: String(url), options }});
                const next = fetchResponses.length ? fetchResponses.shift() : {{ tickets: [] }};
                return {{
                  ok: true,
                  json: async () => next,
                }};
              }},
              WebSocket: function WebSocket() {{
                this.readyState = 1;
                this.close = () => {{}};
                this.send = () => {{}};
              }},
              HTMLTextAreaElement: function HTMLTextAreaElement() {{}},
              setTimeout(callback) {{
                if (typeof callback === "function") callback();
                return 0;
              }},
              clearTimeout() {{}},
              setInterval() {{
                return 0;
              }},
              clearInterval() {{}},
            }};

            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            if (fs.existsSync(sharedComposerPath)) {{
              const sharedSource = fs.readFileSync(sharedComposerPath, "utf8");
              vm.runInContext(sharedSource, sandbox);
            }}
            vm.runInContext(source, sandbox);
            await vm.runInContext(`(async () => {{\\n${{userScript}}\\n}})()`, sandbox);
            }})().catch((error) => {{
              console.error(error);
              process.exit(1);
            }});
            """
        )
        result = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_workspace_ui_is_served_as_independent_static_page(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('WORKSPACE_DIR = UI_DIR / "workspace-ui"', main_source)
        self.assertIn(
            'app.mount("/workspace", StaticFiles(directory=WORKSPACE_DIR, html=True), name="workspace-ui")',
            main_source,
        )
        self.assertIn('app.mount("/engineer", StaticFiles(directory=ENGINEER_DIR, html=True), name="engineer-ui")', main_source)
        self.assertIn('app.mount("/assignment", StaticFiles(directory=ASSIGNMENT_DIR, html=True), name="assignment-ui")', main_source)

        for path in (
            Path("ui/workspace-ui/index.html"),
            Path("ui/workspace-ui/styles.css"),
            Path("ui/workspace-ui/app.js"),
        ):
            self.assertTrue(path.exists(), f"{path} should exist")

    def test_workspace_ui_combines_assignment_entry_with_real_engineer_flow(self) -> None:
        html = Path("ui/workspace-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("SupportPortal Workspace", html)
        self.assertIn("Choose a demo engineer", html)
        self.assertIn("I'm ready to roll", app_source)
        self.assertIn("supportportal_workspace_selected_engineer", app_source)
        self.assertIn("supportportal_workspace_daily_shift", app_source)
        self.assertIn("supportportal_workspace_active", app_source)
        self.assertNotIn("ASSIGNMENT_AUTH_KEY", app_source)
        self.assertNotIn("ASSIGNMENT_WORKSPACE_KEY", app_source)
        self.assertNotIn('const ENGINEER_ID = "Jack";', app_source)
        self.assertIn("/api/engineer/tickets?status=investigating", app_source)
        self.assertIn("findNextInvestigatingCase", app_source)
        self.assertIn("No investigating cases available", app_source)
        self.assertIn("assignment-sidebar", css)
        self.assertIn("problem-workspace", css)

    def test_workspace_case_shell_keeps_assignment_sidebar_and_case_controls(self) -> None:
        app_source = Path("ui/workspace-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("supportportal_workspace_break_after_case", app_source)
        self.assertIn("renderWorkspaceAssignmentSidebarHtml", app_source)
        self.assertIn("renderWorkspaceCaseControlsHtml", app_source)
        self.assertIn("Concierge AI", app_source)
        self.assertIn("Assignment Command", app_source)
        self.assertIn("Engineer context", app_source)
        self.assertIn("UTC+8 daily shift", app_source)
        self.assertIn("Break after this case", app_source)
        self.assertIn("data-action=\"toggle-break-after-case\"", app_source)
        self.assertIn("current-ticket-sla", app_source)
        self.assertIn("workspace-assignment-sidebar", css)
        self.assertIn("workspace-case-controls", css)
        self.assertIn("break-after-case-btn", css)

    def test_workspace_assignment_sidebar_collapses_until_hover(self) -> None:
        css = Path("ui/workspace-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("--workspace-sidebar-collapsed-width: 96px", css)
        self.assertIn("--workspace-sidebar-expanded-width: 320px", css)
        self.assertIn("width: var(--workspace-sidebar-collapsed-width)", css)
        self.assertIn("margin-left: var(--workspace-sidebar-collapsed-width)", css)
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.sidebar-inner\s*\{.*grid-template-columns: minmax\(0, 1fr\);.*min-width: 0;.*max-width: 100%",
        )
        self.assertIn(".workspace-assignment-sidebar .rail-brand-icon", css)
        self.assertIn("flex: 0 0 54px", css)
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.rail-brand\s*\{.*min-width: 0;.*max-width: 100%",
        )
        self.assertIn(".workspace-assignment-sidebar:hover,\n.workspace-assignment-sidebar:focus-within", css)
        self.assertIn("width: var(--workspace-sidebar-expanded-width)", css)
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.rail-compact-stack\s*\{.*visibility: visible",
        )
        self.assertIn(".workspace-assignment-sidebar .rail-compact-status", css)
        self.assertIn(".workspace-assignment-sidebar .rail-compact-avatar", css)
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar \.sidebar-inner > :not\(\.rail-brand\):not\(\.rail-compact-stack\)\s*\{.*opacity: 0;.*pointer-events: none",
        )
        self.assertRegex(
            css,
            r"(?s)\.workspace-assignment-sidebar:hover \.sidebar-inner > :not\(\.rail-brand\):not\(\.rail-compact-stack\),\s*\.workspace-assignment-sidebar:focus-within \.sidebar-inner > :not\(\.rail-brand\):not\(\.rail-compact-stack\)\s*\{.*opacity: 1;.*pointer-events: auto",
        )

    def test_workspace_ready_opens_only_investigating_case(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(false));
            window.__fetchResponses.push({
              tickets: [
                { ticket_id: "TK-ESC-1", status: "escalated" },
                { ticket_id: "TK-INV-1", engineer_case_id: "TK-INV-1", status: "investigating" },
              ],
            });
            await readyToRoll();
            if (!window.__fetchCalls.some((call) => call.url === "/api/engineer/tickets?status=investigating")) {
              throw new Error("ready flow did not request investigating tickets");
            }
            if (window.location.hash !== "#/tickets/TK-INV-1") {
              throw new Error(`expected investigating case hash, got ${window.location.hash}`);
            }
            """
        )

    def test_workspace_ready_does_not_open_non_investigating_cases(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(false));
            window.__fetchResponses.push({
              tickets: [
                { ticket_id: "TK-ESC-1", status: "escalated" },
                { ticket_id: "TK-COMM-1", status: "communicating" },
                { ticket_id: "TK-RES-1", status: "resolved" },
              ],
            });
            await readyToRoll();
            if (window.location.hash.includes("TK-")) {
              throw new Error(`non-investigating case should not be opened: ${window.location.hash}`);
            }
            const root = document.getElementById("workspace-root");
            if (!root.innerHTML.includes("No investigating cases available")) {
              throw new Error("missing no-investigating empty state");
            }
            """
        )

    def test_workspace_root_does_not_fallback_to_engineer_ticket_pool(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            localStorage.setItem("supportportal_workspace_active", JSON.stringify(true));
            window.location.hash = "";

            await enterBoard();

            if (window.__fetchCalls.some((call) => call.url === "/api/engineer/tickets?status=all")) {
              throw new Error("workspace root should not load the engineer ticket pool");
            }
            const root = document.getElementById("workspace-root");
            if (!root.innerHTML.includes("I'm ready to roll")) {
              throw new Error("workspace root should return to readiness instead of pool");
            }
            const workspace = document.getElementById("workspace-region");
            if (workspace.innerHTML.includes("Engineer queue metrics")) {
              throw new Error("workspace root rendered the engineer pool fallback");
            }
            """
        )

    def test_workspace_mutation_payloads_use_selected_engineer_id(self) -> None:
        self.run_workspace_app_script(
            """
            localStorage.setItem("supportportal_workspace_selected_engineer", JSON.stringify("Maya"));
            await updateTicketStatus("TK-INV-1", "investigate");
            await runMultiAgentInvestigation("TK-INV-1");
            await submitInvestigationMessage("TK-INV-1", "Collected SDK logs.");
            await submitInvestigationConfirmation("TK-INV-1", "approve", "Looks grounded.");

            const bodies = window.__fetchCalls
              .filter((call) => call.options && call.options.body)
              .map((call) => JSON.parse(call.options.body));
            if (bodies.length !== 4) {
              throw new Error(`expected 4 mutation bodies, got ${bodies.length}`);
            }
            for (const body of bodies) {
              if (body.engineer_id !== "Maya") {
                throw new Error(`expected Maya engineer_id, got ${body.engineer_id}`);
              }
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
