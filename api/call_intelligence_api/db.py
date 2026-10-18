"""Domain database configuration; Better Auth uses a different database."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import certifi
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def domain_database_url() -> str:
    value = os.environ.get("DOMAIN_DATABASE_URL")
    if not value:
        env_file = Path(__file__).resolve().parents[1] / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("DOMAIN_DATABASE_URL="):
                    value = line.partition("=")[2]
                    break
    if not value:
        raise RuntimeError("DOMAIN_DATABASE_URL is required")
    if "sslmode=verify-full" in value and "sslrootcert=system" in value:
        # libpq on Windows may not load the OS store for Neon's certificate chain.
        pem = quote(certifi.where().replace("\\", "/"), safe="/")
        return value.replace("sslrootcert=system", f"sslrootcert={pem}")
    return value


def create_domain_engine() -> Engine:
    # The shared DigitalOcean cluster has few direct slots. PgBouncer handles
    # backend reuse; keep one client connection and avoid session-bound prepares.
    return create_engine(
        domain_database_url(),
        pool_pre_ping=False,
        pool_size=1,
        max_overflow=0,
        connect_args={"prepare_threshold": None},
    )


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
