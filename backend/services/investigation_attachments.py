"""Authenticated attachment transfer; bytes are never persisted or model input."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.error
import urllib.request
from typing import Any

from backend.services.engineer_slack import EngineerSlackDeliveryError
from backend.services.zendesk_comments import _basic_auth_header, ZENDESK_TICKET_API_BASE

MAX_FILE_BYTES = 50 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _slack(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request("https://slack.com/api/" + method,
        data=urllib.parse.urlencode({key: json.dumps(value) if isinstance(value, (list, dict)) else value for key, value in payload.items()}).encode(), headers={
            "Authorization": "Bearer " + str(os.getenv("ENGINEER_SLACK_ACCESS_TOKEN") or ""),
            "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise EngineerSlackDeliveryError(f"attachment_slack_http_{exc.code}", outcome_unknown=method == "files.completeUploadExternal" and exc.code >= 500) from exc
    except (OSError, ValueError) as exc:
        raise EngineerSlackDeliveryError("attachment_slack_request_failed", outcome_unknown=method == "files.completeUploadExternal") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise EngineerSlackDeliveryError("attachment_slack_" + str(result.get("error", "invalid_response") if isinstance(result, dict) else "invalid_response"))
    return result


def _download(url: str, authorization: str, *, provider: str) -> bytes:
    opener = urllib.request.build_opener(_NoRedirect())
    original_host = urllib.parse.urlparse(url).hostname
    for _ in range(5):
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        authenticated_host = host.endswith(".slack.com") if provider == "slack" else host.endswith(".zendesk.com")
        cdn_host = provider == "zendesk" and host.endswith(".zdusercontent.com")
        if parsed.scheme != "https" or not (authenticated_host or cdn_host) or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("attachment_download_host_invalid")
        # CDN signed URLs do not need our credentials. Never forward them across hosts.
        headers = {"Authorization": authorization} if authenticated_host and host == original_host else {}
        request = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(request, timeout=60) as response:
                data = response.read(MAX_FILE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308) or not exc.headers.get("Location"):
                raise
            url = urllib.parse.urljoin(url, exc.headers["Location"])
            continue
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError("attachment_download_size_invalid")
        return data
    raise ValueError("attachment_download_redirect_limit")


def validate_slack_files(files: Any, *, channel_id: str, thread_ts: str,
                         message_ts: str, actor: str, source_event_id: str) -> list[dict[str, Any]]:
    if not isinstance(files, list) or len(files) > 10:
        raise ValueError("attachment_files_invalid")
    if not files:
        return []
    if not actor or not message_ts or not source_event_id:
        raise ValueError("attachment_source_identity_required")
    result = []
    seen = set()
    for supplied in files:
        file_id = str(supplied.get("file_id") or "") if isinstance(supplied, dict) else ""
        file = _slack("files.info", {"file": file_id}).get("file") or {}
        shares = (file.get("shares") or {}).get("private", {}) | (file.get("shares") or {}).get("public", {})
        belongs = any(share.get("ts") == message_ts and share.get("thread_ts") == thread_ts for share in shares.get(channel_id, []))
        if file.get("id") != file_id or file.get("user") != actor or not belongs:
            raise ValueError("attachment_message_identity_mismatch")
        if not file or file_id in seen or file.get("is_external") or file.get("mode") == "tombstone":
            raise ValueError("attachment_file_not_in_bound_message")
        seen.add(file_id)
        size = int(file.get("size") or 0)
        if size <= 0 or size > MAX_FILE_BYTES:
            raise ValueError("attachment_file_size_invalid")
        result.append({"slack_file_id": file_id, "source_message_ts": message_ts,
            "source_event_id": source_event_id, "channel_id": channel_id, "thread_ts": thread_ts,
            "actor_id": actor, "file_name": str(file.get("name") or "attachment"),
            "content_type": str(file.get("mimetype") or "application/octet-stream"), "size_bytes": size})
    return result


def download_slack_attachment(ref: dict[str, Any]) -> bytes:
    # Revalidate message membership at delivery, including deleted/revoked files.
    current = validate_slack_files([{"file_id": ref["slack_file_id"]}],
        channel_id=ref["channel_id"], thread_ts=ref["thread_ts"],
        message_ts=ref["source_message_ts"], actor=ref["actor_id"], source_event_id=ref["source_event_id"])
    if any(current[0][k] != ref[k] for k in ("file_name", "size_bytes", "content_type")):
        raise ValueError("attachment_source_changed")
    file = _slack("files.info", {"file": ref["slack_file_id"]}).get("file") or {}
    if file.get("id") != ref["slack_file_id"] or not file.get("url_private_download"):
        raise ValueError("attachment_source_unavailable_please_reattach")
    data = _download(file["url_private_download"], "Bearer " + str(os.getenv("ENGINEER_SLACK_ACCESS_TOKEN") or ""), provider="slack")
    if len(data) != ref["size_bytes"]:
        raise ValueError("attachment_size_mismatch")
    return data


def download_zendesk_attachment(*, ticket_id: str, comment_id: str, attachment_id: str) -> bytes:
    # Resolve private URL from the authenticated ticket comment, never accept a caller URL.
    request = urllib.request.Request(f"{ZENDESK_TICKET_API_BASE}/{urllib.parse.quote(ticket_id, safe='')}/comments.json",
        headers={"Authorization": _basic_auth_header()})
    seen_urls = set()
    while request:
        if request.full_url in seen_urls:
            raise ValueError("attachment_zendesk_pagination_loop")
        seen_urls.add(request.full_url)
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        comment = next((c for c in payload.get("comments", []) if str(c.get("id")) == comment_id), None)
        if comment:
            attachment = next((a for a in comment.get("attachments", []) if str(a.get("id")) == attachment_id), None)
            if not attachment or comment.get("public") is not True:
                raise ValueError("attachment_zendesk_source_unavailable")
            return _download(str(attachment.get("content_url") or ""), _basic_auth_header(), provider="zendesk")
        next_url = payload.get("next_page")
        if not next_url:
            break
        if not str(next_url).startswith(ZENDESK_TICKET_API_BASE + "/"):
            raise ValueError("attachment_zendesk_pagination_invalid")
        request = urllib.request.Request(next_url, headers={"Authorization": _basic_auth_header()})
    raise ValueError("attachment_zendesk_comment_missing")


def upload_slack_attachment(*, data: bytes, file_name: str, channel_id: str, thread_ts: str,
                            persist_file_id: Any) -> dict[str, Any]:
    slot = _slack("files.getUploadURLExternal", {"filename": file_name, "length": len(data)})
    file_id = str(slot.get("file_id") or "")
    url = str(slot.get("upload_url") or "")
    if not file_id or not url.startswith("https://files.slack.com/"):
        raise EngineerSlackDeliveryError("attachment_upload_slot_invalid")
    persist_file_id(file_id)
    with urllib.request.build_opener(_NoRedirect()).open(urllib.request.Request(url, data=data, method="POST"), timeout=60):
        pass
    completed = _slack("files.completeUploadExternal", {"files": [{"id": file_id, "title": file_name}],
        "channel_id": channel_id, "thread_ts": thread_ts})
    if not any(file.get("id") == file_id for file in completed.get("files", [])):
        raise EngineerSlackDeliveryError("attachment_completion_identity_unknown", outcome_unknown=True)
    return {"file_id": file_id, "channel_id": channel_id, "thread_ts": thread_ts}
