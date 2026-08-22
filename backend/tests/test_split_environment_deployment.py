from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SplitEnvironmentDeploymentTest(unittest.TestCase):
    def test_compose_declares_six_profile_services_and_pointers(self):
        compose = (ROOT / "deployment/docker-compose.single-host.yml").read_text()
        for service in (
            "route_staging",
            "route_preproduction",
            "route_production",
            "automation_staging",
            "automation_preproduction",
            "automation_production",
        ):
            self.assertIn(f"  {service}:", compose)
            self.assertIn('profiles: ["automation"]', compose)
        for pointer in (
            "ROUTE_STAGING_IMAGE",
            "ROUTE_PREPRODUCTION_IMAGE",
            "ROUTE_PRODUCTION_IMAGE",
            "AUTOMATION_STAGING_IMAGE",
            "AUTOMATION_PREPRODUCTION_IMAGE",
            "AUTOMATION_PRODUCTION_IMAGE",
        ):
            self.assertIn(pointer, compose)
        for environment in ("staging", "preproduction", "production"):
            self.assertIn(f"AUTOMATION_DB_RESOURCE_ID: {environment}", compose)
        self.assertIn("supportportal_automation_edge", compose)
        self.assertIn("supportportal_automation_internal_staging", compose)
        self.assertIn("supportportal_automation_internal_preproduction", compose)
        self.assertIn("supportportal_automation_internal_production", compose)
        self.assertEqual(compose.count("external: true"), 4)

    def test_nginx_has_new_paths_and_deploy_script_has_environment_mode(self):
        nginx = (ROOT / "deployment/nginx/supportportal.conf").read_text()
        deploy = (ROOT / "deployment/deploy_ec2.sh").read_text()
        for path in ("/automation/staging/", "/automation/preproduction/", "/automation/production/"):
            self.assertIn(path, nginx)
        self.assertIn("--environment", deploy)
        self.assertIn("docker compose", deploy)
        self.assertIn("rollback scope is", deploy)
        self.assertIn("--project-name", deploy)
        self.assertIn("--rollback", deploy)
        self.assertIn("previous_route_image", deploy)
        self.assertIn("ensure_automation_networks", deploy)
        self.assertIn("ensure_nginx_automation_edge_network", deploy)
        self.assertIn('docker network connect "${network_name}" "${nginx_container_id}"', deploy)

    def test_production_bundle_has_no_rerun_ui_or_main_module(self):
        production_js = (ROOT / "ui/automation-production/app.js").read_text().lower()
        self.assertNotIn("rerun", production_js)
        dockerfile = (ROOT / "backend/Dockerfile.automation").read_text()
        self.assertIn("AUTOMATION_IMAGE_ROLE", dockerfile)
        self.assertIn("/app/backend/main.py", dockerfile)
        self.assertIn("account_full_reroute.py", dockerfile)
        self.assertIn("automation_production_runtime:app", (ROOT / "deployment/docker-compose.single-host.yml").read_text())
        self.assertIn("automation_rerun_contracts.py", dockerfile)
        production_block = dockerfile.split('if [ "${AUTOMATION_IMAGE_ROLE}" = "production" ]; then', 1)[1]
        for module in (
            "/app/backend/services/automation_rerun_contracts.py",
            "/app/backend/worker.py",
            "/app/backend/services/account_reply_jobs.py",
            "/app/backend/services/account_automation_delivery.py",
            "/app/backend/services/internal_email_payload.py",
            "/app/backend/services/account_ai_execution.py",
            "/app/backend/services/automation_persona.py",
            "/app/backend/services/rag_reset.py",
        ):
            self.assertIn(module, production_block)
        contracts = (ROOT / "backend/services/automation_contracts.py").read_text().lower()
        self.assertNotIn("allow_rerun", contracts)
        self.assertNotIn("allow_reset", contracts)

    def test_route_bundle_excludes_side_effect_and_legacy_runtime_modules(self):
        dockerfile = (ROOT / "backend/Dockerfile.automation").read_text()
        route_block = dockerfile.split('elif [ "${AUTOMATION_IMAGE_ROLE}" = "route" ]; then', 1)[1]
        for module in (
            "/app/backend/main.py",
            "/app/backend/services/automation_side_effects.py",
            "/app/backend/services/automation_delivery_reconciliation.py",
            "/app/backend/services/zendesk_comments.py",
            "/app/backend/services/automation_rerun_contracts.py",
        ):
            self.assertIn(module, route_block)


if __name__ == "__main__":
    unittest.main()
