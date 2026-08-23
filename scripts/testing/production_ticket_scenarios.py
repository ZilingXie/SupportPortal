#!/usr/bin/env python3
"""Multi-turn Zendesk regression scenario CLI (thin wrapper).

The scenario logic lives in backend.services.automation_test_scenarios and is
shared with the /automation/test console; this wrapper only loads the repo
.env, prints progress, and reports the final PASS/FAIL matrix.

Every run creates REAL Zendesk tickets. Subjects carry the [zac test] tag.

Usage:
    python3 scripts/testing/production_ticket_scenarios.py --list
    python3 scripts/testing/production_ticket_scenarios.py --check
    python3 scripts/testing/production_ticket_scenarios.py --scenario E1 [--yes]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV_PATH = Path(os.environ.get("SUPPORTPORTAL_ENV_FILE") or REPO_ROOT / ".env")


def log(message: str) -> None:
    from datetime import datetime, timezone

    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}", flush=True)


def load_env_into_process() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"missing .env at {ENV_PATH}")
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["E1", "E2", "F1", "S1", "all"])
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--check", action="store_true", help="verify DB/SMTP/IMAP reachability only")
    parser.add_argument("--turn-timeout-min", type=int, default=None)
    parser.add_argument("--approval-timeout-min", type=int, default=None)
    args = parser.parse_args()

    load_env_into_process()
    from backend.services.automation_test_scenarios import (
        AutomationTestScenarioError,
        ScenarioEngine,
    )

    if args.list or (not args.scenario and not args.check):
        for scenario_id, meta in ScenarioEngine.SCENARIOS.items():
            print(f"  {scenario_id}: {meta['description']}")
        return 0

    try:
        engine = ScenarioEngine.from_env()
    except AutomationTestScenarioError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.turn_timeout_min:
        engine.turn_timeout_min = args.turn_timeout_min
    if args.approval_timeout_min:
        engine.approval_timeout_min = args.approval_timeout_min

    if args.check:
        for channel, result in engine.connectivity_check().items():
            log(f"{channel.upper()}: {result}")
        log("all channels reachable; no emails were sent.")
        return 0

    selected = list(ScenarioEngine.SCENARIOS) if args.scenario == "all" else [args.scenario]
    if not args.yes:
        print(
            "This will send REAL emails from "
            f"{engine.sender} and create REAL Zendesk tickets:\n  {', '.join(selected)}"
        )
        if input("Continue? [yes/N] ").strip().lower() != "yes":
            print("aborted.")
            return 1

    def cli_listener(kind: str, data: dict) -> None:
        if kind == "info":
            log(data.get("message") or "")
        elif kind == "waiting":
            suffix = f" (last error: {data['last_error']})" if data.get("last_error") else ""
            log(f"… waiting for {data['description']} ({data.get('waited_seconds')}s){suffix}")
        elif kind == "approval_required":
            print("\n" + "=" * 72)
            print("MANUAL APPROVAL REQUIRED")
            print(f"  Zendesk ticket : {data['zendesk_ticket_url']}")
            print(f"  Reply (from YOUR mailbox) to the internal email whose subject starts")
            print(f"  with \"{data['internal_email_subject_prefix']}\" and include a sentence such as:")
            print(f"      {data['suggested_reply']}")
            print(f"  Waiting up to {data['timeout_min']} minutes…")
            print("=" * 72 + "\n", flush=True)

    engine.listener = cli_listener
    all_ok = True
    for scenario_id in selected:
        log(f"========== scenario {scenario_id} ==========")
        try:
            engine.run_scenario(scenario_id)
        except Exception as exc:  # noqa: BLE001 - report and continue to the matrix
            log(f"scenario {scenario_id} aborted: {exc}")
        all_ok = all_ok and engine.all_passed()

    print("\n================ SCENARIO RESULTS ================")
    for step in engine.steps:
        mark = "✓" if step.status == "PASS" else "✗"
        print(f"  {mark} {step.step}" + (f" — {step.detail}" if step.detail else ""))
    print("  clean up test tickets in Zendesk afterwards (subjects tagged [zac test]).")
    print("==================================================")
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
