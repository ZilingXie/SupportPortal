from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

DEFAULT_EMBEDDING_PROVIDER = "siliconflow_qwen3"
DEFAULT_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-8B"
DEFAULT_EMBEDDING_BATCH_SIZE = 16
DEFAULT_PGVECTOR_TABLE = "docagent_chunks_qwen3_1024"
DEFAULT_PGVECTOR_SCHEMA = "supportportal"
DEFAULT_PRIMARY_CHUNK_STRATEGY = "markdown_header_v1"
DEFAULT_SHADOW_CHUNK_STRATEGY = "semantic_qwen3_v1"
DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_SILICONFLOW_EMBEDDING_DIMENSIONS = 1024

_OPENAI_EMBEDDING_PRICING = {
    "text-embedding-3-large": 0.00013,
    "text-embedding-3-small": 0.00002,
}


def _safe_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _estimate_tokens_fallback(text: str) -> int:
    raw = str(text or "")
    if not raw.strip():
        return 0
    return max(1, len(raw.split()), (len(raw) + 3) // 4)


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def embedding_provider_name() -> str:
    return _clean_text(os.getenv("EMBEDDING_PROVIDER")) or DEFAULT_EMBEDDING_PROVIDER


def embedding_model_id() -> str:
    legacy = _clean_text(os.getenv("OPENAI_EMBEDDING_MODEL"))
    if embedding_provider_name() == "openai":
        return legacy or "text-embedding-3-large"
    return _clean_text(os.getenv("EMBEDDING_MODEL_ID")) or DEFAULT_EMBEDDING_MODEL_ID


def embedding_device() -> str:
    return _clean_text(os.getenv("EMBEDDING_DEVICE")) or "auto"


def embedding_batch_size() -> int:
    return _safe_int_env("EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE)


def embedding_cache_dir() -> str | None:
    value = _clean_text(os.getenv("EMBEDDING_CACHE_DIR"))
    return value or None


def embedding_request_timeout_seconds() -> float:
    return _safe_float_env(
        "EMBEDDING_REQUEST_TIMEOUT_SECONDS",
        _safe_float_env("OPENAI_REQUEST_TIMEOUT_SECONDS", 20.0),
    )


def embedding_max_retries() -> int:
    return _safe_int_env(
        "EMBEDDING_MAX_RETRIES",
        _safe_int_env("OPENAI_MAX_RETRIES", 1),
    )


def siliconflow_base_url() -> str:
    return _clean_text(os.getenv("SILICONFLOW_BASE_URL")) or DEFAULT_SILICONFLOW_BASE_URL


def siliconflow_api_key() -> str:
    for key in [
        "SILICONFLOW_API_KEY",
        "SILICONFLOW_KEY",
        "SILLICONFLOW_KEY",
        "silliconflow_key",
    ]:
        value = _clean_text(os.getenv(key))
        if value:
            return value
    return ""


def siliconflow_embedding_dimensions() -> int:
    raw = _clean_text(os.getenv("SILICONFLOW_EMBEDDING_DIMENSIONS"))
    if not raw:
        return DEFAULT_SILICONFLOW_EMBEDDING_DIMENSIONS
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError("SILICONFLOW_EMBEDDING_DIMENSIONS must be a positive integer") from None
    if value <= 0:
        raise RuntimeError("SILICONFLOW_EMBEDDING_DIMENSIONS must be a positive integer")
    return value


def siliconflow_embedding_cost_per_1k() -> float:
    return _safe_float_env("SILICONFLOW_EMBEDDING_COST_PER_1K", 0.0)


def _model_dim_from_config(model_id: str, cache_dir: str | None = None) -> int | None:
    try:
        from transformers import AutoConfig
    except ImportError:
        return None

    kwargs: dict[str, Any] = {}
    if cache_dir:
        kwargs["cache_dir"] = cache_dir
    config = AutoConfig.from_pretrained(model_id, **kwargs)
    for attr in ("sentence_embedding_dimension", "hidden_size", "d_model"):
        value = getattr(config, attr, None)
        if value is not None:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
    text_config = getattr(config, "text_config", None)
    if text_config is not None:
        for attr in ("sentence_embedding_dimension", "hidden_size", "d_model"):
            value = getattr(text_config, attr, None)
            if value is not None:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    return parsed
    return None


def primary_chunk_strategy_name() -> str:
    return _clean_text(os.getenv("PRIMARY_CHUNK_STRATEGY")) or DEFAULT_PRIMARY_CHUNK_STRATEGY


def shadow_chunk_strategy_name() -> str:
    return _clean_text(os.getenv("SHADOW_CHUNK_STRATEGY")) or DEFAULT_SHADOW_CHUNK_STRATEGY


def shadow_chunk_enabled() -> bool:
    return _env_flag("SHADOW_CHUNK_ENABLED", True)


def vector_table_name() -> str:
    raw = _clean_text(os.getenv("PGVECTOR_TABLE")) or DEFAULT_PGVECTOR_TABLE
    if "." in raw:
        return raw
    schema = _clean_text(os.getenv("PGVECTOR_SCHEMA")) or DEFAULT_PGVECTOR_SCHEMA
    return f"{schema}.{raw}"


def configured_vector_dim() -> int | None:
    raw = _clean_text(os.getenv("PGVECTOR_DIM"))
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError("PGVECTOR_DIM must be a positive integer") from None
    if value <= 0:
        raise RuntimeError("PGVECTOR_DIM must be a positive integer")
    return value


def require_configured_vector_dim() -> int:
    value = configured_vector_dim()
    if value is None:
        raise RuntimeError("PGVECTOR_DIM is required and must match the embedding provider dimension")
    return value


@dataclass(frozen=True)
class EmbeddingRuntimeConfig:
    provider: str
    model_id: str
    device: str
    batch_size: int
    cache_dir: str | None
    request_timeout_seconds: float
    max_retries: int
    configured_vector_dim: int


class EmbeddingProvider(Protocol):
    provider_name: str
    model_id: str
    vector_dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...

    def count_tokens(self, text: str) -> int:
        ...

    def drain_request_log(self) -> list[dict[str, Any]]:
        ...


def _runtime_config() -> EmbeddingRuntimeConfig:
    return EmbeddingRuntimeConfig(
        provider=embedding_provider_name(),
        model_id=embedding_model_id(),
        device=embedding_device(),
        batch_size=embedding_batch_size(),
        cache_dir=embedding_cache_dir(),
        request_timeout_seconds=embedding_request_timeout_seconds(),
        max_retries=embedding_max_retries(),
        configured_vector_dim=require_configured_vector_dim(),
    )


def _resolve_embedding_device(preferred: str) -> str:
    normalized = _clean_text(preferred).lower() or "auto"
    if normalized != "auto":
        return normalized
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    backends = getattr(torch, "backends", None)
    mps = getattr(backends, "mps", None) if backends is not None else None
    if mps is not None and bool(getattr(mps, "is_available", lambda: False)()):
        return "mps"
    return "cpu"


@contextmanager
def _disable_torch_load_mmap():
    try:
        import torch
    except ImportError:
        yield
        return

    original_load = torch.load

    def load_without_mmap(*args: Any, **kwargs: Any):
        kwargs.pop("mmap", None)
        return original_load(*args, **kwargs)

    torch.load = load_without_mmap
    try:
        yield
    finally:
        torch.load = original_load


class LocalBGEM3EmbeddingProvider:
    def __init__(self, config: EmbeddingRuntimeConfig) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover - depends on runtime image
            raise RuntimeError(
                "sentence-transformers and transformers are required for EMBEDDING_PROVIDER=local_bge_m3"
            ) from exc

        self.provider_name = "local_bge_m3"
        self.model_id = config.model_id
        self.device = _resolve_embedding_device(config.device)
        sentence_transformer_kwargs: dict[str, Any] = {
            "device": self.device,
            "model_kwargs": {"low_cpu_mem_usage": True},
        }
        if config.cache_dir:
            sentence_transformer_kwargs["cache_folder"] = config.cache_dir
        # Transformers enables mmap for zip checkpoints by default, which can
        # fail inside constrained containers for large models such as BGE-M3.
        with _disable_torch_load_mmap():
            self._model = SentenceTransformer(self.model_id, **sentence_transformer_kwargs)
        tokenizer = getattr(self._model, "tokenizer", None)
        if tokenizer is None:
            tokenizer_kwargs: dict[str, Any] = {}
            if config.cache_dir:
                tokenizer_kwargs["cache_dir"] = config.cache_dir
            tokenizer = AutoTokenizer.from_pretrained(self.model_id, **tokenizer_kwargs)
        self._tokenizer = tokenizer
        self._batch_size = max(1, int(config.batch_size))
        self.vector_dim = int(self._model.get_sentence_embedding_dimension() or 0)
        if self.vector_dim <= 0:
            raise RuntimeError(f"Could not determine embedding dimension for {self.model_id}")
        if self.vector_dim != config.configured_vector_dim:
            raise RuntimeError(
                "PGVECTOR_DIM does not match the embedding provider dimension "
                f"(configured={config.configured_vector_dim}, actual={self.vector_dim})"
            )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        normalized = [str(text or "").strip() for text in texts]
        if not normalized:
            return []
        vectors = self._model.encode(
            normalized,
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0] if vectors else []

    def count_tokens(self, text: str) -> int:
        raw = str(text or "")
        if not raw.strip():
            return 0
        return len(self._tokenizer.encode(raw, add_special_tokens=False, truncation=False))

    def drain_request_log(self) -> list[dict[str, Any]]:
        return []


def _normalize_vector(vector: list[float]) -> list[float]:
    if not vector:
        return []
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if norm <= 0:
        return [float(value) for value in vector]
    return [float(value) / norm for value in vector]


def _siliconflow_request_json(
    *,
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"SupportPortalEmbeddingProvider/{socket.gethostname()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace").strip()
            parsed = json.loads(body) if body else {}
            if not isinstance(parsed, dict):
                raise RuntimeError("SiliconFlow embedding response must be a JSON object")
            return parsed, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"message": body}
        message = parsed.get("message") if isinstance(parsed, dict) else None
        detail = _clean_text(message) or body or f"HTTP {exc.code}"
        raise RuntimeError(f"SiliconFlow embedding request failed: {detail}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise RuntimeError(f"SiliconFlow embedding request failed: {exc}") from exc


class SiliconFlowQwenEmbeddingProvider:
    def __init__(self, config: EmbeddingRuntimeConfig) -> None:
        api_key = siliconflow_api_key()
        if not api_key:
            raise RuntimeError(
                "SILICONFLOW_API_KEY is required for EMBEDDING_PROVIDER=siliconflow_qwen3"
            )
        self.provider_name = "siliconflow_qwen3"
        self.model_id = config.model_id
        self._api_key = api_key
        self._base_url = siliconflow_base_url().rstrip("/")
        self._batch_size = max(1, int(config.batch_size))
        self._timeout_seconds = max(1.0, float(config.request_timeout_seconds))
        self._max_retries = max(0, int(config.max_retries))
        self.vector_dim = siliconflow_embedding_dimensions()
        if self.vector_dim != config.configured_vector_dim:
            raise RuntimeError(
                "PGVECTOR_DIM does not match the embedding provider dimension "
                f"(configured={config.configured_vector_dim}, actual={self.vector_dim})"
            )
        self._request_log: list[dict[str, Any]] = []
        self._tokenizer = self._load_tokenizer(config.cache_dir)

    def _load_tokenizer(self, cache_dir: str | None) -> Any:
        try:
            from transformers import AutoTokenizer
        except ImportError:
            return None
        kwargs: dict[str, Any] = {}
        if cache_dir:
            kwargs["cache_dir"] = cache_dir
        try:
            return AutoTokenizer.from_pretrained(self.model_id, **kwargs)
        except Exception:
            return None

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        attempt = 0
        last_error: Exception | None = None
        while attempt <= self._max_retries:
            try:
                payload, headers = _siliconflow_request_json(
                    url=f"{self._base_url}/embeddings",
                    api_key=self._api_key,
                    payload={
                        "model": self.model_id,
                        "input": texts,
                        "dimensions": self.vector_dim,
                        "encoding_format": "float",
                    },
                    timeout_seconds=self._timeout_seconds,
                )
                data = payload.get("data") if isinstance(payload.get("data"), list) else []
                ordered = sorted(
                    [
                        item
                        for item in data
                        if isinstance(item, dict) and isinstance(item.get("embedding"), list)
                    ],
                    key=lambda item: int(item.get("index") or 0),
                )
                vectors = [
                    _normalize_vector([float(value) for value in item.get("embedding", [])])
                    for item in ordered
                ]
                if len(vectors) != len(texts):
                    raise RuntimeError(
                        f"SiliconFlow embedding response size mismatch (expected={len(texts)}, actual={len(vectors)})"
                    )
                usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                self._request_log.append(
                    {
                        "provider": self.provider_name,
                        "model": self.model_id,
                        "dimensions": self.vector_dim,
                        "input_count": len(texts),
                        "usage": usage,
                        "trace_id": _clean_text(
                            headers.get("x-siliconcloud-trace-id")
                            or headers.get("X-SiliconCloud-Trace-Id")
                        ),
                    }
                )
                return vectors
            except Exception as exc:
                last_error = exc
                attempt += 1
        raise RuntimeError(str(last_error) if last_error is not None else "SiliconFlow embedding failed")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        normalized = [str(text or "").strip() for text in texts]
        if not normalized:
            return []
        vectors: list[list[float]] = []
        for offset in range(0, len(normalized), self._batch_size):
            vectors.extend(self._embed_batch(normalized[offset : offset + self._batch_size]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0] if vectors else []

    def count_tokens(self, text: str) -> int:
        raw = str(text or "")
        if not raw.strip():
            return 0
        if self._tokenizer is not None:
            try:
                return len(self._tokenizer.encode(raw, add_special_tokens=False, truncation=False))
            except Exception:
                pass
        return _estimate_tokens_fallback(raw)

    def drain_request_log(self) -> list[dict[str, Any]]:
        log = list(self._request_log)
        self._request_log.clear()
        return log


class OpenAIEmbeddingProvider:
    def __init__(self, config: EmbeddingRuntimeConfig) -> None:
        api_key = _clean_text(os.getenv("OPENAI_API_KEY"))
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for EMBEDDING_PROVIDER=openai")
        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as exc:  # pragma: no cover - depends on runtime image
            raise RuntimeError("langchain-openai is required for EMBEDDING_PROVIDER=openai") from exc

        self.provider_name = "openai"
        self.model_id = config.model_id
        self.vector_dim = config.configured_vector_dim
        self._client = OpenAIEmbeddings(
            model=self.model_id,
            api_key=api_key,
            request_timeout=config.request_timeout_seconds,
            max_retries=int(config.max_retries),
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents([str(text or "") for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(str(text or ""))

    def count_tokens(self, text: str) -> int:
        return _estimate_tokens_fallback(text)

    def drain_request_log(self) -> list[dict[str, Any]]:
        return []


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    config = _runtime_config()
    if config.provider == "siliconflow_qwen3":
        return SiliconFlowQwenEmbeddingProvider(config)
    if config.provider == "local_bge_m3":
        return LocalBGEM3EmbeddingProvider(config)
    if config.provider == "openai":
        return OpenAIEmbeddingProvider(config)
    raise RuntimeError(f"Unsupported EMBEDDING_PROVIDER: {config.provider}")


def reset_embedding_provider_cache() -> None:
    get_embedding_provider.cache_clear()


def validate_embedding_provider_dim(provider: EmbeddingProvider | None = None) -> int:
    configured_dim = require_configured_vector_dim()
    if provider is None and embedding_provider_name() == "siliconflow_qwen3":
        actual_dim = siliconflow_embedding_dimensions()
        if actual_dim != configured_dim:
            raise RuntimeError(
                "PGVECTOR_DIM does not match the embedding provider dimension "
                f"(configured={configured_dim}, actual={actual_dim})"
            )
        return configured_dim
    if provider is None and embedding_provider_name() == "local_bge_m3":
        config_dim = _model_dim_from_config(embedding_model_id(), cache_dir=embedding_cache_dir())
        if config_dim is not None:
            if config_dim != configured_dim:
                raise RuntimeError(
                    "PGVECTOR_DIM does not match the embedding provider dimension "
                    f"(configured={configured_dim}, actual={config_dim})"
                )
            return configured_dim
    runtime_provider = provider or get_embedding_provider()
    if runtime_provider.vector_dim != configured_dim:
        raise RuntimeError(
            "PGVECTOR_DIM does not match the embedding provider dimension "
            f"(configured={configured_dim}, actual={runtime_provider.vector_dim})"
        )
    return configured_dim


def embedding_external_cost_per_1k() -> float:
    provider = embedding_provider_name()
    if provider == "siliconflow_qwen3":
        return siliconflow_embedding_cost_per_1k()
    if provider != "openai":
        return 0.0
    return float(_OPENAI_EMBEDDING_PRICING.get(embedding_model_id(), 0.0))
