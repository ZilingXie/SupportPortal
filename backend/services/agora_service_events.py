from __future__ import annotations

import copy
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import logging
import re
import threading
import time
from typing import Any
import urllib.request
import xml.etree.ElementTree as ET

LOGGER = logging.getLogger(__name__)

STATUS_PAGE_URL = "https://status.agora.io/"
STATUS_EVENTS_RSS_URL = f"{STATUS_PAGE_URL}history.rss"
SERVICE_EVENTS_CACHE_TTL_SECONDS = 5 * 60
MAX_SERVICE_EVENTS = 3

_KNOWN_STATUS_LABELS = {
    "investigating",
    "identified",
    "monitoring",
    "resolved",
    "minor",
    "major",
    "critical",
    "error",
    "update",
    "completed",
}
_SUMMARY_HEADINGS = {"summary", "summarize"}
_CACHE_LOCK = threading.Lock()
_CACHE_EXPIRES_AT_MONOTONIC = 0.0
_CACHE_PAYLOAD: dict[str, Any] | None = None


class _HtmlLineExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"p", "div", "li", "br"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "li"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def lines(self) -> list[str]:
        raw_text = unescape("".join(self._parts))
        normalized_lines: list[str] = []
        for line in raw_text.splitlines():
            collapsed = " ".join(line.split()).strip()
            if collapsed:
                normalized_lines.append(collapsed)
        return normalized_lines


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _sanitize_http_url(value: Any) -> str:
    text = _clean_text(value)
    if text.startswith("https://") or text.startswith("http://"):
        return text
    return ""


def _extract_description_fields(description_html: Any) -> tuple[str, str, str]:
    parser = _HtmlLineExtractor()
    parser.feed(str(description_html or ""))
    parser.close()
    lines = parser.lines()
    if not lines:
        return "", "", ""

    posted_at_label = ""
    if lines and lines[0].lower().startswith("posted "):
        posted_at_label = lines.pop(0)

    status_label = ""
    if lines:
        first_line = lines[0]
        normalized_first_line = first_line.lower()
        if normalized_first_line in _KNOWN_STATUS_LABELS:
            status_label = first_line
            lines.pop(0)
        elif normalized_first_line in _SUMMARY_HEADINGS:
            lines.pop(0)

    summary = _clean_text(" ".join(lines))
    return posted_at_label, status_label, summary


def build_default_service_events_payload(
    *,
    items: list[dict[str, Any]] | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    normalized_items = [
        {
            "title": _clean_text(item.get("title")),
            "summary": _clean_text(item.get("summary")),
            "link": _sanitize_http_url(item.get("link")),
            "status_label": _clean_text(item.get("status_label")),
            "posted_at_label": _clean_text(item.get("posted_at_label")),
        }
        for item in (items or [])
    ]
    return {
        "items": normalized_items[:MAX_SERVICE_EVENTS],
        "status_page_url": STATUS_PAGE_URL,
        "fetched_at": _clean_text(fetched_at) or _utc_now_iso(),
    }


def parse_agora_service_events_rss(feed_text: str, *, limit: int = MAX_SERVICE_EVENTS) -> list[dict[str, str]]:
    xml_root = ET.fromstring(str(feed_text or "").strip())
    items: list[dict[str, str]] = []
    for item_node in xml_root.findall("./channel/item"):
        title = _clean_text(item_node.findtext("title"))
        link = _sanitize_http_url(item_node.findtext("link"))
        posted_at_label, status_label, summary = _extract_description_fields(item_node.findtext("description"))
        if not title and not summary and not link:
            continue
        items.append(
            {
                "title": title,
                "summary": summary,
                "link": link,
                "status_label": status_label,
                "posted_at_label": posted_at_label,
            }
        )
        if len(items) >= max(int(limit or MAX_SERVICE_EVENTS), 0):
            break
    return items


def _fetch_agora_service_events_rss(*, timeout_seconds: float = 10.0) -> str:
    request = urllib.request.Request(
        STATUS_EVENTS_RSS_URL,
        headers={
            "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
            "User-Agent": "SupportPortal/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="replace")


def get_agora_service_events_payload(
    *,
    fetcher: Any | None = None,
    current_time: float | None = None,
    cache_ttl_seconds: int = SERVICE_EVENTS_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    global _CACHE_PAYLOAD, _CACHE_EXPIRES_AT_MONOTONIC
    now_monotonic = current_time if current_time is not None else time.monotonic()
    with _CACHE_LOCK:
        if _CACHE_PAYLOAD is not None and now_monotonic < _CACHE_EXPIRES_AT_MONOTONIC:
            return copy.deepcopy(_CACHE_PAYLOAD)

    items: list[dict[str, Any]] = []
    try:
        feed_text = (fetcher or _fetch_agora_service_events_rss)()
        items = parse_agora_service_events_rss(feed_text, limit=MAX_SERVICE_EVENTS)
    except Exception as error:  # pragma: no cover - guarded by tests
        LOGGER.warning("Failed to fetch Agora service events feed: %s", error)

    payload = build_default_service_events_payload(items=items)
    with _CACHE_LOCK:
        _CACHE_PAYLOAD = copy.deepcopy(payload)
        _CACHE_EXPIRES_AT_MONOTONIC = now_monotonic + max(int(cache_ttl_seconds or 0), 0)
    return copy.deepcopy(payload)


def reset_agora_service_events_cache() -> None:
    global _CACHE_PAYLOAD, _CACHE_EXPIRES_AT_MONOTONIC
    with _CACHE_LOCK:
        _CACHE_PAYLOAD = None
        _CACHE_EXPIRES_AT_MONOTONIC = 0.0
