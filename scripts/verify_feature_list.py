#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path


EXPECTED_TITLE = "# SupportPortal 主功能清单"
EXPECTED_SECTIONS = (
    "Client 端",
    "Engineer 端",
    "Ticket Dashboard",
    "RAG Dashboard",
    "RAG",
)
EXPECTED_STATUSES = ("已完成", "未完成")


def validate_feature_list(path: Path) -> list[str]:
    errors: list[str] = []

    if not path.is_file():
        return [f"Feature list not found: {path}"]

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return [f"Feature list is empty: {path}"]

    if lines[0].strip() != EXPECTED_TITLE:
        errors.append(
            f"First line must be {EXPECTED_TITLE!r}, found {lines[0].strip()!r}."
        )

    section_order: list[str] = []
    status_order: dict[str, list[str]] = {}
    item_counts: dict[str, dict[str, int]] = {}
    current_section: str | None = None
    current_status: str | None = None

    for line in lines:
        if line.startswith("## "):
            heading = line[3:].strip()
            section_order.append(heading)
            current_section = heading
            current_status = None
            if heading in EXPECTED_SECTIONS:
                status_order.setdefault(heading, [])
                item_counts.setdefault(
                    heading,
                    {status: 0 for status in EXPECTED_STATUSES},
                )
            continue

        if line.startswith("### "):
            status = line[4:].strip()
            if current_section is None:
                errors.append(f"Status heading {status!r} appears before any section.")
                continue
            if current_section not in EXPECTED_SECTIONS:
                errors.append(
                    f"Unexpected section {current_section!r} before status {status!r}."
                )
                continue
            status_order[current_section].append(status)
            current_status = status
            continue

        if line.startswith("- ") and current_section in EXPECTED_SECTIONS and current_status in EXPECTED_STATUSES:
            item_counts[current_section][current_status] += 1

    if section_order != list(EXPECTED_SECTIONS):
        errors.append(
            "Section order must be exactly: "
            + ", ".join(EXPECTED_SECTIONS)
            + f". Found: {section_order!r}."
        )

    for section in EXPECTED_SECTIONS:
        statuses = status_order.get(section, [])
        if statuses != list(EXPECTED_STATUSES):
            errors.append(
                f"Section {section!r} must contain statuses in order {EXPECTED_STATUSES!r}, found {statuses!r}."
            )
        counts = item_counts.get(section, {})
        for status in EXPECTED_STATUSES:
            if counts.get(status, 0) < 1:
                errors.append(
                    f"Section {section!r} must contain at least one bullet under {status!r}."
                )

    return errors


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/feature_list.md")
    errors = validate_feature_list(target)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"Feature list verification passed: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
