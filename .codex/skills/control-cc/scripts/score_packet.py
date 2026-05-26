#!/usr/bin/env python3
"""Legacy v2 scorer for strict control-cc worker packets."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


BOOL_TRUE = {"1", "true", "yes", "y", "on"}
FIELD_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


def parse_fields(markdown: str) -> dict[str, str]:
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        match = FIELD_RE.match(line)
        if match:
            current = match.group(1).strip().casefold().replace("-", "_")
            fields[current] = [match.group(2).strip()] if match.group(2).strip() else []
            continue
        if current and line.strip():
            fields[current].append(line.strip().lstrip("-*").strip())
        elif not line.strip():
            current = None
    return {key: "\n".join(value).strip() for key, value in fields.items()}


def field_bool(fields: dict[str, str], name: str) -> bool:
    return fields.get(name, "").strip().casefold() in BOOL_TRUE


def split_paths(value: str) -> list[str]:
    paths: list[str] = []
    for line in value.replace(",", "\n").splitlines():
        path = line.strip().lstrip("-*").strip()
        if path and path.casefold() != "read-only":
            paths.append(path)
    return paths


def production_paths(paths: list[str]) -> list[str]:
    production: list[str] = []
    for path in paths:
        if path.startswith(("docs/", "tests/", "backend/tests/", "ui/tests/", ".codex/", ".claude/")):
            continue
        if path.endswith((".md", ".rst", ".txt", ".yaml", ".yml")):
            continue
        production.append(path)
    return production


def score_packet(fields: dict[str, str], args: argparse.Namespace) -> dict[str, object]:
    paths = split_paths(fields.get("write_scope", ""))
    reasons: list[str] = []
    score = 0

    checks = [
        ("shared_core_file", 2, args.shared_core_file or field_bool(fields, "shared_core_file")),
        ("multi_stage_flow", 2, args.multi_stage_flow or field_bool(fields, "multi_stage_flow")),
        ("runtime_state", 3, args.runtime_state or field_bool(fields, "runtime_state")),
        ("semantic_test_reinterpretation", 2, args.semantic_tests or field_bool(fields, "semantic_tests")),
        ("docs_or_finalization_in_scope", 1, args.docs_in_scope or field_bool(fields, "docs_in_scope")),
        ("broad_write_scope", 2, args.broad_write_scope or field_bool(fields, "broad_write_scope")),
    ]

    if len(production_paths(paths)) > 2 and not any(name == "broad_write_scope" and enabled for name, _, enabled in checks):
        checks.append(("broad_write_scope", 2, True))

    for reason, points, enabled in checks:
        if enabled:
            score += points
            reasons.append(reason)

    packet_type = args.packet_type or fields.get("packet_type") or "unspecified"
    normalized_packet_type = packet_type.strip().casefold()
    if score <= 4 and normalized_packet_type == "atomic writing packet":
        decision = "writing_allowed"
        allowed = ["atomic writing packet", "read-only probe"]
    elif normalized_packet_type == "read-only probe":
        decision = "probe_allowed"
        allowed = ["read-only probe"]
    elif score <= 4:
        decision = "needs_packet_type"
        allowed = ["atomic writing packet", "read-only probe"]
    else:
        decision = "split_required"
        allowed = ["read-only probe"]

    return {
        "score": score,
        "decision": decision,
        "reasons": reasons,
        "packet_type": packet_type,
        "write_scope": paths,
        "allowed_packet_types": allowed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-plan-file", type=Path, required=True)
    parser.add_argument("--packet-type", choices=["atomic writing packet", "read-only probe"], default=None)
    parser.add_argument("--shared-core-file", action="store_true")
    parser.add_argument("--multi-stage-flow", action="store_true")
    parser.add_argument("--runtime-state", action="store_true")
    parser.add_argument("--semantic-tests", action="store_true")
    parser.add_argument("--docs-in-scope", action="store_true")
    parser.add_argument("--broad-write-scope", action="store_true")
    parser.add_argument("--fail-on-blocked", action="store_true")
    args = parser.parse_args()

    fields = parse_fields(args.task_plan_file.read_text(encoding="utf-8"))
    result = score_packet(fields, args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on_blocked and result["decision"] == "split_required":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
