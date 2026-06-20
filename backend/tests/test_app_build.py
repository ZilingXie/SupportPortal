from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.services.app_build import resolve_app_build_info


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


class AppBuildTests(unittest.TestCase):
    def test_resolve_app_build_info_prefers_git_short_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            _git(["init", "-b", "main"], cwd=repo)
            _git(["config", "user.name", "Build Tester"], cwd=repo)
            _git(["config", "user.email", "build@example.com"], cwd=repo)
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            _git(["add", "README.md"], cwd=repo)
            _git(["commit", "-m", "Initial commit"], cwd=repo)

            info = resolve_app_build_info(
                repo_root=repo,
                env={
                    "APP_BUILD_REF": "env-fallback",
                    "APP_BUILD_TIME": "2026-04-08T07:30:00Z",
                },
            )

        self.assertRegex(str(info["ref"]), r"^[0-9a-f]{7,12}$")
        self.assertEqual(info["built_at"], "2026-04-08T07:30:00Z")

    def test_resolve_app_build_info_falls_back_to_env_when_git_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            info = resolve_app_build_info(
                repo_root=Path(temp_dir),
                env={
                    "APP_BUILD_REF": "abc123def456",
                    "APP_BUILD_TIME": "2026-04-08T07:31:00Z",
                },
            )

        self.assertEqual(info["ref"], "abc123def456")
        self.assertEqual(info["built_at"], "2026-04-08T07:31:00Z")

    def test_resolve_app_build_info_returns_stable_unknown_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            info = resolve_app_build_info(repo_root=Path(temp_dir), env={})

        self.assertEqual(info["ref"], "unknown")
        self.assertIsNone(info["built_at"])

    def test_dockerfile_exports_build_metadata_env(self) -> None:
        dockerfile = Path(__file__).resolve().parents[2] / "backend" / "Dockerfile"
        content = dockerfile.read_text(encoding="utf-8")

        self.assertIn("ARG APP_BUILD_REF=unknown", content)
        self.assertIn("ARG APP_BUILD_TIME=", content)
        self.assertIn("APP_BUILD_REF=${APP_BUILD_REF}", content)
        self.assertIn("APP_BUILD_TIME=${APP_BUILD_TIME}", content)

    def test_dockerfile_includes_vendored_graphrag_runtime(self) -> None:
        dockerfile = Path(__file__).resolve().parents[2] / "backend" / "Dockerfile"
        content = dockerfile.read_text(encoding="utf-8")

        self.assertIn("COPY vendor/cusmem /app/vendor/cusmem", content)

    def test_base_requirements_include_vendored_graphrag_runtime_dependencies(self) -> None:
        requirements = (Path(__file__).resolve().parents[2] / "requirements.base.txt").read_text(encoding="utf-8")

        for package in ["neo4j", "numpy", "openai", "tenacity", "posthog"]:
            self.assertRegex(requirements, rf"(?m)^{package}[<>=~!]")


if __name__ == "__main__":
    unittest.main()
