from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_ASSET_ALLOWED_EXTENSIONS = ".log,.err,.txt"
DEFAULT_ASSET_UPLOAD_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_ASSET_PRESIGN_TTL_SECONDS = 300


def _safe_int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def asset_upload_max_bytes() -> int:
    return _safe_int_env("ASSET_UPLOAD_MAX_BYTES", DEFAULT_ASSET_UPLOAD_MAX_BYTES)


def asset_presign_ttl_seconds() -> int:
    return _safe_int_env("ASSET_PRESIGN_TTL_SECONDS", DEFAULT_ASSET_PRESIGN_TTL_SECONDS)


def allowed_asset_extensions() -> set[str]:
    raw = os.getenv("ASSET_ALLOWED_EXTENSIONS") or DEFAULT_ASSET_ALLOWED_EXTENSIONS
    values = {
        item.strip().lower() if item.strip().startswith(".") else f".{item.strip().lower()}"
        for item in raw.split(",")
        if item.strip()
    }
    return values or {".log", ".err", ".txt"}


def sanitize_asset_filename(file_name: str) -> str:
    normalized = Path(str(file_name or "attachment.log")).name
    clean_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", normalized).strip(".-")
    return clean_name or "attachment.log"


def validate_asset_upload_request(file_name: str, size_bytes: int) -> tuple[str, str]:
    safe_name = sanitize_asset_filename(file_name)
    extension = Path(safe_name).suffix.lower()
    allowed = allowed_asset_extensions()
    if extension not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"Only {allowed_text} files are supported")
    if int(size_bytes or 0) <= 0:
        raise ValueError("Uploaded file is empty")
    max_bytes = asset_upload_max_bytes()
    if int(size_bytes) > max_bytes:
        raise ValueError(f"Uploaded file is too large. Max size is {max_bytes} bytes.")
    return safe_name, extension


def build_asset_s3_key(*, ticket_id: str, asset_id: str, file_name: str) -> str:
    prefix = str(os.getenv("ASSET_S3_PREFIX") or "supportportal").strip().strip("/") or "supportportal"
    safe_ticket_id = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(ticket_id or "ticket").strip()).strip(".-") or "ticket"
    safe_asset_id = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(asset_id or "asset").strip()).strip(".-") or "asset"
    return f"{prefix}/tickets/{safe_ticket_id}/assets/{safe_asset_id}/{sanitize_asset_filename(file_name)}"


def create_asset_id() -> str:
    return f"ASSET-{uuid4().hex[:12].upper()}"


class S3AssetStorage:
    def __init__(self, *, bucket: str | None = None, region: str | None = None) -> None:
        self.bucket = str(bucket or os.getenv("ASSET_S3_BUCKET") or "").strip()
        self.region = str(region or os.getenv("ASSET_S3_REGION") or os.getenv("AWS_REGION") or "").strip()
        if not self.bucket:
            raise RuntimeError("ASSET_S3_BUCKET is required")
        if not self.region:
            raise RuntimeError("ASSET_S3_REGION is required")
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def create_presigned_post(self, asset: dict[str, Any]) -> dict[str, Any]:
        key = str(asset.get("s3_key") or "").strip()
        content_type = str(asset.get("content_type") or "application/octet-stream").strip()
        fields: dict[str, str] = {
            "Content-Type": content_type,
        }
        conditions: list[Any] = [
            {"Content-Type": content_type},
            ["content-length-range", 1, asset_upload_max_bytes()],
        ]
        kms_key_id = str(os.getenv("ASSET_S3_KMS_KEY_ID") or "").strip()
        if kms_key_id:
            fields["x-amz-server-side-encryption"] = "aws:kms"
            fields["x-amz-server-side-encryption-aws-kms-key-id"] = kms_key_id
            conditions.extend(
                [
                    {"x-amz-server-side-encryption": "aws:kms"},
                    {"x-amz-server-side-encryption-aws-kms-key-id": kms_key_id},
                ]
            )
        return self.client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=asset_presign_ttl_seconds(),
        )

    def verify_uploaded(self, asset: dict[str, Any]) -> dict[str, Any]:
        response = self.client.head_object(
            Bucket=str(asset.get("bucket") or self.bucket),
            Key=str(asset.get("s3_key") or ""),
        )
        return {
            "size_bytes": int(response.get("ContentLength") or 0),
            "etag": str(response.get("ETag") or "").strip('"') or None,
            "checksum": response.get("ChecksumSHA256") or response.get("ChecksumCRC32") or None,
        }

    def create_download_url(self, asset: dict[str, Any]) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": str(asset.get("bucket") or self.bucket),
                "Key": str(asset.get("s3_key") or ""),
            },
            ExpiresIn=asset_presign_ttl_seconds(),
        )

    def store_bytes(self, asset: dict[str, Any], content: bytes) -> dict[str, Any]:
        extra_args: dict[str, Any] = {
            "Bucket": str(asset.get("bucket") or self.bucket),
            "Key": str(asset.get("s3_key") or ""),
            "Body": content,
            "ContentType": str(asset.get("content_type") or "application/octet-stream").strip(),
        }
        kms_key_id = str(os.getenv("ASSET_S3_KMS_KEY_ID") or "").strip()
        if kms_key_id:
            extra_args["ServerSideEncryption"] = "aws:kms"
            extra_args["SSEKMSKeyId"] = kms_key_id
        response = self.client.put_object(**extra_args)
        return {
            "etag": str(response.get("ETag") or "").strip('"') or None,
            "checksum": response.get("ChecksumSHA256") or response.get("ChecksumCRC32") or None,
        }

    def fetch_bytes(self, asset: dict[str, Any]) -> bytes:
        response = self.client.get_object(
            Bucket=str(asset.get("bucket") or self.bucket),
            Key=str(asset.get("s3_key") or ""),
        )
        body = response.get("Body")
        if body is None:
            raise RuntimeError(f"asset object body is empty for {asset.get('s3_key') or asset.get('asset_id')}")
        return body.read()


class MissingAssetStorage:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def create_presigned_post(self, asset: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(self.reason)

    def verify_uploaded(self, asset: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(self.reason)

    def create_download_url(self, asset: dict[str, Any]) -> str:
        raise RuntimeError(self.reason)

    def store_bytes(self, asset: dict[str, Any], content: bytes) -> dict[str, Any]:
        _ = content
        raise RuntimeError(self.reason)

    def fetch_bytes(self, asset: dict[str, Any]) -> bytes:
        raise RuntimeError(self.reason)


def create_asset_storage() -> S3AssetStorage:
    bucket = str(os.getenv("ASSET_S3_BUCKET") or "").strip()
    region = str(os.getenv("ASSET_S3_REGION") or os.getenv("AWS_REGION") or "").strip()
    if not bucket or not region:
        missing = []
        if not bucket:
            missing.append("ASSET_S3_BUCKET")
        if not region:
            missing.append("ASSET_S3_REGION")
        return MissingAssetStorage(f"Asset storage is not configured. Missing {', '.join(missing)}.")
    return S3AssetStorage()
