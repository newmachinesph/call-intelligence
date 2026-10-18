"""One selected call: private detail, playback, and one saved grounded answer."""

from __future__ import annotations

import argparse
from uuid import UUID

import httpx

from scripts.ingest_one import bootstrap_credentials, check


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("call_id", type=UUID)
    args = parser.parse_args()
    web = "http://127.0.0.1:3000"
    api = "http://127.0.0.1:8000/api/v1"
    email, password = bootstrap_credentials()
    with httpx.Client(timeout=120) as client:
        login = client.post(
            web + "/api/auth/sign-in/email",
            json={"email": email, "password": password},
            headers={"Origin": web},
        )
        check(login, "Sign-in")
        token = client.get(web + "/api/auth/token")
        check(token, "API token")
        headers = {"Authorization": "Bearer " + token.json()["token"]}
        detail = client.get(api + f"/calls/{args.call_id}", headers=headers)
        check(detail, "Call detail")
        transcript = client.get(api + f"/calls/{args.call_id}/transcript", headers=headers)
        check(transcript, "Transcript")
        playback = client.get(api + f"/calls/{args.call_id}/audio-url", headers=headers)
        check(playback, "Playback signing")
        audio = client.get(playback.json()["url"], headers={"Range": "bytes=0-1023"})
        check(audio, "Private playback")
        threads = client.get(api + f"/calls/{args.call_id}/chat/threads", headers=headers)
        check(threads, "Thread listing")
        items = threads.json()["items"]
        if items:
            thread_id = items[0]["id"]
        else:
            created = client.post(
                api + f"/calls/{args.call_id}/chat/threads",
                headers=headers,
                json={"title": "Single call verification"},
            )
            check(created, "Thread creation")
            thread_id = created.json()["id"]
        answer = client.post(
            api + f"/calls/{args.call_id}/chat/threads/{thread_id}/messages",
            headers={**headers, "Idempotency-Key": "single-call-check-v1"},
            json={
                "text": "Why did this call receive its score? Cite one criterion's source.",
                "client_message_id": "single-call-check-v1",
            },
        )
        check(answer, "Call chat")
        history = client.get(api + f"/calls/{args.call_id}/chat/threads/{thread_id}/messages", headers=headers)
        check(history, "Saved chat history")
        result = detail.json()
        print(
            "detail=200",
            f"segments={len(transcript.json()['segments'])}",
            f"analysis={result['call']['analysis_status']}",
            f"index={result['call']['index_status']}",
            f"playback={audio.status_code}",
            f"answer_status={answer.json()['evidence_status']}",
            f"citations={len(answer.json()['citations'])}",
            f"saved_messages={len(history.json()['items'])}",
        )


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as exc:
        raise SystemExit(f"Connection failed ({type(exc).__name__})") from None
