from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deployment/deploy_automation_production_blue_green.sh"
IMAGE_ID = "sha256:" + ("a" * 64)


class ProductionBlueGreenBehaviorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name) / "repo"
        (self.project / "deployment").mkdir(parents=True)
        (self.project / "deployment" / "nginx" / "runtime").mkdir(parents=True)
        (self.project / ".deployments" / "releases").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.project / "deployment" / SCRIPT.name)
        bootstrap = self.project / "deployment" / "bootstrap_automation_production_schema.sh"
        bootstrap.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'bootstrap schema\\n' >> \"$FAKE_DOCKER_LOG\"\n"
            "exit \"${FAKE_BOOTSTRAP_STATUS:-0}\"\n"
        )
        bootstrap.chmod(bootstrap.stat().st_mode | stat.S_IXUSR)
        (self.project / "deployment" / "docker-compose.single-host.yml").write_text("services: {}\n")
        (self.project / ".env").write_text(
            "TICKET_DB_DSN=postgresql://ticket\n"
            "PRODUCTION_TICKET_DB_DSN=postgresql://production\n"
            "APP_RUNTIME_IMAGE=localhost/supportportal-app:test\n"
            "NGINX_HOST_PORT=18080\n"
        )
        (self.project / "deployment" / "nginx" / "runtime" / "automation_production_active.conf").write_text(
            "set $automation_production_active automation_production:8000;\n"
        )
        self.bin = Path(self.tempdir.name) / "bin"
        self.bin.mkdir()
        self.docker_log = Path(self.tempdir.name) / "docker.log"
        self.reload_marker = Path(self.tempdir.name) / "reload.once"
        self.reload_count = Path(self.tempdir.name) / "reload.count"
        self._write_fake_commands()
        self._write_manifest("release-test-1")
        self._write_manifest("release-test-2")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_manifest(self, release: str) -> None:
        (self.project / ".deployments" / "releases" / f"{release}.env").write_text(
            f"release_id={release}\n"
            "commit=test-commit\n"
            "build_time=2026-08-24T00:00:00Z\n"
            f"ROUTE_PRODUCTION_IMAGE=localhost/supportportal-route:{release}\n"
            f"ROUTE_PRODUCTION_IMAGE_ID={IMAGE_ID}\n"
            f"AUTOMATION_PRODUCTION_IMAGE=localhost/supportportal-automation-production:{release}\n"
            f"AUTOMATION_PRODUCTION_IMAGE_ID={IMAGE_ID}\n"
        )

    def _write_fake_commands(self) -> None:
        docker = self.bin / "docker"
        docker.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
            "if [[ \"$1 $2\" == \"image inspect\" ]]; then printf '%s\\n' \"$FAKE_IMAGE_ID\"; exit 0; fi\n"
            "if [[ \"$1\" == inspect && \"$*\" == *'{{.Config.Image}}'* ]]; then printf '%s\\n' \"$FAKE_APP_IMAGE\"; exit 0; fi\n"
            "if [[ \"$1\" == inspect && \"$*\" == *'.Config.Env'* ]]; then printf 'APP_BUILD_REF=%s\\n' \"$FAKE_APP_BUILD_REF\"; exit 0; fi\n"
            "if [[ \"$1\" == inspect && \"$*\" == *'.State.Running'* ]]; then printf '%s\\n' \"${FAKE_WORKER_STATE:-true running 0}\"; exit 0; fi\n"
            "if [[ \"$1\" == inspect ]]; then printf 'mounted\\n'; exit 0; fi\n"
            "if [[ \"$1\" == compose ]]; then\n"
            "  [[ \"$*\" == *'ps -q nginx'* ]] && printf 'nginx\\n'\n"
            "  [[ \"$*\" == *'ps -q api'* ]] && printf 'api\\n'\n"
            "  [[ \"$*\" == *'ps -q automation_redis_production'* ]] && printf 'redis\\n'\n"
            "  [[ \"$*\" == *'ps -q automation_production_worker'* ]] && printf 'worker\\n'\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$1\" == exec && \"$*\" == *'nginx -s reload'* ]]; then\n"
            "  count=0; [[ -f \"$FAKE_RELOAD_COUNT\" ]] && count=$(<\"$FAKE_RELOAD_COUNT\"); count=$((count + 1)); printf '%s' \"$count\" > \"$FAKE_RELOAD_COUNT\"\n"
            "  [[ \"${FAKE_RELOAD_FAILURE_ON:-0}\" == \"$count\" ]] && exit 1\n"
            "fi\n"
            "exit 0\n"
        )
        curl = self.bin / "curl"
        curl.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_CURL_LOG\"\n"
            "exit \"${FAKE_CURL_STATUS:-0}\"\n"
        )
        flock = self.bin / "flock"
        flock.write_text("#!/usr/bin/env bash\nexit 0\n")
        for command in (docker, curl, flock):
            command.chmod(command.stat().st_mode | stat.S_IXUSR)

    def _run(self, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.bin}:{environment['PATH']}",
                "DEPLOY_PRODUCTION_APPROVED": "1",
                "DEPLOY_HEALTH_TIMEOUT_SECONDS": "1",
                "DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS": "0",
                "DEPLOY_WORKER_STABILITY_SECONDS": "0",
                "FAKE_DOCKER_LOG": str(self.docker_log),
                "FAKE_CURL_LOG": str(Path(self.tempdir.name) / "curl.log"),
                "FAKE_RELOAD_MARKER": str(self.reload_marker),
                "FAKE_RELOAD_COUNT": str(self.reload_count),
                "FAKE_IMAGE_ID": IMAGE_ID,
                "FAKE_APP_IMAGE": "localhost/supportportal-app:test",
                "FAKE_APP_BUILD_REF": "test-commit",
            }
        )
        environment.update(extra_env)
        return subprocess.run(
            [str(self.project / "deployment" / SCRIPT.name), *args],
            cwd=self.project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def _active_pointer(self) -> str:
        return (self.project / "deployment" / "nginx" / "runtime" / "automation_production_active.conf").read_text()

    def test_bootstrap_and_worker_gate_precede_nginx_cutover(self) -> None:
        result = self._run("--release", "release-test-1", "--drain-seconds", "0", "--skip-health")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.docker_log.read_text()
        self.assertLess(calls.index("bootstrap schema"), calls.index("route_production_candidate_release-test-1"))
        self.assertLess(calls.index("automation_production_worker"), calls.index("nginx -s reload"))
        self.assertIn("Split production worker remained stable", result.stdout)

    def test_bootstrap_failure_stops_before_candidate_and_cutover(self) -> None:
        result = self._run("--release", "release-test-1", "--skip-health", FAKE_BOOTSTRAP_STATUS="1")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.docker_log.read_text()
        self.assertIn("bootstrap schema", calls)
        self.assertNotIn("route_production_candidate_release-test-1", calls)
        self.assertNotIn("nginx -s reload", calls)
        self.assertFalse((self.project / ".deployments" / "automation-production-blue-green.manifest").exists())

    def test_restarting_worker_fails_before_cutover_and_stops_candidate(self) -> None:
        result = self._run(
            "--release",
            "release-test-1",
            "--skip-health",
            FAKE_WORKER_STATE="false restarting 3",
            DEPLOY_HEALTH_TIMEOUT_SECONDS="0",
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.docker_log.read_text()
        self.assertIn("worker did not remain stable", result.stdout + result.stderr)
        self.assertIn("stop route_production_candidate_release-test-1 automation_production_candidate_release-test-1", calls)
        self.assertNotIn("nginx -s reload", calls)
        self.assertIn("set $automation_production_active automation_production:8000;", self._active_pointer())

    def test_reload_failure_restores_pointer_and_stops_candidate(self) -> None:
        result = self._run("--release", "release-test-1", "--drain-seconds", "0", "--skip-health", FAKE_RELOAD_FAILURE_ON="1")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("nginx reload failed; restoring the previous upstream pointer", result.stdout)
        self.assertIn("set $automation_production_active automation_production:8000;", self._active_pointer())
        self.assertIn("stop route_production_candidate_release-test-1 automation_production_candidate_release-test-1", self.docker_log.read_text())

    def test_through_nginx_health_failure_restores_pointer(self) -> None:
        result = self._run("--release", "release-test-1", "--drain-seconds", "0", FAKE_CURL_STATUS="22")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("previous upstream was restored", result.stdout + result.stderr)
        self.assertIn("set $automation_production_active automation_production:8000;", self._active_pointer())

    def test_health_failure_keeps_candidate_if_restore_reload_fails(self) -> None:
        result = self._run(
            "--release",
            "release-test-1",
            "--drain-seconds",
            "0",
            FAKE_CURL_STATUS="22",
            FAKE_RELOAD_FAILURE_ON="2",
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("candidate was left running for immediate inspection", result.stdout + result.stderr)
        self.assertNotIn("stop route_production_candidate_release-test-1 automation_production_candidate_release-test-1", self.docker_log.read_text())
        self.assertIn("set $automation_production_active automation_production_candidate_release-test-1:8000;", self._active_pointer())

    def test_invalid_nginx_port_fails_before_candidate_start(self) -> None:
        result = self._run("--release", "release-test-1", NGINX_HOST_PORT="not-a-port")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NGINX_HOST_PORT must be numeric", result.stdout + result.stderr)
        self.assertNotIn("Starting candidate project", result.stdout)

    def test_resolves_app_image_from_running_api_when_env_key_is_missing(self) -> None:
        env_file = self.project / ".env"
        env_file.write_text(env_file.read_text().replace("APP_RUNTIME_IMAGE=localhost/supportportal-app:test\n", ""))

        result = self._run("--release", "release-test-1", "--drain-seconds", "0", "--skip-health")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Resolved APP_RUNTIME_IMAGE from the official api container", result.stdout)
        self.assertIn("automation_production_worker", self.docker_log.read_text())

    def test_rejects_running_api_from_another_commit(self) -> None:
        env_file = self.project / ".env"
        env_file.write_text(env_file.read_text().replace("APP_RUNTIME_IMAGE=localhost/supportportal-app:test\n", ""))

        result = self._run("--release", "release-test-1", "--skip-health", FAKE_APP_BUILD_REF="old-commit")

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("does not match release commit", result.stdout + result.stderr)
        self.assertNotIn("Starting candidate project", result.stdout)

    def test_new_process_rollback_reloads_previous_release_manifest(self) -> None:
        first = self._run("--release", "release-test-1", "--drain-seconds", "0", "--skip-health")
        second = self._run("--release", "release-test-2", "--drain-seconds", "0", "--skip-health")
        rollback = self._run("--rollback", "--skip-health")

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(rollback.returncode, 0, rollback.stdout + rollback.stderr)
        self.assertIn("Loaded release manifest", rollback.stdout)
        self.assertIn("release-test-1.env", rollback.stdout)
        self.assertIn("supportportal-automation-production-bg-release-test-1", self.docker_log.read_text())
        self.assertIn("set $automation_production_active automation_production_candidate_release-test-1:8000;", self._active_pointer())
        state = (self.project / ".deployments" / "automation-production-blue-green.manifest").read_text()
        self.assertIn("release=release-test-1", state)
        self.assertIn("previous_release=release-test-2", state)
