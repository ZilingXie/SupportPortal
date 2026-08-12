#!/usr/bin/env python3
"""Read-only Account rerun incident audit and recovery-readiness command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
load_dotenv(REPOSITORY_ROOT / ".env", override=False)

from backend.repositories.ticket_repository import create_ticket_repository  # noqa: E402
from backend.services.account_rerun_recovery import (  # noqa: E402
    build_recovery_manifest,
    recovery_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--readiness", action="store_true", help="print readiness only")
    args = parser.parse_args()
    repository = create_ticket_repository()
    try:
        manifest = build_recovery_manifest(args.job_id, repository=repository)
        result: dict[str, Any] = recovery_readiness(manifest) if args.readiness else {
            **manifest,
            "readiness": recovery_readiness(manifest),
        }
        print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
        return 0 if result.get("ready", result.get("readiness", {}).get("ready", False)) else 2
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
