from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

from backend.services.embedding_provider import (
    _normalize_vector,
    _siliconflow_request_json,
    _disable_torch_load_mmap,
    _model_dim_from_config,
    _resolve_embedding_device,
    embedding_model_id,
    embedding_provider_name,
    siliconflow_api_key,
    siliconflow_embedding_dimensions,
    require_configured_vector_dim,
    validate_embedding_provider_dim,
)


class _FakeProvider:
    def __init__(self, vector_dim: int) -> None:
        self.vector_dim = vector_dim


class EmbeddingProviderConfigTests(unittest.TestCase):
    def test_embedding_defaults_to_siliconflow_bge_large(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(embedding_provider_name(), "siliconflow")
            self.assertEqual(embedding_model_id(), "BAAI/bge-large-en-v1.5")
            self.assertEqual(siliconflow_embedding_dimensions(), 1024)

    def test_embedding_provider_normalizes_legacy_siliconflow_qwen3_alias(self) -> None:
        with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "siliconflow_qwen3"}, clear=True):
            self.assertEqual(embedding_provider_name(), "siliconflow")

    def test_require_configured_vector_dim_needs_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                require_configured_vector_dim()

    def test_validate_embedding_provider_dim_uses_explicit_dimension(self) -> None:
        with patch.dict(os.environ, {"PGVECTOR_DIM": "1024"}, clear=True):
            self.assertEqual(validate_embedding_provider_dim(_FakeProvider(1024)), 1024)
            with self.assertRaises(RuntimeError):
                validate_embedding_provider_dim(_FakeProvider(1536))

    def test_model_dim_from_config_reads_hidden_size(self) -> None:
        fake_config = types.SimpleNamespace(hidden_size=1024)
        fake_auto_config = types.SimpleNamespace(from_pretrained=lambda *args, **kwargs: fake_config)
        fake_transformers = types.SimpleNamespace(AutoConfig=fake_auto_config)
        with patch.dict(sys.modules, {"transformers": fake_transformers}):
            self.assertEqual(_model_dim_from_config("BAAI/bge-large-en-v1.5"), 1024)

    def test_validate_embedding_provider_dim_uses_configured_dimensions_for_siliconflow(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMBEDDING_PROVIDER": "siliconflow_qwen3",
                "EMBEDDING_MODEL_ID": "BAAI/bge-large-en-v1.5",
                "PGVECTOR_DIM": "1024",
                "SILICONFLOW_EMBEDDING_DIMENSIONS": "1024",
            },
            clear=True,
        ):
            self.assertEqual(validate_embedding_provider_dim(), 1024)

    def test_siliconflow_api_key_accepts_legacy_typo_alias(self) -> None:
        with patch.dict(os.environ, {"silliconflow_key": "secret-123"}, clear=True):
            self.assertEqual(siliconflow_api_key(), "secret-123")

    def test_resolve_embedding_device_honors_explicit_value(self) -> None:
        self.assertEqual(_resolve_embedding_device("cpu"), "cpu")
        self.assertEqual(_resolve_embedding_device("mps"), "mps")

    def test_disable_torch_load_mmap_strips_mmap_kwarg(self) -> None:
        calls: list[dict[str, object]] = []
        fake_torch = types.SimpleNamespace()

        def original_load(*args: object, **kwargs: object) -> str:
            calls.append(dict(kwargs))
            return "loaded"

        fake_torch.load = original_load
        with patch.dict(sys.modules, {"torch": fake_torch}):
            with _disable_torch_load_mmap():
                result = fake_torch.load("/tmp/model.bin", mmap=True, weights_only=True)
            self.assertEqual(result, "loaded")
            self.assertIs(fake_torch.load, original_load)
        self.assertEqual(calls, [{"weights_only": True}])

    def test_normalize_vector_preserves_unit_norm(self) -> None:
        normalized = _normalize_vector([3.0, 4.0])
        self.assertAlmostEqual(normalized[0], 0.6, places=6)
        self.assertAlmostEqual(normalized[1], 0.8, places=6)

    def test_siliconflow_request_json_parses_response_and_headers(self) -> None:
        class _Response:
            status = 200

            def __init__(self) -> None:
                self.headers = {"x-siliconcloud-trace-id": "trace-1"}

            def read(self) -> bytes:
                return b'{"data":[{"index":0,"embedding":[1,2]}],"usage":{"prompt_tokens":12}}'

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        with patch("urllib.request.urlopen", return_value=_Response()):
            payload, headers = _siliconflow_request_json(
                url="https://api.siliconflow.cn/v1/embeddings",
                api_key="secret",
                payload={"model": "BAAI/bge-large-en-v1.5", "input": ["hello"]},
                timeout_seconds=10.0,
            )
        self.assertEqual(payload["usage"]["prompt_tokens"], 12)
        self.assertEqual(headers["x-siliconcloud-trace-id"], "trace-1")


if __name__ == "__main__":
    unittest.main()
