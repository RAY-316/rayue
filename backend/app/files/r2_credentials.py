from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.config import Settings


class R2CredentialError(ValueError):
    pass


@dataclass(frozen=True)
class R2TemporaryCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expires_at: int


def create_r2_temporary_credentials(
    settings: Settings,
    *,
    prefix: str,
    ttl_seconds: int,
) -> R2TemporaryCredentials:
    if not settings.s3_bucket or not settings.s3_endpoint or not settings.s3_access_key or not settings.s3_secret_key:
        raise R2CredentialError("R2 storage is not configured")
    audience = _r2_audience(settings.s3_endpoint)
    if not audience:
        raise R2CredentialError("R2 endpoint is required")

    normalized_prefix = prefix.strip("/")
    if not normalized_prefix:
        raise R2CredentialError("R2 credential prefix is required")
    now = int(time.time())
    exp = now + max(60, ttl_seconds)
    token = _jwt_encode(
        {
            "alg": "HS256",
            "typ": "JWT",
        },
        {
            "bucket": settings.s3_bucket,
            "scope": "object-read-write",
            "paths": {
                "prefixPaths": [f"{normalized_prefix}/"],
                "objectPaths": [],
            },
            "sub": _r2_account_id(settings.s3_endpoint),
            "iss": settings.s3_access_key,
            "aud": audience,
            "iat": now,
            "exp": exp,
        },
        settings.s3_secret_key,
    )
    return R2TemporaryCredentials(
        access_key_id=settings.s3_access_key,
        secret_access_key=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        session_token=base64.b64encode(f"jwt/{token}".encode("utf-8")).decode("ascii"),
        expires_at=exp,
    )


def _jwt_encode(header: dict, payload: dict, secret: str) -> str:
    header_part = _base64url_json(header)
    payload_part = _base64url_json(payload)
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_base64url(signature)}"


def _base64url_json(value: dict) -> str:
    data = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _base64url(data)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _r2_audience(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    return parsed.netloc or parsed.path


def _r2_account_id(endpoint: str) -> str:
    audience = _r2_audience(endpoint)
    if ".r2.cloudflarestorage.com" not in audience:
        return ""
    return audience.split(".", 1)[0]
