from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _git_short_ref(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=1.5,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return _normalize_text(result.stdout)


def resolve_app_build_info(
    *,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, str | None]:
    root = repo_root or REPO_ROOT
    environment = os.environ if env is None else env
    ref = _git_short_ref(root) or _normalize_text(environment.get("APP_BUILD_REF")) or "unknown"
    built_at = _normalize_text(environment.get("APP_BUILD_TIME")) or None
    return {
        "ref": ref,
        "built_at": built_at,
    }


@lru_cache(maxsize=1)
def get_app_build_info() -> dict[str, str | None]:
    return resolve_app_build_info()


def clear_app_build_info_cache_for_testing() -> None:
    get_app_build_info.cache_clear()
