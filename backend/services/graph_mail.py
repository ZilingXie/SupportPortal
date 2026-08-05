from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


GRAPH_SENDMAIL_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
DEFAULT_TENANT_ID = "60275374-3eaa-49c2-83c3-cc189d126981"
DEFAULT_CLIENT_ID = "cb5aaefe-2ee2-4ac9-a3ee-5490ddf70d80"
DEFAULT_USERNAME = "ai-support-agent@agora.io"
DEFAULT_TOKEN_CACHE = ".msgraph/billing-automation-token.json"


def load_graph_mail_config() -> dict[str, str]:
    return {
        "tenant_id": _env("MSGRAPH_TENANT_ID", "BILLING_AUTOMATION_GRAPH_TENANT_ID") or DEFAULT_TENANT_ID,
        "client_id": _env("MSGRAPH_CLIENT_ID", "BILLING_AUTOMATION_GRAPH_CLIENT_ID") or DEFAULT_CLIENT_ID,
        "client_secret": _env("MSGRAPH_CLIENT_SECRET", "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET"),
        "username": _env("MSGRAPH_USERNAME", "BILLING_AUTOMATION_GRAPH_USERNAME") or DEFAULT_USERNAME,
        "token_cache": _env("MSGRAPH_TOKEN_CACHE", "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE")
        or DEFAULT_TOKEN_CACHE,
    }


def acquire_graph_access_token(config: dict[str, str]) -> str:
    cache_path = Path(config["token_cache"]).expanduser()
    token_cache = _read_token_cache(cache_path)
    access_token, expires_at = _cached_access_token(token_cache)
    if access_token and expires_at > int(time.time()) + 60:
        return access_token

    refresh_token = _cached_refresh_token(token_cache)
    if not refresh_token:
        raise ValueError("missing refresh_token in Graph token cache")
    form = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "User.Read User.ReadBasic.All Mail.ReadWrite Mail.Send MailboxSettings.Read offline_access",
    }
    payload = _post_form_json(
        f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token",
        form,
    )
    access_token = _clean(payload.get("access_token"))
    if not access_token:
        raise RuntimeError("Microsoft Graph token refresh did not return access_token")
    _write_token_cache(
        cache_path,
        {
            **token_cache,
            "access_token": access_token,
            "refresh_token": _clean(payload.get("refresh_token")) or refresh_token,
            "expires_at": int(time.time()) + _safe_int(payload.get("expires_in"), 3600),
            "username": config["username"],
        },
    )
    return access_token


def send_graph_mail(
    *,
    to_address: str,
    subject: str,
    body: str,
    content_type: str = "Text",
) -> None:
    config = load_graph_mail_config()
    missing = [name for name, value in config.items() if not value]
    if missing:
        raise ValueError(f"missing Graph mail config: {', '.join(missing)}")
    access_token = acquire_graph_access_token(config)
    send_graph_mail_with_token(
        access_token=access_token,
        to_address=to_address,
        subject=subject,
        body=body,
        content_type=content_type,
    )


def send_graph_mail_with_token(
    *,
    access_token: str,
    to_address: str,
    subject: str,
    body: str,
    content_type: str = "Text",
) -> None:
    normalized_content_type = str(content_type or "Text").strip().lower()
    if normalized_content_type not in {"text", "html"}:
        raise ValueError("Graph mail content_type must be Text or HTML")
    graph_content_type = "HTML" if normalized_content_type == "html" else "Text"
    request = urllib.request.Request(
        GRAPH_SENDMAIL_URL,
        data=json.dumps(
            {
                "message": {
                    "subject": subject,
                    "body": {"contentType": graph_content_type, "content": body},
                    "toRecipients": [{"emailAddress": {"address": to_address}}],
                },
                "saveToSentItems": True,
            }
        ).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status not in {200, 202}:
            raise RuntimeError(f"Microsoft Graph sendMail returned HTTP {response.status}")


def _env(primary: str, fallback: str) -> str:
    return _clean(os.getenv(primary)) or _clean(os.getenv(fallback))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _read_token_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing Graph token cache: {path}")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Graph token cache: {path}") from exc
    return parsed if isinstance(parsed, dict) else {}


def _write_token_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _cached_access_token(cache: dict[str, Any]) -> tuple[str, int]:
    token = _clean(cache.get("access_token"))
    if token:
        return token, _safe_int(cache.get("expires_at"), 0)
    records = cache.get("AccessToken")
    if isinstance(records, dict):
        for record in records.values():
            if isinstance(record, dict):
                token = _clean(record.get("secret"))
                target = _clean(record.get("target")).lower()
                if token and ("mail.send" in target or not target):
                    return token, _safe_int(record.get("expires_on"), 0)
    return "", 0


def _cached_refresh_token(cache: dict[str, Any]) -> str:
    token = _clean(cache.get("refresh_token"))
    if token:
        return token
    records = cache.get("RefreshToken")
    if isinstance(records, dict):
        for record in records.values():
            if isinstance(record, dict) and _clean(record.get("secret")):
                return _clean(record.get("secret"))
    return ""


def _post_form_json(url: str, form: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    return payload if isinstance(payload, dict) else {}
