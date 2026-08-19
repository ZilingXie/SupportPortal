from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class ProductionUiContractTests(unittest.TestCase):
    def test_production_mount_and_assets_exist(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('PRODUCTION_DIR = UI_DIR / "production-ui"', main_source)
        self.assertIn(
            'app.mount("/production", StaticFiles(directory=PRODUCTION_DIR, html=True), name="production-ui")',
            main_source,
        )

        expected_files = [
            Path("ui/production-ui/index.html"),
            Path("ui/production-ui/styles.css"),
            Path("ui/production-ui/app.js"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_production_html_uses_client_shared_assets(self) -> None:
        html = Path("ui/production-ui/index.html").read_text(encoding="utf-8")

        self.assertIn("<title>Account Production</title>", html)
        self.assertIn("/shared-ui/composer.css", html)
        self.assertIn("/shared-ui/composer.js", html)
        self.assertIn("./styles.css", html)
        self.assertIn("./app.js", html)
        self.assertIn("20260819-automated-public-1", html)

    def test_production_app_prefixes_api_calls_with_production_base(self) -> None:
        app_source = Path("ui/production-ui/app.js").read_text(encoding="utf-8")

        self.assertIn('const PRODUCTION_API_BASE = "/production";', app_source)
        self.assertIn("function withProductionApiBase(url)", app_source)
        self.assertIn(
            "if (url === \"/account\" || url.startsWith(\"/api/\")) return `${PRODUCTION_API_BASE}${url}`;",
            app_source,
        )
        self.assertIn(
            "fetch(withProductionApiBase(url), authRequestInit(requestOptions))",
            app_source,
        )
        self.assertIn(
            'fetch(withProductionApiBase("/api/workspace/me")',
            app_source,
        )
        self.assertIn(
            'fetch(withProductionApiBase("/api/workspace/auth/login")',
            app_source,
        )

    def test_production_app_excludes_run_in_production_controls(self) -> None:
        app_source = Path("ui/production-ui/app.js").read_text(encoding="utf-8")
        styles = Path("ui/production-ui/styles.css").read_text(encoding="utf-8")

        forbidden_markers = [
            "promote-production",
            "renderProductionPromotionAction",
            "promoteAccountCaseToProduction",
            "PRODUCTION_PROMOTION_TIMEOUT_MS",
            "productionPromotion",
            "Run in Production",
        ]
        for marker in forbidden_markers:
            self.assertNotIn(marker, app_source, marker)
            self.assertNotIn(marker, styles, marker)
        self.assertNotIn("production-promotion", styles)

    def test_production_app_keeps_delayed_reply_copy(self) -> None:
        app_source = Path("ui/production-ui/app.js").read_text(encoding="utf-8")

        self.assertIn("AI reply scheduled", app_source)
        self.assertIn("standard 6-10 minute delay", app_source)

    def test_production_app_javascript_syntax(self) -> None:
        result = subprocess.run(
            ["node", "--check", "ui/production-ui/app.js"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backend_default_profile_wiring_keeps_staging_default(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")

        self.assertIn('os.getenv("ACCOUNT_DEFAULT_PROCESSING_PROFILE")', main_source)
        self.assertIn("def _default_account_processing_profile()", main_source)
        self.assertIn(
            "def _account_intake_zendesk_ticket_id(request: AccountIntakeRequest)",
            main_source,
        )
        self.assertIn(
            'processing_profile: str | None = Query(default=None, pattern="^(staging|production)$")',
            main_source,
        )
        self.assertIn(
            "processing_profile or _default_account_processing_profile()",
            main_source,
        )
        # The staging intake endpoint must stay on the env-driven default and
        # must not hard-code either profile.
        intake_start = main_source.index('async def create_account_intake(')
        intake_end = main_source.index("\nasync def ", intake_start + 10)
        intake_body = main_source[intake_start:intake_end]
        self.assertIn("_default_account_processing_profile()", intake_body)
        self.assertIn("_account_intake_zendesk_ticket_id(request)", intake_body)


class ProductionDeploymentContractTests(unittest.TestCase):
    def test_production_services_are_profile_gated_in_compose(self) -> None:
        compose = Path("deployment/docker-compose.single-host.yml").read_text(encoding="utf-8")

        for service in ("api_production", "worker_query_production", "worker_aux_production"):
            self.assertIn(f"  {service}:", compose, service)
        self.assertIn("- production\n", compose)
        self.assertIn("TICKET_DB_DSN: ${PRODUCTION_TICKET_DB_DSN:-}", compose)
        self.assertIn("ACCOUNT_DEFAULT_PROCESSING_PROFILE: production", compose)
        self.assertIn("TICKET_QUERY_QUEUE_NAME: support.ticket_queries.production", compose)
        self.assertIn("TICKET_AUX_QUEUE_NAME: support.ticket_aux.production", compose)
        self.assertIn("EVENT_BUS_CHANNEL: support.events.production", compose)
        self.assertIn("TICKET_DB_APPLICATION_NAME: supportportal-api-production", compose)
        # The staging stack keeps its required-DSN contract untouched.
        self.assertIn("TICKET_DB_DSN: ${TICKET_DB_DSN:?TICKET_DB_DSN is required}", compose)

    def test_nginx_routes_production_paths_to_production_api(self) -> None:
        nginx = Path("deployment/nginx/supportportal.conf").read_text(encoding="utf-8")

        self.assertIn("resolver 127.0.0.11", nginx)
        self.assertIn("set $production_api api_production:8000;", nginx)
        self.assertIn("location = /production {", nginx)
        self.assertIn("location = /production/account {", nginx)
        self.assertIn("rewrite ^/production(/account)$ $1 break;", nginx)
        self.assertIn("location /production/api/ {", nginx)
        self.assertIn("rewrite ^/production(/api/.*)$ $1 break;", nginx)
        self.assertIn("location /production/ {", nginx)
        intake_block = nginx[
            nginx.index("location = /production/account {") :
            nginx.index("location /production/api/ {")
        ]
        self.assertIn("proxy_read_timeout 300s;", intake_block)

    def test_deploy_script_gates_production_profile_on_dsn(self) -> None:
        deploy = Path("deployment/deploy_ec2.sh").read_text(encoding="utf-8")

        self.assertIn("resolve_compose_profile_args", deploy)
        self.assertIn('production_dsn="$(resolve_env_value PRODUCTION_TICKET_DB_DSN)"', deploy)
        self.assertIn("PRODUCTION_TICKET_DB_DSN must differ from TICKET_DB_DSN", deploy)
        self.assertIn("COMPOSE_PROFILE_ARGS=(--profile production)", deploy)
        self.assertIn('"${COMPOSE_PROFILE_ARGS[@]}" up -d', deploy)
        self.assertIn('"${COMPOSE_PROFILE_ARGS[@]}" down', deploy)
        self.assertIn('"${COMPOSE_PROFILE_ARGS[@]}" build', deploy)
        self.assertIn('production_page_url="http://127.0.0.1:${host_port}/production/"', deploy)

    def test_deploy_script_syncs_prompt_release_to_production_database(self) -> None:
        deploy = Path("deployment/deploy_ec2.sh").read_text(encoding="utf-8")

        self.assertIn("sync_candidate_prompt_release_to_production", deploy)
        self.assertIn(
            'sync --release-id "${CANDIDATE_PROMPT_RELEASE_ID}" --target-dsn "${target_dsn}"',
            deploy,
        )
        self.assertIn("Prompt Release production sync failed", deploy)

    def test_env_example_documents_production_dsn(self) -> None:
        env_example = Path(".env.example").read_text(encoding="utf-8")

        self.assertIn("PRODUCTION_TICKET_DB_DSN=", env_example)
        self.assertIn("Must never equal TICKET_DB_DSN.", env_example)


if __name__ == "__main__":
    unittest.main()
