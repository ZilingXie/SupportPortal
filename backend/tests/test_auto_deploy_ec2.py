from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ops" / "auto_deploy_ec2.sh"
SYSTEMD_DIR = REPO_ROOT / "deployment" / "systemd"


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


class AutoDeployEc2Tests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.remote_bare, self.seed, self.repo = self._init_remote_repo_on_main()
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                NGINX_HOST_PORT=18080
                DEPLOY_REPORT_TIMEZONE=Asia/Shanghai
                DEPLOY_REPORT_ENABLE_AI=true
                """
            ),
        )
        self._write(self.repo, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self.deploy_script = self.root / "fake-deploy-ec2.sh"
        self._install_fake_commands()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, root: Path, relative_path: str, content: str) -> None:
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _commit_all(self, repo: Path, message: str) -> None:
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", message], cwd=repo)

    def _init_remote_repo_on_main(self) -> tuple[Path, Path, Path]:
        bare = self.root / "origin.git"
        _git(["init", "--bare", str(bare)], cwd=self.root)
        _git(["symbolic-ref", "HEAD", "refs/heads/main"], cwd=bare)

        seed = self.root / "seed"
        _git(["clone", str(bare), str(seed)], cwd=self.root)
        _git(["config", "user.name", "Auto Deploy Tester"], cwd=seed)
        _git(["config", "user.email", "auto-deploy@example.com"], cwd=seed)
        self._write(seed, "README.md", "initial\n")
        self._write(seed, ".gitignore", ".env\n.deploy_ec2.lock\n")
        self._write(seed, "deployment/docker-compose.single-host.yml", "services: {}\n")
        self._commit_all(seed, "Initial commit")
        _git(["push", "origin", "main"], cwd=seed)

        repo = self.root / "repo"
        _git(["clone", str(bare), str(repo)], cwd=self.root)
        _git(["config", "user.name", "Auto Deploy Tester"], cwd=repo)
        _git(["config", "user.email", "auto-deploy@example.com"], cwd=repo)
        return bare, seed, repo

    def _advance_origin_main(self) -> None:
        self._write(self.seed, "README.md", "advanced\n")
        self._commit_all(self.seed, "Advance origin main")
        _git(["push", "origin", "main"], cwd=self.seed)

    def _install_fake_commands(self) -> None:
        self._write_executable(
            self.deploy_script,
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail
                state_dir="${AUTO_DEPLOY_TEST_STATE_DIR:?}"
                printf '%s\n' "$*" >> "${state_dir}/deploy_calls.log"
                printf '%s\n' "${DEPLOY_LOCK_ALREADY_HELD:-}" > "${state_dir}/deploy_lock_already_held.txt"
                pwd > "${state_dir}/deploy_pwd.txt"
                exit "${FAKE_DEPLOY_EXIT_CODE:-0}"
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

                state_dir = Path(os.environ["AUTO_DEPLOY_TEST_STATE_DIR"])
                url = sys.argv[-1]
                with (state_dir / "curl_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"argv": sys.argv[1:], "url": url}) + "\\n")

                fail_urls = {
                    item.strip()
                    for item in os.environ.get("FAKE_CURL_FAIL_URLS", "").split(",")
                    if item.strip()
                }
                if url in fail_urls:
                    print(f"curl: simulated failure for {url}", file=sys.stderr)
                    sys.exit(22)

                print('{"status":"ok"}')
                """
            ),
        )

        self._write_executable(
            self.fake_bin / "aws",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["AUTO_DEPLOY_TEST_STATE_DIR"])
                payload = None
                args = sys.argv[1:]
                for index, value in enumerate(args):
                    if value == "--cli-input-json" and index + 1 < len(args):
                        payload_arg = args[index + 1]
                        if payload_arg.startswith("file://"):
                            payload = json.loads(Path(payload_arg[7:]).read_text(encoding="utf-8"))
                with (state_dir / "aws_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"argv": args, "payload": payload}) + "\\n")

                if os.environ.get("FAKE_AWS_SHOULD_FAIL") == "1":
                    print("aws: simulated failure", file=sys.stderr)
                    sys.exit(1)

                print('{"MessageId":"test-message"}')
                """
            ),
        )

        self._write_executable(
            self.fake_bin / "docker",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["AUTO_DEPLOY_TEST_STATE_DIR"])
                args = sys.argv[1:]
                with (state_dir / "docker_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"argv": args}) + "\\n")

                if args[:1] != ["compose"]:
                    print("unexpected docker invocation", args, file=sys.stderr)
                    sys.exit(1)

                if "ps" in args:
                    print(os.environ.get("FAKE_DOCKER_PS_OUTPUT", "NAME\\napi up"))
                    sys.exit(0)

                if "logs" in args:
                    print(
                        os.environ.get(
                            "FAKE_DOCKER_LOGS_OUTPUT",
                            "api | INFO startup complete\\nworker | WARNING queue depth high",
                        )
                    )
                    sys.exit(0)

                print("unsupported docker compose invocation", args, file=sys.stderr)
                sys.exit(1)
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

        self._write_executable(
            self.fake_bin / "hostname",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                printf '%s\n' "test-host.example"
                """
            ),
        )

    def _script_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["AUTO_DEPLOY_REPO_ROOT"] = str(self.repo)
        env["AUTO_DEPLOY_DEPLOY_SCRIPT"] = str(self.deploy_script)
        env["AUTO_DEPLOY_LOCK_FILE"] = str(self.root / "auto-deploy.lock")
        env["AUTO_DEPLOY_TEST_STATE_DIR"] = str(self.state_dir)
        env["DEPLOY_BRANCH"] = "main"
        env["DEPLOY_DOMAIN"] = "support.stellarix.space"
        env["DEPLOY_ALERT_TO"] = "ops@example.com"
        env["DEPLOY_ALERT_FROM"] = "alerts@example.com"
        env["DEPLOY_AWS_REGION"] = "us-east-1"
        env["AUTO_DEPLOY_REPORT_NOW_UTC"] = "2026-04-03T19:00:00Z"
        if extra_env:
            env.update(extra_env)
        return env

    def _run_script(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT_PATH)],
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

    def test_health_only_mode_checks_internal_and_external_health_without_deploy(self) -> None:
        result = self._run_script()

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Execution mode: health-only", result.stdout)
        self.assertFalse((self.state_dir / "deploy_calls.log").exists())
        self.assertEqual(
            [call["url"] for call in self._read_json_lines(self.state_dir / "curl_calls.jsonl")],
            [
                "http://127.0.0.1:18080/health",
                "https://support.stellarix.space/health",
            ],
        )
        aws_calls = self._read_json_lines(self.state_dir / "aws_calls.jsonl")
        self.assertEqual(len(aws_calls), 1)
        payload = aws_calls[0]["payload"]
        self.assertIsInstance(payload, dict)
        assert isinstance(payload, dict)
        self.assertEqual(
            payload["Content"]["Simple"]["Subject"]["Data"],
            "SupportPortal Report 4/4",
        )
        body = payload["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertIn("运行摘要", body)
        self.assertIn("执行模式：health-only", body)
        self.assertIn("AI 日志分析", body)
        self.assertIn("AI analysis unavailable", body)
        docker_calls = self._read_json_lines(self.state_dir / "docker_calls.jsonl")
        self.assertTrue(any("ps" in call["argv"] for call in docker_calls))
        self.assertTrue(any("logs" in call["argv"] for call in docker_calls))

    def test_deploy_mode_invokes_deploy_script_when_origin_main_is_ahead(self) -> None:
        self._advance_origin_main()

        result = self._run_script()

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Execution mode: deploy", result.stdout)
        self.assertEqual(
            (self.state_dir / "deploy_calls.log").read_text(encoding="utf-8").splitlines(),
            ["--branch main --domain support.stellarix.space"],
        )
        self.assertEqual(
            (self.state_dir / "deploy_lock_already_held.txt").read_text(encoding="utf-8").strip(),
            "1",
        )
        self.assertEqual(self._read_json_lines(self.state_dir / "curl_calls.jsonl"), [])
        aws_calls = self._read_json_lines(self.state_dir / "aws_calls.jsonl")
        self.assertEqual(len(aws_calls), 1)
        payload = aws_calls[0]["payload"]
        self.assertIsInstance(payload, dict)
        assert isinstance(payload, dict)
        self.assertEqual(payload["Content"]["Simple"]["Subject"]["Data"], "SupportPortal Report 4/4")
        self.assertIn("执行模式：deploy", payload["Content"]["Simple"]["Body"]["Text"]["Data"])

    def test_failed_health_check_sends_ses_alert_with_context(self) -> None:
        result = self._run_script(
            {
                "FAKE_CURL_FAIL_URLS": "http://127.0.0.1:18080/health",
                "DEPLOY_HEALTH_TIMEOUT_SECONDS": "1",
                "DEPLOY_HEALTH_RETRY_INTERVAL_SECONDS": "1",
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state_dir / "deploy_calls.log").exists())
        aws_calls = self._read_json_lines(self.state_dir / "aws_calls.jsonl")
        self.assertEqual(len(aws_calls), 1)
        payload = aws_calls[0]["payload"]
        self.assertIsInstance(payload, dict)
        assert isinstance(payload, dict)
        self.assertEqual(payload["Destination"]["ToAddresses"], ["ops@example.com"])
        subject = payload["Content"]["Simple"]["Subject"]["Data"]
        body = payload["Content"]["Simple"]["Body"]["Text"]["Data"]
        self.assertEqual(subject, "[Failed] SupportPortal Report 4/4")
        self.assertIn("执行模式：health-only", body)
        self.assertIn("失败步骤：Internal health check", body)
        self.assertIn("分支：main", body)
        self.assertIn("可疑原始日志", body)


class AutoDeployAssetTests(unittest.TestCase):
    def test_gitignore_ignores_deploy_lock_file(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn(".deploy_ec2.lock", gitignore)

    def test_systemd_assets_define_expected_contract(self) -> None:
        service = (SYSTEMD_DIR / "supportportal-auto-deploy.service").read_text(encoding="utf-8")
        timer = (SYSTEMD_DIR / "supportportal-auto-deploy.timer").read_text(encoding="utf-8")
        env_example = (SYSTEMD_DIR / "auto-deploy.env.example").read_text(encoding="utf-8")

        self.assertIn("Type=oneshot", service)
        self.assertIn("WorkingDirectory=/opt/supportportal/SupportPortal", service)
        self.assertIn("EnvironmentFile=/etc/supportportal/auto-deploy.env", service)
        self.assertIn("ExecStart=/opt/supportportal/SupportPortal/scripts/ops/auto_deploy_ec2.sh", service)
        self.assertIn("User=ubuntu", service)

        self.assertIn("Unit=supportportal-auto-deploy.service", timer)
        self.assertIn("OnCalendar=*-*-* 19:00:00 UTC", timer)
        self.assertIn("Persistent=true", timer)

        self.assertIn("DEPLOY_BRANCH=main", env_example)
        self.assertIn("DEPLOY_DOMAIN=support.stellarix.space", env_example)
        self.assertIn("DEPLOY_ALERT_TO=", env_example)
        self.assertIn("DEPLOY_ALERT_FROM=", env_example)
        self.assertIn("DEPLOY_AWS_REGION=", env_example)
        self.assertIn("DEPLOY_REPORT_ENABLE_AI=", env_example)
        self.assertIn("DEPLOY_REPORT_LOG_SINCE=", env_example)
        self.assertIn("DEPLOY_REPORT_LOG_LINES_PER_SERVICE=", env_example)
        self.assertIn("DEPLOY_REPORT_MAX_LOG_CHARS=", env_example)


if __name__ == "__main__":
    unittest.main()
