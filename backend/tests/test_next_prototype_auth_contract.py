from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class NextPrototypeAuthContractTests(unittest.TestCase):
    def run_auth_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");
            const userScript = {script!r};

            const constantsSource = fs.readFileSync("ui/client-ui/next-prototype/lib/constants.ts", "utf8");
            const demoUsersMatch = constantsSource.match(/export const DEMO_USERS = (\\[[\\s\\S]*?\\n\\])/);
            if (!demoUsersMatch) {{
              throw new Error("Could not find DEMO_USERS in constants.ts");
            }}

            let authSource = fs.readFileSync("ui/client-ui/next-prototype/lib/auth.ts", "utf8");
            authSource = authSource
              .replace('import {{ DEMO_USERS }} from "./constants"\\n\\n', "")
              .replace(/export interface User \\{{[\\s\\S]*?\\n\\}}\\n\\n/g, "")
              .replace(
                "function isValidStoredUser(value: unknown): value is User {{",
                "function isValidStoredUser(value) {{"
              )
              .replace(
                "export function login(username: string, password: string): User | null {{",
                "function login(username, password) {{"
              )
              .replace(
                "const userData: User = {{ id: user.id, name: user.name, email: user.email }}",
                "const userData = {{ id: user.id, name: user.name, email: user.email }}"
              )
              .replace("export function logout(): void {{", "function logout() {{")
              .replace("export function getCurrentUser(): User | null {{", "function getCurrentUser() {{")
              .replace(/ as User/g, "");
            authSource = `const DEMO_USERS = globalThis.DEMO_USERS;\\n${{authSource}}`;

            const storage = new Map();
            const sandbox = {{
              console,
              window: {{}},
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
            }};

            sandbox.globalThis = sandbox;
            vm.createContext(sandbox);
            vm.runInContext(`globalThis.DEMO_USERS = ${{demoUsersMatch[1]}};`, sandbox);
            vm.runInContext(authSource, sandbox);
            vm.runInContext(`(async () => {{\\n${{userScript}}\\n}})()`, sandbox);
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

    def test_next_prototype_brand_and_demo_identity_use_sid(self) -> None:
        layout_source = Path("ui/client-ui/next-prototype/app/layout.tsx").read_text(encoding="utf-8")
        login_source = Path("ui/client-ui/next-prototype/app/login/page.tsx").read_text(encoding="utf-8")
        sidebar_source = Path("ui/client-ui/next-prototype/components/chat-sidebar.tsx").read_text(encoding="utf-8")
        constants_source = Path("ui/client-ui/next-prototype/lib/constants.ts").read_text(encoding="utf-8")

        self.assertIn("Sid", layout_source)
        self.assertNotIn("IT HelpDesk", layout_source)
        self.assertIn("Sid", login_source)
        self.assertNotIn("IT HelpDesk", login_source)
        self.assertNotIn("IT Operations Support Portal", login_source)
        self.assertIn('placeholder="Zac"', login_source)
        self.assertIn("Username: Zac / Password: Zac", login_source)
        self.assertIn("Sid", sidebar_source)
        self.assertNotIn("IT HelpDesk", sidebar_source)
        self.assertNotIn("Support Portal", sidebar_source)
        self.assertIn('name: "Zac"', constants_source)
        self.assertIn('email: "zac@example.com"', constants_source)
        self.assertIn('password: "Zac"', constants_source)
        self.assertNotIn('name: "Admin"', constants_source)

    def test_next_prototype_auth_uses_zac_and_rejects_legacy_admin(self) -> None:
        self.run_auth_script(
            textwrap.dedent(
                """
                const success = login("Zac", "Zac");
                if (!success) {
                  throw new Error("Expected Zac / Zac to authenticate.");
                }
                if (success.name !== "Zac" || success.email !== "zac@example.com") {
                  throw new Error(`Expected Zac identity, got ${JSON.stringify(success)}.`);
                }
                if (login("admin", "admin") !== null) {
                  throw new Error("Legacy admin / admin credentials should no longer authenticate.");
                }

                const legacyUsers = [
                  { id: "user-1", name: "Admin", email: "admin" },
                  { id: "user-1", name: "Admin", email: "admin@example.com" },
                ];
                for (const legacyUser of legacyUsers) {
                  localStorage.setItem("helpdesk_auth_user", JSON.stringify(legacyUser));
                  const restored = getCurrentUser();
                  if (restored !== null) {
                    throw new Error(`Legacy Admin session should be rejected, got ${JSON.stringify(restored)}.`);
                  }
                  if (localStorage.getItem("helpdesk_auth_user") !== null) {
                    throw new Error("Legacy Admin session should be cleared from localStorage.");
                  }
                }
                """
            )
        )


if __name__ == "__main__":
    unittest.main()
