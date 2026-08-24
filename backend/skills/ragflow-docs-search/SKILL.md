---
name: ragflow-docs-search
description: Search the Agora official documentation knowledge base (RAGFlow-backed RAG over docs.agora.io + api-ref.agora.io). Use to answer Agora product/API/how-to questions with authoritative, citable answers — semantic retrieval returns ranked passages with their exact HTML source_url, and full-document fetch pulls the complete doc when needed. Covers ConvoAI, RTC, RTM, recording, SIP, TTS/LLM/ASR, SDK API reference, pricing, best practices. Use whenever the user asks "how do I / what is / which API / does Agora support ..." about an Agora product, or when a case investigation needs the documented behavior.
---

# Agora Docs Knowledge Base Search (RAGFlow)

Semantic search over the full Agora official documentation, backed by the
RAGFlow knowledge base at `knowledge.convoai.club`. Two datasets are indexed:

- **docs** — `docs.agora.io` main docs (~3,000 pages: guides, best practices,
  overviews, pricing, ConvoAI/RTC/RTM/recording/SIP/TTS/LLM/ASR).
- **apiref** — `api-ref.agora.io` SDK API reference (classes/methods across
  all SDKs, platforms, versions).

Retrieval is hybrid (vector + keyword), then **precision-reranked with NVIDIA**
(over-retrieve a candidate pool → rerank → keep top-k). Every result carries a
clean HTML `source_url`, so answers are always citable back to the real docs
page — never a `.md` URL.

## When to use

- Any Agora product / how-to / API / pricing / capability question.
- A case investigation needs the *documented* behavior of a feature
  (parameter meaning, default, supported version, event field, etc.).

Prefer this over guessing from memory — the KB is the source of truth and is
kept in sync with the live docs.

## Usage

Auth: `RAGFLOW_API_KEY` (or `op read $RAGFLOW_OP_REF`) for retrieval, and
`NVIDIA_API_KEY` (or `NVIDIA_OP_REF`) for rerank. If the NVIDIA key is missing
or the rerank call fails, the skill warns on stderr and falls back to the raw
hybrid order — it never hard-fails. Endpoint defaults to
`https://knowledge.convoai.club`.

Semantic search (default — returns ranked passages grouped by doc + source URL):

```
python3 scripts/search.py search "how to reduce ConvoAI latency"
python3 scripts/search.py "join a channel with a token"          # bare = search
python3 scripts/search.py search "RTM sendMessageToPeer" --scope apiref
python3 scripts/search.py search "cloud recording pricing" --top-k 8 --json
```

Options: `--scope docs|apiref|both` (default both), `--top-k N` (default 6),
`--candidates N` (pool retrieved before rerank, default 20), `--threshold F`
(default 0.2), `--no-rerank` (skip NVIDIA rerank, raw hybrid order),
`--json` (machine-readable array).

Read a full document (when passages aren't enough — pulls the complete
LLM-ready markdown for a docs.agora.io URL):

```
python3 scripts/search.py full "https://docs.agora.io/en/conversational-ai/best-practices/optimize-latency"
```

## How to answer with it

1. Run `search` with the user's question.
2. Read the returned passages; if you need the whole page, `full <source_url>`.
3. Answer concisely and **cite the `source:` HTML URL(s)** you used. Never
   expose the internal `.md` / `docs-md.agora.io` URLs to the user.
4. For SDK-method questions, use `--scope apiref` (or both); for
   conceptual/how-to questions `docs` alone is usually cleaner.

## Notes

- The KB is refreshed from live docs; if a very new feature is missing,
  say so rather than inventing an answer.
- Cloudflare fronts the endpoint; the script sends a curl-style User-Agent
  to avoid the bot integrity check (error 1010) — keep that header.
