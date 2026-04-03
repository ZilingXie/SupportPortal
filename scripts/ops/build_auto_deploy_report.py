#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.auto_deploy_report import generate_report_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build SupportPortal auto-deploy SES report payload.")
    parser.add_argument("--context-file", required=True)
    parser.add_argument("--output-file", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--compose-file", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = generate_report_payload(
        context_file=Path(args.context_file),
        project_root=Path(args.project_root),
        env_file=Path(args.env_file),
        compose_file=Path(args.compose_file),
    )
    Path(args.output_file).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
