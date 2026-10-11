"""Confirm one model price for subsequent Account usage captures."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from backend.repositories.ticket_repository import create_ticket_repository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--input", type=float)
    parser.add_argument("--cached", type=float)
    parser.add_argument("--output", type=float)
    parser.add_argument("--embedding", type=float)
    parser.add_argument("--source-url", required=True)
    args = parser.parse_args()
    if not any(value is not None for value in (args.input, args.cached, args.output, args.embedding)):
        parser.error("at least one price is required")
    if any(value is not None and value < 0 for value in (args.input, args.cached, args.output, args.embedding)):
        parser.error("prices must be non-negative")
    now = datetime.now(timezone.utc).isoformat()
    repository = create_ticket_repository()
    repository.initialize()
    result = repository.upsert_llm_pricing({
        "provider": args.provider,
        "model": args.model,
        "input_usd_per_1m": args.input,
        "cached_input_usd_per_1m": args.cached,
        "output_usd_per_1m": args.output,
        "embedding_usd_per_1m": args.embedding,
        "pricing_source": "user_confirmed",
        "source_url": args.source_url,
        "observed_at": now,
        "last_checked_at": now,
        "last_error": None,
    })
    print({"provider": result.get("provider"), "model": result.get("model"), "pricing_source": result.get("pricing_source")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
