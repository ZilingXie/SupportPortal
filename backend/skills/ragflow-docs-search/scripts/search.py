#!/usr/bin/env python3
"""
RAGFlow-backed search over the Agora official docs knowledge base.

Two modes:
  search  (default) — semantic hybrid retrieval over the Agora docs KB.
                      Returns ranked passages grouped by source document,
                      each with its clean HTML source_url (docs.agora.io).
  full            — fetch the FULL markdown of a doc by its HTML URL
                      (docs.agora.io publishes LLM-ready markdown: append .md).

Auth: needs a RAGFlow API key. Resolution order:
  1. env RAGFLOW_API_KEY
  2. `op read $RAGFLOW_OP_REF`   (1Password reference)
Endpoint: RAGFLOW_BASE_URL (default https://knowledge.convoai.club)

The KB has two datasets: main docs (docs.agora.io) and the API reference
(api-ref.agora.io). By default we query both; --scope narrows it.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

BASE_URL = os.environ.get("RAGFLOW_BASE_URL", "https://knowledge.convoai.club").rstrip("/")

# Dataset IDs (see reference_ragflow_kb_server). Stable unless the KB is re-ingested.
DATASETS = {
    "docs": "c2eaf30463e511f18586e7085c4194fc",    # docs.agora.io main docs
    "apiref": "d3d8e64e63ea11f18586e7085c4194fc",   # api-ref.agora.io SDK reference
}

# NVIDIA reranking (free). Over-retrieve from RAGFlow, then precision-rerank here.
# Only working combo: model nvidia/rerank-qa-mistral-4b on the generic endpoint.
NV_RERANK_URL = "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking"
NV_RERANK_MODEL = "nvidia/rerank-qa-mistral-4b"


def get_key():
    key = os.environ.get("RAGFLOW_API_KEY")
    if key:
        return key.strip()
    ref = os.environ.get("RAGFLOW_OP_REF")
    if ref:
        try:
            return subprocess.check_output(["op", "read", ref], text=True).strip()
        except Exception as e:
            sys.exit(f"ERROR: RAGFLOW_API_KEY not set and `op read {ref}` failed: {e}")
    sys.exit("ERROR: set RAGFLOW_API_KEY (or RAGFLOW_OP_REF for 1Password).")


def get_nv_key():
    key = os.environ.get("NVIDIA_API_KEY")
    if key:
        return key.strip()
    ref = os.environ.get("NVIDIA_OP_REF")
    if ref:
        try:
            return subprocess.check_output(["op", "read", ref], text=True).strip()
        except Exception:
            return None
    return None


def rerank(query, chunks, top_k):
    """Precision-rerank RAGFlow candidates with NVIDIA; return top_k reordered.

    Resilient: if no key or the call fails, fall back to the RAGFlow order
    (retrieval is still hybrid-ranked) and warn on stderr — never hard-fail.
    """
    nv_key = get_nv_key()
    if not nv_key:
        print("[rerank] NVIDIA_API_KEY not set — falling back to hybrid order.",
              file=sys.stderr)
        return chunks[:top_k]
    passages = [{"text": (c.get("content") or "")[:4000]} for c in chunks]
    body = {"model": NV_RERANK_MODEL, "query": {"text": query}, "passages": passages}
    data = json.dumps(body).encode()
    req = urllib.request.Request(NV_RERANK_URL, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {nv_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "curl/8.4.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rankings = json.loads(r.read().decode()).get("rankings", [])
        if not rankings:
            raise ValueError("empty rankings")
        ordered = [chunks[x["index"]] for x in rankings if x.get("index") is not None
                   and x["index"] < len(chunks)]
        return ordered[:top_k]
    except Exception as e:
        print(f"[rerank] NVIDIA rerank failed ({e}) — falling back to hybrid order.",
              file=sys.stderr)
        return chunks[:top_k]


def http(method, path, key, body=None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    # A plain browser-ish UA avoids Cloudflare's bot integrity check (1010) on python.
    req.add_header("User-Agent", "curl/8.4.0")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: HTTP {e.code} on {method} {path}: {e.read().decode()[:300]}")
    except Exception as e:
        sys.exit(f"ERROR: request failed {method} {path}: {e}")


def resolve_source_urls(doc_ids_by_dataset, key):
    """document_id -> source_url via GET /datasets/{ds}/documents?id=."""
    out = {}
    for ds_id, doc_ids in doc_ids_by_dataset.items():
        for did in doc_ids:
            r = http("GET", f"/api/v1/datasets/{ds_id}/documents?id={did}", key)
            docs = (r.get("data") or {}).get("docs") or []
            if docs:
                mf = docs[0].get("meta_fields") or {}
                url = mf.get("source_url")
                plat = mf.get("platform")
                if url and plat:
                    url = f"{url}?platform={plat}"
                out[did] = url or docs[0].get("name")
    return out


def cmd_search(args, key):
    ds_ids = ([DATASETS[args.scope]] if args.scope in DATASETS
              else list(DATASETS.values()))
    do_rerank = not args.no_rerank
    # Over-retrieve a larger candidate pool when reranking, then precision-rerank.
    cand = max(args.candidates, args.top_k) if do_rerank else args.top_k
    body = {
        "question": args.query,
        "dataset_ids": ds_ids,
        "page": 1,
        "page_size": cand,
        "similarity_threshold": args.threshold,
    }
    r = http("POST", "/api/v1/retrieval", key, body)
    if r.get("code") != 0:
        sys.exit(f"ERROR: retrieval code={r.get('code')} msg={r.get('message')}")
    data = r.get("data") or {}
    chunks = data.get("chunks") or []
    if not chunks:
        print(f"No results for: {args.query}")
        return
    total = data.get("total")
    if do_rerank:
        chunks = rerank(args.query, chunks, args.top_k)
    else:
        chunks = chunks[:args.top_k]

    # map document_id -> source_url
    by_ds = {}
    for c in chunks:
        by_ds.setdefault(c.get("dataset_id"), set()).add(c.get("document_id"))
    urls = resolve_source_urls(by_ds, key)

    if args.json:
        for c in chunks:
            c["source_url"] = urls.get(c.get("document_id"))
        print(json.dumps([
            {"source_url": c.get("source_url"),
             "doc": c.get("document_keyword"),
             "similarity": round(c.get("similarity", 0), 3),
             "content": c.get("content")}
            for c in chunks], ensure_ascii=False, indent=2))
        return

    # human/agent-readable, grouped by document
    mode = "hybrid+NVIDIA-rerank" if not args.no_rerank else "hybrid"
    print(f"Query: {args.query}   ({len(chunks)} passages, {mode}, from {total} candidates)\n")
    seen = {}
    for c in chunks:
        did = c.get("document_id")
        seen.setdefault(did, []).append(c)
    order = sorted(seen.items(),
                   key=lambda kv: max(x.get("similarity", 0) for x in kv[1]),
                   reverse=True)
    for did, cs in order:
        url = urls.get(did, "(url?)")
        print(f"### {cs[0].get('document_keyword')}")
        print(f"source: {url}")
        for c in cs:
            txt = " ".join((c.get("content") or "").split())
            print(f"  [sim {c.get('similarity',0):.2f}] {txt[:600]}")
        print()
    print("Tip: to read a full doc, run:  full <source_url>")


def cmd_full(args, key):
    """Fetch full markdown for a docs.agora.io HTML URL by appending .md."""
    url = args.url.split("?")[0].rstrip("/")
    plat = None
    if "?platform=" in args.url:
        plat = args.url.split("?platform=")[1].split("&")[0]
    # docs.agora.io -> docs-md.agora.io + .md ; api-ref served as-is
    if "docs.agora.io" in url:
        md = url.replace("docs.agora.io", "docs-md.agora.io")
        md = md + (f"_{plat}.md" if plat else ".md")
    else:
        md = url + ".md"
    req = urllib.request.Request(md)
    req.add_header("User-Agent", "curl/8.4.0")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            print(r.read().decode(errors="replace"))
    except Exception as e:
        sys.exit(f"ERROR: could not fetch {md}: {e}")


def main():
    p = argparse.ArgumentParser(description="Search the Agora docs knowledge base (RAGFlow).")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("search", help="semantic retrieval over the docs KB")
    s.add_argument("query")
    s.add_argument("--scope", choices=["docs", "apiref", "both"], default="both")
    s.add_argument("--top-k", type=int, default=6)
    s.add_argument("--threshold", type=float, default=0.2)
    s.add_argument("--candidates", type=int, default=20,
                   help="candidate pool retrieved before rerank (default 20)")
    s.add_argument("--no-rerank", action="store_true",
                   help="skip NVIDIA rerank, use raw hybrid order")
    s.add_argument("--json", action="store_true")

    f = sub.add_parser("full", help="fetch full markdown of a doc by URL")
    f.add_argument("url")

    # allow bare `search.py "query"` as a shortcut for search
    args, extra = p.parse_known_args()
    if args.cmd is None:
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
            args = s.parse_args(sys.argv[1:])
            args.cmd = "search"
        else:
            p.print_help()
            sys.exit(1)

    key = get_key()
    if args.cmd == "search":
        cmd_search(args, key)
    elif args.cmd == "full":
        cmd_full(args, key)


if __name__ == "__main__":
    main()
