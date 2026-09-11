"""Record deploy-time release notes into the environment schema.

The deploy pipeline invokes `record` after the activation phase passes; the
Admin console reads the same table read-only via the ECS Admin API.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

import psycopg
from psycopg import sql


RELEASE_NOTES_TABLE = "support_release_notes"


def _changes_since(worktree: str, previous_commit: str | None) -> list[str]:
    """Squash PR subjects between the previous recorded release and HEAD."""
    args = ["git", "-C", worktree, "log", "--format=%s", "--no-color", "--no-decorate"]
    args.append(f"{previous_commit}..HEAD" if previous_commit else "-1")
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def record_release_note(
    *,
    dsn: str,
    schema: str,
    release_id: str,
    git_commit: str,
    build_time: str | None,
    prompt_release_id: str | None,
    image_digests: dict[str, Any],
    worktree: str,
) -> dict[str, Any]:
    if not release_id.strip() or not git_commit.strip():
        raise ValueError("release_id and git_commit are required")
    if not image_digests:
        raise ValueError("image_digests must not be empty")
    table = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(RELEASE_NOTES_TABLE))
    with psycopg.connect(dsn, application_name="supportportal-release-notes-record") as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SELECT git_commit FROM {} ORDER BY deployed_at DESC LIMIT 1").format(table)
            )
            row = cursor.fetchone()
        previous_commit = str(row[0]) if row else None
        changes = _changes_since(worktree, previous_commit)
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    INSERT INTO {} (release_id, git_commit, build_time, prompt_release_id,
                                    image_digests, changes, deployed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (release_id) DO UPDATE SET
                        git_commit = EXCLUDED.git_commit,
                        build_time = EXCLUDED.build_time,
                        prompt_release_id = EXCLUDED.prompt_release_id,
                        image_digests = EXCLUDED.image_digests,
                        changes = EXCLUDED.changes,
                        deployed_at = NOW()
                    """
                ).format(table),
                (
                    release_id,
                    git_commit,
                    build_time,
                    prompt_release_id,
                    json.dumps(image_digests, sort_keys=True),
                    json.dumps(changes),
                ),
            )
        connection.commit()
    return {
        "release_id": release_id,
        "git_commit": git_commit,
        "previous_commit": previous_commit,
        "changes": len(changes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage deploy-time release notes")
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record", help="Upsert the release note for a completed deploy")
    record.add_argument("--release-id", required=True)
    record.add_argument("--git-commit", required=True)
    record.add_argument("--build-time", default=None)
    record.add_argument("--prompt-release-id", default=None)
    record.add_argument("--image-digests", required=True, help="JSON object of role -> digest")
    record.add_argument("--worktree", required=True, help="Git worktree checked out at the release commit")
    args = parser.parse_args(argv)

    dsn = str(os.getenv("PROMPT_RELEASE_TARGET_DSN") or "").strip()
    schema = str(os.getenv("PROMPT_RELEASE_TARGET_SCHEMA") or "").strip()
    if not dsn or not schema:
        print("PROMPT_RELEASE_TARGET_DSN and PROMPT_RELEASE_TARGET_SCHEMA are required", file=sys.stderr)
        return 1
    try:
        payload = record_release_note(
            dsn=dsn,
            schema=schema,
            release_id=args.release_id,
            git_commit=args.git_commit,
            build_time=args.build_time or None,
            prompt_release_id=args.prompt_release_id or None,
            image_digests=json.loads(args.image_digests),
            worktree=args.worktree,
        )
    except Exception as exc:
        print(f"release note record failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
