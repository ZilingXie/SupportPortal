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
                    print("image tag ok")
                    sys.exit(0)

                if args[:2] == ["image", "rm"]:
                    print("image rm ok")
                    sys.exit(0)

                if args[:1] == ["inspect"]:
                    format_value = args[args.index("--format") + 1]
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
                        print(previous_image_id)
                    elif format_value == "{{.Config.Image}}":
                        print(previous_image)
                    else:
                        print(f"APP_BUILD_REF={previous_ref}")
                        print("APP_BUILD_TIME=2026-07-20T00:00:00Z")
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

                if "down" in args:
                    print("down ok")
                    sys.exit(0)

                if "up" in args:
                    if os.environ.get("FAKE_DOCKER_UP_EXIT_CODE") == "1" and "--no-build" not in args:
                        print("up failed", file=sys.stderr)
                        sys.exit(1)
                    print("up ok")
                    sys.exit(0)

                if "ps" in args:
                    if "-q" in args:
                        print("api-container-id")
                    else:
                        print("NAME\\napi up")
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
        self.assertEqual(self._compose_verbs(), ["build", "ps", "logs"])
        self.assertEqual(self._read_json_lines(self.state_dir / "curl_calls.jsonl"), [])
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        logs_call = next(call for call in docker_calls if "logs" in call["argv"])
        self.assertIn("worker_query", logs_call["argv"])
        self.assertIn("worker_aux", logs_call["argv"])
        self.assertNotIn("worker", logs_call["argv"])

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
        self.assertEqual(verbs, ["build", "ps", "down", "up", "ps"])
        self.assertNotIn("builder-prune", self._docker_actions())
        self.assertNotIn("image-prune", self._docker_actions())

        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        up_call = next(call for call in docker_calls if "up" in call["argv"])
        self.assertNotIn("--build", up_call["argv"])

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
            self._docker_actions(),
            [
                "builder-prune",
                "image-prune",
                "compose-build",
                "compose-ps",
                "compose-down",
                "compose-up",
                "compose-ps",
            ],
        )

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


if __name__ == "__main__":
    unittest.main()
