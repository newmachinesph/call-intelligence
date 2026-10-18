"""Private R2 client and short-lived, single-operation signed URLs."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse
from uuid import UUID

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class R2Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    r2_account_id: str
    r2_access_key_id: SecretStr
    r2_secret_access_key: SecretStr
    r2_bucket_name: str
    r2_endpoint_url: str

    @model_validator(mode="after")
    def check_endpoint(self) -> R2Settings:
        parsed = urlparse(self.r2_endpoint_url)
        expected = f"{self.r2_account_id}.r2.cloudflarestorage.com"
        if parsed.scheme != "https" or parsed.hostname != expected or parsed.path not in ("", "/"):
            raise ValueError("R2 endpoint must use this Cloudflare account's S3 hostname")
        return self


@lru_cache
def r2_settings() -> R2Settings:
    return R2Settings()


@lru_cache
def r2_client() -> BaseClient:
    settings = r2_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=20,
            retries={"mode": "standard", "max_attempts": 3},
        ),
    )


def staging_key(organization_id: UUID, call_id: UUID, upload_id: UUID) -> str:
    return f"staging/{organization_id}/{call_id}/{upload_id}.mp3"


def original_key(organization_id: UUID, call_id: UUID, sha256_hex: str) -> str:
    if len(sha256_hex) != 64 or any(c not in "0123456789abcdef" for c in sha256_hex):
        raise ValueError("Invalid SHA-256 digest")
    return f"audio/{organization_id}/{call_id}/{sha256_hex}.mp3"


def sign_upload(key: str, *, expires_seconds: int = 300) -> str:
    if not key.startswith("staging/") or not 1 <= expires_seconds <= 600:
        raise ValueError("Invalid upload signing request")
    settings = r2_settings()
    return r2_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.r2_bucket_name, "Key": key, "ContentType": "audio/mpeg"},
        ExpiresIn=expires_seconds,
        HttpMethod="PUT",
    )


def sign_playback(key: str, *, expires_seconds: int = 60) -> str:
    if not key.startswith("audio/") or not 1 <= expires_seconds <= 300:
        raise ValueError("Invalid playback signing request")
    settings = r2_settings()
    return r2_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket_name, "Key": key},
        ExpiresIn=expires_seconds,
        HttpMethod="GET",
    )
