"""One-call read-only workspace smoke check; never uploads or invokes AI."""

from __future__ import annotations

import argparse
from uuid import UUID

import httpx

from scripts.ingest_one import bootstrap_credentials, check


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("call_id", type=UUID)
    call_id = parser.parse_args().call_id
    web = "http://127.0.0.1:3000"
    api = "http://127.0.0.1:8000/api/v1"
    email, password = bootstrap_credentials()
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        check(client.post(web + "/api/auth/sign-in/email", json={"email": email, "password": password},
                          headers={"Origin": web}), "Sign-in")
        token = client.get(web + "/api/auth/token")
        check(token, "API token")
        headers = {"Authorization": "Bearer " + token.json()["token"]}
        api_paths = [f"/calls/{call_id}", f"/calls/{call_id}/transcript", "/insights/overview",
                     "/tasks", "/crm/contacts", "/crm/opportunities", "/reps", "/connections",
                     "/insights/threads"]
        results = {}
        for path in api_paths:
            response = client.get(api + path, headers=headers)
            check(response, path)
            results[path] = response.json()
        pages = ["/", "/sales", "/sales?view=calls", "/sales?view=pipeline",
                 f"/sales/calls/{call_id}", "/tasks", "/team", "/assistant", "/connections"]
        for path in pages:
            response = client.get(web + path)
            check(response, f"Web {path}")
            if "sign-in" in str(response.url):
                raise RuntimeError(f"Web {path} redirected to sign-in")
        detail = results[f"/calls/{call_id}"]
        transcript = results[f"/calls/{call_id}/transcript"]
        overview = results["/insights/overview"]
        print(f"web_pages={len(pages)} api_reads={len(api_paths)} selected_call_segments={len(transcript['segments'])}",
              f"analysis={detail['call']['analysis_status']} index={detail['call']['index_status']}",
              f"active_calls={overview['uploaded_calls']} analyzed={overview['analyzed_calls']}")


if __name__ == "__main__":
    main()
