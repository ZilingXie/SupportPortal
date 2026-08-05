from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "deployment" / "docker-compose.single-host.yml"
LIGHTWEIGHT_COMPOSE_PATH = REPO_ROOT / "deployment" / "docker-compose.single-host.local-lightweight.yml"
DOCKERFILE_PATH = REPO_ROOT / "backend" / "Dockerfile"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"
REQUIREMENTS_BASE_PATH = REPO_ROOT / "requirements.base.txt"
REQUIREMENTS_ML_PATH = REPO_ROOT / "requirements.ml.txt"
RUNTIME_SERVICE_NAMES = ("api", "rag_api", "rag_worker", "ws_gateway", "worker_query", "worker_aux")


class SingleHostComposeTests(unittest.TestCase):
    def _service_block(self, service_name: str) -> str:
        content = COMPOSE_PATH.read_text(encoding="utf-8")
        match = re.search(rf"(?ms)^  {re.escape(service_name)}:\n(.*?)(?=^  [a-zA-Z0-9_]+:|\Z)", content)
        self.assertIsNotNone(match, f"{service_name} service block should exist in single-host compose")
        assert match is not None
        return match.group(1)

    def test_api_service_defaults_to_two_workers(self) -> None:
        api_block = self._service_block("api")

        self.assertIn(
            '- "${API_WORKERS:-2}"',
            api_block,
        )
        self.assertIn(
            "TICKET_DB_APPLICATION_NAME: ${TICKET_DB_APPLICATION_NAME:-supportportal-api}",
            api_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MIN_SIZE: ${TICKET_DB_POOL_MIN_SIZE:-3}",
            api_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MAX_SIZE: ${TICKET_DB_POOL_MAX_SIZE:-4}",
            api_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_TIMEOUT_SECONDS: ${TICKET_DB_POOL_TIMEOUT_SECONDS:-15}",
            api_block,
        )

    def test_worker_query_service_defaults_query_concurrency_and_queue(self) -> None:
        worker_block = self._service_block("worker_query")

        self.assertIn(
            "WORKER_TASK_TYPES: ${WORKER_QUERY_TASK_TYPES:-ticket_query}",
            worker_block,
        )
        self.assertIn('ACCOUNT_REPLY_POLLER_ENABLED: "false"', worker_block)
        self.assertIn('ACCOUNT_REPLY_LEGACY_POLLER_ENABLED: "false"', worker_block)
        self.assertIn('AUTOMATION_REPLY_POLL_ENABLED: "false"', worker_block)
        self.assertIn('BILLING_AUTOMATION_REPLY_POLL_ENABLED: "false"', worker_block)
        self.assertIn(
            "WORKER_CONCURRENCY: ${WORKER_QUERY_CONCURRENCY:-2}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_APPLICATION_NAME: ${TICKET_DB_APPLICATION_NAME:-supportportal-worker-query}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MIN_SIZE: ${TICKET_DB_POOL_MIN_SIZE:-2}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MAX_SIZE: ${TICKET_DB_POOL_MAX_SIZE:-4}",
            worker_block,
        )
        self.assertIn(
            "TICKET_QUERY_QUEUE_NAME: ${TICKET_QUERY_QUEUE_NAME:-support.ticket_queries}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MAX_LIFETIME_SECONDS: ${TICKET_DB_POOL_MAX_LIFETIME_SECONDS:-1800}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MAX_IDLE_SECONDS: ${TICKET_DB_POOL_MAX_IDLE_SECONDS:-300}",
            worker_block,
        )

    def test_worker_aux_service_defaults_sentiment_provider_and_queue(self) -> None:
        worker_block = self._service_block("worker_aux")

        self.assertIn(
            "WORKER_TASK_TYPES: ${WORKER_AUX_TASK_TYPES:-ticket_message_sentiment}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_APPLICATION_NAME: ${TICKET_DB_APPLICATION_NAME:-supportportal-worker-aux}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MIN_SIZE: ${TICKET_DB_POOL_MIN_SIZE:-1}",
            worker_block,
        )
        self.assertIn(
            "TICKET_DB_POOL_MAX_SIZE: ${TICKET_DB_POOL_MAX_SIZE:-2}",
            worker_block,
        )
        self.assertIn(
            "WORKER_CONCURRENCY: ${WORKER_AUX_CONCURRENCY:-1}",
            worker_block,
        )
        self.assertIn(
            "TICKET_AUX_QUEUE_NAME: ${TICKET_AUX_QUEUE_NAME:-support.ticket_aux}",
            worker_block,
        )
        self.assertIn(
            "SENTIMENT_PROVIDER: ${WORKER_SENTIMENT_PROVIDER:-legacy}",
            worker_block,
        )
        self.assertIn(
            "SENTIMENT_MODEL_ID: ${SENTIMENT_MODEL_ID:-j-hartmann/emotion-english-distilroberta-base}",
            worker_block,
        )
        self.assertIn(
            "SENTIMENT_MIN_CONFIDENCE: ${SENTIMENT_MIN_CONFIDENCE:-0.45}",
            worker_block,
        )
        self.assertIn(
            "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL: ${ENABLEMENT_AUTOMATION_INTERNAL_EMAIL:-}",
            worker_block,
        )
        self.assertIn(
            "QUOTA_AUTOMATION_INTERNAL_EMAIL: ${QUOTA_AUTOMATION_INTERNAL_EMAIL:-}",
            worker_block,
        )
        self.assertIn(
            "AUTOMATION_REPLY_POLL_ENABLED: ${AUTOMATION_REPLY_POLL_ENABLED:-true}",
            worker_block,
        )
        self.assertIn('ACCOUNT_REPLY_POLLER_ENABLED: "true"', worker_block)
        self.assertIn('ACCOUNT_REPLY_LEGACY_POLLER_ENABLED: "true"', worker_block)
        self.assertIn(
            "AUTOMATION_REPLY_POLL_INTERVAL_SECONDS: ${AUTOMATION_REPLY_POLL_INTERVAL_SECONDS:-}",
            worker_block,
        )
        self.assertIn(
            "AUTOMATION_REPLY_POLL_MAX_MESSAGES: ${AUTOMATION_REPLY_POLL_MAX_MESSAGES:-}",
            worker_block,
        )
        self.assertIn(
            "BILLING_AUTOMATION_REPLY_POLL_ENABLED: ${BILLING_AUTOMATION_REPLY_POLL_ENABLED:-false}",
            worker_block,
        )
        self.assertIn(
            "BILLING_AUTOMATION_REPLY_POLL_INTERVAL_SECONDS: ${BILLING_AUTOMATION_REPLY_POLL_INTERVAL_SECONDS:-300}",
            worker_block,
        )
        self.assertIn(
            "BILLING_AUTOMATION_REPLY_POLL_MAX_MESSAGES: ${BILLING_AUTOMATION_REPLY_POLL_MAX_MESSAGES:-25}",
            worker_block,
        )
        self.assertIn(
            "BILLING_AUTOMATION_REPLY_RECORD_PATH: ${BILLING_AUTOMATION_REPLY_RECORD_PATH:-.msgraph/billing-request-replies.jsonl}",
            worker_block,
        )
        self.assertIn(
            "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE: ${BILLING_AUTOMATION_GRAPH_TOKEN_CACHE:-.msgraph/billing-automation-token.json}",
            worker_block,
        )
        self.assertIn(
            "BILLING_AUTOMATION_REPLY_PDF_MAX_ATTACHMENTS: ${BILLING_AUTOMATION_REPLY_PDF_MAX_ATTACHMENTS:-3}",
            worker_block,
        )
        self.assertIn(
            "BILLING_AUTOMATION_REPLY_PDF_MAX_BYTES: ${BILLING_AUTOMATION_REPLY_PDF_MAX_BYTES:-20971520}",
            worker_block,
        )
        self.assertNotIn("PADDLEOCR_API_TOKEN", worker_block)

    def test_worker_service_mounts_huggingface_cache(self) -> None:
        worker_block = self._service_block("worker_query")

        self.assertIn(
            "volumes:\n      - huggingface_cache:/root/.cache/huggingface",
            worker_block,
        )

    def test_billing_graph_token_cache_is_mounted_for_api_and_worker_aux(self) -> None:
        api_block = self._service_block("api")
        worker_aux_block = self._service_block("worker_aux")

        self.assertIn("- ../.msgraph:/app/.msgraph", api_block)
        self.assertIn("- ../.msgraph:/app/.msgraph", worker_aux_block)

    def test_api_receives_read_only_environment_inventory_file(self) -> None:
        api_block = self._service_block("api")

        self.assertIn("- ../.env:/run/supportportal/environment-config.env:ro", api_block)
        self.assertIn(
            "SUPPORTPORTAL_ENV_CONFIG_PATH: /run/supportportal/environment-config.env",
            api_block,
        )
        for service_name in RUNTIME_SERVICE_NAMES:
            if service_name != "api":
                self.assertNotIn(
                    "/run/supportportal/environment-config.env",
                    self._service_block(service_name),
                )

    def test_legacy_single_worker_service_is_removed(self) -> None:
        content = COMPOSE_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(content, r"(?m)^  worker:\n")

    def test_prompt_runtime_release_is_shared_by_all_llm_services_only(self) -> None:
        prompt_services = ("api", "rag_api", "rag_worker", "worker_query", "worker_aux")
        for service_name in prompt_services:
            block = self._service_block(service_name)
            self.assertIn("PROMPT_RELEASE_ID: ${PROMPT_RELEASE_ID:-}", block)
            self.assertIn("PROMPT_RELEASE_REQUIRED: ${PROMPT_RELEASE_REQUIRED:-false}", block)
            self.assertIn(f"PROMPT_RUNTIME_SERVICE: {service_name}", block)
        ws_gateway_block = self._service_block("ws_gateway")
        self.assertNotIn("PROMPT_RELEASE_ID", ws_gateway_block)
        self.assertNotIn("PROMPT_RUNTIME_SERVICE", ws_gateway_block)

    def test_client_rag_recovery_defaults_are_present(self) -> None:
        content = COMPOSE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "CLIENT_RAG_SERVICE_TIMEOUT_SECONDS: ${CLIENT_RAG_SERVICE_TIMEOUT_SECONDS:-180.0}",
            content,
        )
        self.assertIn(
            "CLIENT_RAG_RECOVERY_WINDOW_SECONDS: ${CLIENT_RAG_RECOVERY_WINDOW_SECONDS:-90.0}",
            content,
        )
        self.assertIn(
            "CLIENT_RAG_RECOVERY_POLL_INTERVAL_SECONDS: ${CLIENT_RAG_RECOVERY_POLL_INTERVAL_SECONDS:-2.0}",
            content,
        )

    def test_rag_request_timeout_default_is_ten_minutes(self) -> None:
        rag_api_block = self._service_block("rag_api")
        env_example = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "RAG_REQUEST_TIMEOUT_SECONDS: ${RAG_REQUEST_TIMEOUT_SECONDS:-600.0}",
            rag_api_block,
        )
        self.assertIn("RAG_REQUEST_TIMEOUT_SECONDS=600.0", env_example)

    def test_client_ack_and_async_query_defaults_are_present(self) -> None:
        content = COMPOSE_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "CLIENT_ACK_MODEL: ${CLIENT_ACK_MODEL:-gpt-5.4-nano}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_REASONING_EFFORT: ${CLIENT_ACK_REASONING_EFFORT:-none}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_TIMEOUT_SECONDS: ${CLIENT_ACK_TIMEOUT_SECONDS:-5.0}",
            content,
        )
        self.assertIn(
            "CLIENT_ACK_MAX_OUTPUT_TOKENS: ${CLIENT_ACK_MAX_OUTPUT_TOKENS:-32}",
            content,
        )
        self.assertIn("ASYNC_QUERY_ENABLED: ${ASYNC_QUERY_ENABLED:-true}", content)
        self.assertNotIn("OPTIMISTIC_PARALLEL_ROUTE_ENABLED", content)
        self.assertIn(
            "CLIENT_ACK_FALLBACK_TIMEOUT_MS: ${CLIENT_ACK_FALLBACK_TIMEOUT_MS:-5000}",
            content,
        )

    def test_api_service_exposes_asset_storage_defaults(self) -> None:
        api_block = self._service_block("api")
        env_example = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        base_requirements = REQUIREMENTS_BASE_PATH.read_text(encoding="utf-8")

        self.assertIn("ASSET_STORAGE_PROVIDER: ${ASSET_STORAGE_PROVIDER:-s3}", api_block)
        self.assertIn("ASSET_S3_BUCKET: ${ASSET_S3_BUCKET:-}", api_block)
        self.assertIn("ASSET_S3_REGION: ${ASSET_S3_REGION:-}", api_block)
        self.assertIn("ASSET_S3_PREFIX: ${ASSET_S3_PREFIX:-supportportal}", api_block)
        self.assertIn("ASSET_UPLOAD_MAX_BYTES: ${ASSET_UPLOAD_MAX_BYTES:-20971520}", api_block)
        self.assertIn("ASSET_ALLOWED_EXTENSIONS: ${ASSET_ALLOWED_EXTENSIONS:-.log,.err,.txt}", api_block)
        self.assertIn("ASSET_PRESIGN_TTL_SECONDS: ${ASSET_PRESIGN_TTL_SECONDS:-300}", api_block)
        self.assertIn("ASSET_S3_KMS_KEY_ID: ${ASSET_S3_KMS_KEY_ID:-}", api_block)
        self.assertIn("AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:-}", api_block)
        self.assertIn("AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY:-}", api_block)
        self.assertIn("AWS_SESSION_TOKEN: ${AWS_SESSION_TOKEN:-}", api_block)
        self.assertIn("AWS_REGION: ${AWS_REGION:-}", api_block)
        self.assertIn("AWS_DEFAULT_REGION: ${AWS_DEFAULT_REGION:-}", api_block)
        self.assertNotIn("${AWS_REGION:-${ASSET_S3_REGION:-}}", api_block)
        self.assertNotIn("${AWS_DEFAULT_REGION:-${ASSET_S3_REGION:-}}", api_block)
        self.assertNotIn("AWS_PROFILE: ${AWS_PROFILE:-}", api_block)
        self.assertIn("ASSET_STORAGE_PROVIDER=s3", env_example)
        self.assertIn("ASSET_ALLOWED_EXTENSIONS=.log,.err,.txt", env_example)
        self.assertIn("AWS_ACCESS_KEY_ID=", env_example)
        self.assertIn("AWS_SECRET_ACCESS_KEY=", env_example)
        self.assertIn("AWS_SESSION_TOKEN=", env_example)
        self.assertIn("AWS_REGION=us-east-1", env_example)
        self.assertIn("AWS_DEFAULT_REGION=us-east-1", env_example)
        self.assertIn("boto3>=", base_requirements)

    def test_engineer_investigation_reply_defaults_are_present_for_api_service(self) -> None:
        api_block = self._service_block("api")

        self.assertIn(
            "ENGINEER_INVESTIGATION_REPLY_MODEL: ${ENGINEER_INVESTIGATION_REPLY_MODEL:-gpt-5.4}",
            api_block,
        )
        self.assertIn(
            "ENGINEER_INVESTIGATION_REPLY_REASONING_EFFORT: ${ENGINEER_INVESTIGATION_REPLY_REASONING_EFFORT:-medium}",
            api_block,
        )
        self.assertIn(
            "ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS: ${ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS:-20.0}",
            api_block,
        )
        self.assertIn(
            "ENGINEER_INVESTIGATION_REPLY_MAX_RETRIES: ${ENGINEER_INVESTIGATION_REPLY_MAX_RETRIES:-1}",
            api_block,
        )

    def test_deepseek_fallback_env_is_exposed_to_llm_runtime_services(self) -> None:
        llm_service_names = ("api", "rag_api", "rag_worker", "worker_query", "worker_aux")

        for service_name in llm_service_names:
            service_block = self._service_block(service_name)
            self.assertIn("DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-}", service_block)
            self.assertIn("DEEPSEEK_BASE_URL: ${DEEPSEEK_BASE_URL:-https://api.deepseek.com}", service_block)
            self.assertIn(
                "DEEPSEEK_FALLBACK_MODEL: ${DEEPSEEK_FALLBACK_MODEL:-deepseek-v4-pro}",
                service_block,
            )
            self.assertIn("DEEPSEEK_FALLBACK_ENABLED: ${DEEPSEEK_FALLBACK_ENABLED:-true}", service_block)

    def test_env_example_documents_deepseek_fallback_defaults(self) -> None:
        content = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

        self.assertIn("DEEPSEEK_API_KEY=", content)
        self.assertIn("DEEPSEEK_BASE_URL=https://api.deepseek.com", content)
        self.assertIn("DEEPSEEK_FALLBACK_MODEL=deepseek-v4-pro", content)
        self.assertIn("DEEPSEEK_FALLBACK_ENABLED=true", content)

    def test_root_env_example_documents_all_stack_modes(self) -> None:
        env_content = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

        self.assertIn("STACK_RUNTIME_MODE=full", env_content)
        self.assertIn("STACK_DB_MODE=remote", env_content)
        self.assertIn("LOCAL_POSTGRES_USER=", env_content)
        self.assertIn("LOCAL_PGVECTOR_TABLE=", env_content)
        self.assertIn("LOCAL_NEO4J_AUTH=", env_content)
        self.assertFalse((REPO_ROOT / ".env.local.example").exists())

    def test_api_build_injects_app_build_metadata(self) -> None:
        api_block = self._service_block("api")

        self.assertIn("args:", api_block)
        self.assertIn("APP_BUILD_REF: ${APP_BUILD_REF:-unknown}", api_block)
        self.assertIn("APP_BUILD_TIME: ${APP_BUILD_TIME:-}", api_block)
        self.assertIn("APP_BUILD_REF: ${APP_BUILD_REF:-unknown}", api_block)
        self.assertIn("APP_BUILD_TIME: ${APP_BUILD_TIME:-}", api_block)

    def test_rag_api_runtime_env_includes_app_build_metadata(self) -> None:
        rag_api_block = self._service_block("rag_api")

        self.assertIn("APP_BUILD_REF: ${APP_BUILD_REF:-unknown}", rag_api_block)
        self.assertIn("APP_BUILD_TIME: ${APP_BUILD_TIME:-}", rag_api_block)

    def test_runtime_services_share_explicit_runtime_image(self) -> None:
        content = COMPOSE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("localhost/supportportal-app:latest", content)
        self.assertEqual(content.count("${APP_RUNTIME_IMAGE:-localhost/supportportal-app:unknown}"), len(RUNTIME_SERVICE_NAMES))
        for service_name in RUNTIME_SERVICE_NAMES:
            service_block = self._service_block(service_name)
            self.assertIn("image: ${APP_RUNTIME_IMAGE:-localhost/supportportal-app:unknown}", service_block)

    def test_runtime_services_expose_app_build_metadata_env(self) -> None:
        for service_name in RUNTIME_SERVICE_NAMES:
            service_block = self._service_block(service_name)
            self.assertIn("APP_BUILD_REF: ${APP_BUILD_REF:-unknown}", service_block)
            self.assertIn("APP_BUILD_TIME: ${APP_BUILD_TIME:-}", service_block)

    def test_dockerfile_exports_app_build_env(self) -> None:
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")

        self.assertIn("ARG APP_BUILD_REF=unknown", content)
        self.assertIn("ARG APP_BUILD_TIME=", content)
        self.assertIn("APP_BUILD_REF=${APP_BUILD_REF}", content)
        self.assertIn("APP_BUILD_TIME=${APP_BUILD_TIME}", content)

    def test_dockerfile_supports_optional_ml_dependency_install(self) -> None:
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")

        self.assertIn("ARG INSTALL_ML_DEPS=1", content)
        self.assertIn("COPY requirements.base.txt /app/requirements.base.txt", content)
        self.assertIn("COPY requirements.ml.txt /app/requirements.ml.txt", content)
        self.assertIn("python -m pip install --no-cache-dir -r /app/requirements.base.txt", content)
        self.assertIn('if [ "${INSTALL_ML_DEPS}" = "1" ]; then', content)
        self.assertIn("python -m pip install --no-cache-dir -r /app/requirements.ml.txt", content)

    def test_requirements_txt_aggregates_base_and_ml_files(self) -> None:
        content = REQUIREMENTS_PATH.read_text(encoding="utf-8")

        self.assertIn("-r requirements.base.txt", content)
        self.assertIn("-r requirements.ml.txt", content)

    def test_base_and_ml_requirements_are_split_for_lightweight_builds(self) -> None:
        base_content = REQUIREMENTS_BASE_PATH.read_text(encoding="utf-8")
        ml_content = REQUIREMENTS_ML_PATH.read_text(encoding="utf-8")

        self.assertIn("transformers==4.46.3", base_content)
        self.assertIn("sentencepiece>=0.2.0", base_content)
        self.assertNotIn("torch>=2.2.0", base_content)
        self.assertNotIn("sentence-transformers>=3.2.1", base_content)
        self.assertNotIn("accelerate>=0.26.0", base_content)

        self.assertIn("torch>=2.2.0", ml_content)
        self.assertIn("sentence-transformers>=3.2.1", ml_content)
        self.assertIn("accelerate>=0.26.0", ml_content)

    def test_local_lightweight_override_forces_legacy_sentiment_and_skips_ml_deps(self) -> None:
        content = LIGHTWEIGHT_COMPOSE_PATH.read_text(encoding="utf-8")

        self.assertIn("INSTALL_ML_DEPS: \"0\"", content)
        self.assertIn("SENTIMENT_PROVIDER: legacy", content)
        self.assertIn("RUNTIME_PROFILE: local_lightweight", content)

    def test_base_compose_defaults_runtime_profile_to_full(self) -> None:
        content = COMPOSE_PATH.read_text(encoding="utf-8")

        self.assertGreaterEqual(content.count("RUNTIME_PROFILE: ${RUNTIME_PROFILE:-full}"), 6)


if __name__ == "__main__":
    unittest.main()
