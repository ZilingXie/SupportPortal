"""Loopback-only HTTP service for the stateless Hermes experiment candidate."""

from __future__ import annotations

import argparse
import hmac
import json
import os
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .hermes_classifier import (
    HermesExperimentError,
    build_experiment_profile,
    classify_case_snapshot,
    validate_normalizer_environment,
)


_PATH = "/route-alignment/v1/classify"
_MAX_REQUEST_BYTES = 1_000_000
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def create_server(
    *,
    host: str,
    port: int,
    token: str,
    classifier: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> ThreadingHTTPServer:
    """Create a server that cannot bind to a non-loopback interface."""
    if host not in _LOOPBACK_HOSTS:
        raise HermesExperimentError("loopback_bind_required")
    token = token.strip()
    if not token:
        raise HermesExperimentError("missing_service_token")

    class Handler(BaseHTTPRequestHandler):
        server_version = "HermesRouteExperiment/1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def _write(self, status: int, payload: Mapping[str, Any]) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != _PATH:
                self._write(404, {"contract": "route-alignment-v1", "error": "not_found"})
                return
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if not hmac.compare_digest(supplied, expected):
                self._write(401, {"contract": "route-alignment-v1", "error": "authentication_error"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length <= 0 or length > _MAX_REQUEST_BYTES:
                self._write(400, {"contract": "route-alignment-v1", "error": "invalid_request_size"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._write(400, {"contract": "route-alignment-v1", "error": "invalid_json"})
                return
            if not isinstance(payload, Mapping) or payload.get("contract") != "route-alignment-v1":
                self._write(400, {"contract": "route-alignment-v1", "error": "invalid_contract"})
                return
            try:
                response = classifier(payload.get("case_snapshot"))
            except HermesExperimentError as exc:
                self._write(422, {"contract": "route-alignment-v1", "error": exc.code})
                return
            except Exception:
                self._write(500, {"contract": "route-alignment-v1", "error": "classification_failed"})
                return
            snapshot = payload.get("case_snapshot")
            expected_alias = str(snapshot.get("case_alias") or "") if isinstance(snapshot, Mapping) else ""
            expected_revision = str(snapshot.get("case_revision") or "") if isinstance(snapshot, Mapping) else ""
            if (
                not isinstance(response, Mapping)
                or response.get("contract") != "route-alignment-v1"
                or response.get("case_alias") != expected_alias
                or response.get("case_revision") != expected_revision
            ):
                self._write(500, {"contract": "route-alignment-v1", "error": "invalid_classifier_response"})
                return
            self._write(200, response)

    return ThreadingHTTPServer((host, port), Handler)


def _required_env(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise HermesExperimentError("missing_service_configuration")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    validate_normalizer_environment()
    token = _required_env("HERMES_EXPERIMENT_TOKEN")
    profile = build_experiment_profile(
        api_key=_required_env("HERMES_ROUTE_EXPERIMENT_API_KEY"),
        base_url=_required_env("HERMES_ROUTE_EXPERIMENT_BASE_URL"),
        model=_required_env("HERMES_ROUTE_EXPERIMENT_MODEL"),
        reasoning_effort=_required_env("HERMES_ROUTE_EXPERIMENT_REASONING_EFFORT"),
    )
    server = create_server(
        host=args.host,
        port=args.port,
        token=token,
        classifier=lambda snapshot: classify_case_snapshot(snapshot, profile=profile),
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
