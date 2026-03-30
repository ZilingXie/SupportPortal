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

    def test_tokenize_bm25_query_filters_low_information_english_stopwords(self) -> None:
        tokens = tokenize_bm25_query(
            "What is an Agora token, and why is it recommended over using only an App ID?"
        )

        self.assertNotIn("what", tokens)
        self.assertNotIn("is", tokens)
        self.assertNotIn("an", tokens)
        self.assertNotIn("and", tokens)
        self.assertNotIn("why", tokens)
        self.assertNotIn("it", tokens)
        self.assertNotIn("over", tokens)
        self.assertNotIn("using", tokens)
        self.assertNotIn("only", tokens)
        self.assertIn("agora", tokens)
        self.assertIn("token", tokens)
        self.assertIn("app", tokens)
        self.assertIn("id", tokens)

    def test_tokenize_bm25_query_filters_conversational_noise_terms(self) -> None:
        tokens = tokenize_bm25_query(
            "I'm getting error 109 when users join. Does that mean the token expired?"
        )

        self.assertNotIn("i", tokens)
        self.assertNotIn("m", tokens)
        self.assertNotIn("getting", tokens)
        self.assertNotIn("mean", tokens)
        self.assertIn("109", tokens)
        self.assertIn("error", tokens)
        self.assertIn("token", tokens)
        self.assertIn("expired", tokens)

    def test_tokenize_bm25_query_filters_pronouns_and_low_signal_prepositions(self) -> None:
        tokens = tokenize_bm25_query("How early does Agora warn me before a token expires?")

        self.assertNotIn("me", tokens)
        self.assertNotIn("before", tokens)
        self.assertIn("early", tokens)
        self.assertIn("warn", tokens)
        self.assertIn("token", tokens)
        self.assertIn("expires", tokens)


if __name__ == "__main__":
    unittest.main()
