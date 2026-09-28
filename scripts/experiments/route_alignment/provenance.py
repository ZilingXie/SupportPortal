"""Provenance helpers bound to the loaded experiment source tree."""

from __future__ import annotations

import subprocess
from pathlib import Path


def source_code_commit(source_file: str | Path) -> str:
    """Return the commit containing the loaded source file, independent of cwd."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(Path(source_file).resolve().parent), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip() or "unverified"
    except (OSError, subprocess.SubprocessError):
        return "unverified"
