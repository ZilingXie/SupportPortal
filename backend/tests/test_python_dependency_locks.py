from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE_IMAGE = (
    "python:3.11-slim@sha256:"
    "d1e9ca7c4e78d1e8ecadb5d44bfc8e956e7a65b659a9950f569f243d72b326d0"
)


def _locked_version(content: str, package: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(package)}==([^\s\\]+)", content)
    return match.group(1) if match else None


def test_runtime_dockerfiles_pin_the_same_multiplatform_python_image() -> None:
    expected = f"ARG PYTHON_BASE_IMAGE={BASE_IMAGE}"
    for path in (ROOT / "backend/Dockerfile", ROOT / "backend/Dockerfile.automation"):
        content = path.read_text(encoding="utf-8")
        assert expected in content
        assert "FROM python:3.11-slim" not in content
        assert "pip install --upgrade pip" not in content
        assert "--mount=type=cache,target=/root/.cache/pip,sharing=locked" in content


def test_lock_updater_uses_the_pinned_builder_and_strict_hashes() -> None:
    content = (ROOT / "scripts/ops/update_python_dependency_locks.sh").read_text(encoding="utf-8")
    assert f'PYTHON_BASE_IMAGE="{BASE_IMAGE}"' in content
    assert 'PIP_TOOLS_VERSION="7.5.1"' in content
    for option in ("--allow-unsafe", "--generate-hashes", "--reuse-hashes", "--check"):
        assert option in content


def test_base_and_full_locks_preserve_the_transformers_contract() -> None:
    base = (ROOT / "requirements.base.lock").read_text(encoding="utf-8")
    full = (ROOT / "requirements.full.lock").read_text(encoding="utf-8")
    for content in (base, full):
        assert _locked_version(content, "transformers") == "4.46.3"
        assert _locked_version(content, "tokenizers") == "0.20.3"
        assert "--hash=sha256:" in content
    assert _locked_version(full, "setuptools") is not None


def test_full_lock_uses_cpu_only_pytorch_without_cuda_dependencies() -> None:
    content = (ROOT / "requirements.full.lock").read_text(encoding="utf-8")
    assert _locked_version(content, "torch") == "2.13.0+cpu"
    assert _locked_version(content, "sentence-transformers") == "5.7.0"
    assert _locked_version(content, "accelerate") == "1.14.0"
    assert not re.search(r"(?m)^(?:nvidia-|triton==|cuda-)", content)
