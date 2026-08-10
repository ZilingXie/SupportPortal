#!/usr/bin/env python3
"""Compatibility entrypoint for the Automated Account Case rerun operation."""

from __future__ import annotations

from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.ops.rerun_automated_account_cases import cli  # noqa: E402


if __name__ == "__main__":
    cli()
