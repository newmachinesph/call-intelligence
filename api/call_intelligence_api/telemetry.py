"""Content-free Sentry envelope for private customer recordings."""

from __future__ import annotations

import os
from functools import lru_cache

import sentry_sdk
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelemetrySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    sentry_dsn: SecretStr | None = None


@lru_cache
def settings() -> TelemetrySettings:
    return TelemetrySettings()


def scrub_event(event: dict, _hint: dict | None = None) -> dict:
    """Allowlist only diagnostic fields; drop raw exception, request, URL and breadcrumbs."""
    safe_tags = {}
    for key in ("stage", "error_code", "component"):
        value = event.get("tags", {}).get(key)
        if isinstance(value, str) and value.replace("_", "").replace("-", "").isalnum() and len(value) <= 80:
            safe_tags[key] = value
    return {
        "event_id": event.get("event_id"),
        "timestamp": event.get("timestamp"),
        "platform": "python",
        "level": "error",
        "message": "Call Intelligence service error",
        "tags": safe_tags,
    }


def configure_telemetry() -> bool:
    if os.environ.get("CALL_INTELLIGENCE_SENTRY_DISABLED") == "1":
        return False
    dsn = settings().sentry_dsn
    if not dsn or not dsn.get_secret_value():
        return False
    sentry_sdk.init(
        dsn=dsn.get_secret_value(),
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        profiles_sample_rate=0.0,
        before_send=scrub_event,
        before_breadcrumb=lambda _breadcrumb, _hint: None,
    )
    return True
