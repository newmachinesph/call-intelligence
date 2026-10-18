"""Synthetic signing tests never contact R2 or use real credentials."""

from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config

from call_intelligence_api import storage


def test_presigned_upload_is_short_lived_and_content_type_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = storage.R2Settings(
        _env_file=None,
        r2_account_id="a" * 32,
        r2_access_key_id="synthetic-access",
        r2_secret_access_key="synthetic-secret",
        r2_bucket_name="synthetic-private",
        r2_endpoint_url=f"https://{'a' * 32}.r2.cloudflarestorage.com",
    )
    client = boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
        region_name="auto",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    monkeypatch.setattr(storage, "r2_settings", lambda: settings)
    monkeypatch.setattr(storage, "r2_client", lambda: client)

    key = storage.staging_key(uuid4(), uuid4(), uuid4())
    signed = urlparse(storage.sign_upload(key))
    query = parse_qs(signed.query)
    assert signed.hostname == f"{'a' * 32}.r2.cloudflarestorage.com"
    assert signed.path == f"/synthetic-private/{key}"
    assert query["X-Amz-Expires"] == ["300"]
    assert "content-type" in query["X-Amz-SignedHeaders"][0]
    assert "synthetic-secret" not in signed.geturl()

    final_key = storage.original_key(uuid4(), uuid4(), "b" * 64)
    assert parse_qs(urlparse(storage.sign_playback(final_key)).query)["X-Amz-Expires"] == ["60"]
    with pytest.raises(ValueError):
        storage.sign_playback(key)


def test_r2_endpoint_must_match_account() -> None:
    with pytest.raises(ValueError):
        storage.R2Settings(
            _env_file=None,
            r2_account_id="a" * 32,
            r2_access_key_id="synthetic-access",
            r2_secret_access_key="synthetic-secret",
            r2_bucket_name="synthetic-private",
            r2_endpoint_url=f"https://{'b' * 32}.r2.cloudflarestorage.com",
        )
