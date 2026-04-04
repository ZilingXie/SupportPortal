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
                    handle.write(json.dumps({"argv": args}) + "\\n")

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
                    if os.environ.get("FAKE_DOCKER_UP_EXIT_CODE") == "1":
                        print("up failed", file=sys.stderr)
                        sys.exit(1)
                    print("up ok")
                    sys.exit(0)

                if "ps" in args:
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
        self.assertEqual(verbs, ["build", "down", "up", "ps"])

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


if __name__ == "__main__":
    unittest.main()
