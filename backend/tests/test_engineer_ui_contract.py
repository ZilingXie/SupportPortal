from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class EngineerUiContractTests(unittest.TestCase):
    def run_engineer_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");

            let source = fs.readFileSync("ui/engineer-ui/app.js", "utf8");
            source = source.replace(/\\nloginFormEl\\.addEventListener\\([\\s\\S]*$/, "\\n");

            function createElementStub() {{
              return {{
                innerHTML: "",
                textContent: "",
                value: "",
                dataset: {{}},
                disabled: false,
                addEventListener() {{}},
                removeEventListener() {{}},
                querySelector() {{ return null; }},
                querySelectorAll() {{ return []; }},
                closest() {{ return null; }},
                focus() {{}},
              }};
            }}

            const storage = new Map();
            const sandbox = {{
              console,
              URL,
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
              }},
              document: {{
                getElementById() {{
                  return createElementStub();
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
              fetch: async () => ({{
                ok: true,
                json: async () => ({{ tickets: [] }}),
              }}),
              WebSocket: function WebSocket() {{
                this.readyState = 1;
                this.close = () => {{}};
                this.send = () => {{}};
              }},
              setTimeout() {{
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
            vm.runInContext(source, sandbox);
            vm.runInContext({script!r}, sandbox);
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

    def test_engineer_ui_uses_stitch_workspace_language(self) -> None:
        html = Path("ui/engineer-ui/index.html").read_text(encoding="utf-8")
        app_source = Path("ui/engineer-ui/app.js").read_text(encoding="utf-8")
        css = Path("ui/engineer-ui/styles.css").read_text(encoding="utf-8")

        self.assertIn("Concierge AI", html)
        self.assertIn("Manrope", html)
        self.assertIn("./styles.css?v=20260324-engineer-stitch-6", html)
        self.assertIn('./app.js?v=20260324-engineer-stitch-6', html)
        self.assertIn("function parseRoute()", app_source)
        self.assertIn('path.startsWith("/tickets/")', app_source)
        self.assertIn("function renderTicketPoolView()", app_source)
        self.assertIn("function renderTicketDetailView()", app_source)
        self.assertIn("Next Action Needed", app_source)
        self.assertIn("AI Managing", app_source)
        self.assertIn('window.addEventListener("hashchange"', app_source)
        self.assertIn('class="rail-footer"', html)
        self.assertIn('class="rail-status-icon realtime-icon"', html)
        self.assertNotIn('class="engineer-topbar-meta"', html)
        self.assertNotIn('class="engineer-topbar"', html)
        self.assertNotIn("Active Pool", app_source)
        self.assertNotIn("ticket-pool-hero", app_source)
        self.assertNotIn('id="detail-modal"', html)
        self.assertNotIn("detailModalEl", app_source)
        self.assertNotIn("boardScreenEl", app_source)
        self.assertNotIn("ticketTableBodyEl", app_source)
        self.assertNotIn("detailBodyEl", app_source)
        self.assertNotIn("Noto Sans SC", css)
        self.assertIn("--surface:", css)
        self.assertIn("--primary:", css)
        self.assertIn(".engineer-shell", css)
        self.assertIn(".rail-footer", css)
        self.assertIn(".rail-footer {\n  display: grid;\n  gap: 12px;\n  justify-items: center;", css)
        self.assertIn(".engineer-rail:hover .rail-footer {\n  justify-items: stretch;", css)
        self.assertIn(".rail-status-card {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 52px;", css)
        self.assertIn("  height: 52px;", css)
        self.assertIn("  gap: 0;", css)
        self.assertIn("  border-radius: 18px;", css)
        self.assertIn(".engineer-rail:hover .rail-status-card {\n  width: 100%;\n  justify-content: flex-start;\n  gap: 12px;", css)
        self.assertIn(".realtime-icon {\n  width: 20px;\n  height: 20px;", css)
        self.assertIn(".engineer-rail .user-profile-chip {\n  display: none;", css)
        self.assertIn(".engineer-rail:hover .user-profile-chip {\n  display: flex;", css)
        self.assertIn(".ticket-pool-list", css)
        self.assertIn(".ticket-row", css)
        self.assertNotIn(".ticket-pool-grid", css)
        self.assertIn(".ticket-workspace", css)

    def test_engineer_ticket_pool_uses_list_rows_and_prioritizes_waiting_for_engineer(self) -> None:
        self.run_engineer_app_script(
            textwrap.dedent(
                """
                tickets = [
                  {
                    ticket_id: "TK-OPEN-URGENT",
                    subject: "open urgent ticket",
                    requester: "user-1",
                    priority: "urgent",
                    status: "open",
                    engineer_mode: "managed",
                    created_at: "2026-03-24T09:00:00+00:00",
                    updated_at: "2026-03-24T10:00:00+00:00",
                    pending_engineer_question: "",
                  },
                  {
                    ticket_id: "TK-WAITING-LOW",
                    subject: "waiting engineer ticket",
                    requester: "user-2",
                    priority: "low",
                    status: "waiting_for_engineer",
                    engineer_mode: "managed",
                    created_at: "2026-03-24T08:00:00+00:00",
                    updated_at: "2026-03-24T08:30:00+00:00",
                    pending_engineer_question: "Need engineer review.",
                  },
                ];

                filterValues.priority = "all";
                filterValues.mode = "all";
                filterValues.status = "all";

                const html = renderTicketPoolView();
                if (html.includes("ticket-card")) {{
                  throw new Error("Ticket pool should no longer render card layout.");
                }}
                if (!html.includes("ticket-pool-list")) {{
                  throw new Error("Ticket pool should render the list container.");
                }}
                if (!html.includes("ticket-row")) {{
                  throw new Error("Ticket pool should render list rows.");
                }}

                const waitingIndex = html.indexOf("TK-WAITING-LOW");
                const openIndex = html.indexOf("TK-OPEN-URGENT");
                if (waitingIndex === -1 || openIndex === -1) {{
                  throw new Error("Expected both sample tickets in rendered HTML.");
                }}
                if (waitingIndex > openIndex) {{
                  throw new Error("Waiting for engineer tickets should render ahead of normal open tickets.");
                }}
                """
            )
        )


if __name__ == "__main__":
    unittest.main()
