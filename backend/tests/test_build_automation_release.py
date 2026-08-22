from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_SOURCE = REPO_ROOT / "deployment" / "build_automation_release.sh"


class BuildAutomationReleaseTests(unittest.TestCase):
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
        self._write(self.repo, "deployment/build_automation_release.sh", SCRIPT_SOURCE.read_text(encoding="utf-8"))
        (self.repo / "deployment/build_automation_release.sh").chmod(0o755)
        self._write(self.repo, ".gitignore", ".deployments/\n")
        self._git("init -b main")
        self._git("config user.name 'Release Tester'")
        self._git("config user.email release@example.com")
        self._git("add .")
        self._git("commit -m initial")
        self._install_fake_docker()

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
                import os
                import sys
                from pathlib import Path

                state = Path(os.environ["RELEASE_TEST_STATE"])
                args = sys.argv[1:]
                with (state / "docker_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(args) + "\\n")
                if args[:1] == ["build"] or args[:1] == ["push"]:
                    sys.exit(0)
                if args[:2] == ["image", "inspect"]:
                    tag = args[-1]
                    repository = tag.rsplit(":", 1)[0]
                    if repository.endswith("supportportal-route"):
                        digest = "1" * 64
                    elif repository.endswith("supportportal-automation-production"):
                        digest = "3" * 64
                    else:
                        digest = "2" * 64
                    print(f"{repository}@sha256:{digest}")
                    sys.exit(0)
                print(f"unexpected docker invocation: {args}", file=sys.stderr)
                sys.exit(1)
                """
            ),
        )
        (self.fake_bin / "docker").chmod(0o755)

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PATH"] = f"{self.fake_bin}:{environment['PATH']}"
        environment["RELEASE_TEST_STATE"] = str(self.state_dir)
        return subprocess.run(
            [str(self.repo / "deployment/build_automation_release.sh"), *args],
            cwd=self.repo,
            text=True,
            capture_output=True,
            env=environment,
        )

    def test_builds_three_roles_and_writes_promotable_manifest(self) -> None:
        result = self._run("--registry", "registry.example/supportportal", "--release-id", "release-42")

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        manifest = self.repo / ".deployments/releases/release-42.env"
        values = dict(
            line.split("=", 1)
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        self.assertEqual(values["release_id"], "release-42")
        self.assertRegex(values["ROUTE_STAGING_IMAGE"], r"@sha256:[0-9a-f]{64}$")
        self.assertEqual(values["ROUTE_STAGING_IMAGE"], values["ROUTE_PRODUCTION_IMAGE"])
        self.assertEqual(values["AUTOMATION_STAGING_IMAGE"], values["AUTOMATION_PREPRODUCTION_IMAGE"])
        self.assertNotEqual(values["AUTOMATION_STAGING_IMAGE"], values["AUTOMATION_PRODUCTION_IMAGE"])

        calls = [json.loads(line) for line in (self.state_dir / "docker_calls.jsonl").read_text().splitlines()]
        builds = [call for call in calls if call[:1] == ["build"]]
        pushes = [call for call in calls if call[:1] == ["push"]]
        self.assertEqual(len(builds), 3)
        self.assertEqual(len(pushes), 3)
        self.assertIn("AUTOMATION_IMAGE_ROLE=production", builds[-1])

    def test_dirty_worktree_fails_before_building(self) -> None:
        self._write(self.repo, "dirty.txt", "uncommitted\n")

        result = self._run("--registry", "registry.example/supportportal")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Working tree is not clean", result.stdout + result.stderr)
        self.assertFalse((self.state_dir / "docker_calls.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
