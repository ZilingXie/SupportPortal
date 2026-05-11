from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.knowledge_ingestion import (
    _build_chunk_rows,
    _build_shadow_chunk_rows,
    _enrich_metadata_with_llm,
    parse_official_markdown_file,
    parse_official_markdown_content,
    parse_technical_article,
)
from backend.services.llm_profiles import KNOWLEDGE_INGESTION_SCENARIO, ModelProfile, OPENAI_CHAT_API

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_TOKEN_SERVER_DOC = REPO_ROOT / "ag_docs" / "video-calling_deploy-token-server.md"
TECH_BLOG_DOC = REPO_ROOT / "backend" / "tests" / "fixtures" / "tech_blog.md"


SAMPLE_OFFICIAL_MARKDOWN = """---
title: Agora Console REST API
description: Interact with Agora Console using the REST API.
platform: android
exported_from: https://docs.agora.io/en/video-calling/channel-management-api/agora-console-rest-api
exported_on: '2026-01-20T05:57:32.558572Z'
exported_file: agora-console-rest-api.md
---

[HTML Version](https://docs.agora.io/en/video-calling/channel-management-api/agora-console-rest-api)

# Agora Console REST API

When you need to create and manage Agora projects, you can also call the Agora Console RESTful API.

## Basic information

This section provides basic information about the Agora Console RESTful APIs.

#### Authentication

The Agora Console RESTful APIs only support HTTPS.

## Create a project

Creates an Agora project.
"""


SAMPLE_OFFICIAL_TITLE_ONLY_MARKDOWN = """---
title: Preload channels
description: Preloading channels for faster rendering.
platform: web
exported_from: https://docs.agora.io/en/video-calling/best-practices/preload-channels?platform=web
exported_on: '2026-01-20T05:44:18.970947Z'
exported_file: preload-channels_web.md
---

[HTML Version](https://docs.agora.io/en/video-calling/best-practices/preload-channels?platform=web)

# Preload channels
"""


def _build_official_markdown(
    *,
    title: str,
    description: str,
    platform: str,
    exported_from: str,
    exported_file: str,
) -> str:
    return f"""---
title: {title}
description: {description}
platform: {platform}
exported_from: {exported_from}
exported_on: '2026-01-20T05:44:18.970947Z'
exported_file: {exported_file}
---

[HTML Version]({exported_from})

# {title}

{description}
"""


SAMPLE_TECHNICAL_ARTICLE = """**Issue Description:**
A livestream archive was missing approximately the first 64 seconds of content. The delay occurred between the initiation of the Cloud Transcoder creation request and the time the first RTMP frame was received by the streaming service (AWS IVS).

**Platform/SDK:**
Agora Cloud Transcoder used with AWS IVS for RTMP livestreaming.

**Error Message:**
No explicit error message was generated. The issue is identified by a delay in stream start timestamps.

---

### Step by Step Solution

1. **Check Agora Dashboard or Cloud Transcoder Logs:**
   - Access the Agora Console and locate the project associated with the reported channel ID (`g1OYN8`).

2. **Identify "Acquire" and "Create" Timestamps:**
   - Look for entries that include the `acquire` and `create` API calls.

3. **Locate Transcoder Initialization Events:**
   - In the same logs, check for events where the Cloud Transcoder moved to a "started" or "running" state.

4. **Compare Timings:**
   - Compare the logged "create" API timestamp with the start of RTMP transmission.

5. **Report Findings and Correlate:**
   - If Agora's logs show a delay between "create" and "start output," it is likely due to transcoder initialization.

---

### Root Cause

In most cases, this type of delay is caused by startup latency within the Cloud Transcoder initialization phase.

---

### Prevention/Best Practice

- Ensure that background jobs triggering the Cloud Transcoder API call include robust logging.

---

### Corresponding Document/Link

- [Agora Cloud Transcoding Overview](https://docs.agora.io/en/live-streaming/video_transcoding_overview)
- [Using Cloud Recording APIs](https://docs.agora.io/en/cloud-recording/recording_restfulapi)
- [AWS IVS RTMP Ingest Documentation](https://docs.aws.amazon.com/ivs/latest/userguide/stream.html)
"""


def _build_large_pricing_markdown() -> str:
    table_rows = "\n".join(
        f"| Messaging | Endpoint {index} | Supports overage billing with repeated quota and pricing details for large scale enterprise workloads {index} |"
        for index in range(1, 181)
    )
    return f"""---
title: Pricing plan details
description: Lists the details of the pricing plans for Agora Chat.
platform: android
exported_from: https://docs.agora.io/en/agora-chat/reference/pricing-plan-details
exported_on: '2026-01-20T05:42:25.211420Z'
exported_file: pricing-plan-details.md
---

[HTML Version](https://docs.agora.io/en/agora-chat/reference/pricing-plan-details)

# Pricing plan details

## RESTful APIs

### RESTful API call detailed pricing

Submit a support ticket if you want to lift the limits and pay for overage charge.

| Category | Rest API Description | Notes |
| :--- | :--- | :--- |
{table_rows}
"""


class KnowledgeIngestionParsingTests(unittest.TestCase):
    class _FakeProvider:
        provider_name = "siliconflow"
        model_id = "BAAI/bge-m3"
        vector_dim = 1024

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            vectors: list[list[float]] = []
            for text in texts:
                score = float(len(text or ""))
                vectors.append([score, score / 2.0, 1.0])
            return vectors

        def count_tokens(self, text: str) -> int:
            return max(1, len(str(text or "").split()))

    def test_parse_official_markdown_extracts_front_matter_and_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            markdown_path = Path(tmpdir) / "agora-console-rest-api.md"
            markdown_path.write_text(SAMPLE_OFFICIAL_MARKDOWN, encoding="utf-8")
            document = parse_official_markdown_file(markdown_path, ingestion_id="KI-TEST-OFFICIAL")

        self.assertEqual(document.title, "Agora Console REST API")
        self.assertEqual(
            document.url,
            "https://docs.agora.io/en/video-calling/channel-management-api/agora-console-rest-api",
        )
        self.assertEqual(document.source_path, "official/agora-console-rest-api.md")
        self.assertEqual(document.knowledge_type, "official")
        self.assertEqual(document.source_type, "official_markdown_upload")
        self.assertEqual(document.metadata["platform"], "android")
        self.assertEqual(document.metadata["product"], "video-calling")
        self.assertEqual(document.metadata["module"], "channel-management-api")
        self.assertEqual(document.cleaning_report["parser_name"], "official_markdown_parser")
        self.assertGreaterEqual(len(document.sections), 2)
        self.assertGreater(len(document.content_blocks), 0)
        self.assertEqual(document.sections[0].h2, "Introduction")
        self.assertTrue(any(section.h2 == "Basic information" for section in document.sections))

    def test_tech_blog_fixture_exists_under_backend_test_fixtures(self) -> None:
        self.assertTrue(TECH_BLOG_DOC.exists())

    def test_parse_official_markdown_content_supports_db_backed_uploads(self) -> None:
        document = parse_official_markdown_content(
            raw_markdown=SAMPLE_OFFICIAL_MARKDOWN,
            file_name="agora-console-rest-api.md",
            ingestion_id="KI-TEST-OFFICIAL-CONTENT",
        )

        self.assertEqual(document.title, "Agora Console REST API")
        self.assertEqual(document.source_path, "official/agora-console-rest-api.md")
        self.assertEqual(document.knowledge_type, "official")
        self.assertEqual(document.source_type, "official_markdown_upload")
        self.assertTrue(any(section.h2 == "Create a project" for section in document.sections))

    def test_parse_official_markdown_content_generates_overview_block_for_title_only_pages(self) -> None:
        document = parse_official_markdown_content(
            raw_markdown=SAMPLE_OFFICIAL_TITLE_ONLY_MARKDOWN,
            file_name="preload-channels_web.md",
            ingestion_id="KI-TEST-OFFICIAL-TITLE-ONLY",
        )

        self.assertEqual(document.title, "Preload channels")
        self.assertGreaterEqual(len(document.sections), 1)
        self.assertGreaterEqual(len(document.content_blocks), 1)
        self.assertEqual(document.sections[0].h2, "Overview")
        self.assertIn("Preloading channels for faster rendering.", document.content_blocks[0].text)

    def test_parse_official_markdown_content_assigns_same_source_family_to_platform_variants(self) -> None:
        android_document = parse_official_markdown_content(
            raw_markdown=_build_official_markdown(
                title="Get started with Video SDK",
                description="Android quickstart.",
                platform="android",
                exported_from="https://docs.agora.io/en/video-calling/get-started/get-started-sdk?platform=android",
                exported_file="get-started-sdk_android.md",
            ),
            file_name="get-started-sdk_android.md",
            ingestion_id="KI-TEST-OFFICIAL-FAMILY-ANDROID",
        )
        ios_document = parse_official_markdown_content(
            raw_markdown=_build_official_markdown(
                title="Get started with Video SDK",
                description="iOS quickstart.",
                platform="ios",
                exported_from="https://docs.agora.io/en/video-calling/get-started/get-started-sdk?platform=ios",
                exported_file="get-started-sdk_ios.md",
            ),
            file_name="get-started-sdk_ios.md",
            ingestion_id="KI-TEST-OFFICIAL-FAMILY-IOS",
        )

        self.assertEqual(android_document.source_family, "video-calling/get-started/get-started-sdk")
        self.assertEqual(ios_document.source_family, "video-calling/get-started/get-started-sdk")
        self.assertEqual(android_document.metadata["source_family"], ios_document.metadata["source_family"])

    def test_parse_official_markdown_content_distinguishes_same_basename_on_different_paths(self) -> None:
        get_started_document = parse_official_markdown_content(
            raw_markdown=_build_official_markdown(
                title="Authentication workflow",
                description="Get started authentication guide.",
                platform="android",
                exported_from="https://docs.agora.io/en/video-calling/get-started/authentication-workflow?platform=android",
                exported_file="authentication-workflow_android.md",
            ),
            file_name="authentication-workflow_android.md",
            ingestion_id="KI-TEST-OFFICIAL-FAMILY-GET-STARTED",
        )
        token_document = parse_official_markdown_content(
            raw_markdown=_build_official_markdown(
                title="Authentication workflow",
                description="Token authentication guide.",
                platform="android",
                exported_from="https://docs.agora.io/en/video-calling/token-authentication/authentication-workflow?platform=android",
                exported_file="authentication-workflow_android.md",
            ),
            file_name="authentication-workflow_android.md",
            ingestion_id="KI-TEST-OFFICIAL-FAMILY-TOKEN",
        )

        self.assertEqual(get_started_document.source_family, "video-calling/get-started/authentication-workflow")
        self.assertEqual(token_document.source_family, "video-calling/token-authentication/authentication-workflow")
        self.assertNotEqual(get_started_document.source_family, token_document.source_family)

    def test_parse_official_markdown_ignores_fenced_code_headings_and_preserves_heading_path(self) -> None:
        document = parse_official_markdown_file(
            DEPLOY_TOKEN_SERVER_DOC,
            ingestion_id="KI-TEST-OFFICIAL-DEPLOY-TOKEN",
        )

        self.assertEqual({section.h1 for section in document.sections}, {"Deploy a token server"})
        self.assertTrue(
            any(
                list(getattr(section, "heading_path", ())) == ["Token generation code", "Basic authentication"]
                for section in document.sections
            )
        )
        self.assertTrue(
            any(
                list(getattr(section, "heading_path", ()))
                == ["Reference", "API Reference", "`BuildTokenWithUid`"]
                for section in document.sections
            )
        )

    def test_parse_technical_article_builds_case_sections_and_metadata(self) -> None:
        document = parse_technical_article(
            title="Livestream archive missing first 64 seconds",
            content=SAMPLE_TECHNICAL_ARTICLE,
            source_url="https://internal.example.com/kb/stream-start-delay",
            ingestion_id="KI-TEST-TECHNICAL",
        )

        self.assertEqual(document.title, "Livestream archive missing first 64 seconds")
        self.assertEqual(document.knowledge_type, "technical")
        self.assertEqual(document.source_type, "technical_article_api")
        self.assertEqual(
            document.metadata["platform_sdk"],
            "Agora Cloud Transcoder used with AWS IVS for RTMP livestreaming.",
        )
        self.assertEqual(len(document.metadata["reference_links"]), 3)
        self.assertEqual(document.metadata["doc_subtype"], "troubleshooting_case")
        self.assertEqual(
            [section.section_type for section in document.sections],
            [
                "issue_summary",
                "troubleshooting_procedure",
                "decision_logic",
                "root_cause_summary",
                "best_practice",
            ],
        )
        self.assertEqual(document.sections[0].heading_path, ("Issue Summary",))
        self.assertEqual(document.sections[2].heading_path, ("Decision Logic",))
        self.assertIn("startup_delay", document.metadata["issue_category"])
        self.assertIn("AWS IVS", document.metadata["external_service"])
        self.assertEqual(document.metadata["protocol"], "RTMP")
        self.assertFalse(document.metadata["error_present"])
        self.assertGreater(len(document.content_blocks), 0)

    def test_parse_technical_article_assigns_source_family_from_url_and_source_path_fallback(self) -> None:
        url_backed = parse_technical_article(
            title="Livestream archive missing first 64 seconds",
            content=SAMPLE_TECHNICAL_ARTICLE,
            source_url="https://internal.example.com/kb/stream-start-delay",
            ingestion_id="KI-TEST-TECHNICAL-FAMILY-URL",
        )
        path_backed = parse_technical_article(
            title="Livestream archive missing first 64 seconds",
            content=SAMPLE_TECHNICAL_ARTICLE,
            source_url=None,
            ingestion_id="KI-TEST-TECHNICAL-FAMILY-PATH",
        )

        self.assertEqual(url_backed.source_family, "kb/stream-start-delay")
        self.assertEqual(url_backed.metadata["source_family"], "kb/stream-start-delay")
        self.assertEqual(path_backed.source_family, "technical/livestream-archive-missing-first-64-seconds")
        self.assertEqual(path_backed.metadata["source_family"], "technical/livestream-archive-missing-first-64-seconds")

    def test_chunk_rows_include_context_prefix_for_technical_articles(self) -> None:
        document = parse_technical_article(
            title="Livestream archive missing first 64 seconds",
            content=SAMPLE_TECHNICAL_ARTICLE,
            source_url="https://internal.example.com/kb/stream-start-delay",
            ingestion_id="KI-TEST-TECHNICAL",
        )

        rows = _build_chunk_rows(document, document.metadata)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["knowledge_type"], "technical")
        self.assertEqual(rows[0]["metadata"]["source_type"], "technical_article_api")
        self.assertIn("Title: Livestream archive missing first 64 seconds", rows[0]["content"])
        self.assertIn("Platform: Agora Cloud Transcoder used with AWS IVS for RTMP livestreaming.", rows[0]["content"])
        self.assertIn("Section:", rows[0]["content"])
        self.assertEqual(rows[0]["metadata"]["chunk_type"], "issue_summary")
        self.assertEqual(rows[1]["metadata"]["chunk_type"], "troubleshooting_procedure")
        self.assertEqual(rows[2]["metadata"]["chunk_type"], "decision_logic")
        self.assertEqual(rows[3]["metadata"]["chunk_type"], "root_cause_summary")
        self.assertEqual(rows[4]["metadata"]["chunk_type"], "best_practice")
        self.assertEqual(rows[0]["metadata"]["doc_subtype"], "troubleshooting_case")
        self.assertEqual(rows[0]["metadata"]["issue_category"], "startup_delay")
        self.assertEqual(rows[0]["metadata"]["protocol"], "RTMP")
        self.assertEqual(rows[0]["metadata"]["external_service"], "AWS IVS")
        self.assertEqual(len(rows[0]["metadata"]["related_links"]), 3)
        self.assertEqual(rows[0]["chunk_strategy"], "technical_case_units_v1")
        self.assertEqual(rows[0]["metadata"]["section_path"], ["Issue Summary"])
        self.assertEqual(rows[0]["metadata"]["source_family"], "kb/stream-start-delay")

    def test_technical_case_primary_chunk_rows_keep_links_in_metadata_without_reference_chunk(self) -> None:
        document = parse_technical_article(
            title="Livestream archive missing first 64 seconds",
            content=TECH_BLOG_DOC.read_text(encoding="utf-8"),
            source_url="https://internal.example.com/kb/stream-start-delay",
            ingestion_id="KI-TEST-TECHNICAL-GOLD",
        )

        rows = _build_chunk_rows(document, document.metadata)

        self.assertEqual(len(rows), 5)
        self.assertFalse(any(row["metadata"]["chunk_type"] == "references" for row in rows))
        self.assertTrue(all(len(row["metadata"]["related_links"]) == 3 for row in rows))
        self.assertTrue(any(row["metadata"]["chunk_type"] == "decision_logic" for row in rows))
        self.assertTrue(
            any(
                row["metadata"]["chunk_type"] == "troubleshooting_procedure"
                and row["metadata"]["section_path"] == ["Troubleshooting Procedure"]
                for row in rows
            )
        )

    def test_official_primary_chunk_rows_use_structured_chunking_and_metadata(self) -> None:
        document = parse_official_markdown_file(
            DEPLOY_TOKEN_SERVER_DOC,
            ingestion_id="KI-TEST-OFFICIAL-PRIMARY-STRUCTURED",
        )

        rows = _build_chunk_rows(document, document.metadata, provider=self._FakeProvider())

        self.assertGreaterEqual(len(rows), 35)
        self.assertLessEqual(len(rows), 45)
        code_languages = {
            row["metadata"].get("language")
            for row in rows
            if row["metadata"].get("chunk_type") == "code"
        }
        self.assertTrue({"go", "nodejs", "php", "python", "java", "cpp"}.issubset(code_languages))
        self.assertTrue(
            any(
                row["metadata"].get("chunk_type") == "rules_table"
                and row["metadata"].get("use_case") == "wildcard_tokens"
                for row in rows
            )
        )
        self.assertTrue(
            any(
                row["metadata"].get("chunk_type") == "api_params"
                and row["metadata"].get("method_name") == "BuildTokenWithUid"
                for row in rows
            )
        )
        self.assertTrue(
            any(
                row["metadata"].get("use_case") == "docker_deployment"
                and row["metadata"].get("chunk_type") == "howto"
                for row in rows
            )
        )
        self.assertTrue(
            all(isinstance(row["metadata"].get("section_path"), list) for row in rows)
        )

    def test_official_primary_chunk_rows_split_large_table_sections(self) -> None:
        document = parse_official_markdown_content(
            raw_markdown=_build_large_pricing_markdown(),
            file_name="pricing-plan-details.md",
            ingestion_id="KI-TEST-OFFICIAL-LARGE-TABLE-PRIMARY",
        )

        rows = _build_chunk_rows(document, document.metadata, provider=self._FakeProvider())
        target_rows = [
            row
            for row in rows
            if row["metadata"].get("section_path") == ["RESTful APIs", "RESTful API call detailed pricing"]
        ]

        self.assertGreaterEqual(len(target_rows), 2)
        self.assertTrue(all(row["metadata"].get("chunk_type") == "rules_table" for row in target_rows))
        self.assertLess(max(row["chunk_token_count"] for row in target_rows), 1200)

    def test_shadow_chunk_rows_capture_shadow_role_and_strategy(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            document = parse_official_markdown_content(
                raw_markdown=SAMPLE_OFFICIAL_MARKDOWN,
                file_name="agora-console-rest-api.md",
                ingestion_id="KI-TEST-OFFICIAL-SHADOW",
            )

            result = _build_shadow_chunk_rows(
                document,
                document.metadata,
                provider=self._FakeProvider(),
            )

        self.assertGreaterEqual(len(result.rows), 1)
        self.assertEqual(result.index_role, "shadow")
        self.assertEqual(result.chunk_strategy, "official_section_token_v1")
        self.assertTrue(all(row["index_role"] == "shadow" for row in result.rows))
        self.assertTrue(all(trace["index_role"] == "shadow" for trace in result.traces))
        self.assertTrue(all(isinstance(row["metadata"].get("section_path"), list) for row in result.rows))

    def test_technical_shadow_chunk_rows_keep_existing_semantic_strategy(self) -> None:
        document = parse_technical_article(
            title="Livestream archive missing first 64 seconds",
            content=SAMPLE_TECHNICAL_ARTICLE,
            source_url="https://internal.example.com/kb/stream-start-delay",
            ingestion_id="KI-TEST-TECHNICAL-SHADOW",
        )

        with patch.dict("os.environ", {}, clear=True):
            result = _build_shadow_chunk_rows(
                document,
                document.metadata,
                provider=self._FakeProvider(),
            )

        self.assertEqual(result.index_role, "shadow")
        self.assertEqual(result.chunk_strategy, "semantic_qwen3_v1")
        self.assertTrue(all(row["index_role"] == "shadow" for row in result.rows))
        self.assertGreaterEqual(len(result.rows), 1)

    def test_official_shadow_chunk_rows_use_section_token_baseline(self) -> None:
        document = parse_official_markdown_file(
            DEPLOY_TOKEN_SERVER_DOC,
            ingestion_id="KI-TEST-OFFICIAL-SHADOW-BASELINE",
        )

        with patch.dict("os.environ", {}, clear=True):
            result = _build_shadow_chunk_rows(
                document,
                document.metadata,
                provider=self._FakeProvider(),
            )

        self.assertEqual(result.index_role, "shadow")
        self.assertEqual(result.chunk_strategy, "official_section_token_v1")
        self.assertTrue(all(row["index_role"] == "shadow" for row in result.rows))
        self.assertTrue(all(isinstance(row["metadata"].get("section_path"), list) for row in result.rows))
        self.assertLess(len(result.rows), 80)

    def test_official_shadow_chunk_rows_split_large_table_sections(self) -> None:
        document = parse_official_markdown_content(
            raw_markdown=_build_large_pricing_markdown(),
            file_name="pricing-plan-details.md",
            ingestion_id="KI-TEST-OFFICIAL-LARGE-TABLE-SHADOW",
        )

        result = _build_shadow_chunk_rows(
            document,
            document.metadata,
            provider=self._FakeProvider(),
        )
        target_rows = [
            row
            for row in result.rows
            if row["metadata"].get("section_path") == ["RESTful APIs", "RESTful API call detailed pricing"]
        ]

        self.assertGreaterEqual(len(target_rows), 2)
        self.assertLess(max(row["chunk_token_count"] for row in target_rows), 1200)

    def test_parse_technical_article_records_missing_sections_as_warnings(self) -> None:
        document = parse_technical_article(
            title="Partial technical note",
            content="**Issue Description:**\nOnly the issue description is present.",
            source_url="https://internal.example.com/kb/partial-note",
            ingestion_id="KI-TEST-TECHNICAL-PARTIAL",
        )

        warnings = document.cleaning_report.get("warnings") if isinstance(document.cleaning_report.get("warnings"), list) else []
        self.assertTrue(any(str(item).startswith("missing_section:") for item in warnings))
        self.assertIn("issue_description", document.metadata["section_names"])
        self.assertEqual([section.section_type for section in document.sections], ["issue_summary"])
        rows = _build_chunk_rows(document, document.metadata)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metadata"]["chunk_type"], "issue_summary")

    def test_metadata_enrichment_can_be_disabled_via_env(self) -> None:
        document = parse_official_markdown_content(
            raw_markdown=SAMPLE_OFFICIAL_MARKDOWN,
            file_name="agora-console-rest-api.md",
            ingestion_id="KI-TEST-OFFICIAL-META-DISABLED",
        )

        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "test-key",
                "KNOWLEDGE_METADATA_ENRICHMENT_ENABLED": "false",
            },
            clear=True,
        ):
            with patch("backend.services.knowledge_ingestion.invoke_chat_text") as invoke_chat_text:
                metadata, meta_info = _enrich_metadata_with_llm(document)

        self.assertEqual(meta_info["metadata_source"], "rule")
        self.assertEqual(metadata["metadata_source"], "rule")
        invoke_chat_text.assert_not_called()

    def test_metadata_enrichment_records_resolved_profile_model_when_llm_succeeds(self) -> None:
        document = parse_technical_article(
            title="Livestream archive missing first 64 seconds",
            content=SAMPLE_TECHNICAL_ARTICLE,
            source_url="https://internal.example.com/kb/stream-start-delay",
            ingestion_id="KI-TEST-TECHNICAL-META-SUCCESS",
        )
        profile = ModelProfile(
            scenario=KNOWLEDGE_INGESTION_SCENARIO,
            provider="openai",
            model="gpt-test-metadata",
            api_mode=OPENAI_CHAT_API,
            api_key="test-key",
        )

        with patch(
            "backend.services.knowledge_ingestion.resolve_model_profile",
            return_value=profile,
        ), patch(
            "backend.services.knowledge_ingestion.invoke_chat_text",
            return_value=SimpleNamespace(
                text='{"summary":"LLM summary","tags":["latency"],"symptoms":["missing archive"]}',
            ),
        ) as invoke_chat_text:
            metadata, meta_info = _enrich_metadata_with_llm(document)

        self.assertEqual(meta_info["metadata_source"], "merged")
        self.assertEqual(meta_info["metadata_model"], "gpt-test-metadata")
        self.assertEqual(metadata["metadata_model"], "gpt-test-metadata")
        self.assertEqual(metadata["summary"], "LLM summary")
        invoke_chat_text.assert_called_once()


if __name__ == "__main__":
    unittest.main()
