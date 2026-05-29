from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
import mimetypes
import re

from app.core.config import Settings


def object_storage_enabled(settings: Settings) -> bool:
    return bool(
        settings.s3_bucket
        and settings.s3_endpoint
        and settings.s3_access_key
        and settings.s3_secret_key
    )


def _clean_key_part(value: str) -> str:
    part = value.strip().strip("/")
    part = re.sub(r"[^A-Za-z0-9._/-]+", "-", part)
    part = re.sub(r"-{2,}", "-", part)
    return part.strip("-/")


def conversation_object_key(
    settings: Settings,
    conversation_id: str,
    scope: str,
    relative_path: str,
) -> str:
    prefix = _clean_key_part(settings.object_storage_prefix or "rayue-agent")
    clean_conversation = _clean_key_part(conversation_id)
    clean_scope = _clean_key_part(scope)
    relative = PurePosixPath(relative_path)
    if relative.parts and relative.parts[0] == clean_scope:
        relative = PurePosixPath(*relative.parts[1:])
    clean_relative = _clean_key_part(relative.as_posix())
    if not clean_conversation or not clean_scope or not clean_relative:
        raise ValueError("conversation id, scope, and object path must not be empty")
    return f"{prefix}/conversations/{clean_conversation}/{clean_scope}/{clean_relative}"


def content_type_for_path(path: Path | str) -> str:
    guessed = mimetypes.guess_type(str(path))[0]
    return guessed or "application/octet-stream"


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        if not object_storage_enabled(settings):
            raise RuntimeError("S3/R2 object storage is not fully configured")
        self.settings = settings
        assert settings.s3_bucket is not None
        self.bucket = settings.s3_bucket
        self._cached_client = None

    def _client(self):
        if self._cached_client is not None:
            return self._cached_client
        try:
            import boto3
            from botocore.client import Config
        except ImportError as exc:
            raise RuntimeError("boto3 is required when S3/R2 storage is configured") from exc

        self._cached_client = boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint,
            region_name=self.settings.s3_region,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            config=Config(signature_version="s3v4"),
        )
        return self._cached_client

    def object_exists(self, *, key: str, size: int | None = None) -> bool:
        try:
            response = self._client().head_object(Bucket=self.bucket, Key=key)
        except Exception:
            return False
        if size is not None and int(response.get("ContentLength") or -1) != size:
            return False
        return True

    def ensure_path_uploaded(self, *, key: str, path: Path, content_type: str) -> None:
        size = path.stat().st_size
        if self.object_exists(key=key, size=size):
            return
        self._client().upload_file(
            Filename=str(path),
            Bucket=self.bucket,
            Key=key,
            ExtraArgs={"ContentType": content_type},
        )

    def presigned_download_url(self, *, key: str, expires_in: int) -> str:
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )


def build_object_storage(settings: Settings) -> ObjectStorage | None:
    if not object_storage_enabled(settings):
        return None
    return ObjectStorage(settings)
