from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[2] / "scripts" / "workflow"


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


class WorkflowScriptTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, repo: Path, relative_path: str, content: str) -> None:
        destination = repo / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def _commit_all(self, repo: Path, message: str) -> None:
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", message], cwd=repo)

    def _init_repo(self) -> Path:
        repo = self.root / "repo"
        repo.mkdir()
        _git(["init", "-b", "main"], cwd=repo)
        _git(["config", "user.name", "Workflow Tester"], cwd=repo)
        _git(["config", "user.email", "workflow@example.com"], cwd=repo)
        self._write(repo, "README.md", "initial\n")
        self._commit_all(repo, "Initial commit")
        return repo

    def _init_remote_repo(self) -> tuple[Path, Path, Path]:
        bare = self.root / "origin.git"
        _git(["init", "--bare", str(bare)], cwd=self.root)
        _git(["symbolic-ref", "HEAD", "refs/heads/main"], cwd=bare)

        seed = self.root / "seed"
        _git(["clone", str(bare), str(seed)], cwd=self.root)
        _git(["config", "user.name", "Workflow Tester"], cwd=seed)
        _git(["config", "user.email", "workflow@example.com"], cwd=seed)
        self._write(seed, "README.md", "initial\n")
        self._commit_all(seed, "Initial commit")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["switch", "-c", "mac"], cwd=seed)
        _git(["push", "-u", "origin", "mac"], cwd=seed)

        repo = self.root / "repo"
        _git(["clone", str(bare), str(repo)], cwd=self.root)
        _git(["config", "user.name", "Workflow Tester"], cwd=repo)
        _git(["config", "user.email", "workflow@example.com"], cwd=repo)
        _git(["switch", "mac"], cwd=repo)
        return bare, seed, repo

    def _init_remote_repo_on_main(self) -> tuple[Path, Path, Path]:
        bare, seed, repo = self._init_remote_repo()
        _git(["switch", "main"], cwd=repo)
        return bare, seed, repo

    def _script_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        return env

    def _run_workflow(self, script_name: str, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        script_path = SCRIPT_ROOT / script_name
        return subprocess.run(
            ["bash", str(script_path), *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            env=self._script_env(),
        )

    def _add_task_worktree(self, repo: Path, branch: str = "codex/example-task") -> Path:
        worktree = self.root / "worktrees" / branch.split("/", 1)[1]
        worktree.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "-b", branch, str(worktree), "main"], cwd=repo)
        return worktree

    def _advance_origin_main(self, seed: Path, relative_path: str = "main.txt") -> None:
        _git(["switch", "main"], cwd=seed)
        self._write(seed, relative_path, "main advanced\n")
        self._commit_all(seed, "Advance main")
        _git(["push", "origin", "main"], cwd=seed)

    def test_check_task_worktree_rejects_detached_head(self) -> None:
        repo = self._init_repo()
        commit = _git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
        _git(["switch", "--detach", commit], cwd=repo)

        result = self._run_workflow("check_task_worktree.sh", repo, "codex/example-task")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("detached HEAD", result.stderr)

    def test_check_task_worktree_allows_task_changes_and_known_artifacts(self) -> None:
        repo = self._init_repo()
        task_worktree = self._add_task_worktree(repo)
        self._write(task_worktree, "README.md", "task change\n")
        self._write(task_worktree, ".DS_Store", "junk")
        self._write(task_worktree, ".superpowers/session/state.json", "{}")

        result = self._run_workflow("check_task_worktree.sh", task_worktree, "codex/example-task")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Task changes may be committed", result.stdout)
        self.assertIn(".superpowers/session/state.json", result.stdout)
        self.assertIn(".DS_Store", result.stdout)

    def test_check_task_worktree_blocks_unknown_untracked_files(self) -> None:
        repo = self._init_repo()
        task_worktree = self._add_task_worktree(repo)
        self._write(task_worktree, "notes.txt", "todo\n")

        result = self._run_workflow("check_task_worktree.sh", task_worktree, "codex/example-task")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ambiguous untracked paths", result.stderr)
        self.assertIn("notes.txt", result.stderr)

    def test_check_task_worktree_rejects_codex_branch_in_root_workspace(self) -> None:
        repo = self._init_repo()
        _git(["switch", "-c", "codex/example-task"], cwd=repo)

        result = self._run_workflow("check_task_worktree.sh", repo, "codex/example-task")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rehome_task_worktree.sh", result.stderr)

    def test_create_task_worktree_creates_dedicated_worktree_from_clean_root_main(self) -> None:
        _, _, repo = self._init_remote_repo_on_main()

        result = self._run_workflow("create_task_worktree.sh", repo, "Engineer Opt")

        expected_path = self.home / ".config" / "superpowers" / "worktrees" / "repo" / "engineer-opt"
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Created task branch codex/engineer-opt.", result.stdout)
        self.assertIn(f"Worktree path: {expected_path}", result.stdout)
        self.assertEqual(_git(["branch", "--show-current"], cwd=repo).stdout.strip(), "main")
        self.assertEqual(_git(["status", "--short"], cwd=repo).stdout.strip(), "")
        self.assertTrue(expected_path.is_dir())
        self.assertEqual(_git(["branch", "--show-current"], cwd=expected_path).stdout.strip(), "codex/engineer-opt")

    def test_create_task_worktree_requires_clean_root_workspace(self) -> None:
        _, _, repo = self._init_remote_repo_on_main()
        self._write(repo, "README.md", "dirty root\n")

        result = self._run_workflow("create_task_worktree.sh", repo, "Engineer Opt")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Current worktree must be clean", result.stderr)

    def test_create_task_worktree_requires_main_in_root_workspace(self) -> None:
        _, _, repo = self._init_remote_repo()

        result = self._run_workflow("create_task_worktree.sh", repo, "Engineer Opt")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected current branch 'main'", result.stderr)

    def test_create_task_worktree_suffixes_existing_branch_name(self) -> None:
        _, _, repo = self._init_remote_repo_on_main()
        _git(["branch", "codex/engineer-opt", "main"], cwd=repo)

        result = self._run_workflow("create_task_worktree.sh", repo, "Engineer Opt")

        expected_path = self.home / ".config" / "superpowers" / "worktrees" / "repo" / "engineer-opt-2"
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Created task branch codex/engineer-opt-2.", result.stdout)
        self.assertTrue(expected_path.is_dir())
        self.assertEqual(_git(["branch", "--show-current"], cwd=expected_path).stdout.strip(), "codex/engineer-opt-2")

    def test_rehome_task_worktree_moves_dirty_root_codex_branch(self) -> None:
        _, _, repo = self._init_remote_repo_on_main()
        _git(["switch", "-c", "codex/example-task"], cwd=repo)
        self._write(repo, "README.md", "task change\n")
        self._write(repo, "notes.txt", "carry me\n")

        result = self._run_workflow("rehome_task_worktree.sh", repo, "codex/example-task")

        expected_path = self.home / ".config" / "superpowers" / "worktrees" / "repo" / "example-task"
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Root workspace is back on clean main.", result.stdout)
        self.assertIn(f"Task branch codex/example-task now lives at {expected_path}", result.stdout)
        self.assertEqual(_git(["branch", "--show-current"], cwd=repo).stdout.strip(), "main")
        self.assertEqual(_git(["status", "--short"], cwd=repo).stdout.strip(), "")
        self.assertTrue(expected_path.is_dir())
        self.assertEqual(_git(["branch", "--show-current"], cwd=expected_path).stdout.strip(), "codex/example-task")
        self.assertEqual((expected_path / "README.md").read_text(encoding="utf-8"), "task change\n")
        self.assertEqual((expected_path / "notes.txt").read_text(encoding="utf-8"), "carry me\n")

    def test_rehome_task_worktree_fails_on_unmerged_paths(self) -> None:
        repo = self._init_repo()
        self._write(repo, "conflict.txt", "base\n")
        self._commit_all(repo, "Add conflict base")
        _git(["switch", "-c", "other"], cwd=repo)
        self._write(repo, "conflict.txt", "other\n")
        self._commit_all(repo, "Other side")
        _git(["switch", "main"], cwd=repo)
        self._write(repo, "conflict.txt", "main\n")
        self._commit_all(repo, "Main side")
        _git(["switch", "-c", "codex/conflicted"], cwd=repo)
        merge_result = _git(["merge", "other"], cwd=repo, check=False)
        self.assertNotEqual(merge_result.returncode, 0)

        result = self._run_workflow("rehome_task_worktree.sh", repo, "codex/conflicted")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmerged paths", result.stderr.lower())

    def test_sync_mac_from_main_merges_origin_main_into_clean_mac(self) -> None:
        _, seed, repo = self._init_remote_repo()

        self._write(repo, "mac.txt", "mac only\n")
        self._commit_all(repo, "Mac-only change")
        _git(["push", "origin", "mac"], cwd=repo)
        self._advance_origin_main(seed)

        result = self._run_workflow("sync_mac_from_main.sh", repo)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(_git(["branch", "--show-current"], cwd=repo).stdout.strip(), "mac")
        self.assertEqual(
            _git(["merge-base", "--is-ancestor", "origin/main", "HEAD"], cwd=repo, check=False).returncode,
            0,
        )
        parents = _git(["rev-list", "--parents", "-n", "1", "HEAD"], cwd=repo).stdout.strip().split()
        self.assertGreaterEqual(len(parents), 3)

    def test_check_release_ready_requires_synced_mac(self) -> None:
        _, seed, repo = self._init_remote_repo()
        self._advance_origin_main(seed)

        result = self._run_workflow("check_release_ready.sh", repo)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Run scripts/workflow/sync_mac_from_main.sh first.", result.stderr)

    def test_check_release_ready_reports_pr_parameters_when_ready(self) -> None:
        _, seed, repo = self._init_remote_repo()
        self._write(repo, "mac.txt", "mac only\n")
        self._commit_all(repo, "Mac-only change")
        _git(["push", "origin", "mac"], cwd=repo)
        self._advance_origin_main(seed)

        sync_result = self._run_workflow("sync_mac_from_main.sh", repo)
        self.assertEqual(sync_result.returncode, 0, msg=sync_result.stderr)

        result = self._run_workflow("check_release_ready.sh", repo)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("base=main", result.stdout)
        self.assertIn("head=mac", result.stdout)
        self.assertIn("Create a new PR", result.stdout)

    def test_link_worktree_env_links_root_env_into_target(self) -> None:
        repo = self._init_repo()
        self._write(repo, ".env", "TOKEN=abc\n")
        worktree_path = self.root / "worktree-target"
        worktree_path.mkdir()

        result = self._run_workflow("link_worktree_env.sh", repo, str(worktree_path))

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        linked_env = worktree_path / ".env"
        self.assertTrue(linked_env.is_symlink())
        self.assertEqual(linked_env.resolve(), (repo / ".env").resolve())

    def test_link_worktree_env_requires_root_env(self) -> None:
        repo = self._init_repo()
        worktree_path = self.root / "worktree-target"
        worktree_path.mkdir()

        result = self._run_workflow("link_worktree_env.sh", repo, str(worktree_path))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Root .env not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
