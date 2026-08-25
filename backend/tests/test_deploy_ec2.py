from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_SOURCE = REPO_ROOT / "deployment" / "deploy_ec2.sh"
BOOTSTRAP_SOURCE = REPO_ROOT / "deployment" / "bootstrap_automation_production_schema.sh"


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


class DeployEc2ScriptTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()

        _git(["init", "-b", "main"], cwd=self.repo)
        _git(["config", "user.name", "Deploy Tester"], cwd=self.repo)
        _git(["config", "user.email", "deploy@example.com"], cwd=self.repo)

        self._write(self.repo, "README.md", "deploy test\n")
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                NGINX_HOST_PORT=18080
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                PGVECTOR_DSN=postgresql://rag:test@db.local/rag
                """
            ),
        )
        self._write(self.repo, ".gitignore", ".env\n.deploy_ec2.lock\n")
        self._write(self.repo, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write_executable(
            self.repo / "deployment" / "deploy_ec2.sh",
            SCRIPT_SOURCE.read_text(encoding="utf-8"),
        )
        self._write_executable(
            self.repo / "deployment" / "bootstrap_automation_production_schema.sh",
            BOOTSTRAP_SOURCE.read_text(encoding="utf-8"),
        )
        _git(["add", "."], cwd=self.repo)
        _git(["commit", "-m", "Initial commit"], cwd=self.repo)

        self._install_fake_commands()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, root: Path, relative_path: str, content: str) -> None:
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def _write_executable(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _install_fake_commands(self) -> None:
        self._write_executable(
            self.fake_bin / "docker",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["DEPLOY_TEST_STATE_DIR"])
                args = sys.argv[1:]
                with (state_dir / "docker_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "argv": args,
                                "app_build_ref": os.environ.get("APP_BUILD_REF"),
                                "app_build_time": os.environ.get("APP_BUILD_TIME"),
                                "app_runtime_image": os.environ.get("APP_RUNTIME_IMAGE"),
                                "build_refs": {
                                    key: os.environ.get(key)
                                    for key in (
                                        "ROUTE_STAGING_BUILD_REF",
                                        "ROUTE_PREPRODUCTION_BUILD_REF",
                                        "ROUTE_PRODUCTION_BUILD_REF",
                                        "AUTOMATION_STAGING_BUILD_REF",
                                        "AUTOMATION_PREPRODUCTION_BUILD_REF",
                                        "AUTOMATION_PRODUCTION_BUILD_REF",
                                    )
                                },
                                "build_times": {
                                    key: os.environ.get(key)
                                    for key in (
                                        "ROUTE_STAGING_BUILD_TIME",
                                        "ROUTE_PREPRODUCTION_BUILD_TIME",
                                        "ROUTE_PRODUCTION_BUILD_TIME",
                                        "AUTOMATION_STAGING_BUILD_TIME",
                                        "AUTOMATION_PREPRODUCTION_BUILD_TIME",
                                        "AUTOMATION_PRODUCTION_BUILD_TIME",
                                    )
                                },
                                "automation_staging_db_dsn": os.environ.get("AUTOMATION_STAGING_DB_DSN"),
                                "automation_staging_db_schema": os.environ.get("AUTOMATION_STAGING_DB_SCHEMA"),
                                "automation_staging_db_table": os.environ.get("AUTOMATION_STAGING_DB_TABLE"),
                                "automation_staging_queue": os.environ.get("AUTOMATION_STAGING_QUEUE"),
                                "automation_staging_event_channel": os.environ.get("AUTOMATION_STAGING_EVENT_CHANNEL"),
                                "automation_preproduction_db_dsn": os.environ.get("AUTOMATION_PREPRODUCTION_DB_DSN"),
                                "automation_preproduction_db_schema": os.environ.get("AUTOMATION_PREPRODUCTION_DB_SCHEMA"),
                                "automation_preproduction_db_table": os.environ.get("AUTOMATION_PREPRODUCTION_DB_TABLE"),
                                "automation_preproduction_queue": os.environ.get("AUTOMATION_PREPRODUCTION_QUEUE"),
                                "automation_preproduction_event_channel": os.environ.get("AUTOMATION_PREPRODUCTION_EVENT_CHANNEL"),
                                "automation_production_db_dsn": os.environ.get("AUTOMATION_PRODUCTION_DB_DSN"),
                                "automation_production_db_migration_dsn": os.environ.get("AUTOMATION_PRODUCTION_DB_MIGRATION_DSN"),
                                "automation_production_db_schema": os.environ.get("AUTOMATION_PRODUCTION_DB_SCHEMA"),
                                "automation_production_db_table": os.environ.get("AUTOMATION_PRODUCTION_DB_TABLE"),
                                "automation_production_queue": os.environ.get("AUTOMATION_PRODUCTION_QUEUE"),
                                "automation_production_event_channel": os.environ.get("AUTOMATION_PRODUCTION_EVENT_CHANNEL"),
                                "ticket_db_migration_dsn": os.environ.get("TICKET_DB_MIGRATION_DSN"),
                                "prompt_release_id": os.environ.get("PROMPT_RELEASE_ID"),
                                "prompt_release_required": os.environ.get("PROMPT_RELEASE_REQUIRED"),
                            }
                        )
                        + "\\n"
                    )

                if args[:2] == ["builder", "prune"]:
                    print("builder prune ok")
                    sys.exit(0)

                if args[:2] == ["image", "prune"]:
                    print("image prune ok")
                    sys.exit(0)

                if args[:2] == ["image", "tag"]:
                    if args[2] == os.environ.get("FAKE_IMAGE_TAG_FAIL_SOURCE"):
                        print(f"No such image: {args[2]}", file=sys.stderr)
                        sys.exit(1)
                    print("image tag ok")
                    sys.exit(0)

                if args[:2] == ["image", "rm"]:
                    print("image rm ok")
                    sys.exit(0)

                if args[:2] == ["image", "inspect"]:
                    if os.environ.get("FAKE_CANDIDATE_IMAGE_MISSING") == "1":
                        sys.exit(1)
                    if "--format" in args:
                        image_ref = args[-1]
                        if "supportportal-app:" in image_ref:
                            image_id = os.environ.get("FAKE_RUNTIME_IMAGE_ID", "sha256:" + "d" * 64)
                        elif "supportportal-automation-production:" in image_ref:
                            image_id = os.environ.get("FAKE_PRODUCTION_IMAGE_ID", "sha256:" + "c" * 64)
                        elif "supportportal-automation:" in image_ref:
                            image_id = os.environ.get("FAKE_AUTOMATION_IMAGE_ID", "sha256:" + "b" * 64)
                        else:
                            image_id = os.environ.get("FAKE_ROUTE_IMAGE_ID", "sha256:" + "a" * 64)
                        print(image_id)
                        sys.exit(0)
                    print("candidate image present")
                    sys.exit(0)

                if args[:1] == ["inspect"]:
                    format_value = args[args.index("--format") + 1]
                    if ".NetworkSettings.Networks" in format_value:
                        networks_path = state_dir / "nginx_networks.txt"
                        if networks_path.exists():
                            networks = set(networks_path.read_text(encoding="utf-8").splitlines())
                        else:
                            networks = set(
                                item.strip()
                                for item in os.environ.get("FAKE_NGINX_NETWORKS", "deployment_default").split(",")
                                if item.strip()
                            )
                        target = format_value.split('Networks "', 1)[1].split('"', 1)[0]
                        if target in networks:
                            print("attached")
                        sys.exit(0)
                    container_id = args[-1]
                    service = container_id.removesuffix("-container-id")
                    stack_started = (state_dir / "stack_started.txt").exists()
                    runtime_image_id = os.environ.get("FAKE_RUNTIME_IMAGE_ID", "sha256:" + "d" * 64)
                    if service == os.environ.get("FAKE_RUNTIME_BAD_IMAGE_SERVICE"):
                        runtime_image_id = "sha256:" + "e" * 64
                    restart_count = "1" if service == os.environ.get("FAKE_RUNTIME_RESTART_SERVICE") else "0"
                    if format_value == "{{.State.Running}} {{.State.Status}} {{.RestartCount}} {{.Image}}":
                        print(f"true running {restart_count} {runtime_image_id}")
                        sys.exit(0)
                    if format_value == "{{.State.Running}} {{.State.Status}} {{.RestartCount}}":
                        print(f"true running {restart_count}")
                        sys.exit(0)
                    previous_image_id = os.environ.get(
                        "FAKE_PREVIOUS_IMAGE_ID",
                        "sha256:previous-image-id",
                    )
                    previous_image = os.environ.get(
                        "FAKE_PREVIOUS_IMAGE",
                        "localhost/supportportal-app:previous-ref",
                    )
                    previous_ref = os.environ.get("FAKE_PREVIOUS_BUILD_REF", "previous-ref")
                    if format_value == "{{.Image}}":
                        print(runtime_image_id if stack_started else previous_image_id)
                    elif format_value == "{{.Config.Image}}":
                        print(previous_image)
                    else:
                        build_ref = os.environ.get("APP_BUILD_REF") if stack_started else previous_ref
                        if service == os.environ.get("FAKE_RUNTIME_BAD_BUILD_SERVICE"):
                            build_ref = "stale-build"
                        prompt_release_id = (
                            os.environ.get("PROMPT_RELEASE_ID") if stack_started else "release-previous"
                        )
                        if service == os.environ.get("FAKE_RUNTIME_BAD_RELEASE_SERVICE"):
                            prompt_release_id = "release-stale"
                        print(f"APP_BUILD_REF={build_ref}")
                        print(
                            "APP_BUILD_TIME="
                            + (os.environ.get("APP_BUILD_TIME") if stack_started else "2026-07-20T00:00:00Z")
                        )
                        print(
                            "PROMPT_RELEASE_ID="
                            + (prompt_release_id or "")
                        )
                    sys.exit(0)

                if args[:1] == ["logs"]:
                    container_id = args[-1]
                    service = container_id.removesuffix("-container-id")
                    log_service = "api-production" if service == "api_production" else service
                    if service == os.environ.get("FAKE_RUNTIME_BAD_LOG_SERVICE"):
                        log_service = "stale-service"
                    print(
                        f"prompt_runtime_loaded service={log_service} "
                        "release_id=release-candidate prompts=1 source=release"
                    )
                    sys.exit(0)

                if args[:2] == ["network", "inspect"]:
                    network_name = args[-1]
                    networks_path = state_dir / "networks.txt"
                    networks = set(networks_path.read_text(encoding="utf-8").splitlines()) if networks_path.exists() else set()
                    if network_name in networks and "--format" in args:
                        internal_networks = set(
                            item.strip()
                            for item in os.environ.get("FAKE_INTERNAL_NETWORKS", "").split(",")
                            if item.strip()
                        )
                        print("true" if network_name in internal_networks else "false")
                        sys.exit(0)
                    sys.exit(0 if network_name in networks else 1)

                if args[:2] == ["network", "create"]:
                    network_name = args[-1]
                    with (state_dir / "networks.txt").open("a", encoding="utf-8") as handle:
                        handle.write(network_name + "\\n")
                    print(network_name)
                    sys.exit(0)

                if args[:2] == ["network", "connect"]:
                    if os.environ.get("FAKE_DOCKER_NETWORK_CONNECT_EXIT_CODE") == "1":
                        print("network connect failed", file=sys.stderr)
                        sys.exit(1)
                    network_name = args[-2]
                    networks_path = state_dir / "nginx_networks.txt"
                    networks = set(networks_path.read_text(encoding="utf-8").splitlines()) if networks_path.exists() else set()
                    networks.add(network_name)
                    networks_path.write_text("\\n".join(sorted(networks)) + "\\n", encoding="utf-8")
                    sys.exit(0)

                if args[:1] != ["compose"]:
                    print("unexpected docker invocation", args, file=sys.stderr)
                    sys.exit(1)

                if "build" in args:
                    if os.environ.get("FAKE_DOCKER_BUILD_EXIT_CODE") == "1":
                        print(
                            "failed to solve: failed to prepare extraction snapshot "
                            "'extract-test': parent snapshot sha256:test does not exist: not found",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                    print("build ok")
                    sys.exit(0)

                if "pull" in args:
                    print("pull ok")
                    sys.exit(0)

                if "run" in args:
                    if "runtime_bootstrap" in args:
                        print('{"mode": "bootstrap", "ok": true}')
                        sys.exit(0)
                    if "prepare" in args:
                        print("release-candidate\\ttrue")
                    elif "validate" in args:
                        if os.environ.get("FAKE_PROMPT_VALIDATE_EXIT_CODE") == "1":
                            print("validate failed", file=sys.stderr)
                            sys.exit(1)
                        print('{"ok":true}')
                    elif "activate" in args:
                        if os.environ.get("FAKE_PROMPT_ACTIVATE_EXIT_CODE") == "1":
                            print("activate failed", file=sys.stderr)
                            sys.exit(1)
                        print('{"ok":true}')
                    elif "current" in args:
                        if any(item.startswith("TICKET_DB_DSN=") for item in args):
                            print(os.environ.get("FAKE_PRODUCTION_ACTIVE_RELEASE_ID", "release-candidate"))
                        elif os.environ.get("FAKE_PROMPT_ACTIVATE_COMMITTED") == "1":
                            print("release-candidate")
                        else:
                            print("release-previous")
                    elif "sync" in args:
                        sync_counter_path = state_dir / "prompt_sync_calls.txt"
                        sync_calls = int(sync_counter_path.read_text(encoding="utf-8")) if sync_counter_path.exists() else 0
                        sync_calls += 1
                        sync_counter_path.write_text(str(sync_calls), encoding="utf-8")
                        fail_on_call = int(os.environ.get("FAKE_PROMPT_SYNC_FAIL_ON_CALL", "0"))
                        if os.environ.get("FAKE_PROMPT_SYNC_EXIT_CODE") == "1" or sync_calls == fail_on_call:
                            print("sync failed", file=sys.stderr)
                            sys.exit(1)
                        print('{"ok":true}')
                    elif "fail" in args:
                        print('{"ok":true}')
                    else:
                        print("unsupported prompt command", args, file=sys.stderr)
                        sys.exit(1)
                    sys.exit(0)

                if "down" in args:
                    (state_dir / "stack_started.txt").unlink(missing_ok=True)
                    print("down ok")
                    sys.exit(0)

                if "up" in args:
                    if os.environ.get("FAKE_DOCKER_UP_EXIT_CODE") == "1" and "--no-build" not in args:
                        print("up failed", file=sys.stderr)
                        sys.exit(1)
                    (state_dir / "stack_started.txt").write_text("1", encoding="utf-8")
                    print("up ok")
                    sys.exit(0)

                if "ps" in args:
                    if "-q" in args:
                        if args[-1] == "nginx" and os.environ.get("FAKE_NGINX_CONTAINER_MISSING") == "1":
                            sys.exit(0)
                        print(f"{args[-1]}-container-id")
                    else:
                        print("NAME\\napi up")
                    sys.exit(0)

                if "exec" in args:
                    failure_limit = int(os.environ.get("FAKE_PROMPT_RUNTIME_VERIFY_FAILURES", "0"))
                    failure_file = state_dir / "prompt_runtime_verify_attempts.txt"
                    attempts = int(failure_file.read_text(encoding="utf-8")) if failure_file.exists() else 0
                    if attempts < failure_limit:
                        failure_file.write_text(str(attempts + 1), encoding="utf-8")
                        print("runtime service is still starting", file=sys.stderr)
                        sys.exit(1)
                    print("health contract ok")
                    sys.exit(0)

                if "logs" in args:
                    print("api | INFO startup complete")
                    sys.exit(0)

                print("unsupported docker compose invocation", args, file=sys.stderr)
                sys.exit(1)
                """
            ),
        )

        self._write_executable(
            self.fake_bin / "df",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["DEPLOY_TEST_STATE_DIR"])
                sequence = [
                    item.strip()
                    for item in os.environ.get("FAKE_DF_AVAILABLE_KB_SEQUENCE", "209715200").split(",")
                    if item.strip()
                ]
                counter_path = state_dir / "df_counter.txt"
                current_index = int(counter_path.read_text(encoding="utf-8")) if counter_path.exists() else 0
                selected_index = min(current_index, len(sequence) - 1)
                available_kb = sequence[selected_index]
                counter_path.write_text(str(current_index + 1), encoding="utf-8")

                with (state_dir / "df_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"argv": sys.argv[1:], "available_kb": available_kb}) + "\\n")

                print("Filesystem 1024-blocks Used Available Capacity Mounted on")
                print(f"/dev/root 314572800 104857600 {available_kb} 34% /")
                """
            ),
        )

        self._write_executable(
            self.fake_bin / "curl",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["DEPLOY_TEST_STATE_DIR"])
                url = sys.argv[-1]
                with (state_dir / "curl_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"argv": sys.argv[1:], "url": url}) + "\\n")

                failure_counter_path = state_dir / "curl_failure_counter.txt"
                failure_count = int(os.environ.get("FAKE_CURL_FAIL_COUNT", "0"))
                failures_seen = (
                    int(failure_counter_path.read_text(encoding="utf-8"))
                    if failure_counter_path.exists()
                    else 0
                )
                if url == os.environ.get("FAKE_CURL_FAIL_URL") and failures_seen < failure_count:
                    failure_counter_path.write_text(str(failures_seen + 1), encoding="utf-8")
                    print("health failed", file=sys.stderr)
                    sys.exit(22)

                print('{"status":"ok"}')
                """
            ),
        )

        self._write_executable(
            self.fake_bin / "flock",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                exit 0
                """
            ),
        )

    def _script_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["DEPLOY_LOCK_FILE"] = str(self.root / "deploy.lock")
        env["DEPLOY_TEST_STATE_DIR"] = str(self.state_dir)
        env["DEPLOY_WORKER_STABILITY_SECONDS"] = "0"
        if extra_env:
            env.update(extra_env)
        return env

    def _run_script(self, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.repo / "deployment" / "deploy_ec2.sh"), *args],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
            env=self._script_env(extra_env),
        )

    def _read_json_lines(self, path: Path) -> list[dict[str, object]]:
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _compose_verbs(self) -> list[str]:
        verbs: list[str] = []
        for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl"):
            argv = call["argv"]
            assert isinstance(argv, list)
            for item in argv:
                if item in {"build", "down", "up", "ps", "logs"}:
                    verbs.append(item)
                    break
        return verbs

    def _docker_actions(self) -> list[str]:
        actions: list[str] = []
        for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl"):
            argv = call["argv"]
            assert isinstance(argv, list)
            if argv[:2] == ["builder", "prune"]:
                actions.append("builder-prune")
                continue
            if argv[:2] == ["image", "prune"]:
                actions.append("image-prune")
                continue
            if "compose" not in argv:
                continue
            for item in argv:
                if item in {"build", "down", "up", "ps", "logs"}:
                    actions.append(f"compose-{item}")
                    break
        return actions

    def test_build_failure_preserves_running_services_until_new_image_exists(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            "--domain",
            "support.stellarix.space",
            extra_env={"FAKE_DOCKER_BUILD_EXIT_CODE": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("docker compose build failed", result.stdout + result.stderr)
        self.assertEqual(self._compose_verbs(), ["ps", "build", "ps", "logs"])
        self.assertEqual(self._read_json_lines(self.state_dir / "curl_calls.jsonl"), [])
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        logs_call = next(call for call in docker_calls if "logs" in call["argv"])
        self.assertIn("worker_query", logs_call["argv"])
        self.assertIn("worker_aux", logs_call["argv"])
        self.assertNotIn("worker", logs_call["argv"])
        self.assertTrue(any(call["argv"][:3] == ["image", "rm", "-f"] for call in docker_calls))

    def test_successful_deploy_builds_before_down_and_reuses_built_image(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            "--domain",
            "support.stellarix.space",
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        verbs = self._compose_verbs()
        self.assertEqual(verbs[:5], ["ps", "build", "down", "up", "ps"])
        self.assertGreaterEqual(verbs.count("ps"), 7)
        self.assertNotIn("builder-prune", self._docker_actions())
        self.assertNotIn("image-prune", self._docker_actions())

        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertFalse(
            any(call["argv"][:2] == ["network", "create"] for call in docker_calls),
            "main-stack deployment must not recreate retired split networks",
        )
        self.assertFalse(
            any(call["argv"][:2] == ["network", "connect"] for call in docker_calls),
            "main-stack deployment must not attach nginx to the retired split edge network",
        )
        tag_index = next(
            index
            for index, call in enumerate(docker_calls)
            if call["argv"][:2] == ["image", "tag"]
        )
        build_index = next(index for index, call in enumerate(docker_calls) if "build" in call["argv"])
        prepare_index = next(index for index, call in enumerate(docker_calls) if "prepare" in call["argv"])
        validate_index = next(index for index, call in enumerate(docker_calls) if "validate" in call["argv"])
        down_index = next(index for index, call in enumerate(docker_calls) if "down" in call["argv"])
        activate_index = next(index for index, call in enumerate(docker_calls) if "activate" in call["argv"])
        self.assertLess(tag_index, build_index)
        self.assertLess(prepare_index, down_index)
        self.assertLess(prepare_index, validate_index)
        self.assertLess(validate_index, down_index)
        self.assertGreater(activate_index, down_index)
        up_call = next(call for call in docker_calls if "up" in call["argv"])
        self.assertNotIn("--build", up_call["argv"])
        self.assertEqual(up_call["prompt_release_id"], "release-candidate")
        self.assertEqual(up_call["prompt_release_required"], "true")

    def test_prompt_runtime_verification_retries_transient_startup_failure(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={
                "FAKE_PROMPT_RUNTIME_VERIFY_FAILURES": "2",
                "DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS": "1",
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Waiting for Prompt runtime verification", result.stdout)
        self.assertGreaterEqual(
            int((self.state_dir / "prompt_runtime_verify_attempts.txt").read_text(encoding="utf-8")),
            2,
        )

        self.assertEqual(
            [call["url"] for call in self._read_json_lines(self.state_dir / "curl_calls.jsonl")],
            [
                "http://127.0.0.1:18080/health",
                "https://support.stellarix.space/health",
            ],
        )
        self.assertTrue(
            any(
                call["argv"][:3] == ["image", "rm", "-f"]
                for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
            )
        )

    def test_prompt_runtime_verification_rejects_stale_image(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={
                "FAKE_RUNTIME_BAD_IMAGE_SERVICE": "worker_query",
                "DEPLOY_HEALTH_TIMEOUT_SECONDS": "1",
                "DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS": "1",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prompt Release verification failed", result.stdout + result.stderr)

    def test_prompt_runtime_verification_rejects_stale_build_or_release(self) -> None:
        for variable, service in (
            ("FAKE_RUNTIME_BAD_BUILD_SERVICE", "worker_aux"),
            ("FAKE_RUNTIME_BAD_RELEASE_SERVICE", "rag_worker"),
        ):
            with self.subTest(variable=variable):
                result = self._run_script(
                    "--skip-pull",
                    "--branch",
                    "main",
                    extra_env={
                        variable: service,
                        "DEPLOY_HEALTH_TIMEOUT_SECONDS": "1",
                        "DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS": "1",
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Prompt Release verification failed", result.stdout + result.stderr)
                for path in self.state_dir.iterdir():
                    if path.is_file():
                        path.unlink()

    def test_prompt_runtime_verification_rejects_restarting_worker(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={
                "FAKE_RUNTIME_RESTART_SERVICE": "worker_query",
                "DEPLOY_HEALTH_TIMEOUT_SECONDS": "1",
                "DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS": "1",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prompt Release verification failed", result.stdout + result.stderr)

    def test_deploy_generates_dynamic_build_metadata(self) -> None:
        expected_ref = _git(["rev-parse", "--short=12", "HEAD"], cwd=self.repo).stdout.strip()

        result = self._run_script("--skip-pull", "--branch", "main")

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        build_call = next(call for call in docker_calls if "build" in call["argv"])
        self.assertEqual(build_call["app_build_ref"], expected_ref)
        self.assertEqual(build_call["app_runtime_image"], f"localhost/supportportal-app:{expected_ref}")
        self.assertRegex(str(build_call["app_build_time"]), r"^2026-|^20\d{2}-")
        self.assertIn(f"Build ref: {expected_ref}", result.stdout)

    def test_production_deploy_syncs_candidate_release_to_production_database(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                NGINX_HOST_PORT=18080
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                PGVECTOR_DSN=postgresql://rag:test@db.local/rag
                PRODUCTION_TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets-production
                """
            ),
        )

        result = self._run_script("--skip-pull", "--branch", "main")

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Synced Prompt Release release-candidate to the /production database.", result.stdout)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        sync_calls = [call for call in docker_calls if "sync" in call["argv"]]
        self.assertEqual(len(sync_calls), 2)
        for call in sync_calls:
            self.assertIn("backend.scripts.prompt_release", call["argv"])
            self.assertEqual(
                call["argv"][call["argv"].index("--release-id") + 1],
                "release-candidate",
            )
            self.assertEqual(
                call["argv"][call["argv"].index("--target-dsn") + 1],
                "postgresql://ticket:test@db.local/tickets-production",
            )
        validate_index = next(index for index, call in enumerate(docker_calls) if "validate" in call["argv"])
        down_index = next(index for index, call in enumerate(docker_calls) if "down" in call["argv"])
        activate_index = next(index for index, call in enumerate(docker_calls) if "activate" in call["argv"])
        first_sync_index, second_sync_index = [
            index for index, call in enumerate(docker_calls) if "sync" in call["argv"]
        ]
        self.assertLess(validate_index, first_sync_index)
        self.assertLess(first_sync_index, down_index)
        self.assertLess(down_index, activate_index)
        self.assertLess(activate_index, second_sync_index)
        self.assertIn("Verified /production active Prompt Release release-candidate.", result.stdout)

        runtime_ps_services = {
            call["argv"][-1]
            for call in docker_calls
            if "ps" in call["argv"] and "-q" in call["argv"]
        }
        self.assertTrue(
            {
                "api",
                "rag_api",
                "rag_worker",
                "worker_query",
                "worker_aux",
                "api_production",
                "worker_query_production",
                "worker_aux_production",
            }.issubset(runtime_ps_services)
        )
        exec_services = {
            call["argv"][call["argv"].index("-T") + 1]
            for call in docker_calls
            if "exec" in call["argv"] and "-T" in call["argv"]
        }
        self.assertTrue({"api", "rag_api", "api_production"}.issubset(exec_services))

    def test_post_activation_production_sync_failure_keeps_healthy_new_stack(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                NGINX_HOST_PORT=18080
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                PGVECTOR_DSN=postgresql://rag:test@db.local/rag
                PRODUCTION_TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets-production
                """
            ),
        )

        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={"FAKE_PROMPT_SYNC_FAIL_ON_CALL": "2"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("healthy activated main stack remains running", result.stdout + result.stderr)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertTrue(any("activate" in call["argv"] for call in docker_calls))
        self.assertFalse(any("up" in call["argv"] and "--no-build" in call["argv"] for call in docker_calls))
        self.assertTrue(any(call["argv"][:3] == ["image", "rm", "-f"] for call in docker_calls))

    def test_production_active_release_readback_mismatch_is_partial_failure(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                NGINX_HOST_PORT=18080
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                PGVECTOR_DSN=postgresql://rag:test@db.local/rag
                PRODUCTION_TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets-production
                """
            ),
        )

        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={"FAKE_PRODUCTION_ACTIVE_RELEASE_ID": "release-stale"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/production Prompt Release readback failed", result.stdout + result.stderr)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertFalse(any("up" in call["argv"] and "--no-build" in call["argv"] for call in docker_calls))

    def test_production_sync_failure_fails_before_stopping_services(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                NGINX_HOST_PORT=18080
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                PGVECTOR_DSN=postgresql://rag:test@db.local/rag
                PRODUCTION_TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets-production
                """
            ),
        )

        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={"FAKE_PROMPT_SYNC_EXIT_CODE": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prompt Release production sync failed", result.stdout + result.stderr)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertFalse(any("down" in call["argv"] for call in docker_calls))
        self.assertFalse(any("up" in call["argv"] for call in docker_calls))

    def test_up_failure_restores_previous_image_id(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={"FAKE_DOCKER_UP_EXIT_CODE": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Restored previous image localhost/supportportal-app:previous-ref", result.stdout)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        tag_call = next(call for call in docker_calls if call["argv"][:2] == ["image", "tag"])
        self.assertEqual(tag_call["argv"][2], "sha256:previous-image-id")
        rollback_tag = tag_call["argv"][3]
        rollback_up = next(
            call
            for call in docker_calls
            if "up" in call["argv"] and "--no-build" in call["argv"]
        )
        self.assertEqual(rollback_up["app_runtime_image"], rollback_tag)
        self.assertEqual(rollback_up["app_build_ref"], "previous-ref")
        self.assertFalse(any(call["argv"][:3] == ["image", "rm", "-f"] for call in docker_calls))

    def test_same_runtime_tag_failure_restores_previous_image_id(self) -> None:
        expected_ref = _git(["rev-parse", "--short=12", "HEAD"], cwd=self.repo).stdout.strip()
        current_tag = f"localhost/supportportal-app:{expected_ref}"
        previous_image_id = "sha256:same-tag-previous-image-id"

        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={
                "FAKE_DOCKER_UP_EXIT_CODE": "1",
                "FAKE_PREVIOUS_IMAGE": current_tag,
                "FAKE_PREVIOUS_BUILD_REF": expected_ref,
                "FAKE_PREVIOUS_IMAGE_ID": previous_image_id,
            },
        )

        self.assertNotEqual(result.returncode, 0)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        tag_call = next(call for call in docker_calls if call["argv"][:2] == ["image", "tag"])
        self.assertEqual(tag_call["argv"][2], previous_image_id)
        rollback_tag = tag_call["argv"][3]
        self.assertNotEqual(rollback_tag, current_tag)
        rollback_up = next(
            call
            for call in docker_calls
            if "up" in call["argv"] and "--no-build" in call["argv"]
        )
        self.assertEqual(rollback_up["app_runtime_image"], rollback_tag)
        self.assertEqual(rollback_up["app_build_ref"], expected_ref)
        self.assertFalse(any(call["argv"][:3] == ["image", "rm", "-f"] for call in docker_calls))

    def test_missing_same_build_manifest_uses_existing_tag_for_rollback(self) -> None:
        expected_ref = _git(["rev-parse", "--short=12", "HEAD"], cwd=self.repo).stdout.strip()
        current_tag = f"localhost/supportportal-app:{expected_ref}"
        missing_image_id = "sha256:missing-running-image"

        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={
                "FAKE_DOCKER_UP_EXIT_CODE": "1",
                "FAKE_PREVIOUS_IMAGE": current_tag,
                "FAKE_PREVIOUS_BUILD_REF": expected_ref,
                "FAKE_PREVIOUS_IMAGE_ID": missing_image_id,
                "FAKE_IMAGE_TAG_FAIL_SOURCE": missing_image_id,
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("using the existing same-build image for rollback", result.stdout)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        tag_calls = [call for call in docker_calls if call["argv"][:2] == ["image", "tag"]]
        self.assertEqual(tag_calls[0]["argv"][2], missing_image_id)
        self.assertEqual(tag_calls[1]["argv"][2], current_tag)
        rollback_tag = tag_calls[1]["argv"][3]
        rollback_up = next(
            call
            for call in docker_calls
            if "up" in call["argv"] and "--no-build" in call["argv"]
        )
        self.assertEqual(rollback_up["app_runtime_image"], rollback_tag)
        self.assertEqual(rollback_up["app_build_ref"], expected_ref)

    def test_missing_different_build_manifest_aborts_before_build(self) -> None:
        missing_image_id = "sha256:missing-running-image"

        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={
                "FAKE_PREVIOUS_IMAGE_ID": missing_image_id,
                "FAKE_IMAGE_TAG_FAIL_SOURCE": missing_image_id,
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unable to preserve the running API image before build", result.stderr)
        self.assertNotIn("build", self._compose_verbs())

    def test_internal_health_failure_restores_previous_image_id(self) -> None:
        internal_url = "http://127.0.0.1:18080/health"
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={
                "DEPLOY_HEALTH_TIMEOUT_SECONDS": "1",
                "DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS": "1",
                "FAKE_CURL_FAIL_URL": internal_url,
                "FAKE_CURL_FAIL_COUNT": "2",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Internal health check failed", result.stdout + result.stderr)
        self.assertIn("Restored previous image localhost/supportportal-app:previous-ref", result.stdout)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        rollback_up = next(
            call
            for call in docker_calls
            if "up" in call["argv"] and "--no-build" in call["argv"]
        )
        self.assertEqual(rollback_up["app_build_ref"], "previous-ref")
        self.assertEqual(
            [call["url"] for call in self._read_json_lines(self.state_dir / "curl_calls.jsonl")],
            [internal_url, internal_url, internal_url],
        )

    def test_low_disk_space_prunes_docker_cache_before_build(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            "--domain",
            "support.stellarix.space",
            extra_env={"FAKE_DF_AVAILABLE_KB_SEQUENCE": "1048576,83886080"},
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Pruning Docker cache before build", result.stdout)
        self.assertEqual(
            self._docker_actions()[:7],
            [
                "builder-prune",
                "image-prune",
                "compose-ps",
                "compose-build",
                "compose-down",
                "compose-up",
                "compose-ps",
            ],
        )

    def test_activation_failure_marks_candidate_failed_and_restores_previous_release(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={"FAKE_PROMPT_ACTIVATE_EXIT_CODE": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prompt Release activation failed", result.stdout + result.stderr)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertTrue(any("fail" in call["argv"] for call in docker_calls))
        rollback_up = next(
            call for call in docker_calls if "up" in call["argv"] and "--no-build" in call["argv"]
        )
        self.assertEqual(rollback_up["prompt_release_id"], "release-previous")
        self.assertEqual(rollback_up["prompt_release_required"], "true")

    def test_prompt_validation_failure_keeps_running_stack_up(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={"FAKE_PROMPT_VALIDATE_EXIT_CODE": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prompt Release validation failed; the running stack was not stopped", result.stdout + result.stderr)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertTrue(any("fail" in call["argv"] for call in docker_calls))
        self.assertFalse(any("down" in call["argv"] for call in docker_calls))
        self.assertFalse(any("up" in call["argv"] for call in docker_calls))

    def test_activation_transport_failure_keeps_new_stack_when_candidate_is_already_active(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={
                "FAKE_PROMPT_ACTIVATE_EXIT_CODE": "1",
                "FAKE_PROMPT_ACTIVATE_COMMITTED": "1",
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("is already active; treating activation as successful", result.stdout)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertFalse(any("fail" in call["argv"] for call in docker_calls))
        self.assertFalse(any("up" in call["argv"] and "--no-build" in call["argv"] for call in docker_calls))

    def test_external_health_failure_restores_previous_release(self) -> None:
        external_url = "https://support.stellarix.space/health"
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            extra_env={
                "DEPLOY_HEALTH_TIMEOUT_SECONDS": "1",
                "DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS": "1",
                "FAKE_CURL_FAIL_URL": external_url,
                "FAKE_CURL_FAIL_COUNT": "2",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("External health check failed", result.stdout + result.stderr)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        rollback_up = next(
            call for call in docker_calls if "up" in call["argv"] and "--no-build" in call["argv"]
        )
        self.assertEqual(rollback_up["prompt_release_id"], "release-previous")

    def test_low_disk_space_after_prune_fails_before_build(self) -> None:
        result = self._run_script(
            "--skip-pull",
            "--branch",
            "main",
            "--domain",
            "support.stellarix.space",
            extra_env={"FAKE_DF_AVAILABLE_KB_SEQUENCE": "1048576,2097152"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("below required 40 GiB even after docker cache cleanup", result.stdout + result.stderr)
        self.assertEqual(self._docker_actions(), ["builder-prune", "image-prune"])
        self.assertEqual(self._read_json_lines(self.state_dir / "curl_calls.jsonl"), [])

    def test_split_rollback_swaps_current_and_previous_image_pointers(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                ROUTE_STAGING_IMAGE=registry.example/route@sha256:route-a
                AUTOMATION_STAGING_IMAGE=registry.example/automation@sha256:automation-a
                ROUTE_STAGING_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                AUTOMATION_STAGING_DB_DSN=postgresql://automation:test@db.local/staging
                AUTOMATION_STAGING_DB_SCHEMA=automation_staging
                AUTOMATION_STAGING_QUEUE=automation-staging
                AUTOMATION_STAGING_EVENT_CHANNEL=automation-staging-events
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )

        first = self._run_script("--environment", "staging", "--skip-pull")
        self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)
        network_creates = [
            call["argv"]
            for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
            if call["argv"][:2] == ["network", "create"]
        ]
        self.assertIn(["network", "create", "supportportal_automation_edge"], network_creates)
        self.assertIn(
            ["network", "create", "supportportal_automation_internal_staging"],
            network_creates,
        )
        self.assertNotIn(
            ["network", "create", "--internal", "supportportal_automation_internal_staging"],
            network_creates,
        )
        manifest = self.repo / ".deployments/staging.manifest"
        self.assertIn("route_image=registry.example/route@sha256:route-a", manifest.read_text())
        self.assertIn("previous_route_image=", manifest.read_text())

        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                ROUTE_STAGING_IMAGE=registry.example/route@sha256:route-b
                AUTOMATION_STAGING_IMAGE=registry.example/automation@sha256:automation-b
                ROUTE_STAGING_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                AUTOMATION_STAGING_DB_DSN=postgresql://automation:test@db.local/staging
                AUTOMATION_STAGING_DB_SCHEMA=automation_staging
                AUTOMATION_STAGING_QUEUE=automation-staging
                AUTOMATION_STAGING_EVENT_CHANNEL=automation-staging-events
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )
        second = self._run_script("--environment", "staging", "--skip-pull")
        self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
        second_manifest = manifest.read_text()
        self.assertIn("route_image=registry.example/route@sha256:route-b", second_manifest)
        self.assertIn("previous_route_image=registry.example/route@sha256:route-a", second_manifest)
        self.assertIn("previous_automation_image=registry.example/automation@sha256:automation-a", second_manifest)

        rollback = self._run_script("--environment", "staging", "--rollback", "--skip-pull")
        self.assertEqual(rollback.returncode, 0, msg=rollback.stdout + rollback.stderr)
        rollback_manifest = manifest.read_text()
        self.assertIn("route_image=registry.example/route@sha256:route-a", rollback_manifest)
        self.assertIn("previous_route_image=registry.example/route@sha256:route-b", rollback_manifest)
        self.assertIn("automation_image=registry.example/automation@sha256:automation-a", rollback_manifest)
        self.assertIn("previous_automation_image=registry.example/automation@sha256:automation-b", rollback_manifest)

        rollback_again = self._run_script("--environment", "staging", "--rollback", "--skip-pull")
        self.assertEqual(rollback_again.returncode, 0, msg=rollback_again.stdout + rollback_again.stderr)
        rollback_again_manifest = manifest.read_text()
        self.assertIn("route_image=registry.example/route@sha256:route-b", rollback_again_manifest)
        self.assertIn("previous_route_image=registry.example/route@sha256:route-a", rollback_again_manifest)
        nginx_connects = [
            call["argv"]
            for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
            if call["argv"][:2] == ["network", "connect"]
        ]
        self.assertEqual(
            nginx_connects,
            [["network", "connect", "supportportal_automation_edge", "nginx-container-id"]],
        )

    def test_split_deploy_fails_closed_when_automation_network_is_internal(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                ROUTE_STAGING_IMAGE=registry.example/route@sha256:route-a
                AUTOMATION_STAGING_IMAGE=registry.example/automation@sha256:automation-a
                ROUTE_STAGING_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                AUTOMATION_STAGING_DB_DSN=postgresql://automation:test@db.local/staging
                AUTOMATION_STAGING_DB_SCHEMA=automation_staging
                AUTOMATION_STAGING_QUEUE=automation-staging
                AUTOMATION_STAGING_EVENT_CHANNEL=automation-staging-events
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )

        first = self._run_script("--environment", "staging", "--skip-pull")
        self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)
        up_calls_before = len(
            [
                call
                for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
                if "up" in call["argv"]
            ]
        )

        second = self._run_script(
            "--environment",
            "staging",
            "--skip-pull",
            extra_env={"FAKE_INTERNAL_NETWORKS": "supportportal_automation_internal_staging"},
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("is internal", second.stdout + second.stderr)
        up_calls_after = len(
            [
                call
                for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
                if "up" in call["argv"]
            ]
        )
        self.assertEqual(up_calls_after, up_calls_before)

    def test_split_deploy_requires_execution_token(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                ROUTE_STAGING_IMAGE=registry.example/route@sha256:route-a
                AUTOMATION_STAGING_IMAGE=registry.example/automation@sha256:automation-a
                ROUTE_STAGING_SERVICE_TOKEN=route-token
                AUTOMATION_STAGING_DB_DSN=postgresql://automation:test@db.local/staging
                AUTOMATION_STAGING_DB_SCHEMA=automation_staging
                AUTOMATION_STAGING_QUEUE=automation-staging
                AUTOMATION_STAGING_EVENT_CHANNEL=automation-staging-events
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )

        result = self._run_script("--environment", "staging", "--skip-pull")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("n8n_request_token is required", result.stdout + result.stderr)

    def test_split_deploy_loads_release_manifest_without_manual_image_variables(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                ROUTE_STAGING_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )
        release_dir = self.repo / ".deployments/releases"
        release_dir.mkdir(parents=True)
        route_image = "localhost/supportportal-route:release-42"
        automation_image = "localhost/supportportal-automation:release-42"
        production_image = "localhost/supportportal-automation-production:release-42"
        route_image_id = "sha256:" + ("a" * 64)
        automation_image_id = "sha256:" + ("b" * 64)
        production_image_id = "sha256:" + ("c" * 64)
        self._write(
            self.repo,
            ".deployments/releases/release-42.env",
            "\n".join(
                [
                    "release_id=release-42",
                    "commit=a50ff400b635",
                    "build_time=2026-08-22T01:02:03Z",
                    f"ROUTE_STAGING_IMAGE={route_image}",
                    f"ROUTE_STAGING_IMAGE_ID={route_image_id}",
                    f"ROUTE_PREPRODUCTION_IMAGE={route_image}",
                    f"ROUTE_PREPRODUCTION_IMAGE_ID={route_image_id}",
                    f"ROUTE_PRODUCTION_IMAGE={route_image}",
                    f"ROUTE_PRODUCTION_IMAGE_ID={route_image_id}",
                    f"AUTOMATION_STAGING_IMAGE={automation_image}",
                    f"AUTOMATION_STAGING_IMAGE_ID={automation_image_id}",
                    f"AUTOMATION_PREPRODUCTION_IMAGE={automation_image}",
                    f"AUTOMATION_PREPRODUCTION_IMAGE_ID={automation_image_id}",
                    f"AUTOMATION_PRODUCTION_IMAGE={production_image}",
                    f"AUTOMATION_PRODUCTION_IMAGE_ID={production_image_id}",
                    "",
                ]
            ),
        )

        result = self._run_script(
            "--environment",
            "staging",
            "--release",
            "release-42",
            "--skip-pull",
            extra_env={
                "FAKE_ROUTE_IMAGE_ID": route_image_id,
                "FAKE_AUTOMATION_IMAGE_ID": automation_image_id,
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Loaded release manifest", result.stdout + result.stderr)
        self.assertIn(f"route={route_image}", result.stdout + result.stderr)
        self.assertIn(f"automation={automation_image}", result.stdout + result.stderr)
        self.assertIn("Using local split images; skipping docker compose pull.", result.stdout + result.stderr)
        calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertFalse(any("pull" in call["argv"] for call in calls))
        connect_index = next(
            index
            for index, call in enumerate(calls)
            if call["argv"][:2] == ["network", "connect"]
        )
        up_index = next(index for index, call in enumerate(calls) if "up" in call["argv"])
        self.assertLess(connect_index, up_index)
        self.assertTrue((self.repo / ".deployments/staging.manifest").exists())
        self.assertIn("db_table=automation_executions_staging", (self.repo / ".deployments/staging.manifest").read_text())
        up_call = next(call for call in calls if "up" in call["argv"] and "--no-build" in call["argv"])
        self.assertEqual(up_call["automation_staging_db_dsn"], "postgresql://ticket:test@db.local/tickets")
        self.assertEqual(up_call["automation_staging_db_schema"], "supportportal_staging")
        self.assertEqual(up_call["automation_staging_db_table"], "automation_executions_staging")
        self.assertEqual(up_call["automation_staging_queue"], "automation.staging")
        self.assertEqual(up_call["automation_staging_event_channel"], "automation.events.staging")
        self.assertEqual(
            up_call["build_refs"],
            {
                "ROUTE_STAGING_BUILD_REF": "a50ff400b635",
                "ROUTE_PREPRODUCTION_BUILD_REF": "a50ff400b635",
                "ROUTE_PRODUCTION_BUILD_REF": "a50ff400b635",
                "AUTOMATION_STAGING_BUILD_REF": "a50ff400b635",
                "AUTOMATION_PREPRODUCTION_BUILD_REF": "a50ff400b635",
                "AUTOMATION_PRODUCTION_BUILD_REF": "a50ff400b635",
            },
        )
        self.assertEqual(
            up_call["build_times"],
            {
                "ROUTE_STAGING_BUILD_TIME": "2026-08-22T01:02:03Z",
                "ROUTE_PREPRODUCTION_BUILD_TIME": "2026-08-22T01:02:03Z",
                "ROUTE_PRODUCTION_BUILD_TIME": "2026-08-22T01:02:03Z",
                "AUTOMATION_STAGING_BUILD_TIME": "2026-08-22T01:02:03Z",
                "AUTOMATION_PREPRODUCTION_BUILD_TIME": "2026-08-22T01:02:03Z",
                "AUTOMATION_PRODUCTION_BUILD_TIME": "2026-08-22T01:02:03Z",
            },
        )

    def test_split_deploy_fails_before_service_start_when_official_nginx_is_missing(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                ROUTE_STAGING_IMAGE=registry.example/route@sha256:route-a
                AUTOMATION_STAGING_IMAGE=registry.example/automation@sha256:automation-a
                ROUTE_STAGING_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                AUTOMATION_STAGING_DB_DSN=postgresql://automation:test@db.local/staging
                AUTOMATION_STAGING_DB_SCHEMA=automation_staging
                AUTOMATION_STAGING_QUEUE=automation-staging
                AUTOMATION_STAGING_EVENT_CHANNEL=automation-staging-events
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )

        result = self._run_script(
            "--environment",
            "staging",
            "--skip-pull",
            extra_env={"FAKE_NGINX_CONTAINER_MISSING": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Official nginx container is not running", result.stdout + result.stderr)
        calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertFalse(any("up" in call["argv"] for call in calls))

    def test_split_deploy_fails_before_service_start_when_nginx_network_connect_fails(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                ROUTE_STAGING_IMAGE=registry.example/route@sha256:route-a
                AUTOMATION_STAGING_IMAGE=registry.example/automation@sha256:automation-a
                ROUTE_STAGING_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                AUTOMATION_STAGING_DB_DSN=postgresql://automation:test@db.local/staging
                AUTOMATION_STAGING_DB_SCHEMA=automation_staging
                AUTOMATION_STAGING_QUEUE=automation-staging
                AUTOMATION_STAGING_EVENT_CHANNEL=automation-staging-events
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )

        result = self._run_script(
            "--environment",
            "staging",
            "--skip-pull",
            extra_env={"FAKE_DOCKER_NETWORK_CONNECT_EXIT_CODE": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unable to attach official nginx", result.stdout + result.stderr)
        calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertFalse(any("up" in call["argv"] for call in calls))

    def test_release_manifest_image_id_mismatch_fails_before_network_or_compose_changes(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                ROUTE_STAGING_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                AUTOMATION_STAGING_DB_DSN=postgresql://automation:test@db.local/staging
                AUTOMATION_STAGING_DB_SCHEMA=automation_staging
                AUTOMATION_STAGING_QUEUE=automation-staging
                AUTOMATION_STAGING_EVENT_CHANNEL=automation-staging-events
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )
        release_dir = self.repo / ".deployments/releases"
        release_dir.mkdir(parents=True)
        route_image = "localhost/supportportal-route:release-mismatch"
        automation_image = "localhost/supportportal-automation:release-mismatch"
        production_image = "localhost/supportportal-automation-production:release-mismatch"
        self._write(
            self.repo,
            ".deployments/releases/release-mismatch.env",
            "\n".join(
                [
                    "release_id=release-mismatch",
                    "commit=main",
                    f"ROUTE_STAGING_IMAGE={route_image}",
                    "ROUTE_STAGING_IMAGE_ID=sha256:" + ("d" * 64),
                    f"ROUTE_PREPRODUCTION_IMAGE={route_image}",
                    "ROUTE_PREPRODUCTION_IMAGE_ID=sha256:" + ("d" * 64),
                    f"ROUTE_PRODUCTION_IMAGE={route_image}",
                    "ROUTE_PRODUCTION_IMAGE_ID=sha256:" + ("d" * 64),
                    f"AUTOMATION_STAGING_IMAGE={automation_image}",
                    "AUTOMATION_STAGING_IMAGE_ID=sha256:" + ("b" * 64),
                    f"AUTOMATION_PREPRODUCTION_IMAGE={automation_image}",
                    "AUTOMATION_PREPRODUCTION_IMAGE_ID=sha256:" + ("b" * 64),
                    f"AUTOMATION_PRODUCTION_IMAGE={production_image}",
                    "AUTOMATION_PRODUCTION_IMAGE_ID=sha256:" + ("c" * 64),
                    "",
                ]
            ),
        )

        result = self._run_script("--environment", "staging", "--release", "release-mismatch", "--skip-pull")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local image ID mismatch", result.stdout + result.stderr)
        self.assertEqual(self._compose_verbs(), [])
        calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertFalse(any(call["argv"][:2] == ["network", "create"] for call in calls))

    def test_preproduction_reuses_account_database_with_environment_defaults(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                ROUTE_PREPRODUCTION_IMAGE=registry.example/route@sha256:route-preproduction
                AUTOMATION_PREPRODUCTION_IMAGE=registry.example/automation@sha256:automation-preproduction
                ROUTE_PREPRODUCTION_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )

        result = self._run_script("--environment", "preproduction", "--skip-pull")

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        up_call = next(
            call
            for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
            if "up" in call["argv"] and "--no-build" in call["argv"]
        )
        self.assertEqual(up_call["automation_preproduction_db_dsn"], "postgresql://ticket:test@db.local/tickets")
        self.assertEqual(up_call["automation_preproduction_db_schema"], "supportportal_preproduction")
        self.assertEqual(up_call["automation_preproduction_db_table"], "automation_executions_preproduction")
        self.assertEqual(up_call["automation_preproduction_queue"], "automation.preproduction")
        self.assertEqual(up_call["automation_preproduction_event_channel"], "automation.events.preproduction")

    def test_production_reuses_production_database_with_environment_defaults(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                PRODUCTION_TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets-production
                TICKET_DB_MIGRATION_DSN=postgresql://migration:test@db.local/tickets
                AUTOMATION_PRODUCTION_DB_MIGRATION_DSN=postgresql://migration:test@db.local/tickets-production
                PGVECTOR_DSN=postgresql://rag:test@db.local/rag
                APP_RUNTIME_IMAGE=registry.example/app@sha256:app-production
                ROUTE_PRODUCTION_IMAGE=registry.example/route@sha256:route-production
                AUTOMATION_PRODUCTION_IMAGE=registry.example/automation@sha256:automation-production
                ROUTE_PRODUCTION_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )

        result = self._run_script(
            "--environment",
            "production",
            "--skip-pull",
            extra_env={"DEPLOY_PRODUCTION_APPROVED": "1"},
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        bootstrap_call = next(
            call
            for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
            if "runtime_bootstrap" in call["argv"]
        )
        self.assertEqual(
            bootstrap_call["automation_production_db_migration_dsn"],
            "postgresql://migration:test@db.local/tickets-production",
        )
        self.assertEqual(
            bootstrap_call["ticket_db_migration_dsn"],
            "postgresql://migration:test@db.local/tickets-production",
        )
        up_call = next(
            call
            for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
            if "up" in call["argv"] and "--no-build" in call["argv"]
        )
        self.assertEqual(up_call["automation_production_db_dsn"], "postgresql://ticket:test@db.local/tickets-production")
        self.assertEqual(up_call["automation_production_db_schema"], "supportportal_production")
        self.assertEqual(up_call["automation_production_db_table"], "automation_executions_production")
        self.assertEqual(up_call["automation_production_queue"], "automation.production")
        self.assertEqual(up_call["automation_production_event_channel"], "automation.events.production")

    def test_production_requires_dedicated_migration_dsn_before_compose_up(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                PRODUCTION_TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets-production
                TICKET_DB_MIGRATION_DSN=postgresql://migration:test@db.local/tickets-production
                PGVECTOR_DSN=postgresql://rag:test@db.local/rag
                APP_RUNTIME_IMAGE=registry.example/app@sha256:app-production
                ROUTE_PRODUCTION_IMAGE=registry.example/route@sha256:route-production
                AUTOMATION_PRODUCTION_IMAGE=registry.example/automation@sha256:automation-production
                ROUTE_PRODUCTION_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                """
            ),
        )

        result = self._run_script(
            "--environment",
            "production",
            "--skip-pull",
            extra_env={"DEPLOY_PRODUCTION_APPROVED": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AUTOMATION_PRODUCTION_DB_MIGRATION_DSN is required", result.stdout + result.stderr)
        self.assertFalse(
            any(
                "up" in call["argv"]
                for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
            )
        )

    def test_production_rejects_dedicated_migration_dsn_for_another_database(self) -> None:
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets
                PRODUCTION_TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets-production
                TICKET_DB_MIGRATION_DSN=postgresql://migration:test@db.local/tickets-production
                AUTOMATION_PRODUCTION_DB_MIGRATION_DSN=postgresql://migration:test@db.local/tickets
                PGVECTOR_DSN=postgresql://rag:test@db.local/rag
                APP_RUNTIME_IMAGE=registry.example/app@sha256:app-production
                ROUTE_PRODUCTION_IMAGE=registry.example/route@sha256:route-production
                AUTOMATION_PRODUCTION_IMAGE=registry.example/automation@sha256:automation-production
                ROUTE_PRODUCTION_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                """
            ),
        )

        result = self._run_script(
            "--environment",
            "production",
            "--skip-pull",
            extra_env={"DEPLOY_PRODUCTION_APPROVED": "1"},
        )

        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn("AUTOMATION_PRODUCTION_DB_MIGRATION_DSN must target the same database", output)
        self.assertIn("'tickets' vs 'tickets-production'", output)
        self.assertFalse(
            any(
                "up" in call["argv"]
                for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")
            )
        )

    def test_split_deploy_honors_branch_pull_before_loading_images(self) -> None:
        remote = self.root / "remote.git"
        _git(["init", "--bare", str(remote)], cwd=self.root)
        _git(["remote", "add", "origin", str(remote)], cwd=self.repo)
        _git(["push", "--set-upstream", "origin", "main"], cwd=self.repo)
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                ROUTE_STAGING_IMAGE=registry.example/route@sha256:route-a
                AUTOMATION_STAGING_IMAGE=registry.example/automation@sha256:automation-a
                ROUTE_STAGING_SERVICE_TOKEN=route-token
                n8n_request_token=execution-token
                AUTOMATION_STAGING_DB_DSN=postgresql://automation:test@db.local/staging
                AUTOMATION_STAGING_DB_SCHEMA=automation_staging
                AUTOMATION_STAGING_QUEUE=automation-staging
                AUTOMATION_STAGING_EVENT_CHANNEL=automation-staging-events
                DEPLOY_HEALTH_TIMEOUT_SECONDS=1
                DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS=1
                """
            ),
        )

        result = self._run_script("--environment", "staging", "--branch", "main")

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Pulling latest code from origin/main", result.stdout + result.stderr)

    def test_release_manifest_missing_fails_before_network_or_compose_changes(self) -> None:
        result = self._run_script("--environment", "staging", "--release", "missing", "--skip-pull")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Release manifest not found", result.stdout + result.stderr)
        self.assertEqual(self._read_json_lines(self.state_dir / "docker_calls.jsonl"), [])

    def test_split_rollback_without_environment_fails_closed(self) -> None:
        result = self._run_script("--rollback", "--skip-pull")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--rollback requires --environment", result.stdout + result.stderr)
        self.assertFalse(any("build" in call["argv"] for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")))
        self.assertFalse(any("down" in call["argv"] for call in self._read_json_lines(self.state_dir / "docker_calls.jsonl")))


if __name__ == "__main__":
    unittest.main()
