from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.services.knowledge_ingestion import (
    _build_chunk_rows,
    parse_official_markdown_file,
    parse_technical_article,
)


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


class KnowledgeIngestionParsingTests(unittest.TestCase):
    def test_parse_official_markdown_extracts_front_matter_and_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            markdown_path = Path(tmpdir) / "agora-console-rest-api.md"
            markdown_path.write_text(SAMPLE_OFFICIAL_MARKDOWN, encoding="utf-8")
            document = parse_official_markdown_file(markdown_path, ingestion_id="KI-TEST-OFFICIAL")

        self.assertEqual(document.title, "Agora Console REST API")
        self.assertEqual(
            document.source_url,
            "https://docs.agora.io/en/video-calling/channel-management-api/agora-console-rest-api",
        )
        self.assertEqual(document.source_path, "official/agora-console-rest-api.md")
        self.assertEqual(document.knowledge_type, "official")
        self.assertEqual(document.base_metadata["platform"], "android")
        self.assertEqual(document.base_metadata["product"], "video-calling")
        self.assertEqual(document.base_metadata["module"], "channel-management-api")
        self.assertGreaterEqual(len(document.sections), 2)
        self.assertEqual(document.sections[0].h2, "Introduction")
        self.assertTrue(any(section.h2 == "Basic information" for section in document.sections))

    def test_parse_technical_article_groups_steps_and_links(self) -> None:
        document = parse_technical_article(
            title="Livestream archive missing first 64 seconds",
            content=SAMPLE_TECHNICAL_ARTICLE,
            source_url="https://internal.example.com/kb/stream-start-delay",
            ingestion_id="KI-TEST-TECHNICAL",
        )

        self.assertEqual(document.title, "Livestream archive missing first 64 seconds")
        self.assertEqual(document.knowledge_type, "technical")
        self.assertEqual(
            document.base_metadata["platform_sdk"],
            "Agora Cloud Transcoder used with AWS IVS for RTMP livestreaming.",
        )
        self.assertEqual(len(document.base_metadata["reference_links"]), 3)
        self.assertEqual(document.sections[0].section_type, "issue_overview")
        solution_chunks = [section for section in document.sections if section.section_type == "solution_steps"]
        self.assertEqual([section.h3 for section in solution_chunks], ["Steps 1-2", "Steps 3-4", "Steps 5-5"])
        self.assertTrue(any(section.section_type == "root_cause" for section in document.sections))
        self.assertTrue(any(section.section_type == "prevention_refs" for section in document.sections))

    def test_chunk_rows_include_context_prefix_for_technical_articles(self) -> None:
        document = parse_technical_article(
            title="Livestream archive missing first 64 seconds",
            content=SAMPLE_TECHNICAL_ARTICLE,
            source_url="https://internal.example.com/kb/stream-start-delay",
            ingestion_id="KI-TEST-TECHNICAL",
        )

        rows = _build_chunk_rows(document, document.base_metadata)
        self.assertGreaterEqual(len(rows), 4)
        self.assertEqual(rows[0]["knowledge_type"], "technical")
        self.assertIn("Title: Livestream archive missing first 64 seconds", rows[0]["content"])
        self.assertIn("Platform: Agora Cloud Transcoder used with AWS IVS for RTMP livestreaming.", rows[0]["content"])
        self.assertIn("Section:", rows[0]["content"])


if __name__ == "__main__":
    unittest.main()
