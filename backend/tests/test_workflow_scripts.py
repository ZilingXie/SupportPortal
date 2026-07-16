from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import textwrap
import threading
import time
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


class _FakePostgresSslServer:
    def __init__(self, port: int, *, response: bytes | None = b"S", hold_seconds: float = 0.0) -> None:
        self.port = port
        self.response = response
        self.hold_seconds = hold_seconds
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(32)
        self._sock.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_FakePostgresSslServer":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=1)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(1)
                conn.recv(8)
                if self.hold_seconds:
                    time.sleep(self.hold_seconds)
                if self.response is not None:
                    conn.sendall(self.response)
            except OSError:
                return


class WorkflowScriptTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _reserve_tcp_port(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
        sock.close()
        return port

    def _relay_env(
        self,
        *,
        listen_port: int,
        upstream_port: int,
        pid_path: Path,
        log_path: Path,
    ) -> dict[str, str]:
        return {
            "SUPPORTPORTAL_LOCAL_DB_RELAY_PORT": str(listen_port),
            "SUPPORTPORTAL_LOCAL_DB_RELAY_UPSTREAM_PORT": str(upstream_port),
            "SUPPORTPORTAL_LOCAL_DB_RELAY_PID_FILE": str(pid_path),
            "SUPPORTPORTAL_LOCAL_DB_RELAY_LOG_FILE": str(log_path),
        }

    def _pg_ssl_request(self, host: str, port: int) -> bytes:
        sock = socket.create_connection((host, port), timeout=5)
        try:
            sock.settimeout(5)
            sock.sendall((8).to_bytes(4, "big") + (80877103).to_bytes(4, "big"))
            return sock.recv(16)
        finally:
            sock.close()

    def _terminate_pid_file(self, path: Path) -> None:
        if not path.exists():
            return
        pid = int(path.read_text(encoding="utf-8").strip())
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass
        path.unlink(missing_ok=True)

    def _write(self, repo: Path, relative_path: str, content: str) -> None:
        destination = repo / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def _write_executable(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _valid_feature_list(self) -> str:
        return textwrap.dedent(
            """\
            # SupportPortal 主功能清单

            本文件是 SupportPortal 的唯一主功能清单。

            维护规则：
            - 只记录主功能。

            ## Client 端

            ### 已完成
            - 客户端已完成能力。

            ### 未完成
            - 客户端待补充。

            ## Engineer 端

            ### 已完成
            - 工程师端已完成能力。

            ### 未完成
            - 工程师端待补充。

            ## Ticket Dashboard

            ### 已完成
            - Ticket Dashboard 已完成能力。

            ### 未完成
            - Ticket Dashboard 待补充。

            ## RAG Dashboard

            ### 已完成
            - RAG Dashboard 已完成能力。

            ### 未完成
            - RAG Dashboard 待补充。

            ## RAG

            ### 已完成
            - RAG 已完成能力。

            ### 未完成
            - RAG 待补充。
            """
        )

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

        repo = self.root / "repo"
        _git(["clone", str(bare), str(repo)], cwd=self.root)
        _git(["config", "user.name", "Workflow Tester"], cwd=repo)
        _git(["config", "user.email", "workflow@example.com"], cwd=repo)
        return bare, seed, repo

    def _init_remote_repo_on_main(self) -> tuple[Path, Path, Path]:
        return self._init_remote_repo()

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

    def _read_json_lines(self, path: Path) -> list[dict[str, object]]:
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

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

    def _install_fake_single_host_commands(
        self,
        *,
        official_runtime_profile: str = "full",
        official_sentiment_provider: str = "model",
        official_torch_available: bool = True,
        official_image: str = "localhost/supportportal-app:test-ref",
        official_health_build_ref: str = "test-ref",
        official_runtime_build_ref: str = "test-ref",
        official_runtime_build_time: str = "2026-04-20T00:00:00Z",
        auxiliary_present: bool = False,
        auxiliary_runtime_profile: str = "local_lightweight",
        auxiliary_sentiment_provider: str = "legacy",
        auxiliary_torch_available: bool = False,
        auxiliary_image: str = "localhost/supportportal-app:local-lightweight-verify",
        auxiliary_health_build_ref: str = "aux-build-ref",
        auxiliary_runtime_build_ref: str = "aux-build-ref",
        auxiliary_runtime_build_time: str = "2026-04-20T00:00:00Z",
    ) -> tuple[Path, Path]:
        bin_dir = self.root / "restart-bin"
        state_dir = self.root / "restart-state"
        bin_dir.mkdir()
        state_dir.mkdir()

        self._write_executable(
            bin_dir / "podman-compose",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["RESTART_TEST_STATE_DIR"])
                payload = {
                    "argv": sys.argv[1:],
                    "cwd": os.getcwd(),
                    "app_build_ref": os.environ.get("APP_BUILD_REF"),
                    "app_build_time": os.environ.get("APP_BUILD_TIME"),
                    "app_runtime_image": os.environ.get("APP_RUNTIME_IMAGE"),
                    "buildah_progress": os.environ.get("BUILDAH_PROGRESS"),
                    "buildkit_progress": os.environ.get("BUILDKIT_PROGRESS"),
                    "supportportal_build_progress": os.environ.get("SUPPORTPORTAL_BUILD_PROGRESS"),
                    "supportportal_no_build_cache": os.environ.get("SUPPORTPORTAL_NO_BUILD_CACHE"),
                    "ticket_db_dsn": os.environ.get("TICKET_DB_DSN"),
                    "pgvector_dsn": os.environ.get("PGVECTOR_DSN"),
                    "ticket_db_schema": os.environ.get("TICKET_DB_SCHEMA"),
                    "pgvector_schema": os.environ.get("PGVECTOR_SCHEMA"),
                    "pgvector_table": os.environ.get("PGVECTOR_TABLE"),
                    "pgvector_dim": os.environ.get("PGVECTOR_DIM"),
                }
                with (state_dir / "podman_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload) + "\\n")

                if "ps" in sys.argv[1:]:
                    print("NAME\\napi up")
                else:
                    print("ok")
                """
            ),
        )
        self._write_executable(
            bin_dir / "curl",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["RESTART_TEST_STATE_DIR"])
                payload = {{"argv": sys.argv[1:], "url": sys.argv[-1]}}
                with (state_dir / "curl_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload) + "\\n")
                if sys.argv[-1].endswith(":18080/health"):
                    print({json.dumps({"status": "ok", "app_build": {"ref": auxiliary_health_build_ref}})!r})
                else:
                    print({json.dumps({"status": "ok", "app_build": {"ref": official_health_build_ref}})!r})
                """
            ),
        )
        self._write_executable(
            bin_dir / "podman",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["RESTART_TEST_STATE_DIR"])
                payload = {{"argv": sys.argv[1:], "cwd": os.getcwd()}}
                with (state_dir / "podman_cli_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload) + "\\n")

                args = sys.argv[1:]
                if args[:2] == ["ps", "--format"]:
                    lines = ["deployment_api_1|{official_image}"]
                    if {auxiliary_present!r}:
                        lines.append("deploymentlw_api_1|{auxiliary_image}")
                    print("\\n".join(lines))
                elif args[:2] == ["pod", "ps"]:
                    lines = ["pod_deployment"]
                    if {auxiliary_present!r}:
                        lines.append("pod_deploymentlw")
                    print("\\n".join(lines))
                elif args[:1] == ["port"]:
                    container = args[1]
                    if container == "deployment_nginx_1":
                        print("80/tcp -> 0.0.0.0:8080")
                    elif container == "deploymentlw_nginx_1":
                        print("80/tcp -> 0.0.0.0:18080")
                elif args[:1] == ["exec"]:
                    container = args[1]
                    if container == "deployment_api_1":
                        print(json.dumps({{
                            "runtime_profile": {official_runtime_profile!r},
                            "sentiment_provider": {official_sentiment_provider!r},
                            "torch_available": {official_torch_available!r},
                            "app_build_ref": {official_runtime_build_ref!r},
                            "app_build_time": {official_runtime_build_time!r},
                        }}))
                    elif container == "deploymentlw_api_1":
                        print(json.dumps({{
                            "runtime_profile": {auxiliary_runtime_profile!r},
                            "sentiment_provider": {auxiliary_sentiment_provider!r},
                            "torch_available": {auxiliary_torch_available!r},
                            "app_build_ref": {auxiliary_runtime_build_ref!r},
                            "app_build_time": {auxiliary_runtime_build_time!r},
                        }}))
                else:
                    print("")
                """
            ),
        )
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
        _, _, repo = self._init_remote_repo_on_main()
        _git(["switch", "-c", "scratch"], cwd=repo)

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

    def test_restart_single_host_stack_requires_clean_root_main(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(seed, ".env", "TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets\nPGVECTOR_DSN=postgresql://rag:test@db.local/rag\n")
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._commit_all(seed, "Add local runtime files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        self._write(repo, "README.md", "dirty\n")

        result = self._run_workflow("restart_single_host_stack.sh", repo)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Current worktree must be clean", result.stderr)

    def test_restart_single_host_stack_rebuilds_with_current_main_build_metadata(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(seed, ".env", "TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets\nPGVECTOR_DSN=postgresql://rag:test@db.local/rag\n")
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._commit_all(seed, "Add local runtime files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        result = self._run_workflow(
            "restart_single_host_stack.sh",
            repo,
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        self.assertEqual([call["argv"][-1] for call in calls], ["down", "down", "--build", "ps"])
        self.assertEqual(calls[0]["argv"][:4], ["-p", "deploymentlw", "-f", "deployment/docker-compose.single-host.yml"])
        self.assertEqual(calls[1]["argv"][:2], ["-f", "deployment/docker-compose.single-host.yml"])
        self.assertEqual(calls[2]["argv"][:2], ["-f", "deployment/docker-compose.single-host.yml"])
        self.assertEqual(calls[3]["argv"][:2], ["-f", "deployment/docker-compose.single-host.yml"])
        expected_ref = _git(["rev-parse", "--short=12", "HEAD"], cwd=repo).stdout.strip()
        for call in calls:
            self.assertEqual(call["app_build_ref"], expected_ref)
            self.assertEqual(call["app_runtime_image"], f"localhost/supportportal-app:{expected_ref}")
            self.assertTrue(str(call["app_build_time"]).strip())
        curl_calls = self._read_json_lines(state_dir / "curl_calls.jsonl")
        self.assertEqual(curl_calls[0]["url"], "http://127.0.0.1:8080/health")

    def test_restart_single_host_stack_prints_build_cache_diagnostics_and_honors_no_cache_flag(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(seed, ".env", "TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets\nPGVECTOR_DSN=postgresql://rag:test@db.local/rag\n")
        self._write(seed, "requirements.base.txt", "fastapi==0.1\n")
        self._write(seed, "requirements.ml.txt", "torch==0.1\n")
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._commit_all(seed, "Add lightweight runtime files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        result = self._run_workflow(
            "restart_single_host_stack.sh",
            repo,
            "--mode",
            "local_lightweight",
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
                "SUPPORTPORTAL_BUILD_PROGRESS": "plain",
                "SUPPORTPORTAL_NO_BUILD_CACHE": "1",
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        expected_ref = _git(["rev-parse", "--short=12", "HEAD"], cwd=repo).stdout.strip()
        expected_base_hash = _git(["hash-object", "requirements.base.txt"], cwd=repo).stdout.strip()
        expected_ml_hash = _git(["hash-object", "requirements.ml.txt"], cwd=repo).stdout.strip()
        self.assertIn("Runtime mode: local_lightweight", result.stdout)
        self.assertIn("INSTALL_ML_DEPS: 0", result.stdout)
        self.assertIn(f"Runtime image tag: {expected_ref}", result.stdout)
        self.assertIn("Build cache: disabled (SUPPORTPORTAL_NO_BUILD_CACHE=1)", result.stdout)
        self.assertIn("Build progress: plain", result.stdout)
        self.assertIn(f"requirements.base.txt: {expected_base_hash}", result.stdout)
        self.assertIn(f"requirements.ml.txt: {expected_ml_hash}", result.stdout)
        self.assertNotIn("fatal:", result.stderr.lower())
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        self.assertIn("--no-cache", calls[2]["argv"])
        self.assertEqual(calls[2]["buildah_progress"], "plain")
        self.assertEqual(calls[2]["buildkit_progress"], "plain")

    def test_restart_single_host_stack_ignores_env_local_without_use_local_env(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        listen_port = self._reserve_tcp_port()
        upstream_port = self._reserve_tcp_port()
        pid_path = self.root / "stack-default-remote.pid"
        log_path = self.root / "stack-default-remote.log"
        self._write(
            seed,
            ".env",
            "STACK_RUNTIME_MODE=full\n"
            "STACK_DB_MODE=remote\n"
            "TICKET_DB_DSN='"
            "postgresql://ticket:test@127.0.0.1:15433/tickets?sslmode=require&hostaddr=192.168.127.254'\n"
            "PGVECTOR_DSN='"
            "postgresql://rag:test@127.0.0.1:15433/rag?sslmode=require&hostaddr=192.168.127.254'\n",
        )
        self._write(
            seed,
            ".env.local",
            "STACK_RUNTIME_MODE=local_lightweight\n"
            "STACK_DB_MODE=local\n"
            "LOCAL_POSTGRES_USER=localuser\n"
            "LOCAL_POSTGRES_PASSWORD=localpass\n"
            "LOCAL_POSTGRES_DB=localdb\n"
            "LOCAL_POSTGRES_HOST_PORT=15555\n"
            "LOCAL_TICKET_DB_SCHEMA=local_ticket\n"
            "LOCAL_PGVECTOR_SCHEMA=local_rag\n"
            "LOCAL_PGVECTOR_TABLE=local_chunks\n"
            "LOCAL_PGVECTOR_DIM=768\n",
        )
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-db.yml", "services: {}\n")
        self._commit_all(seed, "Add local lightweight runtime files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        with _FakePostgresSslServer(upstream_port, response=b"S"):
            result = self._run_workflow(
                "restart_single_host_stack.sh",
                repo,
                extra_env={
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "RESTART_TEST_STATE_DIR": str(state_dir),
                    **self._relay_env(
                        listen_port=listen_port,
                        upstream_port=upstream_port,
                        pid_path=pid_path,
                        log_path=log_path,
                    ),
                },
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        self.assertEqual([call["argv"][-1] for call in calls], ["down", "down", "--build", "ps"])
        self.assertEqual(calls[0]["argv"][:4], ["-p", "deploymentlw", "-f", "deployment/docker-compose.single-host.yml"])
        expected_ref = _git(["rev-parse", "--short=12", "HEAD"], cwd=repo).stdout.strip()
        for call in calls[1:]:
            self.assertEqual(
                call["argv"][:2],
                [
                    "-f",
                    "deployment/docker-compose.single-host.yml",
                ],
            )
            self.assertEqual(call["app_build_ref"], expected_ref)
            self.assertEqual(call["app_runtime_image"], f"localhost/supportportal-app:{expected_ref}")
            self.assertEqual(
                call["ticket_db_dsn"],
                "postgresql://ticket:test@127.0.0.1:15433/tickets?sslmode=require&hostaddr=192.168.127.254",
            )
            self.assertEqual(
                call["pgvector_dsn"],
                "postgresql://rag:test@127.0.0.1:15433/rag?sslmode=require&hostaddr=192.168.127.254",
            )
            self.assertNotIn("deployment/docker-compose.single-host.local-lightweight.yml", call["argv"])
            self.assertNotIn("deployment/docker-compose.single-host.local-db.yml", call["argv"])
        curl_calls = self._read_json_lines(state_dir / "curl_calls.jsonl")
        self.assertEqual(curl_calls[0]["url"], "http://127.0.0.1:8080/health")
        self._terminate_pid_file(pid_path)

    def test_restart_single_host_stack_use_local_env_uses_remote_db_from_env_local(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(
            seed,
            ".env",
            "STACK_RUNTIME_MODE=full\n"
            "STACK_DB_MODE=remote\n"
            "OPENAI_API_KEY=test-key\n"
            "TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets\n"
            "PGVECTOR_DSN=postgresql://rag:test@db.local/rag\n",
        )
        self._write(
            seed,
            ".env.local",
            "STACK_RUNTIME_MODE=local_lightweight\n"
            "STACK_DB_MODE=remote\n"
            "LOCAL_POSTGRES_USER=localuser\n"
            "LOCAL_POSTGRES_PASSWORD=localpass\n"
            "LOCAL_POSTGRES_DB=localdb\n"
            "LOCAL_POSTGRES_HOST_PORT=15555\n"
            "LOCAL_TICKET_DB_SCHEMA=local_ticket\n"
            "LOCAL_PGVECTOR_SCHEMA=local_rag\n"
            "LOCAL_PGVECTOR_TABLE=local_chunks\n"
            "LOCAL_PGVECTOR_DIM=768\n",
        )
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-db.yml", "services: {}\n")
        self._commit_all(seed, "Add stack local-env runtime files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        result = self._run_workflow(
            "restart_single_host_stack.sh",
            repo,
            "--use-local-env",
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        for call in calls[1:]:
            self.assertEqual(
                call["argv"][:4],
                [
                    "-f",
                    "deployment/docker-compose.single-host.yml",
                    "-f",
                    "deployment/docker-compose.single-host.local-lightweight.yml",
                ],
            )
            self.assertNotIn("deployment/docker-compose.single-host.local-db.yml", call["argv"])
            self.assertEqual(call["ticket_db_dsn"], "postgresql://ticket:test@db.local/tickets")
            self.assertEqual(call["pgvector_dsn"], "postgresql://rag:test@db.local/rag")

    def test_restart_single_host_stack_use_local_env_and_db_local_opts_into_local_db(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(
            seed,
            ".env",
            "STACK_RUNTIME_MODE=full\n"
            "STACK_DB_MODE=remote\n"
            "OPENAI_API_KEY=test-key\n"
            "TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets\n"
            "PGVECTOR_DSN=postgresql://rag:test@db.local/rag\n",
        )
        self._write(
            seed,
            ".env.local",
            "STACK_RUNTIME_MODE=local_lightweight\n"
            "STACK_DB_MODE=local\n"
            "LOCAL_POSTGRES_USER=localuser\n"
            "LOCAL_POSTGRES_PASSWORD=localpass\n"
            "LOCAL_POSTGRES_DB=localdb\n"
            "LOCAL_POSTGRES_HOST_PORT=15555\n"
            "LOCAL_TICKET_DB_SCHEMA=local_ticket\n"
            "LOCAL_PGVECTOR_SCHEMA=local_rag\n"
            "LOCAL_PGVECTOR_TABLE=local_chunks\n"
            "LOCAL_PGVECTOR_DIM=768\n",
        )
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-db.yml", "services: {}\n")
        self._commit_all(seed, "Add stack local-db opt-in runtime files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        result = self._run_workflow(
            "restart_single_host_stack.sh",
            repo,
            "--use-local-env",
            "--db",
            "local",
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        for call in calls[1:]:
            self.assertEqual(
                call["argv"][:6],
                [
                    "-f",
                    "deployment/docker-compose.single-host.yml",
                    "-f",
                    "deployment/docker-compose.single-host.local-lightweight.yml",
                    "-f",
                    "deployment/docker-compose.single-host.local-db.yml",
                ],
            )
            self.assertEqual(
                call["ticket_db_dsn"],
                "postgresql://localuser:localpass@local_postgres:5432/localdb?sslmode=disable",
            )
            self.assertEqual(
                call["pgvector_dsn"],
                "postgresql://localuser:localpass@local_postgres:5432/localdb?sslmode=disable",
            )
            self.assertEqual(call["ticket_db_schema"], "local_ticket")
            self.assertEqual(call["pgvector_schema"], "local_rag")
            self.assertEqual(call["pgvector_table"], "local_chunks")
            self.assertEqual(call["pgvector_dim"], "768")

    def test_restart_single_host_stack_use_local_env_and_db_remote_overrides_local_db_default(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        listen_port = self._reserve_tcp_port()
        upstream_port = self._reserve_tcp_port()
        pid_path = self.root / "relay-stack-local-env-remote.pid"
        log_path = self.root / "relay-stack-local-env-remote.log"
        self._write(
            seed,
            ".env",
            "STACK_RUNTIME_MODE=full\n"
            "STACK_DB_MODE=remote\n"
            "TICKET_DB_DSN='"
            "postgresql://ticket:test@127.0.0.1:15433/tickets?sslmode=require&hostaddr=192.168.127.254'\n"
            "PGVECTOR_DSN='"
            "postgresql://rag:test@127.0.0.1:15433/rag?sslmode=require&hostaddr=192.168.127.254'\n",
        )
        self._write(
            seed,
            ".env.local",
            "STACK_RUNTIME_MODE=local_lightweight\n"
            "STACK_DB_MODE=local\n"
            "LOCAL_POSTGRES_USER=localuser\n"
            "LOCAL_POSTGRES_PASSWORD=localpass\n"
            "LOCAL_POSTGRES_DB=localdb\n",
        )
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-db.yml", "services: {}\n")
        self._commit_all(seed, "Add stack local-env remote override files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        with _FakePostgresSslServer(upstream_port, response=b"S"):
            result = self._run_workflow(
                "restart_single_host_stack.sh",
                repo,
                "--use-local-env",
                "--db",
                "remote",
                extra_env={
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "RESTART_TEST_STATE_DIR": str(state_dir),
                    **self._relay_env(
                        listen_port=listen_port,
                        upstream_port=upstream_port,
                        pid_path=pid_path,
                        log_path=log_path,
                    ),
                },
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        for call in calls[1:]:
            self.assertEqual(
                call["argv"][:4],
                [
                    "-f",
                    "deployment/docker-compose.single-host.yml",
                    "-f",
                    "deployment/docker-compose.single-host.local-lightweight.yml",
                ],
            )
            self.assertNotIn("deployment/docker-compose.single-host.local-db.yml", call["argv"])
            self.assertEqual(
                call["ticket_db_dsn"],
                "postgresql://ticket:test@127.0.0.1:15433/tickets?sslmode=require&hostaddr=192.168.127.254",
            )
            self.assertEqual(
                call["pgvector_dsn"],
                "postgresql://rag:test@127.0.0.1:15433/rag?sslmode=require&hostaddr=192.168.127.254",
            )
        self.assertTrue(pid_path.exists())
        self._terminate_pid_file(pid_path)

    def test_restart_single_host_stack_mode_full_and_db_local_override_local_env_defaults(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(
            seed,
            ".env",
            "STACK_RUNTIME_MODE=full\n"
            "STACK_DB_MODE=remote\n"
            "OPENAI_API_KEY=test-key\n"
            "TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets\n"
            "PGVECTOR_DSN=postgresql://rag:test@db.local/rag\n",
        )
        self._write(
            seed,
            ".env.local",
            "STACK_RUNTIME_MODE=local_lightweight\n"
            "STACK_DB_MODE=local\n"
            "LOCAL_POSTGRES_USER=localuser\n"
            "LOCAL_POSTGRES_PASSWORD=localpass\n"
            "LOCAL_POSTGRES_DB=localdb\n"
            "LOCAL_TICKET_DB_SCHEMA=local_ticket\n"
            "LOCAL_PGVECTOR_SCHEMA=local_rag\n"
            "LOCAL_PGVECTOR_TABLE=local_chunks\n"
            "LOCAL_PGVECTOR_DIM=768\n",
        )
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-db.yml", "services: {}\n")
        self._commit_all(seed, "Add mode-override test files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        result = self._run_workflow(
            "restart_single_host_stack.sh",
            repo,
            "--use-local-env",
            "--mode",
            "full",
            "--db",
            "local",
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        for call in calls[1:]:
            self.assertEqual(
                call["argv"][:4],
                [
                    "-f",
                    "deployment/docker-compose.single-host.yml",
                    "-f",
                    "deployment/docker-compose.single-host.local-db.yml",
                ],
            )
            self.assertNotIn("deployment/docker-compose.single-host.local-lightweight.yml", call["argv"])
            self.assertEqual(
                call["ticket_db_dsn"],
                "postgresql://localuser:localpass@local_postgres:5432/localdb?sslmode=disable",
            )
            self.assertEqual(
                call["pgvector_dsn"],
                "postgresql://localuser:localpass@local_postgres:5432/localdb?sslmode=disable",
            )

    def test_restart_single_host_stack_rejects_unknown_mode(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(seed, ".env", "OPENAI_API_KEY=test-key\n")
        self._write(seed, ".env.local", "LOCAL_POSTGRES_USER=supportportal\n")
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-db.yml", "services: {}\n")
        self._commit_all(seed, "Add invalid-mode test files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)

        result = self._run_workflow("restart_single_host_stack.sh", repo, "--mode", "bogus")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported --mode", result.stderr)

    def test_restart_single_host_stack_rejects_unknown_db_mode(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(seed, ".env", "OPENAI_API_KEY=test-key\n")
        self._write(seed, ".env.local", "LOCAL_POSTGRES_USER=supportportal\n")
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-db.yml", "services: {}\n")
        self._commit_all(seed, "Add invalid-db-mode test files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)

        result = self._run_workflow("restart_single_host_stack.sh", repo, "--db", "bogus")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported --db mode", result.stderr)

    def test_ensure_local_db_relay_noops_when_dsn_does_not_require_host_relay(self) -> None:
        repo = self._init_repo()
        self._write(
            repo,
            ".env",
            "TICKET_DB_DSN=postgresql://ticket:test@db.local:5432/tickets?sslmode=require\n"
            "PGVECTOR_DSN=postgresql://rag:test@db.local:5432/rag?sslmode=require\n",
        )
        pid_path = self.root / "relay-noop.pid"
        log_path = self.root / "relay-noop.log"

        result = self._run_workflow(
            "ensure_local_db_relay.sh",
            repo,
            extra_env=self._relay_env(
                listen_port=self._reserve_tcp_port(),
                upstream_port=self._reserve_tcp_port(),
                pid_path=pid_path,
                log_path=log_path,
            ),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Local DB relay is not required", result.stdout)
        self.assertFalse(pid_path.exists())

    def test_ensure_local_db_relay_starts_and_reuses_existing_healthy_relay(self) -> None:
        repo = self._init_repo()
        listen_port = self._reserve_tcp_port()
        upstream_port = self._reserve_tcp_port()
        pid_path = self.root / "relay.pid"
        log_path = self.root / "relay.log"
        self._write(
            repo,
            ".env",
            "TICKET_DB_DSN='"
            "postgresql://ticket:test@127.0.0.1:15433/tickets?sslmode=require&hostaddr=192.168.127.254'\n"
            "PGVECTOR_DSN='"
            "postgresql://rag:test@127.0.0.1:15433/rag?sslmode=require&hostaddr=192.168.127.254'\n",
        )

        with _FakePostgresSslServer(upstream_port, response=b"S"):
            result = self._run_workflow(
                "ensure_local_db_relay.sh",
                repo,
                extra_env=self._relay_env(
                    listen_port=listen_port,
                    upstream_port=upstream_port,
                    pid_path=pid_path,
                    log_path=log_path,
                ),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(self._pg_ssl_request("127.0.0.1", listen_port), b"S")
            pid_before = pid_path.read_text(encoding="utf-8").strip()

            second = self._run_workflow(
                "ensure_local_db_relay.sh",
                repo,
                extra_env=self._relay_env(
                    listen_port=listen_port,
                    upstream_port=upstream_port,
                    pid_path=pid_path,
                    log_path=log_path,
                ),
            )
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            self.assertIn("Reusing existing healthy local DB relay", second.stdout)
            self.assertEqual(pid_path.read_text(encoding="utf-8").strip(), pid_before)

        self._terminate_pid_file(pid_path)

    def test_ensure_local_db_relay_fails_when_unknown_listener_is_unhealthy(self) -> None:
        repo = self._init_repo()
        listen_port = self._reserve_tcp_port()
        pid_path = self.root / "relay-bad.pid"
        log_path = self.root / "relay-bad.log"
        self._write(
            repo,
            ".env",
            "TICKET_DB_DSN='"
            "postgresql://ticket:test@127.0.0.1:15433/tickets?sslmode=require&hostaddr=192.168.127.254'\n"
            "PGVECTOR_DSN='"
            "postgresql://rag:test@127.0.0.1:15433/rag?sslmode=require&hostaddr=192.168.127.254'\n",
        )

        with _FakePostgresSslServer(listen_port, response=None, hold_seconds=0.2):
            result = self._run_workflow(
                "ensure_local_db_relay.sh",
                repo,
                extra_env=self._relay_env(
                    listen_port=listen_port,
                    upstream_port=self._reserve_tcp_port(),
                    pid_path=pid_path,
                    log_path=log_path,
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("occupied by an unknown unhealthy listener", result.stderr)

    def test_restart_single_host_lightweight_stack_starts_local_db_relay_when_required(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        listen_port = self._reserve_tcp_port()
        upstream_port = self._reserve_tcp_port()
        pid_path = self.root / "relay-restart.pid"
        log_path = self.root / "relay-restart.log"
        self._write(
            seed,
            ".env",
            "TICKET_DB_DSN='"
            "postgresql://ticket:test@127.0.0.1:15433/tickets?sslmode=require&hostaddr=192.168.127.254'\n"
            "PGVECTOR_DSN='"
            "postgresql://rag:test@127.0.0.1:15433/rag?sslmode=require&hostaddr=192.168.127.254'\n",
        )
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._commit_all(seed, "Add relay-required runtime files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        with _FakePostgresSslServer(upstream_port, response=b"S"):
            result = self._run_workflow(
                "restart_single_host_lightweight_stack.sh",
                repo,
                extra_env={
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "RESTART_TEST_STATE_DIR": str(state_dir),
                    **self._relay_env(
                        listen_port=listen_port,
                        upstream_port=upstream_port,
                        pid_path=pid_path,
                        log_path=log_path,
                    ),
                },
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(pid_path.exists())
            self.assertEqual(self._pg_ssl_request("127.0.0.1", listen_port), b"S")

        self._terminate_pid_file(pid_path)

    def test_restart_single_host_local_stack_uses_local_pgvector_without_relay(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(
            seed,
            ".env",
            "OPENAI_API_KEY=test-key\n"
            "TICKET_DB_DSN='"
            "postgresql://ticket:test@127.0.0.1:15433/tickets?sslmode=require&hostaddr=192.168.127.254'\n"
            "PGVECTOR_DSN='"
            "postgresql://rag:test@127.0.0.1:15433/rag?sslmode=require&hostaddr=192.168.127.254'\n",
        )
        self._write(
            seed,
            ".env.local",
            "LOCAL_POSTGRES_USER=localuser\n"
            "LOCAL_POSTGRES_PASSWORD=localpass\n"
            "LOCAL_POSTGRES_DB=localdb\n"
            "LOCAL_POSTGRES_HOST_PORT=15555\n"
            "LOCAL_TICKET_DB_SCHEMA=local_ticket\n"
            "LOCAL_PGVECTOR_SCHEMA=local_rag\n"
            "LOCAL_PGVECTOR_TABLE=local_chunks\n"
            "LOCAL_PGVECTOR_DIM=768\n",
        )
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-db.yml", "services: {}\n")
        self._commit_all(seed, "Add local runtime files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        result = self._run_workflow(
            "restart_single_host_local_stack.sh",
            repo,
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Compatibility wrapper", result.stdout)
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        self.assertEqual([call["argv"][-1] for call in calls], ["down", "down", "--build", "ps"])
        for call in calls[1:]:
            self.assertEqual(
                call["argv"][:6],
                [
                    "-f",
                    "deployment/docker-compose.single-host.yml",
                    "-f",
                    "deployment/docker-compose.single-host.local-lightweight.yml",
                    "-f",
                    "deployment/docker-compose.single-host.local-db.yml",
                ],
            )
            self.assertEqual(
                call["ticket_db_dsn"],
                "postgresql://localuser:localpass@local_postgres:5432/localdb?sslmode=disable",
            )
            self.assertEqual(
                call["pgvector_dsn"],
                "postgresql://localuser:localpass@local_postgres:5432/localdb?sslmode=disable",
            )
            self.assertEqual(call["ticket_db_schema"], "local_ticket")
            self.assertEqual(call["pgvector_schema"], "local_rag")
            self.assertEqual(call["pgvector_table"], "local_chunks")
            self.assertEqual(call["pgvector_dim"], "768")
        curl_calls = self._read_json_lines(state_dir / "curl_calls.jsonl")
        self.assertEqual(curl_calls[0]["url"], "http://127.0.0.1:8080/health")

    def test_restart_single_host_lightweight_stack_wrapper_preserves_remote_db_defaults_without_local_env(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(
            seed,
            ".env",
            "STACK_RUNTIME_MODE=full\n"
            "STACK_DB_MODE=remote\n"
            "OPENAI_API_KEY=test-key\n"
            "TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets\n"
            "PGVECTOR_DSN=postgresql://rag:test@db.local/rag\n",
        )
        self._write(
            seed,
            ".env.local",
            "STACK_RUNTIME_MODE=local_lightweight\n"
            "STACK_DB_MODE=local\n"
            "LOCAL_POSTGRES_USER=localuser\n"
            "LOCAL_POSTGRES_PASSWORD=localpass\n"
            "LOCAL_POSTGRES_DB=localdb\n"
            "LOCAL_TICKET_DB_SCHEMA=local_ticket\n"
            "LOCAL_PGVECTOR_SCHEMA=local_rag\n"
            "LOCAL_PGVECTOR_TABLE=local_chunks\n"
            "LOCAL_PGVECTOR_DIM=768\n",
        )
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-lightweight.yml", "services: {}\n")
        self._write(seed, "deployment/docker-compose.single-host.local-db.yml", "services: {}\n")
        self._commit_all(seed, "Add lightweight-wrapper remote-default files")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        result = self._run_workflow(
            "restart_single_host_lightweight_stack.sh",
            repo,
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Compatibility wrapper", result.stdout)
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        for call in calls[1:]:
            self.assertEqual(call["argv"][:4], [
                "-f",
                "deployment/docker-compose.single-host.yml",
                "-f",
                "deployment/docker-compose.single-host.local-lightweight.yml",
            ])
            self.assertNotIn("deployment/docker-compose.single-host.local-db.yml", call["argv"])
            self.assertEqual(call["ticket_db_dsn"], "postgresql://ticket:test@db.local/tickets")
            self.assertEqual(call["pgvector_dsn"], "postgresql://rag:test@db.local/rag")

    def test_run_with_local_db_env_exports_host_side_local_dsns(self) -> None:
        repo = self._init_repo()
        self._write(repo, ".env", "OPENAI_API_KEY=test-key\n")
        self._write(
            repo,
            ".env.local",
            "LOCAL_POSTGRES_USER=hostuser\n"
            "LOCAL_POSTGRES_PASSWORD=hostpass\n"
            "LOCAL_POSTGRES_DB=hostdb\n"
            "LOCAL_POSTGRES_HOST_PORT=16666\n"
            "LOCAL_TICKET_DB_SCHEMA=host_ticket\n"
            "LOCAL_PGVECTOR_SCHEMA=host_rag\n"
            "LOCAL_PGVECTOR_TABLE=host_chunks\n"
            "LOCAL_PGVECTOR_DIM=384\n",
        )

        result = self._run_workflow(
            "run_with_local_db_env.sh",
            repo,
            "--",
            "python3",
            "-c",
            (
                "import os; print('|'.join(["
                "os.environ['TICKET_DB_DSN'], os.environ['PGVECTOR_DSN'], "
                "os.environ['TICKET_DB_SCHEMA'], os.environ['PGVECTOR_SCHEMA'], "
                "os.environ['PGVECTOR_TABLE'], os.environ['PGVECTOR_DIM']]))"
            ),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "postgresql://hostuser:hostpass@127.0.0.1:16666/hostdb?sslmode=disable|"
            "postgresql://hostuser:hostpass@127.0.0.1:16666/hostdb?sslmode=disable|"
            "host_ticket|host_rag|host_chunks|384",
        )

    def test_local_db_compose_contract_uses_pgvector_and_app_local_dsns(self) -> None:
        compose_source = Path("deployment/docker-compose.single-host.local-db.yml").read_text(encoding="utf-8")

        self.assertIn("local_postgres:", compose_source)
        self.assertIn("image: pgvector/pgvector:pg16", compose_source)
        self.assertIn("supportportal_local_pgdata:/var/lib/postgresql/data", compose_source)
        self.assertIn("healthcheck:", compose_source)
        self.assertIn("pg_isready", compose_source)
        self.assertIn('"${LOCAL_POSTGRES_HOST_PORT:-15432}:5432"', compose_source)
        self.assertIn("@local_postgres:5432/", compose_source)
        self.assertIn("TICKET_DB_DSN:", compose_source)
        self.assertIn("PGVECTOR_DSN:", compose_source)
        self.assertIn("condition: service_healthy", compose_source)
        self.assertIn("supportportal_local_pgdata:", compose_source)

    def test_local_lightweight_compose_enables_rag_kg_sandbox(self) -> None:
        compose_source = Path("deployment/docker-compose.single-host.local-lightweight.yml").read_text(encoding="utf-8")

        self.assertIn("local_neo4j:", compose_source)
        self.assertIn("image: neo4j:5-community", compose_source)
        self.assertIn("RAG_KG_AUXILIARY_ENABLED: ${RAG_KG_AUXILIARY_ENABLED:-true}", compose_source)
        self.assertIn("KG_NEO4J_URI: ${KG_NEO4J_URI:-bolt://local_neo4j:7687}", compose_source)
        self.assertIn("KG_NEO4J_USER: ${KG_NEO4J_USER:-neo4j}", compose_source)
        self.assertIn("KG_NEO4J_PASSWORD: ${KG_NEO4J_PASSWORD:-supportportal-kg-local}", compose_source)
        self.assertIn("KG_LLM_API_KEY: ${KG_LLM_API_KEY:-}", compose_source)
        self.assertIn("KG_LLM_BASE_URL: ${KG_LLM_BASE_URL:-}", compose_source)
        self.assertIn("KG_LLM_MODEL: ${KG_LLM_MODEL:-}", compose_source)
        self.assertIn("KG_EMBEDDING_API_KEY: ${KG_EMBEDDING_API_KEY:-}", compose_source)
        self.assertIn("KG_EMBEDDING_MODEL: ${KG_EMBEDDING_MODEL:-}", compose_source)
        self.assertIn("KG_EMBEDDING_BASE_URL: ${KG_EMBEDDING_BASE_URL:-}", compose_source)
        self.assertIn("KG_EMBEDDING_DIM: ${KG_EMBEDDING_DIM:-}", compose_source)
        self.assertIn(
            "NEO4J_server_memory_heap_initial__size: ${LOCAL_NEO4J_HEAP_INITIAL:-256m}", compose_source
        )
        self.assertIn("NEO4J_server_memory_heap_max__size: ${LOCAL_NEO4J_HEAP_MAX:-512m}", compose_source)
        self.assertIn("NEO4J_server_memory_pagecache_size: ${LOCAL_NEO4J_PAGECACHE:-256m}", compose_source)
        self.assertIn('NEO4J_server_http_enabled: "false"', compose_source)
        self.assertIn('"${LOCAL_NEO4J_BOLT_PORT:-17687}:7687"', compose_source)
        self.assertNotIn("7474", compose_source)
        self.assertIn("local_neo4j:", compose_source)
        self.assertIn("condition: service_healthy", compose_source)

    def test_local_env_template_keeps_remote_db_default_and_does_not_replace_online_env(self) -> None:
        gitignore_source = Path(".gitignore").read_text(encoding="utf-8")
        local_env_source = Path(".env.local.example").read_text(encoding="utf-8")

        self.assertIn(".env.local", gitignore_source)
        self.assertIn("STACK_RUNTIME_MODE=local_lightweight", local_env_source)
        self.assertIn("STACK_DB_MODE=remote", local_env_source)
        self.assertIn("LOCAL_POSTGRES_USER=supportportal", local_env_source)
        self.assertIn("LOCAL_POSTGRES_HOST_PORT=15432", local_env_source)
        self.assertIn("LOCAL_PGVECTOR_TABLE=docagent_chunks_bge_m3_1024", local_env_source)
        self.assertIn("KG_NEO4J_URI=bolt://local_neo4j:7687", local_env_source)
        self.assertIn("LOCAL_NEO4J_HEAP_INITIAL=256m", local_env_source)
        self.assertIn("LOCAL_NEO4J_HEAP_MAX=512m", local_env_source)
        self.assertIn("LOCAL_NEO4J_PAGECACHE=256m", local_env_source)
        self.assertNotIn("LOCAL_NEO4J_HTTP_PORT", local_env_source)
        self.assertIn("KG_EMBEDDING_MODEL=BAAI/bge-m3", local_env_source)
        self.assertIn("KG_EMBEDDING_DIM=1024", local_env_source)
        self.assertIn("# Use --db local to opt into local Postgres/pgvector.", local_env_source)
        self.assertNotIn("YOUR_AWS_POSTGRES_HOST", local_env_source)

    def test_cleanup_single_host_aux_stack_only_targets_auxiliary_project(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._commit_all(seed, "Add compose file")
        _git(["push", "origin", "main"], cwd=seed)
        _git(["pull", "--ff-only", "origin", "main"], cwd=repo)
        fake_bin, state_dir = self._install_fake_single_host_commands()

        result = self._run_workflow(
            "cleanup_single_host_aux_stack.sh",
            repo,
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        calls = self._read_json_lines(state_dir / "podman_calls.jsonl")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["argv"],
            ["-p", "deploymentlw", "-f", "deployment/docker-compose.single-host.yml", "down"],
        )

    def test_inspect_single_host_stack_mode_reports_full_profile(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        expected_ref = _git(["rev-parse", "--short=12", "HEAD"], cwd=repo).stdout.strip()
        fake_bin, state_dir = self._install_fake_single_host_commands(
            official_runtime_profile="full",
            official_sentiment_provider="model",
            official_torch_available=True,
            official_image=f"localhost/supportportal-app:{expected_ref}",
            official_health_build_ref=expected_ref,
            official_runtime_build_ref=expected_ref,
            auxiliary_present=False,
        )

        result = self._run_workflow(
            "inspect_single_host_stack_mode.sh",
            repo,
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("official_project=deployment", result.stdout)
        self.assertIn(f"root_main_ref={expected_ref}", result.stdout)
        self.assertIn("official_health_url=http://127.0.0.1:8080/health", result.stdout)
        self.assertIn(f"official_image=localhost/supportportal-app:{expected_ref}", result.stdout)
        self.assertIn(f"official_image_tag={expected_ref}", result.stdout)
        self.assertIn(f"official_health_build_ref={expected_ref}", result.stdout)
        self.assertIn(f"official_runtime_build_ref={expected_ref}", result.stdout)
        self.assertIn("official_runtime_profile=full", result.stdout)
        self.assertIn("official_sentiment_provider=model", result.stdout)
        self.assertIn("official_torch_available=true", result.stdout)
        self.assertIn("build_provenance_status=matched", result.stdout)
        self.assertIn("auxiliary_stack_present=false", result.stdout)

    def test_inspect_single_host_stack_mode_fails_when_auxiliary_stack_present(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        expected_ref = _git(["rev-parse", "--short=12", "HEAD"], cwd=repo).stdout.strip()
        fake_bin, state_dir = self._install_fake_single_host_commands(
            official_runtime_profile="local_lightweight",
            official_sentiment_provider="legacy",
            official_torch_available=False,
            official_image=f"localhost/supportportal-app:{expected_ref}",
            official_health_build_ref=expected_ref,
            official_runtime_build_ref=expected_ref,
            auxiliary_present=True,
        )

        result = self._run_workflow(
            "inspect_single_host_stack_mode.sh",
            repo,
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported auxiliary single-host stack detected: deploymentlw", result.stderr)

    def test_inspect_single_host_stack_mode_fails_on_build_provenance_mismatch(self) -> None:
        _, seed, repo = self._init_remote_repo_on_main()
        expected_ref = _git(["rev-parse", "--short=12", "HEAD"], cwd=repo).stdout.strip()
        fake_bin, state_dir = self._install_fake_single_host_commands(
            official_runtime_profile="full",
            official_sentiment_provider="model",
            official_torch_available=True,
            official_image="localhost/supportportal-app:stale-build",
            official_health_build_ref=expected_ref,
            official_runtime_build_ref=expected_ref,
            auxiliary_present=False,
        )

        result = self._run_workflow(
            "inspect_single_host_stack_mode.sh",
            repo,
            extra_env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RESTART_TEST_STATE_DIR": str(state_dir),
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Official single-host build provenance mismatch", result.stderr)
        self.assertIn(expected_ref, result.stderr)
        self.assertIn("stale-build", result.stderr)

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

    def test_finalize_task_to_main_ignores_stale_mac_named_prs(self) -> None:
        bare, _, repo = self._init_remote_repo_on_main()
        task_worktree = self._add_task_worktree(repo)
        self._write(task_worktree, "README.md", "task change\n")
        fake_bin, state_dir = self._install_fake_gh(bare)
        state = self._read_fake_gh_state(state_dir)
        state["next_pr"] = 2
        state["prs"]["mac"] = {
            "number": 1,
            "url": "https://example.test/pr/1",
            "title": "Stale legacy branch PR",
            "body": "legacy",
            "state": "OPEN",
            "isDraft": False,
            "headRefName": "mac",
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
        self.assertIn("Merged PR", result.stdout)

    def test_finalize_task_to_main_auto_verifies_valid_feature_list_changes(self) -> None:
        bare, _, repo = self._init_remote_repo_on_main()
        self._write(repo, "docs/feature_list.md", self._valid_feature_list())
        self._commit_all(repo, "Add feature list")
        _git(["push", "origin", "main"], cwd=repo)

        task_worktree = self._add_task_worktree(repo)
        updated = self._valid_feature_list().replace(
            "- 客户端待补充。\n",
            "- 客户端待补充。\n- 对话支持流式输出。\n",
        )
        self._write(task_worktree, "docs/feature_list.md", updated)
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
        self.assertIn("Running automatic feature list verification.", result.stdout)
        self.assertIn(
            "Feature list verification passed: docs/feature_list.md",
            result.stdout,
        )

    def test_finalize_task_to_main_fails_on_invalid_feature_list_changes(self) -> None:
        bare, _, repo = self._init_remote_repo_on_main()
        self._write(repo, "docs/feature_list.md", self._valid_feature_list())
        self._commit_all(repo, "Add feature list")
        _git(["push", "origin", "main"], cwd=repo)

        task_worktree = self._add_task_worktree(repo)
        invalid = self._valid_feature_list().replace("### 未完成\n", "### 规划中\n", 1)
        self._write(task_worktree, "docs/feature_list.md", invalid)
        fake_bin, state_dir = self._install_fake_gh(bare)

        result = self._run_workflow(
            "finalize_task_to_main.sh",
            task_worktree,
            "codex/example-task",
            "--verify",
            "git diff --check",
            extra_env=self._fake_gh_env(fake_bin, state_dir, bare),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Automatic feature list verification failed.", result.stderr)

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
