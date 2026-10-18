"""Explicit one-file local smoke path through Better Auth, FastAPI and R2."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[3]


def bootstrap_credentials() -> tuple[str, str]:
    path = ROOT / "data" / "private" / "bootstrap-owner.txt"
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            pairs[key.strip()] = value.strip()
    return pairs["Email"], pairs["Password"]


def check(response: httpx.Response, stage: str) -> None:
    if response.status_code >= 400:
        raise RuntimeError(f"{stage} failed with HTTP {response.status_code}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mp3", type=Path, help="Exactly one MP3 to upload")
    parser.add_argument("--external-id", required=True)
    args = parser.parse_args()
    path = args.mp3.resolve(strict=True)
    if path.suffix.lower() != ".mp3":
        raise SystemExit("Only MP3 files are accepted")
    email, password = bootstrap_credentials()
    web = os.environ.get("CALL_INTELLIGENCE_WEB_URL", "http://127.0.0.1:3000")
    api = os.environ.get("CALL_INTELLIGENCE_API_URL", "http://127.0.0.1:8000")
    with httpx.Client(timeout=120, follow_redirects=False) as client:
        signed_in = client.post(
            web + "/api/auth/sign-in/email",
            json={"email": email, "password": password},
            headers={"Origin": web},
        )
        check(signed_in, "Sign-in")
        token_response = client.get(web + "/api/auth/token")
        check(token_response, "API token")
        headers = {"Authorization": "Bearer " + token_response.json()["token"]}
        intent = client.post(
            api + "/api/v1/uploads",
            headers=headers,
            json={
                "files": [
                    {
                        "filename": path.name,
                        "byte_size": path.stat().st_size,
                        "content_type": "audio/mpeg",
                        "external_call_id": args.external_id,
                    }
                ]
            },
        )
        check(intent, "Upload intent")
        ticket = intent.json()["items"][0]
        with path.open("rb") as audio:
            uploaded = client.put(ticket["upload_url"], headers=ticket["headers"], content=audio.read())
        check(uploaded, "Private upload")
        complete = client.post(
            api + f"/api/v1/calls/{ticket['call_id']}/complete-upload",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"upload_id": ticket["upload_id"]},
        )
        check(complete, "Upload finalization")
        private_path = ROOT / "data" / "private" / "live-call-id.txt"
        private_path.write_text(ticket["call_id"] + "\n", encoding="utf-8")
        print(f"call_id={ticket['call_id']} status={complete.json()['processing_status']}")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as exc:
        raise SystemExit(f"Provider connection failed ({type(exc).__name__})") from None
