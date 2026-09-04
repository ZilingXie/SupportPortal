from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_SOURCE = REPO_ROOT / "deployment" / "build_automation_ecs_release.sh"


class BuildAutomationEcsReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()

        self._write(self.repo, "backend/Dockerfile.automation", "FROM scratch\n")
        self._write(self.repo, "deployment/build_automation_ecs_release.sh", SCRIPT_SOURCE.read_text(encoding="utf-8"))
        (self.repo / "deployment/build_automation_ecs_release.sh").chmod(0o755)
        for relative in (
            "backend/__init__.py",
            "backend/scripts/__init__.py",
            "backend/scripts/automation_release.py",
            "backend/services/__init__.py",
            "backend/services/automation_release_manifest.py",
            "backend/services/automation_ecs_contracts.py",
        ):
            source = REPO_ROOT / relative
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        self._write(self.repo, ".gitignore", ".deployments/\n")
        self._git("init -b main")
        self._git("config user.name 'Release Tester'")
        self._git("config user.email release@example.com")
        self._git("add .")
        self._git("commit -m initial")
        self._install_fake_docker()
        self._install_fake_podman()
        self._install_fake_python()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *command.split()],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=check,
        )

    def _write(self, root: Path, relative: str, content: str) -> None:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def _install_fake_docker(self) -> None:
        self._write(
            self.fake_bin,
            "docker",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import io
                import os
                import sys
                import tarfile
                from pathlib import Path

                state = Path(os.environ["RELEASE_TEST_STATE"])
                args = sys.argv[1:]
                with (state / "docker_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(args) + "\\n")
                if args[:2] == ["buildx", "version"]:
                    sys.exit(0)
                if args[:2] == ["buildx", "build"]:
                    output = next(value for value in args if value.startswith("type=oci,dest="))
                    destination = Path(output.split("dest=", 1)[1])
                    role_arg = next(value for value in args if value.startswith("AUTOMATION_IMAGE_ROLE="))
                    role = role_arg.rsplit("ecs-", 1)[1]
                    digit = {"api": "1", "route": "2", "worker": "3"}[role]
                    payload = json.dumps({
                        "schemaVersion": 2,
                        "manifests": [{
                            "mediaType": "application/vnd.oci.image.manifest.v1+json",
                            "digest": "sha256:" + digit * 64,
                            "size": 123,
                            "platform": {"os": "linux", "architecture": "amd64"},
                        }],
                    }).encode()
                    with tarfile.open(destination, "w") as archive:
                        info = tarfile.TarInfo("index.json")
                        info.size = len(payload)
                        archive.addfile(info, io.BytesIO(payload))
                    sys.exit(0)
                print(f"unexpected docker invocation: {args}", file=sys.stderr)
                sys.exit(1)
                """
            ),
        )
        (self.fake_bin / "docker").chmod(0o755)

    def _install_fake_podman(self) -> None:
        self._write(
            self.fake_bin,
            "podman",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import io
                import json
                import os
                import sys
                import tarfile
                from pathlib import Path

                state = Path(os.environ["RELEASE_TEST_STATE"])
                args = sys.argv[1:]
                with (state / "podman_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(args) + "\\n")
                if args == ["version"] or args[:1] == ["build"] or args[:2] == ["image", "rm"]:
                    sys.exit(0)
                if args[:1] == ["save"]:
                    destination = Path(args[args.index("--output") + 1])
                    tag = args[-1]
                    role = next(value for value in ("api", "route", "worker") if f":{value}-" in tag)
                    digit = {"api": "1", "route": "2", "worker": "3"}[role]
                    payload = json.dumps({
                        "schemaVersion": 2,
                        "manifests": [{
                            "mediaType": "application/vnd.oci.image.manifest.v1+json",
                            "digest": "sha256:" + digit * 64,
                            "size": 123,
                            "platform": {"os": "linux", "architecture": "amd64"},
                        }],
                    }).encode()
                    with tarfile.open(destination, "w") as archive:
                        info = tarfile.TarInfo("index.json")
                        info.size = len(payload)
                        archive.addfile(info, io.BytesIO(payload))
                    sys.exit(0)
                print(f"unexpected podman invocation: {args}", file=sys.stderr)
                sys.exit(1)
                """
            ),
        )
        (self.fake_bin / "podman").chmod(0o755)

    def _install_fake_python(self) -> None:
        self._write(
            self.fake_bin,
            "release-python",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state = Path(os.environ["RELEASE_TEST_STATE"])
                args = sys.argv[1:]
                with (state / "python_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(args) + "\\n")
                if args[:3] == ["-m", "backend.scripts.prompt_release", "validate"]:
                    sys.exit(int(os.environ.get("PROMPT_RELEASE_VALIDATE_EXIT", "0")))
                os.execv(os.environ["RELEASE_TEST_REAL_PYTHON"], [os.environ["RELEASE_TEST_REAL_PYTHON"], *args])
                """
            ),
        )
        (self.fake_bin / "release-python").chmod(0o755)

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PATH"] = f"{self.fake_bin}:{environment['PATH']}"
        environment["RELEASE_TEST_STATE"] = str(self.state_dir)
        environment["AUTOMATION_RELEASE_PYTHON"] = str(self.fake_bin / "release-python")
        environment["RELEASE_TEST_REAL_PYTHON"] = sys.executable
        return subprocess.run(
            [str(self.repo / "deployment/build_automation_ecs_release.sh"), *args],
            cwd=self.repo,
            text=True,
            capture_output=True,
            env=environment,
        )

    def test_builds_three_roles_and_writes_promotable_manifest(self) -> None:
        result = self._run(
            "--release-id", "release-42", "--prompt-release-id", "prompt-42"
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        manifest = self.repo / ".deployments/releases/release-42/release-manifest.json"
        values = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(values["release_id"], "release-42")
        self.assertEqual(values["prompt_release_id"], "prompt-42")
        self.assertEqual(values["platform"], "linux/amd64")
        self.assertEqual(values["components"]["api"]["tag"], "api-release-42")
        self.assertEqual(values["components"]["route"]["digest"], "sha256:" + "2" * 64)
        self.assertEqual(values["components"]["worker"]["oci_layout"], "worker.oci.tar")
        self.assertNotIn("repository", manifest.read_text(encoding="utf-8").lower())

        calls = [json.loads(line) for line in (self.state_dir / "docker_calls.jsonl").read_text().splitlines()]
        builds = [call for call in calls if call[:2] == ["buildx", "build"]]
        pushes = [call for call in calls if "push" in call]
        self.assertEqual(len(builds), 3)
        self.assertEqual(len(pushes), 0)
        self.assertTrue(all("--platform" in call and "linux/amd64" in call for call in builds))
        self.assertIn("AUTOMATION_IMAGE_ROLE=ecs-worker", builds[-1])
        python_calls = [
            json.loads(line)
            for line in (self.state_dir / "python_calls.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            python_calls[0],
            ["-m", "backend.scripts.prompt_release", "validate", "--release-id", "prompt-42"],
        )

    def test_prompt_release_validation_failure_stops_before_building(self) -> None:
        environment = os.environ.copy()
        environment["PATH"] = f"{self.fake_bin}:{environment['PATH']}"
        environment["RELEASE_TEST_STATE"] = str(self.state_dir)
        environment["AUTOMATION_RELEASE_PYTHON"] = str(self.fake_bin / "release-python")
        environment["RELEASE_TEST_REAL_PYTHON"] = sys.executable
        environment["PROMPT_RELEASE_VALIDATE_EXIT"] = "1"

        result = subprocess.run(
            [
                str(self.repo / "deployment/build_automation_ecs_release.sh"),
                "--prompt-release-id",
                "prompt-42",
            ],
            cwd=self.repo,
            text=True,
            capture_output=True,
            env=environment,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prompt Release validation failed", result.stdout + result.stderr)
        docker_log = self.state_dir / "docker_calls.jsonl"
        calls = [json.loads(line) for line in docker_log.read_text().splitlines()] if docker_log.exists() else []
        self.assertFalse(any(call[:2] == ["buildx", "build"] for call in calls))

    def test_dirty_worktree_fails_before_building(self) -> None:
        self._write(self.repo, "dirty.txt", "uncommitted\n")

        result = self._run("--prompt-release-id", "prompt-42")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Working tree is not clean", result.stdout + result.stderr)
        docker_log = self.state_dir / "docker_calls.jsonl"
        calls = [json.loads(line) for line in docker_log.read_text().splitlines()] if docker_log.exists() else []
        self.assertFalse(any(call[:2] == ["buildx", "build"] for call in calls))

    def test_podman_builds_and_saves_three_amd64_oci_archives(self) -> None:
        result = self._run(
            "--builder", "podman",
            "--release-id", "release-podman",
            "--prompt-release-id", "prompt-42",
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        calls = [
            json.loads(line)
            for line in (self.state_dir / "podman_calls.jsonl").read_text().splitlines()
        ]
        builds = [call for call in calls if call[:1] == ["build"]]
        saves = [call for call in calls if call[:1] == ["save"]]
        removals = [call for call in calls if call[:2] == ["image", "rm"]]
        self.assertEqual(len(builds), 3)
        self.assertEqual(len(saves), 3)
        self.assertEqual(len(removals), 3)
        self.assertTrue(all("--platform" in call and "linux/amd64" in call for call in builds))
        self.assertTrue(all("oci-archive" in call for call in saves))
        self.assertEqual(
            [next(value for value in call if value.startswith("--pull=")) for call in builds],
            ["--pull=always", "--pull=never", "--pull=never"],
        )


if __name__ == "__main__":
    unittest.main()
