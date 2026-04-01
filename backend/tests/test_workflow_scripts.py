from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
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

    def _run_workflow(
        self,
        script_name: str,
        cwd: Path,
        *args: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        script_path = SCRIPT_ROOT / script_name
        env = self._script_env()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(script_path), *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def _add_task_worktree(self, repo: Path, branch: str = "codex/example-task") -> Path:
        worktree = self.root / "worktrees" / branch.split("/", 1)[1]
        worktree.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "-b", branch, str(worktree), "main"], cwd=repo)
        return worktree

    def _read_json(self, path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _install_fake_gh(self, remote_bare: Path | None = None) -> tuple[Path, Path]:
        bin_dir = self.root / "fake-bin"
        state_dir = self.root / "gh-state"
        bin_dir.mkdir()
        state_dir.mkdir()

        self._write_json(
            state_dir / "state.json",
            {
                "next_pr": 1,
                "prs": {},
                "repo": {
                    "name": "SupportPortal",
                    "default_branch": "main",
                    "allow_auto_merge": False,
                    "delete_branch_on_merge": False,
                    "allow_squash_merge": True,
                    "allow_merge_commit": True,
                    "allow_rebase_merge": True,
                },
                "rulesets": [],
            },
        )

        fake_gh = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import subprocess
            import sys
            import tempfile
            from datetime import datetime, timezone
            from pathlib import Path

            STATE_DIR = Path(os.environ["GH_FAKE_STATE_DIR"])
            STATE_PATH = STATE_DIR / "state.json"
            CALLS_PATH = STATE_DIR / "calls.log"
            REMOTE_BARE = os.environ.get("GH_FAKE_REMOTE_BARE")


            def load_state():
                return json.loads(STATE_PATH.read_text(encoding="utf-8"))


            def save_state(state):
                STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


            def record(argv):
                with CALLS_PATH.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(argv) + "\\n")


            def parse_flag(argv, flag):
                for index, value in enumerate(argv):
                    if value == flag and index + 1 < len(argv):
                        return argv[index + 1]
                return None


            def has_flag(argv, flag):
                return flag in argv


            def first_positional(argv, start_index):
                for value in argv[start_index:]:
                    if not value.startswith("-"):
                        return value
                return None


            def format_pr(pr):
                return {
                    "number": pr["number"],
                    "url": pr["url"],
                    "title": pr["title"],
                    "body": pr["body"],
                    "state": pr["state"],
                    "isDraft": pr.get("isDraft", False),
                    "headRefName": pr["headRefName"],
                    "baseRefName": pr["baseRefName"],
                    "mergedAt": pr.get("mergedAt"),
                    "mergeCommit": (
                        {"oid": pr["mergeCommit"]} if pr.get("mergeCommit") else None
                    ),
                }


            def find_pr(state, identifier):
                if identifier is None:
                    return None

                for pr in state["prs"].values():
                    if (
                        str(pr["number"]) == identifier
                        or pr["url"] == identifier
                        or pr["headRefName"] == identifier
                    ):
                        return pr
                return None


            def git(args, cwd=None, check=True):
                return subprocess.run(
                    ["git", *args],
                    cwd=cwd,
                    text=True,
                    capture_output=True,
                    check=check,
                )


            def iso_utc_now():
                return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


            def merge_pull_request(pr, delete_branch, match_head_commit):
                if not REMOTE_BARE:
                    raise RuntimeError("GH_FAKE_REMOTE_BARE is required for merge operations.")

                with tempfile.TemporaryDirectory(dir=STATE_DIR) as temp_dir:
                    repo = Path(temp_dir) / "repo"
                    git(["clone", REMOTE_BARE, str(repo)])
                    git(["config", "user.name", "Workflow Tester"], cwd=repo)
                    git(["config", "user.email", "workflow@example.com"], cwd=repo)
                    git(["fetch", "origin"], cwd=repo)
                    git(["switch", pr["baseRefName"]], cwd=repo)
                    head_ref = f"origin/{pr['headRefName']}"
                    remote_head = git(["rev-parse", head_ref], cwd=repo).stdout.strip()
                    if match_head_commit and remote_head != match_head_commit:
                        raise RuntimeError(
                            f"Head SHA mismatch for {pr['headRefName']}: expected {match_head_commit}, got {remote_head}"
                        )

                    git(["merge", "--squash", head_ref], cwd=repo)
                    commit_args = ["commit", "-m", pr["title"]]
                    if pr.get("body"):
                        commit_args.extend(["-m", pr["body"]])
                    git(commit_args, cwd=repo)
                    merge_commit = git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
                    git(["push", "origin", pr["baseRefName"]], cwd=repo)
                    if delete_branch:
                        git(["push", "origin", "--delete", pr["headRefName"]], cwd=repo, check=False)
                    return merge_commit


            def handle_pr(argv):
                state = load_state()
                subcommand = argv[1]

                if subcommand == "list":
                    head = parse_flag(argv, "--head")
                    base = parse_flag(argv, "--base")
                    wanted_state = parse_flag(argv, "--state")
                    matches = []
                    for pr in state["prs"].values():
                        if head and pr["headRefName"] != head:
                            continue
                        if base and pr["baseRefName"] != base:
                            continue
                        if wanted_state and pr["state"].lower() != wanted_state.lower():
                            continue
                        matches.append(format_pr(pr))
                    print(json.dumps(matches))
                    return 0

                if subcommand == "create":
                    head = parse_flag(argv, "--head")
                    base = parse_flag(argv, "--base")
                    title = parse_flag(argv, "--title") or f"PR for {head}"
                    body = parse_flag(argv, "--body") or ""
                    number = state["next_pr"]
                    state["next_pr"] += 1
                    pr = {
                        "number": number,
                        "url": f"https://example.test/pr/{number}",
                        "title": title,
                        "body": body,
                        "state": "OPEN",
                        "isDraft": False,
                        "headRefName": head,
                        "baseRefName": base,
                        "mergedAt": None,
                        "mergeCommit": None,
                    }
                    state["prs"][head] = pr
                    save_state(state)
                    print(pr["url"])
                    return 0

                if subcommand == "view":
                    identifier = first_positional(argv, 2)
                    pr = find_pr(state, identifier)
                    if not pr:
                        print(f"PR not found: {identifier}", file=sys.stderr)
                        return 1
                    print(json.dumps(format_pr(pr)))
                    return 0

                if subcommand == "merge":
                    identifier = first_positional(argv, 2)
                    pr = find_pr(state, identifier)
                    if not pr:
                        print(f"PR not found: {identifier}", file=sys.stderr)
                        return 1
                    try:
                        pr["mergeCommit"] = merge_pull_request(
                            pr,
                            delete_branch=has_flag(argv, "--delete-branch"),
                            match_head_commit=parse_flag(argv, "--match-head-commit"),
                        )
                    except Exception as exc:  # pragma: no cover - failure path asserted via return code
                        print(str(exc), file=sys.stderr)
                        return 1
                    pr["state"] = "MERGED"
                    pr["mergedAt"] = iso_utc_now()
                    save_state(state)
                    return 0

                print(f"Unsupported gh pr subcommand: {subcommand}", file=sys.stderr)
                return 1


            def handle_repo(argv):
                state = load_state()
                subcommand = argv[1]
                if subcommand == "edit":
                    settings = state["repo"]
                    for value in argv[2:]:
                        if value == "--enable-auto-merge":
                            settings["allow_auto_merge"] = True
                        elif value == "--enable-auto-merge=false":
                            settings["allow_auto_merge"] = False
                        elif value == "--delete-branch-on-merge":
                            settings["delete_branch_on_merge"] = True
                        elif value == "--delete-branch-on-merge=false":
                            settings["delete_branch_on_merge"] = False
                        elif value == "--enable-squash-merge":
                            settings["allow_squash_merge"] = True
                        elif value == "--enable-squash-merge=false":
                            settings["allow_squash_merge"] = False
                        elif value == "--enable-merge-commit":
                            settings["allow_merge_commit"] = True
                        elif value == "--enable-merge-commit=false":
                            settings["allow_merge_commit"] = False
                        elif value == "--enable-rebase-merge":
                            settings["allow_rebase_merge"] = True
                        elif value == "--enable-rebase-merge=false":
                            settings["allow_rebase_merge"] = False
                    save_state(state)
                    return 0

                print(f"Unsupported gh repo subcommand: {subcommand}", file=sys.stderr)
                return 1


            def handle_api(argv):
                state = load_state()
                method = "GET"
                input_path = None
                endpoint = None
                index = 1

                while index < len(argv):
                    value = argv[index]
                    if value in {"-X", "--method"} and index + 1 < len(argv):
                        method = argv[index + 1].upper()
                        index += 2
                    elif value == "--input" and index + 1 < len(argv):
                        input_path = argv[index + 1]
                        index += 2
                    elif value.startswith("-"):
                        index += 1
                    else:
                        if endpoint is None:
                            endpoint = value
                        index += 1

                if not endpoint:
                    print("Missing API endpoint", file=sys.stderr)
                    return 1

                if endpoint.endswith("/rulesets") and method == "GET":
                    summaries = []
                    for ruleset in state["rulesets"]:
                        summaries.append(
                            {
                                "id": ruleset["id"],
                                "name": ruleset["name"],
                                "target": ruleset["target"],
                                "source_type": "Repository",
                                "source": "example/SupportPortal",
                                "enforcement": ruleset["enforcement"],
                            }
                        )
                    print(json.dumps(summaries))
                    return 0

                if "/rulesets/" in endpoint and method == "GET":
                    ruleset_id = int(endpoint.rsplit("/", 1)[1])
                    for existing in state["rulesets"]:
                        if existing["id"] == ruleset_id:
                            print(json.dumps(existing))
                            return 0
                    print(f"Ruleset not found: {ruleset_id}", file=sys.stderr)
                    return 1

                if "/rulesets/" in endpoint and method == "PUT":
                    ruleset_id = int(endpoint.rsplit("/", 1)[1])
                    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
                    payload["id"] = ruleset_id
                    for idx, existing in enumerate(state["rulesets"]):
                        if existing["id"] == ruleset_id:
                            state["rulesets"][idx] = payload
                            save_state(state)
                            print(json.dumps(payload))
                            return 0
                    print(f"Ruleset not found: {ruleset_id}", file=sys.stderr)
                    return 1

                if endpoint.endswith("/rulesets") and method == "POST":
                    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
                    payload["id"] = state.setdefault("next_ruleset", 1)
                    state["next_ruleset"] += 1
                    state["rulesets"].append(payload)
                    save_state(state)
                    print(json.dumps(payload))
                    return 0

                if endpoint.startswith("repos/") and method == "GET":
                    print(json.dumps(state["repo"]))
                    return 0

                print(f"Unsupported gh api call: {method} {endpoint}", file=sys.stderr)
                return 1


            def main():
                argv = sys.argv[1:]
                record(argv)
                if not argv:
                    print("Missing gh command", file=sys.stderr)
                    return 1
                if argv[0] == "pr":
                    return handle_pr(argv)
                if argv[0] == "repo":
                    return handle_repo(argv)
                if argv[0] == "api":
                    return handle_api(argv)
                print(f"Unsupported gh command: {argv[0]}", file=sys.stderr)
                return 1


            if __name__ == "__main__":
                sys.exit(main())
            """
        )

        gh_path = bin_dir / "gh"
        gh_path.write_text(fake_gh, encoding="utf-8")
        gh_path.chmod(0o755)
        return bin_dir, state_dir

    def _fake_gh_env(self, bin_dir: Path, state_dir: Path, remote_bare: Path | None = None) -> dict[str, str]:
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GH_FAKE_STATE_DIR": str(state_dir),
        }
        if remote_bare is not None:
            env["GH_FAKE_REMOTE_BARE"] = str(remote_bare)
        return env

    def _read_fake_gh_state(self, state_dir: Path) -> dict[str, object]:
        return self._read_json(state_dir / "state.json")  # type: ignore[return-value]

    def _write_fake_gh_state(self, state_dir: Path, state: dict[str, object]) -> None:
        self._write_json(state_dir / "state.json", state)

    def _read_fake_gh_calls(self, state_dir: Path) -> list[list[str]]:
        calls_path = state_dir / "calls.log"
        if not calls_path.exists():
            return []
        return [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line]

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

    def test_finalize_task_to_main_commits_merges_and_cleans_up(self) -> None:
        bare, _, repo = self._init_remote_repo_on_main()
        task_worktree = self._add_task_worktree(repo)
        self._write(task_worktree, "README.md", "task change\n")
        fake_bin, state_dir = self._install_fake_gh(bare)

        result = self._run_workflow(
            "finalize_task_to_main.sh",
            task_worktree,
            "codex/example-task",
            "--verify",
            "git diff --check",
            "--commit-message",
            "Complete task branch",
            extra_env=self._fake_gh_env(fake_bin, state_dir, bare),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Merged PR", result.stdout)
        self.assertFalse(task_worktree.exists())
        self.assertEqual(_git(["branch", "--list", "codex/example-task"], cwd=repo).stdout.strip(), "")
        self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "task change\n")
        pr_state = self._read_fake_gh_state(state_dir)
        pr = pr_state["prs"]["codex/example-task"]
        self.assertEqual(pr["state"], "MERGED")
        calls = self._read_fake_gh_calls(state_dir)
        self.assertTrue(any(call[:2] == ["pr", "create"] for call in calls))
        merge_call = next(call for call in calls if call[:2] == ["pr", "merge"])
        self.assertIn("--squash", merge_call)
        self.assertIn("--auto", merge_call)
        self.assertNotIn("--delete-branch", merge_call)

    def test_finalize_task_to_main_refreshes_branch_from_latest_origin_main(self) -> None:
        bare, seed, repo = self._init_remote_repo_on_main()
        task_worktree = self._add_task_worktree(repo)
        self._write(task_worktree, "README.md", "task change\n")
        self._advance_origin_main(seed, "main.txt")
        fake_bin, state_dir = self._install_fake_gh(bare)

        result = self._run_workflow(
            "finalize_task_to_main.sh",
            task_worktree,
            "codex/example-task",
            "--verify",
            "git diff --check",
            extra_env=self._fake_gh_env(fake_bin, state_dir, bare),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue((repo / "main.txt").exists())
        self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "task change\n")

    def test_finalize_task_to_main_reuses_existing_open_pr(self) -> None:
        bare, _, repo = self._init_remote_repo_on_main()
        task_worktree = self._add_task_worktree(repo)
        self._write(task_worktree, "README.md", "task change\n")
        fake_bin, state_dir = self._install_fake_gh(bare)
        state = self._read_fake_gh_state(state_dir)
        state["next_pr"] = 8
        state["prs"]["codex/example-task"] = {
            "number": 7,
            "url": "https://example.test/pr/7",
            "title": "Existing task PR",
            "body": "## Summary\n- existing\n\n## Test Plan\n- git diff --check",
            "state": "OPEN",
            "isDraft": False,
            "headRefName": "codex/example-task",
            "baseRefName": "main",
            "mergedAt": None,
            "mergeCommit": None,
        }
        self._write_fake_gh_state(state_dir, state)

        result = self._run_workflow(
            "finalize_task_to_main.sh",
            task_worktree,
            "codex/example-task",
            "--verify",
            "git diff --check",
            extra_env=self._fake_gh_env(fake_bin, state_dir, bare),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._read_fake_gh_calls(state_dir)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in calls))
        self.assertTrue(any(call[:2] == ["pr", "merge"] for call in calls))

    def test_finalize_task_to_main_times_out_when_lock_is_held(self) -> None:
        bare, _, repo = self._init_remote_repo_on_main()
        task_worktree = self._add_task_worktree(repo)
        fake_bin, state_dir = self._install_fake_gh(bare)
        lock_dir = repo / ".git" / "codex-finalize-main.lock"
        lock_dir.mkdir()

        result = self._run_workflow(
            "finalize_task_to_main.sh",
            task_worktree,
            "codex/example-task",
            "--verify",
            "git diff --check",
            extra_env={
                **self._fake_gh_env(fake_bin, state_dir, bare),
                "CODEX_FINALIZE_LOCK_TIMEOUT_SECONDS": "1",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Timed out acquiring the main finalization lock", result.stderr)

    def test_bootstrap_main_repo_policy_applies_and_verifies_repo_settings(self) -> None:
        repo = self._init_repo()
        fake_bin, state_dir = self._install_fake_gh()

        apply_result = self._run_workflow(
            "bootstrap_main_repo_policy.sh",
            repo,
            extra_env=self._fake_gh_env(fake_bin, state_dir),
        )
        self.assertEqual(apply_result.returncode, 0, msg=apply_result.stderr)

        verify_result = self._run_workflow(
            "bootstrap_main_repo_policy.sh",
            repo,
            "--verify-only",
            extra_env=self._fake_gh_env(fake_bin, state_dir),
        )
        self.assertEqual(verify_result.returncode, 0, msg=verify_result.stderr)

        state = self._read_fake_gh_state(state_dir)
        self.assertTrue(state["repo"]["allow_auto_merge"])
        self.assertTrue(state["repo"]["delete_branch_on_merge"])
        self.assertTrue(state["repo"]["allow_squash_merge"])
        self.assertFalse(state["repo"]["allow_merge_commit"])
        self.assertFalse(state["repo"]["allow_rebase_merge"])
        self.assertEqual(len(state["rulesets"]), 1)
        self.assertEqual(state["rulesets"][0]["name"], "codex-main-direct-pr")
        self.assertEqual(
            state["rulesets"][0]["rules"][0]["parameters"]["allowed_merge_methods"],
            ["squash"],
        )


if __name__ == "__main__":
    unittest.main()
