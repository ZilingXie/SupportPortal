#!/usr/bin/env python3
"""Enable Archer cross-channel co-hosting for one Agora AppID."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote, urlencode


ARCHER_URL = "https://archer.agora.io"
UAP_TYPE_ID = 6
TARGET_STATUS = 1
TARGET_REGION = 2
TARGET_MAX_SUBSCRIBE_LOAD = 50
APP_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
INVALID_KEYWORD_MESSAGE = "关键词必须为整数或 32 位字符串"
PROJECT_NOT_FOUND_MESSAGE = "查无项目"


class EnablementError(RuntimeError):
    """A failure that can be reported safely to the operator."""


@dataclass(frozen=True)
class Project:
    vendor_id: int | str
    company_id: int | str
    project_id: int | str
    project_name: str


class PilotClient:
    def __init__(self) -> None:
        configured = os.environ.get("PILOT_BIN", "pilot")
        self.command = shlex.split(configured)
        if not self.command:
            raise EnablementError("PILOT_BIN 不能为空")

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        command = [*self.command, "archer", "call", method, path]
        if body is not None:
            command.append(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
        command.extend(["--url", ARCHER_URL, "--output", "json"])

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise EnablementError("未找到 pilot，请先安装 Pilot") from exc
        except subprocess.TimeoutExpired as exc:
            raise EnablementError("Pilot 调用 Archer 超时") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            if "token expired" in detail.lower():
                raise EnablementError(
                    "Pilot 的 Archer 登录已过期，请运行 pilot archer login 后重试"
                )
            raise EnablementError(detail or f"Pilot 调用失败，退出码 {completed.returncode}")

        raw = completed.stdout.strip()
        if not raw:
            raise EnablementError("Pilot 未返回 Archer 响应")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EnablementError("Pilot 返回的 Archer 响应不是有效 JSON") from exc

        status = _first_direct(payload, ("status", "statusCode", "status_code"))
        if _is_http_error(status):
            message = _extract_message(payload) or f"Archer 返回 HTTP {status}"
            raise EnablementError(message)

        if isinstance(payload, dict) and "body" in payload:
            payload = payload["body"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise EnablementError("Pilot 返回的 Archer body 不是有效 JSON") from exc
        return payload


def _is_http_error(value: Any) -> bool:
    try:
        return int(value) >= 400
    except (TypeError, ValueError):
        return False


def _first_direct(value: Any, keys: Iterable[str]) -> Any:
    if not isinstance(value, dict):
        return None
    for key in keys:
        candidate = value.get(key)
        if candidate is not None and candidate != "":
            return candidate
    return None


def _first_nested(value: Any, keys: Iterable[str]) -> Any:
    wanted = tuple(keys)
    if isinstance(value, dict):
        direct = _first_direct(value, wanted)
        if direct is not None:
            return direct
        for nested in value.values():
            candidate = _first_nested(nested, wanted)
            if candidate is not None:
                return candidate
    elif isinstance(value, list):
        for nested in value:
            candidate = _first_nested(nested, wanted)
            if candidate is not None:
                return candidate
    return None


def _extract_message(payload: Any) -> str | None:
    message = _first_nested(payload, ("message", "msg", "error"))
    return str(message) if message is not None else None


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("data", "result", "list", "items", "records", "rows", "content"):
        if key in payload:
            return _records(payload[key])
    return [payload]


def _appid_in(record: dict[str, Any]) -> str | None:
    value = _first_nested(record, ("appId", "appID", "appid", "appKey", "app_key"))
    return str(value) if value is not None else None


def _select_exact_record(records: list[dict[str, Any]], appid: str) -> dict[str, Any] | None:
    exact = [record for record in records if (_appid_in(record) or "").lower() == appid.lower()]
    if exact:
        return exact[0]
    if len(records) == 1 and _appid_in(records[0]) is None:
        return records[0]
    return None


def _normalize_id(value: Any) -> int | str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text) if text.isdecimal() else text


def _project_from(
    check_record: dict[str, Any], search_record: dict[str, Any] | None
) -> Project:
    vendor_id = _normalize_id(
        _first_nested(check_record, ("vendorId", "vendor_id"))
    )
    company_id = _normalize_id(
        _first_nested(check_record, ("companyId", "company_id"))
    )
    project_id = _normalize_id(
        _first_nested(check_record, ("projectId", "project_id"))
    )
    project_name = _first_nested(check_record, ("projectName", "project_name"))

    if search_record is not None:
        vendor_id = vendor_id or _normalize_id(
            _first_nested(search_record, ("vendorId", "vendor_id"))
        )
        company_id = company_id or _normalize_id(
            _first_nested(search_record, ("companyId", "company_id"))
        )
        project_id = project_id or _normalize_id(
            _first_direct(search_record, ("projectId", "project_id", "id"))
        )
        project_name = project_name or _first_direct(
            search_record, ("projectName", "project_name", "name")
        )

    missing = [
        name
        for name, value in (
            ("vendorId", vendor_id),
            ("companyId", company_id),
            ("projectId", project_id),
            ("projectName", project_name),
        )
        if value is None or value == ""
    ]
    if missing:
        raise EnablementError(f"Archer 项目响应缺少字段：{', '.join(missing)}")

    return Project(
        vendor_id=vendor_id,
        company_id=company_id,
        project_id=project_id,
        project_name=str(project_name),
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _matches_target(config: dict[str, Any] | None) -> bool:
    if config is None:
        return False
    return (
        _as_int(_first_nested(config, ("status",))) == TARGET_STATUS
        and _as_int(_first_nested(config, ("region",))) == TARGET_REGION
        and _as_int(_first_nested(config, ("maxSubscribeLoad", "max_subscribe_load")))
        == TARGET_MAX_SUBSCRIBE_LOAD
    )


def _find_config(payload: Any, appid: str) -> dict[str, Any] | None:
    return _select_exact_record(_records(payload), appid)


def enable(appid: str, client: PilotClient) -> str:
    encoded = urlencode({"keywords": appid})
    check_payload = client.call("GET", f"/api/v2/check-simple-vendor?{encoded}")
    check_records = _records(check_payload)
    check_record = _select_exact_record(check_records, appid)
    if check_record is None:
        return PROJECT_NOT_FOUND_MESSAGE

    search_payload = client.call(
        "GET", f"/api/v2/search-project?{encoded}&fuzzy=false"
    )
    search_record = _select_exact_record(_records(search_payload), appid)
    if search_record is None and _first_nested(
        check_record, ("projectId", "project_id")
    ) is None:
        return PROJECT_NOT_FOUND_MESSAGE

    project = _project_from(check_record, search_record)
    uap_query_path = f"/api/v2/agora-config/uap-app/{UAP_TYPE_ID}/uap?{encoded}"
    existing = _find_config(client.call("GET", uap_query_path), appid)

    operation = "无需更新"
    if existing is None:
        create_path = (
            f"/api/v2/company/{quote(str(project.company_id), safe='')}"
            f"/project/{quote(str(project.project_id), safe='')}"
            f"/uap-type/{UAP_TYPE_ID}"
        )
        client.call(
            "POST",
            create_path,
            {
                "vendorId": project.vendor_id,
                "appKey": appid,
                "companyId": project.company_id,
                "projectId": project.project_id,
                "projectName": project.project_name,
                "maxSubscribeLoad": TARGET_MAX_SUBSCRIBE_LOAD,
                "status": TARGET_STATUS,
                "region": TARGET_REGION,
            },
        )
        operation = "创建"
    elif not _matches_target(existing):
        update_path = (
            f"/api/v2/company/{quote(str(project.company_id), safe='')}"
            f"/project/{quote(str(project.project_id), safe='')}"
            f"/uap-type/{UAP_TYPE_ID}"
        )
        client.call(
            "PUT",
            update_path,
            {
                "status": TARGET_STATUS,
                "region": TARGET_REGION,
                "maxSubscribeLoad": TARGET_MAX_SUBSCRIBE_LOAD,
            },
        )
        operation = "更新"

    verified = existing if operation == "无需更新" else _find_config(
        client.call("GET", uap_query_path), appid
    )
    if not _matches_target(verified):
        raise EnablementError(
            "写入后读回不一致，期望 status=1、region=2、maxSubscribeLoad=50"
        )

    return "\n".join(
        (
            "开启结果：成功",
            f"AppID：{appid}",
            "region：oversea",
            "max subscribe load：50",
            f"操作：{operation}",
        )
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(INVALID_KEYWORD_MESSAGE)
        return 2

    appid = argv[1].strip()
    if not APP_ID_PATTERN.fullmatch(appid):
        print(INVALID_KEYWORD_MESSAGE)
        return 2

    try:
        result = enable(appid, PilotClient())
    except EnablementError as exc:
        print("开启结果：失败")
        print(f"原因：{exc}")
        return 1

    print(result)
    return 3 if result == PROJECT_NOT_FOUND_MESSAGE else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

