"""Link an existing Better Auth owner to the single local test organization."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import certifi
import psycopg


def read_env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            value = line.partition("=")[2]
            # Same Windows libpq CA substitution as call_intelligence_api.db; TLS stays verify-full.
            pem = quote(certifi.where().replace("\\", "/"), safe="/")
            if "sslrootcert=system" in value:
                return value.replace("sslrootcert=system", f"sslrootcert={pem}")
            if "sslmode=verify-full" in value and "sslrootcert=" not in value:
                return f"{value}&sslrootcert={pem}"
            return value
    raise RuntimeError(f"{key} is missing from {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-email", required=True)
    args = parser.parse_args()
    api_root = Path(__file__).resolve().parents[1]
    web_env = api_root.parent / "web" / ".env.local"
    auth_url = read_env_value(web_env, "AUTH_DATABASE_URL")
    domain_url = read_env_value(api_root / ".env", "DOMAIN_DATABASE_URL")

    with psycopg.connect(auth_url) as auth_conn:
        rows = auth_conn.execute(
            'SELECT id FROM "user" WHERE email = %s AND "emailVerified" = true',
            (args.owner_email,),
        ).fetchall()
    if len(rows) != 1:
        raise RuntimeError("A single verified Better Auth owner must exist before seeding")
    user_id = rows[0][0]

    with psycopg.connect(domain_url) as domain_conn:
        org = domain_conn.execute(
            "INSERT INTO organization (id, slug, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (uuid4(), "demo", "Demo Organization"),
        ).fetchone()
        domain_conn.execute(
            "INSERT INTO membership (id, organization_id, user_id, role, active) "
            "VALUES (%s, %s, %s, 'owner', true) "
            "ON CONFLICT (organization_id, user_id) DO UPDATE "
            "SET role = EXCLUDED.role, active = true",
            (uuid4(), org[0], user_id),
        )
    print("Local demo organization and owner membership are ready")


if __name__ == "__main__":
    main()
