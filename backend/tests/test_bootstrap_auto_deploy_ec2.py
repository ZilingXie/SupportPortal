from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "ops" / "bootstrap_auto_deploy_ec2.sh"


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


class BootstrapAutoDeployEc2Tests(unittest.TestCase):
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
        self.fake_aws_source = self.root / "fake-aws-source"
        self.fake_unzip_source = self.root / "fake-unzip-source"
        self.etc_dir = self.root / "etc-supportportal"
        self.systemd_dir = self.root / "systemd"
        self.remote_bare, self.seed, self.repo = self._init_remote_repo_on_main()
        self._copy_systemd_templates()
        self._write(
            self.repo,
            ".env",
            textwrap.dedent(
                """\
                NGINX_HOST_PORT=8080
                DEPLOY_DOMAIN=support.stellarix.space
                AWS_REGION=us-east-1
                ALERT_FROM_EMAIL=alerts@example.com
                ALERT_TO_EMAIL=ops@example.com
                """
            ),
        )
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
        _git(["config", "user.name", "Bootstrap Tester"], cwd=seed)
        _git(["config", "user.email", "bootstrap@example.com"], cwd=seed)
        self._write(seed, "README.md", "initial\n")
        self._write(seed, ".gitignore", ".env\n")
        self._commit_all(seed, "Initial commit")
        _git(["push", "origin", "main"], cwd=seed)

        repo = self.root / "repo"
        _git(["clone", str(bare), str(repo)], cwd=self.root)
        _git(["config", "user.name", "Bootstrap Tester"], cwd=repo)
        _git(["config", "user.email", "bootstrap@example.com"], cwd=repo)
        return bare, seed, repo

    def _copy_systemd_templates(self) -> None:
        source = REPO_ROOT / "deployment" / "systemd"
        destination = self.repo / "deployment" / "systemd"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)

    def _install_fake_commands(self) -> None:
        self.fake_aws_source.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["BOOTSTRAP_TEST_STATE_DIR"])
                args = sys.argv[1:]
                with (state_dir / "aws_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"argv": args}) + "\\n")

                if args and args[0] == "--version":
                    print("aws-cli/2.17.0 Python/3.11 botocore/2.0.0")
                    sys.exit(0)

                if args[:2] == ["sts", "get-caller-identity"]:
                    if os.environ.get("FAKE_AWS_STS_FAIL") == "1":
                        print("missing credentials", file=sys.stderr)
                        sys.exit(1)
                    print('{"Account":"123456789012","Arn":"arn:aws:iam::123456789012:role/test","UserId":"test"}')
                    sys.exit(0)

                if args[:2] == ["sesv2", "get-email-identity"]:
                    identity = args[args.index("--email-identity") + 1]
                    created_file = Path(os.environ["BOOTSTRAP_TEST_CREATED_IDENTITIES"])
                    created = set()
                    if created_file.exists():
                        created = {line.strip() for line in created_file.read_text(encoding="utf-8").splitlines() if line.strip()}
                    if identity in created:
                        print(json.dumps({"IdentityType": "EMAIL_ADDRESS", "VerifiedForSendingStatus": False}))
                        sys.exit(0)
                    sys.exit(255)

                if args[:2] == ["sesv2", "create-email-identity"]:
                    identity = args[args.index("--email-identity") + 1]
                    created_file = Path(os.environ["BOOTSTRAP_TEST_CREATED_IDENTITIES"])
                    with created_file.open("a", encoding="utf-8") as handle:
                        handle.write(identity + "\\n")
                    print(json.dumps({"IdentityType": "EMAIL_ADDRESS"}))
                    sys.exit(0)

                if args[:2] == ["sesv2", "get-account"]:
                    print(json.dumps({"ProductionAccessEnabled": False}))
                    sys.exit(0)

                print("unsupported aws invocation", args, file=sys.stderr)
                sys.exit(1)
                """
            ),
            encoding="utf-8",
        )
        self.fake_aws_source.chmod(0o755)

        self._write_executable(
            self.fake_bin / "sudo",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                state_dir="${BOOTSTRAP_TEST_STATE_DIR:?}"
                printf '%s\n' "$*" >> "${state_dir}/sudo_calls.log"
                exec "$@"
                """
            ),
        )

        self._write_executable(
            self.fake_bin / "apt-get",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import os
                import sys
                from pathlib import Path

                state_dir = Path(os.environ["BOOTSTRAP_TEST_STATE_DIR"])
                with (state_dir / "apt_get_calls.log").open("a", encoding="utf-8") as handle:
                    handle.write(" ".join(sys.argv[1:]) + "\\n")

                if os.environ.get("BOOTSTRAP_TEST_INSTALL_UNZIP") == "1" and "install" in sys.argv[1:]:
                    fake_bin = Path(os.environ["BOOTSTRAP_TEST_FAKE_BIN"])
                    unzip = fake_bin / "unzip"
                    unzip.write_text(Path(os.environ["BOOTSTRAP_FAKE_UNZIP_SOURCE"]).read_text(encoding="utf-8"), encoding="utf-8")
                    unzip.chmod(0o755)

                sys.exit(0)
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

                state_dir = Path(os.environ["BOOTSTRAP_TEST_STATE_DIR"])
                args = sys.argv[1:]
                with (state_dir / "curl_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"argv": args}) + "\\n")

                if "-o" in args:
                    output = Path(args[args.index("-o") + 1])
                    output.write_text("fake aws zip", encoding="utf-8")

                sys.exit(0)
                """
            ),
        )

        fake_unzip = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import os
            import sys
            from pathlib import Path

            state_dir = Path(os.environ["BOOTSTRAP_TEST_STATE_DIR"])
            with (state_dir / "unzip_calls.log").open("a", encoding="utf-8") as handle:
                handle.write(" ".join(sys.argv[1:]) + "\\n")

            install_dir = Path("aws")
            install_dir.mkdir(parents=True, exist_ok=True)
            install_script = install_dir / "install"
            install_script.write_text(
                "#!/usr/bin/env bash\\n"
                "state_dir=\\\"${BOOTSTRAP_TEST_STATE_DIR:?}\\\"\\n"
                "printf '%s\\\\n' \\\"$*\\\" >> \\\"${state_dir}/aws_install_calls.log\\\"\\n"
                "cp \\\"${BOOTSTRAP_FAKE_AWS_SOURCE:?}\\\" \\\"${BOOTSTRAP_TEST_FAKE_BIN:?}/aws\\\"\\n"
                "chmod +x \\\"${BOOTSTRAP_TEST_FAKE_BIN:?}/aws\\\"\\n",
                encoding="utf-8",
            )
            install_script.chmod(0o755)
            """
        )
        self.fake_unzip_source.write_text(fake_unzip, encoding="utf-8")
        self.fake_unzip_source.chmod(0o755)

        self._write_executable(
            self.fake_bin / "unzip",
            textwrap.dedent(
                fake_unzip
            ),
        )

        self._write_executable(
            self.fake_bin / "systemctl",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                state_dir="${BOOTSTRAP_TEST_STATE_DIR:?}"
                printf '%s\n' "$*" >> "${state_dir}/systemctl_calls.log"
                exit 0
                """
            ),
        )

    def _script_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["USER"] = "ubuntu"
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["BOOTSTRAP_TEST_STATE_DIR"] = str(self.state_dir)
        env["BOOTSTRAP_TEST_CREATED_IDENTITIES"] = str(self.state_dir / "created_identities.txt")
        env["BOOTSTRAP_TEST_FAKE_BIN"] = str(self.fake_bin)
        env["BOOTSTRAP_FAKE_AWS_SOURCE"] = str(self.fake_aws_source)
        env["BOOTSTRAP_FAKE_UNZIP_SOURCE"] = str(self.fake_unzip_source)
        env["BOOTSTRAP_REPO_ROOT"] = str(self.repo)
        env["BOOTSTRAP_AUTO_DEPLOY_ETC_DIR"] = str(self.etc_dir)
        env["BOOTSTRAP_SYSTEMD_TARGET_DIR"] = str(self.systemd_dir)
        if extra_env:
            env.update(extra_env)
        return env

    def _run_script(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(BOOTSTRAP_SCRIPT)],
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

    def test_bootstrap_script_installs_cli_creates_ses_identities_and_writes_systemd_config(self) -> None:
        result = self._run_script()

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Bootstrapping EC2 auto deploy", result.stdout)
        self.assertIn("SES account is still in sandbox mode", result.stdout)

        auto_deploy_env = (self.etc_dir / "auto-deploy.env").read_text(encoding="utf-8")
        self.assertIn("DEPLOY_BRANCH=main", auto_deploy_env)
        self.assertIn("DEPLOY_DOMAIN=support.stellarix.space", auto_deploy_env)
        self.assertIn("DEPLOY_ALERT_FROM=alerts@example.com", auto_deploy_env)
        self.assertIn("DEPLOY_ALERT_TO=ops@example.com", auto_deploy_env)
        self.assertIn("DEPLOY_AWS_REGION=us-east-1", auto_deploy_env)

        service = (self.systemd_dir / "supportportal-auto-deploy.service").read_text(encoding="utf-8")
        timer = (self.systemd_dir / "supportportal-auto-deploy.timer").read_text(encoding="utf-8")
        self.assertIn(f"WorkingDirectory={self.repo}", service)
        self.assertIn(f"ExecStart={self.repo}/scripts/ops/auto_deploy_ec2.sh", service)
        self.assertIn("User=ubuntu", service)
        self.assertIn("OnCalendar=*-*-* 19:00:00 UTC", timer)

        aws_calls = self._read_json_lines(self.state_dir / "aws_calls.jsonl")
        self.assertTrue(any(call["argv"][:2] == ["sts", "get-caller-identity"] for call in aws_calls))
        create_calls = [
            call for call in aws_calls if call["argv"][:2] == ["sesv2", "create-email-identity"]
        ]
        self.assertEqual(len(create_calls), 2)
        self.assertTrue(any("--email-identity" in call["argv"] and "alerts@example.com" in call["argv"] for call in create_calls))
        self.assertTrue(any("--email-identity" in call["argv"] and "ops@example.com" in call["argv"] for call in create_calls))

        self.assertIn("install -y curl unzip ca-certificates git", (self.state_dir / "apt_get_calls.log").read_text(encoding="utf-8"))
        self.assertTrue((self.state_dir / "aws_install_calls.log").exists())
        systemctl_calls = (self.state_dir / "systemctl_calls.log").read_text(encoding="utf-8")
        self.assertIn("daemon-reload", systemctl_calls)
        self.assertIn("enable --now supportportal-auto-deploy.timer", systemctl_calls)

    def test_repo_env_example_mentions_bootstrap_variables(self) -> None:
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("DEPLOY_DOMAIN=support.stellarix.space", env_example)
        self.assertIn("DEPLOY_AWS_REGION=us-east-1", env_example)
        self.assertIn("DEPLOY_ALERT_FROM=", env_example)
        self.assertIn("DEPLOY_ALERT_TO=", env_example)
        self.assertIn("DEPLOY_REPORT_ENABLE_AI=true", env_example)
        self.assertIn("DEPLOY_REPORT_MODEL=gpt-5.4-mini", env_example)
        self.assertIn("DEPLOY_REPORT_LOG_SINCE=24h", env_example)

    def test_bootstrap_script_does_not_require_unzip_before_install(self) -> None:
        script = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("require_cmd unzip", script)


if __name__ == "__main__":
    unittest.main()
