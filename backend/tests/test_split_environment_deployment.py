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

    def test_nginx_has_new_paths_and_deploy_script_has_environment_mode(self):
        nginx = (ROOT / "deployment/nginx/supportportal.conf").read_text()
        deploy = (ROOT / "deployment/deploy_ec2.sh").read_text()
        for path in ("/automation/staging/", "/automation/preproduction/", "/automation/production/"):
            self.assertIn(path, nginx)
        self.assertIn("--environment", deploy)
        self.assertIn("docker compose", deploy)
        self.assertIn("rollback scope is", deploy)

    def test_production_bundle_has_no_rerun_ui_or_main_module(self):
        production_js = (ROOT / "ui/automation-production/app.js").read_text().lower()
        self.assertNotIn("rerun", production_js)
        dockerfile = (ROOT / "backend/Dockerfile.automation").read_text()
        self.assertIn("AUTOMATION_IMAGE_ROLE", dockerfile)
        self.assertIn("/app/backend/main.py", dockerfile)
        self.assertIn("account_full_reroute.py", dockerfile)
        self.assertIn("automation_production_runtime:app", (ROOT / "deployment/docker-compose.single-host.yml").read_text())
        self.assertIn("automation_rerun_contracts.py", dockerfile)


if __name__ == "__main__":
    unittest.main()
