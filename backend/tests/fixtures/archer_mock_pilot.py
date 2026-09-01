#!/usr/bin/env python3
"""Small Pilot test double for enable_cross_channel_hosting.py."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


SCENARIO = os.environ.get("MOCK_SCENARIO", "create")
STATE_PATH = Path(os.environ["MOCK_STATE"])
LOG_PATH = Path(os.environ["MOCK_LOG"])


def emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def initial_state(appid: str) -> dict[str, object]:
    if SCENARIO == "update":
        config: dict[str, object] | None = {
            "appKey": appid,
            "status": 0,
            "region": 1,
            "maxSubscribeLoad": 10,
        }
    elif SCENARIO == "already_enabled":
        config = {
            "appKey": appid,
            "status": 1,
            "region": 2,
            "maxSubscribeLoad": 50,
        }
    else:
        config = None
    return {"config": config}


def load_state(appid: str) -> dict[str, object]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state = initial_state(appid)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    return state


def save_state(state: dict[str, object]) -> None:
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 4 or args[:2] != ["archer", "call"]:
        print("unexpected mock pilot invocation", file=sys.stderr)
        return 2

    method, path = args[2], args[3]
    body = None
    if len(args) > 4 and not args[4].startswith("--"):
        body = json.loads(args[4])

    parsed = urlparse(path)
    appid = parse_qs(parsed.query).get("keywords", ["a" * 32])[0]
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"method": method, "path": path, "body": body}) + "\n")

    if parsed.path == "/api/v2/check-simple-vendor":
        if SCENARIO == "not_found":
            emit({"data": None, "message": "查无项目"})
        else:
            emit(
                {
                    "data": {
                        "appId": appid,
                        "vendorId": 1322905,
                        "companyId": 1138100,
                        "projectId": 7001,
                        "projectName": "Mock Project",
                    }
                }
            )
        return 0

    if parsed.path == "/api/v2/search-project":
        emit(
            {
                "data": [
                    {
                        "appId": appid,
                        "id": 7001,
                        "name": "Mock Project",
                        "vendorId": 1322905,
                        "companyId": 1138100,
                    }
                ]
            }
        )
        return 0

    state = load_state(appid)
    if parsed.path == "/api/v2/agora-config/uap-app/6/uap" and method == "GET":
        config = state["config"]
        emit({"data": [] if config is None else [config]})
        return 0

    if parsed.path.endswith("/uap-type/6") and method in {"POST", "PUT"}:
        if SCENARIO != "readback_mismatch":
            if method == "POST":
                state["config"] = body
            else:
                current = state["config"] or {"appKey": appid}
                assert isinstance(current, dict)
                current.update(body)
                state["config"] = current
            save_state(state)
        emit({"data": {"success": True}})
        return 0

    print(f"unexpected mock endpoint: {method} {path}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

