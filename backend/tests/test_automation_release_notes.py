from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.scripts.automation_release_notes import (
    _changes_since,
    main,
    record_release_note,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "first.txt").write_text("1", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "First feature (p2-001) (#900)")
    (tmp_path / "second.txt").write_text("2", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "Second feature (p2-002) (#901)")
    return tmp_path


def test_changes_since_previous_commit_lists_only_new_subjects(git_repo: Path) -> None:
    head = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    parent = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert _changes_since(str(git_repo), parent) == ["Second feature (p2-002) (#901)"]
    assert _changes_since(str(git_repo), None) == ["Second feature (p2-002) (#901)"]
    assert head != parent


def _cursor_results(rows: list[tuple]) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchone.return_value = rows[0] if rows else None
    return cursor


def _connection_with_cursors(cursors: list[MagicMock]) -> MagicMock:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.side_effect = cursors
    return connection


def test_record_release_note_upserts_with_changes_and_json_payloads(git_repo: Path) -> None:
    parent = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    write_cursor = MagicMock()
    connection = _connection_with_cursors([_cursor_results([(parent,)]), write_cursor])

    with patch(
        "backend.scripts.automation_release_notes.psycopg.connect",
        return_value=connection,
    ) as connect:
        payload = record_release_note(
            dsn="postgresql://release.invalid/supportportal",
            schema="supportportal_preproduction",
            release_id="r20260912-abcdef1",
            git_commit="b" * 40,
            build_time="2026-09-12T00:00:00Z",
            prompt_release_id="pr-1",
            image_digests={"api": "sha256:aaa", "route": "sha256:bbb", "worker": "sha256:ccc"},
            worktree=str(git_repo),
        )

    connect.assert_called_once()
    statement, parameters = write_cursor.execute.call_args.args
    rendered = statement.as_string()
    assert 'INSERT INTO "supportportal_preproduction"."support_release_notes"' in rendered
    assert "ON CONFLICT (release_id) DO UPDATE" in rendered
    assert parameters[:4] == ("r20260912-abcdef1", "b" * 40, "2026-09-12T00:00:00Z", "pr-1")
    assert json.loads(parameters[4]) == {"api": "sha256:aaa", "route": "sha256:bbb", "worker": "sha256:ccc"}
    assert json.loads(parameters[5]) == ["Second feature (p2-002) (#901)"]
    connection.commit.assert_called_once()
    assert payload == {
        "release_id": "r20260912-abcdef1",
        "git_commit": "b" * 40,
        "previous_commit": parent,
        "changes": 1,
    }


def test_record_release_note_first_record_falls_back_to_single_commit(git_repo: Path) -> None:
    write_cursor = MagicMock()
    connection = _connection_with_cursors([_cursor_results([]), write_cursor])

    with patch(
        "backend.scripts.automation_release_notes.psycopg.connect",
        return_value=connection,
    ):
        payload = record_release_note(
            dsn="postgresql://release.invalid/supportportal",
            schema="supportportal_production",
            release_id="r20260912-first000",
            git_commit="c" * 40,
            build_time=None,
            prompt_release_id=None,
            image_digests={"api": "sha256:aaa"},
            worktree=str(git_repo),
        )

    _, parameters = write_cursor.execute.call_args.args
    assert json.loads(parameters[5]) == ["Second feature (p2-002) (#901)"]
    assert parameters[2] is None and parameters[3] is None
    assert payload["previous_commit"] is None


@pytest.mark.parametrize(
    "overrides",
    (
        {"release_id": "  "},
        {"git_commit": ""},
        {"image_digests": {}},
    ),
)
def test_record_release_note_fails_closed_on_invalid_input(overrides: dict) -> None:
    base = {
        "dsn": "postgresql://release.invalid/supportportal",
        "schema": "supportportal_preproduction",
        "release_id": "r1",
        "git_commit": "c" * 40,
        "build_time": None,
        "prompt_release_id": None,
        "image_digests": {"api": "sha256:aaa"},
        "worktree": ".",
    }
    with pytest.raises(ValueError):
        record_release_note(**{**base, **overrides})


def test_main_requires_target_dsn_and_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMPT_RELEASE_TARGET_DSN", raising=False)
    monkeypatch.delenv("PROMPT_RELEASE_TARGET_SCHEMA", raising=False)
    assert (
        main(
            [
                "record",
                "--release-id",
                "r1",
                "--git-commit",
                "c" * 40,
                "--image-digests",
                "{}",
                "--worktree",
                ".",
            ]
        )
        == 1
    )
