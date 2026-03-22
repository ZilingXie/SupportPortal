from __future__ import annotations

import unittest

from backend.services.rag_tokenizer import (
    build_bm25_document_text,
    tokenize_bm25_query,
    tokenize_bm25_text,
)


class RagTokenizerTests(unittest.TestCase):
    def test_build_bm25_document_text_weights_headings(self) -> None:
        text = build_bm25_document_text(
            h1="Deploy a token server",
            h2="Token generation code",
            h3="BuildTokenWithUidAndPrivilege",
            content="Use BuildTokenWithUidAndPrivilege to generate a token.",
        )

        self.assertGreaterEqual(text.count("Deploy a token server"), 3)
        self.assertGreaterEqual(text.count("Token generation code"), 2)
        self.assertGreaterEqual(text.count("BuildTokenWithUidAndPrivilege"), 3)

    def test_tokenize_bm25_text_preserves_ascii_identifiers(self) -> None:
        tokens = tokenize_bm25_text("Node.js BuildTokenWithUidAndPrivilege uid=0")

        self.assertIn("nodejs", tokens)
        self.assertIn("buildtokenwithuidandprivilege", tokens)
        self.assertIn("uid", tokens)
        self.assertIn("0", tokens)

    def test_tokenize_bm25_text_emits_cjk_terms_and_bigrams(self) -> None:
        tokens = tokenize_bm25_text("怎么判断延迟发生在客户队列")

        self.assertIn("判断", tokens)
        self.assertIn("延迟", tokens)
        self.assertIn("客户", tokens)
        self.assertIn("队列", tokens)

    def test_tokenize_bm25_query_dedupes_repeated_terms(self) -> None:
        tokens = tokenize_bm25_query("Cloud Transcoder cloud transcoder 延迟 延迟")

        self.assertEqual(tokens.count("cloud"), 1)
        self.assertEqual(tokens.count("transcoder"), 1)
        self.assertEqual(tokens.count("延迟"), 1)


if __name__ == "__main__":
    unittest.main()
