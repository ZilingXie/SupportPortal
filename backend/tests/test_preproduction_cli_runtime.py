from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.testing.preproduction import __main__ as cli


ROOT = Path(__file__).resolve().parents[2]


def test_preproduction_cli_reexecs_system_python_into_project_venv() -> None:
    """Scenario discovery must not fail before --help with system Python.

    The system interpreter intentionally lacks psycopg in this environment.
    Running the real module through it proves the entry point re-execs into
    the repository interpreter before importing scenario modules.
    """
    system_python = Path("/opt/homebrew/bin/python3")
    if not system_python.exists():
        system_python = Path(shutil.which("python3") or "")
    if not system_python.is_file():
        pytest.skip("no system python available for the interpreter boundary test")
    if not cli._project_python().is_file():
        pytest.skip("repository .venv is not installed in this checkout")
    if system_python.resolve() == Path(sys.executable).resolve():
        pytest.skip("test runner is already using the system interpreter")

    env = os.environ.copy()
    env.pop("SUPPORTPORTAL_PREPRODUCTION_REEXEC", None)
    result = subprocess.run(
        [str(system_python), "-m", "scripts.testing.preproduction", "--help"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
    assert "ModuleNotFoundError: No module named 'psycopg'" not in result.stderr


def test_preproduction_cli_project_python_is_declared_runtime() -> None:
    project_python = cli._project_python()
    if not project_python.is_file():
        pytest.skip("repository .venv is not installed in this checkout")
    assert project_python.is_file()
    probe = subprocess.run(
        [str(project_python), "-c", "import psycopg, sys; print(sys.executable)"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert "psycopg" not in probe.stderr
