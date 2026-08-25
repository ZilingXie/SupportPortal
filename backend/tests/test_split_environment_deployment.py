from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SplitEnvironmentDeploymentTest(unittest.TestCase):
    def test_compose_declares_profile_services_and_pointers(self):
        compose = (ROOT / "deployment/docker-compose.single-host.yml").read_text()
        for service in (
            "route_staging",
            "route_preproduction",
            "route_production",
            "automation_staging",
            "automation_preproduction",
            "automation_production",
            "automation_production_worker",
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

    def test_production_blue_green_contract(self):
        nginx = (ROOT / "deployment/nginx/supportportal.conf").read_text()
        script = (ROOT / "deployment/deploy_automation_production_blue_green.sh").read_text()
        runtime = (ROOT / "deployment/nginx/runtime/automation_production_active.conf").read_text()
        compose = (ROOT / "deployment/docker-compose.single-host.yml").read_text()
        self.assertIn("include /etc/nginx/runtime/automation_production_active.conf", nginx)
        self.assertIn("proxy_pass http://$automation_production_active", nginx)
        self.assertIn("proxy_next_upstream off", nginx)
        self.assertIn("set $automation_production_active automation_production:8000", runtime)
        self.assertIn("./nginx/runtime:/etc/nginx/runtime:ro", compose)
        for marker in (
            "extends:",
            "route_production_candidate_",
            "automation_production_candidate_",
            "AUTOMATION_REDIS_URL: redis://automation_redis_production:6379/0",
            "refusing to create a second Redis",
            "RELEASE_DIR/${RELEASE}.env",
            "docker image inspect",
            "release_from_override",
            "derived_previous_release",
            "load_production_resource_identity",
            "flock -n 9",
            "nginx -t",
            "nginx -s reload",
            "nginx reload failed; restoring the previous upstream pointer",
            "ensure_nginx_runtime_mount",
            "--no-deps nginx",
            "DRAIN_SECONDS",
            "previous_override",
            "previous_release=%s",
            "resolve_env_value NGINX_HOST_PORT",
            "active upstream state is incomplete",
            "previous pointer was restored",
            "previous upstream was restored",
            "candidate was left running for immediate inspection",
            "--rollback",
            "no request was replayed",
            "resolve_app_runtime_image",
            "Resolved APP_RUNTIME_IMAGE from the official api container",
            "does not match release commit",
            "up -d --no-build --no-deps automation_production_worker",
            "bootstrap_automation_production_schema.sh",
            "wait_for_worker_stable",
            ".RestartCount",
        ):
            self.assertIn(marker, script)
        self.assertLess(script.index('bash "$BOOTSTRAP_SCRIPT"'), script.index('log "Starting candidate project'))
        self.assertLess(script.index("if ! wait_for_worker_stable; then"), script.index("old_target=\"$(awk"))

    def test_automation_production_worker_contract(self):
        compose = (ROOT / "deployment/docker-compose.single-host.yml").read_text()
        env_example = (ROOT / ".env.example").read_text()
        # Full app image: the production automation image physically excludes
        # backend.worker and the reply-job services.
        self.assertIn("image: ${APP_RUNTIME_IMAGE:-localhost/supportportal-app:unknown}", compose)
        self.assertIn("command:\n      - python\n      - -m\n      - backend.worker", compose)
        worker_block = compose.split("automation_production_worker:", 1)[1].split("\n  automation_redis_staging:", 1)[0]
        # Bound to the split production schema, never the legacy one.
        self.assertIn("TICKET_DB_DSN: ${AUTOMATION_PRODUCTION_DB_DSN:-${PRODUCTION_TICKET_DB_DSN:-}}", worker_block)
        self.assertIn("TICKET_DB_SCHEMA: ${AUTOMATION_PRODUCTION_DB_SCHEMA:-supportportal_production}", worker_block)
        self.assertIn("TICKET_DB_APPLICATION_NAME: supportportal-automation-production-worker", worker_block)
        self.assertIn("PGVECTOR_DSN: ${PGVECTOR_DSN:?PGVECTOR_DSN is required}", worker_block)
        # Fail fast when the bootstrap script has not provisioned the schema.
        self.assertIn("RUNTIME_SCHEMA_MODE: check", worker_block)
        # Queue/event identity must be distinct from the legacy production
        # stack (support.ticket_queries.production / support.events.production).
        self.assertIn("TICKET_QUERY_QUEUE_NAME: support.ticket_queries.automation_production", worker_block)
        self.assertIn("TICKET_AUX_QUEUE_NAME: support.ticket_aux.automation_production", worker_block)
        self.assertIn("EVENT_BUS_CHANNEL: support.events.automation.production", worker_block)
        self.assertNotIn("support.ticket_queries.production\n", worker_block)
        # Third mailbox namespace: the empty namespace belongs to the legacy
        # production stack only.
        self.assertIn('INTERNAL_EMAIL_SUBJECT_NAMESPACE: "[automation]"', worker_block)
        # Reply pollers on; internal-email consumption stays off by default.
        self.assertIn('ACCOUNT_REPLY_POLLER_ENABLED: "true"', worker_block)
        self.assertIn("AUTOMATION_REPLY_POLL_ENABLED: ${AUTOMATION_PRODUCTION_REPLY_POLL_ENABLED:-false}", worker_block)
        self.assertIn("AUTOMATION_PRODUCTION_REPLY_POLL_ENABLED=false", env_example)
        self.assertIn("AUTOMATION_PRODUCTION_RAG_SERVICE_URL=", env_example)
        # Graph token cache mount, shared with the legacy aux worker.
        self.assertIn("- ../.msgraph:/app/.msgraph", worker_block)
        # Egress-capable split network only.
        self.assertIn("networks: [automation_internal_production]", worker_block)

    def test_split_verifier_resolves_blue_green_candidate_and_worker(self):
        verify = (ROOT / "deployment/verify_split_environments.sh").read_text()
        self.assertIn("ACTIVE_PRODUCTION_FILE", verify)
        self.assertIn('$automation_production_active', verify)
        self.assertIn('label=com.docker.compose.service=${service_name}', verify)
        self.assertIn("label=com.docker.compose.service=automation_production_worker", verify)
        self.assertIn(".RestartCount", verify)
        self.assertIn("VERIFY_WORKER_STABILITY_SECONDS", verify)
        self.assertIn('worker_state_before" == "$worker_state_after', verify)
        self.assertNotIn('grep "supportportal-automation-${1}-automation_${1}-1"', verify)

    def test_production_schema_bootstrap_script_contract(self):
        script_path = ROOT / "deployment/bootstrap_automation_production_schema.sh"
        self.assertTrue(script_path.exists())
        script = script_path.read_text()
        # DDL runs through the migration role on the split production schema.
        self.assertIn("TICKET_DB_MIGRATION_DSN is required", script)
        self.assertIn('schema="${schema:-supportportal_production}"', script)
        self.assertIn("TICKET_DB_MIGRATION_DSN", script)
        self.assertIn("runtime_bootstrap", script)
        self.assertIn("--profile bootstrap", script)
        # Fail-closed guards: never target the staging main database and never
        # run DDL against a different database than the runtime DSN.
        self.assertIn("production DB DSN must differ from TICKET_DB_DSN", script)
        self.assertIn("must target the same database", script)
        # Deploy integration: the production split deploy bootstraps before up.
        deploy = (ROOT / "deployment/deploy_ec2.sh").read_text()
        self.assertIn("bootstrap_automation_production_schema.sh", deploy)
        self.assertIn("APP_RUNTIME_IMAGE is required for automation_production_worker", deploy)
        self.assertIn("SPLIT_SERVICES=(route_production automation_production automation_production_worker)", deploy)
        # Workers have no HTTP health port; deploy readiness accepts a running
        # container for *_worker services.
        self.assertIn("*_worker", deploy)

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
            "/app/backend/services/rag_reset.py",
        ):
            self.assertIn(module, production_block)
        # Parity pipeline dependencies stay in the production bundle (p2-109):
        # the runtime imports them for the old-stack intake semantics.
        for module in (
            "/app/backend/services/account_reply_jobs.py",
            "/app/backend/services/account_automation_delivery.py",
            "/app/backend/services/internal_email_payload.py",
            "/app/backend/services/account_failure_alerts.py",
            "/app/backend/services/account_ai_execution.py",
            "/app/backend/services/automation_persona.py",
        ):
            self.assertNotIn(module, production_block)
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

    def test_local_split_startup_script_contract(self):
        script = (ROOT / "scripts/workflow/start_local_split_environments.sh").read_text()
        # Builds the three image roles from the current working tree.
        self.assertIn("--build-arg \"AUTOMATION_IMAGE_ROLE=${role}\"", script)
        self.assertIn("build_role route", script)
        self.assertIn("build_role automation", script)
        self.assertIn("build_role production", script)
        self.assertIn("--skip-build", script)
        # Uncommitted changes must be visible in the image tag.
        self.assertIn("-wip", script)
        # Idempotent networks and auto-generated execution tokens.
        self.assertIn("create_network_if_missing", script)
        self.assertIn("ensure_token n8n_request_token", script)
        # One compose project per environment, mirroring the EC2 shape.
        self.assertIn("supportportal-automation-${environment}", script)
        self.assertIn("--profile automation", script)
        # Production environment is conditional on the production DSN.
        self.assertIn("PRODUCTION_TICKET_DB_DSN", script)
        # Post-start verification covers health and the auth negative case
        # through the dedicated local nginx.
        self.assertIn('base="${LOCAL_NGINX_BASE}/automation/${environment}"', script)
        self.assertIn('expect_http "${environment} /health" 200 "${base}/health"', script)
        self.assertIn("unauthenticated POST is rejected", script)
        # A dedicated nginx is required because the official config hardcodes
        # Docker's embedded DNS resolver, which podman does not provide.
        self.assertIn("127.0.0.11", script)
        self.assertIn("supportportal-automation-nginx", script)
        self.assertIn("18080", script)
        # Local runs must not silently write Zendesk.
        self.assertIn("side effects are disabled by default", script)


if __name__ == "__main__":
    unittest.main()
